#!/usr/bin/env python3
"""Offline unit test for the AI review's blocking gate (scripts/ci/ai_review_gate.py).

Checks, without calling Jev:
  1. the request uses LiteLLM's OpenAI-shaped /v1/decisions body (`input` text, a list of
     `predicate` questions, one per blocker rule, model `jev`);
  2. decide() blocks at the threshold, and returns None (could not decide) on a refusal or a
     missing answer, never a silent pass;
  3. candidate_undefined_variables() hints an added `${NAME}` that is not a Flux variable, and
     skips an escaped `$${NAME}`, a defined variable, a comment, a substitute-disabled file and
     a file whose overlay has no postBuild.
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
def answers(p: float) -> list:
    return [{"name": k, "probability": p if k == "plaintext_secret" else 0.0} for k in gate.QUESTIONS]


blocked, scores = gate.decide(answers(t), t)
check(blocked is True and scores["plaintext_secret"] == t, "decide: a score at the threshold blocks")
blocked, _ = gate.decide(answers(t - 0.01), t)
check(blocked is False, "decide: a score below the threshold passes")
full = [{"name": k, "probability": 0.1} for k in gate.QUESTIONS]
blocked, _ = gate.decide(full, t)
check(blocked is False, "decide: all answers low passes")
blocked, scores = gate.decide(full[:-1] + [{"type": "refusal", "name": full[-1]["name"]}], t)
check(blocked is None, "decide: a refusal means could not decide, not pass")

FILES = {
    "kubernetes/apps/base/x/app/helmrelease.yaml": "spec: {}\n",
    "kubernetes/apps/base/y/app/configmap.yaml": "metadata:\n  annotations:\n    kustomize.toolkit.fluxcd.io/substitute: disabled\n",
    "kubernetes/apps/base/z/app/resources/config.yaml": "a: b\n",
    "kubernetes/apps/base/z/app/kustomization.yaml": "      kustomize.toolkit.fluxcd.io/substitute: disabled\n",
    "kubernetes/apps/base/n/app/cronjob.yaml": "spec: {}\n",
}
gate.show.cache_clear()
gate.git = lambda *args: FILES.get(args[1].split(":", 1)[1], "") if args[0] == "show" else ""
diff = (
    "+++ b/kubernetes/apps/base/x/app/helmrelease.yaml\n"
    "+  A: \"${HOME}/x\"\n+  B: \"$${HOME}/x\"\n+  C: \"https://a.${SECRET_DOMAIN}\"\n"
    "+  # a comment mentioning ${HOME}\n"
    "+++ b/kubernetes/apps/base/y/app/configmap.yaml\n+  D: \"${datasource}\"\n"
    "+++ b/kubernetes/apps/base/z/app/resources/config.yaml\n+  E: \"${TOKEN}\"\n"
    "+++ b/kubernetes/apps/base/n/app/cronjob.yaml\n+  F: \"${rc}\"\n"
    "+++ b/scripts/x.sh\n+echo ${HOME}\n"
)
owners = [("kubernetes/apps/base/x/app/", True), ("kubernetes/apps/base/n/app/", False)]
found = gate.candidate_undefined_variables(diff, "HEAD", ["SECRET_DOMAIN"], owners)
check(found == ["kubernetes/apps/base/x/app/helmrelease.yaml: ${HOME}"],
      f"candidates: only the unescaped, undefined, substituted, non-comment one (got {found})")

print(f"Summary: {len(failures)} failed")
sys.exit(1 if failures else 0)
