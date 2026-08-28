#!/usr/bin/env python3
"""Behavioral validation of the D3 complexity-tier auto-router config.

Since captain decision O1 (2026-08-26) the proxy config is not a file in Git:
the home-operations litellm-operator renders it from the `LiteLLMModel` CRs in
kubernetes/apps/base/ai/litellm/app/models/. This test therefore RENDERS that
config the way the operator does (see render_config_from_crs below) and then
exercises the result through the real LiteLLM v1.98.0 ComplexityRouter / Router
APIs (same image the cluster runs). Consumers come from the `LiteLLMVirtualKey`
CRs in app/virtualkeys/. Mock OpenAI-compatible backends stand in for the local
classifier/chat model and the cloud tiers so classification and fail-open are
observable without the cluster or paid APIs.

This is intentionally NOT a source-grep test: assertions are on parsed config
semantics and on live routing outcomes (which backend was selected, which
cause label the classifier produced).
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "kubernetes/apps/base/ai/litellm/app"
MODELS_DIR = APP_DIR / "models"
VIRTUALKEYS_DIR = APP_DIR / "virtualkeys"
PROXY_PATH = APP_DIR / "litellmproxy.yaml"
BASELINE_QWEN = {
    "model_name": "qwen3.6-35b-a3b",
    "litellm_params": {
        "model": "openai/qwen3.6-35b-a3b",
        "api_base": "http://vllm-app.ai.svc.cluster.local:8000/v1",
        "api_key": "not-needed",
    },
    "model_info": {
        "input_cost_per_token": 0.00005,
        "output_cost_per_token": 0.0001,
    },
}

RESULTS: list[dict[str, Any]] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append({"name": name, "ok": ok, "detail": detail})
    status = "PASS" if ok else "FAIL"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))


def load_yaml(path: Path) -> dict:
    # PyYAML may not be on host; prefer it, else a tiny subset via the container.
    try:
        import yaml  # type: ignore
        return yaml.safe_load(path.read_text())
    except ImportError:
        # Minimal fallback is not used when running inside litellm image (has yaml).
        raise


def _load_crs(directory: Path, kind: str) -> list[dict]:
    """Every CR of `kind` under `directory`, sorted by metadata.name.

    Sorting matches the operator, which sorts adopted models by resource name
    before rendering so the config (and its hash) is stable regardless of the
    order the API server lists them in.
    """
    import yaml  # type: ignore

    out = []
    for f in sorted(directory.glob("*.yaml")):
        for doc in yaml.safe_load_all(f.read_text()):
            if doc and doc.get("kind") == kind:
                out.append(doc)
    return sorted(out, key=lambda d: d["metadata"]["name"])


def render_config_from_crs() -> dict:
    """Render the proxy config.yaml the way litellm-operator does in file mode.

    Faithful port of internal/controller/render.go renderConfig() at chart
    0.0.15, restricted to the features this app actually uses: the named
    top-level passthrough blocks, and model_list built from LiteLLMModel CRs
    with `params.additional` merged UNDER the typed params (typed keys win).
    Secret-backed apiKeyRef/apiBaseRef are not used here and are asserted away
    rather than silently ignored - if this app ever starts using them, this
    renderer must grow the os.environ indirection the operator emits.

    Kept in-repo rather than shelling out to the operator because the whole
    point of the test is to exercise the rendered config through the real
    LiteLLM Router without a cluster.
    """
    proxy = load_yaml(PROXY_PATH)
    assert proxy["kind"] == "LiteLLMProxy", PROXY_PATH
    spec = proxy["spec"]
    assert spec.get("applyMode", "file") == "file", "renderer only covers file mode"
    assert spec.get("callbacks") is None, "renderer does not cover spec.callbacks"

    config: dict = dict(spec.get("extraConfig") or {})
    for field, key in (
        ("generalSettings", "general_settings"),
        ("routerSettings", "router_settings"),
        ("litellmSettings", "litellm_settings"),
        ("environmentVariables", "environment_variables"),
        ("credentialList", "credential_list"),
        ("defaultVertexConfig", "default_vertex_config"),
        ("filesSettings", "files_settings"),
        ("assistantSettings", "assistant_settings"),
        ("finetuneSettings", "finetune_settings"),
        ("prompts", "prompts"),
        ("vectorStoreRegistry", "vector_store_registry"),
    ):
        if spec.get(field) is not None:
            config[key] = spec[field]

    entries = []
    for m in _load_crs(MODELS_DIR, "LiteLLMModel"):
        ms = m["spec"]
        assert ms.get("proxyRef") == proxy["metadata"]["name"], (
            f"{m['metadata']['name']} does not bind to this proxy"
        )
        pr = ms["params"]
        assert not pr.get("apiKeyRef") and not pr.get("apiBaseRef"), (
            "renderer does not cover secret-backed api key/base refs"
        )
        params = dict(pr.get("additional") or {})
        params["model"] = pr["model"]
        if pr.get("apiVersion"):
            params["api_version"] = pr["apiVersion"]
        if pr.get("dropParams") is not None:
            params["drop_params"] = pr["dropParams"]
        if pr.get("rpm") is not None:
            params["rpm"] = pr["rpm"]
        if pr.get("tpm") is not None:
            params["tpm"] = pr["tpm"]
        if pr.get("apiBase"):
            params["api_base"] = pr["apiBase"]
        if pr.get("apiKey"):
            params["api_key"] = pr["apiKey"]

        entry = {"model_name": ms["modelName"], "litellm_params": params}
        info = ms.get("info") or {}
        rendered_info = dict(info.get("extra") or {})
        for src, dst in (
            ("maxTokens", "max_tokens"),
            ("maxInputTokens", "max_input_tokens"),
            ("maxOutputTokens", "max_output_tokens"),
            ("mode", "mode"),
            ("supportsFunctionCalling", "supports_function_calling"),
            ("supportsPromptCaching", "supports_prompt_caching"),
            ("supportsVision", "supports_vision"),
        ):
            if info.get(src) not in (None, ""):
                rendered_info[dst] = info[src]
        if rendered_info:
            entry["model_info"] = rendered_info
        entries.append(entry)

    config["model_list"] = entries
    return config


# ---------------------------------------------------------------------------
# Mock OpenAI-compatible backend
# ---------------------------------------------------------------------------

class BackendState:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.classifier_mode = "ok"  # ok | empty | timeout | error
        self.forced_tier = "SIMPLE"


STATE = BackendState()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        return

    def _json(self, code: int, body: dict) -> None:
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # health
        if self.path.rstrip("/").endswith("/models") or self.path == "/v1/models":
            self._json(200, {"object": "list", "data": [{"id": "mock", "object": "model"}]})
            return
        self._json(200, {"ok": True})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            body = {}
        path = self.path
        role = self.headers.get("X-Mock-Role", "unknown")
        # Infer role from path prefix we mount under different ports
        STATE.calls.append({"role": role, "path": path, "body": body, "ts": time.time()})

        if STATE.classifier_mode == "timeout" and role == "classifier":
            time.sleep(2.5)
            self._json(500, {"error": {"message": "forced timeout"}})
            return
        if STATE.classifier_mode == "error" and role == "classifier":
            self._json(500, {"error": {"message": "classifier boom"}})
            return
        if STATE.classifier_mode == "empty" and role == "classifier":
            # Mimic thinking-model empty content trap
            self._json(
                200,
                {
                    "id": "chatcmpl-empty",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": "",
                                "reasoning_content": "thinking " * 200,
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                },
            )
            return

        # Classifier: return structured tier JSON LiteLLM expects
        if role == "classifier":
            tier = STATE.forced_tier
            content = json.dumps({"tier": tier, "confidence": 0.9, "reason": "mock"})
            # Also accept schema-wrapped responses - LiteLLM parses content as JSON
            self._json(
                200,
                {
                    "id": "chatcmpl-class",
                    "object": "chat.completion",
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": content},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
                },
            )
            return

        # Chat backends
        model_name = body.get("model", role)
        self._json(
            200,
            {
                "id": f"chatcmpl-{role}",
                "object": "chat.completion",
                "model": model_name,
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": f"served-by:{role}",
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
            },
        )


def start_server(role: str) -> tuple[ThreadingHTTPServer, str]:
    class RoleHandler(Handler):
        def do_POST(self) -> None:  # type: ignore[override]
            self.headers = self.headers  # noqa
            # inject role via rewriting - simpler: set on server
            super().do_POST()

        def handle_one_request(self) -> None:  # type: ignore[override]
            # Patch headers dict-like after parse
            super().handle_one_request()

    # Custom handler factory binding role
    def make_handler(*args, **kwargs):
        h = Handler(*args, **kwargs)
        return h

    class Bound(Handler):
        def do_POST(self) -> None:  # type: ignore[override]
            # Force role header
            self.headers = self.headers  # keep
            # monkeypatch get
            orig_get = self.headers.get

            def get(k, default=None):
                if k.lower() == "x-mock-role":
                    return role
                return orig_get(k, default)

            self.headers.get = get  # type: ignore[method-assign]
            # Also stamp role into STATE via path side channel: set before super
            # Easiest: temporarily set a global current_role
            global CURRENT_ROLE
            CURRENT_ROLE = role
            # Override role detection by patching STATE call append in parent
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode() or "{}")
            except json.JSONDecodeError:
                body = {}
            STATE.calls.append({"role": role, "path": self.path, "body": body, "ts": time.time()})

            if STATE.classifier_mode == "timeout" and role == "classifier":
                time.sleep(2.5)
                self._json(500, {"error": {"message": "forced timeout"}})
                return
            if STATE.classifier_mode == "error" and role == "classifier":
                self._json(500, {"error": {"message": "classifier boom"}})
                return
            if STATE.classifier_mode == "empty" and role == "classifier":
                self._json(
                    200,
                    {
                        "id": "chatcmpl-empty",
                        "object": "chat.completion",
                        "choices": [
                            {
                                "index": 0,
                                "message": {
                                    "role": "assistant",
                                    "content": "",
                                    "reasoning_content": "thinking " * 200,
                                },
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        },
                    },
                )
                return
            if role == "classifier":
                content = json.dumps(
                    {"tier": STATE.forced_tier, "confidence": 0.9, "reason": "mock"}
                )
                self._json(
                    200,
                    {
                        "id": "chatcmpl-class",
                        "object": "chat.completion",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": content},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {
                            "prompt_tokens": 100,
                            "completion_tokens": 20,
                            "total_tokens": 120,
                        },
                    },
                )
                return
            self._json(
                200,
                {
                    "id": f"chatcmpl-{role}",
                    "object": "chat.completion",
                    "model": body.get("model", role),
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": f"served-by:{role}",
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 5,
                        "completion_tokens": 3,
                        "total_tokens": 8,
                    },
                },
            )

        def do_GET(self) -> None:  # type: ignore[override]
            self._json(200, {"object": "list", "data": [{"id": role, "object": "model"}]})

    srv = ThreadingHTTPServer(("127.0.0.1", 0), Bound)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    host, port = srv.server_address[:2]
    base = f"http://{host}:{port}/v1"
    return srv, base


CURRENT_ROLE = ""


def by_name(model_list: list[dict], name: str) -> dict:
    for m in model_list:
        if m.get("model_name") == name:
            return m
    raise KeyError(name)


def test_config_semantics(cfg: dict) -> dict:
    """Parse and assert router semantics via LiteLLM's own pydantic models."""
    from litellm.router_strategy.complexity_router.config import (
        ComplexityRouterConfig,
        ClassifierLLMConfig,
    )

    model_list = cfg["model_list"]
    names = [m["model_name"] for m in model_list]
    record(
        "model_list_contains_required_aliases",
        set(names) >= {
            "qwen3.6-35b-a3b",
            "qwen3.6-35b-a3b-classifier",
            "chat-local",
            "claude-sonnet-5",
            "claude-opus-5",
            "auto",
            "chat-ha",
        },
        f"names={names}",
    )

    # Additive: direct qwen entry matches pre-D3 definition (byte-level fields)
    qwen = by_name(model_list, "qwen3.6-35b-a3b")
    qwen_cmp = {
        "model_name": qwen["model_name"],
        "litellm_params": {
            "model": qwen["litellm_params"]["model"],
            "api_base": qwen["litellm_params"]["api_base"],
            "api_key": qwen["litellm_params"]["api_key"],
        },
        "model_info": dict(qwen.get("model_info") or {}),
    }
    record(
        "direct_qwen_byte_identical_to_pre_d3",
        qwen_cmp == BASELINE_QWEN,
        f"got={qwen_cmp}",
    )

    classifier = by_name(model_list, "qwen3.6-35b-a3b-classifier")
    lp = classifier["litellm_params"]
    record(
        "classifier_points_at_same_backend_as_qwen",
        lp.get("model") == qwen["litellm_params"]["model"]
        and lp.get("api_base") == qwen["litellm_params"]["api_base"],
        f"classifier model/api_base={lp.get('model')}/{lp.get('api_base')}",
    )
    thinking = (
        (lp.get("extra_body") or {})
        .get("chat_template_kwargs", {})
        .get("enable_thinking")
    )
    record(
        "classifier_disables_thinking",
        thinking is False,
        f"enable_thinking={thinking!r}",
    )
    record(
        "classifier_num_retries_zero",
        lp.get("num_retries") == 0,
        f"num_retries={lp.get('num_retries')!r}",
    )

    for cloud in ("claude-sonnet-5", "claude-opus-5"):
        c = by_name(model_list, cloud)
        record(
            f"{cloud}_uses_existing_anthropic_env_key",
            c["litellm_params"].get("api_key") == "os.environ/ANTHROPIC_API_KEY",
            f"api_key={c['litellm_params'].get('api_key')!r}",
        )
        record(
            f"{cloud}_has_no_custom_model_info_prices",
            not c.get("model_info"),
            f"model_info={c.get('model_info')!r}",
        )

    auto = by_name(model_list, "auto")
    alp = auto["litellm_params"]
    record(
        "auto_is_complexity_router",
        alp.get("model") == "auto_router/complexity_router",
        f"model={alp.get('model')!r}",
    )
    record(
        "fail_open_pin_sibling_is_local",
        alp.get("complexity_router_default_model") == "chat-local",
        f"complexity_router_default_model={alp.get('complexity_router_default_model')!r}",
    )

    crc_raw = alp["complexity_router_config"]
    # Real consumer: LiteLLM pydantic model
    parsed = ComplexityRouterConfig.model_validate(crc_raw)
    record(
        "complexity_router_config_parses_as_ComplexityRouterConfig",
        True,
        f"classifier_type={parsed.classifier_type}",
    )
    record(
        "classifier_type_is_llm",
        parsed.classifier_type == "llm",
        f"got={parsed.classifier_type!r}",
    )
    record(
        "classifier_fallback_is_default_model_not_heuristic",
        parsed.classifier_fallback == "default_model",
        f"got={parsed.classifier_fallback!r}",
    )
    record(
        "default_model_is_local",
        parsed.default_model == "chat-local",
        f"got={parsed.default_model!r}",
    )
    record(
        "session_affinity_off",
        parsed.session_affinity is False,
        f"got={parsed.session_affinity!r}",
    )
    record(
        "return_raw_model_name_on",
        parsed.return_raw_model_name is True,
        f"got={parsed.return_raw_model_name!r}",
    )

    llm_cfg = parsed.classifier_llm_config
    record(
        "classifier_llm_config_present",
        isinstance(llm_cfg, ClassifierLLMConfig) and llm_cfg is not None,
        f"type={type(llm_cfg)}",
    )
    assert llm_cfg is not None
    record(
        "classifier_uses_local_classifier_deployment",
        llm_cfg.model == "qwen3.6-35b-a3b-classifier",
        f"model={llm_cfg.model!r}",
    )
    record(
        "classifier_timeout_ms_is_8000",
        llm_cfg.timeout_ms == 8000,
        f"timeout_ms={llm_cfg.timeout_ms!r}",
    )
    record(
        "classification_rubric_is_agentic",
        llm_cfg.classification_rubric == "agentic"
        or str(llm_cfg.classification_rubric) in ("agentic", "ClassificationRubric.agentic"),
        f"rubric={llm_cfg.classification_rubric!r}",
    )

    tiers = parsed.tiers
    # tiers may be enum-keyed
    def tier_val(t):
        if isinstance(tiers, dict):
            for k, v in tiers.items():
                ks = getattr(k, "value", k)
                if str(ks).endswith(t) or ks == t:
                    return v
        return None

    record("tier_SIMPLE_local", tier_val("SIMPLE") == "chat-local", f"{tier_val('SIMPLE')!r}")
    record("tier_MEDIUM_local", tier_val("MEDIUM") == "chat-local", f"{tier_val('MEDIUM')!r}")
    record("tier_COMPLEX_sonnet", tier_val("COMPLEX") == "claude-sonnet-5", f"{tier_val('COMPLEX')!r}")
    record("tier_REASONING_opus", tier_val("REASONING") == "claude-opus-5", f"{tier_val('REASONING')!r}")

    return {
        "parsed": parsed,
        "auto_litellm_params": alp,
        "crc_raw": crc_raw,
    }


