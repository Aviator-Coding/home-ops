#!/usr/bin/env python3
"""Behavioral contract for the ai/embedding-gpu VALUE-asserting health probe.

Pins the 2026-09-16 null-vector incident, and - more importantly - pins the
reason it went undetected for hours.

WHAT HAPPENED. `ai/embedding-gpu` returned 1024-dimension vectors in which
every element was `0xFFC00000` (a quiet NaN) on all three request shapes:
`/v1/embeddings` single, `/v1/embeddings` batch, and the native `/embedding`
endpoint. The server reported perfect health throughout - 202,582 tasks, clean
slot releases (`stop processing: n_tokens = 25, truncated = 0`), zero errors,
zero warnings, `/health` = ok. llama.cpp serialises through nlohmann::json,
which renders a non-finite float as a bare `null`, so the wire format was
`{"embedding":[null,null,...]}` under HTTP 200.

WHY IT WAS REPORTED HEALTHY, TWICE. The endpoint was verified with a probe of
the form `sum(1 for x in v if x != 0)`. In Python `None != 0` is True, so a
vector of 1024 nulls scored a PERFECT 1024 non-zero and the probe could not
distinguish total failure from success. A check that cannot fail is worse than
no check, because it manufactures confidence. That is the defect this file
exists to prevent, and `test_the_old_probe_could_not_have_failed` below pins
the trap itself so it cannot be reintroduced as "an equivalent check".

WHAT THIS ASSERTS. The probe shipped in the HelmRelease is extracted from the
manifest and EXECUTED against canned responses, so this gate tests the real
shipped command rather than a copy of it that could drift:

  good response       -> probe must PASS
  all-`null` vector   -> probe must FAIL   <- the exact observed failure
  all-zero vector     -> probe must FAIL
  HTTP 500            -> probe must FAIL
  connection refused  -> probe must FAIL

plus the manifest wiring: readiness and liveness must both use the value
probe (not `/health`), startup may keep `/health` because there is nothing to
embed until the GGUF has loaded, and the probe script must contain no `$`,
because Flux's envsubst substitutes bare `$var` as well as `${var}` across the
whole built Kustomization output.

WHAT THIS DOES NOT CATCH
  - whether the GPU is producing CORRECT vectors, only whether it is producing
    real numbers. A semantically wrong but finite vector passes. Agreement with
    the CPU endpoint is a measured, operator-run check - see
    docs/ai/embedder-gpu-migration-analysis-2026-09-15.md section 4 (baseline
    cos = 0.9999992) and section 11 (the re-measure procedure).
  - the root cause of the NaN itself, which is GPU-side and was NOT reproducible
    on any locally-runnable backend (section 11 records the full bisect).
"""

from __future__ import annotations

import http.server
import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/base/ai/embedding-gpu/app"
HR_PATH = APP / "helmrelease.yaml"

N_DIMS = 1024
PROBE_URL_HOST = "http://127.0.0.1:8080"


class Failure(Exception):
    pass


