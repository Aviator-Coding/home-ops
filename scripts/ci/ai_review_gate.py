#!/usr/bin/env python3
"""Blocking gate for the AI PR review: ask Jev one yes/no question per blocker rule.

The local reviewer model writes the review text; it missed 3 of 5 seeded blockers and
invented details on a good PR (goal §8, D6 baseline). Jev (`jev`, TypeSafe's decision
model behind LiteLLM's POST /v1/decisions) answers each yes/no (predicate) question with a
probability of "yes". The gate fails when any probability reaches the threshold.

The request uses LiteLLM's OpenAI-shaped body (`input` + `predicate` questions). Jev sees the diff, the full post-change text of each changed file (the diff alone hid a
kopiur include), and the Flux variables the repo defines (it once called ${HOME} one).

    ai_review_gate.py --base <sha> --head <sha> [--threshold 0.8] [--dry-run]

Env: LITELLM_URL (default http://litellm.ai.svc.cluster.local:4000), LITELLM_KEY.
Exit 0 pass, 1 blocked, 2 could not decide (the workflow treats 2 as pass with a notice).
Stdlib only. Tested by scripts/ci/ai-review-gate-test.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

MAX_STATE_BYTES = 30000  # Jev's accurate mode is about 32 KB of text
MAX_FILE_BYTES = 8000
# Calibrated 2026-10-31 on the 10 D6 seeds (two runs, +-0.03): caught blockers 0.87-0.98,
# good PRs at most 0.32. Re-calibrate when the jev model version changes.
THRESHOLD = 0.6
ADDED_VAR = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

QUESTIONS: dict[str, dict] = {
    "kopiur_claim_wait": {
        "instructions": "Does this change set `wait: true` on a Flux Kustomization whose components "
                        "include kopiur or volsync (a Kustomization that owns a backed-up volume claim)?",
        "criteria": {"true": "A changed or added line makes such a Kustomization `wait: true`.",
                     "false": "No such line, or the Kustomization has no kopiur/volsync component."},
    },
    "kopiur_force_or_ssa": {
        "instructions": "Does this change set `force: enabled` on, or remove `ssa: IfNotPresent` from, "
                        "a kopiur or volsync PVC component or overlay?",
        "criteria": {"true": "A changed line does that.", "false": "No changed line does that."},
    },
    "healthcheck_on_helmrelease": {
        "instructions": "Does this change add or change a Flux Kustomization `healthChecks` entry whose "
                        "`kind` is `HelmRelease`?",
        "criteria": {"true": "A healthChecks entry now targets kind HelmRelease.",
                     "false": "Health checks target workloads (Deployment, StatefulSet, ...) or are unchanged."},
    },
    "undefined_flux_variable": {
        "instructions": "Does this change add a literal `${NAME}` to a Kubernetes manifest where NAME is "
                        "not in state.flux_variables, it is not escaped as `$${NAME}`, and the resource is "
                        "not annotated `kustomize.toolkit.fluxcd.io/substitute: disabled`?",
        "criteria": {"true": "Such an unescaped, undefined ${NAME} is added.",
                     "false": "Every added ${NAME} is a defined Flux variable, escaped, or in a disabled resource."},
    },
    "ccnp_default_deny": {
        "instructions": "Does this change add or change a CiliumClusterwideNetworkPolicy that has a "
                        "`nodeSelector` and `ingress` or `egress` allow rules, without setting "
                        "`enableDefaultDeny` to false for that direction?",
        "criteria": {"true": "Such a policy would put the host endpoint into default-deny.",
                     "false": "No such policy, or enableDefaultDeny is false for that direction."},
    },
    "workflow_without_fork_guard": {
        "instructions": "Does this change add or change a GitHub Actions job that runs on a "
                        "`gha-runner-scale-set-*` self-hosted runner, is reachable from `pull_request`, "
                        "and lacks the fork guard `github.event.pull_request.head.repo.full_name == "
                        "github.repository` (on the job or on a job it needs)?",
        "criteria": {"true": "Such an unguarded in-cluster job exists in the change.",
                     "false": "No such job, or it is guarded, or it runs on ubuntu-latest."},
    },
    "plaintext_secret": {
        "instructions": "Does this change add a literal secret value (password, token, API key, private "
                        "key, kubeconfig) to a file in Git, rather than an ExternalSecret reference?",
        "criteria": {"true": "A real secret value is added in plain text.",
                     "false": "No secret values, only references, placeholders or public data."},
    },
    "other_tripwire": {
        "instructions": "Does this change do any of: a HelmRelease `postRenderer` running bash; "
                        "`sessions.vacuum_after_prune: true` for Hermes; a generic-device-plugin "
                        "`mountPath` renaming a DRM device node; `security.cephx.csi: aes256k` or a "
                        "lower `keyGeneration`; a workflow that runs `tofu apply` automatically?",
        "criteria": {"true": "One of those is added.", "false": "None of those."},
    },
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


def flux_variables(root: Path) -> list[str]:
    """SECRET_DOMAIN (the only cluster-secrets key) plus every overlay's postBuild.substitute key."""
    names = {"SECRET_DOMAIN"}
    for path in (root / "kubernetes" / "apps" / "main").rglob("*.yaml"):
        text = path.read_text(errors="replace")
        for block in re.findall(r"\n    substitute:\n((?:      [A-Z0-9_]+:.*\n)+)", text):
            names.update(re.findall(r"^      ([A-Z0-9_]+):", block, re.M))
    return sorted(names)


