#!/usr/bin/env python3
"""Hermes may look up the media stack and queue a title through Seerr, never delete or approve.

  1. arr MCP: tools are an allow-list of reads (no add_*, no search/search_missing triggers, no delete);
     service URLs carry the real Service ports; Lidarr is wired (URL + secret).
  2. seerr MCP: enabled; allow-list is exactly search, details and request_media
     (``manage_media_requests`` can approve and delete, so it is never exposed).
  3. Each filter is attached with ``toolConfigRef`` to a same-folder MCPToolConfig.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_spec = importlib.util.spec_from_file_location("bc", Path(__file__).with_name("_books_common.py"))
bc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bc
_spec.loader.exec_module(bc)  # type: ignore[union-attr]

MCP = bc.ROOT / "kubernetes/apps/base/ai/toolhive/mcp-servers"
FORBIDDEN_WORDS = ("add_", "delete", "remove", "approve", "decline", "manage", "search_missing",
                   "search_movie", "search_episode", "search_album", "test_indexers")
ARR_PORTS = {"SONARR_URL": "http://sonarr.downloads.svc.cluster.local:8989",
             "RADARR_URL": "http://radarr.downloads.svc.cluster.local:7878",
             "PROWLARR_URL": "http://prowlarr.downloads.svc.cluster.local:9696",
             "LIDARR_URL": "http://lidarr.downloads.svc.cluster.local:8080"}
SEERR_TOOLS = {"search_media", "get_media_details", "request_media"}


def server(name: str) -> dict:
    return bc.load_kind(MCP / name / "mcpserver.yaml", "MCPServer")


def tool_filter(name: str) -> set[str]:
    ref = server(name)["spec"].get("toolConfigRef", {}).get("name")
    bc.assert_true(ref, f"{name}: MCPServer must set toolConfigRef")
    cfg = bc.load_kind(MCP / name / "toolconfig.yaml", "MCPToolConfig")
    bc.assert_true(cfg["metadata"]["name"] == ref, f"{name}: toolconfig name must be {ref}")
    tools = cfg["spec"].get("toolsFilter") or []
    bc.assert_true(tools, f"{name}: toolsFilter must be a non-empty allow-list")
    return set(tools)


def check_arr_allow_list() -> None:
    tools = tool_filter("arr-mcp")
    for tool in tools:
        for word in FORBIDDEN_WORDS:
            bc.assert_true(word not in tool, f"arr allow-list contains {tool!r} ({word})")
    for need in ("arr_status", "radarr_get_queue", "sonarr_get_queue", "lidarr_get_queue"):
        bc.assert_true(need in tools, f"arr allow-list missing {need}")


def check_arr_wiring() -> None:
    spec = server("arr-mcp")["spec"]
    env = {e["name"]: e["value"] for e in spec["env"]}
    for key, url in ARR_PORTS.items():
        bc.assert_true(env.get(key) == url, f"{key}={env.get(key)!r}, want {url!r}")
    secrets = {s["targetEnvName"] for s in spec["secrets"]}
    bc.assert_true("LIDARR_API_KEY" in secrets, "Lidarr API key secret not wired")
    es = bc.load_kind(MCP / "arr-mcp/externalsecret.yaml", "ExternalSecret")
    bc.assert_true(es["metadata"]["name"] == "toolhive-lidarr" or any(
        d["metadata"]["name"] == "toolhive-lidarr" for d in bc.load_all(MCP / "arr-mcp/externalsecret.yaml")),
        "toolhive-lidarr ExternalSecret missing")


def check_seerr() -> None:
    kz = bc.load_kind(MCP / "kustomization.yaml", "Kustomization")
    bc.assert_true("./seerr-mcp" in kz["resources"], "seerr-mcp must be enabled in the kustomization")
    tools = tool_filter("seerr-mcp")
    bc.assert_true(tools == SEERR_TOOLS, f"seerr allow-list {sorted(tools)}, want {sorted(SEERR_TOOLS)}")
    env = {e["name"]: e["value"] for e in server("seerr-mcp")["spec"]["env"]}
    bc.assert_true(env.get("SEERR_URL") == "http://seerr.media.svc.cluster.local:5055", f"SEERR_URL {env.get('SEERR_URL')!r}")


if __name__ == "__main__":
    sys.exit(bc.run([
        ("arr MCP is a read-only allow-list", check_arr_allow_list),
        ("arr MCP ports and Lidarr wiring", check_arr_wiring),
        ("seerr MCP enabled, request-only", check_seerr),
    ]))