def test_local_pricing_split(cfg: dict) -> None:
    """The pricing split that keeps recorded spend honest.

    `qwen3.6-35b-a3b` carries deliberately synthetic per-token prices so the
    `demo` key's $0.05 cap can be exhausted by one smoke test. That is a
    captain-approved test design and it stays. What must NOT happen is real
    production traffic running on a priced local alias: before 2026-08-27 the
    router's SIMPLE/MEDIUM tiers and repo-wiki both did, so a single trivial
    routed request recorded $0.04735 and repo-wiki had accrued $7.82 - all of
    it play money for compute that runs free on our own B70.

    These assertions pin the split in both directions, because either half
    silently rotting reintroduces the bug: the demo alias must KEEP its prices
    (or the budget path stops being provable) and the production alias must
    NEVER gain any (or dashboards start lying again).
    """
    model_list = cfg["model_list"]
    # Non-zero only: an explicit 0 (e.g. claude-code-subscription's flat-rate
    # pass-through) is an honest zero invoice, not a synthetic price, and must
    # not count here. Treat missing and 0 the same.
    priced = {
        m["model_name"]
        for m in model_list
        if ((m.get("model_info") or {}).get("input_cost_per_token") or 0) != 0
    }
    # Exactly ONE alias, the one only the demo budget smoke test can reach.
    # The classifier was zeroed on 2026-08-27 too: LiteLLM bills its sub-call to
    # the CALLING key, so while it was priced it was ~100% of an `auto`
    # consumer's recorded spend even after the tiers moved to `chat-local`.
    # Cloud models are priced by LiteLLM's built-in cost map, never by hand -
    # see models/kustomization.yaml.
    record(
        "only_the_demo_alias_carries_synthetic_prices",
        priced == {"qwen3.6-35b-a3b"},
        f"priced={sorted(priced)}",
    )

    classifier = by_name(model_list, "qwen3.6-35b-a3b-classifier")
    record(
        "classifier_has_no_prices",
        not (classifier.get("model_info") or {}),
        f"model_info={classifier.get('model_info')!r}",
    )

    local = by_name(model_list, "chat-local")
    qwen = by_name(model_list, "qwen3.6-35b-a3b")
    record(
        "chat_local_is_the_same_backend_as_qwen",
        local["litellm_params"].get("model") == qwen["litellm_params"].get("model")
        and local["litellm_params"].get("api_base") == qwen["litellm_params"].get("api_base"),
        f"model={local['litellm_params'].get('model')!r} api_base={local['litellm_params'].get('api_base')!r}",
    )
    record(
        "chat_local_has_no_prices",
        not (local.get("model_info") or {}),
        f"model_info={local.get('model_info')!r}",
    )

    # The priced alias must stay reachable by the demo budget test and NOTHING
    # else. Any other consumer appearing here means production traffic is
    # accruing play money again - the exact regression this split fixed.
    keys = {k["metadata"]["name"]: k["spec"] for k in _load_crs(VIRTUALKEYS_DIR, "LiteLLMVirtualKey")}
    priced_holders = sorted(
        name for name, spec in keys.items()
        if "qwen3.6-35b-a3b" in (spec.get("models") or [])
    )
    record(
        "demo_is_the_only_consumer_of_the_priced_alias",
        priced_holders == ["demo"],
        f"holders={priced_holders}",
    )

    # D4 boundary: a config-declared fallback bypasses the calling key's
    # allow-list (docs/ai-system/litellm/fallbacks.md#1), so an alias listed in
    # either fallback map IS a cloud entitlement regardless of who holds it.
    # `chat-local` must appear in neither, which is what makes it structurally
    # incapable of reaching a paid API - the same property `qwen3.6-35b-a3b`
    # has, and the reason `chat-ha` exists separately for consumers that do
    # want cloud failover.
    try:
        import yaml
    except ImportError:
        record("chat_local_has_no_cloud_fallback", False, "PyYAML missing")
        return
    proxy = yaml.safe_load(PROXY_PATH.read_text())
    router_settings = proxy["spec"].get("routerSettings") or {}
    entitled = set()
    for field in ("fallbacks", "context_window_fallbacks"):
        for entry in router_settings.get(field) or []:
            entitled.update(entry.keys())
    record(
        "chat_local_has_no_cloud_fallback",
        "chat-local" not in entitled,
        f"cloud_entitled_aliases={sorted(entitled)}",
    )
    record(
        "priced_demo_alias_also_has_no_cloud_fallback",
        "qwen3.6-35b-a3b" not in entitled,
        f"cloud_entitled_aliases={sorted(entitled)}",
    )