def undefined_variables(diff: str, head: str, known: list[str]) -> list[str]:
    """Deterministic: `${NAME}` added to a Flux-reconciled file under kubernetes/ where NAME is
    not a Flux variable, not escaped as `$${NAME}`, and the file is not substitute-disabled."""
    found, path = [], ""
    for line in diff.splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            continue
        if not line.startswith("+") or line.startswith("+++") or not path.startswith("kubernetes/"):
            continue
        if not path.endswith((".yaml", ".yml")):
            continue
        try:
            text = git("show", f"{head}:{path}")
        except subprocess.CalledProcessError:
            text = ""
        if "kustomize.toolkit.fluxcd.io/substitute: disabled" in text:
            continue
        for name in ADDED_VAR.findall(line[1:]):
            if name not in known:
                found.append(f"{path}: ${{{name}}}")
    return sorted(set(found))


def build_state(base: str, head: str, root: Path) -> dict:
    diff = git("diff", "--unified=5", f"{base}...{head}")
    files = [f for f in git("diff", "--name-only", "--diff-filter=AMR", f"{base}...{head}").split() if f]
    state: dict = {"diff": diff, "changed_files": {}, "flux_variables": flux_variables(root)}
    budget = MAX_STATE_BYTES - len(diff) - 2000
    for f in files:
        try:
            body = git("show", f"{head}:{f}")
        except subprocess.CalledProcessError:
            continue
        body = body[:MAX_FILE_BYTES]
        if budget - len(body) < 0:
            state["changed_files"][f] = "(omitted: over the size budget)"
            continue
        state["changed_files"][f] = body
        budget -= len(body)
    if len(state["diff"]) > MAX_STATE_BYTES:
        state["diff"] = state["diff"][:MAX_STATE_BYTES] + "\n(diff truncated)"
    return state


def request_body(state: dict) -> dict:
    """LiteLLM's OpenAI-shaped /v1/decisions body: `input` text and a list of predicates."""
    questions = [
        {"type": "predicate", "name": k,
         "instructions": f"{v['instructions']} Yes: {v['criteria']['true']} No: {v['criteria']['false']}"}
        for k, v in QUESTIONS.items()
    ]
    return {"model": "jev", "input": json.dumps(state, indent=1), "questions": questions}


def decide(answers: list, threshold: float) -> tuple[bool, dict[str, float]]:
    """Blocked when any predicate's yes-probability reaches the threshold. A refusal or a
    missing answer scores 0 here; the caller reports it."""
    by_name = {a.get("name"): a for a in answers if isinstance(a, dict)}
    scores = {k: float((by_name.get(k) or {}).get("probability", 0.0)) for k in QUESTIONS}
    return any(v >= threshold for v in scores.values()), scores


def call(body: dict, url: str, key: str, timeout: float = 60) -> dict:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/v1/decisions", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.load(resp)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the raw scores as JSON")
    args = ap.parse_args()
    state = build_state(args.base, args.head, Path.cwd())
    undefined = undefined_variables(git("diff", "--unified=0", f"{args.base}...{args.head}"),
                                    args.head, state["flux_variables"])
    body = request_body(state)
    if args.dry_run:
        print(json.dumps(body, indent=2)[:4000])
        return 0
    try:
        resp = call(body, os.environ.get("LITELLM_URL", "http://litellm.ai.svc.cluster.local:4000"),
                    os.environ["LITELLM_KEY"])
    except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
        print(f"Jev gate could not decide: {exc}")
        if undefined:
            print("BLOCKED anyway: undefined Flux variables " + ", ".join(undefined))
            return 1
        return 2
    blocked, scores = decide(list(resp.get("answers") or []), args.threshold)
    blocked = blocked or bool(undefined)
    if args.json:
        print(json.dumps({"blocked": blocked, "scores": scores, "undefined_variables": undefined,
                          "usage": resp.get("usage")}))
    else:
        print(f"Jev gate (threshold {args.threshold}): {'BLOCKED' if blocked else 'pass'}")
        for item in undefined:
            print(f"  BLOCK  undefined Flux variable (not escaped as $${{...}}): {item}")
        for k, v in sorted(scores.items(), key=lambda kv: -kv[1]):
            print(f"  {'BLOCK' if v >= args.threshold else '     '} {v:.2f}  {k}")
    return 1 if blocked else 0


if __name__ == "__main__":
    sys.exit(main())
