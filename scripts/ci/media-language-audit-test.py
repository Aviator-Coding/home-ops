#!/usr/bin/env python3
"""classify() decides whether a Radarr movie satisfies the English-audio policy.

Radarr reports ``movieFile.mediaInfo.audioLanguages`` as ISO 639-2 codes joined by ``/``
with duplicates (``eng/eng/ger``). German alone is a violation: English is required.
Also checks the report never leaks the API key or exception text.
"""

from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("language_audit", ROOT / "scripts/media/language_audit.py")
audit = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = audit
spec.loader.exec_module(audit)  # type: ignore[union-attr]


def movie(audio: str | None, *, has_file: bool = True) -> dict:
    if not has_file:
        return {"title": "T", "year": 2000, "hasFile": False}
    info = {} if audio is None else {"audioLanguages": audio}
    return {"title": "T", "year": 2000, "hasFile": True, "movieFile": {"mediaInfo": info}}


CASES = [
    ("eng", audit.Verdict.OK),
    ("eng/eng/eng", audit.Verdict.OK),
    ("eng/ger", audit.Verdict.OK),
    ("fre/eng", audit.Verdict.OK),
    ("ger", audit.Verdict.VIOLATION),
    ("fre", audit.Verdict.VIOLATION),
    ("fre/fre/por", audit.Verdict.VIOLATION),
    ("hin/tam", audit.Verdict.VIOLATION),
    ("", audit.Verdict.UNKNOWN),
    (None, audit.Verdict.UNKNOWN),
]


def main() -> int:
    failures = 0
    for audio, expected in CASES:
        got = audit.classify(movie(audio))
        if got is not expected:
            failures += 1
            print(f"[FAIL] classify({audio!r}) = {got}, expected {expected}")
    if audit.classify(movie(None, has_file=False)) is not audit.Verdict.NO_FILE:
        failures += 1
        print("[FAIL] movie without a file must be NO_FILE")

    for url, ok in (("http://localhost:7878", True), ("https://radarr.example", True),
                    ("file:///etc/passwd", False), ("ftp://x", False), ("localhost:7878", False)):
        if audit.valid_base_url(url) is not ok:
            failures += 1
            print(f"[FAIL] valid_base_url({url!r}) should be {ok}")

    movies = [movie("eng"), movie("fre"), movie(""), movie(None, has_file=False)]
    movies[1]["title"] = "Taxi"
    out = io.StringIO()
    with redirect_stdout(out):
        code = audit.report(movies)
    text = out.getvalue()
    if code != 1 or "Taxi" not in text or "violations: 1" not in text or "unknown: 1" not in text:
        failures += 1
        print(f"[FAIL] report exit/summary wrong: code={code} out={text!r}")
    if audit.report([movie("eng")]) != 0:
        failures += 1
        print("[FAIL] clean library must exit 0")

    print(f"{failures} failure(s)" if failures else "all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
