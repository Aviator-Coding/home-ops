#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Ask Jev (TypeSafe's decision model) questions through the home-ops LiteLLM gateway.

JSONL in, JSONL out. Each input line is one decision request in LiteLLM's
OpenAI-shaped POST /v1/decisions body, minus the model:

  {"id": "pr-12", "input": "<text, or any JSON value>",
   "questions": [{"type": "predicate", "name": "safe", "instructions": "Is ...?"},
                 {"type": "choice", "name": "tier", "instructions": "...",
                  "choices": [{"value": "low"}, {"value": "high"}]},
                 {"type": "score", "name": "risk", "instructions": "...",
                  "levels": [{"label": "none"}, {"label": "some"}, {"label": "severe"}]}]}

A non-string `input` is JSON-serialised. Each output line is
{"id", "ok": true, "answers": {name: answer}, "usage", "model"} or
{"id", "ok": false, "error": {...}}. A predicate answer is {"probability": p} (p = yes).

The key is read once from 1Password (`op read`) and cached in
${XDG_CACHE_HOME:-~/.cache}/jev-decisions/key (0600). Exit codes: 0 every line answered,
2 usage, 3 key/1Password, 4 network, 5 at least one line failed (see its JSON).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://litellm.sklab.dev"
DEFAULT_OP_REF = "op://Homelab/litellm-consumer-jev-decisions/key"
EXIT_USAGE, EXIT_KEY, EXIT_NET, EXIT_LINE = 2, 3, 4, 5


class Fail(Exception):
    def __init__(self, code: int, kind: str, message: str, hint: str = ""):
        super().__init__(message)
        self.code, self.kind, self.message, self.hint = code, kind, message, hint


def cache_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "jev-decisions" / "key"


def resolve_key(op_ref: str, refresh: bool) -> str:
    env = os.environ.get("JEV_API_KEY", "").strip()
    if env:
        return env
    path = cache_path()
    if not refresh and path.is_file():
        key = path.read_text().strip()
        if key:
            return key
    try:
        out = subprocess.run(["op", "read", op_ref], capture_output=True, text=True, timeout=60)
    except FileNotFoundError:
        raise Fail(EXIT_KEY, "op_missing", "1Password CLI `op` not found.",
                   "Install it, or export JEV_API_KEY.") from None
    except subprocess.TimeoutExpired:
        raise Fail(EXIT_KEY, "op_timeout", "`op read` timed out.", "Run `op signin` and retry.") from None
    if out.returncode != 0 or not out.stdout.strip():
        raise Fail(EXIT_KEY, "op_read_failed", f"`op read {op_ref}` failed: {out.stderr.strip()[:200]}",
                   "Check `op signin` and the vault, or pass --op-ref / JEV_OP_REF, or set JEV_API_KEY.")
    key = out.stdout.strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    path.write_text(key)
    return key


def decide(base_url: str, key: str, request: dict, timeout: float) -> dict:
    body = {k: v for k, v in request.items() if k != "id"}
    body["model"] = "jev"
    if not isinstance(body.get("input"), str):
        body["input"] = json.dumps(body.get("input"), indent=1)
    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/v1/decisions", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    answers = {}
    for a in data.get("answers") or []:
        name = a.get("name") or f"q{len(answers)}"
        answers[name] = {k: v for k, v in a.items() if k != "name"}
    names = [q.get("name") for q in request.get("questions") or []]
    missing = [n for n in names if n and n not in answers]
    if missing:
        raise ValueError(f"no answer for {missing}")
    return {"ok": True, "answers": answers, "usage": data.get("usage"), "model": data.get("model")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("file", nargs="?", default="-", help="JSONL requests (default: stdin)")
    ap.add_argument("--base-url", default=os.environ.get("JEV_BASE_URL", DEFAULT_BASE_URL))
    ap.add_argument("--op-ref", default=os.environ.get("JEV_OP_REF", DEFAULT_OP_REF))
    ap.add_argument("--refresh-key", action="store_true", help="re-read the key from 1Password")
    ap.add_argument("--timeout", type=float, default=60)
    args = ap.parse_args()

    stream = sys.stdin if args.file == "-" else open(args.file, encoding="utf-8")
    lines = [ln for ln in stream if ln.strip()]
    if not lines:
        print(json.dumps({"ok": False, "error": {"type": "usage", "message": "no JSONL requests"}}), file=sys.stderr)
        return EXIT_USAGE
    try:
        key = resolve_key(args.op_ref, args.refresh_key)
    except Fail as exc:
        print(json.dumps({"ok": False, "error": {"type": exc.kind, "message": exc.message, "hint": exc.hint}}),
              file=sys.stderr)
        return exc.code

    failed = network = 0
    for n, line in enumerate(lines, 1):
        rid = f"line-{n}"
        try:
            request = json.loads(line)
            rid = request.get("id", rid)
            result = decide(args.base_url, key, request, args.timeout)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:300]
            hint = {401: "key rejected: --refresh-key", 403: "key lacks the `jev` model",
                    429: "rpm or budget limit", 400: "malformed request body"}.get(exc.code, "")
            result = {"ok": False, "error": {"type": "http", "status": exc.code, "message": detail, "hint": hint}}
        except (urllib.error.URLError, TimeoutError) as exc:
            network += 1
            result = {"ok": False, "error": {"type": "network", "message": str(exc),
                                             "hint": "be on the LAN/VPN, or fix --base-url"}}
        except (ValueError, json.JSONDecodeError) as exc:
            result = {"ok": False, "error": {"type": "bad_request_or_response", "message": str(exc)}}
        failed += not result["ok"]
        print(json.dumps({"id": rid, **result}), flush=True)
    if network == len(lines):
        return EXIT_NET
    return EXIT_LINE if failed else 0


if __name__ == "__main__":
    sys.exit(main())
