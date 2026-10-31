#!/usr/bin/env python3
"""Contract tests for the admin-only lockdown of LiteLLM's built-in provider pass-throughs.

Invariant (skill litellm-proxy, references/passthrough-lockdown.md): the image's built-in `/openrouter/{endpoint}`
and `/anthropic/{endpoint}` routes forward to the provider on a shared server-
side credential (OPENROUTER_API_KEY, and the household METERED
ANTHROPIC_API_KEY) and are NOT gated by a virtual key's `models` allow-list
(that check only fires when the body carries a `model` field). So every
virtual key must be denied each whole prefix by default, and only the master
key (proxy admin) may reach the exact paths listed in PREFIXES below (none for /openrouter).

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

No non-admin door exists. The Jev decision model is served natively on
/v1/decisions and /v1/systemone as the `jev` LiteLLMModel, gated by a normal
`models` allow-list, so there is no pass-through grant (the former out-of-Git
`jev-door-grant` is gone). CI asserts the parts Git owns:

  6. `LiteLLMModel/jev` routes `openrouter/typesafe/*` on OPENROUTER_API_KEY.
  7. The `jev-decisions` key is aliased so, allow-lists exactly [jev], has a
     positive budget and a PushSecret to `litellm-consumer-jev-decisions`.
  8. No LiteLLMVirtualKey or LiteLLMTeam declares `allowed_passthrough_routes`,
     and no key allow-lists a raw `typesafe/` id.

Each of 6-8 is re-run against mutations to prove it can fail.

Live proof (virtual keys 403 on each prefix, master key served on the exact
paths, chat and embedding keys unaffected, /v1/decisions served for the jev key) needs the running proxy and is produced by the live drill, not by CI.
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
        admin_paths=(),
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

# The Jev decision model: a native LiteLLMModel behind /v1/decisions (no pass-through door).
JEV_KEY_NAME = "jev-decisions"
JEV_MODEL = "jev"
JEV_MODELS = [JEV_MODEL]
JEV_REMOTE_KEY = "litellm-consumer-jev-decisions"
JEV_UPSTREAM_PREFIX = "openrouter/typesafe/"
JEV_CREDENTIAL = "os.environ/OPENROUTER_API_KEY"
# /v1/decisions must stay a governed route.
ALLOW_PROBES.extend([("POST", "/v1/decisions"), ("POST", "/v1/systemone")])

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


def load_rendered() -> tuple[list[dict[str, Any]] | None, str]:
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
            return [d for d in yaml.safe_load_all(built.stdout) if d], "kustomize"
        return None, f"kustomize failed: {built.stderr[-300:]}"
    docs = [d for d in yaml.safe_load_all(PROXY_PATH.read_text()) if d]
    for f in sorted((APP_DIR / "virtualkeys").glob("*.yaml")) + sorted((APP_DIR / "models").glob("*.yaml")):
        docs += [d for d in yaml.safe_load_all(f.read_text()) if d]
    return docs, "files (no kubectl)"


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
    allowed = {p, *(ap.path for ap in spec.admin_paths)}
    for i, e in ours:
        if e.get("path") not in allowed:
            bad.append(f"entry {i} ({e.get('path')}) is not a declared admin path (a reopened door)")
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


def _register(entries: list[dict[str, Any]]) -> None:
    """Register entries through LiteLLM's own pass-through registry."""
    from fastapi import FastAPI
    from litellm.proxy.pass_through_endpoints.pass_through_endpoints import (
        InitPassThroughEndpointHelpers,
        _register_pass_through_endpoint,
    )

    InitPassThroughEndpointHelpers.clear_all_pass_through_routes()
    app = FastAPI()

    async def _run() -> None:
        visited: set[str] = set()
        for e in copy.deepcopy(entries):
            await _register_pass_through_endpoint(
                endpoint=e, app=app, premium_user=False, visited_endpoints=visited
            )

    asyncio.run(_run())


def _clear() -> None:
    from litellm.proxy.pass_through_endpoints.pass_through_endpoints import InitPassThroughEndpointHelpers

    InitPassThroughEndpointHelpers.clear_all_pass_through_routes()


def _refused(metadata: dict[str, Any], method: str, path: str) -> bool:
    """True when LiteLLM's RouteChecks refuse a non-admin key carrying `metadata`."""
    from fastapi import HTTPException
    from litellm.proxy._types import UserAPIKeyAuth
    from litellm.proxy.auth.route_checks import RouteChecks

    key = UserAPIKeyAuth(api_key="sk-test", metadata=metadata)
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