def test_repo_wiki_consumer_matches_generator() -> None:
    """repo-wiki's allow-list and the CronJob's WIKI_MODEL must move together.

    The proxy checks the model the caller ASKS for, not what it resolves to, so
    a key scoped to `chat-local` while the generator still requests
    `qwen3.6-35b-a3b` fails every generation call on the allow-list. These two
    values live in different apps, which is exactly why this is asserted.
    """
    try:
        import yaml
    except ImportError:
        record("repo_wiki_model_matches_allowlist", False, "PyYAML missing")
        return
    keys = {k["metadata"]["name"]: k["spec"] for k in _load_crs(VIRTUALKEYS_DIR, "LiteLLMVirtualKey")}
    spec = keys.get("repo-wiki")
    if spec is None:
        record("repo_wiki_model_matches_allowlist", False, "repo-wiki LiteLLMVirtualKey missing")
        return
    record(
        "repo_wiki_scoped_to_zero_priced_local_alias",
        spec["models"] == ["chat-local"],
        f"models={spec['models']!r}",
    )
    hr = yaml.safe_load(
        (REPO / "kubernetes/apps/base/ai/repo-wiki/app/helmrelease.yaml").read_text()
    )
    env = {}
    for controller in (hr["spec"]["values"].get("controllers") or {}).values():
        for container in (controller.get("containers") or {}).values():
            env.update(container.get("env") or {})
    wiki_model = env.get("WIKI_MODEL")
    record(
        "repo_wiki_model_matches_allowlist",
        wiki_model in spec["models"],
        f"WIKI_MODEL={wiki_model!r} allow_list={spec['models']!r}",
    )


