#!/usr/bin/env python3
"""Every route on the public gateway is on an explicit allow-list, path by path.

Prevents: an internal admin UI or a credential-spending app exposed on the public gateway.

`envoy-external` is reachable from the internet.
Admin UIs (Wizarr admin, Cleanuparr, Shelfmark, Calibre-Web-Automated, the *arr
apps) and apps that hold API keys stay on `envoy-internal`. This test walks every
YAML document under kubernetes/, finds every route-shaped mapping (an HTTPRoute
spec or a chart's `route:` values) whose parentRefs name `envoy-external`, and
compares its hostnames and path prefixes with ALLOWED. A new public route, a new
path on one, or a removed route all fail until ALLOWED is edited, so exposure
is always a reviewed diff in this file.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import yaml

ROOT = Path(__file__).resolve().parents[2]
GATEWAY = "envoy-external"

# hostname -> path prefixes it may expose ("/" is the whole app).
ALLOWED: dict[str, set[str]] = {
    "auth.${SECRET_DOMAIN}": {"/", "/outpost.goauthentik.io"},
    "coder.${SECRET_DOMAIN}": {"/"},
    "flux-webhook.${SECRET_DOMAIN}": {"/hook/"},
    "hass.${SECRET_DOMAIN}": {"/"},
    "plex.${SECRET_DOMAIN}": {"/"},
    "seerr.${SECRET_DOMAIN}": {"/"},
    "kromgo.${SECRET_DOMAIN}": {"/"},  # read-only status badges
    "echo.${SECRET_DOMAIN}": {"/"},
    "n8n-webhook.${SECRET_DOMAIN}": {"/webhook", "/rest/oauth2-credential"},  # not the editor
    # Wizarr invite flow only; the admin UI is wizarr.${SECRET_DOMAIN} on envoy-internal.
    "join.${SECRET_DOMAIN}": {
        "/j", "/join", "/invitation", "/wizard", "/static", "/image-proxy",
        "/cinema-posters", "/reset", "/favicon.ico", "/plex/callback",
    },
}
# Paths that are the Wizarr admin UI or its API; never on the public join host.
JOIN_FORBIDDEN = {"/", "/admin", "/settings", "/plex", "/api", "/login", "/health"}

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


class Loader(yaml.SafeLoader):
    """SafeLoader that tolerates a re-used anchor name (later wins), as YAML 1.2 allows."""

    def compose_node(self, parent: Any, index: Any) -> Any:
        event = self.peek_event()
        if not isinstance(event, yaml.AliasEvent) and getattr(event, "anchor", None) in self.anchors:
            del self.anchors[event.anchor]
        return super().compose_node(parent, index)


def walk(node: Any) -> Iterator[dict[str, Any]]:
    if isinstance(node, dict):
        if isinstance(node.get("parentRefs"), list):
            yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def public_routes() -> list[tuple[str, dict[str, Any], str]]:
    """(file, route, release name for a chart's `{{ .Release.Name }}` hostname)."""
    found = []
    for path in sorted((ROOT / "kubernetes").rglob("*.yaml")):
        text = path.read_text()
        if GATEWAY not in text:
            continue
        for doc in yaml.load_all(text, Loader=Loader):
            if not isinstance(doc, dict):
                continue
            release = (doc.get("spec") or {}).get("releaseName") or (doc.get("metadata") or {}).get("name", "")
            for route in walk(doc):
                if any(isinstance(r, dict) and r.get("name") == GATEWAY for r in route["parentRefs"]):
                    found.append((str(path.relative_to(ROOT)), route, str(release)))
    return found


def paths_of(route: dict[str, Any]) -> set[str]:
    out = set()
    for rule in route.get("rules") or [{}]:
        for match in rule.get("matches") or [{}]:
            out.add(((match or {}).get("path") or {}).get("value", "/"))
    return out


def main() -> int:
    routes = public_routes()
    check(bool(routes), f"found {len(routes)} routes on {GATEWAY}")
    seen: dict[str, set[str]] = {}
    for where, route, release in routes:
        hostnames = [h.replace("{{ .Release.Name }}", release) for h in route.get("hostnames") or []]
        if not hostnames:
            backends = [b for rule in route.get("rules") or [] for b in rule.get("backendRefs") or []]
            check(not backends, f"{where}: a hostname-less public route only redirects (no backendRefs)")
            continue
        for host in hostnames:
            seen.setdefault(host, set()).update(paths_of(route))
    for host, paths in sorted(seen.items()):
        allowed = ALLOWED.get(host)
        check(allowed is not None, f"{host} is on the public allow-list")
        if allowed is not None:
            check(paths <= allowed, f"{host} exposes only allowed paths (extra: {sorted(paths - allowed)})")
    stale = sorted(set(ALLOWED) - set(seen))
    check(not stale, f"every allow-list host still has a public route (stale: {stale})")
    join = seen.get("join.${SECRET_DOMAIN}", set())
    check(not join & JOIN_FORBIDDEN, f"join host exposes no admin path ({sorted(join & JOIN_FORBIDDEN)})")
    print(f"Summary: {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
