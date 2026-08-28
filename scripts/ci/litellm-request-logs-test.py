#!/usr/bin/env python3
"""Behavioral validation of full prompt/response capture in LiteLLM spend logs.

Captain request 2026-08-27 / docs/ai-system/litellm/request-logs.md.

Renders the litellm-operator LiteLLMProxy CR the way file-mode does, then:

  1. Asserts general_settings.store_prompts_in_spend_logs is true.
  2. Asserts store_model_in_db stays false (GitOps protection, out of scope).
  3. Asserts MAX_STRING_LENGTH_PROMPT_IN_DB=1000000 is declared on the CR env.
  4. Asserts maximum_spend_logs_retention_period is "30d" (matches Barman).
  5. Asserts litellm_settings.callbacks is prometheus-only (no content export).
  6. When the real LiteLLM v1.98.0 runtime is importable, prove observable
     behaviour of the spend-log writers:
       - store flag off -> request/response payloads are literally "{}"
       - store flag on  -> full body and completion are serialized
       - default 2048-char cap truncates a 4683-char prompt with the marker
       - 1000000-char cap keeps that same prompt intact
       - messages column stays "{}" for ordinary (non-realtime) traffic
       - redact_credential_headers masks Authorization / x-api-key
       - cold_storage_object_key is None when cold_storage_custom_logger is unset
       - duration_in_seconds("30d") == 2592000; "1mo" is longer (31d)
  7. Asserts the request-logs runbook is a published retrieval contract.
  8. Asserts postgres-17 Barman retentionPolicy is 30d on the LAN NAS endpoint.

This is intentionally NOT a source-grep test: assertions are on the operator
render output, CR semantic model, and live library call results.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any
from unittest import mock

import yaml

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "kubernetes/apps/base/ai/litellm/app"
PROXY_PATH = APP_DIR / "litellmproxy.yaml"
RUNBOOK = REPO / "docs/ai-system/litellm/request-logs.md"
README_APP = REPO / "kubernetes/apps/base/ai/litellm/README.md"
README_DOCS = REPO / "docs/ai-system/litellm/README.md"
CLUSTER_17 = (
    REPO
    / "kubernetes/apps/base/database/cloudnative-pg/cluster-17/cluster-17.yaml"
)

# Matches the live probe recorded in request-logs.md §10.
PROBE_PROMPT_REPS = 140
PROBE_PROMPT = ("full-content-capture-probe-line\n" * PROBE_PROMPT_REPS).rstrip("\n")
PROBE_COMPLETION = "full content capture works"
EXPECTED_RETENTION = "30d"
EXPECTED_MAX_STRING = "1000000"
EXPECTED_IMAGE = "ghcr.io/berriai/litellm-non_root:v1.98.0"

RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def load_all_yaml(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def render_config_from_cr() -> dict:
    """Faithful port of litellm-operator file-mode render (see sibling tests)."""
    proxy = load_yaml(PROXY_PATH)
    assert proxy["kind"] == "LiteLLMProxy", PROXY_PATH
    spec = proxy["spec"]
    assert spec.get("applyMode", "file") == "file"

    config: dict = dict(spec.get("extraConfig") or {})
    for field, key in (
        ("generalSettings", "general_settings"),
        ("routerSettings", "router_settings"),
        ("litellmSettings", "litellm_settings"),
    ):
        if spec.get(field) is not None:
            config[key] = spec[field]
    return config


def test_cr_config() -> dict:
    proxy = load_yaml(PROXY_PATH)
    spec = proxy["spec"]
    cfg = render_config_from_cr()
    gs = cfg.get("general_settings") or {}
    ls = cfg.get("litellm_settings") or {}

    record(
        "pinned_image_is_v1_98_0",
        spec.get("image") == EXPECTED_IMAGE,
        f"image={spec.get('image')!r}",
    )

    record(
        "store_prompts_in_spend_logs_enabled",
        gs.get("store_prompts_in_spend_logs") is True,
        f"value={gs.get('store_prompts_in_spend_logs')!r}",
    )

    record(
        "store_model_in_db_remains_false",
        gs.get("store_model_in_db") is False,
        f"value={gs.get('store_model_in_db')!r}",
    )

    record(
        "retention_period_is_30d",
        gs.get("maximum_spend_logs_retention_period") == EXPECTED_RETENTION,
        f"value={gs.get('maximum_spend_logs_retention_period')!r}",
    )

    env = spec.get("env") or []
    env_map = {
        e["name"]: str(e.get("value", ""))
        for e in env
        if isinstance(e, dict) and "name" in e
    }
    record(
        "max_string_length_prompt_in_db_is_1e6",
        env_map.get("MAX_STRING_LENGTH_PROMPT_IN_DB") == EXPECTED_MAX_STRING,
        f"env={env_map.get('MAX_STRING_LENGTH_PROMPT_IN_DB')!r}",
    )

    callbacks = ls.get("callbacks")
    record(
        "callbacks_are_prometheus_only",
        callbacks == ["prometheus"],
        f"callbacks={callbacks!r}",
    )

    # No LiteLLM-native cold storage / S3 callback knobs on the CR.
    forbidden_keys = {
        "cold_storage_custom_logger",
        "s3_callback_params",
        "gcs_callback_params",
        "success_callback",
        "failure_callback",
    }
    present = sorted(k for k in forbidden_keys if k in ls or k in gs)
    record(
        "no_content_export_callbacks_configured",
        present == [],
        f"present={present}",
    )

    record(
        "apply_mode_is_file",
        spec.get("applyMode") == "file",
        f"applyMode={spec.get('applyMode')!r}",
    )

    return cfg


def test_barman_alignment() -> None:
    docs = load_all_yaml(CLUSTER_17)
    cluster = next(d for d in docs if d.get("kind") == "Cluster")
    backup = (cluster.get("spec") or {}).get("backup") or {}
    store = backup.get("barmanObjectStore") or {}
    record(
        "postgres17_barman_retention_is_30d",
        backup.get("retentionPolicy") == "30d",
        f"retentionPolicy={backup.get('retentionPolicy')!r}",
    )
    endpoint = store.get("endpointURL") or ""
    dest = store.get("destinationPath") or ""
    record(
        "postgres17_barman_is_lan_nas_minio",
        endpoint.startswith("https://nas.") and "home-ops-postgres-cluster" in dest,
        f"endpoint={endpoint!r} dest={dest!r}",
    )


def test_runbook_contract() -> None:
    text = RUNBOOK.read_text()
    required_phrases = [
        "store_prompts_in_spend_logs",
        "proxy_server_request",
        "GET /spend/logs/ui",
        "MAX_STRING_LENGTH_PROMPT_IN_DB",
        "maximum_spend_logs_retention_period",
        "store_model_in_db",
        "full content capture works",
        "0.0008539",
        "gen-1787882691-eK0dnnHsVU5MzNL03Rqx",
        "LiteLLM_SpendLogs",
        "cost_breakdown",
        "nas.",
        "30d",
    ]
    missing = [p for p in required_phrases if p not in text]
    record(
        "request_logs_runbook_is_retrieval_contract",
        missing == [],
        f"missing={missing}" if missing else f"bytes={len(text)}",
    )

    # Cross-links from the two READMEs so the captain can find the runbook.
    app_readme = README_APP.read_text()
    docs_readme = README_DOCS.read_text()
    record(
        "app_readme_points_at_request_logs_runbook",
        "request-logs.md" in app_readme or "request logs" in app_readme.lower(),
        "link present" if "request-logs" in app_readme else "missing link",
    )
    record(
        "docs_readme_points_at_request_logs_runbook",
        "request-logs.md" in docs_readme,
        "link present" if "request-logs.md" in docs_readme else "missing link",
    )


def _try_import_litellm() -> bool:
    try:
        import litellm  # noqa: F401

        return True
    except ImportError:
        return False


def test_runtime_spend_log_behaviour() -> None:
    if not _try_import_litellm():
        record(
            "runtime_litellm_available",
            False,
            "litellm not importable; run inside ghcr.io/berriai/litellm-non_root:v1.98.0",
        )
        return

    import litellm
    from litellm.constants import LITELLM_TRUNCATED_PAYLOAD_FIELD
    from litellm.litellm_core_utils.duration_parser import duration_in_seconds
    from litellm.litellm_core_utils.litellm_logging import (
        StandardLoggingPayloadSetup,
    )
    from litellm.proxy.litellm_pre_call_utils import redact_credential_headers
    from litellm.proxy.spend_tracking import spend_tracking_utils as stu

    record("runtime_litellm_available", True, f"litellm={getattr(litellm, '__version__', '?')}")

    # --- gate: off yields empty content columns ---
    with mock.patch.object(stu, "_should_store_prompts_and_responses_in_spend_logs", return_value=False):
        off_req = stu._get_proxy_server_request_for_spend_logs_payload(
            metadata={},
            litellm_params={
                "proxy_server_request": {
                    "body": {
                        "model": "gemini-3.5-flash-lite",
                        "messages": [{"role": "user", "content": PROBE_PROMPT}],
                    }
                }
            },
        )
        off_resp = stu._get_response_for_spend_logs_payload(
            payload={"response": {"choices": [{"message": {"content": PROBE_COMPLETION}}]}},
        )
        off_msgs = stu._get_messages_for_spend_logs_payload(
            standard_logging_payload={
                "call_type": "acompletion",
                "messages": [{"role": "user", "content": PROBE_PROMPT}],
            }
        )
    record(
        "runtime_store_off_request_is_empty_object",
        off_req == "{}",
        f"request={off_req!r}",
    )
    record(
        "runtime_store_off_response_is_empty_object",
        off_resp == "{}",
        f"response={off_resp!r}",
    )
    record(
        "runtime_store_off_messages_is_empty_object",
        off_msgs == "{}",
        f"messages={off_msgs!r}",
    )

    # --- gate: on stores body + response; messages still empty for chat ---
    body = {
        "model": "gemini-3.5-flash-lite",
        "messages": [{"role": "user", "content": PROBE_PROMPT}],
    }
    response_obj = {
        "id": "gen-1787882691-eK0dnnHsVU5MzNL03Rqx",
        "choices": [{"message": {"role": "assistant", "content": PROBE_COMPLETION}}],
        "usage": {"prompt_tokens": 2813, "completion_tokens": 4, "cost": 0.0008539},
    }

    with mock.patch.object(stu, "_should_store_prompts_and_responses_in_spend_logs", return_value=True):
        # Force the raised cap used on the CR so the probe is stored whole.
        with mock.patch.dict(os.environ, {"MAX_STRING_LENGTH_PROMPT_IN_DB": EXPECTED_MAX_STRING}):
            on_req = stu._get_proxy_server_request_for_spend_logs_payload(
                metadata={},
                litellm_params={"proxy_server_request": {"body": body}},
            )
            on_resp = stu._get_response_for_spend_logs_payload(
                payload={"response": response_obj},
            )
            on_msgs_chat = stu._get_messages_for_spend_logs_payload(
                standard_logging_payload={
                    "call_type": "acompletion",
                    "messages": body["messages"],
                }
            )
            on_msgs_realtime = stu._get_messages_for_spend_logs_payload(
                standard_logging_payload={
                    "call_type": "_arealtime",
                    "messages": body["messages"],
                }
            )

    stored_body = json.loads(on_req)
    stored_resp = json.loads(on_resp)
    stored_prompt = stored_body["messages"][0]["content"]
    stored_completion = stored_resp["choices"][0]["message"]["content"]

    record(
        "runtime_store_on_keeps_full_over_2048_char_prompt",
        stored_prompt == PROBE_PROMPT
        and len(stored_prompt) > 2048
        and LITELLM_TRUNCATED_PAYLOAD_FIELD not in on_req,
        f"len={len(stored_prompt)} expected={len(PROBE_PROMPT)} truncated={LITELLM_TRUNCATED_PAYLOAD_FIELD in on_req}",
    )
    record(
        "runtime_store_on_keeps_completion_and_cost",
        stored_completion == PROBE_COMPLETION
        and stored_resp.get("usage", {}).get("cost") == 0.0008539,
        f"completion={stored_completion!r} cost={stored_resp.get('usage', {}).get('cost')!r}",
    )
    record(
        "runtime_messages_column_empty_for_chat_completions",
        on_msgs_chat == "{}",
        f"messages={on_msgs_chat!r}",
    )
    realtime_parsed = json.loads(on_msgs_realtime) if on_msgs_realtime != "{}" else None
    realtime_content = None
    if isinstance(realtime_parsed, list) and realtime_parsed:
        realtime_content = realtime_parsed[0].get("content")
    record(
        "runtime_messages_column_populated_only_for_realtime",
        realtime_content == PROBE_PROMPT and on_msgs_realtime != "{}",
        f"len={len(on_msgs_realtime)} content_match={realtime_content == PROBE_PROMPT}",
    )

    # --- default 2048 truncates the same probe ---
    with mock.patch.dict(os.environ, {}, clear=False):
        # Ensure the env override is absent so the default 2048 path is used.
        env_without = {k: v for k, v in os.environ.items() if k != "MAX_STRING_LENGTH_PROMPT_IN_DB"}
        with mock.patch.dict(os.environ, env_without, clear=True):
            truncated = stu._sanitize_request_body_for_spend_logs_payload(
                {"messages": [{"role": "user", "content": PROBE_PROMPT}]},
                max_string_length_prompt_in_db=2048,
            )
    trunc_content = truncated["messages"][0]["content"]
    record(
        "runtime_default_2048_truncates_probe_prompt",
        LITELLM_TRUNCATED_PAYLOAD_FIELD in trunc_content
        and len(trunc_content) < len(PROBE_PROMPT)
        and trunc_content != PROBE_PROMPT,
        f"out_len={len(trunc_content)} in_len={len(PROBE_PROMPT)} has_marker={LITELLM_TRUNCATED_PAYLOAD_FIELD in trunc_content}",
    )

    # --- 1e6 keeps a prompt just under the cap and still truncates above it ---
    almost = "x" * 999_999
    over = "y" * 1_000_001
    kept = stu._sanitize_request_body_for_spend_logs_payload(
        {"t": almost},
        max_string_length_prompt_in_db=1_000_000,
    )
    cut = stu._sanitize_request_body_for_spend_logs_payload(
        {"t": over},
        max_string_length_prompt_in_db=1_000_000,
    )
    record(
        "runtime_1e6_cap_keeps_sub_limit_string",
        kept["t"] == almost,
        f"kept_len={len(kept['t'])}",
    )
    record(
        "runtime_1e6_cap_still_truncates_over_limit",
        LITELLM_TRUNCATED_PAYLOAD_FIELD in cut["t"] and cut["t"] != over,
        f"cut_has_marker={LITELLM_TRUNCATED_PAYLOAD_FIELD in cut['t']}",
    )

    # --- gate function itself reads general_settings / env ---
    # Patch the proxy_server general_settings the function imports.
    import litellm.proxy.proxy_server as proxy_server

    original_gs = dict(getattr(proxy_server, "general_settings", {}) or {})
    try:
        proxy_server.general_settings = {"store_prompts_in_spend_logs": True}
        with mock.patch.dict(os.environ, {"STORE_PROMPTS_IN_SPEND_LOGS": "false"}):
            gate_on = stu._should_store_prompts_and_responses_in_spend_logs()
        proxy_server.general_settings = {"store_prompts_in_spend_logs": False}
        with mock.patch.dict(os.environ, {}, clear=False):
            # Clear both the setting and env.
            env2 = {k: v for k, v in os.environ.items() if k != "STORE_PROMPTS_IN_SPEND_LOGS"}
            with mock.patch.dict(os.environ, env2, clear=True):
                gate_off = stu._should_store_prompts_and_responses_in_spend_logs()
        proxy_server.general_settings = {}
        with mock.patch.dict(os.environ, {"STORE_PROMPTS_IN_SPEND_LOGS": "true"}):
            gate_env = stu._should_store_prompts_and_responses_in_spend_logs()
    finally:
        proxy_server.general_settings = original_gs

    record(
        "runtime_gate_honours_general_settings_true",
        gate_on is True,
        f"gate_on={gate_on}",
    )
    record(
        "runtime_gate_default_false_without_setting",
        gate_off is False,
        f"gate_off={gate_off}",
    )
    record(
        "runtime_gate_honours_env_fallback",
        gate_env is True,
        f"gate_env={gate_env}",
    )

    # --- credential redaction ---
    fake_oauth = "Bearer sk-ant-oat01-FAKE-TOKEN-FOR-REDACTION-TEST"
    fake_key = "sk-or-v1-FAKE-OPENROUTER-KEY"
    redacted = redact_credential_headers(
        {
            "authorization": fake_oauth,
            "x-api-key": fake_key,
            "x-litellm-api-key": "sk-litellm-fake",
            "content-type": "application/json",
            "user-agent": "probe/1.0",
        }
    )
    redacted_blob = json.dumps(dict(redacted))
    record(
        "runtime_credentials_redacted_from_log_headers",
        redacted["authorization"] == "***REDACTED***"
        and redacted["x-api-key"] == "***REDACTED***"
        and redacted["x-litellm-api-key"] == "***REDACTED***"
        and redacted["content-type"] == "application/json"
        and fake_oauth not in redacted_blob
        and fake_key not in redacted_blob,
        f"authorization={redacted['authorization']!r}",
    )

    # --- cold storage off by default ---
    record(
        "runtime_cold_storage_logger_defaults_none",
        litellm.cold_storage_custom_logger is None,
        f"value={litellm.cold_storage_custom_logger!r}",
    )
    from datetime import datetime, timezone

    key = StandardLoggingPayloadSetup._generate_cold_storage_object_key(
        start_time=datetime.now(timezone.utc),
        response_id="gen-test",
        team_alias=None,
    )
    record(
        "runtime_cold_storage_object_key_is_none_when_unset",
        key is None,
        f"key={key!r}",
    )

    # --- retention duration parsing ---
    d30 = duration_in_seconds("30d")
    d1mo = duration_in_seconds("1mo")
    record(
        "runtime_30d_is_exactly_2592000_seconds",
        d30 == 2_592_000,
        f"30d={d30}",
    )
    record(
        "runtime_1mo_is_longer_than_30d",
        d1mo > d30,
        f"1mo={d1mo} 30d={d30}",
    )

    # Probe size sanity - the live verification used 4683 chars.
    record(
        "probe_prompt_exceeds_default_2048_cap",
        len(PROBE_PROMPT) > 2048,
        f"len={len(PROBE_PROMPT)}",
    )


def main() -> int:
    print(f"PROBE_PROMPT length: {len(PROBE_PROMPT)} chars")
    test_cr_config()
    test_barman_alignment()
    test_runbook_contract()
    test_runtime_spend_log_behaviour()

    failed = [r for r in RESULTS if not r["ok"]]
    print()
    print(f"Result: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    if failed:
        print("Failures:")
        for r in failed:
            print(f"  - {r['name']}: {r['detail']}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