def test_consumers() -> None:
    keys = {k["metadata"]["name"]: k["spec"] for k in _load_crs(VIRTUALKEYS_DIR, "LiteLLMVirtualKey")}
    # Phase 5 added ha-demo (cloud-entitled chat-ha holder). demo + router-demo
    # stay exactly as D3/D4 defined them; ha-demo is asserted fully by
    # litellm-fallback-chain-test.py.
    required = {"demo", "router-demo", "ha-demo"}
    record(
        "virtualkey_crs_present",
        set(keys) >= required,
        f"names={sorted(keys)}",
    )
    if not required <= set(keys):
        return
    record("demo_consumer_still_direct_only", keys["demo"]["models"] == ["qwen3.6-35b-a3b"])
    record(
        "router_demo_scoped_to_auto_only",
        keys["router-demo"]["models"] == ["auto"],
        f"models={keys['router-demo']['models']!r}",
    )
    record(
        "ha_demo_scoped_to_chat_ha_only",
        keys["ha-demo"]["models"] == ["chat-ha"],
        f"models={keys['ha-demo']['models']!r}",
    )
    # The CRD types maxBudget as a decimal STRING, not a number - a YAML float
    # here is silently rejected at admission, so assert the type as well as the
    # value rather than letting `0.5` look fine to this test and fail in-cluster.
    for name in ("demo", "router-demo", "ha-demo"):
        raw = keys[name].get("maxBudget")
        record(
            f"{name}_maxBudget_is_positive_decimal_string",
            isinstance(raw, str) and float(raw) > 0,
            f"maxBudget={raw!r} type={type(raw).__name__}",
        )
    record(
        "every_virtualkey_binds_to_the_proxy",
        all(spec.get("proxyRef") == "litellm" for spec in keys.values()),
        f"proxyRefs={[s.get('proxyRef') for s in keys.values()]}",
    )


