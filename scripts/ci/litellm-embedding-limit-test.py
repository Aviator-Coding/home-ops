#!/usr/bin/env python3
"""Load caps on the LiteLLM `embedding-local` alias (the shared B70 embedder).

Measured live 2026-09-30 (PR #1821 follow-ups): the embedder saturates at about
17 req/s. 100 concurrent workers slowed chat-local from ~3.3 s to ~20 s, and at
500 workers 1042 of 1529 requests waited 28-31 s, all HTTP 200, because
embedding-gpu's `--timeout 30` is a socket timeout and the server has no
admission limit. The caps live at LiteLLM instead, on the model CR:

  - `params.rpm`: a hard request rate for every key together (needs
    `router_settings.optional_pre_call_checks: [enforce_model_rate_limits]`,
    otherwise rpm only weights routing and nothing is ever refused).
  - `additional.max_parallel_requests`: the in-flight cap, refused at once.
  - `additional.timeout`: per-attempt request timeout.
  - `model_group_retry_policy.embedding-local` with 0 rate-limit and timeout
    retries. A cap that was live-tested earlier without this hung callers
    45 s+: the router sleeps to the end of the rpm window and retries a 429,
    and retries a timeout twice more.

This script pins the rendered values as relationships and then drives the real
LiteLLM Router (the library version CI installs to match the proxy image)
against a mock backend to prove each cap answers fast, plus two negative
controls that prove the router settings are load-bearing.

What this does not catch: whether the operator renders `params.rpm` the way
this test assumes (check `kubectl -n ai get cm litellm-config` after a merge),
the Redis-backed counters on the live proxy, and traffic that reaches
embedding-gpu without passing through LiteLLM (the agentgateway
`embedding-local` backend).
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/base/ai/litellm/app"
PROXY = APP / "litellmproxy.yaml"
MODELS = APP / "models"

ALIAS = "embedding-local"
# Saturation measured live; the rate cap must stay far below it.
SATURATION_RPM = 1000
# The openai client inside LiteLLM retries a timed-out embedding twice (measured on
# v1.103.1: `max_retries` is not honoured on that path), so one caller waits about
# attempts x timeout.
SDK_ATTEMPTS = 3


class Failure(Exception):
    pass


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_cr(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text())


def render_model_list() -> list[dict[str, Any]]:
    """The operator's file-mode render of every LiteLLMModel (typed rpm/tpm included)."""
    out = []
    for path in sorted(MODELS.glob("*.yaml")):
        for doc in yaml.safe_load_all(path.read_text()):
            if not doc or doc.get("kind") != "LiteLLMModel":
                continue
            pr = doc["spec"]["params"]
            params = dict(pr.get("additional") or {})
            params["model"] = pr["model"]
            for field, key in (("apiBase", "api_base"), ("apiKey", "api_key"), ("rpm", "rpm"), ("tpm", "tpm")):
                if pr.get(field) is not None:
                    params[key] = pr[field]
            out.append({"model_name": doc["spec"]["modelName"], "litellm_params": params})
    return out


def router_settings() -> dict[str, Any]:
    return load_cr(PROXY)["spec"]["routerSettings"]


