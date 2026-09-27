#!/usr/bin/env python3
"""Contract tests for the admin-only lockdown of LiteLLM's /openrouter pass-through.

Invariant (kubernetes/apps/base/ai/litellm/README.md, the typesafe/jev-1.13
section of Model catalog): the image's built-in `/openrouter/{endpoint:path}`
route forwards to OpenRouter on the shared OPENROUTER_API_KEY and is NOT gated
by a virtual key's `models` allow-list (that check only fires when the body
carries a `model` field; OpenRouter's `models` array or a GET skips it). So
every virtual key must be denied the whole /openrouter prefix by default, and
only the master key (proxy admin) may reach POST /openrouter/alpha/decisions.

The lockdown is two `generalSettings.pass_through_endpoints` entries on the
LiteLLMProxy CR. Their ORDER is load-bearing, which is why this test exists:

  1. Every entry under /openrouter carries `auth: true`. That is what makes
     RouteChecks.non_proxy_admin_allowed_routes_check demand an
     `allowed_passthrough_routes` match from every non-admin key.
  2. A subpath catch-all on /openrouter exists, so NEW paths and NEW keys are
     denied too (default-deny, not an enumerated block list).
  3. The exact decisions entry precedes the catch-all. The native handler
     takes target + headers from the FIRST registry entry matching the path
     and drops the subpath, so with the catch-all first the master key's jev
     call would be sent to the dead catch-all target.
  4. The catch-all points at a dead loopback port with no headers, so no other
     /openrouter path ever reaches OpenRouter with the credential, admin or not.

Checks 1-4 are asserted structurally on the kustomize-rendered CR AND
semantically by registering the rendered entries through LiteLLM's own
`_register_pass_through_endpoint` and driving its own RouteChecks (litellm is
pinned to the cluster image version by validate.yaml). The semantic checks are
then re-run against mutated copies (auth dropped, order swapped, catch-all
removed) to prove each one can fail.

Live proof (demo key 403 on /openrouter/*, master key 200 on decisions, chat
and embedding keys unaffected, spend recorded) needs the running proxy and is
produced by the pre-merge suspend/patch/resume drill, not by CI.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "kubernetes" / "apps" / "base" / "ai" / "litellm" / "app"
PROXY_PATH = APP_DIR / "litellmproxy.yaml"

PREFIX = "/openrouter"
DECISIONS_PATH = "/openrouter/alpha/decisions"
DECISIONS_TARGET = "https://openrouter.ai/api/alpha/decisions"
CREDENTIAL_REF = "os.environ/OPENROUTER_API_KEY"

# Paths a non-admin key must be refused on. Includes the measured bypass
# shapes and normalization variants (all 403 against the real proxy in the
# 2026-09-27 lab run).
DENY_PROBES: list[tuple[str, str]] = [
    ("POST", "/openrouter/alpha/decisions"),
    ("GET", "/openrouter/alpha/decisions"),
    ("POST", "/openrouter/v1/chat/completions"),
    ("GET", "/openrouter/v1/key"),
    ("GET", "/openrouter/v1/models"),
    ("POST", "/openrouter/v1/embeddings"),
    ("POST", "/openrouter"),
    ("POST", "/openrouter//v1/chat/completions"),
    ("POST", "/openrouter/./v1/chat/completions"),
    ("POST", "/openrouter/alpha/decisions/"),
    ("DELETE", "/openrouter/v1/keys/x"),
]

# Governed routes every existing key uses. The lockdown must not touch them.
ALLOW_PROBES: list[tuple[str, str]] = [
    ("POST", "/v1/chat/completions"),
    ("POST", "/chat/completions"),
    ("POST", "/v1/embeddings"),
    ("POST", "/v1/messages"),
]

RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" - {detail}" if detail else ""))


def _proxy_from(docs: list[dict[str, Any]]) -> dict[str, Any] | None:
    for d in docs:
        if d.get("kind") == "LiteLLMProxy" and (d.get("metadata") or {}).get("name") == "litellm":
            return d
    return None


def load_rendered_proxy() -> tuple[dict[str, Any] | None, str]:
    """Render the app with kustomize so a patch cannot silently strip the block."""
    kubectl = shutil.which("kubectl")
    if kubectl is not None:
        built = subprocess.run(
            [kubectl, "kustomize", str(APP_DIR)],
            capture_output=True,
            text=True,
            check=False,
        )
        if built.returncode == 0:
            return _proxy_from([d for d in yaml.safe_load_all(built.stdout) if d]), "kustomize"
        return None, f"kustomize failed: {built.stderr[-300:]}"
    return _proxy_from([d for d in yaml.safe_load_all(PROXY_PATH.read_text()) if d]), "file (no kubectl)"


def _under_prefix(path: str) -> bool:
    return path == PREFIX or path.startswith(PREFIX + "/")


def _auth_true(entry: dict[str, Any]) -> bool:
    auth = entry.get("auth")
    return auth is not None and str(auth).lower() == "true"


def structural_findings(entries: list[dict[str, Any]]) -> list[str]:
    """Return the violated invariants for a pass_through_endpoints list."""
    bad: list[str] = []
    ours = [(i, e) for i, e in enumerate(entries) if _under_prefix(str(e.get("path", "")))]
    for i, e in ours:
        if not _auth_true(e):
            bad.append(f"entry {i} ({e.get('path')}) lacks auth: true")
    catch_all = [(i, e) for i, e in ours if e.get("path") == PREFIX and e.get("include_subpath") is True]
    if len(catch_all) != 1:
        bad.append(f"expected exactly one {PREFIX} include_subpath catch-all, found {len(catch_all)}")
    decisions = [(i, e) for i, e in ours if e.get("path") == DECISIONS_PATH]
    if len(decisions) != 1:
        bad.append(f"expected exactly one {DECISIONS_PATH} entry, found {len(decisions)}")
    if catch_all:
        ci, ce = catch_all[0]
        if ci != max(i for i, _ in ours):
            bad.append("catch-all is not the last /openrouter entry (native handler takes the first match)")
        host = urlparse(str(ce.get("target", ""))).hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            bad.append(f"catch-all target host {host!r} is not a dead loopback")
        if ce.get("headers"):
            bad.append("catch-all carries headers (must not send any credential)")
    if decisions:
        di, de = decisions[0]
        if catch_all and di > catch_all[0][0]:
            bad.append("decisions entry comes after the catch-all")
        if de.get("target") != DECISIONS_TARGET:
            bad.append(f"decisions target {de.get('target')!r} != {DECISIONS_TARGET!r}")
        if de.get("include_subpath"):
            bad.append("decisions entry must be exact (include_subpath would widen it)")
        auth_header = str((de.get("headers") or {}).get("Authorization", ""))
        if CREDENTIAL_REF not in auth_header:
            bad.append("decisions entry does not send the OpenRouter credential")
    return bad


def _request(method: str, path: str):
    from starlette.requests import Request

    return Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b""})


def semantic_findings(entries: list[dict[str, Any]], metadata: dict[str, Any] | None = None) -> list[str]:
    """Register entries through LiteLLM itself and drive its RouteChecks."""
    from fastapi import FastAPI, HTTPException
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.auth.route_checks import RouteChecks
    from litellm.proxy.pass_through_endpoints.pass_through_endpoints import (
        InitPassThroughEndpointHelpers,
        _register_pass_through_endpoint,
    )

    InitPassThroughEndpointHelpers.clear_all_pass_through_routes()
    app = FastAPI()

    async def _register() -> None:
        visited: set[str] = set()
        for e in copy.deepcopy(entries):
            await _register_pass_through_endpoint(
                endpoint=e, app=app, premium_user=False, visited_endpoints=visited
            )

    asyncio.run(_register())

    bad: list[str] = []
    key = UserAPIKeyAuth(api_key="sk-test", metadata=metadata or {})

    def refused(method: str, path: str) -> bool:
        try:
            RouteChecks.non_proxy_admin_allowed_routes_check(
                user_obj=None,
                _user_role=None,
                route=path,
                request=_request(method, path),
                valid_token=key,
                request_data={},
            )
        except HTTPException as e:
            return e.status_code == 403
        except Exception:
            return True
        return False

    for method, path in DENY_PROBES:
        if not refused(method, path):
            bad.append(f"non-admin key NOT refused on {method} {path}")
    for method, path in ALLOW_PROBES:
        if refused(method, path):
            bad.append(f"non-admin key refused on governed route {method} {path}")

    hit = InitPassThroughEndpointHelpers.get_registered_pass_through_route(route=DECISIONS_PATH, method="POST")
    target = ((hit or {}).get("passthrough_params") or {}).get("target")
    if target != DECISIONS_TARGET:
        bad.append(f"admin POST {DECISIONS_PATH} would be sent to {target!r}, not {DECISIONS_TARGET!r}")

    other = InitPassThroughEndpointHelpers.get_registered_pass_through_route(
        route="/openrouter/v1/chat/completions", method="POST"
    )
    other_target = ((other or {}).get("passthrough_params") or {}).get("target")
    if other_target is None or urlparse(str(other_target)).hostname not in {"127.0.0.1", "localhost", "::1"}:
        bad.append(f"admin POST /openrouter/v1/chat/completions would reach {other_target!r}, not a dead loopback")

    InitPassThroughEndpointHelpers.clear_all_pass_through_routes()
    return bad


def main() -> int:
    logging.disable(logging.CRITICAL)
    proxy, source = load_rendered_proxy()
    record("rendered_litellmproxy_found", proxy is not None, source)
    if proxy is None:
        return 1
    entries = ((proxy.get("spec") or {}).get("generalSettings") or {}).get("pass_through_endpoints") or []
    record("pass_through_endpoints_present", bool(entries), f"n={len(entries)}")

    s = structural_findings(entries)
    record("structural_contract", not s, "; ".join(s))

    try:
        import litellm  # noqa: F401
    except ImportError:
        record("litellm_importable", False, "pip install 'litellm[proxy]' at the cluster image version")
        return 1

    m = semantic_findings(entries)
    record("semantic_contract_via_litellm_routechecks", not m, "; ".join(m))

    # The only grant shape the operator can emit is a map[string]string value.
    # It must NOT grant anything (a string iterates as characters in
    # check_passthrough_route_access); if a future litellm starts honouring it,
    # the README's "no virtual key can be granted declaratively" claim is stale.
    g = semantic_findings(entries, metadata={"allowed_passthrough_routes": DECISIONS_PATH})
    record(
        "operator_string_metadata_grants_nothing",
        not any("NOT refused" in f for f in g),
        "; ".join(f for f in g if "NOT refused" in f),
    )

    # Mutation proof: each invariant must be able to fail.
    ours = [i for i, e in enumerate(entries) if _under_prefix(str(e.get("path", "")))]
    mutations: dict[str, list[dict[str, Any]]] = {}
    no_auth = copy.deepcopy(entries)
    for i in ours:
        no_auth[i].pop("auth", None)
    mutations["auth_dropped"] = no_auth
    mutations["catch_all_removed"] = [e for e in copy.deepcopy(entries) if e.get("path") != PREFIX]
    swapped = copy.deepcopy(entries)
    if len(ours) >= 2:
        a, b = ours[0], ours[-1]
        swapped[a], swapped[b] = swapped[b], swapped[a]
    mutations["order_swapped"] = swapped
    for name, mutated in mutations.items():
        caught_s = bool(structural_findings(mutated))
        caught_m = bool(semantic_findings(mutated))
        record(f"mutation_{name}_is_caught", caught_s and caught_m, f"structural={caught_s} semantic={caught_m}")

    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed" + (f", {len(failed)} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
