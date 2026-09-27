#!/usr/bin/env python3
"""Contract tests for what the LiteLLM AI Hub publishes and how it renders.

Invariant (kubernetes/apps/base/ai/litellm/README.md "AI Hub publication
(Git-only)", captain decision `hub-public-set` 2026-09-27): the Hub lists
exactly `spec.litellmSettings.public_model_groups` on the LiteLLMProxy CR. The
Admin UI "make public" path returns 500 on this proxy (store_model_in_db is
false), so that list is the only publication mechanism, and a model reaches the
Hub only when someone adds it there.

Checks, each proven able to fail by the mutations at the end:

  1. Every published name is a registered LiteLLMModel `modelName`. A typo is
     not an error in LiteLLM - it renders a Hub row with no provider at all.
  2. The three internal plumbing aliases stay unpublished (the router's
     classifier, the CI reviewer's alias, and the demo-budget alias whose
     synthetic governance price would display as a real one).
  3. Every registered model is either published or one of those three, so a
     new LiteLLMModel cannot silently miss the Hub - its author has to decide.
  4. Rendered through LiteLLM's own Router (pinned to the cluster image by
     validate.yaml), every published group has a provider, and every
     in-cluster (local) model also has a mode and a context window. LiteLLM's
     cost map cannot resolve a local llama.cpp model, so a local LiteLLMModel
     without an `info` block publishes a blank row - the defect this test was
     written for. Cloud rows are deliberately NOT checked for mode/limits: the
     live proxy fills them from the remote cost map it downloads at boot, while
     CI runs offline on the image's bundled map, which predates most of them.
  5. No published local model shows a non-zero price (local compute is sunk
     cost; a price there is either the demo alias or a mistake).

Live proof (GET /public/model_hub returns these rows) needs the running proxy
and is a post-merge check, not CI.
"""

from __future__ import annotations

import copy
import logging
import os
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "kubernetes" / "apps" / "base" / "ai" / "litellm" / "app"
MODELS_DIR = APP_DIR / "models"
PROXY_PATH = APP_DIR / "litellmproxy.yaml"

UNPUBLISHED = frozenset({"qwen3.6-35b-a3b-classifier", "pr-review-local", "qwen3.6-35b-a3b"})

# The operator's typed LiteLLMModel.spec.info fields and the model_info keys
# they render to; `extra` is merged under them (typed fields win on conflict).
TYPED_INFO_FIELDS = {
    "mode": "mode",
    "maxInputTokens": "max_input_tokens",
    "maxOutputTokens": "max_output_tokens",
    "maxTokens": "max_tokens",
    "supportsFunctionCalling": "supports_function_calling",
    "supportsPromptCaching": "supports_prompt_caching",
    "supportsVision": "supports_vision",
}
TYPED_PARAM_FIELDS = {"model": "model", "apiBase": "api_base", "apiKey": "api_key"}

RESULTS: list[dict[str, Any]] = []
_PRISTINE_MODEL_COST: dict | None = None


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok})
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" - {detail}" if detail else ""))


def load_models() -> list[dict]:
    listed = yaml.safe_load((MODELS_DIR / "kustomization.yaml").read_text())["resources"]
    return [yaml.safe_load((MODELS_DIR / f).read_text()) for f in listed]


def load_public_groups() -> list[str] | None:
    doc = next(d for d in yaml.safe_load_all(PROXY_PATH.read_text()) if d)
    return (doc["spec"].get("litellmSettings") or {}).get("public_model_groups")


def render_entry(cr: dict) -> dict:
    """One LiteLLMModel CR as the model_list entry the operator renders."""
    spec = cr["spec"]
    params = dict(spec.get("params") or {})
    litellm_params = dict(params.pop("additional", None) or {})
    for field, value in params.items():
        litellm_params[TYPED_PARAM_FIELDS.get(field, field)] = value
    info = dict(spec.get("info") or {})
    model_info = dict(info.pop("extra", None) or {})
    for field, value in info.items():
        model_info[TYPED_INFO_FIELDS.get(field, field)] = value
    entry = {"model_name": spec["modelName"], "litellm_params": litellm_params}
    if model_info:
        entry["model_info"] = model_info
    return entry


