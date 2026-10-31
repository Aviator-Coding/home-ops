#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# dependencies = []
# ///
"""Generate images through the home-ops LiteLLM gateway (POST /v1/images/generations).

Models: gpt-image-2 (default), gemini-3.1-flash-image, grok-imagine-image.
Aliases: gpt, gemini, grok. Run with --list-models to see them.

The key is read once from 1Password (`op read`) and cached in
${XDG_CACHE_HOME:-~/.cache}/image-generation/key (0600). On success a JSON
result (files, model, usage, cost) goes to stdout. On failure a JSON error goes
to stderr and the exit code says why: 2 usage, 3 key/1Password, 4 network,
5 HTTP error from LiteLLM, 6 bad or empty response.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

DEFAULT_BASE_URL = "https://litellm.sklab.dev"
DEFAULT_OP_REF = "op://Homelab/litellm-consumer-image-generation/key"
DEFAULT_MODEL = "gpt-image-2"

EXIT_USAGE, EXIT_KEY, EXIT_NETWORK, EXIT_HTTP, EXIT_RESPONSE = 2, 3, 4, 5, 6

MODELS: dict[str, dict] = {
    "gpt-image-2": {
        "alias": "gpt",
        "provider": "OpenAI via OpenRouter",
        "cost": "~$0.006 low, more at medium/high",
        "options": True,
        "strengths": "text and typography, UI-style assets, precise instruction following",
    },
    "gemini-3.1-flash-image": {
        "alias": "gemini",
        "provider": "Google via OpenRouter",
        "cost": "~$0.07 per image",
        "options": False,
        "strengths": "photoreal scenes, natural lighting, describe-an-edit prompts",
    },
    "grok-imagine-image": {
        "alias": "grok",
        "provider": "xAI via cliproxyapi",
        "cost": "$0 (subscription)",
        "options": False,
        "strengths": "fast free drafts, stylized and illustrative looks",
    },
}
ALIASES = {m["alias"]: name for name, m in MODELS.items()}
SIZES = ("1024x1024", "1536x1024", "1024x1536", "auto")
QUALITIES = ("low", "medium", "high", "auto")


class Fail(Exception):
    def __init__(self, code: int, kind: str, message: str, hint: str = "", status: int | None = None):
        super().__init__(message)
        self.code, self.kind, self.message, self.hint, self.status = code, kind, message, hint, status


def cache_path() -> Path:
    base = Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache")
    return base / "image-generation" / "key"


def read_cached_key() -> str | None:
    try:
        key = cache_path().read_text().strip()
    except OSError:
        return None
    return key or None


def write_cached_key(key: str) -> None:
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key)
    path.chmod(0o600)


def clear_cached_key() -> None:
    cache_path().unlink(missing_ok=True)


def read_key_from_op(ref: str) -> str:
    try:
        out = subprocess.run(["op", "read", ref], capture_output=True, text=True, timeout=60, check=False)
    except FileNotFoundError:
        raise Fail(EXIT_KEY, "op_missing", "The 1Password CLI `op` is not installed.",
                   "Install it, or export IMAGEGEN_API_KEY.") from None
    except subprocess.TimeoutExpired:
        raise Fail(EXIT_KEY, "op_timeout", "`op read` timed out.",
                   "Run `op signin` (or unlock the 1Password app), then retry.") from None
    key = out.stdout.strip()
    if out.returncode != 0 or not key:
        detail = (out.stderr.strip().splitlines() or ["empty value"])[-1]
        raise Fail(EXIT_KEY, "op_read_failed", f"`op read {ref}` failed: {detail}",
                   "Check `op signin`, that the item exists in that vault, or pass --op-ref / IMAGEGEN_OP_REF "
                   "(vault is Homelab, Automation or Services). IMAGEGEN_API_KEY skips 1Password.")
    write_cached_key(key)
    return key


def resolve_key(op_ref: str, refresh: bool) -> tuple[str, str]:
    """Return (key, source). Order: env, cache, 1Password."""
    env = os.environ.get("IMAGEGEN_API_KEY", "").strip()
    if env:
        return env, "env"
    if not refresh:
        cached = read_cached_key()
        if cached:
            return cached, "cache"
    return read_key_from_op(op_ref), "op"


def http(method: str, url: str, key: str, body: dict | None, timeout: float):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise Fail(EXIT_NETWORK, "network", f"Could not reach {url}: {reason}",
                   "The gateway resolves on the private VLAN only; check VPN/LAN, or pass --base-url.") from None


def http_hint(status: int, message: str) -> str:
    low = message.lower()
    if "key limit exceeded" in low or "monthly limit" in low or "insufficient credits" in low:
        return ("The upstream OpenRouter key hit its own spend limit (shared by every OpenRouter model, not this "
                "key's budget). Use grok ($0) until it resets, or raise the limit in OpenRouter.")
    if status == 401:
        return "Key rejected. Re-run with --refresh-key; confirm the Secret/1Password item matches the live key."
    if status == 403 or "not allowed to access model" in low:
        return "The image-generation key does not allow this model. Run --list-models."
    if status == 429 or "budget" in low:
        return "Rate limit (30 rpm) or the $10/30d budget is exhausted. Wait, or use grok ($0)."
    if status == 400:
        return "Request rejected. Check the prompt (content policy) and that --size/--quality suit the model."
    if status >= 500:
        return "Upstream provider error. Retry, or try another model."
    return ""


def error_message(raw: bytes) -> str:
    try:
        err = json.loads(raw).get("error", raw.decode(errors="replace"))
    except (ValueError, AttributeError):
        return raw.decode(errors="replace")[:500]
    return err.get("message", json.dumps(err)) if isinstance(err, dict) else str(err)


def list_models(args, key: str) -> int:
    live: set[str] | None = None
    note = ""
    try:
        status, _, raw = http("GET", f"{args.base_url}/v1/models", key, None, args.timeout)
        if status == 200:
            live = {m["id"] for m in json.loads(raw).get("data", [])}
        else:
            note = f"live check failed (HTTP {status}): {error_message(raw)}"
    except Fail as e:
        note = f"live check unavailable: {e.message}"
    rows = []
    for name, m in MODELS.items():
        rows.append({"model": name, "alias": m["alias"], "default": name == DEFAULT_MODEL,
                     "provider": m["provider"], "cost": m["cost"], "strengths": m["strengths"],
                     "supports_size_quality": m["options"],
                     "available": None if live is None else name in live})
    print(json.dumps({"ok": True, "models": rows, "base_url": args.base_url, "note": note or None}, indent=2))
    return 0


def output_paths(out: str | None, model: str, n: int) -> list[Path]:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    base = Path(out) if out else Path(f"image-{model}-{stamp}.png")
    if base.suffix == "":
        base = base.with_suffix(".png")
    if n == 1:
        return [base]
    return [base.with_name(f"{base.stem}-{i}{base.suffix}") for i in range(1, n + 1)]


def save_item(item: dict, path: Path, timeout: float) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if item.get("b64_json"):
        path.write_bytes(base64.b64decode(item["b64_json"]))
    elif item.get("url"):
        try:
            with urllib.request.urlopen(item["url"], timeout=timeout) as r:
                path.write_bytes(r.read())
        except (urllib.error.URLError, OSError) as e:
            raise Fail(EXIT_RESPONSE, "download_failed", f"Could not download image URL: {e}") from None
    else:
        raise Fail(EXIT_RESPONSE, "empty_image", "Response item had neither b64_json nor url.")


def generate(args, key: str) -> dict:
    model = ALIASES.get(args.model, args.model)
    if model not in MODELS:
        raise Fail(EXIT_USAGE, "unknown_model", f"Unknown model {args.model!r}.",
                   "Use --list-models. Valid: " + ", ".join(list(MODELS) + list(ALIASES)))
    body: dict = {"model": model, "prompt": args.prompt, "n": args.n}
    warnings: list[str] = []
    for flag, value in (("size", args.size), ("quality", args.quality)):
        if value is None:
            continue
        if MODELS[model]["options"]:
            body[flag] = value
        else:
            warnings.append(f"--{flag} is ignored by {model}")
    started = time.monotonic()
    status, headers, raw = http("POST", f"{args.base_url}/v1/images/generations", key, body, args.timeout)
    elapsed = round(time.monotonic() - started, 2)
    if status != 200:
        msg = error_message(raw)
        raise Fail(EXIT_HTTP, "http_error", msg, http_hint(status, msg), status)
    try:
        payload = json.loads(raw)
        items = payload["data"]
    except (ValueError, KeyError, TypeError):
        raise Fail(EXIT_RESPONSE, "bad_response", "Response was not an images payload.",
                   status=status) from None
    if not items:
        raise Fail(EXIT_RESPONSE, "empty_response", "The model returned no images.", status=status)
    paths = output_paths(args.out, model, len(items))
    for item, path in zip(items, paths):
        save_item(item, path, args.timeout)
    cost = headers.get("x-litellm-response-cost") or headers.get("X-Litellm-Response-Cost")
    result = {"ok": True, "files": [str(p.resolve()) for p in paths], "model": model, "elapsed_s": elapsed,
              "size": body.get("size"), "quality": body.get("quality"), "usage": payload.get("usage"),
              "cost_usd": float(cost) if cost not in (None, "") else None,
              "revised_prompt": next((i.get("revised_prompt") for i in items if i.get("revised_prompt")), None)}
    if warnings:
        result["warnings"] = warnings
    return result


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("prompt", nargs="?", help="image prompt, or '-' to read it from stdin")
    p.add_argument("-p", "--prompt", dest="prompt_opt", help="image prompt (alternative to the positional)")
    p.add_argument("-m", "--model", default=DEFAULT_MODEL,
                   help=f"gpt | gemini | grok or a full model name (default {DEFAULT_MODEL})")
    p.add_argument("-s", "--size", choices=SIZES, help="gpt-image-2 only")
    p.add_argument("-q", "--quality", choices=QUALITIES, help="gpt-image-2 only")
    p.add_argument("-n", type=int, default=1, help="number of images (1-4, default 1)")
    p.add_argument("-o", "--out", help="output file (default ./image-<model>-<timestamp>.png)")
    p.add_argument("--list-models", action="store_true", help="list models and whether the key can use them")
    p.add_argument("--refresh-key", action="store_true", help="ignore the cache and re-read the key from 1Password")
    p.add_argument("--base-url", default=os.environ.get("IMAGEGEN_BASE_URL", DEFAULT_BASE_URL))
    p.add_argument("--op-ref", default=os.environ.get("IMAGEGEN_OP_REF", DEFAULT_OP_REF),
                   help=f"1Password reference for the key (default {DEFAULT_OP_REF})")
    p.add_argument("--timeout", type=float, default=300, help="seconds (default 300)")
    return p


def run(argv: list[str]) -> int:
    args = build_parser().parse_args(argv)
    args.base_url = args.base_url.rstrip("/")
    args.prompt = args.prompt_opt or args.prompt
    if args.prompt == "-":
        args.prompt = sys.stdin.read().strip()
    if not args.list_models:
        if not args.prompt:
            raise Fail(EXIT_USAGE, "missing_prompt", "No prompt given.",
                       'Usage: generate_image.py "your prompt" [-m gpt|gemini|grok]; --list-models lists models.')
        if not 1 <= args.n <= 4:
            raise Fail(EXIT_USAGE, "bad_n", "-n must be between 1 and 4.")
        if ALIASES.get(args.model, args.model) not in MODELS:
            raise Fail(EXIT_USAGE, "unknown_model", f"Unknown model {args.model!r}.",
                       "Valid: " + ", ".join(list(MODELS) + list(ALIASES)))
    key, source = resolve_key(args.op_ref, args.refresh_key)
    if args.list_models:
        return list_models(args, key)
    try:
        result = generate(args, key)
    except Fail as e:
        if e.status == 401 and source != "op" and not os.environ.get("IMAGEGEN_API_KEY"):
            clear_cached_key()
            key = read_key_from_op(args.op_ref)
            result = generate(args, key)
        else:
            raise
    print(json.dumps(result, indent=2))
    return 0


def main() -> int:
    try:
        return run(sys.argv[1:])
    except Fail as e:
        err = {"type": e.kind, "message": e.message}
        if e.status is not None:
            err["status"] = e.status
        if e.hint:
            err["hint"] = e.hint
        print(json.dumps({"ok": False, "error": err}, indent=2), file=sys.stderr)
        return e.code


if __name__ == "__main__":
    sys.exit(main())