def _target_for(method: str, path: str) -> Any:
    """Where the native handler would send this request (first registry match)."""
    from litellm.proxy.pass_through_endpoints.pass_through_endpoints import InitPassThroughEndpointHelpers

    hit = InitPassThroughEndpointHelpers.get_registered_pass_through_route(route=path, method=method)
    return ((hit or {}).get("passthrough_params") or {}).get("target")


def semantic_findings(
    entries: list[dict[str, Any]], specs: tuple[Prefix, ...], metadata: dict[str, Any] | None = None
) -> list[str]:
    """Register entries through LiteLLM itself and drive its RouteChecks."""
    _register(entries)
    bad: list[str] = []
    md = metadata or {}

    for spec in specs:
        for method, path in spec.deny_probes:
            if not _refused(md, method, path):
                bad.append(f"non-admin key NOT refused on {method} {path}")
        for ap in spec.admin_paths:
            target = _target_for(ap.method, ap.path)
            if target != ap.target:
                bad.append(f"admin {ap.method} {ap.path} would be sent to {target!r}, not {ap.target!r}")
        method, path = spec.dead_probe
        dead = _target_for(method, path)
        if dead is None or urlparse(str(dead)).hostname not in LOOPBACK_HOSTS:
            bad.append(f"admin {method} {path} would reach {dead!r}, not a dead loopback")
    for method, path in ALLOW_PROBES:
        if _refused(md, method, path):
            bad.append(f"non-admin key refused on governed route {method} {path}")

    _clear()
    return bad


def _metadata_grants(docs: list[dict[str, Any]]) -> list[str]:
    """Names of key/team CRs whose declared metadata mentions a pass-through grant."""
    out = []
    for d in docs:
        if d.get("kind") in {"LiteLLMVirtualKey", "LiteLLMTeam"}:
            md = (d.get("spec") or {}).get("metadata") or {}
            if "allowed_passthrough_routes" in md:
                out.append(f"{d['kind']}/{d['metadata']['name']}")
    return out


def jev_model_findings(docs: list[dict[str, Any]]) -> list[str]:
    """models/jev.yaml: the decision model routes to OpenRouter's typesafe ids on the shared key."""
    models = [d for d in docs if d.get("kind") == "LiteLLMModel" and d["metadata"]["name"] == JEV_MODEL]
    if len(models) != 1:
        return [f"expected exactly one LiteLLMModel/{JEV_MODEL}, found {len(models)}"]
    spec = models[0].get("spec") or {}
    params = spec.get("params") or {}
    bad: list[str] = []
    if spec.get("modelName") != JEV_MODEL:
        bad.append(f"modelName {spec.get('modelName')!r} != {JEV_MODEL!r}")
    if not str(params.get("model", "")).startswith(JEV_UPSTREAM_PREFIX):
        bad.append(f"params.model {params.get('model')!r} is not an {JEV_UPSTREAM_PREFIX}* id")
    if params.get("apiKey") != JEV_CREDENTIAL:
        bad.append(f"params.apiKey {params.get('apiKey')!r} != {JEV_CREDENTIAL!r}")
    return bad


def jev_key_findings(docs: list[dict[str, Any]]) -> list[str]:
    """The declared key (virtualkeys/jev-decisions.yaml) and that no key names a raw typesafe id."""
    bad: list[str] = []
    for d in docs:
        if d.get("kind") == "LiteLLMVirtualKey":
            raw = [m for m in (d.get("spec") or {}).get("models") or [] if str(m).startswith("typesafe/")]
            if raw:
                bad.append(f"{d['metadata']['name']} allow-lists raw upstream ids {raw} (use the {JEV_MODEL!r} alias)")
    keys = [d for d in docs if d.get("kind") == "LiteLLMVirtualKey" and d["metadata"]["name"] == JEV_KEY_NAME]
    if len(keys) != 1:
        return bad + [f"expected exactly one LiteLLMVirtualKey/{JEV_KEY_NAME}, found {len(keys)}"]
    spec = keys[0].get("spec") or {}
    if spec.get("keyAlias") != JEV_KEY_NAME:
        bad.append(f"keyAlias {spec.get('keyAlias')!r} != {JEV_KEY_NAME!r}")
    if spec.get("models") != JEV_MODELS:
        bad.append(f"models {spec.get('models')!r} != {JEV_MODELS!r}")
    raw = spec.get("maxBudget")
    if not (isinstance(raw, str) and float(raw) > 0):
        bad.append(f"maxBudget {raw!r} must be a positive decimal string")
    secret = spec.get("secretName")
    pushes = [
        d
        for d in docs
        if d.get("kind") == "PushSecret"
        and ((d.get("spec") or {}).get("selector") or {}).get("secret", {}).get("name") == secret
    ]
    remotes = {
        (m.get("match") or {}).get("remoteRef", {}).get("remoteKey")
        for p in pushes
        for m in (p["spec"].get("data") or [])
    }
    if remotes != {JEV_REMOTE_KEY}:
        bad.append(f"PushSecret for {secret!r} writes {sorted(map(str, remotes))}, not {JEV_REMOTE_KEY!r}")
    return bad


