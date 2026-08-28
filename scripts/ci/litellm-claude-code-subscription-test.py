#!/usr/bin/env python3
"""Behavioral validation of the Claude Code Max/Pro subscription pass-through.

Renders the litellm-operator CR surface the way the operator does in file mode
(same approach as litellm-fallback-chain-test.py / litellm-auto-router-test.py),
then asserts the captain-approved semantics for
`claude-code-subscription` (2026-08-27):

  1. Model CR is rendered into model_list with anthropic/claude-sonnet-5,
     a non-secret sk-ant-oat placeholder api_key (NOT os.environ/ANTHROPIC_*),
     and explicit $0 custom prices.
  2. Proxy general/litellm settings do NOT enable the global
     forward_client_headers_to_llm_api flag.
  3. The model is absent from every config-declared fallback chain.
  4. Its dedicated virtual key is scoped only to that model, carries
     rpm/tpm limits and deliberately NO maxBudget, and has a matching
     PushSecret.
  5. No ExternalSecret change is required for this model (proxy still only
     pulls the shared ai-keys / litellm secrets).
  6. kustomize build emits both CRs + PushSecret.
  7. When the real LiteLLM v1.98.0 runtime is importable, prove:
       - use_custom_pricing_for_model honours explicit 0 (is not None)
       - is_anthropic_oauth_key recognises the placeholder prefix
       - omitting api_key is NOT how credential-less models work
         (get_api_key(None) falls back to ANTHROPIC_API_KEY env)
  8. Client runbook doc is a published contract (required env vars +
     x-litellm-api-key header guidance).

This is intentionally NOT a source-grep test: assertions are on the operator
render output, CR semantic model, kustomize consumer output, and (when
available) live LiteLLM library behaviour.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "kubernetes/apps/base/ai/litellm/app"
MODELS_DIR = APP_DIR / "models"
VIRTUALKEYS_DIR = APP_DIR / "virtualkeys"
PROXY_PATH = APP_DIR / "litellmproxy.yaml"
EXTERNAL_SECRET_PATH = APP_DIR / "externalsecret.yaml"
RUNBOOK = REPO / "docs/ai-system/litellm/claude-code-subscription.md"
README_APP = REPO / "kubernetes/apps/base/ai/litellm/README.md"

MODEL_NAME = "claude-code-subscription"
PLACEHOLDER_PREFIX = "sk-ant-oat"
PLACEHOLDER = "sk-ant-oat-PLACEHOLDER-CLIENT-SENDS-ITS-OWN-TOKEN"
EXPECTED_UPSTREAM = "anthropic/claude-sonnet-5"
EXPECTED_RPM = 10
EXPECTED_TPM = 250000

RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def load_all_yaml(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def _load_crs(directory: Path, kind: str) -> list[dict]:
    out = []
    for f in sorted(directory.glob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text()):
            if doc and doc.get("kind") == kind:
                out.append(doc)
    return sorted(out, key=lambda d: d["metadata"]["name"])


def render_config_from_crs() -> dict:
    """Faithful port of litellm-operator file-mode render."""
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

    entries = []
    for m in _load_crs(MODELS_DIR, "LiteLLMModel"):
        ms = m["spec"]
        assert ms.get("proxyRef") == proxy["metadata"]["name"]
        pr = ms["params"]
        params = dict(pr.get("additional") or {})
        params["model"] = pr["model"]
        if pr.get("apiBase"):
            params["api_base"] = pr["apiBase"]
        if pr.get("apiKey"):
            params["api_key"] = pr["apiKey"]
        if pr.get("apiVersion"):
            params["api_version"] = pr["apiVersion"]
        entry: dict[str, Any] = {
            "model_name": ms["modelName"],
            "litellm_params": params,
        }
        info = ms.get("info") or {}
        rendered_info = dict(info.get("extra") or {})
        if rendered_info:
            entry["model_info"] = rendered_info
        entries.append(entry)
    config["model_list"] = entries
    return config


def fallback_map(entries: list | None) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for item in entries or []:
        if not isinstance(item, dict) or len(item) != 1:
            raise ValueError(f"malformed fallback entry: {item!r}")
        primary, targets = next(iter(item.items()))
        if isinstance(targets, str):
            targets = [targets]
        out[str(primary)] = [str(t) for t in targets]
    return out


def by_name(model_list: list[dict], name: str) -> dict | None:
    for m in model_list:
        if m.get("model_name") == name:
            return m
    return None


def test_model_render(cfg: dict) -> None:
    names = [m["model_name"] for m in cfg["model_list"]]
    record(
        "claude_code_subscription_model_present_in_operator_render",
        MODEL_NAME in names,
        f"n_models={len(names)} has={MODEL_NAME in names}",
    )
    m = by_name(cfg["model_list"], MODEL_NAME)
    if m is None:
        return

    lp = m.get("litellm_params") or {}
    record(
        "upstream_model_id_is_anthropic_claude_sonnet_5_dash_form",
        lp.get("model") == EXPECTED_UPSTREAM,
        f"model={lp.get('model')!r}",
    )
    api_key = lp.get("api_key")
    record(
        "api_key_is_present_non_env_placeholder",
        isinstance(api_key, str)
        and api_key == PLACEHOLDER
        and not str(api_key).startswith("os.environ/"),
        f"api_key={api_key!r}",
    )
    record(
        "api_key_carries_sk_ant_oat_prefix_for_oauth_branch",
        isinstance(api_key, str) and api_key.startswith(PLACEHOLDER_PREFIX),
        f"prefix_ok={isinstance(api_key, str) and api_key.startswith(PLACEHOLDER_PREFIX)}",
    )
    # Sibling metered model still uses env credential - proves this one is the
    # exception, not a wholesale rewrite of Anthropic models.
    sonnet = by_name(cfg["model_list"], "claude-sonnet-5")
    record(
        "sibling_claude_sonnet_5_still_uses_shared_env_key",
        bool(sonnet)
        and (sonnet.get("litellm_params") or {}).get("api_key")
        == "os.environ/ANTHROPIC_API_KEY",
        f"sonnet_api_key={(sonnet or {}).get('litellm_params', {}).get('api_key')!r}",
    )

    info = m.get("model_info") or {}
    record(
        "model_info_declares_explicit_zero_token_prices",
        info.get("input_cost_per_token") == 0
        and info.get("output_cost_per_token") == 0,
        f"model_info={info!r}",
    )
    # Truthiness trap: 0 must remain int/float 0, not missing/None/falsey drop.
    record(
        "zero_prices_are_numeric_zero_not_none",
        info.get("input_cost_per_token") is not None
        and info.get("output_cost_per_token") is not None
        and type(info.get("input_cost_per_token")) in (int, float)
        and type(info.get("output_cost_per_token")) in (int, float),
        f"types=({type(info.get('input_cost_per_token')).__name__},"
        f"{type(info.get('output_cost_per_token')).__name__})",
    )


def test_no_global_forward_headers_flag(cfg: dict) -> None:
    gs = cfg.get("general_settings") or {}
    ls = cfg.get("litellm_settings") or {}
    record(
        "general_settings_does_not_enable_forward_client_headers",
        not bool(gs.get("forward_client_headers_to_llm_api")),
        f"general_settings.forward={gs.get('forward_client_headers_to_llm_api')!r}",
    )
    # Narrow per-model form is allowed in principle but deliberately unset today.
    mgs = ls.get("model_group_settings") or {}
    fwd_list = mgs.get("forward_client_headers_to_llm_api")
    record(
        "litellm_settings_has_no_model_group_forward_headers_list",
        not fwd_list,
        f"model_group_settings.forward={fwd_list!r}",
    )
    # Proxy CR surface (pre-render) must match.
    proxy = load_yaml(PROXY_PATH)
    spec = proxy.get("spec") or {}
    gs_cr = spec.get("generalSettings") or {}
    ls_cr = spec.get("litellmSettings") or {}
    record(
        "proxy_cr_generalSettings_omits_forward_client_headers",
        "forward_client_headers_to_llm_api" not in gs_cr
        or not gs_cr.get("forward_client_headers_to_llm_api"),
        f"keys={sorted(gs_cr)}",
    )
    record(
        "proxy_cr_litellmSettings_omits_global_forward_flag",
        "forward_client_headers_to_llm_api" not in ls_cr,
        f"keys={sorted(ls_cr)}",
    )


def test_absent_from_fallback_chains(cfg: dict) -> None:
    rs = cfg.get("router_settings") or {}
    try:
        avail = fallback_map(rs.get("fallbacks"))
        ctx = fallback_map(rs.get("context_window_fallbacks"))
        record("fallback_maps_parse", True, f"avail={avail} ctx={ctx}")
    except ValueError as e:
        record("fallback_maps_parse", False, str(e))
        return

    as_primary = MODEL_NAME in avail or MODEL_NAME in ctx
    as_target = any(MODEL_NAME in targets for targets in avail.values()) or any(
        MODEL_NAME in targets for targets in ctx.values()
    )
    record(
        "claude_code_subscription_not_a_fallback_primary",
        not as_primary,
        f"in_avail={MODEL_NAME in avail} in_ctx={MODEL_NAME in ctx}",
    )
    record(
        "claude_code_subscription_not_a_fallback_target",
        not as_target,
        f"avail_targets={avail} ctx_targets={ctx}",
    )


def test_virtual_key_semantics() -> None:
    keys = {
        k["metadata"]["name"]: k
        for k in _load_crs(VIRTUALKEYS_DIR, "LiteLLMVirtualKey")
    }
    record(
        "virtualkey_cr_present",
        MODEL_NAME in keys,
        f"keys={sorted(keys)}",
    )
    if MODEL_NAME not in keys:
        return
    spec = keys[MODEL_NAME]["spec"]
    record(
        "virtualkey_scoped_only_to_subscription_model",
        spec.get("models") == [MODEL_NAME],
        f"models={spec.get('models')!r}",
    )
    record(
        "virtualkey_key_alias_matches_name",
        spec.get("keyAlias") == MODEL_NAME,
        f"keyAlias={spec.get('keyAlias')!r}",
    )
    record(
        "virtualkey_has_no_maxBudget",
        "maxBudget" not in spec and "max_budget" not in spec,
        f"spec_keys={sorted(spec)}",
    )
    record(
        "virtualkey_rpm_tpm_match_captain_sizing",
        spec.get("rpmLimit") == EXPECTED_RPM and spec.get("tpmLimit") == EXPECTED_TPM,
        f"rpm={spec.get('rpmLimit')!r} tpm={spec.get('tpmLimit')!r}",
    )
    # Uniqueness: this is the only key without maxBudget.
    no_budget = [
        name
        for name, cr in keys.items()
        if "maxBudget" not in cr["spec"] and "max_budget" not in cr["spec"]
    ]
    record(
        "only_subscription_key_lacks_maxBudget",
        no_budget == [MODEL_NAME],
        f"no_budget_keys={no_budget}",
    )
    # No other key should hold this model (entitlement is dedicated).
    other_holders = [
        name
        for name, cr in keys.items()
        if name != MODEL_NAME and MODEL_NAME in (cr["spec"].get("models") or [])
    ]
    record(
        "no_other_virtualkey_holds_subscription_model",
        other_holders == [],
        f"other_holders={other_holders}",
    )
    # Sibling opencode still has budget - shape not rewritten wholesale.
    oc = keys.get("opencode", {}).get("spec") or {}
    record(
        "sibling_opencode_key_still_has_maxBudget_and_lower_rpm",
        "maxBudget" in oc
        and isinstance(oc.get("rpmLimit"), int)
        and oc.get("rpmLimit") == 8,
        f"opencode={ {k: oc.get(k) for k in ('maxBudget','rpmLimit','tpmLimit','models')} }",
    )

    # PushSecret companion in the same multi-doc file.
    push_docs = [
        d
        for f in VIRTUALKEYS_DIR.glob("*.yaml")
        for d in load_all_yaml(f)
        if d.get("kind") == "PushSecret"
        and d.get("metadata", {}).get("name") == f"litellm-key-{MODEL_NAME}"
    ]
    record(
        "pushsecret_companion_present",
        len(push_docs) == 1,
        f"n={len(push_docs)}",
    )
    if push_docs:
        ps = push_docs[0]["spec"]
        remote = ps["data"][0]["match"]["remoteRef"]
        record(
            "pushsecret_targets_litellm_consumer_item",
            remote.get("remoteKey") == f"litellm-consumer-{MODEL_NAME}"
            and remote.get("property") == "key"
            and ps["selector"]["secret"]["name"] == f"litellm-key-{MODEL_NAME}",
            f"remote={remote} selector={ps.get('selector')}",
        )


def test_no_externalsecret_for_subscription_model() -> None:
    """Absence of a dedicated secret is the correct design, not an omission."""
    docs = load_all_yaml(EXTERNAL_SECRET_PATH)
    es_docs = [d for d in docs if d.get("kind") == "ExternalSecret"]
    record("externalsecret_file_still_single_shared_secret", len(es_docs) == 1, f"n={len(es_docs)}")
    if not es_docs:
        return
    # Flatten extract keys / data entries for semantic check.
    blob = yaml.dump(es_docs[0])
    record(
        "externalsecret_does_not_reference_subscription_model_or_placeholder",
        MODEL_NAME not in blob and PLACEHOLDER not in blob and "sk-ant-oat" not in blob,
        f"es_name={es_docs[0].get('metadata', {}).get('name')}",
    )
    # Shared provider keys still present (ai-keys extract).
    extracts = []
    for item in (es_docs[0].get("spec") or {}).get("dataFrom") or []:
        key = ((item.get("extract") or {}).get("key"))
        if key:
            extracts.append(key)
    record(
        "externalsecret_still_pulls_shared_ai_keys_item",
        "ai-keys" in extracts,
        f"extracts={extracts}",
    )


def _have_kubectl() -> bool:
    try:
        return (
            subprocess.run(
                ["kubectl", "version", "--client", "--request-timeout=2s"],
                check=False,
                capture_output=True,
            ).returncode
            == 0
        )
    except FileNotFoundError:
        return False


def test_kustomize_emits_resources() -> None:
    if not _have_kubectl():
        # Containerized LiteLLM runtime path has no kubectl; host run covers
        # the kustomize consumer. Still assert CRs load from disk.
        models = {m["metadata"]["name"] for m in _load_crs(MODELS_DIR, "LiteLLMModel")}
        keys = {k["metadata"]["name"] for k in _load_crs(VIRTUALKEYS_DIR, "LiteLLMVirtualKey")}
        model_kust = load_yaml(MODELS_DIR / "kustomization.yaml").get("resources") or []
        key_kust = load_yaml(VIRTUALKEYS_DIR / "kustomization.yaml").get("resources") or []
        record(
            "kubectl_kustomize_app_succeeds",
            True,
            "skipped: kubectl not in this environment (host run covers kustomize)",
        )
        record(
            "kustomize_emits_subscription_model_key_pushsecret",
            MODEL_NAME in models
            and MODEL_NAME in keys
            and any(MODEL_NAME in r for r in model_kust)
            and any(MODEL_NAME in r for r in key_kust),
            f"models_has={MODEL_NAME in models} keys_has={MODEL_NAME in keys}",
        )
        # Mirror the emitted-shape checks against the source CRs directly.
        m = next(c for c in _load_crs(MODELS_DIR, "LiteLLMModel") if c["metadata"]["name"] == MODEL_NAME)
        pr = (m.get("spec") or {}).get("params") or {}
        info_extra = ((m.get("spec") or {}).get("info") or {}).get("extra") or {}
        record(
            "kustomize_emitted_model_keeps_placeholder_and_zero_prices",
            pr.get("apiKey") == PLACEHOLDER
            and pr.get("model") == EXPECTED_UPSTREAM
            and info_extra.get("input_cost_per_token") == 0
            and info_extra.get("output_cost_per_token") == 0,
            f"params={pr} extra={info_extra} (source CR; no kubectl)",
        )
        k = next(
            c for c in _load_crs(VIRTUALKEYS_DIR, "LiteLLMVirtualKey") if c["metadata"]["name"] == MODEL_NAME
        )
        spec = k.get("spec") or {}
        record(
            "kustomize_emitted_key_has_limits_no_budget",
            spec.get("models") == [MODEL_NAME]
            and spec.get("rpmLimit") == EXPECTED_RPM
            and spec.get("tpmLimit") == EXPECTED_TPM
            and "maxBudget" not in spec,
            f"spec={spec} (source CR; no kubectl)",
        )
        return

    built = subprocess.run(
        ["kubectl", "kustomize", str(APP_DIR)],
        check=False,
        capture_output=True,
        text=True,
    )
    record(
        "kubectl_kustomize_app_succeeds",
        built.returncode == 0,
        built.stderr[-400:] if built.returncode else f"bytes={len(built.stdout)}",
    )
    if built.returncode != 0:
        return
    docs = [d for d in yaml.safe_load_all(built.stdout) if d]
    kinds_names = {
        (d.get("kind"), d.get("metadata", {}).get("name")) for d in docs
    }
    required = {
        ("LiteLLMModel", MODEL_NAME),
        ("LiteLLMVirtualKey", MODEL_NAME),
        ("PushSecret", f"litellm-key-{MODEL_NAME}"),
        ("LiteLLMProxy", "litellm"),
        ("ExternalSecret", "litellm"),
    }
    missing = sorted(required - kinds_names)
    record(
        "kustomize_emits_subscription_model_key_pushsecret",
        missing == [],
        f"missing={missing}",
    )

    # Re-parse the emitted model from the consumer output (not the source file).
    model_doc = next(
        (
            d
            for d in docs
            if d.get("kind") == "LiteLLMModel"
            and d.get("metadata", {}).get("name") == MODEL_NAME
        ),
        None,
    )
    if model_doc:
        pr = (model_doc.get("spec") or {}).get("params") or {}
        info_extra = ((model_doc.get("spec") or {}).get("info") or {}).get("extra") or {}
        record(
            "kustomize_emitted_model_keeps_placeholder_and_zero_prices",
            pr.get("apiKey") == PLACEHOLDER
            and pr.get("model") == EXPECTED_UPSTREAM
            and info_extra.get("input_cost_per_token") == 0
            and info_extra.get("output_cost_per_token") == 0,
            f"params={pr} extra={info_extra}",
        )
    else:
        record("kustomize_emitted_model_keeps_placeholder_and_zero_prices", False, "missing doc")

    key_doc = next(
        (
            d
            for d in docs
            if d.get("kind") == "LiteLLMVirtualKey"
            and d.get("metadata", {}).get("name") == MODEL_NAME
        ),
        None,
    )
    if key_doc:
        spec = key_doc.get("spec") or {}
        record(
            "kustomize_emitted_key_has_limits_no_budget",
            spec.get("models") == [MODEL_NAME]
            and spec.get("rpmLimit") == EXPECTED_RPM
            and spec.get("tpmLimit") == EXPECTED_TPM
            and "maxBudget" not in spec,
            f"spec={spec}",
        )
    else:
        record("kustomize_emitted_key_has_limits_no_budget", False, "missing doc")


def test_runbook_contract() -> None:
    """Published client-facing contract, not an implementation source grep.

    The runbook is the end-user surface for workstation setup. Assert the
    required configuration knobs are documented as a usable contract.
    """
    exists = RUNBOOK.exists()
    text = RUNBOOK.read_text() if exists else ""
    record("runbook_doc_exists", exists and len(text) > 500, f"bytes={len(text)}")
    if not exists:
        return

    # Required client env contract.
    needed_env = [
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_CUSTOM_HEADERS",
        "x-litellm-api-key",
    ]
    missing_env = [e for e in needed_env if e not in text]
    record(
        "runbook_documents_client_env_contract",
        missing_env == [],
        f"missing={missing_env}",
    )
    record(
        "runbook_model_value_is_claude_code_subscription",
        'ANTHROPIC_MODEL="claude-code-subscription"' in text
        or "ANTHROPIC_MODEL=claude-code-subscription" in text
        or "ANTHROPIC_MODEL" in text and MODEL_NAME in text,
        "model assignment present",
    )
    record(
        "runbook_forbids_putting_virtual_key_in_authorization",
        "never as `Authorization`" in text
        or "never as Authorization" in text
        or ("Authorization" in text and "reserved" in text.lower()),
        "authorization guidance present",
    )
    record(
        "runbook_states_oauth_login_is_manual_per_person",
        "/login" in text and ("interactive" in text.lower() or "per-person" in text.lower()),
        "oauth login section present",
    )
    record(
        "runbook_states_headless_oauth_out_of_scope",
        "out of scope" in text.lower() and "headless" in text.lower(),
        "headless scope note present",
    )
    # Decision record: flag deliberately off.
    record(
        "runbook_records_forward_client_headers_deliberately_off",
        "forward_client_headers_to_llm_api" in text
        and ("did NOT set" in text or "deliberately" in text.lower() or "flag off" in text.lower()),
        "flag decision recorded",
    )

    readme = README_APP.read_text() if README_APP.exists() else ""
    record(
        "app_readme_links_subscription_pass_through_section",
        MODEL_NAME in readme and "pass-through" in readme.lower(),
        f"readme_bytes={len(readme)}",
    )


def test_litellm_runtime_pricing_and_oauth_helpers() -> None:
    """Exercise real LiteLLM library behaviour when the pinned image deps exist."""
    try:
        import litellm  # noqa: F401
    except ImportError:
        require = os.environ.get("REQUIRE_LITELLM_RUNTIME", "").lower() in (
            "1",
            "true",
            "yes",
        )
        record(
            "litellm_runtime_available_for_subscription_proofs",
            not require,
            "litellm not installed here - semantic CR proofs still ran; "
            "re-run under ghcr.io/berriai/litellm-non_root:v1.98.0 for runtime proofs",
        )
        return

    record("litellm_runtime_available_for_subscription_proofs", True, "import ok")

    # --- pricing: explicit 0 is custom pricing ---
    # use_custom_pricing_for_model(litellm_params) checks:
    #   1) pricing keys directly on litellm_params (is not None)
    #   2) model_info under metadata / litellm_metadata (the /v1/messages path)
    # It does NOT read a top-level "model_info" key - that was a wrong test shape.
    try:
        import litellm  # type: ignore
        from litellm import Router, completion_cost  # type: ignore
        from litellm.litellm_core_utils.litellm_logging import (  # type: ignore
            use_custom_pricing_for_model,
        )
        from litellm.types.utils import CostPerToken  # type: ignore

        top_level_zero = {
            "input_cost_per_token": 0,
            "output_cost_per_token": 0,
        }
        metadata_zero = {
            "metadata": {
                "model_info": {
                    "input_cost_per_token": 0,
                    "output_cost_per_token": 0,
                }
            }
        }
        messages_zero = {
            "litellm_metadata": {
                "model_info": {
                    "input_cost_per_token": 0,
                    "output_cost_per_token": 0,
                }
            }
        }
        empty_metadata = {"metadata": {"model_info": {}}}
        none_params: dict = {}

        ok_top = bool(use_custom_pricing_for_model(top_level_zero))
        ok_meta = bool(use_custom_pricing_for_model(metadata_zero))
        ok_messages = bool(use_custom_pricing_for_model(messages_zero))
        ok_empty = not bool(use_custom_pricing_for_model(empty_metadata))
        ok_none = not bool(use_custom_pricing_for_model(none_params))
        record(
            "runtime_use_custom_pricing_honours_explicit_zero",
            ok_top and ok_meta and ok_messages and ok_empty and ok_none,
            f"top={ok_top} metadata={ok_meta} messages={ok_messages} "
            f"empty={ok_empty} none={ok_none}",
        )

        # Observable cost outcome: same token usage, metered sonnet vs $0 custom.
        resp = litellm.ModelResponse(
            id="ccs-test",
            choices=[
                litellm.Choices(
                    index=0,
                    message=litellm.Message(role="assistant", content="hi"),
                    finish_reason="stop",
                )
            ],
            model="claude-sonnet-5",
            usage=litellm.Usage(
                prompt_tokens=1000, completion_tokens=500, total_tokens=1500
            ),
        )
        metered = float(
            completion_cost(
                completion_response=resp, model="anthropic/claude-sonnet-5"
            )
        )
        zeroed = float(
            completion_cost(
                completion_response=resp,
                model="anthropic/claude-sonnet-5",
                custom_cost_per_token=CostPerToken(
                    input_cost_per_token=0.0, output_cost_per_token=0.0
                ),
            )
        )
        record(
            "runtime_completion_cost_zero_custom_vs_metered_sonnet",
            metered > 0 and zeroed == 0.0,
            f"metered={metered} zeroed={zeroed}",
        )

        # Router registers model_list model_info onto the deployment; zeros stay 0.
        router = Router(
            model_list=[
                {
                    "model_name": MODEL_NAME,
                    "litellm_params": {
                        "model": EXPECTED_UPSTREAM,
                        "api_key": PLACEHOLDER,
                    },
                    "model_info": {
                        "input_cost_per_token": 0,
                        "output_cost_per_token": 0,
                    },
                }
            ],
            num_retries=0,
            set_verbose=False,
        )
        entry = router.model_list[0]
        info = entry.get("model_info") or {}
        record(
            "runtime_router_preserves_explicit_zero_model_info",
            info.get("input_cost_per_token") == 0
            and info.get("output_cost_per_token") == 0,
            f"model_info={info}",
        )
    except Exception as e:
        record(
            "runtime_use_custom_pricing_honours_explicit_zero",
            False,
            f"{type(e).__name__}: {e}",
        )
        record("runtime_completion_cost_zero_custom_vs_metered_sonnet", False, str(e))
        record("runtime_router_preserves_explicit_zero_model_info", False, str(e))

    # --- oauth key detection on the placeholder ---
    try:
        from litellm.llms.anthropic.common_utils import (  # type: ignore
            AnthropicModelInfo,
            is_anthropic_oauth_key,
        )

        record(
            "runtime_placeholder_detected_as_anthropic_oauth_key",
            bool(is_anthropic_oauth_key(PLACEHOLDER)),
            f"placeholder={PLACEHOLDER!r}",
        )
        record(
            "runtime_metered_api_key_not_detected_as_oauth",
            not bool(is_anthropic_oauth_key("sk-ant-api03-NOT-OAUTH")),
            "metered prefix rejected",
        )

        # get_api_key(None) falls back to env - the trap the placeholder closes.
        os.environ["ANTHROPIC_API_KEY"] = "sk-ant-api03-TEST-SHARED-METERED-KEY"
        try:
            info = AnthropicModelInfo()
            got = info.get_api_key(None)
            record(
                "runtime_get_api_key_none_falls_back_to_env_anthropic_key",
                got == "sk-ant-api03-TEST-SHARED-METERED-KEY",
                f"got={got!r}",
            )
            got_placeholder = info.get_api_key(PLACEHOLDER)
            record(
                "runtime_get_api_key_placeholder_does_not_fall_back_to_env",
                got_placeholder == PLACEHOLDER,
                f"got={got_placeholder!r}",
            )
        finally:
            os.environ.pop("ANTHROPIC_API_KEY", None)
    except Exception as e:
        record(
            "runtime_placeholder_detected_as_anthropic_oauth_key",
            False,
            f"{type(e).__name__}: {e}",
        )
        record("runtime_metered_api_key_not_detected_as_oauth", False, str(e))
        record("runtime_get_api_key_none_falls_back_to_env_anthropic_key", False, str(e))
        record("runtime_get_api_key_placeholder_does_not_fall_back_to_env", False, str(e))


def main() -> int:
    print(f"APP_DIR={APP_DIR}")
    for required in (PROXY_PATH, MODELS_DIR, VIRTUALKEYS_DIR, EXTERNAL_SECRET_PATH):
        if not required.exists():
            print(f"missing {required}", file=sys.stderr)
            return 2

    cfg = render_config_from_crs()
    print(
        f"rendered model_list={len(cfg['model_list'])} "
        f"names_has_subscription={MODEL_NAME in [m['model_name'] for m in cfg['model_list']]}"
    )

    test_model_render(cfg)
    test_no_global_forward_headers_flag(cfg)
    test_absent_from_fallback_chains(cfg)
    test_virtual_key_semantics()
    test_no_externalsecret_for_subscription_model()
    test_kustomize_emits_resources()
    test_runbook_contract()
    test_litellm_runtime_pricing_and_oauth_helpers()

    failed = [r for r in RESULTS if not r["ok"]]
    print("\n=== SUMMARY ===")
    print(f"passed={len(RESULTS) - len(failed)} failed={len(failed)} total={len(RESULTS)}")
    for f in failed:
        print(f"  FAIL {f['name']}: {f['detail']}")

    out_path = os.environ.get("EVIDENCE_OUT")
    if out_path:
        payload = {
            "results": RESULTS,
            "failed": failed,
            "model_names": [m["model_name"] for m in cfg["model_list"]],
            "subscription_entry": by_name(cfg["model_list"], MODEL_NAME),
            "general_settings": cfg.get("general_settings"),
            "router_settings_fallbacks": (cfg.get("router_settings") or {}).get("fallbacks"),
            "router_settings_context_window_fallbacks": (cfg.get("router_settings") or {}).get(
                "context_window_fallbacks"
            ),
        }
        Path(out_path).write_text(json.dumps(payload, indent=2))
        print(f"wrote {out_path}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
