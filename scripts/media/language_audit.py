#!/usr/bin/env python3
"""Read-only audit: which Radarr movie files lack an English audio track.

Usage (key from 1Password, never on the command line):
    kubectl -n downloads port-forward svc/radarr 7878:7878 &
    RADARR_URL=http://localhost:7878 RADARR_API_KEY=... python3 scripts/media/language_audit.py

Exit 0 clean, 1 violations or unknown files found, 2 Radarr unreadable. Deletes nothing.
Prints no API key and no exception text.
"""

from __future__ import annotations

import enum
import json
import os
import sys
import urllib.request

EXIT_OK, EXIT_FINDINGS, EXIT_ERROR = 0, 1, 2
ENGLISH_CODES = frozenset({"eng", "en", "english"})
REQUEST_TIMEOUT_SECONDS = 60


class Verdict(enum.Enum):
    OK = "ok"
    NO_FILE = "no_file"
    VIOLATION = "violation"
    UNKNOWN = "unknown"


def classify(movie: dict) -> Verdict:
    """English must be among the audio languages; German alone is not enough."""
    media_file = movie.get("movieFile")
    if not movie.get("hasFile") or not media_file:
        return Verdict.NO_FILE
    raw = (media_file.get("mediaInfo") or {}).get("audioLanguages") or ""
    languages = {code.strip().lower() for code in raw.split("/") if code.strip()}
    if not languages:
        return Verdict.UNKNOWN
    return Verdict.OK if languages & ENGLISH_CODES else Verdict.VIOLATION


def report(movies: list[dict]) -> int:
    verdicts = [(movie, classify(movie)) for movie in movies]
    counts = {v: sum(1 for _, got in verdicts if got is v) for v in Verdict}
    for movie, verdict in verdicts:
        if verdict in (Verdict.VIOLATION, Verdict.UNKNOWN):
            audio = ((movie.get("movieFile") or {}).get("mediaInfo") or {}).get("audioLanguages", "")
            print(f"{verdict.value}: {movie.get('title')} ({movie.get('year')}) [{audio}]")
    print(
        f"ok: {counts[Verdict.OK]} no_file: {counts[Verdict.NO_FILE]} "
        f"violations: {counts[Verdict.VIOLATION]} unknown: {counts[Verdict.UNKNOWN]}"
    )
    findings = counts[Verdict.VIOLATION] + counts[Verdict.UNKNOWN]
    return EXIT_FINDINGS if findings else EXIT_OK


def valid_base_url(url: str) -> bool:
    """Only http(s): urlopen would also read file:// and ftp:// URLs."""
    return url.lower().startswith(("http://", "https://"))


def fetch_movies(base_url: str, api_key: str) -> list[dict]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/v3/movie", headers={"X-Api-Key": api_key}
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    base_url, api_key = os.environ.get("RADARR_URL"), os.environ.get("RADARR_API_KEY")
    if not base_url or not api_key:
        print("RADARR_URL and RADARR_API_KEY must be set")
        return EXIT_ERROR
    if not valid_base_url(base_url):
        print("RADARR_URL must start with http:// or https://")
        return EXIT_ERROR
    try:
        movies = fetch_movies(base_url, api_key)
    except Exception:  # noqa: BLE001 - never echo exception text, it may carry the URL or key
        print("could not read the Radarr movie list")
        return EXIT_ERROR
    return report(movies)


if __name__ == "__main__":
    sys.exit(main())
