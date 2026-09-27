#!/usr/bin/env python3
"""Semantic regression test for the "ToolHive MCP Session" Gatus check.

From 2026-09-14 to 2026-09-27 no MCP client could open a session on ToolHive's
vmcp gateway (`ai/mcp-gateway-internal`): `initialize` hung because vmcp embeds
the whole tool catalog inside the handshake and the CPU embedding server behind
it took ~490s per session. Hermes reported "Connecting to MCP server 'toolhive'
timed out after 30s" for thirteen days while every health signal stayed green,
because the only Gatus check on the gateway probed vmcp's `/health` - a path
that answers 200 as long as the process is alive. Diagnosis:
docs/ai-system/toolhive-optimizer-embedding-timeout-2026-09-27.md.

The fix added a Gatus endpoint that performs a real MCP `initialize` against the
same URL Hermes uses. It was run both ways before merge (red at 25.0s against
the broken embedder, green at 8.1s against the fixed one). This test keeps that
check from being "simplified" back into one that cannot fail. It asserts
relationships, not literals:

  1. The probe targets the exact URL Hermes' `toolhive` MCP server uses, so it
     measures the path Hermes depends on (read from Hermes' own config).
  2. It is a POST whose body is a JSON-RPC `initialize`, so it opens a session.
  3. Its conditions look inside the body (vmcp's serverInfo), not only at the
     status code, so /health, a 406 or a login page cannot satisfy it.
  4. Its client timeout is below Hermes' 30s `connect_timeout` default, so a
     green check means Hermes can connect in time.
  5. The GatusServiceDown alert still matches its group and name, so red pages.

Nothing else in CI looks at Gatus endpoint contents: `flate` only validates the
ConfigMap as YAML.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
GATUS_CONFIG = REPO / "kubernetes/apps/base/monitoring/gatus/app/resources/config.yaml"
GATUS_RULE = REPO / "kubernetes/apps/base/monitoring/gatus/app/prometheusrule.yaml"
HERMES_CONFIG = REPO / "kubernetes/apps/base/ai/hermes/app/resources/config.yaml"
EVIDENCE_DOC = REPO / "docs/ai-system/toolhive-optimizer-embedding-timeout-2026-09-27.md"

PROBE_NAME = "ToolHive MCP Session"
# Hermes' default MCP connect budget: hermes_cli/mcp_config.py
# `config.get("connect_timeout", 30)` (the path that printed the captain's
# error). The runtime connect path allows 60s; the tighter one is the bound.
HERMES_CONNECT_TIMEOUT_S = 30.0


class Failure(Exception):
    pass


def require(cond: Any, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load(path: Path) -> Any:
    require(path.is_file(), f"{path.relative_to(REPO)} is missing")
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def probe() -> dict[str, Any]:
    endpoints = (load(GATUS_CONFIG) or {}).get("endpoints") or []
    matches = [e for e in endpoints if e.get("name") == PROBE_NAME]
    require(
        len(matches) == 1,
        f"expected exactly one Gatus endpoint named {PROBE_NAME!r}, found {len(matches)}. "
        "It is the only check that sees an MCP session fail to open; the route's "
        "/health check stayed green through the 13-day outage",
    )
    return matches[0]


def hermes_toolhive_url() -> str:
    servers = (load(HERMES_CONFIG) or {}).get("mcp_servers") or {}
    url = (servers.get("toolhive") or {}).get("url")
    require(url, "Hermes config.yaml no longer declares mcp_servers.toolhive.url")
    return url


def parse_duration_s(value: Any) -> float:
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(ms|s|m)", str(value).strip())
    require(m, f"cannot parse Gatus duration {value!r}")
    n, unit = float(m.group(1)), m.group(2)
    return {"ms": n / 1000, "s": n, "m": n * 60}[unit]


def test_probe_targets_the_url_hermes_uses() -> None:
    url, want = probe().get("url"), hermes_toolhive_url()
    require(
        url == want,
        f"probe url {url!r} != Hermes' toolhive url {want!r}; the probe must exercise "
        "the path Hermes actually connects through",
    )


def test_probe_sends_an_mcp_initialize() -> None:
    e = probe()
    require(str(e.get("method", "GET")).upper() == "POST", "probe must POST (MCP streamable HTTP)")
    try:
        body = json.loads(e.get("body") or "")
    except json.JSONDecodeError as err:
        raise Failure(f"probe body is not JSON: {err}") from err
    require(body.get("jsonrpc") == "2.0", "probe body must be JSON-RPC 2.0")
    require(
        body.get("method") == "initialize",
        f"probe body method is {body.get('method')!r}; it must be `initialize`, the call "
        "that hung for 13 days while /health stayed 200",
    )
    accept = str((e.get("headers") or {}).get("Accept", ""))
    require(
        "application/json" in accept and "text/event-stream" in accept,
        "probe must send `Accept: application/json, text/event-stream`; vmcp answers "
        "406 without both",
    )


def test_probe_asserts_on_the_session_body() -> None:
    conditions = [str(c) for c in probe().get("conditions") or []]
    require(
        any(c.replace(" ", "").startswith("[BODY].result.serverInfo") for c in conditions),
        f"probe conditions {conditions!r} never look at the initialize result; a "
        "status-only condition is satisfied by /health, a 406 or a login page",
    )


def test_probe_timeout_fits_hermes_budget() -> None:
    client = probe().get("client") or {}
    require("timeout" in client, "probe must set client.timeout (Gatus defaults to 10s)")
    timeout = parse_duration_s(client["timeout"])
    require(
        timeout < HERMES_CONNECT_TIMEOUT_S,
        f"probe client.timeout {timeout}s must be below Hermes' {HERMES_CONNECT_TIMEOUT_S}s "
        "connect_timeout, or the check can pass while Hermes times out",
    )


def test_probe_failure_pages() -> None:
    e = probe()
    rules = [
        r
        for g in (load(GATUS_RULE).get("spec") or {}).get("groups") or []
        for r in g.get("rules") or []
        if r.get("alert") == "GatusServiceDown"
    ]
    require(len(rules) == 1, "GatusServiceDown rule not found")
    expr = rules[0]["expr"]
    require("gatus_results_endpoint_success" in expr, f"unexpected GatusServiceDown expr: {expr}")
    labels = {"group": e.get("group", ""), "name": e.get("name", "")}
    # PromQL label matchers are fully anchored RE2; re.fullmatch mirrors that.
    for label, op, pattern in re.findall(r'(\w+)\s*(=~|!~|!=|=)\s*"([^"]*)"', expr):
        if label not in labels:
            continue
        value = labels[label]
        hit = {
            "=~": re.fullmatch(pattern, value) is not None,
            "!~": re.fullmatch(pattern, value) is None,
            "=": value == pattern,
            "!=": value != pattern,
        }[op]
        require(
            hit,
            f"GatusServiceDown matcher {label}{op}\"{pattern}\" excludes the probe "
            f"({label}={value!r}); a red check would never page",
        )


def test_evidence_document_exists() -> None:
    require(EVIDENCE_DOC.is_file(), f"{EVIDENCE_DOC.relative_to(REPO)} is missing")


def main() -> int:
    tests: list[str] = []
    failures: list[str] = []

    def run(name: str, fn: Any) -> None:
        tests.append(name)
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as e:
            failures.append(name)
            print(f"[FAIL] {name}: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append(name)
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}")

    run("probe_targets_the_url_hermes_uses", test_probe_targets_the_url_hermes_uses)
    run("probe_sends_an_mcp_initialize", test_probe_sends_an_mcp_initialize)
    run("probe_asserts_on_the_session_body", test_probe_asserts_on_the_session_body)
    run("probe_timeout_fits_hermes_budget", test_probe_timeout_fits_hermes_budget)
    run("probe_failure_pages", test_probe_failure_pages)
    run("evidence_document_exists", test_evidence_document_exists)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