def embedding_entry(models: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [m for m in models if m["model_name"] == ALIAS]
    assert_true(len(matches) == 1, f"expected one {ALIAS} LiteLLMModel, found {len(matches)}")
    return matches[0]


# --- static contract -------------------------------------------------------


def test_rendered_caps_sit_between_legitimate_use_and_saturation() -> None:
    lp = embedding_entry(render_model_list())["litellm_params"]
    rpm = lp.get("rpm")
    assert_true(isinstance(rpm, int) and rpm > 0, f"{ALIAS} must set params.rpm (got {rpm!r})")
    assert_true(
        rpm <= SATURATION_RPM // 8,
        f"rpm={rpm} is not clearly below the ~{SATURATION_RPM}/min saturation point that slowed chat "
        f"5-6x; keep it at or below {SATURATION_RPM // 8}.",
    )
    assert_true(
        rpm >= 30,
        f"rpm={rpm} is below the 30/min (0.5 req/s) backfill pace measured safe for chat "
        "(skill b70-llm-serving, contention.md), so legitimate bulk use would be refused.",
    )
    cap = lp.get("max_parallel_requests")
    assert_true(
        isinstance(cap, int) and 0 < cap <= 16,
        f"additional.max_parallel_requests={cap!r} must be an explicit cap of 1-16 "
        "(LiteLLM otherwise derives it from rpm, which would be 60 in flight)",
    )
    timeout = lp.get("timeout")
    assert_true(
        isinstance(timeout, (int, float)) and 0 < timeout <= 10,
        f"additional.timeout={timeout!r} must be 1-10 s: one caller waits about {SDK_ATTEMPTS} x it "
        "on a wedged backend, which has to stay under typical client timeouts (30 s+).",
    )


def test_router_makes_rpm_a_hard_limit_and_never_retries_the_caps() -> None:
    rs = router_settings()
    assert_true(
        "enforce_model_rate_limits" in (rs.get("optional_pre_call_checks") or []),
        "routerSettings.optional_pre_call_checks must include enforce_model_rate_limits; without it "
        f"{ALIAS}'s rpm only weights routing and never produces a 429.",
    )
    policy = (rs.get("model_group_retry_policy") or {}).get(ALIAS) or {}
    for key in ("RateLimitErrorRetries", "TimeoutErrorRetries"):
        assert_true(
            policy.get(key) == 0,
            f"model_group_retry_policy.{ALIAS}.{key} must be 0 (got {policy.get(key)!r}): the router "
            "otherwise sleeps to the end of the rpm window and retries, so the caller hangs ~60 s.",
        )
    assert_true(
        rs.get("routing_strategy") == "simple-shuffle",
        "routing_strategy must stay simple-shuffle (skill litellm-proxy, model-catalog.md)",
    )


def test_enforcement_is_scoped_to_the_embedder() -> None:
    limited = sorted(
        m["model_name"] for m in render_model_list() if {"rpm", "tpm"} & set(m["litellm_params"])
    )
    assert_true(
        limited == [ALIAS],
        f"models carrying rpm/tpm are {limited}; enforce_model_rate_limits is router-wide, so a new "
        f"deployment limit is a deliberate choice. Only {ALIAS} is expected (chat limits are out of scope).",
    )


# --- runtime proof against the real Router ---------------------------------


class Backend:
    def __init__(self) -> None:
        self.latency = 0.0
        self.hits = 0
        self.lock = threading.Lock()
        backend = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: Any) -> None:
                return

            def do_POST(self) -> None:
                self.rfile.read(int(self.headers.get("content-length", 0)))
                with backend.lock:
                    backend.hits += 1
                time.sleep(backend.latency)
                body = json.dumps(
                    {
                        "object": "list",
                        "model": "mock",
                        "data": [{"object": "embedding", "index": 0, "embedding": [0.1, 0.2]}],
                        "usage": {"prompt_tokens": 3, "total_tokens": 3},
                    }
                ).encode()
                try:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}/v1"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()


def build_router(backend: Backend, *, strip: tuple[str, ...] = (), timeout: float | None = None):
    """A Router from the rendered CRs, every backend pointed at the mock.

    `strip` removes router-setting keys to run a negative control.
    """
    from litellm import Router

    models = []
    for entry in render_model_list():
        if entry["model_name"] != ALIAS:
            continue
        lp = dict(entry["litellm_params"])
        lp["api_base"] = backend.base
        lp["api_key"] = "mock"
        if entry["model_name"] == ALIAS and timeout is not None:
            lp["timeout"] = timeout
        models.append({"model_name": entry["model_name"], "litellm_params": lp})
    rs = dict(router_settings())
    kwargs = {
        "routing_strategy": rs["routing_strategy"],
        "optional_pre_call_checks": rs.get("optional_pre_call_checks"),
        "model_group_retry_policy": {ALIAS: rs["model_group_retry_policy"][ALIAS]},
    }
    for key in strip:
        kwargs.pop(key, None)
    return Router(model_list=models, set_verbose=False, **kwargs)