def assert_true(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def _docs() -> list[dict[str, Any]]:
    """Render the app the way Flux builds it, falling back to the raw file."""
    try:
        built = subprocess.run(
            ["kubectl", "kustomize", str(APP), "--load-restrictor", "LoadRestrictionsNone"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        docs = [d for d in yaml.safe_load_all(built) if isinstance(d, dict)]
        if docs:
            return docs
    except (FileNotFoundError, subprocess.CalledProcessError):
        pass
    return [d for d in yaml.safe_load_all(HR_PATH.read_text()) if isinstance(d, dict)]


def _probes(docs: list[dict[str, Any]]) -> dict[str, Any]:
    for doc in docs:
        if doc.get("kind") == "HelmRelease" and (doc.get("metadata") or {}).get("name") == "embedding-gpu":
            ctrl = doc["spec"]["values"]["controllers"]["embedding-gpu"]
            return ctrl["containers"]["app"]["probes"]
    raise Failure("could not load the embedding-gpu HelmRelease probes")


def _probe_script(probes: dict[str, Any], which: str) -> str:
    spec = (probes.get(which) or {}).get("spec") or {}
    exec_ = spec.get("exec") or {}
    cmd = exec_.get("command") or []
    assert_true(
        len(cmd) >= 3 and cmd[0] in ("sh", "/bin/sh") and cmd[1] == "-c",
        f"{which} probe must be an `sh -c <script>` exec probe that asserts on the "
        f"returned VALUES. An httpGet /health probe returns ok while every vector is "
        f"NaN - that is exactly the 2026-09-16 failure.",
    )
    return cmd[2]


# --------------------------------------------------------------------------
# canned responses
# --------------------------------------------------------------------------

def _response(vector: list[Any]) -> str:
    """Shaped exactly like llama.cpp's /v1/embeddings reply (compact separators)."""
    return json.dumps(
        {
            "model": "probe",
            "object": "list",
            "usage": {"prompt_tokens": 4, "total_tokens": 4},
            "data": [{"embedding": vector, "index": 0, "object": "embedding"}],
        },
        separators=(",", ":"),
    )


GOOD_VECTOR = [round(-0.0299 + i * 0.00007, 6) for i in range(N_DIMS)]
NULL_VECTOR: list[Any] = [None] * N_DIMS
ZERO_VECTOR: list[Any] = [0.0] * N_DIMS


class _Stub(http.server.BaseHTTPRequestHandler):
    body = ""
    status = 200

    def do_POST(self) -> None:  # noqa: N802
        payload = type(self).body.encode()
        self.send_response(type(self).status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args: Any) -> None:
        pass


def _run_probe(script: str, body: str, status: int = 200, serve: bool = True) -> int:
    """Run the SHIPPED probe script against a stub serving `body`."""
    if not serve:
        # nothing listening: connection refused
        return subprocess.run(
            ["sh", "-c", script.replace(PROBE_URL_HOST, "http://127.0.0.1:1")],
            capture_output=True,
        ).returncode

    _Stub.body = body
    _Stub.status = status
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
    port = srv.server_address[1]
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    try:
        target = f"http://127.0.0.1:{port}"
        assert_true(
            PROBE_URL_HOST in script,
            f"probe script no longer contains {PROBE_URL_HOST!r}, so this gate cannot "
            f"redirect it at a stub and would silently test nothing.",
        )
        return subprocess.run(
            ["sh", "-c", script.replace(PROBE_URL_HOST, target)],
            capture_output=True,
        ).returncode
    finally:
        srv.shutdown()
        srv.server_close()


# --------------------------------------------------------------------------
# tests
# --------------------------------------------------------------------------

def test_probe_passes_a_real_vector(docs: list[dict[str, Any]]) -> None:
    """Control: the shipped probe must accept a genuine response."""
    script = _probe_script(_probes(docs), "readiness")
    rc = _run_probe(script, _response(GOOD_VECTOR))
    assert_true(
        rc == 0,
        f"the readiness probe REJECTED a valid 1024-float embedding (exit {rc}). A probe "
        f"that fails on healthy output would crashloop the endpoint.",
    )


def test_probe_fails_a_null_filled_vector(docs: list[dict[str, Any]]) -> None:
    """THE point of this file: prove the assertion can fail, on the real payload.

    This is the exact wire format ai/embedding-gpu returned on 2026-09-16.
    """
    probes = _probes(docs)
    for which in ("readiness", "liveness"):
        script = _probe_script(probes, which)
        rc = _run_probe(script, _response(NULL_VECTOR))
        assert_true(
            rc != 0,
            f"the {which} probe ACCEPTED a vector of 1024 nulls. This is the 2026-09-16 "
            f"failure verbatim: llama.cpp renders NaN as bare JSON `null`, and a probe "
            f"that tolerates it cannot distinguish a working embedder from a dead one.",
        )


def test_probe_fails_an_all_zero_vector(docs: list[dict[str, Any]]) -> None:
    """A degenerate but finite vector is still a dead embedder."""
    probes = _probes(docs)
    for which in ("readiness", "liveness"):
        script = _probe_script(probes, which)
        rc = _run_probe(script, _response(ZERO_VECTOR))
        assert_true(
            rc != 0,
            f"the {which} probe ACCEPTED an all-zero vector. Zeros are finite numbers, so "
            f"a NaN-only check would pass them; the probe must also require a non-zero "
            f"magnitude inside the embedding array.",
        )


def test_probe_fails_when_the_server_errors(docs: list[dict[str, Any]]) -> None:
    """HTTP 500 and connection-refused must both fail closed."""
    script = _probe_script(_probes(docs), "readiness")
    rc = _run_probe(script, '{"error":{"code":500,"message":"boom"}}', status=500)
    assert_true(rc != 0, f"the readiness probe ACCEPTED an HTTP 500 response (exit {rc}).")
    rc = _run_probe(script, "", serve=False)
    assert_true(rc != 0, f"the readiness probe ACCEPTED a refused connection (exit {rc}).")


def test_the_old_probe_could_not_have_failed(_docs_unused: list[dict[str, Any]]) -> None:
    """Pin the trap itself, so it is never reintroduced as 'an equivalent check'.

    `sum(1 for x in v if x != 0)` scores a null-filled vector as fully non-zero,
    because `None != 0` is True in Python. This test does not check the repo; it
    documents WHY a shape/length/non-zero-count check is not evidence.
    """
    broken_score = sum(1 for x in NULL_VECTOR if x != 0)
    assert_true(
        broken_score == N_DIMS,
        "the historical broken probe no longer scores a null vector as 1024/1024 "
        "non-zero; if Python's semantics changed, rewrite this note rather than "
        "deleting it.",
    )

    def strict_ok(v: list[Any]) -> bool:
        import math

        return (
            isinstance(v, list)
            and len(v) == N_DIMS
            and not any(x is None for x in v)
            and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v)
            and all(math.isfinite(x) for x in v)
            and any(x != 0 for x in v)
        )

    assert_true(not strict_ok(NULL_VECTOR), "strict assertion must reject a null vector")
    assert_true(not strict_ok(ZERO_VECTOR), "strict assertion must reject an all-zero vector")
    assert_true(not strict_ok(GOOD_VECTOR[:-1]), "strict assertion must reject a short vector")
    assert_true(strict_ok(GOOD_VECTOR), "strict assertion must accept a real vector")


def test_probe_wiring_and_envsubst_safety(docs: list[dict[str, Any]]) -> None:
    """readiness+liveness must use the value probe; startup may keep /health."""
    probes = _probes(docs)

    startup = (probes.get("startup") or {}).get("spec") or {}
    assert_true(
        "httpGet" in startup or "exec" in startup,
        "startup probe must exist - first boot pulls a ~1.2GB GGUF.",
    )

    for which in ("readiness", "liveness"):
        script = _probe_script(probes, which)
        assert_true(
            "$" not in script,
            f"the {which} probe script contains a `$`. Flux runs envsubst over the entire "
            f"built Kustomization output and substitutes bare `$var` as well as `${{var}}`, "
            f"so a shell variable here is either blanked or fails the whole Kustomization "
            f"(AGENTS.md, postBuild.substitute collision).",
        )
        spec = probes[which]["spec"]
        assert_true(
            isinstance(spec.get("failureThreshold"), int) and spec["failureThreshold"] >= 3,
            f"{which} failureThreshold must be >= 3 so a single slow probe under backfill "
            f"load cannot bounce the pod.",
        )

    live = probes["liveness"]["spec"]
    ready = probes["readiness"]["spec"]
    live_budget = live["periodSeconds"] * live["failureThreshold"]
    ready_budget = ready["periodSeconds"] * ready["failureThreshold"]
    assert_true(
        live_budget > ready_budget,
        f"liveness must tolerate failure longer than readiness ({live_budget}s vs "
        f"{ready_budget}s), so the pod leaves the Service and callers fail over to the "
        f"CPU endpoint before a restart is attempted.",
    )


def main() -> int:
    docs = _docs()
    tests = [
        test_probe_passes_a_real_vector,
        test_probe_fails_a_null_filled_vector,
        test_probe_fails_an_all_zero_vector,
        test_probe_fails_when_the_server_errors,
        test_the_old_probe_could_not_have_failed,
        test_probe_wiring_and_envsubst_safety,
    ]
    failed = 0
    for test in tests:
        try:
            test(docs)
        except Failure as exc:
            failed += 1
            print(f"FAIL {test.__name__}: {exc}")
        else:
            print(f"ok   {test.__name__}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
