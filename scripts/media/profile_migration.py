#!/usr/bin/env python3
"""One-off: move Radarr movies off the unmanaged "Any" profile onto managed catalogue profiles.

Dry run by default. Targets are the min-score-0 catalogue profiles (2160p file -> UHD Bluray
+ WEB, anything else -> HD Bluray + WEB), looked up by name. SQP-1 is not a target: its
minimum score of 1000 would starve old titles.

    RADARR_URL=http://localhost:7878 RADARR_API_KEY=... \\
        python3 scripts/media/profile_migration.py --before before.json [--apply]

--before is written before any change so the pass can be reversed. Exit 0 ok, 2 error.
Prints no API key and no exception text.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request

EXIT_OK, EXIT_ERROR = 0, 2
SOURCE_PROFILE, HD_PROFILE, UHD_PROFILE = "Any", "HD Bluray + WEB", "UHD Bluray + WEB"
UHD_RESOLUTION = 2160
BATCH_SIZE = 100
REQUEST_TIMEOUT_SECONDS = 120


def plan_moves(movies: list[dict], *, any_id: int, hd_id: int, uhd_id: int) -> dict[int, list[int]]:
    """target profile id -> movie ids, only for movies currently on the unmanaged profile."""
    plan: dict[int, list[int]] = {}
    for movie in movies:
        if movie.get("qualityProfileId") != any_id:
            continue
        file_quality = (movie.get("movieFile") or {}).get("quality") or {}
        resolution = (file_quality.get("quality") or {}).get("resolution")
        target = uhd_id if resolution == UHD_RESOLUTION else hd_id
        plan.setdefault(target, []).append(movie["id"])
    return plan


def before_state(movies: list[dict], *, any_id: int) -> dict[str, int]:
    return {str(m["id"]): any_id for m in movies if m.get("qualityProfileId") == any_id}


def valid_base_url(url: str) -> bool:
    """Only http(s): urlopen would also read file:// and ftp:// URLs."""
    return url.lower().startswith(("http://", "https://"))


def call(base_url: str, api_key: str, method: str, path: str, body: object = None) -> object:
    data = None if body is None else json.dumps(body).encode("utf-8")
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}{path}", data=data, method=method,
        headers={"X-Api-Key": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8") or "null")


def profile_id(profiles: list[dict], name: str) -> int:
    matches = [p["id"] for p in profiles if p["name"] == name]
    if len(matches) != 1:
        raise LookupError(name)
    return matches[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--before", required=True, help="where to save the reversible before-state")
    parser.add_argument("--apply", action="store_true", help="change Radarr (default: dry run)")
    args = parser.parse_args()

    base_url, api_key = os.environ.get("RADARR_URL"), os.environ.get("RADARR_API_KEY")
    if not base_url or not api_key:
        print("RADARR_URL and RADARR_API_KEY must be set")
        return EXIT_ERROR
    if not valid_base_url(base_url):
        print("RADARR_URL must start with http:// or https://")
        return EXIT_ERROR
    try:
        profiles = call(base_url, api_key, "GET", "/api/v3/qualityprofile")
        any_id = profile_id(profiles, SOURCE_PROFILE)
        hd_id, uhd_id = profile_id(profiles, HD_PROFILE), profile_id(profiles, UHD_PROFILE)
        movies = call(base_url, api_key, "GET", "/api/v3/movie")
        plan = plan_moves(movies, any_id=any_id, hd_id=hd_id, uhd_id=uhd_id)
        for target, ids in plan.items():
            print(f"profile {target}: {len(ids)} movies")
        if not args.apply:
            print("dry run, nothing changed (use --apply)")
            return EXIT_OK
        with open(args.before, "w", encoding="utf-8") as handle:
            json.dump(before_state(movies, any_id=any_id), handle)
        for target, ids in plan.items():
            for start in range(0, len(ids), BATCH_SIZE):
                call(base_url, api_key, "PUT", "/api/v3/movie/editor",
                     {"movieIds": ids[start:start + BATCH_SIZE], "qualityProfileId": target})
        print("applied")
        return EXIT_OK
    except Exception:  # noqa: BLE001 - never echo exception text, it may carry the URL or key
        print("migration failed or Radarr unreadable; re-run the dry run to see what is left")
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