async def embed(router, text: str = "x"):
    return await router.aembedding(model=ALIAS, input=[text])


async def timed(coro, limit: float):
    start = time.monotonic()
    try:
        await asyncio.wait_for(coro, limit)
        return "ok", time.monotonic() - start
    except asyncio.TimeoutError:
        return "hang", time.monotonic() - start
    except Exception as exc:  # litellm maps a refusal to RateLimitError (429)
        return type(exc).__name__, time.monotonic() - start


async def wait_for_minute_headroom(seconds: int = 15) -> None:
    """rpm counts per wall-clock minute; do not straddle the rollover mid-test."""
    while time.time() % 60 > 60 - seconds:
        await asyncio.sleep(1)


async def prove_rate_cap() -> dict[str, Any]:
    backend = Backend()
    try:
        await wait_for_minute_headroom()
        rpm = embedding_entry(render_model_list())["litellm_params"]["rpm"]
        router = build_router(backend)
        outcomes = []
        for i in range(rpm):
            outcomes.append(await timed(embed(router, f"a{i}"), 5))
        assert_true(all(o == "ok" for o, _ in outcomes), f"first {rpm} requests were refused: {outcomes[:3]}")

        refused, took = await timed(embed(router, "over"), 5)
        assert_true(refused == "RateLimitError", f"request {rpm + 1} was {refused!r}, want RateLimitError (429)")
        assert_true(took < 1.0, f"the 429 took {took:.2f}s; it must be immediate")

        return {"rpm": rpm, "refused_in_s": round(took, 3)}
    finally:
        backend.close()


async def prove_caps_need_the_retry_policy() -> dict[str, Any]:
    """Negative control: without the 0-retry policy refused callers are held by router retries."""
    backend = Backend()
    try:
        await wait_for_minute_headroom()
        rpm = embedding_entry(render_model_list())["litellm_params"]["rpm"]
        backend.latency = 0.3
        router = build_router(backend, strip=("model_group_retry_policy",))
        results = await asyncio.gather(*[timed(embed(router, f"c{i}"), 8) for i in range(rpm + 20)])
        slowest = max(t for _, t in results)
        assert_true(
            slowest > 1.0,
            f"without the retry policy the slowest caller still finished in {slowest:.2f}s; the router was "
            "expected to hold refused callers with retries (measured 5 s+ here, ~60 s on the proxy). If "
            "LiteLLM stopped retrying these, the policy is no longer load-bearing.",
        )
        return {"slowest_caller_s": round(slowest, 1)}
    finally:
        backend.close()


async def prove_rate_cap_needs_pre_call_checks() -> dict[str, Any]:
    """Negative control: without enforce_model_rate_limits rpm refuses nothing."""
    backend = Backend()
    try:
        await wait_for_minute_headroom()
        rpm = embedding_entry(render_model_list())["litellm_params"]["rpm"]
        router = build_router(backend, strip=("optional_pre_call_checks",))
        outcomes = [(await timed(embed(router, f"a{i}"), 5))[0] for i in range(rpm + 5)]
        assert_true(
            all(o == "ok" for o in outcomes),
            f"without optional_pre_call_checks some calls were refused: {set(outcomes)}; rpm would then be "
            "enforced by something else and this setting is not what this test assumes.",
        )
        return {"served_over_rpm": rpm + 5}
    finally:
        backend.close()


