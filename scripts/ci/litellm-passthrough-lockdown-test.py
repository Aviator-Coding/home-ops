#!/usr/bin/env python3
"""Contract tests for the admin-only lockdown of LiteLLM's built-in provider pass-throughs.

Invariant (kubernetes/apps/base/ai/litellm/README.md, the typesafe/jev-1.13
section of Model catalog, and docs/ai-system/litellm/README.md#anthropic-pass-
through-route-closed-2026-08-31): the image's built-in `/openrouter/{endpoint}`
and `/anthropic/{endpoint}` routes forward to the provider on a shared server-
side credential (OPENROUTER_API_KEY, and the household METERED
ANTHROPIC_API_KEY) and are NOT gated by a virtual key's `models` allow-list
(that check only fires when the body carries a `model` field). So every
virtual key must be denied each whole prefix by default, and only the master
key (proxy admin) may reach the few exact paths listed in PREFIXES below.

The lockdown is a set of `generalSettings.pass_through_endpoints` entries on
the LiteLLMProxy CR per prefix. Their ORDER is load-bearing, which is why this
test exists. For each prefix:

  1. Every entry under the prefix carries `auth: true`. That is what makes
     RouteChecks.non_proxy_admin_allowed_routes_check demand an
     `allowed_passthrough_routes` match from every non-admin key.
  2. A subpath catch-all on the prefix exists, so NEW paths and NEW keys are
     denied too (default-deny, not an enumerated block list).
  3. Every exact admin entry precedes the catch-all. The native handler takes
     target + headers from the FIRST registry entry matching the path and
     drops the subpath, so with the catch-all first the admin call would be
     sent to the dead catch-all target.
  4. The catch-all points at a dead loopback port with no headers, so no other
     path under the prefix ever reaches the provider with the credential,
     admin or not.
  5. No entry under the prefix sets `forward_headers`. The native handler
     forwards every client header (the caller's own LiteLLM key included) to
     the provider; an entry replaces that with exactly its declared headers.

Checks 1-5 are asserted structurally on the kustomize-rendered CR AND
semantically by registering the rendered entries through LiteLLM's own
`_register_pass_through_endpoint` and driving its own RouteChecks (litellm is
pinned to the cluster image version by validate.yaml). The semantic checks are
then re-run against mutated copies (auth dropped, order swapped, catch-all
removed) to prove each one can fail.

Live proof (virtual keys 403 on each prefix, master key served on the exact
paths, chat and embedding keys unaffected) needs the running proxy and is
produced by the pre-merge suspend/patch/resume drill, not by CI.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "kubernetes" / "apps" / "base" / "ai" / "litellm" / "app"
PROXY_PATH = APP_DIR / "litellmproxy.yaml"

LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True)
class AdminPath:
    """An exact path the master key keeps, and where it must be sent."""

    path: str
    method: str
    target: str
    credential_header: str
    credential_ref: str


@dataclass(frozen=True)
class Prefix:
    prefix: str
    admin_paths: tuple[AdminPath, ...]
    # Paths a non-admin key must be refused on, including the measured bypass
    # shapes and normalization variants.
    deny_probes: tuple[tuple[str, str], ...]
    # A path an admin must NOT get a real target for (must hit the catch-all).
    dead_probe: tuple[str, str]


PREFIXES: tuple[Prefix, ...] = (
    Prefix(
        prefix="/openrouter",
        admin_paths=(
            AdminPath(
                path="/openrouter/alpha/decisions",
                method="POST",
                target="https://openrouter.ai/api/alpha/decisions",
                credential_header="Authorization",
                credential_ref="os.environ/OPENROUTER_API_KEY",
            ),
        ),
        # All 403 against the real proxy in the 2026-09-27 lab run.
        deny_probes=(
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
        ),
        dead_probe=("POST", "/openrouter/v1/chat/completions"),
    ),
    Prefix(
        prefix="/anthropic",
        admin_paths=(
            AdminPath(
                path="/anthropic/v1/messages",
                method="POST",
                target="https://api.anthropic.com/v1/messages",
                credential_header="x-api-key",
                credential_ref="os.environ/ANTHROPIC_API_KEY",
            ),
            AdminPath(
                path="/anthropic/v1/messages/count_tokens",
                method="POST",
                target="https://api.anthropic.com/v1/messages/count_tokens",
                credential_header="x-api-key",
                credential_ref="os.environ/ANTHROPIC_API_KEY",
            ),
        ),
        deny_probes=(
            ("POST", "/anthropic/v1/messages"),
            ("POST", "/anthropic/v1/messages/count_tokens"),
            ("GET", "/anthropic/v1/messages"),
            ("POST", "/anthropic/v1/messages/batches"),
            ("GET", "/anthropic/v1/models"),
            ("POST", "/anthropic/v1/complete"),
            ("POST", "/anthropic/v1/files"),
            ("POST", "/anthropic"),
            ("POST", "/anthropic//v1/messages"),
            ("POST", "/anthropic/./v1/messages"),
            ("POST", "/anthropic/v1/messages/"),
            ("DELETE", "/anthropic/v1/files/x"),
        ),
        dead_probe=("GET", "/anthropic/v1/models"),
    ),
)

# Governed routes every existing key uses. The lockdown must not touch them.
ALLOW_PROBES: list[tuple[str, str]] = [
    ("POST", "/v1/chat/completions"),
    ("POST", "/chat/completions"),
    ("POST", "/v1/embeddings"),
    ("POST", "/v1/messages"),
    ("POST", "/v1/messages/count_tokens"),
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


def _under(prefix: str, path: str) -> bool:
    return path == prefix or path.startswith(prefix + "/")


def _auth_true(entry: dict[str, Any]) -> bool:
    auth = entry.get("auth")
    return auth is not None and str(auth).lower() == "true"


def structural_findings(entries: list[dict[str, Any]], spec: Prefix) -> list[str]:
    """Return the violated invariants for one prefix of a pass_through_endpoints list."""
    bad: list[str] = []
    p = spec.prefix
    ours = [(i, e) for i, e in enumerate(entries) if _under(p, str(e.get("path", "")))]
    for i, e in ours:
        if not _auth_true(e):
            bad.append(f"entry {i} ({e.get('path')}) lacks auth: true")
        if e.get("forward_headers"):
            bad.append(f"entry {i} ({e.get('path')}) forwards client headers (would send the caller's key upstream)")
    catch_all = [(i, e) for i, e in ours if e.get("path") == p and e.get("include_subpath") is True]
    if len(catch_all) != 1:
        bad.append(f"expected exactly one {p} include_subpath catch-all, found {len(catch_all)}")
    if catch_all:
        ci, ce = catch_all[0]
        if ci != max(i for i, _ in ours):
            bad.append(f"catch-all is not the last {p} entry (native handler takes the first match)")
        host = urlparse(str(ce.get("target", ""))).hostname
        if host not in LOOPBACK_HOSTS:
            bad.append(f"{p} catch-all target host {host!r} is not a dead loopback")
        if ce.get("headers"):
            bad.append(f"{p} catch-all carries headers (must not send any credential)")
    for ap in spec.admin_paths:
        found = [(i, e) for i, e in ours if e.get("path") == ap.path]
        if len(found) != 1:
            bad.append(f"expected exactly one {ap.path} entry, found {len(found)}")
            continue
        di, de = found[0]
        if catch_all and di > catch_all[0][0]:
            bad.append(f"{ap.path} entry comes after the catch-all")
        if de.get("target") != ap.target:
            bad.append(f"{ap.path} target {de.get('target')!r} != {ap.target!r}")
        if de.get("include_subpath"):
            bad.append(f"{ap.path} entry must be exact (include_subpath would widen it)")
        if [str(m).upper() for m in de.get("methods") or []] != [ap.method]:
            bad.append(f"{ap.path} entry must be restricted to methods [{ap.method}]")
        header = str((de.get("headers") or {}).get(ap.credential_header, ""))
        if ap.credential_ref not in header:
            bad.append(f"{ap.path} entry does not send {ap.credential_ref} in {ap.credential_header}")
    return bad


def _request(method: str, path: str):
    from starlette.requests import Request

    return Request({"type": "http", "method": method, "path": path, "headers": [], "query_string": b""})


def semantic_findings(
    entries: list[dict[str, Any]], specs: tuple[Prefix, ...], metadata: dict[str, Any] | None = None
) -> list[str]:
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

    def target_for(method: str, path: str) -> Any:
        hit = InitPassThroughEndpointHelpers.get_registered_pass_through_route(route=path, method=method)
        return ((hit or {}).get("passthrough_params") or {}).get("target")

    for spec in specs:
        for method, path in spec.deny_probes:
            if not refused(method, path):
                bad.append(f"non-admin key NOT refused on {method} {path}")
        for ap in spec.admin_paths:
            target = target_for(ap.method, ap.path)
            if target != ap.target:
                bad.append(f"admin {ap.method} {ap.path} would be sent to {target!r}, not {ap.target!r}")
        method, path = spec.dead_probe
        dead = target_for(method, path)
        if dead is None or urlparse(str(dead)).hostname not in LOOPBACK_HOSTS:
            bad.append(f"admin {method} {path} would reach {dead!r}, not a dead loopback")
    for method, path in ALLOW_PROBES:
        if refused(method, path):
            bad.append(f"non-admin key refused on governed route {method} {path}")

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

    for spec in PREFIXES:
        s = structural_findings(entries, spec)
        record(f"structural_contract{spec.prefix}", not s, "; ".join(s))

    try:
        import litellm  # noqa: F401
    except ImportError:
        record("litellm_importable", False, "pip install 'litellm[proxy]' at the cluster image version")
        return 1

    m = semantic_findings(entries, PREFIXES)
    record("semantic_contract_via_litellm_routechecks", not m, "; ".join(m))

    # The only grant shape the operator can emit is a map[string]string value.
    # It must NOT grant anything (a string iterates as characters in
    # check_passthrough_route_access); if a future litellm starts honouring it,
    # the README's "no virtual key can be granted declaratively" claim is stale.
    for spec in PREFIXES:
        g = semantic_findings(
            entries, (spec,), metadata={"allowed_passthrough_routes": spec.admin_paths[0].path}
        )
        record(
            f"operator_string_metadata_grants_nothing{spec.prefix}",
            not any("NOT refused" in f for f in g),
            "; ".join(f for f in g if "NOT refused" in f),
        )

    # Mutation proof: each invariant must be able to fail, per prefix.
    for spec in PREFIXES:
        ours = [i for i, e in enumerate(entries) if _under(spec.prefix, str(e.get("path", "")))]
        mutations: dict[str, list[dict[str, Any]]] = {}
        no_auth = copy.deepcopy(entries)
        for i in ours:
            no_auth[i].pop("auth", None)
        mutations["auth_dropped"] = no_auth
        mutations["catch_all_removed"] = [e for e in copy.deepcopy(entries) if e.get("path") != spec.prefix]
        swapped = copy.deepcopy(entries)
        if len(ours) >= 2:
            a, b = ours[0], ours[-1]
            swapped[a], swapped[b] = swapped[b], swapped[a]
        mutations["order_swapped"] = swapped
        for name, mutated in mutations.items():
            caught_s = bool(structural_findings(mutated, spec))
            caught_m = bool(semantic_findings(mutated, (spec,)))
            record(
                f"mutation{spec.prefix}_{name}_is_caught",
                caught_s and caught_m,
                f"structural={caught_s} semantic={caught_m}",
            )
        # Structural-only: forward_headers has no RouteChecks-visible effect.
        forwarding = copy.deepcopy(entries)
        forwarding[ours[0]]["forward_headers"] = True
        record(
            f"mutation{spec.prefix}_forward_headers_is_caught",
            bool(structural_findings(forwarding, spec)),
        )

    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed" + (f", {len(failed)} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
