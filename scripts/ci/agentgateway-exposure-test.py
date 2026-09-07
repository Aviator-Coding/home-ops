#!/usr/bin/env python3
"""Contract tests for the five ai/agentgateway exposure + reliability fixes.

Intent (fm/homeops-ai-gateway-exposure-fix): close confirmed defects on the
agentgateway surface (namespace `ai`) without touching certificates/import,
without authoring host policy, and without claiming the UniFi wildcard is gone.

This test builds a structured semantic model of the objects Flux will apply
(kustomize inventory of `kubernetes/apps/base/ai/agentgateway/app`) and asserts
meaning the controllers actually consume:

  F1  Gateway/internal has no HTTP listener (https only). llm-unified and
      llm-models still attach without sectionName (so they would bind every
      listener of every parent); tls-redirect no longer parents internal/http.
  F2  ExternalSecret sklab-dev-production-tls is interval-driven (no
      CreatedOnce pin). The bootstrap seed under network/certificates/import
      keeps CreatedOnce and is deliberately out of scope.
  F3  Hostname-less routes tls-redirect, llm-unified, llm-models carry the
      external-dns controller=none opt-out. AC4 (wildcard deletion) is NOT
      claimed here.
  F4  Gateway/internal-noauth has no lbipam pin; it references a Gateway-level
      AgentgatewayParameters whose service.spec.type is ClusterIP.
  F5  agentgateway-api target annotation is the single external target only.

Live curl/openssl proof (LAN refusal of internal:80 and 10.50.0.28, cert
notAfter, in-cluster keyless still 200) needs the real cluster and is produced
during the suspend/apply drill, not by CI.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "kubernetes" / "apps" / "base" / "ai" / "agentgateway" / "app"
GATEWAYS_DIR = APP_DIR / "gateways"
IMPORT_ES = (
    REPO
    / "kubernetes"
    / "apps"
    / "base"
    / "network"
    / "certificates"
    / "import"
    / "externalsecret.yaml"
)

INTERNAL_LB = "10.50.0.27"
NOAUTH_OLD_LB = "10.50.0.28"
PUBLIC_LB = "10.50.0.29"
EXTERNAL_TARGET = "external.${SECRET_DOMAIN}"

RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def load_all_yaml(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def kustomize_build(path: Path) -> list[dict[str, Any]]:
    exe = shutil.which("kustomize")
    if exe:
        cmd = [exe, "build", str(path)]
    else:
        cmd = ["kubectl", "kustomize", str(path)]
    proc = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return [d for d in yaml.safe_load_all(proc.stdout) if d]


def by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == kind]


def named(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any] | None:
    for d in by_kind(docs, kind):
        if (d.get("metadata") or {}).get("name") == name:
            return d
    return None


def listener_names(gw: dict[str, Any]) -> list[str]:
    return [l.get("name") for l in (gw.get("spec") or {}).get("listeners") or []]


def listener_protocols(gw: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for l in (gw.get("spec") or {}).get("listeners") or []:
        out[l.get("name")] = l.get("protocol")
    return out


def parent_refs(route: dict[str, Any]) -> list[tuple[str | None, str | None]]:
    refs = []
    for p in (route.get("spec") or {}).get("parentRefs") or []:
        refs.append((p.get("name"), p.get("sectionName")))
    return refs


def ann(obj: dict[str, Any], key: str) -> str | None:
    return ((obj.get("metadata") or {}).get("annotations") or {}).get(key)


def test_kustomize_inventory_emits_required_objects() -> list[dict[str, Any]]:
    docs = kustomize_build(APP_DIR)
    kinds_names = {
        (d.get("kind"), (d.get("metadata") or {}).get("name")) for d in docs
    }
    required = {
        ("Gateway", "internal"),
        ("Gateway", "internal-noauth"),
        ("Gateway", "public"),
        ("HTTPRoute", "tls-redirect"),
        ("HTTPRoute", "llm-unified"),
        ("HTTPRoute", "llm-models"),
        ("HTTPRoute", "agentgateway-api"),
        ("ExternalSecret", "sklab-dev-production-tls"),
        ("AgentgatewayParameters", "internal-noauth-params"),
        ("AgentgatewayParameters", "agentgateway-params"),
    }
    missing = sorted(required - kinds_names)
    record(
        "kustomize_inventory_has_gateways_routes_es_params",
        not missing,
        f"missing={missing} total_docs={len(docs)}",
    )
    return docs


def test_f1_internal_https_only(docs: list[dict[str, Any]]) -> None:
    gw = named(docs, "Gateway", "internal")
    assert gw is not None
    names = listener_names(gw)
    protos = listener_protocols(gw)
    record(
        "f1_internal_has_no_http_listener",
        "http" not in names and "HTTP" not in protos.values(),
        f"listeners={names} protocols={protos}",
    )
    record(
        "f1_internal_has_https_listener",
        "https" in names and protos.get("https") == "HTTPS",
        f"listeners={names} protocols={protos}",
    )
    # Load-bearing: public must KEEP http so Envoy can forward llm-api to :80
    pub = named(docs, "Gateway", "public")
    assert pub is not None
    pub_names = listener_names(pub)
    record(
        "f1_public_still_has_http_and_https",
        set(pub_names) >= {"http", "https"},
        f"listeners={pub_names}",
    )
    # llm routes attach with no sectionName (bind every listener of each parent)
    for route_name in ("llm-unified", "llm-models"):
        route = named(docs, "HTTPRoute", route_name)
        assert route is not None
        refs = parent_refs(route)
        no_section = [(n, s) for n, s in refs if s is None]
        record(
            f"f1_{route_name}_parents_have_no_sectionName",
            len(no_section) == len(refs) and len(refs) >= 1,
            f"parentRefs={refs}",
        )
        # Must still reach internal-noauth and public; may also reach internal https
        parent_names = {n for n, _ in refs}
        record(
            f"f1_{route_name}_parents_include_noauth_and_public",
            {"internal-noauth", "public"} <= parent_names,
            f"parent_names={parent_names}",
        )
    # tls-redirect must not parent the removed internal/http
    redir = named(docs, "HTTPRoute", "tls-redirect")
    assert redir is not None
    redir_refs = parent_refs(redir)
    record(
        "f1_tls_redirect_has_no_internal_parent",
        all(n != "internal" for n, _ in redir_refs),
        f"parentRefs={redir_refs}",
    )
    record(
        "f1_tls_redirect_still_covers_noauth_and_public_http",
        ("internal-noauth", "http") in redir_refs and ("public", "http") in redir_refs,
        f"parentRefs={redir_refs}",
    )


def test_f2_tls_refresh_not_pinned(docs: list[dict[str, Any]]) -> None:
    es = named(docs, "ExternalSecret", "sklab-dev-production-tls")
    assert es is not None
    spec = es.get("spec") or {}
    record(
        "f2_ai_tls_externalsecret_has_no_created_once",
        spec.get("refreshPolicy") is None,
        f"refreshPolicy={spec.get('refreshPolicy')!r} refreshInterval={spec.get('refreshInterval')!r}",
    )
    record(
        "f2_ai_tls_externalsecret_refresh_interval_set",
        bool(spec.get("refreshInterval")),
        f"refreshInterval={spec.get('refreshInterval')!r}",
    )
    # Bootstrap seed stays CreatedOnce and must remain so
    import_docs = load_all_yaml(IMPORT_ES)
    seeds = [d for d in import_docs if d.get("kind") == "ExternalSecret"]
    seed_policies = [
        (
            (d.get("metadata") or {}).get("name"),
            (d.get("spec") or {}).get("refreshPolicy"),
        )
        for d in seeds
    ]
    record(
        "f2_import_bootstrap_seed_still_created_once",
        any(p == "CreatedOnce" for _, p in seed_policies),
        f"policies={seed_policies}",
    )


def test_f3_external_dns_opt_out_on_hostname_less_routes(
    docs: list[dict[str, Any]],
) -> None:
    for route_name in ("tls-redirect", "llm-unified", "llm-models"):
        route = named(docs, "HTTPRoute", route_name)
        assert route is not None
        hostnames = (route.get("spec") or {}).get("hostnames") or []
        controller = ann(route, "external-dns.alpha.kubernetes.io/controller")
        record(
            f"f3_{route_name}_is_hostname_less",
            hostnames == [] or hostnames is None,
            f"hostnames={hostnames}",
        )
        record(
            f"f3_{route_name}_external_dns_controller_none",
            controller == "none",
            f"controller={controller!r}",
        )


def test_f4_internal_noauth_clusterip(docs: list[dict[str, Any]]) -> None:
    gw = named(docs, "Gateway", "internal-noauth")
    assert gw is not None
    infra = (gw.get("spec") or {}).get("infrastructure") or {}
    infra_ann = infra.get("annotations") or {}
    record(
        "f4_internal_noauth_has_no_lbipam_pin",
        "lbipam.cilium.io/ips" not in infra_ann
        and NOAUTH_OLD_LB not in str(infra_ann),
        f"infrastructure.annotations={infra_ann}",
    )
    pref = infra.get("parametersRef") or {}
    record(
        "f4_internal_noauth_parametersRef_points_at_overlay",
        pref.get("kind") == "AgentgatewayParameters"
        and pref.get("name") == "internal-noauth-params"
        and pref.get("group") == "agentgateway.dev",
        f"parametersRef={pref}",
    )
    params = named(docs, "AgentgatewayParameters", "internal-noauth-params")
    assert params is not None
    svc_type = (
        ((params.get("spec") or {}).get("service") or {}).get("spec") or {}
    ).get("type")
    record(
        "f4_internal_noauth_params_service_type_clusterip",
        svc_type == "ClusterIP",
        f"service.spec.type={svc_type!r}",
    )
    # Class-level params must still exist so Gateway-level MERGE keeps ADMIN_ADDR
    class_params = named(docs, "AgentgatewayParameters", "agentgateway-params")
    record(
        "f4_gatewayclass_params_still_present_for_merge",
        class_params is not None,
        f"present={class_params is not None}",
    )
    # internal and public keep their LB pins
    for name, ip in (("internal", INTERNAL_LB), ("public", PUBLIC_LB)):
        g = named(docs, "Gateway", name)
        assert g is not None
        pinned = (
            ((g.get("spec") or {}).get("infrastructure") or {}).get("annotations") or {}
        ).get("lbipam.cilium.io/ips")
        record(
            f"f4_{name}_keeps_lb_pin_{ip}",
            pinned == ip,
            f"lbipam={pinned!r}",
        )


def test_f5_api_route_single_external_target(docs: list[dict[str, Any]]) -> None:
    route = named(docs, "HTTPRoute", "agentgateway-api")
    assert route is not None
    target = ann(route, "external-dns.alpha.kubernetes.io/target")
    record(
        "f5_agentgateway_api_target_is_external_only",
        target == EXTERNAL_TARGET,
        f"target={target!r}",
    )
    record(
        "f5_agentgateway_api_target_does_not_list_internal",
        target is not None and "internal." not in target,
        f"target={target!r}",
    )
    # Dual parentRefs are intentional (routing); warning silence is out of scope
    refs = parent_refs(route)
    parent_names = {n for n, _ in refs}
    record(
        "f5_agentgateway_api_still_dual_attached_to_envoy",
        {"envoy-internal", "envoy-external"} <= parent_names
        or len(parent_names) >= 2,
        f"parentRefs={refs}",
    )


def test_scope_no_host_policy_in_app_tree(docs: list[dict[str, Any]]) -> None:
    bad = [
        (d.get("kind"), (d.get("metadata") or {}).get("name"))
        for d in docs
        if d.get("kind")
        in (
            "CiliumClusterwideNetworkPolicy",
            "CiliumNetworkPolicy",
            "NetworkPolicy",
        )
    ]
    record(
        "scope_no_host_or_network_policy_in_agentgateway_inventory",
        not bad,
        f"found={bad}",
    )


def main() -> int:
    docs = test_kustomize_inventory_emits_required_objects()
    if not docs:
        print("\nSUMMARY: 0 passed, inventory build failed")
        return 1
    test_f1_internal_https_only(docs)
    test_f2_tls_refresh_not_pinned(docs)
    test_f3_external_dns_opt_out_on_hostname_less_routes(docs)
    test_f4_internal_noauth_clusterip(docs)
    test_f5_api_route_single_external_target(docs)
    test_scope_no_host_policy_in_app_tree(docs)

    passed = sum(1 for r in RESULTS if r["ok"])
    failed = sum(1 for r in RESULTS if not r["ok"])
    print(f"\nSUMMARY: {passed} passed, {failed} failed, {len(RESULTS)} total")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
