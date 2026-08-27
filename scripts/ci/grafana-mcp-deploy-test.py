#!/usr/bin/env python3
"""Semantic regression test for in-cluster grafana-mcp (toolhive MCPServer).

Captain D5/A6: deploy mcp-grafana in-cluster under the ai/toolhive ownership
path, register it into the mcp-tools MCPGroup, pin the upstream image by
digest, point at the in-cluster Grafana service, and keep the token
read-only (Viewer) with --disable-write as defense-in-depth.

This test does not grep source text as a proxy for behavior. It:
  1. Renders kubernetes/apps/base/ai/toolhive/mcp-servers via kubectl kustomize
     and loads the resulting objects as a structured model.
  2. Renders kubernetes/apps/main/ai and inspects the toolhive-mcp-servers
     Flux Kustomization healthChecks.
  3. Asserts the grafana-mcp MCPServer contract (API group, transport, image
     pin, groupRef, Grafana URL, secret wiring, --disable-write).
  4. Asserts the companion ExternalSecret targets the onepassword store and
     the grafana-mcp 1Password item / GRAFANA_SERVICE_ACCOUNT_TOKEN field.
  5. Asserts healthChecks cover both the proxy Deployment and backend
     StatefulSet named after the MCPServer, matching every active server.
  6. Optionally verifies the pinned digest against the Docker Hub registry
     API for grafana/mcp-grafana:1.2.0 (skipped cleanly if offline).

Live read-only query proof against the federated gateway remains a post-merge
gate (needs cluster access and the item `grafana-mcp` / field
`GRAFANA_SERVICE_ACCOUNT_TOKEN` in 1Password to hold a Viewer-scoped token -
the same item/field the original grafana-mcp deployment used, not a new one).
"""

from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
MCP_SERVERS_PATH = ROOT / "kubernetes/apps/base/ai/toolhive/mcp-servers"
MAIN_AI_PATH = ROOT / "kubernetes/apps/main/ai"

EXPECTED_IMAGE_REPO = "docker.io/grafana/mcp-grafana"
EXPECTED_IMAGE_TAG = "1.2.0"
EXPECTED_DIGEST = (
    "sha256:cfae68f893dbe40cb6df69579f78beb226f973ded4436d47c844f91d2f00dc53"
)
EXPECTED_IMAGE = f"{EXPECTED_IMAGE_REPO}:{EXPECTED_IMAGE_TAG}@{EXPECTED_DIGEST}"
EXPECTED_GRAFANA_URL = "http://grafana.monitoring.svc.cluster.local"
EXPECTED_GROUP = "mcp-tools"
EXPECTED_SECRET_NAME = "toolhive-grafana"
EXPECTED_TOKEN_KEY = "GRAFANA_SERVICE_ACCOUNT_TOKEN"
EXPECTED_OP_ITEM = "grafana-mcp"
TOOLHIVE_API = "toolhive.stacklok.dev/v1alpha1"

# Active MCPServer metadata.names after this change (deactivated servers must
# not appear in the rendered build or in Flux healthChecks).
ACTIVE_SERVERS = frozenset(
    {
        "arr",
        "comfyui-mcp",
        "flux",
        "github",
        "grafana-mcp",
        "kubectl",
        "kubesearch",
        "memory",
    }
)
DEACTIVATED_SERVERS = frozenset(
    {
        "garmin-connect",
        "garmin-connect-mcp",
        "ha",
        "ha-mcp",
        "seerr",
        "seerr-mcp",
        "talos",
        "talos-mcp",
    }
)


class Failure(Exception):
    pass


