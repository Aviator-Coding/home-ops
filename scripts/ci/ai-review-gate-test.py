#!/usr/bin/env python3
"""Offline unit test for the AI review's blocking gate (scripts/ci/ai_review_gate.py).

Checks, without calling Jev:
  1. the request uses LiteLLM's OpenAI-shaped /v1/decisions body (`input` text, a list of
     `predicate` questions, one per blocker rule, model `jev`);
  2. decide() blocks at the threshold and treats a refusal or missing answer as 0;
  3. undefined_variables() flags an added `${NAME}` that is not a Flux variable, and passes
     an escaped `$${NAME}`, a defined variable, and a substitute-disabled file.
The live calibration (10 D6 seeds, two runs) is recorded in goal §8.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("ai_review_gate", HERE / "ai_review_gate.py")
gate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gate)

failures: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("[PASS] " if cond else "[FAIL] ") + msg)
    if not cond:
        failures.append(msg)


body = gate.request_body({"diff": "x", "changed_files": {}, "flux_variables": ["SECRET_DOMAIN"]})
check(body["model"] == "jev" and isinstance(body["input"], str), "body: model jev, input is text")
check(isinstance(body["questions"], list) and len(body["questions"]) == len(gate.QUESTIONS),
      "body: one question per blocker rule, as a list")
check(all(q["type"] == "predicate" and q["name"] in gate.QUESTIONS and q["instructions"]
          for q in body["questions"]), "body: predicate questions with names and instructions")
check(json.loads(body["input"])["flux_variables"] == ["SECRET_DOMAIN"], "body: state serialised into input")

t = gate.THRESHOLD
blocked, scores = gate.decide([{"name": "plaintext_secret", "probability": t}], t)
check(blocked and scores["plaintext_secret"] == t, "decide: a score at the threshold blocks")
blocked, _ = gate.decide([{"name": "plaintext_secret", "probability": t - 0.01}], t)
check(not blocked, "decide: a score below the threshold passes")
blocked, scores = gate.decide([{"type": "refusal", "name": "ccnp_default_deny"}], t)
check(not blocked and scores["ccnp_default_deny"] == 0.0, "decide: a refusal scores 0")

FILES = {
    "kubernetes/apps/base/x/app/helmrelease.yaml": "spec: {}\n",
    "kubernetes/apps/base/y/app/configmap.yaml": "metadata:\n  annotations:\n    kustomize.toolkit.fluxcd.io/substitute: disabled\n",
}
gate.git = lambda *args: FILES.get(args[1].split(":", 1)[1], "") if args[0] == "show" else ""
diff = (
    "+++ b/kubernetes/apps/base/x/app/helmrelease.yaml\n"
    "+  A: \"${HOME}/x\"\n+  B: \"$${HOME}/x\"\n+  C: \"https://a.${SECRET_DOMAIN}\"\n"
    "+++ b/kubernetes/apps/base/y/app/configmap.yaml\n+  D: \"${datasource}\"\n"
    "+++ b/scripts/x.sh\n+echo ${HOME}\n"
)
found = gate.undefined_variables(diff, "HEAD", ["SECRET_DOMAIN"])
check(found == ["kubernetes/apps/base/x/app/helmrelease.yaml: ${HOME}"],
      f"undefined_variables: only the unescaped, undefined, Flux-reconciled one (got {found})")

print(f"Summary: {len(failures)} failed")
sys.exit(1 if failures else 0)