def main() -> int:
    logging.disable(logging.CRITICAL)
    docs, source = load_rendered()
    proxy = _proxy_from(docs or [])
    record("rendered_litellmproxy_found", proxy is not None, source)
    if docs is None or proxy is None:
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
    # the skill's "no virtual key can be granted declaratively" claim is stale.
    for spec in PREFIXES:
        g = semantic_findings(
            entries,
            (spec,),
            metadata={"allowed_passthrough_routes": (spec.admin_paths or (AdminPath(spec.prefix + "/x", "", "", "", ""),))[0].path},
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
        if len(ours) >= 2:
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

    # The native Jev model and key; no key or team declares a pass-through grant.
    jm = jev_model_findings(docs)
    record("jev_model_routes_openrouter_typesafe", not jm, "; ".join(jm))
    j = jev_key_findings(docs)
    record("jev_decisions_key_allow_lists_only_the_alias", not j, "; ".join(j))
    others = _metadata_grants(docs)
    record("no_declared_passthrough_grant_on_any_key_or_team", not others, ", ".join(others))

    # Mutation proof.
    for name, change in (
        ("models_widened", {"models": JEV_MODELS + ["chat-local"]}),
        ("raw_upstream_id", {"models": ["typesafe/jev-1.14"]}),
        ("alias_renamed", {"keyAlias": "jev"}),
    ):
        mutated = copy.deepcopy(docs)
        for d in mutated:
            if d.get("kind") == "LiteLLMVirtualKey" and d["metadata"]["name"] == JEV_KEY_NAME:
                d["spec"].update(change)
        record(f"mutation_jev_key_{name}_is_caught", bool(jev_key_findings(mutated)))
    record(
        "mutation_jev_key_removed_is_caught",
        bool(jev_key_findings([d for d in docs if d.get("kind") != "LiteLLMVirtualKey"])),
    )
    for name, change in (
        ("wrong_provider", {"model": "openai/jev"}),
        ("wrong_credential", {"apiKey": "os.environ/ANTHROPIC_API_KEY"}),
    ):
        mutated = copy.deepcopy(docs)
        for d in mutated:
            if d.get("kind") == "LiteLLMModel" and d["metadata"]["name"] == JEV_MODEL:
                d["spec"]["params"].update(change)
        record(f"mutation_jev_model_{name}_is_caught", bool(jev_model_findings(mutated)))
    record(
        "mutation_jev_model_removed_is_caught",
        bool(jev_model_findings([d for d in docs if d.get("kind") != "LiteLLMModel"])),
    )
    team_grant = copy.deepcopy(docs) + [
        {
            "kind": "LiteLLMTeam",
            "metadata": {"name": "t"},
            "spec": {"metadata": {"allowed_passthrough_routes": "/openrouter"}},
        }
    ]
    record("mutation_declared_team_grant_is_caught", bool(_metadata_grants(team_grant)))
    reopened = copy.deepcopy(entries)
    reopened.insert(0, {
        "path": "/openrouter/alpha/decisions",
        "target": "https://openrouter.ai/api/alpha/decisions",
        "methods": ["POST"],
        "auth": True,
        "headers": {"Authorization": "Bearer os.environ/OPENROUTER_API_KEY"},
    })
    record("mutation_reopened_decisions_door_is_caught", bool(structural_findings(reopened, PREFIXES[0])))

    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed" + (f", {len(failed)} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
