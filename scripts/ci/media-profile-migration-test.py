#!/usr/bin/env python3
"""plan_moves() maps movies on the unmanaged profile to the managed catalogue profiles.

2160p files go to UHD, everything else (including no file) to HD. Movies already on another
profile are never touched, so the pass is idempotent and reversible from the saved before-state.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("profile_migration", ROOT / "scripts/media/profile_migration.py")
mig = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mig
spec.loader.exec_module(mig)  # type: ignore[union-attr]

ANY, HD, UHD = 1, 75, 76


def movie(movie_id: int, profile: int, resolution: int | None) -> dict:
    out = {"id": movie_id, "qualityProfileId": profile, "hasFile": resolution is not None}
    if resolution is not None:
        out["movieFile"] = {"quality": {"quality": {"resolution": resolution}}}
    return out


def main() -> int:
    failures = 0
    movies = [
        movie(1, ANY, 2160),
        movie(2, ANY, 1080),
        movie(3, ANY, 720),
        movie(4, ANY, None),
        movie(5, 42, 2160),   # already managed: untouched
        movie(6, ANY, 480),
    ]
    plan = mig.plan_moves(movies, any_id=ANY, hd_id=HD, uhd_id=UHD)
    expected = {UHD: [1], HD: [2, 3, 4, 6]}
    if plan != expected:
        failures += 1
        print(f"[FAIL] plan_moves = {plan}, expected {expected}")
    if mig.plan_moves([movie(5, 42, 2160)], any_id=ANY, hd_id=HD, uhd_id=UHD) != {}:
        failures += 1
        print("[FAIL] nothing on the unmanaged profile must yield an empty plan")
    nulls = [
        {"id": 7, "qualityProfileId": ANY, "hasFile": True, "movieFile": {"quality": None}},
        {"id": 8, "qualityProfileId": ANY, "hasFile": True, "movieFile": {"quality": {"quality": None}}},
    ]
    if mig.plan_moves(nulls, any_id=ANY, hd_id=HD, uhd_id=UHD) != {HD: [7, 8]}:
        failures += 1
        print("[FAIL] null quality fields must fall back to the HD profile")
    for url, ok in (("http://localhost:7878", True), ("https://radarr.example", True),
                    ("file:///etc/passwd", False), ("ftp://x", False), ("localhost:7878", False)):
        if mig.valid_base_url(url) is not ok:
            failures += 1
            print(f"[FAIL] valid_base_url({url!r}) should be {ok}")
    before = mig.before_state(movies, any_id=ANY)
    if before != {"1": ANY, "2": ANY, "3": ANY, "4": ANY, "6": ANY}:
        failures += 1
        print(f"[FAIL] before_state wrong: {before}")
    print(f"{failures} failure(s)" if failures else "all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
