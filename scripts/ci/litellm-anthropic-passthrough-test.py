#!/usr/bin/env python3
"""Contract tests for closing LiteLLM's built-in /anthropic pass-through.

Invariant (docs/ai-system/litellm/README.md § Anthropic pass-through route):
the subscription route must never reach a household-metered Anthropic
credential. LiteLLM v1.98.0 registers `/anthropic/{endpoint}` unconditionally
with no config/CRD gate and no virtual-key model allow-list check, so the
gateway is the narrowest layer that can close hostname traffic.

This test parses the HTTPRoute + HTTPRouteFilter the same way Envoy Gateway
consumes them (structured Gateway API objects, not source greps) and asserts:

  1. A PathPrefix `/anthropic` rule answers via ExtensionRef to a named
     HTTPRouteFilter and carries NO backendRefs (so the request never reaches
     the litellm Service).
  2. The referenced filter is a directResponse 404 with the exact plain-text
     body the live verification proved Envoy emits.
  3. The catch-all path to Service/litellm:4000 is untouched (governed
     /v1/chat/completions and /v1/messages stay open).
  4. ParentRefs stay envoy-internal only (never envoy-external).
  5. kustomize build of the app tree actually emits both objects into the
     inventory Flux applies (orphan YAML in the directory is not enough).

Live gateway proof (POST/GET /anthropic -> 404 text/plain "not found",
POST /v1/chat/completions + /v1/messages still 200 via ai-pr-review key) is
intentionally outside this unit: it needs the in-cluster envoy-internal
listener and a minted virtual key. That evidence is produced during the
pre-merge suspend/apply drill, not by CI.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "kubernetes" / "apps" / "base" / "ai" / "litellm" / "app"
HTTPRoute_PATH = APP_DIR / "httproute-internal.yaml"
KUST_PATH = APP_DIR / "kustomization.yaml"

FILTER_NAME = "litellm-anthropic-passthrough-block"
EXPECTED_STATUS = 404
EXPECTED_CONTENT_TYPE = "text/plain"
EXPECTED_BODY = "not found\n"

RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def load_all_yaml(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _path_match(rule: dict[str, Any]) -> tuple[str | None, str | None]:
    """Return (path_type, path_value) for the first path match on a rule."""
    for m in rule.get("matches") or []:
        path = m.get("path") or {}
        if path:
            return path.get("type"), path.get("value")
    return None, None


def _extension_refs(rule: dict[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for f in rule.get("filters") or []:
        if f.get("type") == "ExtensionRef" and isinstance(f.get("extensionRef"), dict):
            out.append(f["extensionRef"])
    return out


def test_httproute_filter_pair_from_file() -> None:
    docs = load_all_yaml(HTTPRoute_PATH)
    routes = [d for d in docs if d.get("kind") == "HTTPRoute"]
    filters = [d for d in docs if d.get("kind") == "HTTPRouteFilter"]

    record(
        "httproute_file_has_exactly_one_httproute",
        len(routes) == 1,
        f"n={len(routes)} kinds={[d.get('kind') for d in docs]}",
    )
    record(
        "httproute_file_has_exactly_one_httproutefilter",
        len(filters) == 1,
        f"n={len(filters)} names={[d.get('metadata', {}).get('name') for d in filters]}",
    )
    if not routes or not filters:
        return

    route = routes[0]
    filt = filters[0]
    md = route.get("metadata") or {}
    spec = route.get("spec") or {}

    record(
        "httproute_name_is_litellm_internal",
        md.get("name") == "litellm-internal",
        f"name={md.get('name')!r}",
    )
    record(
        "filter_name_is_passthrough_block",
        (filt.get("metadata") or {}).get("name") == FILTER_NAME,
        f"name={(filt.get('metadata') or {}).get('name')!r}",
    )
    record(
        "filter_api_version_is_envoy_gateway_v1alpha1",
        filt.get("apiVersion") == "gateway.envoyproxy.io/v1alpha1",
        f"apiVersion={filt.get('apiVersion')!r}",
    )

    # Semantic model of the filter Envoy Gateway will program.
    dr = (filt.get("spec") or {}).get("directResponse") or {}
    body = dr.get("body") or {}
    record(
        "filter_direct_response_is_404_text_plain_not_found",
        dr.get("statusCode") == EXPECTED_STATUS
        and dr.get("contentType") == EXPECTED_CONTENT_TYPE
        and body.get("type") == "Inline"
        and body.get("inline") == EXPECTED_BODY,
        f"directResponse={dr!r}",
    )
    record(
        "filter_has_no_backend_side_effects",
        set((filt.get("spec") or {}).keys()) == {"directResponse"},
        f"spec_keys={sorted((filt.get('spec') or {}).keys())}",
    )

    parents = spec.get("parentRefs") or []
    parent_keys = {
        (p.get("namespace"), p.get("name"), p.get("sectionName")) for p in parents
    }
    record(
        "httproute_parents_only_envoy_internal_https",
        parent_keys == {("network", "envoy-internal", "https")},
        f"parents={parent_keys}",
    )
    record(
        "httproute_never_attaches_envoy_external",
        all(p.get("name") != "envoy-external" for p in parents),
        f"parents={parents}",
    )

    rules = spec.get("rules") or []
    anthropic_rules = []
    catchall_rules = []
    other_rules = []
    for rule in rules:
        ptype, pval = _path_match(rule)
        if pval == "/anthropic" and ptype == "PathPrefix":
            anthropic_rules.append(rule)
        elif pval in (None, "/") and (ptype in (None, "PathPrefix")):
            # Gateway API default path is PathPrefix `/` when matches omitted.
            if pval == "/" or (not rule.get("matches") and rule.get("backendRefs")):
                catchall_rules.append(rule)
            else:
                other_rules.append(rule)
        else:
            other_rules.append(rule)

    record(
        "exactly_one_anthropic_pathprefix_rule",
        len(anthropic_rules) == 1,
        f"n={len(anthropic_rules)} other={len(other_rules)} catchall={len(catchall_rules)}",
    )
    record(
        "exactly_one_catchall_backend_rule",
        len(catchall_rules) == 1,
        f"n={len(catchall_rules)}",
    )
    record(
        "no_unexpected_path_rules",
        other_rules == [],
        f"other={[ _path_match(r) for r in other_rules ]}",
    )
    if len(anthropic_rules) != 1 or len(catchall_rules) != 1:
        return

    blocked = anthropic_rules[0]
    open_rule = catchall_rules[0]

    # Blocked rule: ExtensionRef only, no backends - the request must terminate
    # at Envoy before it can reach litellm's unconditional pass-through.
    refs = _extension_refs(blocked)
    record(
        "anthropic_rule_has_single_extensionref_to_block_filter",
        len(refs) == 1
        and refs[0].get("group") == "gateway.envoyproxy.io"
        and refs[0].get("kind") == "HTTPRouteFilter"
        and refs[0].get("name") == FILTER_NAME,
        f"refs={refs}",
    )
    record(
        "anthropic_rule_has_no_backend_refs",
        not (blocked.get("backendRefs") or []),
        f"backendRefs={blocked.get('backendRefs')}",
    )
    # Only the ExtensionRef filter - no RequestRedirect/URLRewrite that could
    # accidentally re-open the path onto another backend.
    filter_types = [f.get("type") for f in (blocked.get("filters") or [])]
    record(
        "anthropic_rule_filters_are_only_extensionref",
        filter_types == ["ExtensionRef"],
        f"filter_types={filter_types}",
    )

    backends = open_rule.get("backendRefs") or []
    backend_keys = {
        (b.get("name"), b.get("namespace"), b.get("port")) for b in backends
    }
    record(
        "catchall_still_points_at_litellm_service_4000",
        backend_keys == {("litellm", "ai", 4000)}
        or backend_keys == {("litellm", None, 4000)},
        f"backendRefs={backends}",
    )
    record(
        "catchall_has_no_extensionref_block",
        _extension_refs(open_rule) == [],
        f"refs={_extension_refs(open_rule)}",
    )


def test_kustomize_inventory_includes_both() -> None:
    """The objects must be in the kustomize inventory Flux applies."""
    kust = yaml.safe_load(KUST_PATH.read_text())
    resources = set(kust.get("resources") or [])
    record(
        "kustomization_lists_httproute_internal_yaml",
        "./httproute-internal.yaml" in resources,
        f"resources={sorted(resources)}",
    )

    try:
        built = subprocess.run(
            ["kubectl", "kustomize", str(APP_DIR)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        # No kubectl: still assert the multi-doc file is the listed resource.
        docs = load_all_yaml(HTTPRoute_PATH)
        kinds = {(d.get("kind"), d.get("metadata", {}).get("name")) for d in docs}
        record(
            "kustomize_emits_httproute_and_filter",
            {
                ("HTTPRoute", "litellm-internal"),
                ("HTTPRouteFilter", FILTER_NAME),
            }
            <= kinds,
            f"skipped_kubectl file_kinds={sorted(kinds)}",
        )
        return

    if built.returncode != 0:
        record("kustomize_emits_httproute_and_filter", False, built.stderr[-400:])
        return

    docs = [d for d in yaml.safe_load_all(built.stdout) if d]
    kinds_names = {(d.get("kind"), d.get("metadata", {}).get("name")) for d in docs}
    required = {
        ("HTTPRoute", "litellm-internal"),
        ("HTTPRouteFilter", FILTER_NAME),
    }
    missing = sorted(required - kinds_names)
    record(
        "kustomize_emits_httproute_and_filter",
        missing == [],
        f"missing={missing}",
    )

    # Re-check the semantic contract on the *rendered* objects, not just the
    # source file - catches a kustomize patch that could strip the block rule.
    route = next(
        d
        for d in docs
        if d.get("kind") == "HTTPRoute" and d["metadata"]["name"] == "litellm-internal"
    )
    filt = next(
        d
        for d in docs
        if d.get("kind") == "HTTPRouteFilter" and d["metadata"]["name"] == FILTER_NAME
    )
    rules = (route.get("spec") or {}).get("rules") or []
    blocked = [
        r
        for r in rules
        if _path_match(r) == ("PathPrefix", "/anthropic")
    ]
    record(
        "rendered_route_still_has_anthropic_block_rule",
        len(blocked) == 1 and not (blocked[0].get("backendRefs") or []),
        f"blocked_n={len(blocked)}",
    )
    dr = (filt.get("spec") or {}).get("directResponse") or {}
    record(
        "rendered_filter_still_direct_response_404",
        dr.get("statusCode") == EXPECTED_STATUS
        and (dr.get("body") or {}).get("inline") == EXPECTED_BODY,
        f"directResponse={dr!r}",
    )
    # Namespace must be ai so the ExtensionRef (same-namespace) resolves.
    record(
        "rendered_objects_are_namespaced_ai",
        route.get("metadata", {}).get("namespace") == "ai"
        and filt.get("metadata", {}).get("namespace") == "ai",
        f"route_ns={route.get('metadata', {}).get('namespace')!r} "
        f"filter_ns={filt.get('metadata', {}).get('namespace')!r}",
    )


def main() -> int:
    test_httproute_filter_pair_from_file()
    test_kustomize_inventory_includes_both()
    failed = [r for r in RESULTS if not r["ok"]]
    print(
        f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed"
        + (f", {len(failed)} failed" if failed else "")
    )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
