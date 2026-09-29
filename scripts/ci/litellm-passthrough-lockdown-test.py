#!/usr/bin/env python3
"""Contract tests for the admin-only lockdown of LiteLLM's built-in provider pass-throughs.

Invariant (skill litellm-proxy, references/passthrough-lockdown.md): the image's built-in `/openrouter/{endpoint}`
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

The one non-admin door (captain decision `jev-door-grant`, 2026-09-28): the
operator-minted `jev-decisions` key, granted `POST /openrouter/alpha/decisions`
by a one-time admin /key/update (kubernetes/apps/base/ai/litellm/README.md,
"Granting the jev-decisions key"). The grant itself lives in LiteLLM's DB, so
CI asserts the parts Git owns:

  6. The CR exists with alias `jev-decisions`, models exactly
     [typesafe/jev-1.13], a positive budget, a PushSecret to
     `litellm-consumer-jev-decisions`, and NO `spec.metadata` - the operator
     would send a declared one on every reconcile and LiteLLM replaces the
     whole metadata object, deleting the hand-applied grant.
  7. No LiteLLMVirtualKey or LiteLLMTeam declares a grant at all.
  8. The grant list the README runbook applies admits POST /openrouter/alpha/decisions to the real target,
     and every other probe is refused or lands on a dead loopback (the grant
     is prefix-matched and method-blind, so only the registry order keeps
     subpaths and other methods off the provider).

Each of 6-8 is re-run against mutations to prove it can fail.

Live proof (virtual keys 403 on each prefix, master key served on the exact
paths, chat and embedding keys unaffected, the granted key served on the door
only) needs the running proxy and is produced by the live drill, not by CI.
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

# The one non-admin door (captain decision `jev-door-grant`, 2026-09-28): the
# operator-minted `jev-decisions` key, granted by a one-time admin /key/update
# that puts JEV_GRANT in its metadata (README "Granting the jev-decisions key").
JEV_KEY_NAME = "jev-decisions"
JEV_MODELS = ["typesafe/jev-1.13"]
JEV_REMOTE_KEY = "litellm-consumer-jev-decisions"
JEV_GRANT = ["/openrouter/alpha/decisions"]
JEV_DOOR = ("POST", "/openrouter/alpha/decisions", "https://openrouter.ai/api/alpha/decisions")
# Route-allowed for the granted key by prefix match or method-blindness; each
# must resolve to the dead catch-all, never the provider.
JEV_EXTRA_PROBES: tuple[tuple[str, str], ...] = (
    ("GET", "/openrouter/alpha/decisions"),
    ("PUT", "/openrouter/alpha/decisions"),
    ("POST", "/openrouter/alpha/decisions/"),
    ("POST", "/openrouter/alpha/decisions/x"),
    ("GET", "/openrouter/alpha/decisions/gen-dec-1"),
)

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
    for f in sorted((APP_DIR / "virtualkeys").glob("*.yaml")):
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


def granted_findings(entries: list[dict[str, Any]], grant: list[str]) -> list[str]:
    """The jev-decisions grant must open exactly one real door and nothing else.

    A key whose metadata carries `grant` (the list the README runbook applies)
    must be admitted on POST /openrouter/alpha/decisions and sent to the real
    target. Every other probe must either be refused or resolve to a dead
    loopback: `allowed_passthrough_routes` is prefix-matched and method-blind,
    so a subpath or another method IS route-allowed, and only the registry
    order (exact POST entry first, dead catch-all last) keeps it off the
    provider.
    """
    _register(entries)
    bad: list[str] = []
    md = {"allowed_passthrough_routes": list(grant)}
    method, path, target = JEV_DOOR
    if _refused(md, method, path):
        bad.append(f"granted key refused on {method} {path}")
    if _target_for(method, path) != target:
        bad.append(f"granted {method} {path} would be sent to {_target_for(method, path)!r}, not {target!r}")
    probes = [p for spec in PREFIXES for p in spec.deny_probes] + list(JEV_EXTRA_PROBES)
    for pm, pp in probes:
        if (pm, pp) == (method, path) or _refused(md, pm, pp):
            continue
        reach = _target_for(pm, pp)
        if reach is None or urlparse(str(reach)).hostname not in LOOPBACK_HOSTS:
            bad.append(f"granted key reaches {reach!r} on {pm} {pp}")
    for pm, pp in ALLOW_PROBES:
        if _refused(md, pm, pp):
            bad.append(f"granted key refused on governed route {pm} {pp}")
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


def jev_key_findings(docs: list[dict[str, Any]]) -> list[str]:
    """The declared half of the jev-decisions door (virtualkeys/jev-decisions.yaml)."""
    bad: list[str] = []
    keys = [d for d in docs if d.get("kind") == "LiteLLMVirtualKey" and d["metadata"]["name"] == JEV_KEY_NAME]
    if len(keys) != 1:
        return [f"expected exactly one LiteLLMVirtualKey/{JEV_KEY_NAME}, found {len(keys)}"]
    spec = keys[0].get("spec") or {}
    if spec.get("keyAlias") != JEV_KEY_NAME:
        bad.append(f"keyAlias {spec.get('keyAlias')!r} != {JEV_KEY_NAME!r} (the runbook looks the key up by alias)")
    if spec.get("models") != JEV_MODELS:
        bad.append(f"models {spec.get('models')!r} != {JEV_MODELS!r}")
    if "metadata" in spec:
        bad.append(
            "spec.metadata is declared: the operator would send it on every /key/update and LiteLLM "
            "replaces the whole metadata object, deleting the hand-applied grant"
        )
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

    # The jev-decisions door: the declared key, the grant it gets, and that
    # no other key or team declares a grant at all.
    j = jev_key_findings(docs)
    record("jev_decisions_key_declared_without_metadata", not j, "; ".join(j))
    others = _metadata_grants(docs)
    record("no_declared_passthrough_grant_on_any_key_or_team", not others, ", ".join(others))
    g = granted_findings(entries, JEV_GRANT)
    record("jev_grant_opens_exactly_the_decisions_door", not g, "; ".join(g))

    # Mutation proof for the door.
    jev_mutations: dict[str, list[dict[str, Any]]] = {}
    for name, change in (
        ("metadata_declared", {"metadata": {"purpose": "x"}}),
        ("models_widened", {"models": JEV_MODELS + ["chat-local"]}),
        ("alias_renamed", {"keyAlias": "jev"}),
    ):
        mutated = copy.deepcopy(docs)
        for d in mutated:
            if d.get("kind") == "LiteLLMVirtualKey" and d["metadata"]["name"] == JEV_KEY_NAME:
                d["spec"].update(change)
        jev_mutations[name] = mutated
    for name, mutated in jev_mutations.items():
        record(f"mutation_jev_{name}_is_caught", bool(jev_key_findings(mutated)))
    record(
        "mutation_jev_key_removed_is_caught",
        bool(jev_key_findings([d for d in docs if d.get("kind") != "LiteLLMVirtualKey"])),
    )
    team_grant = copy.deepcopy(docs) + [
        {
            "kind": "LiteLLMTeam",
            "metadata": {"name": "t"},
            "spec": {"metadata": {"allowed_passthrough_routes": "/openrouter"}},
        }
    ]
    record("mutation_declared_team_grant_is_caught", bool(_metadata_grants(team_grant)))
    for name, grant in (("prefix_widened", ["/openrouter", "/anthropic"]), ("anthropic_added", JEV_GRANT + ["/anthropic/v1/messages"])):
        record(f"mutation_jev_grant_{name}_is_caught", bool(granted_findings(entries, grant)))
    swapped = copy.deepcopy(entries)
    ours = [i for i, e in enumerate(entries) if _under("/openrouter", str(e.get("path", "")))]
    swapped[ours[0]], swapped[ours[-1]] = swapped[ours[-1]], swapped[ours[0]]
    record("mutation_jev_order_swapped_is_caught", bool(granted_findings(swapped, JEV_GRANT)))
    wide = copy.deepcopy(entries)
    for e in wide:
        if e.get("path") == "/openrouter/alpha/decisions":
            e["include_subpath"] = True
            e.pop("methods", None)
    record("mutation_jev_door_entry_widened_is_caught", bool(granted_findings(wide, JEV_GRANT)))

    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed" + (f", {len(failed)} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
