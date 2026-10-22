#!/usr/bin/env python3
"""Fail when any Radarr, Sonarr or Lidarr queue item is older than 24 hours.

A failed Job feeds the existing KubeJobFailed alert. Exit codes: 0 healthy,
1 stale item found, 2 a queue could not be read (never reported as healthy).
Only counts and item ids are printed; API keys and error text are not.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

STALE_AFTER = timedelta(hours=24)
PAGE_SIZE = 1000
REQUEST_TIMEOUT_SECONDS = 30
EXIT_OK, EXIT_STALE, EXIT_ERROR = 0, 1, 2

SERVICE_API_VERSIONS = {"RADARR": "v3", "SONARR": "v3", "LIDARR": "v1"}


@dataclass(frozen=True)
class Service:
    name: str
    base_url: str
    api_version: str
    api_key: str


def parse_added(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def stale_ids(records: list[dict[str, Any]], now: datetime) -> list[int]:
    return [r["id"] for r in records if r.get("added") and now - parse_added(r["added"]) > STALE_AFTER]


def fetch_records(service: Service) -> list[dict[str, Any]]:
    url = f"{service.base_url}/api/{service.api_version}/queue?page=1&pageSize={PAGE_SIZE}"
    request = urllib.request.Request(url, headers={"X-Api-Key": service.api_key})
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310 - in-cluster http
        payload = json.load(response)
    records = payload["records"]
    if payload.get("totalRecords", len(records)) > len(records):
        raise ValueError("queue larger than one page")
    return records


def check_service(
    service: Service, now: datetime, fetch: Callable[[Service], list[dict[str, Any]]]
) -> int:
    try:
        records = fetch(service)
        stale = stale_ids(records, now)
    except Exception as exc:  # noqa: BLE001 - any failure must surface as EXIT_ERROR, text may hold the key
        print(f"{service.name}: queue unreadable ({type(exc).__name__})")
        return EXIT_ERROR
    print(f"{service.name}: {len(records)} queued, {len(stale)} stale {stale}")
    return EXIT_STALE if stale else EXIT_OK


def run(
    services: list[Service], now: datetime, fetch: Callable[[Service], list[dict[str, Any]]]
) -> int:
    return max((check_service(service, now, fetch) for service in services), default=EXIT_OK)


def services_from_env(env: dict[str, str]) -> list[Service]:
    return [
        Service(name.lower(), env[f"{name}_URL"], version, env[f"{name}_API_KEY"])
        for name, version in SERVICE_API_VERSIONS.items()
    ]


def main() -> int:
    try:
        services = services_from_env(dict(os.environ))
    except KeyError as missing:
        print(f"missing environment variable {missing}")
        return EXIT_ERROR
    return run(services, datetime.now(timezone.utc), fetch_records)


if __name__ == "__main__":
    sys.exit(main())
