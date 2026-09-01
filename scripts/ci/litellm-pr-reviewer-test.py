#!/usr/bin/env python3
"""Behavioral contract tests for the AI PR reviewer (misospace/pr-reviewer-action).

Parses the GitHub Actions workflow and the LiteLLM operator CR surface into
semantic models, then asserts the captain decisions that make the reviewer
advisory-only, free (local model only), fork-safe, and parse-reliable.

This is not a source-grep test: every check operates on the parsed workflow
graph (triggers, permissions inheritance, job condition, step inputs) or on
the typed LiteLLM / External Secrets CRs that the operator and ESO consume.
Where feasible the real consumer artifact is also exercised:

  - action.yml at the pinned commit SHA is fetched and every `with:` key the
    workflow passes is validated as a declared input (unknown inputs are
    silently ignored by Actions, so a typo would ship as a no-op).
  - kustomize build of the litellm app tree confirms the new model + virtual
    key are actually in the rendered inventory, not orphan files.
  - router_settings fallback maps are parsed the same way the proxy does, so
    pr-review-local cannot silently gain a cloud escalation path.

Live cluster proof (OpenAI chat completion through the virtual key from a
runner pod) is intentionally outside this unit: it needs the in-cluster ARC
runner and a minted key. Evidence, the proven credential-absent skip path,
and what is still unproven (model-backed review) are owned by
docs/ai-system/litellm/pr-reviewer.md §7.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
WF_PATH = REPO / ".github" / "workflows" / "ai-pr-review.yaml"
STANDARDS_PATH = REPO / ".github" / "ai-review-rules.md"
DOC_PATH = REPO / "docs" / "ai-system" / "litellm" / "pr-reviewer.md"
APP_DIR = REPO / "kubernetes" / "apps" / "base" / "ai" / "litellm" / "app"
MODEL_PATH = APP_DIR / "models" / "pr-review-local.yaml"
VK_PATH = APP_DIR / "virtualkeys" / "ai-pr-review.yaml"
MODELS_KUST = APP_DIR / "models" / "kustomization.yaml"
VK_KUST = APP_DIR / "virtualkeys" / "kustomization.yaml"
PROXY_PATH = APP_DIR / "litellmproxy.yaml"

FORK_GUARD = "github.event.pull_request.head.repo.full_name == github.repository"
PEER_CONCURRENCY_GROUP = (
    "${{ github.workflow }}-${{ github.event.number || github.ref }}"
)
PINNED_ACTION_PREFIX = "misospace/pr-reviewer-action@"
PINNED_SHA = "54dfb1aac20e1e410ad8f71dc3681b888500a1ec"
ACTION_YML_URL = (
    "https://raw.githubusercontent.com/misospace/pr-reviewer-action/"
    f"{PINNED_SHA}/action.yml"
)
STANDARDS_HARD_TRUNC_BYTES = 16000
FLOATING_MAJOR = re.compile(r"@v\d+$")

RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def load_yaml(path: Path) -> Any:
    return yaml.safe_load(path.read_text())


def load_all_yaml(path: Path) -> list[dict[str, Any]]:
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    return docs


def on_block(data: dict[str, Any]) -> Any:
    # PyYAML 5.x turns the key `on` into boolean True.
    if "on" in data:
        return data["on"]
    if True in data:
        return data[True]
    raise AssertionError("workflow missing on:")


def steps_of(job: dict[str, Any]) -> list[dict[str, Any]]:
    return list(job.get("steps") or [])


def uses_action(step: dict[str, Any], prefix: str) -> bool:
    uses = step.get("uses") or ""
    return uses.startswith(prefix)


def parse_fallback_map(raw: Any) -> dict[str, list[str]]:
    """LiteLLM router_settings.fallbacks is a list of single-key maps."""
    out: dict[str, list[str]] = {}
    if not raw:
        return out
    if isinstance(raw, dict):
        for k, v in raw.items():
            out[str(k)] = [str(x) for x in (v if isinstance(v, list) else [v])]
        return out
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            for k, v in entry.items():
                out[str(k)] = [str(x) for x in (v if isinstance(v, list) else [v])]
    return out


def fetch_action_yml(url: str, timeout: float = 20.0) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8")
        data = yaml.safe_load(body)
        if not isinstance(data, dict) or "inputs" not in data:
            return None
        return data
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, yaml.YAMLError) as exc:
        record(
            "action_yml_fetch",
            False,
            f"could not fetch pinned action.yml ({exc}); input-key validation skipped",
        )
        return None


def test_workflow_contracts() -> dict[str, Any]:
    data = load_yaml(WF_PATH)
    evidence: dict[str, Any] = {"workflow": str(WF_PATH.relative_to(REPO))}

    record(
        "workflow_file_exists",
        WF_PATH.is_file(),
        f"path={WF_PATH}",
    )
    record(
        "workflow_name_is_ai_pr_review",
        data.get("name") == "AI PR Review",
        f"got={data.get('name')!r}",
    )

    on = on_block(data)
    evidence["on"] = on
    record("trigger_is_mapping", isinstance(on, dict), f"type={type(on).__name__}")
    pr = (on or {}).get("pull_request") if isinstance(on, dict) else None
    record("has_pull_request_trigger", isinstance(pr, dict), f"pr={pr!r}")
    if isinstance(pr, dict):
        types = list(pr.get("types") or [])
        evidence["pr_types"] = types
        required_types = {
            "opened",
            "reopened",
            "synchronize",
            "ready_for_review",
        }
        record(
            "pr_types_cover_required_set",
            required_types.issubset(set(types)),
            f"got={types}",
        )
        branches = pr.get("branches") or []
        record(
            "pr_targets_main",
            "main" in branches,
            f"branches={branches}",
        )
    # Must NOT use pull_request_target
    on_keys = set(on.keys()) if isinstance(on, dict) else set()
    record(
        "no_pull_request_target",
        "pull_request_target" not in on_keys,
        f"on_keys={sorted(on_keys)}",
    )

    conc = data.get("concurrency") or {}
    evidence["concurrency"] = conc
    record(
        "concurrency_group_matches_peer_pattern",
        conc.get("group") == PEER_CONCURRENCY_GROUP,
        f"got={conc.get('group')!r}",
    )
    record(
        "concurrency_cancels_in_progress",
        conc.get("cancel-in-progress") is True,
        f"got={conc.get('cancel-in-progress')!r}",
    )

    top_perm = data.get("permissions")
    evidence["top_permissions"] = top_perm
    record(
        "top_permissions_contents_read_only",
        top_perm == {"contents": "read"},
        f"got={top_perm}",
    )

    jobs = data.get("jobs") or {}
    record("has_review_job", "review" in jobs, f"jobs={sorted(jobs)}")
    job = jobs.get("review") or {}
    evidence["job_name"] = job.get("name")
    record(
        "job_name_is_ai_pr_review",
        job.get("name") == "AI PR Review",
        f"got={job.get('name')!r}",
    )
    record(
        "runs_on_in_cluster_arc_scale_set",
        job.get("runs-on") == "gha-runner-scale-set-aviator-coding-home-ops",
        f"got={job.get('runs-on')!r}",
    )

    job_if = str(job.get("if") or "")
    evidence["if"] = job_if
    # Collapse whitespace so multi-line folded if: still matches.
    collapsed = " ".join(job_if.split())
    record(
        "fork_guard_present",
        FORK_GUARD in collapsed,
        f"if={job_if!r}",
    )
    record(
        "draft_skip_present",
        "github.event.pull_request.draft" in collapsed
        and ("!" in collapsed or "false" in collapsed.lower()),
        f"if={job_if!r}",
    )
    # No author/label/path filters that would drop Renovate.
    record(
        "no_author_login_filter",
        "github.actor" not in collapsed and "author_association" not in collapsed,
        f"if={job_if!r}",
    )
    record(
        "no_renovate_exclusion",
        "renovate" not in collapsed.lower(),
        f"if={job_if!r}",
    )

    job_perm = job.get("permissions") or {}
    evidence["job_permissions"] = job_perm
    record(
        "job_permissions_exactly_contents_read_and_pr_write",
        job_perm == {"contents": "read", "pull-requests": "write"},
        f"got={job_perm}",
    )
    # Nothing wider: no contents:write, issues, checks, id-token, etc.
    record(
        "job_permissions_no_extra_scopes",
        set(job_perm) <= {"contents", "pull-requests"},
        f"keys={sorted(job_perm)}",
    )

    steps = steps_of(job)
    evidence["step_names"] = [s.get("name") or s.get("uses") for s in steps]

    checkout = [s for s in steps if uses_action(s, "actions/checkout@")]
    record("has_checkout_step", len(checkout) == 1, f"n={len(checkout)}")
    if checkout:
        ck = checkout[0].get("with") or {}
        record(
            "checkout_full_history",
            str(ck.get("fetch-depth")) == "0",
            f"fetch-depth={ck.get('fetch-depth')!r}",
        )
        record(
            "checkout_no_persist_credentials",
            ck.get("persist-credentials") in (False, "false"),
            f"persist-credentials={ck.get('persist-credentials')!r}",
        )
        record(
            "checkout_pins_commit_sha",
            bool(re.search(r"actions/checkout@[0-9a-f]{40}", checkout[0].get("uses") or "")),
            f"uses={checkout[0].get('uses')!r}",
        )

    cred = next((s for s in steps if s.get("name") == "Resolve Model Credential"), None)
    record("has_resolve_credential_step", cred is not None)
    if cred is not None:
        run = str(cred.get("run") or "")
        record(
            "credential_missing_skips_green",
            'mode=skip' in run and "LITELLM_PR_REVIEW_KEY" in run,
            "skip path must publish mode=skip when secret unset",
        )
        record(
            "credential_present_sets_review_mode",
            'mode=review' in run,
            "present path must publish mode=review",
        )

    review_steps = [
        s
        for s in steps
        if uses_action(s, PINNED_ACTION_PREFIX)
    ]
    record(
        "invokes_pr_reviewer_action_once",
        len(review_steps) == 1,
        f"n={len(review_steps)} uses={[s.get('uses') for s in review_steps]}",
    )
    if not review_steps:
        return evidence
    step = review_steps[0]
    uses = step.get("uses") or ""
    evidence["uses"] = uses
    record(
        "action_pinned_to_exact_sha",
        uses == f"{PINNED_ACTION_PREFIX}{PINNED_SHA}"
        or uses.startswith(f"{PINNED_ACTION_PREFIX}{PINNED_SHA}"),
        f"uses={uses!r}",
    )
    # Reject floating majors like @v2 even if someone changes the pin form.
    ref = uses.split("@", 1)[-1] if "@" in uses else ""
    record(
        "action_ref_is_not_floating_major",
        not FLOATING_MAJOR.search("@" + ref.split()[0] if ref else ""),
        f"ref={ref!r}",
    )
    # SHA form: 40 hex, optionally with a version comment handled outside uses.
    sha_part = ref.split()[0] if ref else ""
    record(
        "action_ref_is_40_char_commit_sha",
        bool(re.fullmatch(r"[0-9a-f]{40}", sha_part)),
        f"sha_part={sha_part!r}",
    )

    # Gated on credential resolve output.
    step_if = str(step.get("if") or "")
    record(
        "review_step_gated_on_credential_mode",
        "steps.cred.outputs.mode" in step_if and "review" in step_if,
        f"if={step_if!r}",
    )

    with_ = step.get("with") or {}
    evidence["with_keys"] = sorted(with_)
    evidence["with"] = {k: with_[k] for k in sorted(with_)}

    def w(key: str) -> Any:
        return with_.get(key)

    # Endpoint + model
    record(
        "ai_base_url_is_in_cluster_litellm",
        w("ai_base_url") == "http://litellm.ai.svc.cluster.local:4000/v1",
        f"got={w('ai_base_url')!r}",
    )
    record(
        "ai_api_format_openai",
        w("ai_api_format") == "openai",
        f"got={w('ai_api_format')!r}",
    )
    record(
        "ai_model_is_pr_review_local",
        w("ai_model") == "pr-review-local",
        f"got={w('ai_model')!r}",
    )
    record(
        "ai_api_key_from_dedicated_secret",
        str(w("ai_api_key")) == "${{ secrets.LITELLM_PR_REVIEW_KEY }}",
        f"got={w('ai_api_key')!r}",
    )
    record(
        "github_token_is_default_GITHUB_TOKEN",
        str(w("github_token")) == "${{ secrets.GITHUB_TOKEN }}",
        f"got={w('github_token')!r}",
    )

    # Context budget: deliberate undershoot of the 262144 served window.
    record(
        "model_context_tokens_is_65536",
        str(w("model_context_tokens")) == "65536",
        f"got={w('model_context_tokens')!r}",
    )
    record(
        "ai_max_tokens_is_2048",
        str(w("ai_max_tokens")) == "2048",
        f"got={w('ai_max_tokens')!r}",
    )
    record(
        "ai_response_format_json_object_not_schema",
        w("ai_response_format") == "json_object",
        f"got={w('ai_response_format')!r}",
    )

    # Homelab timeout profile
    record(
        "ai_connect_timeout_sec_is_10",
        str(w("ai_connect_timeout_sec")) == "10",
        f"got={w('ai_connect_timeout_sec')!r}",
    )
    record(
        "ai_request_timeout_sec_is_600",
        str(w("ai_request_timeout_sec")) == "600",
        f"got={w('ai_request_timeout_sec')!r}",
    )
    record(
        "ai_primary_retries_is_1",
        str(w("ai_primary_retries")) == "1",
        f"got={w('ai_primary_retries')!r}",
    )
    record(
        "on_model_failure_is_notice",
        w("on_model_failure") == "notice",
        f"got={w('on_model_failure')!r}",
    )

    # Standards file: curated, not AGENTS.md
    record(
        "standards_file_is_curated_ai_review_rules",
        w("standards_file") == ".github/ai-review-rules.md",
        f"got={w('standards_file')!r}",
    )

    # Advisory-only posture (captain decision 2)
    record(
        "publish_mode_is_comment",
        w("publish_mode") == "comment",
        f"got={w('publish_mode')!r}",
    )
    record(
        "publish_review_comment_true",
        str(w("publish_review_comment")).lower() in ("true", "1"),
        f"got={w('publish_review_comment')!r}",
    )
    record(
        "allow_approve_false",
        str(w("allow_approve")).lower() in ("false", "0"),
        f"got={w('allow_approve')!r}",
    )
    record(
        "approve_forks_false",
        str(w("approve_forks")).lower() in ("false", "0"),
        f"got={w('approve_forks')!r}",
    )
    record(
        "fail_on_request_changes_false",
        str(w("fail_on_request_changes")).lower() in ("false", "0"),
        f"got={w('fail_on_request_changes')!r}",
    )
    record(
        "inline_findings_false",
        str(w("inline_findings")).lower() in ("false", "0"),
        f"got={w('inline_findings')!r}",
    )
    # Must never be review_verdict
    record(
        "publish_mode_not_review_verdict",
        w("publish_mode") != "review_verdict",
        f"got={w('publish_mode')!r}",
    )

    # No escalation / outward reach
    record(
        "review_routing_mode_off",
        str(w("review_routing_mode")) == "off",
        f"got={w('review_routing_mode')!r}",
    )
    record(
        "tool_mode_off",
        str(w("tool_mode")) == "off",
        f"got={w('tool_mode')!r}",
    )
    record(
        "no_smart_model_configured",
        not w("ai_smart_model"),
        f"got={w('ai_smart_model')!r}",
    )
    # The pinned action always sets AI_FALLBACK_BASE_URL from ai_base_url when
    # ai_fallback_base_url is blank, then requires AI_FALLBACK_MODEL whenever
    # that URL is non-empty. The only spend-safe value is the same local alias
    # as ai_model; empty fails the review step, and any other model would open
    # a non-local route the virtual key must still 403.
    record(
        "fallback_model_is_same_local_alias",
        w("ai_fallback_model") == "pr-review-local",
        f"got={w('ai_fallback_model')!r}",
    )
    record(
        "evidence_providers_file_empty",
        w("evidence_providers_file") in ("", None),
        f"got={w('evidence_providers_file')!r}",
    )
    record(
        "linear_api_key_empty",
        w("linear_api_key") in ("", None),
        f"got={w('linear_api_key')!r}",
    )
    record(
        "search_url_empty",
        w("search_url") in ("", None),
        f"got={w('search_url')!r}",
    )
    for fork_flag in (
        "tool_enable_for_forks",
        "evidence_enable_for_forks",
        "linear_enable_for_forks",
    ):
        record(
            f"{fork_flag}_false",
            str(w(fork_flag)).lower() in ("false", "0"),
            f"got={w(fork_flag)!r}",
        )

    # Cost containment
    record(
        "skip_if_diff_unchanged_true",
        str(w("skip_if_diff_unchanged")).lower() in ("true", "1"),
        f"got={w('skip_if_diff_unchanged')!r}",
    )
    record(
        "force_review_false",
        str(w("force_review")).lower() in ("false", "0", ""),
        f"got={w('force_review')!r}",
    )

    return evidence


def test_action_inputs_against_pinned_action_yml(with_keys: list[str]) -> None:
    action = fetch_action_yml(ACTION_YML_URL)
    if action is None:
        return
    inputs = action.get("inputs") or {}
    record(
        "action_yml_fetch",
        True,
        f"inputs={len(inputs)} url={ACTION_YML_URL}",
    )
    declared = set(inputs)
    used = set(with_keys)
    unknown = sorted(used - declared)
    record(
        "all_workflow_with_keys_are_declared_action_inputs",
        not unknown,
        f"unknown={unknown} used={len(used)} declared={len(declared)}",
    )


def test_standards_file() -> None:
    record("standards_file_exists", STANDARDS_PATH.is_file(), f"path={STANDARDS_PATH}")
    if not STANDARDS_PATH.is_file():
        return
    size = STANDARDS_PATH.stat().st_size
    record(
        "standards_file_fits_under_action_hard_truncation",
        size < STANDARDS_HARD_TRUNC_BYTES,
        f"bytes={size} limit={STANDARDS_HARD_TRUNC_BYTES}",
    )
    # AGENTS.md is the trap the curated file exists to avoid.
    agents = REPO / "AGENTS.md"
    if agents.is_file():
        agents_size = agents.stat().st_size
        record(
            "agents_md_exceeds_truncation_so_curated_file_is_required",
            agents_size > STANDARDS_HARD_TRUNC_BYTES,
            f"AGENTS.md_bytes={agents_size}",
        )


def test_model_cr() -> dict[str, Any]:
    docs = load_all_yaml(MODEL_PATH)
    models = [d for d in docs if d.get("kind") == "LiteLLMModel"]
    evidence: dict[str, Any] = {"n_models": len(models)}
    record("model_cr_present", len(models) == 1, f"n={len(models)}")
    if not models:
        return evidence
    m = models[0]
    meta = m.get("metadata") or {}
    spec = m.get("spec") or {}
    params = spec.get("params") or {}
    additional = params.get("additional") or {}
    evidence["name"] = meta.get("name")
    evidence["modelName"] = spec.get("modelName")
    evidence["params_model"] = params.get("model")
    evidence["apiBase"] = params.get("apiBase")

    record(
        "model_metadata_name_pr_review_local",
        meta.get("name") == "pr-review-local",
        f"got={meta.get('name')!r}",
    )
    record(
        "model_spec_modelName_pr_review_local",
        spec.get("modelName") == "pr-review-local",
        f"got={spec.get('modelName')!r}",
    )
    record(
        "model_proxyRef_is_litellm",
        spec.get("proxyRef") == "litellm",
        f"got={spec.get('proxyRef')!r}",
    )
    record(
        "model_backend_is_local_qwen_openai_compatible",
        params.get("model") == "openai/qwen3.6-35b-a3b",
        f"got={params.get('model')!r}",
    )
    record(
        "model_apiBase_is_in_cluster_vllm",
        params.get("apiBase") == "http://vllm-app.ai.svc.cluster.local:8000/v1",
        f"got={params.get('apiBase')!r}",
    )
    # The load-bearing non-thinking flag. Declared on the model, not per-request.
    extra_body = additional.get("extra_body") or {}
    chat_kwargs = extra_body.get("chat_template_kwargs") or {}
    evidence["enable_thinking"] = chat_kwargs.get("enable_thinking")
    record(
        "model_enable_thinking_false_in_litellm_params",
        chat_kwargs.get("enable_thinking") is False,
        f"chat_template_kwargs={chat_kwargs}",
    )
    # Zero-priced: no info.extra prices (recorded spend must mean real USD).
    info = spec.get("info") or params.get("info") or additional.get("model_info")
    record(
        "model_has_no_governance_accounting_prices",
        not info,
        f"info={info!r}",
    )

    # Listed in models kustomization so the operator actually sees it.
    kust = load_yaml(MODELS_KUST)
    resources = list(kust.get("resources") or [])
    record(
        "model_listed_in_models_kustomization",
        "./pr-review-local.yaml" in resources,
        f"resources_tail={resources[-5:]}",
    )
    return evidence


def test_virtualkey_cr() -> dict[str, Any]:
    docs = load_all_yaml(VK_PATH)
    keys = [d for d in docs if d.get("kind") == "LiteLLMVirtualKey"]
    pushes = [d for d in docs if d.get("kind") == "PushSecret"]
    evidence: dict[str, Any] = {"n_keys": len(keys), "n_pushes": len(pushes)}
    record("virtualkey_cr_present", len(keys) == 1, f"n={len(keys)}")
    record("pushsecret_present", len(pushes) == 1, f"n={len(pushes)}")
    if not keys:
        return evidence

    vk = keys[0]
    meta = vk.get("metadata") or {}
    spec = vk.get("spec") or {}
    models = list(spec.get("models") or [])
    evidence["name"] = meta.get("name")
    evidence["keyAlias"] = spec.get("keyAlias")
    evidence["models"] = models
    evidence["maxBudget"] = spec.get("maxBudget")
    evidence["rpmLimit"] = spec.get("rpmLimit")
    evidence["tpmLimit"] = spec.get("tpmLimit")

    record(
        "virtualkey_name_ai_pr_review",
        meta.get("name") == "ai-pr-review",
        f"got={meta.get('name')!r}",
    )
    record(
        "virtualkey_alias_ai_pr_review",
        spec.get("keyAlias") == "ai-pr-review",
        f"got={spec.get('keyAlias')!r}",
    )
    record(
        "virtualkey_secretName_litellm_key_ai_pr_review",
        spec.get("secretName") == "litellm-key-ai-pr-review",
        f"got={spec.get('secretName')!r}",
    )
    record(
        "virtualkey_allowlist_exactly_pr_review_local",
        models == ["pr-review-local"],
        f"got={models}",
    )
    # Must not hold auto or any cloud alias. Names are the METERED CRs
    # (`-metered` suffix, renamed 2026-08-31 - captain decision, Alternative B
    # of data/homeops-claude-code-passthrough-design/report.md); the bare
    # `claude-sonnet-5`/`claude-opus-5` now belong to the credential-less
    # Claude Code subscription pass-through, which is a separate (non-money)
    # concern from this allow-list boundary.
    forbidden = {
        "auto",
        "chat-ha",
        "claude-sonnet-5-metered",
        "claude-opus-5-metered",
        "claude-opus-4-8",
    }
    leak = sorted(set(models) & forbidden)
    record(
        "virtualkey_holds_no_cloud_or_auto_alias",
        not leak,
        f"leak={leak}",
    )
    record(
        "virtualkey_has_maxBudget",
        bool(spec.get("maxBudget")),
        f"maxBudget={spec.get('maxBudget')!r}",
    )
    record(
        "virtualkey_has_rpm_and_tpm_limits",
        spec.get("rpmLimit") is not None and spec.get("tpmLimit") is not None,
        f"rpm={spec.get('rpmLimit')!r} tpm={spec.get('tpmLimit')!r}",
    )
    try:
        rpm = int(spec.get("rpmLimit"))
        tpm = int(spec.get("tpmLimit"))
        record("virtualkey_rpm_positive", rpm > 0, f"rpm={rpm}")
        record("virtualkey_tpm_positive", tpm > 0, f"tpm={tpm}")
    except (TypeError, ValueError) as exc:
        record("virtualkey_rpm_tpm_parseable", False, str(exc))

    if pushes:
        ps = pushes[0]
        pspec = ps.get("spec") or {}
        selector = ((pspec.get("selector") or {}).get("secret") or {})
        data = list(pspec.get("data") or [])
        remote_keys = [
            ((d.get("match") or {}).get("remoteRef") or {}).get("remoteKey")
            for d in data
        ]
        evidence["push_selector_secret"] = selector.get("name")
        evidence["push_remote_keys"] = remote_keys
        record(
            "pushsecret_selects_minted_secret",
            selector.get("name") == "litellm-key-ai-pr-review",
            f"got={selector.get('name')!r}",
        )
        record(
            "pushsecret_remote_key_is_litellm_consumer_ai_pr_review",
            "litellm-consumer-ai-pr-review" in remote_keys,
            f"remote_keys={remote_keys}",
        )
        stores = pspec.get("secretStoreRefs") or []
        store_names = {
            (s.get("name"), s.get("kind")) for s in stores if isinstance(s, dict)
        }
        record(
            "pushsecret_uses_shared_onepassword_clustersecretstore",
            ("onepassword", "ClusterSecretStore") in store_names,
            f"stores={store_names}",
        )

    kust = load_yaml(VK_KUST)
    resources = list(kust.get("resources") or [])
    record(
        "virtualkey_listed_in_virtualkeys_kustomization",
        "./ai-pr-review.yaml" in resources,
        f"resources={resources}",
    )
    return evidence


def test_no_cloud_fallback_for_pr_review_local() -> None:
    proxy = load_yaml(PROXY_PATH)
    # LiteLLMProxy CR: routerSettings under spec
    spec = proxy.get("spec") or {}
    rs = spec.get("routerSettings") or spec.get("router_settings") or {}
    # Some shapes nest under values; accept either.
    if not rs and "routerSettings" in (proxy.get("values") or {}):
        rs = proxy["values"]["routerSettings"]

    # The litellmproxy.yaml in this repo puts router settings under
    # spec.routerSettings with camelCase keys that the operator lowercases.
    fallbacks_raw = rs.get("fallbacks") or rs.get("fallback") or []
    ctx_raw = (
        rs.get("contextWindowFallbacks")
        or rs.get("context_window_fallbacks")
        or []
    )
    # Also accept if the file stores them as a YAML list under routerSettings
    # already in LiteLLM's on-disk form.
    avail = parse_fallback_map(fallbacks_raw)
    ctx = parse_fallback_map(ctx_raw)
    record(
        "pr_review_local_has_no_availability_fallback",
        "pr-review-local" not in avail,
        f"avail_keys={sorted(avail)}",
    )
    record(
        "pr_review_local_has_no_context_window_fallback",
        "pr-review-local" not in ctx,
        f"ctx_keys={sorted(ctx)}",
    )
    # Also ensure it is not a TARGET of someone else's chain in a way that
    # would not matter for spend - not required, but chat-local terminal set
    # style: nothing points cloud traffic at it either. Soft check only on
    # targets containing cloud names from pr-review - skip.


def test_kustomize_inventory_includes_new_objects() -> None:
    """Render the litellm app kustomization and confirm both CRs are in inventory."""
    kustomize = None
    for candidate in (
        Path("/Users/coder/.local/share/mise/installs/aqua-kubernetes-sigs-kustomize"),
        Path("/Users/coder/.local/share/mise/installs/kustomize"),
    ):
        if candidate.is_dir():
            # Prefer highest version-looking leaf binary.
            matches = sorted(candidate.rglob("kustomize"))
            for m in reversed(matches):
                if m.is_file() and os_access_executable(m):
                    kustomize = m
                    break
        if kustomize:
            break
    if kustomize is None:
        # Fall back to PATH lookup without mise shims.
        import shutil

        kustomize_str = shutil.which("kustomize")
        kustomize = Path(kustomize_str) if kustomize_str else None

    if kustomize is None:
        record(
            "kustomize_available",
            False,
            "kustomize binary not found; inventory render skipped",
        )
        return

    try:
        proc = subprocess.run(
            [str(kustomize), "build", str(APP_DIR)],
            check=False,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        record("kustomize_build_litellm_app", False, str(exc))
        return

    record(
        "kustomize_build_litellm_app",
        proc.returncode == 0,
        f"rc={proc.returncode} stderr={proc.stderr[-200:]!r}",
    )
    if proc.returncode != 0:
        return

    rendered = list(yaml.safe_load_all(proc.stdout))
    kinds_names = {
        (d.get("kind"), (d.get("metadata") or {}).get("name"))
        for d in rendered
        if isinstance(d, dict)
    }
    record(
        "rendered_inventory_contains_pr_review_local_model",
        ("LiteLLMModel", "pr-review-local") in kinds_names,
        f"model_names={sorted(n for k, n in kinds_names if k == 'LiteLLMModel')[-8:]}",
    )
    record(
        "rendered_inventory_contains_ai_pr_review_virtualkey",
        ("LiteLLMVirtualKey", "ai-pr-review") in kinds_names,
        f"vk_names={sorted(n for k, n in kinds_names if k == 'LiteLLMVirtualKey')}",
    )
    record(
        "rendered_inventory_contains_ai_pr_review_pushsecret",
        ("PushSecret", "litellm-key-ai-pr-review") in kinds_names,
        f"push_names={sorted(n for k, n in kinds_names if k == 'PushSecret')}",
    )


def os_access_executable(path: Path) -> bool:
    import os

    return os.access(path, os.X_OK)


def test_documentation_present() -> None:
    record("pr_reviewer_doc_exists", DOC_PATH.is_file(), f"path={DOC_PATH}")
    if not DOC_PATH.is_file():
        return


def main() -> int:
    print(f"WF={WF_PATH}")
    print(f"MODEL={MODEL_PATH}")
    print(f"VK={VK_PATH}")

    for required in (WF_PATH, MODEL_PATH, VK_PATH, STANDARDS_PATH, PROXY_PATH):
        if not required.is_file():
            record("required_path_present", False, f"missing {required}")
            _emit_summary()
            return 1

    evidence = test_workflow_contracts()
    test_action_inputs_against_pinned_action_yml(evidence.get("with_keys") or [])
    test_standards_file()
    test_model_cr()
    test_virtualkey_cr()
    test_no_cloud_fallback_for_pr_review_local()
    test_kustomize_inventory_includes_new_objects()
    test_documentation_present()

    return _emit_summary()


def _emit_summary() -> int:
    passed = sum(1 for r in RESULTS if r["ok"])
    failed = sum(1 for r in RESULTS if not r["ok"])
    print()
    print("=== SUMMARY ===")
    print(f"passed={passed} failed={failed} total={passed + failed}")
    if failed:
        print("failures:")
        for r in RESULTS:
            if not r["ok"]:
                print(f"  - {r['name']}: {r['detail']}")
    # Machine-readable blob for evidence collectors.
    print("=== JSON ===")
    print(json.dumps({"passed": passed, "failed": failed, "results": RESULTS}, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
