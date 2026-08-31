#!/usr/bin/env python3
"""Behavioral validation of the Claude Code Max/Pro subscription pass-through.

Renders the litellm-operator CR surface the way the operator does in file mode
(same approach as litellm-fallback-chain-test.py / litellm-auto-router-test.py),
then asserts the captain-approved semantics for
`claude-code-subscription` (2026-08-27):

  1. Model CRs (Sonnet + Opus) are rendered into model_list with the matching
     anthropic/claude-*-5 upstream, a non-secret sk-ant-oat placeholder
     api_key (NOT os.environ/ANTHROPIC_*), and explicit $0 custom prices on
     all seven fields (input, output, and the five prompt-cache ones).
  2. Proxy general/litellm settings do NOT enable the global
     forward_client_headers_to_llm_api flag.
  3. The model is absent from every config-declared fallback chain.
  4. Its dedicated virtual key is scoped only to the subscription models
     (Sonnet + Opus pass-through CRs), and carries no rpmLimit/tpmLimit
     (removed 2026-08-31) and deliberately NO maxBudget - no local ceiling
     of any kind - and has a matching PushSecret.
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
# Opus half, added 2026-08-30 after a `claude` subagent asked for Opus by name
# and the subscription key correctly refused it (403). It is a SECOND
# credential-less CR rather than a per-key alias of the metered
# `claude-opus-5`, because LiteLLM v1.98.0 applies a key's `aliases` in
# litellm_pre_call_utils only AFTER can_key_call_model has already 403'd in the
# auth dependency - measured, see the CR header and runbook section 7.
OPUS_MODEL_NAME = "claude-code-subscription-opus"
# Every model this key may name. The invariant is not "exactly one model" but
# "only models for which the proxy holds NO credential", which the
# allowlist_holds_only_credential_less_models check below enforces directly.
SUBSCRIPTION_MODELS = [MODEL_NAME, OPUS_MODEL_NAME]
# Metered Anthropic routes that must never appear on this key's allow-list.
METERED_MODELS = ["claude-sonnet-5", "claude-opus-5", "claude-opus-4-8", "auto"]
PLACEHOLDER_PREFIX = "sk-ant-oat"
PLACEHOLDER = "sk-ant-oat-PLACEHOLDER-CLIENT-SENDS-ITS-OWN-TOKEN"
EXPECTED_UPSTREAM = "anthropic/claude-sonnet-5"
EXPECTED_UPSTREAM_OPUS = "anthropic/claude-opus-5"
# Zeroing input/output alone does NOT make recorded spend $0. Prompt-cache
# pricing lives in its own fields and `_resolve_builtin_model_cost_entry`
# (litellm utils.py) copies the built-in map's `_CACHE_PRICING_FIELDS` onto a
# custom key, so they keep billing at the metered rate. Claude Code caches on
# nearly every turn, so that gap alone put ~$54 of fictional recorded spend on
# the subscription key (measured 2026-08-31: output_cost exactly $0.00, while
# cache_read + cache_creation were the entire total). Verified end-to-end with
# a throwaway probe: recorded spend went 6e-07 -> exactly 0 on both
# /v1/chat/completions and /v1/messages once these five were declared. Dropping
# any one of them silently restarts the accrual, which is why they are pinned.
REQUIRED_ZERO_PRICE_FIELDS = (
    "input_cost_per_token",
    "output_cost_per_token",
    "cache_read_input_token_cost",
    "cache_read_input_token_cost_above_200k_tokens",
    "cache_creation_input_token_cost",
    "cache_creation_input_token_cost_above_1hr",
    "cache_creation_input_token_cost_above_200k_tokens",
)


def _zero_price_offenders(info: dict) -> list[str]:
    """Names of required price fields that are missing or not numeric zero.

    Numeric-zero, not falsey: a missing key falls back to the built-in metered
    map, and None would too, so both must fail.
    """
    bad = []
    for field in REQUIRED_ZERO_PRICE_FIELDS:
        v = info.get(field)
        if v is None or type(v) not in (int, float) or v != 0:
            bad.append(f"{field}={v!r}")
    return bad
# History: 10/250000 -> 300/7500000 on 2026-08-30 after measured proxy 429s
# (~556 request-limit + ~171 token-limit in 24h) -> 3000/750000000 raised live
# in the LiteLLM UI the same day -> REMOVED entirely on 2026-08-31 (captain
# decision: measured headroom was 43x on requests / 267x on tokens, so the
# limits were no longer a meaningful runaway-agent guardrail). This key now
# has no rpmLimit/tpmLimit at all; Anthropic's own subscription rate limit is
# the only ceiling left, same as it always was in practice.

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
    # Cache pricing is where Claude Code's cost actually lives - see
    # REQUIRED_ZERO_PRICE_FIELDS.
    record(
        "model_info_zeroes_cache_prices_not_just_input_output",
        _zero_price_offenders(info) == [],
        f"offenders={_zero_price_offenders(info)}",
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

    # --- Opus sibling: identical shape, one line different -----------------
    om = by_name(cfg["model_list"], OPUS_MODEL_NAME)
    record(
        "opus_subscription_model_present_in_operator_render",
        om is not None,
        f"has={om is not None}",
    )
    if om is None:
        return
    olp = om.get("litellm_params") or {}
    record(
        "opus_upstream_model_id_is_anthropic_claude_opus_5_dash_form",
        olp.get("model") == EXPECTED_UPSTREAM_OPUS,
        f"model={olp.get('model')!r}",
    )
    # The whole money-safety argument in one assertion: no env credential, not
    # absent (absent falls back to ANTHROPIC_API_KEY), and oat-prefixed so a
    # tokenless caller gets Anthropic's 401 instead of a silent metered charge.
    oak = olp.get("api_key")
    record(
        "opus_api_key_is_present_non_env_placeholder",
        isinstance(oak, str)
        and oak == PLACEHOLDER
        and not str(oak).startswith("os.environ/"),
        f"api_key={oak!r}",
    )
    record(
        "opus_api_key_carries_sk_ant_oat_prefix_for_oauth_branch",
        isinstance(oak, str) and oak.startswith(PLACEHOLDER_PREFIX),
        f"prefix_ok={isinstance(oak, str) and oak.startswith(PLACEHOLDER_PREFIX)}",
    )
    # Sibling metered Opus must still use the env credential - proves this is
    # an addition, not a rewrite of the metered route.
    metered_opus = by_name(cfg["model_list"], "claude-opus-5")
    record(
        "sibling_claude_opus_5_still_uses_shared_env_key",
        bool(metered_opus)
        and (metered_opus.get("litellm_params") or {}).get("api_key")
        == "os.environ/ANTHROPIC_API_KEY",
        f"opus_api_key={(metered_opus or {}).get('litellm_params', {}).get('api_key')!r}",
    )
    oinfo = om.get("model_info") or {}
    record(
        "opus_model_info_zeroes_cache_prices_not_just_input_output",
        _zero_price_offenders(oinfo) == [],
        f"offenders={_zero_price_offenders(oinfo)}",
    )
    record(
        "opus_model_info_declares_explicit_zero_token_prices",
        oinfo.get("input_cost_per_token") == 0
        and oinfo.get("output_cost_per_token") == 0
        and type(oinfo.get("input_cost_per_token")) in (int, float)
        and type(oinfo.get("output_cost_per_token")) in (int, float),
        f"model_info={oinfo!r}",
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

    # Both pass-through models must stay out of every chain, in both roles.
    # As a PRIMARY, a fallback would send a tokenless caller's failed request
    # on to a metered model - a config-declared fallback bypasses the calling
    # key's allow-list (see docs/ai-system/litellm/fallbacks.md). As a TARGET,
    # it would route some other consumer's traffic at a model that requires a
    # client-supplied OAuth token nobody else sends.
    as_primary = [s for s in SUBSCRIPTION_MODELS if s in avail or s in ctx]
    as_target = [
        s
        for s in SUBSCRIPTION_MODELS
        if any(s in targets for targets in avail.values())
        or any(s in targets for targets in ctx.values())
    ]
    record(
        "claude_code_subscription_not_a_fallback_primary",
        as_primary == [],
        f"offenders={as_primary} avail={sorted(avail)} ctx={sorted(ctx)}",
    )
    record(
        "claude_code_subscription_not_a_fallback_target",
        as_target == [],
        f"offenders={as_target} avail_targets={avail} ctx_targets={ctx}",
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
    allowed = spec.get("models") or []
    record(
        "virtualkey_scoped_only_to_subscription_models",
        sorted(allowed) == sorted(SUBSCRIPTION_MODELS),
        f"models={allowed!r} expected={sorted(SUBSCRIPTION_MODELS)!r}",
    )
    # THE MONEY-SAFETY INVARIANT, and the reason the check above is a set and
    # not a length. Adding a model to this key is only safe when the proxy
    # holds NO credential for it: every allow-listed name must resolve to a
    # model CR whose params.apiKey is the non-secret placeholder, never
    # `os.environ/ANTHROPIC_API_KEY` and never absent (an absent key is NOT
    # credential-less - AnthropicModelInfo.get_api_key falls back to the env
    # var the pod holds). This is what actually stops subscription traffic
    # billing the household's metered account.
    model_crs = {
        c["metadata"]["name"]: ((c.get("spec") or {}).get("params") or {})
        for c in _load_crs(MODELS_DIR, "LiteLLMModel")
    }
    not_credential_less = [
        name
        for name in allowed
        if model_crs.get(name, {}).get("apiKey") != PLACEHOLDER
    ]
    record(
        "virtualkey_allowlist_holds_only_credential_less_models",
        not_credential_less == [],
        f"offenders={not_credential_less} "
        f"(each allow-listed model must carry apiKey={PLACEHOLDER!r})",
    )
    metered_on_key = [m for m in allowed if m in METERED_MODELS]
    record(
        "virtualkey_allowlist_names_no_metered_route",
        metered_on_key == [],
        f"metered_on_key={metered_on_key} checked={METERED_MODELS}",
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
        "virtualkey_has_no_rate_limits",
        "rpmLimit" not in spec and "tpmLimit" not in spec,
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
    # No other key should hold either subscription model (entitlement is
    # dedicated). A second holder would break the per-consumer attribution the
    # whole feature exists for, and would hand a pass-through model to a key
    # whose caller may send no OAuth token at all.
    other_holders = sorted(
        {
            f"{name}:{sub}"
            for name, cr in keys.items()
            if name != MODEL_NAME
            for sub in SUBSCRIPTION_MODELS
            if sub in (cr["spec"].get("models") or [])
        }
    )
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
        # Both subscription CRs must be listed in models/kustomization.yaml, or
        # the operator never renders them and the allow-list names a model that
        # does not exist.
        missing = [
            s
            for s in SUBSCRIPTION_MODELS
            if s not in models or not any(s in r for r in model_kust)
        ]
        record(
            "kustomize_emits_subscription_model_key_pushsecret",
            missing == []
            and MODEL_NAME in keys
            and any(MODEL_NAME in r for r in key_kust),
            f"missing_models={missing} keys_has={MODEL_NAME in keys}",
        )
        # Mirror the emitted-shape checks against the source CRs directly.
        by_cr = {c["metadata"]["name"]: c for c in _load_crs(MODELS_DIR, "LiteLLMModel")}
        expected_upstream = {
            MODEL_NAME: EXPECTED_UPSTREAM,
            OPUS_MODEL_NAME: EXPECTED_UPSTREAM_OPUS,
        }
        bad = []
        for name, want in expected_upstream.items():
            cr = by_cr.get(name)
            pr = ((cr or {}).get("spec") or {}).get("params") or {}
            extra = (((cr or {}).get("spec") or {}).get("info") or {}).get("extra") or {}
            price_bad = _zero_price_offenders(extra)
            if (
                pr.get("apiKey") != PLACEHOLDER
                or pr.get("model") != want
                or price_bad
            ):
                bad.append(
                    {
                        name: {
                            "params": pr,
                            "extra": extra,
                            "want": want,
                            "price_offenders": price_bad,
                        }
                    }
                )
        record(
            "kustomize_emitted_model_keeps_placeholder_and_zero_prices",
            bad == [],
            f"offenders={bad} (source CRs; no kubectl)",
        )
        k = next(
            c for c in _load_crs(VIRTUALKEYS_DIR, "LiteLLMVirtualKey") if c["metadata"]["name"] == MODEL_NAME
        )
        spec = k.get("spec") or {}
        record(
            "kustomize_emitted_key_has_no_ceiling",
            sorted(spec.get("models") or []) == sorted(SUBSCRIPTION_MODELS)
            and "rpmLimit" not in spec
            and "tpmLimit" not in spec
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

    # Re-parse the emitted models from the consumer output (not the source
    # files). Both subscription CRs must survive the kustomize build with the
    # placeholder key and the zero prices intact.
    expected_upstream = {
        MODEL_NAME: EXPECTED_UPSTREAM,
        OPUS_MODEL_NAME: EXPECTED_UPSTREAM_OPUS,
    }
    emitted = {
        d.get("metadata", {}).get("name"): d
        for d in docs
        if d.get("kind") == "LiteLLMModel"
        and d.get("metadata", {}).get("name") in expected_upstream
    }
    bad = []
    for name, want in expected_upstream.items():
        doc = emitted.get(name)
        if doc is None:
            bad.append({name: "missing doc"})
            continue
        pr = (doc.get("spec") or {}).get("params") or {}
        info_extra = ((doc.get("spec") or {}).get("info") or {}).get("extra") or {}
        price_bad = _zero_price_offenders(info_extra)
        if (
            pr.get("apiKey") != PLACEHOLDER
            or pr.get("model") != want
            or price_bad
        ):
            bad.append(
                {
                    name: {
                        "params": pr,
                        "extra": info_extra,
                        "want": want,
                        "price_offenders": price_bad,
                    }
                }
            )
    record(
        "kustomize_emitted_model_keeps_placeholder_and_zero_prices",
        bad == [],
        f"offenders={bad}",
    )

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
            "kustomize_emitted_key_has_no_ceiling",
            sorted(spec.get("models") or []) == sorted(SUBSCRIPTION_MODELS)
            and "rpmLimit" not in spec
            and "tpmLimit" not in spec
            and "maxBudget" not in spec,
            f"spec={spec}",
        )
    else:
        record("kustomize_emitted_key_has_no_ceiling", False, "missing doc")


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

    # Required client env contract. The two ANTHROPIC_DEFAULT_*_MODEL vars are
    # load-bearing since 2026-08-30: ANTHROPIC_MODEL alone only covers the main
    # loop, so a subagent that asks for "opus" or "sonnet" by name resolves the
    # alias through these vars instead and, left at their defaults, requests the
    # METERED `claude-opus-5`/`claude-sonnet-5` and is refused with a 403. That
    # is the client half of the fix - the collision cannot be closed on the
    # proxy, because per-key `aliases` are applied after the allow-list check.
    needed_env = [
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
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
    # The Opus alias must be pointed at the pass-through CR, never left to
    # resolve to the metered claude-opus-5.
    record(
        "runbook_maps_opus_alias_to_subscription_model",
        OPUS_MODEL_NAME in text
        and "ANTHROPIC_DEFAULT_OPUS_MODEL" in text
        and any(
            f"ANTHROPIC_DEFAULT_OPUS_MODEL={q}{OPUS_MODEL_NAME}{q}" in text
            for q in ('"', "'", "")
        ),
        "opus alias assignment present",
    )
    record(
        "runbook_maps_sonnet_alias_to_subscription_model",
        "ANTHROPIC_DEFAULT_SONNET_MODEL" in text
        and any(
            f"ANTHROPIC_DEFAULT_SONNET_MODEL={q}{MODEL_NAME}{q}" in text
            for q in ('"', "'", "")
        ),
        "sonnet alias assignment present",
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

        # --- cache pricing: the real $54 defect path -----------------------------
        # Router._inherit_builtin_cache_pricing copies the backend model's
        # _CACHE_PRICING_FIELDS onto a custom-priced deployment whenever those
        # fields are missing/None. IO-only zeros therefore still bill cache at
        # the metered rate under the deployment's model_info.id (the key the
        # proxy cost path looks up). Declaring all five cache fields as 0 is
        # what stops the inheritance. Reproduce with Claude-Code-shaped cache
        # usage (heavy cache_read + cache_creation, modest completion).
        def _router_cost_for_model_info(
            model_name: str, upstream: str, model_info: dict
        ) -> tuple[float, dict]:
            r = Router(
                model_list=[
                    {
                        "model_name": model_name,
                        "litellm_params": {
                            "model": upstream,
                            "api_key": PLACEHOLDER,
                        },
                        "model_info": dict(model_info),
                    }
                ],
                num_retries=0,
                set_verbose=False,
            )
            dep = r.model_list[0]
            mid = (dep.get("model_info") or {}).get("id")
            registered = {
                f: (litellm.model_cost.get(mid) or {}).get(f)
                for f in REQUIRED_ZERO_PRICE_FIELDS
            }
            cache_usage = litellm.Usage(
                prompt_tokens=1000,
                completion_tokens=100,
                total_tokens=1100,
                cache_read_input_tokens=50000,
                cache_creation_input_tokens=20000,
            )
            resp = litellm.ModelResponse(
                id="ccs-cache-probe",
                choices=[
                    litellm.Choices(
                        index=0,
                        message=litellm.Message(role="assistant", content="hi"),
                        finish_reason="stop",
                    )
                ],
                model=model_name,
                usage=cache_usage,
            )
            cost = float(
                completion_cost(
                    completion_response=resp,
                    model=model_name,
                    custom_pricing=True,
                    router_model_id=mid,
                    custom_llm_provider="anthropic",
                )
            )
            return cost, registered

        io_only = {
            "input_cost_per_token": 0,
            "output_cost_per_token": 0,
        }
        full_zero = {f: 0 for f in REQUIRED_ZERO_PRICE_FIELDS}

        defect_cost, defect_reg = _router_cost_for_model_info(
            "ccs-probe-io-only", EXPECTED_UPSTREAM, io_only
        )
        fixed_cost, fixed_reg = _router_cost_for_model_info(
            "ccs-probe-full-zero", EXPECTED_UPSTREAM, full_zero
        )
        # Actual CR extras must behave like full_zero, not like io_only.
        cr_sonnet = by_name(render_config_from_crs()["model_list"], MODEL_NAME) or {}
        cr_opus = by_name(render_config_from_crs()["model_list"], OPUS_MODEL_NAME) or {}
        sonnet_cost, sonnet_reg = _router_cost_for_model_info(
            MODEL_NAME,
            EXPECTED_UPSTREAM,
            dict((cr_sonnet.get("model_info") or {})),
        )
        opus_cost, opus_reg = _router_cost_for_model_info(
            OPUS_MODEL_NAME,
            EXPECTED_UPSTREAM_OPUS,
            dict((cr_opus.get("model_info") or {})),
        )

        record(
            "runtime_io_only_zeros_still_bill_inherited_cache_prices",
            defect_cost > 0
            and any(
                defect_reg.get(f) not in (0, 0.0, None)
                for f in (
                    "cache_read_input_token_cost",
                    "cache_creation_input_token_cost",
                )
            ),
            f"cost={defect_cost} registered={defect_reg}",
        )
        record(
            "runtime_full_zero_cache_fields_record_exactly_zero_spend",
            fixed_cost == 0.0
            and all(fixed_reg.get(f) in (0, 0.0) for f in REQUIRED_ZERO_PRICE_FIELDS),
            f"cost={fixed_cost} registered={fixed_reg}",
        )
        record(
            "runtime_subscription_cr_model_info_records_zero_spend",
            sonnet_cost == 0.0 and opus_cost == 0.0,
            f"sonnet_cost={sonnet_cost} opus_cost={opus_cost} "
            f"sonnet_reg={sonnet_reg} opus_reg={opus_reg}",
        )
        # Dropping any one required cache field must re-open non-zero spend
        # (the pin is load-bearing, not decorative).
        drop_costs = {}
        for field in REQUIRED_ZERO_PRICE_FIELDS[2:]:  # the five cache fields
            partial = dict(full_zero)
            del partial[field]
            drop_cost, _ = _router_cost_for_model_info(
                f"ccs-probe-drop-{field}", EXPECTED_UPSTREAM, partial
            )
            drop_costs[field] = drop_cost
        # At least the fields the builtin sonnet map actually prices must
        # re-open spend when dropped (above_* tiers may be absent upstream).
        reopened = [f for f, c in drop_costs.items() if c and c > 0]
        record(
            "runtime_dropping_a_cache_zero_reopens_nonzero_spend",
            "cache_read_input_token_cost" in reopened
            or "cache_creation_input_token_cost" in reopened,
            f"drop_costs={drop_costs} reopened={reopened}",
        )
    except Exception as e:
        record(
            "runtime_use_custom_pricing_honours_explicit_zero",
            False,
            f"{type(e).__name__}: {e}",
        )
        record("runtime_completion_cost_zero_custom_vs_metered_sonnet", False, str(e))
        record("runtime_router_preserves_explicit_zero_model_info", False, str(e))
        record("runtime_io_only_zeros_still_bill_inherited_cache_prices", False, str(e))
        record("runtime_full_zero_cache_fields_record_exactly_zero_spend", False, str(e))
        record("runtime_subscription_cr_model_info_records_zero_spend", False, str(e))
        record("runtime_dropping_a_cache_zero_reopens_nonzero_spend", False, str(e))

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