def kustomize_build(path: Path) -> list[dict[str, Any]]:
    proc = subprocess.run(
        ["kubectl", "kustomize", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise Failure(
            f"kubectl kustomize {path} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    if not docs:
        raise Failure(f"kubectl kustomize {path} produced no documents")
    return docs


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def mcp_container(server: dict[str, Any]) -> dict[str, Any]:
    pts = server.get("spec", {}).get("podTemplateSpec") or {}
    containers = (pts.get("spec") or {}).get("containers") or []
    for c in containers:
        if c.get("name") == "mcp":
            return c
    raise Failure("grafana-mcp MCPServer missing podTemplateSpec container name=mcp")


def assert_grafana_mcpserver(docs: list[dict[str, Any]]) -> dict[str, Any]:
    servers = [
        d
        for d in docs
        if d.get("kind") == "MCPServer" and d.get("apiVersion") == TOOLHIVE_API
    ]
    names = {s["metadata"]["name"] for s in servers}
    require(names == ACTIVE_SERVERS, f"active MCPServer set mismatch: {sorted(names)}")
    require(
        not (names & DEACTIVATED_SERVERS),
        f"deactivated MCPServer rendered: {sorted(names & DEACTIVATED_SERVERS)}",
    )
    # Must not reintroduce the retired kagent API or a non-existent MCPServerEntry.
    for d in docs:
        api = d.get("apiVersion", "")
        kind = d.get("kind", "")
        require(
            not api.startswith("kagent.dev/"),
            f"retired kagent API present: {api}/{kind}",
        )
        require(kind != "MCPServerEntry", "MCPServerEntry must not be used")

    g = next(s for s in servers if s["metadata"]["name"] == "grafana-mcp")
    spec = g["spec"]
    require(spec.get("image") == EXPECTED_IMAGE, f"image pin wrong: {spec.get('image')}")
    require(spec.get("transport") == "streamable-http", "transport must be streamable-http")
    require(spec.get("proxyMode") == "streamable-http", "proxyMode must be streamable-http")
    require(spec.get("proxyPort") == 8080, "proxyPort must be 8080")
    require(spec.get("mcpPort") == 8000, "mcpPort must be 8000")
    require(
        (spec.get("groupRef") or {}).get("name") == EXPECTED_GROUP,
        f"groupRef must be {EXPECTED_GROUP}",
    )

    secrets = spec.get("secrets") or []
    require(len(secrets) == 1, f"expected one secret ref, got {secrets!r}")
    sec = secrets[0]
    require(sec.get("name") == EXPECTED_SECRET_NAME, f"secret name: {sec}")
    require(sec.get("key") == EXPECTED_TOKEN_KEY, f"secret key: {sec}")
    require(
        sec.get("targetEnvName") == EXPECTED_TOKEN_KEY,
        f"secret targetEnvName: {sec}",
    )

    container = mcp_container(g)
    args = list(container.get("args") or [])
    require("-t" in args and "streamable-http" in args, f"transport args missing: {args}")
    require("--disable-write" in args, f"--disable-write missing from args: {args}")
    # ToolHive proxy probes use a non-loopback Host; mcp-grafana defaults to
    # loopback-only allowed-hosts and would 403 without an override.
    require("--allowed-hosts" in args, f"--allowed-hosts missing: {args}")
    ah_idx = args.index("--allowed-hosts")
    require(ah_idx + 1 < len(args), "--allowed-hosts missing value")
    require(args[ah_idx + 1] == "*", f"--allowed-hosts value: {args[ah_idx + 1]!r}")

    env = {e["name"]: e.get("value") for e in (container.get("env") or []) if "name" in e}
    require(
        env.get("GRAFANA_URL") == EXPECTED_GRAFANA_URL,
        f"GRAFANA_URL wrong: {env.get('GRAFANA_URL')!r}",
    )
    return g


def assert_externalsecret(docs: list[dict[str, Any]]) -> None:
    secrets = [
        d
        for d in docs
        if d.get("kind") == "ExternalSecret"
        and d.get("metadata", {}).get("name") == EXPECTED_SECRET_NAME
    ]
    require(len(secrets) == 1, f"toolhive-grafana ExternalSecret count={len(secrets)}")
    es = secrets[0]
    spec = es["spec"]
    store = spec.get("secretStoreRef") or {}
    require(store.get("kind") == "ClusterSecretStore", f"store kind: {store}")
    require(store.get("name") == "onepassword", f"store name: {store}")
    target = spec.get("target") or {}
    require(target.get("name") == EXPECTED_SECRET_NAME, f"target name: {target}")
    template_data = ((target.get("template") or {}).get("data")) or {}
    require(
        EXPECTED_TOKEN_KEY in template_data,
        f"template missing {EXPECTED_TOKEN_KEY}: {template_data}",
    )
    # dataFrom extract key is the 1Password item name.
    data_from = spec.get("dataFrom") or []
    require(data_from, "ExternalSecret dataFrom empty")
    keys = [((item.get("extract") or {}).get("key")) for item in data_from]
    require(EXPECTED_OP_ITEM in keys, f"1Password item key missing: {keys}")


def assert_flux_healthchecks(docs: list[dict[str, Any]]) -> None:
    ks = [
        d
        for d in docs
        if d.get("kind") == "Kustomization"
        and d.get("apiVersion", "").startswith("kustomize.toolkit.fluxcd.io/")
        and d.get("metadata", {}).get("name") == "toolhive-mcp-servers"
    ]
    require(len(ks) == 1, f"toolhive-mcp-servers Kustomization count={len(ks)}")
    hc = ks[0].get("spec", {}).get("healthChecks") or []
    pairs = {(h.get("kind"), h.get("name"), h.get("namespace")) for h in hc}
    for name in sorted(ACTIVE_SERVERS):
        require(
            ("Deployment", name, "ai") in pairs,
            f"missing Deployment healthCheck for {name}",
        )
        require(
            ("StatefulSet", name, "ai") in pairs,
            f"missing StatefulSet healthCheck for {name}",
        )
    for name in sorted(DEACTIVATED_SERVERS):
        require(
            not any(h[1] == name for h in pairs),
            f"deactivated server still in healthChecks: {name}",
        )
    # Path must still point at the mcp-servers base (gateway listener path untouched).
    require(
        ks[0]["spec"].get("path") == "./kubernetes/apps/base/ai/toolhive/mcp-servers",
        f"unexpected path: {ks[0]['spec'].get('path')}",
    )


def verify_image_digest() -> str:
    """Return 'verified' or 'skipped:<reason>' after checking Docker Hub."""
    # Hub tag API returns the index digest for multi-arch tags.
    url = "https://hub.docker.com/v2/repositories/grafana/mcp-grafana/tags/1.2.0"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            body = json.load(resp)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return f"skipped:{exc}"
    digest = body.get("digest")
    if digest != EXPECTED_DIGEST:
        raise Failure(
            f"Docker Hub digest for grafana/mcp-grafana:1.2.0 is {digest!r}, "
            f"manifest pins {EXPECTED_DIGEST!r}"
        )
    return "verified"


def main() -> int:
    print("==> kustomize build mcp-servers")
    mcp_docs = kustomize_build(MCP_SERVERS_PATH)
    print(f"    documents: {len(mcp_docs)}")

    print("==> assert grafana-mcp MCPServer contract")
    assert_grafana_mcpserver(mcp_docs)
    print("    MCPServer OK")

    print("==> assert ExternalSecret contract")
    assert_externalsecret(mcp_docs)
    print("    ExternalSecret OK")

    print("==> kustomize build apps/main/ai")
    main_docs = kustomize_build(MAIN_AI_PATH)
    print(f"    documents: {len(main_docs)}")

    print("==> assert toolhive-mcp-servers healthChecks")
    assert_flux_healthchecks(main_docs)
    print("    healthChecks OK")

    print("==> verify image digest via Docker Hub")
    digest_status = verify_image_digest()
    print(f"    digest: {digest_status}")

    print("PASS: grafana-mcp in-cluster deploy manifests satisfy D5/A6 contracts")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
