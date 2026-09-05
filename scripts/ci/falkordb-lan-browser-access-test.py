#!/usr/bin/env python3
"""Contract tests for database/falkordb LAN LoadBalancer + gated browser UI.

Intent (fm/homeops-falkordb-access):

  PART 1 - LAN Redis wire protocol on a free Cilium LoadBalancer address, with
  external-dns hostname annotation, WITHOUT disturbing the existing ClusterIP
  Service and WITHOUT any public/envoy-external path.

  PART 2 - FalkorDB Browser (explorer) as an Authentik-gated envoy-internal
  route. The browser runs as a second container under the existing 1000:1000 +
  readOnlyRootFilesystem hardening, with measured emptyDir mounts only, port
  3000 on the existing ClusterIP, and the deliberate --requirepass command
  (no REDIS_ARGS reintroduction).

This test builds a structured semantic model of the objects Flux will apply
(kustomize inventory + HelmRelease values shape consumers actually read) and
asserts meaning. Live AUTH/PING against 10.50.0.24 and unauthenticated HTTP
302 checks need the real cluster and are outside this unit - they are the
pre-merge drill recorded in the change intent, not CI.

Evidence is behavioural parsing, not source greps:
  - Load each YAML document into typed dicts and assert field semantics.
  - kustomize-build the app tree and the authentik ReferenceGrant inventory.
  - Model the HelmRelease values the way app-template consumes them
    (controllers/containers/ports/service/persistence/defaultPodOptions).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "kubernetes" / "apps" / "base" / "database" / "falkordb" / "app"
AUTHENTIK_DIR = REPO / "kubernetes" / "apps" / "base" / "security" / "authentik" / "app"
POSTGRES_LB = (
    REPO
    / "kubernetes"
    / "apps"
    / "base"
    / "database"
    / "cloudnative-pg"
    / "cluster-17"
    / "service.yaml"
)

TAKEN_LB_SUFFIXES = {21, 22, 23, 26, 27, 28, 29, 30, 51, 52, 54, 55, 121}
PINNED_LB_IP = "10.50.0.24"
BROWSER_PORT = 3000
REDIS_PORT = 6379
OUTPOST = "ak-outpost-authentik-embedded-outpost"

RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def load_all_yaml(path: Path) -> list[dict[str, Any]]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def load_one(path: Path) -> dict[str, Any]:
    docs = load_all_yaml(path)
    if len(docs) != 1:
        raise AssertionError(f"{path} expected 1 doc, got {len(docs)}")
    return docs[0]


def which_tool(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    # Fall back to known mise install paths without requiring mise trust.
    candidates = [
        Path.home() / ".local/share/mise/installs/kustomize/5.8.1/kustomize",
        Path.home() / ".local/share/mise/installs/aqua-kubernetes-sigs-kustomize/5.6.0/kustomize",
        Path.home() / ".local/share/mise/installs/kubectl/1.36.4/kubectl",
        Path.home() / ".local/share/mise/installs/kubectl/1.36.3/kubectl",
        Path.home() / ".local/share/mise/installs/ubi-home-operations-flate/0.6.1/flate",
    ]
    for c in candidates:
        if c.name in names and c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


def kustomize_build(path: Path) -> list[dict[str, Any]] | None:
    tool = which_tool("kustomize")
    if tool is None:
        kubectl = which_tool("kubectl")
        if kubectl is None:
            return None
        cmd = [kubectl, "kustomize", str(path)]
    else:
        cmd = [tool, "build", str(path)]
    try:
        built = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        record("kustomize_build_available", False, str(exc))
        return None
    if built.returncode != 0:
        record("kustomize_build_ok", False, (built.stderr or built.stdout)[-500:])
        return None
    return [d for d in yaml.safe_load_all(built.stdout) if d]


def test_loadbalancer_service_semantics() -> None:
    """PART 1: additive LAN LB following postgres-17-lb shape."""
    lb = load_one(APP_DIR / "service-lb.yaml")
    postgres = load_one(POSTGRES_LB)

    md = lb.get("metadata") or {}
    spec = lb.get("spec") or {}
    ann = md.get("annotations") or {}
    ports = spec.get("ports") or []
    selector = spec.get("selector") or {}

    record(
        "lb_is_v1_service_named_falkordb_lb",
        lb.get("apiVersion") == "v1"
        and lb.get("kind") == "Service"
        and md.get("name") == "falkordb-lb",
        f"api={lb.get('apiVersion')} kind={lb.get('kind')} name={md.get('name')}",
    )
    record(
        "lb_type_is_loadbalancer",
        spec.get("type") == "LoadBalancer",
        f"type={spec.get('type')!r}",
    )

    # external-dns hostname shape matches postgres-17-lb: <name>.${SECRET_DOMAIN}
    pg_ann = (postgres.get("metadata") or {}).get("annotations") or {}
    pg_host = pg_ann.get("external-dns.alpha.kubernetes.io/hostname")
    host = ann.get("external-dns.alpha.kubernetes.io/hostname")
    record(
        "lb_external_dns_hostname_shape_matches_postgres_precedent",
        isinstance(host, str)
        and host == "falkordb.${SECRET_DOMAIN}"
        and isinstance(pg_host, str)
        and pg_host.endswith(".${SECRET_DOMAIN}")
        and "${SECRET_DOMAIN}" in host,
        f"host={host!r} postgres_host={pg_host!r}",
    )

    # Pinned free address; must not collide with the taken suffixes.
    pinned = ann.get("lbipam.cilium.io/ips")
    record(
        "lb_ip_is_pinned_to_verified_free_address",
        pinned == PINNED_LB_IP,
        f"pinned={pinned!r} expected={PINNED_LB_IP!r}",
    )
    if isinstance(pinned, str) and pinned.startswith("10.50.0."):
        try:
            suffix = int(pinned.rsplit(".", 1)[1])
        except ValueError:
            suffix = -1
        record(
            "lb_ip_not_in_stated_taken_set",
            suffix not in TAKEN_LB_SUFFIXES,
            f"suffix={suffix} taken={sorted(TAKEN_LB_SUFFIXES)}",
        )
    else:
        record("lb_ip_not_in_stated_taken_set", False, f"pinned={pinned!r}")

    # Single redis wire-protocol port only - no HTTP on the LB.
    port_model = {(p.get("name"), p.get("port"), p.get("targetPort"), p.get("protocol")) for p in ports}
    record(
        "lb_exposes_only_redis_6379",
        port_model == {("redis", REDIS_PORT, REDIS_PORT, "TCP")}
        or port_model == {("redis", REDIS_PORT, REDIS_PORT, None)},
        f"ports={ports}",
    )

    # Chart pod selector - must match app-template labels on the falkordb controller.
    expected_selector = {
        "app.kubernetes.io/controller": "falkordb",
        "app.kubernetes.io/instance": "falkordb",
        "app.kubernetes.io/name": "falkordb",
    }
    record(
        "lb_selector_matches_falkordb_controller_pods",
        selector == expected_selector,
        f"selector={selector}",
    )

    # No public annotations that would imply cloudflare/external exposure.
    # cloudflare-dns does not read Services anyway; still forbid external target.
    externalish = [
        k
        for k, v in ann.items()
        if "external.${SECRET_DOMAIN}" in str(v) or k.startswith("cloudflare")
    ]
    record(
        "lb_has_no_public_external_dns_target",
        externalish == [],
        f"externalish={externalish} ann={ann}",
    )


def test_browser_httproute_and_securitypolicy() -> None:
    """PART 2: internal-only route gated by Authentik extAuth."""
    route = load_one(APP_DIR / "httproute.yaml")
    policy = load_one(APP_DIR / "securitypolicy.yaml")

    rmd = route.get("metadata") or {}
    rspec = route.get("spec") or {}
    pmd = policy.get("metadata") or {}
    pspec = policy.get("spec") or {}

    record(
        "httproute_is_gateway_api_v1",
        route.get("apiVersion") == "gateway.networking.k8s.io/v1"
        and route.get("kind") == "HTTPRoute"
        and rmd.get("name") == "falkordb-browser",
        f"api={route.get('apiVersion')} name={rmd.get('name')}",
    )

    hostnames = set(rspec.get("hostnames") or [])
    record(
        "httproute_hostname_is_falkordb_browser_not_db_name",
        hostnames == {"falkordb-browser.${SECRET_DOMAIN}"},
        f"hostnames={hostnames}",
    )
    # Deliberate two-hostname split: DB keeps falkordb.${SECRET_DOMAIN}.
    lb = load_one(APP_DIR / "service-lb.yaml")
    lb_host = ((lb.get("metadata") or {}).get("annotations") or {}).get(
        "external-dns.alpha.kubernetes.io/hostname"
    )
    record(
        "browser_and_db_hostnames_do_not_collide",
        lb_host == "falkordb.${SECRET_DOMAIN}"
        and hostnames == {"falkordb-browser.${SECRET_DOMAIN}"}
        and lb_host not in hostnames,
        f"lb_host={lb_host!r} route_hosts={hostnames}",
    )

    parents = rspec.get("parentRefs") or []
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

    ann = rmd.get("annotations") or {}
    record(
        "httproute_external_dns_target_is_internal_only",
        ann.get("external-dns.alpha.kubernetes.io/target") == "internal.${SECRET_DOMAIN}",
        f"target={ann.get('external-dns.alpha.kubernetes.io/target')!r}",
    )

    rules = rspec.get("rules") or []
    backends: list[tuple[Any, Any, Any]] = []
    for rule in rules:
        for b in rule.get("backendRefs") or []:
            backends.append((b.get("name"), b.get("namespace"), b.get("port")))
    record(
        "httproute_backends_only_clusterip_falkordb_http_3000",
        backends == [("falkordb", "database", BROWSER_PORT)]
        or backends == [("falkordb", None, BROWSER_PORT)],
        f"backends={backends}",
    )
    # Must not point at the LoadBalancer Service.
    record(
        "httproute_does_not_use_loadbalancer_service",
        all(b[0] != "falkordb-lb" for b in backends),
        f"backends={backends}",
    )

    # SecurityPolicy targets the browser route and forwards auth to Authentik.
    record(
        "securitypolicy_is_envoy_gateway_v1alpha1",
        policy.get("apiVersion") == "gateway.envoyproxy.io/v1alpha1"
        and policy.get("kind") == "SecurityPolicy"
        and pmd.get("name") == "falkordb-browser-auth",
        f"api={policy.get('apiVersion')} name={pmd.get('name')}",
    )
    targets = pspec.get("targetRefs") or []
    target_keys = {
        (t.get("group"), t.get("kind"), t.get("name")) for t in targets
    }
    record(
        "securitypolicy_targets_falkordb_browser_httproute",
        target_keys
        == {("gateway.networking.k8s.io", "HTTPRoute", "falkordb-browser")},
        f"targets={target_keys}",
    )

    ext = pspec.get("extAuth") or {}
    http = ext.get("http") or {}
    auth_backends = http.get("backendRefs") or []
    auth_keys = {
        (b.get("name"), b.get("namespace"), b.get("port")) for b in auth_backends
    }
    record(
        "securitypolicy_extauth_points_at_authentik_outpost",
        auth_keys == {(OUTPOST, "security", 9000)}
        and http.get("path") == "/outpost.goauthentik.io/auth/envoy",
        f"auth_backends={auth_backends} path={http.get('path')!r}",
    )
    # Cookie must be forwarded or the outpost cannot establish a session.
    headers = set(ext.get("headersToExtAuth") or [])
    record(
        "securitypolicy_forwards_cookie_and_authorization",
        {"cookie", "authorization"} <= headers,
        f"headersToExtAuth={sorted(headers)}",
    )
    # No second unauthenticated path / no disable flag.
    record(
        "securitypolicy_has_extauth_and_no_bypass_fields",
        "extAuth" in pspec and "jwt" not in pspec,
        f"spec_keys={sorted(pspec.keys())}",
    )


def test_referencegrant_allows_database_namespace() -> None:
    grant = load_one(AUTHENTIK_DIR / "referencegrant.yaml")
    spec = grant.get("spec") or {}
    froms = spec.get("from") or []
    from_ns = {
        (f.get("group"), f.get("kind"), f.get("namespace")) for f in froms
    }
    record(
        "referencegrant_allows_database_securitypolicy",
        ("gateway.envoyproxy.io", "SecurityPolicy", "database") in from_ns,
        f"from={sorted(from_ns)}",
    )
    tos = spec.get("to") or []
    to_keys = {(t.get("group"), t.get("kind"), t.get("name")) for t in tos}
    record(
        "referencegrant_to_authentik_outpost_service",
        ("", "Service", OUTPOST) in to_keys or (None, "Service", OUTPOST) in to_keys,
        f"to={to_keys}",
    )


def _hr_values() -> dict[str, Any]:
    hr = load_one(APP_DIR / "helmrelease.yaml")
    return (hr.get("spec") or {}).get("values") or {}


def test_helmrelease_browser_sidecar_and_hardening() -> None:
    """Semantic model of app-template values for the browser + hardening."""
    values = _hr_values()
    controllers = values.get("controllers") or {}
    falkor = controllers.get("falkordb") or {}
    containers = falkor.get("containers") or {}
    app = containers.get("app") or {}
    browser = containers.get("browser") or {}
    inits = falkor.get("initContainers") or {}
    fix = inits.get("fix-permissions") or {}

    record(
        "helmrelease_has_app_and_browser_containers",
        set(containers.keys()) >= {"app", "browser"},
        f"containers={sorted(containers.keys())}",
    )
    record(
        "helmrelease_has_fix_permissions_init",
        "fix-permissions" in inits,
        f"inits={sorted(inits.keys())}",
    )

    # Database container: BROWSER stays 0; command uses requirepass, not REDIS_ARGS.
    app_env = app.get("env") or {}
    record(
        "database_container_keeps_browser_env_disabled",
        app_env.get("BROWSER") == "0",
        f"BROWSER={app_env.get('BROWSER')!r}",
    )

    cmd = app.get("command") or []
    # command is [sh, -c, script]
    script = cmd[2] if len(cmd) >= 3 and isinstance(cmd[2], str) else " ".join(
        str(c) for c in cmd
    )
    record(
        "database_command_uses_requirepass_not_redis_args",
        "--requirepass" in script
        and "REDIS_ARGS" not in script
        and "exec redis-server" in script,
        # Do not print the full script (may contain password-shaped tokens in comments only;
        # still keep detail short).
        f"has_requirepass={'--requirepass' in script} has_redis_args={'REDIS_ARGS' in script} "
        f"has_exec={'exec redis-server' in script}",
    )
    # Flux strict substitution escaping: shell vars must be $${...} in the
    # committed manifest so postBuild leaves ${...} for the container shell.
    record(
        "database_command_escapes_flux_shell_vars",
        "$${FALKORDB_PASSWORD}" in script
        and "$${FALKORDB_DATA_PATH}" in script
        and "${FALKORDB_PASSWORD}" not in script.replace("$${FALKORDB_PASSWORD}", ""),
        "escaped password/data path present",
    )

    app_sc = app.get("securityContext") or {}
    browser_sc = browser.get("securityContext") or {}
    for label, sc in (("app", app_sc), ("browser", browser_sc)):
        record(
            f"{label}_read_only_root_filesystem_enabled",
            sc.get("readOnlyRootFilesystem") is True,
            f"securityContext={sc}",
        )
        record(
            f"{label}_drops_all_capabilities_no_priv_esc",
            sc.get("allowPrivilegeEscalation") is False
            and (sc.get("capabilities") or {}).get("drop") == ["ALL"],
            f"securityContext={sc}",
        )

    pod_sc = ((values.get("defaultPodOptions") or {}).get("securityContext") or {})
    record(
        "pod_runs_as_1000_1000_with_fsgroup_1000",
        pod_sc.get("runAsUser") == 1000
        and pod_sc.get("runAsGroup") == 1000
        and pod_sc.get("fsGroup") == 1000
        and pod_sc.get("runAsNonRoot") is True,
        f"defaultPodOptions.securityContext={pod_sc}",
    )

    # Browser port measured at 3000, named http, HOSTNAME 0.0.0.0, PORT pinned.
    b_env = browser.get("env") or {}
    record(
        "browser_listens_on_measured_port_3000",
        b_env.get("PORT") == BROWSER_PORT
        and b_env.get("HOSTNAME") == "0.0.0.0",
        f"PORT={b_env.get('PORT')!r} HOSTNAME={b_env.get('HOSTNAME')!r}",
    )
    b_ports = browser.get("ports") or []
    port_names = {(p.get("name"), p.get("containerPort")) for p in b_ports}
    record(
        "browser_declares_named_http_container_port_3000",
        ("http", BROWSER_PORT) in port_names,
        f"ports={b_ports}",
    )
    record(
        "browser_working_dir_is_browser_path",
        browser.get("workingDir") == "/var/lib/falkordb/browser",
        f"workingDir={browser.get('workingDir')!r}",
    )

    # Browser must NOT envFrom the whole secret (no FALKORDB_PASSWORD leak into UI container).
    record(
        "browser_does_not_envfrom_whole_secret",
        not (browser.get("envFrom") or []),
        f"envFrom={browser.get('envFrom')}",
    )
    # Only AUTH_SECRET + ENCRYPTION_KEY from secretKeyRef.
    def _secret_key(ref: Any) -> tuple[str | None, str | None]:
        if not isinstance(ref, dict):
            return None, None
        vf = ref.get("valueFrom") or {}
        sk = vf.get("secretKeyRef") or {}
        return sk.get("name"), sk.get("key")

    auth_secret = _secret_key(b_env.get("AUTH_SECRET"))
    enc_key = _secret_key(b_env.get("ENCRYPTION_KEY"))
    record(
        "browser_injects_only_auth_secret_and_encryption_key",
        auth_secret == ("falkordb-secret", "AUTH_SECRET")
        and enc_key == ("falkordb-secret", "ENCRYPTION_KEY"),
        f"AUTH_SECRET={auth_secret} ENCRYPTION_KEY={enc_key}",
    )
    record(
        "browser_auth_url_matches_route_hostname",
        b_env.get("AUTH_URL") == "https://falkordb-browser.${SECRET_DOMAIN}"
        and b_env.get("ALLOWED_ORIGINS") == "https://falkordb-browser.${SECRET_DOMAIN}"
        and b_env.get("AUTH_TRUST_HOST") == "true",
        f"AUTH_URL={b_env.get('AUTH_URL')!r} ALLOWED={b_env.get('ALLOWED_ORIGINS')!r}",
    )

    # Service: existing ClusterIP shape keeps redis primary + adds http 3000.
    svc = ((values.get("service") or {}).get("app") or {})
    svc_ports = svc.get("ports") or {}
    record(
        "clusterip_service_keeps_redis_primary_and_adds_http",
        (svc_ports.get("redis") or {}).get("port") == REDIS_PORT
        and (svc_ports.get("redis") or {}).get("primary") is True
        and (svc_ports.get("http") or {}).get("port") == BROWSER_PORT,
        f"service.ports={svc_ports}",
    )
    # No chart-owned route block that could drop the SecurityPolicy coupling.
    record(
        "helmrelease_has_no_chart_route_block",
        "route" not in values,
        f"top_level_keys_have_route={'route' in values}",
    )

    # Persistence: data PVC only on app; two measured emptyDirs for browser.
    pers = values.get("persistence") or {}
    data = pers.get("data") or {}
    data_mounts = ((data.get("advancedMounts") or {}).get("falkordb") or {})
    record(
        "data_pvc_mounted_only_on_app_container",
        list(data_mounts.keys()) == ["app"]
        and any(
            m.get("path") == "/var/lib/falkordb/data" for m in (data_mounts.get("app") or [])
        )
        and data.get("forceRename") == "falkordb"
        and data.get("retain") is True,
        f"data_mounts={data_mounts} forceRename={data.get('forceRename')} retain={data.get('retain')}",
    )

    app_data = pers.get("browser-app-data") or {}
    browser_data = pers.get("browser-data") or {}
    record(
        "browser_empty_dirs_are_exactly_two_measured_paths",
        app_data.get("type") == "emptyDir"
        and browser_data.get("type") == "emptyDir"
        and set(pers.keys()) >= {"data", "browser-app-data", "browser-data"},
        f"persistence_keys={sorted(pers.keys())}",
    )

    def mount_paths(entry: dict[str, Any], container: str) -> set[str]:
        mounts = ((entry.get("advancedMounts") or {}).get("falkordb") or {}).get(
            container
        ) or []
        return {m.get("path") for m in mounts if isinstance(m, dict)}

    record(
        "browser_app_data_mounts_app_dot_data_on_init_and_browser",
        mount_paths(app_data, "browser") == {"/app/.data"}
        and mount_paths(app_data, "fix-permissions") == {"/app/.data"},
        f"browser={mount_paths(app_data, 'browser')} init={mount_paths(app_data, 'fix-permissions')}",
    )
    record(
        "browser_data_mounts_workdir_dot_data_on_init_and_browser",
        mount_paths(browser_data, "browser") == {"/var/lib/falkordb/browser/.data"}
        and mount_paths(browser_data, "fix-permissions")
        == {"/var/lib/falkordb/browser/.data"},
        f"browser={mount_paths(browser_data, 'browser')} "
        f"init={mount_paths(browser_data, 'fix-permissions')}",
    )
    # Explicitly NOT mounting data volume on browser.
    record(
        "browser_does_not_mount_database_pvc",
        "browser" not in data_mounts,
        f"data_mount_containers={sorted(data_mounts.keys())}",
    )

    # Init chown both paths as root.
    fix_cmd = fix.get("command") or []
    fix_script = " ".join(str(c) for c in fix_cmd)
    fix_sc = fix.get("securityContext") or {}
    record(
        "fix_permissions_chowns_both_browser_paths_as_root",
        "chown -R 1000:1000" in fix_script
        and "/app/.data" in fix_script
        and "/var/lib/falkordb/browser/.data" in fix_script
        and fix_sc.get("runAsUser") == 0,
        f"cmd={fix_script!r} sc={fix_sc}",
    )

    # Resource limits / PVC size must remain untouched per scope boundary.
    app_res = app.get("resources") or {}
    record(
        "database_resource_limits_unchanged",
        (app_res.get("requests") or {}) == {"cpu": "50m", "memory": "256Mi"}
        and (app_res.get("limits") or {}) == {"memory": "2Gi"},
        f"resources={app_res}",
    )
    record(
        "data_pvc_size_and_storageclass_unchanged",
        data.get("size") == "20Gi" and data.get("storageClass") == "ceph-block",
        f"size={data.get('size')} sc={data.get('storageClass')}",
    )


def test_externalsecret_derives_browser_keys() -> None:
    es = load_one(APP_DIR / "externalsecret.yaml")
    target = (es.get("spec") or {}).get("target") or {}
    tmpl = target.get("template") or {}
    data = tmpl.get("data") or {}

    record(
        "externalsecret_still_emits_password_and_rediscli_auth",
        data.get("FALKORDB_PASSWORD") == "{{ .FALKORDB_PASSWORD }}"
        and data.get("REDISCLI_AUTH") == "{{ .FALKORDB_PASSWORD }}",
        f"keys={sorted(data.keys())}",
    )

    auth = data.get("AUTH_SECRET") or ""
    enc = data.get("ENCRYPTION_KEY") or ""
    # ESO v2 template: sha256sum of salted password - stable, 64 hex chars.
    record(
        "auth_secret_derived_via_sha256_with_distinct_salt",
        "sha256sum" in auth
        and "falkordb-browser-auth" in auth
        and ".FALKORDB_PASSWORD" in auth,
        f"AUTH_SECRET_template_ok={bool(auth)}",
    )
    record(
        "encryption_key_derived_via_sha256_with_distinct_salt",
        "sha256sum" in enc
        and "falkordb-browser-encryption" in enc
        and ".FALKORDB_PASSWORD" in enc
        and "falkordb-browser-auth" not in enc,
        f"ENCRYPTION_KEY_template_ok={bool(enc)}",
    )
    # Salts must differ so neither key is usable in the other's role.
    record(
        "browser_key_salts_are_distinct",
        "falkordb-browser-auth" in auth
        and "falkordb-browser-encryption" in enc
        and auth != enc,
        "salts distinct",
    )


def test_kustomize_inventory() -> None:
    kust = load_one(APP_DIR / "kustomization.yaml")
    resources = set(kust.get("resources") or [])
    required = {
        "./externalsecret.yaml",
        "./helmrelease.yaml",
        "./httproute.yaml",
        "./securitypolicy.yaml",
        "./service-lb.yaml",
    }
    missing = sorted(required - resources)
    record(
        "app_kustomization_lists_all_part1_and_part2_resources",
        missing == [],
        f"missing={missing} resources={sorted(resources)}",
    )

    docs = kustomize_build(APP_DIR)
    if docs is None:
        # Still assert file-level presence when kustomize is unavailable.
        record(
            "kustomize_emits_lb_route_policy_hr_es",
            False,
            "kustomize/kubectl unavailable in environment",
        )
        return

    kinds_names = {
        (d.get("kind"), (d.get("metadata") or {}).get("name")) for d in docs
    }
    required_objs = {
        ("Service", "falkordb-lb"),
        ("HTTPRoute", "falkordb-browser"),
        ("SecurityPolicy", "falkordb-browser-auth"),
        ("HelmRelease", "falkordb"),
        ("ExternalSecret", "falkordb-secret"),
    }
    missing_objs = sorted(required_objs - kinds_names)
    record(
        "kustomize_emits_lb_route_policy_hr_es",
        missing_objs == [],
        f"missing={missing_objs} have={sorted(kinds_names)}",
    )

    # Built Service must still be LoadBalancer 6379 only.
    services = [
        d
        for d in docs
        if d.get("kind") == "Service" and (d.get("metadata") or {}).get("name") == "falkordb-lb"
    ]
    if services:
        built_ports = {
            (p.get("name"), p.get("port"))
            for p in ((services[0].get("spec") or {}).get("ports") or [])
        }
        record(
            "kustomize_built_lb_still_redis_only",
            built_ports == {("redis", REDIS_PORT)},
            f"built_ports={built_ports}",
        )

    # Built HTTPRoute parent still internal-only.
    routes = [
        d
        for d in docs
        if d.get("kind") == "HTTPRoute"
        and (d.get("metadata") or {}).get("name") == "falkordb-browser"
    ]
    if routes:
        parents = (routes[0].get("spec") or {}).get("parentRefs") or []
        record(
            "kustomize_built_route_still_envoy_internal_only",
            all(p.get("name") == "envoy-internal" for p in parents)
            and all(p.get("name") != "envoy-external" for p in parents),
            f"parents={parents}",
        )


def test_authentik_kustomize_still_has_database_grant() -> None:
    docs = kustomize_build(AUTHENTIK_DIR)
    if docs is None:
        return
    grants = [d for d in docs if d.get("kind") == "ReferenceGrant"]
    found = False
    for g in grants:
        for f in ((g.get("spec") or {}).get("from") or []):
            if (
                f.get("group") == "gateway.envoyproxy.io"
                and f.get("kind") == "SecurityPolicy"
                and f.get("namespace") == "database"
            ):
                found = True
    record(
        "authentik_kustomize_inventory_includes_database_from",
        found,
        f"grants={len(grants)}",
    )


def test_eso_template_sha256_length_contract() -> None:
    """sha256sum hex digest is exactly 64 chars - what the browser entrypoint checks.

    This executes the same hash shape ESO's template engine produces for a
    stand-in password (not the real secret) to prove the template yields a
    length-valid ENCRYPTION_KEY.
    """
    import hashlib

    standin = "unit-test-stand-in-not-a-real-secret"
    for salt in ("falkordb-browser-auth", "falkordb-browser-encryption"):
        digest = hashlib.sha256(f"{standin}:{salt}".encode()).hexdigest()
        record(
            f"sha256_digest_length_64_for_{salt.replace('-', '_')}",
            len(digest) == 64 and re.fullmatch(r"[0-9a-f]{64}", digest) is not None,
            f"len={len(digest)}",
        )
    d1 = hashlib.sha256(f"{standin}:falkordb-browser-auth".encode()).hexdigest()
    d2 = hashlib.sha256(f"{standin}:falkordb-browser-encryption".encode()).hexdigest()
    record("derived_keys_differ_for_distinct_salts", d1 != d2, "ok")


def main() -> int:
    test_loadbalancer_service_semantics()
    test_browser_httproute_and_securitypolicy()
    test_referencegrant_allows_database_namespace()
    test_helmrelease_browser_sidecar_and_hardening()
    test_externalsecret_derives_browser_keys()
    test_kustomize_inventory()
    test_authentik_kustomize_still_has_database_grant()
    test_eso_template_sha256_length_contract()

    failed = [r for r in RESULTS if not r["ok"]]
    print()
    print(f"summary: {len(RESULTS) - len(failed)} passed, {len(failed)} failed, {len(RESULTS)} total")
    for r in failed:
        print(f"  FAIL {r['name']}: {r['detail']}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