def is_local(entry: dict) -> bool:
    return ".svc.cluster.local" in str(entry["litellm_params"].get("api_base", ""))


def membership_findings(public: list[str] | None, registered: set[str]) -> list[str]:
    if not isinstance(public, list) or not public:
        return ["public_model_groups missing or empty"]
    findings = [f"unregistered: {n}" for n in public if n not in registered]
    findings += [f"duplicate: {n}" for n in {n for n in public if public.count(n) > 1}]
    findings += [f"plumbing published: {n}" for n in public if n in UNPUBLISHED]
    findings += [
        f"neither published nor a documented exclusion: {n}"
        for n in sorted(registered - set(public) - UNPUBLISHED)
    ]
    return findings


def render_findings(public: list[str], entries: list[dict]) -> list[str]:
    """Build the pinned LiteLLM Router from the rendered entries and read each Hub row."""
    import litellm
    from litellm import Router

    # litellm.model_cost is process-global and every Router writes its
    # deployments into it, so without a reset one render's metadata would leak
    # into the next and mask a missing `info` block in a mutation run.
    global _PRISTINE_MODEL_COST
    if _PRISTINE_MODEL_COST is None:
        _PRISTINE_MODEL_COST = copy.deepcopy(litellm.model_cost)
    litellm.model_cost = copy.deepcopy(_PRISTINE_MODEL_COST)

    model_list = copy.deepcopy(entries)
    for m in model_list:
        m["litellm_params"]["api_key"] = "ci-placeholder"
    router = Router(model_list=model_list)
    by_name = {e["model_name"]: e for e in entries}
    findings = []
    for name in public:
        if name not in by_name:
            continue  # reported by membership_findings
        row = router.get_model_group_info(name)
        if row is None or not row.providers:
            findings.append(f"{name}: no provider")
            continue
        entry = by_name[name]
        if is_local(entry):
            if not row.mode:
                findings.append(f"{name}: local model with no mode (add an `info` block)")
            if not row.max_input_tokens:
                findings.append(f"{name}: local model with no context window (add an `info` block)")
            if (row.input_cost_per_token or 0) or (row.output_cost_per_token or 0):
                findings.append(f"{name}: local model shows a price")
    return findings


def main() -> int:
    os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
    logging.disable(logging.WARNING)

    crs = load_models()
    entries = [render_entry(cr) for cr in crs]
    registered = {e["model_name"] for e in entries}
    public = load_public_groups()

    member = membership_findings(public, registered)
    record("public_groups_membership", not member, "; ".join(member))
    if not isinstance(public, list):
        return 1
    rendered = render_findings(public, entries)
    record("published_rows_render_complete", not rendered, "; ".join(rendered))

    # Every check above must be able to fail.
    local_name = next(e["model_name"] for e in entries if is_local(e) and e["model_name"] in public)
    record(
        "mutation_typo_is_caught",
        bool(membership_findings([*public, "chat-locall"], registered)),
    )
    record(
        "mutation_plumbing_published_is_caught",
        bool(membership_findings([*public, "qwen3.6-35b-a3b-classifier"], registered)),
    )
    record(
        "mutation_unpublished_new_model_is_caught",
        bool(membership_findings([n for n in public if n != local_name], registered)),
    )
    # Strip EVERY local entry, not just one: aliases of one backend share a
    # backend cost-map key in LiteLLM, so a single stripped alias still renders
    # its siblings' metadata. A new local model on its own backend has no
    # sibling, which is exactly what stripping them all reproduces.
    stripped = [{k: v for k, v in e.items() if k != "model_info"} if is_local(e) else e for e in entries]
    record("mutation_local_model_without_info_is_caught", bool(render_findings(public, stripped)))
    priced = copy.deepcopy(entries)
    for e in priced:
        if e["model_name"] == local_name:
            e.setdefault("model_info", {})["input_cost_per_token"] = 0.001
    record("mutation_priced_local_model_is_caught", bool(render_findings(public, priced)))

    failed = [r for r in RESULTS if not r["ok"]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} passed" + (f", {len(failed)} failed" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