def test_externalsecret_no_new_op_item() -> None:
    """Semantic check: ExternalSecret dataFrom keys are only existing items."""
    try:
        import yaml
    except ImportError:
        record("externalsecret_parse", False, "PyYAML missing")
        return
    es = yaml.safe_load(
        (REPO / "kubernetes/apps/base/ai/litellm/app/externalsecret.yaml").read_text()
    )
    keys = [d["extract"]["key"] for d in es["spec"]["dataFrom"]]
    # litellm + cloudnative-pg were pre-existing; ai-keys is the shared existing item
    allowed = {"litellm", "cloudnative-pg", "ai-keys"}
    record(
        "externalsecret_only_existing_1password_items",
        set(keys) <= allowed and "ai-keys" in keys,
        f"keys={keys}",
    )
    tmpl = es["spec"]["target"]["template"]["data"]
    record(
        "externalsecret_templates_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY" in tmpl,
        f"template_keys={sorted(tmpl)}",
    )


async def test_routing_behavior(cfg: dict, bases: dict[str, str]) -> None:
    """Drive the real Router with mock backends; assert tier dispatch + fail-open."""
    from litellm import Router
    from litellm.router_strategy.complexity_router.complexity_router import (
        ComplexityRouter,
    )

    # Rewrite api_bases in a working copy of model_list for local mocks
    model_list = []
    for m in cfg["model_list"]:
        m = json.loads(json.dumps(m))  # deep copy
        name = m["model_name"]
        lp = m["litellm_params"]
        if name in ("qwen3.6-35b-a3b", "chat-local"):
            lp["model"] = "openai/qwen-local"
            lp["api_base"] = bases["local"]
            lp["api_key"] = "mock"
        elif name == "qwen3.6-35b-a3b-classifier":
            lp["model"] = "openai/qwen-classifier"
            lp["api_base"] = bases["classifier"]
            lp["api_key"] = "mock"
            # keep enable_thinking + num_retries
        elif name == "claude-sonnet-5":
            lp["model"] = "openai/claude-sonnet-5"
            lp["api_base"] = bases["sonnet"]
            lp["api_key"] = "mock"
        elif name == "claude-opus-5":
            lp["model"] = "openai/claude-opus-5"
            lp["api_base"] = bases["opus"]
            lp["api_key"] = "mock"
        elif name == "auto":
            # keep complexity router config; rewrite tier targets stay as model_names
            pass
        model_list.append(m)

    router = Router(model_list=model_list, set_verbose=False)

    # Confirm complexity router was initialized from the auto deployment
    complexity_routers = getattr(router, "complexity_routers", None) or getattr(
        router, "complexity_router", None
    )
    # Find the ComplexityRouter instance
    cr: ComplexityRouter | None = None
    for attr in dir(router):
        val = getattr(router, attr, None)
        if isinstance(val, dict):
            for v in val.values():
                if isinstance(v, ComplexityRouter):
                    cr = v
                    break
        if isinstance(val, ComplexityRouter):
            cr = val
        if cr:
            break
    # Fallback: construct from deployment the same way init does
    if cr is None:
        auto = by_name(model_list, "auto")
        cr = ComplexityRouter(
            model_name="auto",
            litellm_router_instance=router,
            complexity_router_config=auto["litellm_params"]["complexity_router_config"],
            default_model=auto["litellm_params"]["complexity_router_default_model"],
        )
        record("complexity_router_constructed_manually", True, "router did not auto-expose instance")
    else:
        record("complexity_router_initialized_by_Router", True, type(cr).__name__)

    record(
        "constructed_router_classifier_type_llm",
        cr.config.classifier_type == "llm",
        f"{cr.config.classifier_type}",
    )
    record(
        "constructed_router_fallback_default_model",
        cr.config.classifier_fallback == "default_model"
        and cr.config.default_model == "chat-local",
        f"fallback={cr.config.classifier_fallback} default={cr.config.default_model}",
    )

    # --- aclassify success path: forced tiers map correctly ---
    async def classify_tier(tier_name: str) -> Any:
        STATE.classifier_mode = "ok"
        STATE.forced_tier = tier_name
        STATE.calls.clear()
        outcome = await cr.aclassify(
            prompt=f"prompt for {tier_name}",
            messages=[{"role": "user", "content": f"prompt for {tier_name}"}],
        )
        return outcome

    # Map expected backend from tier
    expected_backend = {
        "SIMPLE": "chat-local",
        "MEDIUM": "chat-local",
        "COMPLEX": "claude-sonnet-5",
        "REASONING": "claude-opus-5",
    }

    for tier_name, backend in expected_backend.items():
        try:
            outcome = await classify_tier(tier_name)
            tier_val = getattr(outcome.tier, "value", str(outcome.tier))
            cause = outcome.cause
            ok = tier_val == tier_name and cause == "llm_classifier"
            record(
                f"aclassify_{tier_name}_via_llm",
                ok,
                f"tier={tier_val} cause={cause} signals={outcome.signals}",
            )
            # Also exercise _classify_and_route to see deployment selection
            STATE.forced_tier = tier_name
            STATE.classifier_mode = "ok"
            resp = await cr.async_pre_routing_hook(
                model="auto",
                request_kwargs={},
                messages=[{"role": "user", "content": f"route {tier_name}"}],
            )
            routed = None
            if resp is not None:
                routed = getattr(resp, "model", None) or (resp.get("model") if isinstance(resp, dict) else None)
                # PreRoutingHookResponse may use different attr
                if routed is None:
                    rd = getattr(resp, "model_group", None) or getattr(resp, "deployment", None)
                    routed = rd
                # try dict-like
                if hasattr(resp, "__dict__"):
                    detail = {k: v for k, v in vars(resp).items() if not k.startswith("_")}
                else:
                    detail = repr(resp)
            else:
                detail = "None"
            # Accept either model name match or detail containing backend
            ok_route = routed == backend or (isinstance(detail, dict) and backend in str(detail))
            if not ok_route and resp is not None:
                # dump for diagnosis
                detail = repr(resp)
                ok_route = backend in detail
            record(
                f"pre_route_{tier_name}_selects_{backend}",
                ok_route,
                f"routed={routed!r} detail={detail!r}",
            )
        except Exception as e:
            record(f"aclassify_{tier_name}_via_llm", False, f"{type(e).__name__}: {e}")
            traceback.print_exc()

    # --- Fail-open: classifier error ---
    STATE.classifier_mode = "error"
    STATE.calls.clear()
    try:
        outcome = await cr.aclassify(
            prompt="fail open please",
            messages=[{"role": "user", "content": "fail open please"}],
        )
        cause = outcome.cause
        # default_model_fallback is the expected cause
        ok = cause == "default_model_fallback" or (
            cause != "llm_classifier" and cr.config.default_model == "chat-local"
        )
        record(
            "fail_open_on_classifier_error",
            cause == "default_model_fallback",
            f"cause={cause} tier={getattr(outcome.tier, 'value', outcome.tier)}",
        )
        resp = await cr.async_pre_routing_hook(
            model="auto",
            request_kwargs={},
            messages=[{"role": "user", "content": "fail open please"}],
        )
        routed = getattr(resp, "model", None) if resp is not None else None
        decision = getattr(resp, "routing_decision", None) or {}
        record(
            "fail_open_routes_to_local_qwen",
            routed == "chat-local"
            and decision.get("routed_model") == "chat-local"
            and decision.get("cause") == "default_model_fallback",
            f"routed={routed!r} decision={decision!r}",
        )
    except Exception as e:
        record("fail_open_on_classifier_error", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()

    # --- Fail-open: empty content (thinking-model trap) ---
    STATE.classifier_mode = "empty"
    try:
        outcome = await cr.aclassify(
            prompt="empty content trap",
            messages=[{"role": "user", "content": "empty content trap"}],
        )
        record(
            "fail_open_on_empty_classifier_content",
            outcome.cause == "default_model_fallback",
            f"cause={outcome.cause}",
        )
    except Exception as e:
        record(
            "fail_open_on_empty_classifier_content",
            False,
            f"{type(e).__name__}: {e}",
        )

    # --- Direct alias still works without router ---
    STATE.classifier_mode = "ok"
    STATE.calls.clear()
    try:
        resp = await router.acompletion(
            model="qwen3.6-35b-a3b",
            messages=[{"role": "user", "content": "ping"}],
            max_tokens=8,
        )
        content = resp.choices[0].message.content
        classifier_called = any(c["role"] == "classifier" for c in STATE.calls)
        record(
            "direct_qwen_alias_bypasses_router",
            content == "served-by:local" and not classifier_called,
            f"content={content!r} calls={[c['role'] for c in STATE.calls]}",
        )
    except Exception as e:
        record("direct_qwen_alias_bypasses_router", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()


def test_image_version_floor() -> None:
    try:
        import yaml
    except ImportError:
        return
    proxy = yaml.safe_load(PROXY_PATH.read_text())
    # Since O1 the image is a full reference on the LiteLLMProxy CR, not a bare
    # tag in HelmRelease values.
    image = proxy["spec"]["image"]
    repo, _, tag = image.partition(":")
    record(
        "litellm_image_is_non_root_variant",
        repo == "ghcr.io/berriai/litellm-non_root",
        f"repo={repo}",
    )
    # parse vMAJOR.MINOR.PATCH
    assert tag.startswith("v"), f"unparseable tag in {image!r}"
    major, minor, patch = (int(x) for x in tag[1:].split("."))
    record(
        "litellm_image_at_or_above_v1_93_0",
        (major, minor, patch) >= (1, 93, 0),
        f"tag={tag}",
    )
    record("litellm_image_is_v1_98_0", tag == "v1.98.0", f"tag={tag}")

    # Renovate floor
    overrides = (REPO / ".renovate/overrides.json5").read_text()
    # json5 may have comments - semantic check via a small strip
    record(
        "renovate_floor_blocks_below_1_93_0",
        'allowedVersions: ">=1.93.0"' in overrides
        and "ghcr.io/berriai/litellm-non_root" in overrides,
        "overrides.json5 contains floor",
    )


def test_docs_exist() -> None:
    doc = REPO / "docs/ai-system/litellm/auto-router.md"
    text = doc.read_text() if doc.exists() else ""
    record("auto_router_doc_exists", doc.exists() and len(text) > 500, f"bytes={len(text)}")
    # Docs as a user-facing artifact: required sections present as structure,
    # checked via markdown headings (the published contract), not implementation greps.
    headings = [ln.strip() for ln in text.splitlines() if ln.startswith("#")]
    needed = [
        "Tiers",
        "classifier",
        "Fail open",
        "latency",
        "Budgets",
        "Observability",
        "Post-merge verification",
        "Tuning",
        "Version floor",
    ]
    missing = [n for n in needed if not any(n.lower() in h.lower() for h in headings)]
    record("auto_router_doc_has_required_sections", not missing, f"missing={missing} headings={headings}")


def main() -> int:
    print(f"PROXY={PROXY_PATH}")
    print(f"MODELS={MODELS_DIR}")
    for required in (PROXY_PATH, MODELS_DIR, VIRTUALKEYS_DIR):
        if not required.exists():
            print(f"{required} missing", file=sys.stderr)
            return 2

    cfg = render_config_from_crs()
    print(f"rendered {len(cfg['model_list'])} model_list entries from LiteLLMModel CRs")
    test_config_semantics(cfg)
    test_local_pricing_split(cfg)
    test_consumers()
    test_repo_wiki_consumer_matches_generator()
    test_externalsecret_no_new_op_item()
    test_image_version_floor()
    test_docs_exist()

    # Start mock backends
    servers = {}
    bases = {}
    for role in ("local", "classifier", "sonnet", "opus"):
        srv, base = start_server(role)
        servers[role] = srv
        bases[role] = base
        print(f"mock {role} at {base}")

    try:
        asyncio.run(test_routing_behavior(cfg, bases))
    finally:
        for srv in servers.values():
            srv.shutdown()

    failed = [r for r in RESULTS if not r["ok"]]
    print("\n=== SUMMARY ===")
    print(f"passed={len(RESULTS) - len(failed)} failed={len(failed)} total={len(RESULTS)}")
    out_path = os.environ.get("EVIDENCE_OUT")
    if out_path:
        Path(out_path).write_text(json.dumps({"results": RESULTS, "failed": failed}, indent=2))
        print(f"wrote {out_path}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