async def prove_in_flight_cap() -> dict[str, Any]:
    backend = Backend()
    try:
        await wait_for_minute_headroom()
        lp = embedding_entry(render_model_list())["litellm_params"]
        cap = lp["max_parallel_requests"]
        assert_true(cap + 4 <= lp["rpm"], "test assumes the burst fits inside rpm")
        backend.latency = 1.0
        router = build_router(backend)
        burst = cap + 4
        results = await asyncio.gather(*[timed(embed(router, f"b{i}"), 10) for i in range(burst)])
        served = sum(1 for o, _ in results if o == "ok")
        refused = [t for o, t in results if o == "RateLimitError"]
        assert_true(served == cap, f"{served} of {burst} were served concurrently, want exactly {cap}: {results}")
        assert_true(len(refused) == burst - cap, f"expected {burst - cap} refusals, got {len(refused)}: {results}")
        assert_true(max(refused) < 0.5, f"in-flight refusals took {max(refused):.2f}s; they must be immediate")
        return {"cap": cap, "served": served, "refused": len(refused)}
    finally:
        backend.close()


async def prove_timeout_bound() -> dict[str, Any]:
    backend = Backend()
    try:
        await wait_for_minute_headroom()
        timeout = 1.0  # shortened for CI; the contract is the multiple, not the literal
        backend.latency = 30.0
        router = build_router(backend, timeout=timeout)
        outcome, took = await timed(embed(router, "wedged"), 20)
        assert_true(outcome != "hang" and outcome != "ok", f"wedged backend gave {outcome!r} after {took:.1f}s")
        limit = SDK_ATTEMPTS * timeout + 3
        assert_true(
            took < limit,
            f"a wedged backend held the caller {took:.1f}s with timeout={timeout}; bound is ~{limit:.0f}s. "
            "TimeoutErrorRetries on the model group must stay 0 (the router would otherwise triple this).",
        )
        assert_true(backend.hits <= SDK_ATTEMPTS, f"backend saw {backend.hits} attempts, want <= {SDK_ATTEMPTS}")
        return {"timeout": timeout, "caller_held_s": round(took, 1), "backend_attempts": backend.hits}
    finally:
        backend.close()


SCENARIOS = {
    "rate cap answers 429 at once": prove_rate_cap,
    "caps are load-bearing on the 0-retry policy": prove_caps_need_the_retry_policy,
    "rate cap is load-bearing on enforce_model_rate_limits": prove_rate_cap_needs_pre_call_checks,
    "in-flight cap answers 429 at once": prove_in_flight_cap,
    "timeout bounds a wedged backend": prove_timeout_bound,
}


def have_litellm() -> bool:
    try:
        import litellm  # noqa: F401
    except ImportError:
        print("skip runtime proofs: litellm is not installed (CI installs litellm[proxy] at the proxy image version)")
        return False
    return True


def run_scenario_in_subprocess(name: str) -> str:
    """One interpreter per scenario: LiteLLM keeps rpm counters and the enforce_model_rate_limits
    callback process-wide, so scenarios sharing a process would contaminate each other."""
    proc = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--scenario", name],
        capture_output=True,
        text=True,
        timeout=120,
    )
    if proc.returncode != 0:
        raise Failure((proc.stdout.strip().splitlines() or [proc.stderr.strip()])[-1])
    return proc.stdout.strip().splitlines()[-1]


def main() -> int:
    if len(sys.argv) == 3 and sys.argv[1] == "--scenario":
        try:
            print(json.dumps(asyncio.run(SCENARIOS[sys.argv[2]]())))
        except Failure as exc:
            print(exc)
            return 1
        return 0

    failed = 0
    static = [
        test_rendered_caps_sit_between_legitimate_use_and_saturation,
        test_router_makes_rpm_a_hard_limit_and_never_retries_the_caps,
        test_enforcement_is_scoped_to_the_embedder,
    ]
    for test in static:
        try:
            test()
        except Failure as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    names = list(SCENARIOS) if have_litellm() else []
    for name in names:
        try:
            detail = run_scenario_in_subprocess(name)
        except Failure as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
        else:
            print(f"ok   {name} {detail}")
    total = len(static) + len(names)
    print(f"\n{total - failed}/{total} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
