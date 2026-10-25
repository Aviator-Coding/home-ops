#!/usr/bin/env python3
"""Behavioral contract: the loaded chat model's name lives in ONE place.

Before this gate the name `qwen3.6-35b-a3b` was typed into ~36 files, and it was
already wrong: the loaded weights are Qwen3.8. A model swap meant renaming every
consumer. The contract now has two layers:

  1. The REAL loaded name is the Flux variable CHAT_MODEL_ID, defined once in the
     ConfigMap `chat-model` (kubernetes/apps/main/ai/chat-model.yaml). Only the
     places that talk to the backend use it: the llama-server --alias, the
     LiteLLM `params.model` of the local CRs, the agentgateway failover provider,
     and the ServiceMonitor `model` label.
  2. Consumers use the stable ROLE alias `chat-local` and never a model name, so a
     swap changes one value and renames nothing.

What this catches
  - the old literal name creeping back anywhere under kubernetes/
  - a Flux Kustomization using ${CHAT_MODEL_ID} without substituteFrom (the
    variable would render as an empty string and the alias would be blank)
  - a local LiteLLM CR pointing at a hardcoded model instead of the variable
  - Hermes, Linkwarden or the agentgateway route moving back to a raw name
  - the ServiceMonitor label drifting from the variable

Run: python3 scripts/ci/chat-model-id-test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
AI_APPS = ROOT / "kubernetes/apps/main/ai"
AI_BASE = ROOT / "kubernetes/apps/base/ai"

OLD_NAME = "qwen3.6-35b-a3b"
VARIABLE = "${CHAT_MODEL_ID}"
ROLE = "chat-local"
LOCAL_BACKEND_MODELS = (
    "chat-local",
    "chat-ha",
    "demo-priced-local",
    "chat-local-classifier",
    "pr-review-local",
)


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_all(path: Path) -> list[dict]:
    return [d for d in yaml.safe_load_all(path.read_text()) if d]


def load_one(path: Path) -> dict:
    docs = load_all(path)
    require(len(docs) == 1, f"{path.relative_to(ROOT)}: expected one document, got {len(docs)}")
    return docs[0]


def test_config_map() -> None:
    cm = load_one(AI_APPS / "chat-model.yaml")
    require(cm.get("kind") == "ConfigMap", "chat-model.yaml must be a ConfigMap")
    require(cm["metadata"]["name"] == "chat-model", "ConfigMap must be named chat-model")
    value = (cm.get("data") or {}).get("CHAT_MODEL_ID", "")
    require(bool(value), "CHAT_MODEL_ID must be set")
    require(re.fullmatch(r"[A-Za-z0-9._-]+", value) is not None,
            f"CHAT_MODEL_ID {value!r} must be one path-safe token (no '/', no spaces): "
            "a '/' would match agentgateway's OpenRouter vendor-slug rule")
    require(value != OLD_NAME, "CHAT_MODEL_ID must be the loaded model, not the stale alias")
    resources = load_one(AI_APPS / "kustomization.yaml")["resources"]
    require("./chat-model.yaml" in resources, "kustomization.yaml must list ./chat-model.yaml")


def test_substitute_from() -> None:
    for app in ("vllm", "litellm", "agentgateway"):
        ks = next(d for d in load_all(AI_APPS / f"{app}.yaml") if d.get("kind") == "Kustomization")
        sources = ks["spec"]["postBuild"]["substituteFrom"]
        require({"name": "chat-model", "kind": "ConfigMap"} in sources,
                f"{app}.yaml must substituteFrom ConfigMap chat-model")


def test_llama_server() -> None:
    hr = load_one(AI_BASE / "vllm/app/helmrelease.yaml")
    args = hr["spec"]["values"]["controllers"]["vllm"]["containers"]["app"]["args"]
    require(args[args.index("--alias") + 1] == VARIABLE, f"--alias must be {VARIABLE}")
    sm = load_one(AI_BASE / "vllm/app/servicemonitor.yaml")
    relabel = {r["targetLabel"]: r["replacement"] for r in sm["spec"]["endpoints"][0]["relabelings"]}
    require(relabel.get("model") == VARIABLE, f"ServiceMonitor model label must be {VARIABLE}")


def test_litellm_crs() -> None:
    models = AI_BASE / "litellm/app/models"
    for name in LOCAL_BACKEND_MODELS:
        cr = load_one(models / f"{name}.yaml")
        require(cr["metadata"]["name"] == name and cr["spec"]["modelName"] == name,
                f"{name}.yaml must declare model {name}")
        want = f"openai/{VARIABLE}"
        require(cr["spec"]["params"]["model"] == want,
                f"{name}.yaml params.model must be {want}, got {cr['spec']['params']['model']}")
    classifier = load_one(models / "auto.yaml")["spec"]["params"]["additional"]
    llm = classifier["complexity_router_config"]["classifier_llm_config"]
    require(llm["model"] == "chat-local-classifier", "auto router classifier must be chat-local-classifier")


def test_agentgateway() -> None:
    routes = load_all(AI_BASE / "agentgateway/app/httproute-unified.yaml")[0]["spec"]["rules"]
    local = [r for r in routes
             if r["backendRefs"][0]["name"] == "llm-chat-failover"]
    require(len(local) == 1, "exactly one route must target llm-chat-failover")
    header = local[0]["matches"][0]["headers"][0]
    require(header["type"] == "Exact" and header["value"] == ROLE,
            f"local chat route must match x-model Exact {ROLE}")
    backend = next(d for d in load_all(AI_BASE / "agentgateway/app/backends/vllm.yaml")
                   if d.get("metadata", {}).get("name") == "llm-chat-failover")
    provider = backend["spec"]["ai"]["groups"][0]["providers"][0]
    require(provider["openai"]["model"] == VARIABLE, f"local provider model must be {VARIABLE}")
    catalog = (AI_BASE / "agentgateway/app/httproute-models.yaml").read_text()
    require(f'"id":"{ROLE}"' in catalog and f'"root":"{VARIABLE}"' in catalog,
            "models catalog must list the role id and expose the real name as root")


def test_consumers() -> None:
    hermes = load_one(AI_BASE / "hermes/app/resources/config.yaml")
    require(hermes["model"]["default"] == ROLE, f"Hermes default model must be {ROLE}")
    lw = (ROOT / "kubernetes/apps/base/selfhosted/linkwarden/app/helmrelease.yaml").read_text()
    require(re.search(rf'OPENAI_MODEL:\s*"{ROLE}"', lw) is not None, f"Linkwarden OPENAI_MODEL must be {ROLE}")


def test_no_stale_name() -> None:
    hits = []
    for path in (ROOT / "kubernetes").rglob("*"):
        if path.is_file() and OLD_NAME in path.read_text(errors="ignore"):
            hits.append(str(path.relative_to(ROOT)))
    require(not hits, f"stale name {OLD_NAME!r} still present in: {', '.join(sorted(hits))}")


def main() -> int:
    for fn in (test_config_map, test_substitute_from, test_llama_server, test_litellm_crs,
               test_agentgateway, test_consumers, test_no_stale_name):
        print(f"==> {fn.__name__}")
        fn()
    print("PASS: chat model name is defined once; consumers use the role alias")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Failure, FileNotFoundError, KeyError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
