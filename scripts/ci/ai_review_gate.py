#!/usr/bin/env python3
"""Blocking gate for the AI PR review: ask Jev one yes/no question per blocker rule.

The local reviewer model writes the review text; it missed 3 of 5 seeded blockers and
invented details on a good PR (goal §8, D6 baseline). Jev (`jev`, TypeSafe's decision
model behind LiteLLM's POST /v1/decisions) answers each `predicate` question with a
probability of "yes"; the gate fails when any probability reaches the threshold.

The request is LiteLLM's OpenAI-shaped body (`input` text + `predicate` questions). The
input holds the diff, the full post-change text of each changed file (the diff alone hid a
kopiur include), the Flux variables the repo defines, and `candidate_undefined_variables`:
added `${NAME}` tokens a regex could not match to a known variable. Those are hints for Jev,
never a verdict on their own: Flux variables also come from substituteFrom Secrets this
script cannot see, and Kustomizations without postBuild never substitute.

    ai_review_gate.py --base <sha> --head <sha> [--threshold 0.6] [--dry-run] [--json]

Env: LITELLM_URL (default http://litellm.ai.svc.cluster.local:4000), LITELLM_KEY.
Exit 0 pass, 1 blocked, 2 could not decide (any error or a missing answer; the workflow
treats 2 as a pass with a notice). Stdlib only. Tested by scripts/ci/ai-review-gate-test.py.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.request
from functools import lru_cache
from pathlib import Path

MAX_INPUT_BYTES = 30000  # Jev's accurate mode is about 32 KB of text
MAX_FILE_BYTES = 8000
# Calibrated 2026-10-31 on the 10 D6 seeds (goal §8). Re-calibrate when the jev version changes.
THRESHOLD = 0.6
ADDED_VAR = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")
SCOPE = " Only judge Kubernetes manifests and GitHub workflows; ignore docs, comments and tests."

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
        "instructions": "Does this change add a literal `${NAME}` to a Flux-substituted manifest (not a "
                        "comment) where NAME is not in state.flux_variables, it is not escaped as "
                        "`$${NAME}`, and the resource is not annotated "
                        "`kustomize.toolkit.fluxcd.io/substitute: disabled`? "
                        "state.candidate_undefined_variables lists tokens a script could not match.",
        "criteria": {"true": "Such an unescaped, undefined ${NAME} is added where Flux substitutes it.",
                     "false": "Every added ${NAME} is defined, escaped, a comment, or not substituted."},
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
                        "key, kubeconfig) to a manifest or workflow, rather than an ExternalSecret "
                        "reference or template?",
        "criteria": {"true": "A real secret value is added in plain text.",
                     "false": "No secret values, only references, templates, placeholders or public data."},
    },
    "other_tripwire": {
        "instructions": "Does this change set, in a manifest or workflow: a HelmRelease `postRenderer` "
                        "running bash; `sessions.vacuum_after_prune: true` for Hermes; a "
                        "generic-device-plugin `mountPath` renaming a DRM device node; "
                        "`security.cephx.csi: aes256k` or a lower `keyGeneration`; or a step that "
                        "runs `tofu apply` automatically?",
        "criteria": {"true": "One of those is set.", "false": "None of those is set."},
    },
}


def git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout


@lru_cache(maxsize=256)
def show(rev: str, path: str) -> str:
    try:
        return git("show", f"{rev}:{path}")
    except subprocess.CalledProcessError:
        return ""


def overlays(root: Path) -> list[tuple[str, bool]]:
    """(spec.path prefix, has postBuild) for every Flux Kustomization overlay."""
    out = []
    for path in (root / "kubernetes" / "apps" / "main").rglob("*.yaml"):
        for doc in path.read_text(errors="replace").split("\n---"):
            m = re.search(r"\n  path: [\"']?\./([^\"'\s]+)", doc)
            if m and "kind: Kustomization" in doc:
                out.append((m.group(1).rstrip("/") + "/", "postBuild:" in doc))
    return out


def flux_variables(root: Path) -> list[str]:
    """Known Flux variables: SECRET_DOMAIN (cluster-secrets), every overlay's postBuild.substitute
    key, and the keys of repo ConfigMaps / ExternalSecret templates named in a substituteFrom."""
    names = {"SECRET_DOMAIN"}
    sources: set[str] = set()
    for path in (root / "kubernetes" / "apps" / "main").rglob("*.yaml"):
        text = path.read_text(errors="replace")
        for block in re.findall(r"\n(\s+)substitute:\n((?:\1  +[A-Z0-9_]+:.*\n)+)", text):
            names.update(re.findall(r"^\s+([A-Z0-9_]+):", block[1], re.M))
        sources.update(re.findall(r"- name: ([\w.-]+)\n\s+kind: (?:ConfigMap|Secret)", text))
        sources.update(re.findall(r"- kind: (?:ConfigMap|Secret)\n\s+name: ([\w.-]+)", text))
    sources.discard("cluster-secrets")
    for path in (root / "kubernetes").rglob("*.yaml"):
        text = path.read_text(errors="replace")
        if not any(re.search(rf"\n  name: {re.escape(s)}\n", text) for s in sources):
            continue
        if "kind: ConfigMap" in text or "kind: ExternalSecret" in text:
            names.update(re.findall(r"^\s+([A-Z][A-Z0-9_]+): ", text, re.M))
    return sorted(names)


def candidate_undefined_variables(diff: str, head: str, known: list[str],
                                  owners: list[tuple[str, bool]] | None = None) -> list[str]:
    """Added `${NAME}` in kubernetes/ YAML where NAME is not known, not `$$`-escaped, not in a
    comment, the file is not substitute-disabled, and its overlay has postBuild (no postBuild,
    no substitution). Hints only (see the module docstring)."""
    found, path = set(), ""
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[6:] if line.startswith("+++ b/") else ""
            continue
        if not line.startswith("+") or not path.startswith("kubernetes/"):
            continue
        if not path.endswith((".yaml", ".yml")) or line[1:].lstrip().startswith("#"):
            continue
        # Disabled on the file, or on its generated ConfigMap via a nearby kustomization.yaml.
        nearby = [path, *(str(Path(path).parents[i] / "kustomization.yaml") for i in (0, 1))]
        if any("kustomize.toolkit.fluxcd.io/substitute: disabled" in show(head, f) for f in nearby):
            continue
        owner = [pb for prefix, pb in (owners or []) if path.startswith(prefix)]
        if owners is not None and owner and not any(owner):
            continue
        found.update(f"{path}: ${{{n}}}" for n in ADDED_VAR.findall(line[1:]) if n not in known)
    return sorted(found)


def build_state(base: str, head: str, root: Path) -> dict:
    """Diff, changed files and variables, kept under MAX_INPUT_BYTES of serialised JSON."""
    rng = f"{base}...{head}"
    diff = git("diff", "--unified=5", "--irreversible-delete", rng)
    files = [f for f in git("diff", "--name-only", "-z", "--diff-filter=AMR", rng).split("\0") if f]
    known = flux_variables(root)
    state: dict = {
        "diff": diff,
        "changed_files": {},
        "flux_variables": known,
        "candidate_undefined_variables": candidate_undefined_variables(
            git("diff", "--unified=0", rng), head, known, overlays(root)),
    }

    def size() -> int:
        return len(json.dumps(state, indent=1))

    if size() > MAX_INPUT_BYTES:
        state["diff"] = diff[: max(0, len(diff) - (size() - MAX_INPUT_BYTES) - 200)] + "\n(diff truncated)"
    for f in files:
        state["changed_files"][f] = show(head, f)[:MAX_FILE_BYTES]
        if size() > MAX_INPUT_BYTES:
            state["changed_files"][f] = "(omitted: over the size budget)"
    return state


def request_body(state: dict) -> dict:
    """LiteLLM's OpenAI-shaped /v1/decisions body: `input` text and a list of predicates."""
    questions = [
        {"type": "predicate", "name": k,
         "instructions": f"{v['instructions']}{SCOPE} Yes: {v['criteria']['true']} "
                         f"No: {v['criteria']['false']}"}
        for k, v in QUESTIONS.items()
    ]
    return {"model": "jev", "input": json.dumps(state, indent=1), "questions": questions}


def decide(answers: list, threshold: float) -> tuple[bool | None, dict[str, float | None]]:
    """(blocked, scores). blocked is None (could not decide) when any question has no
    numeric answer, e.g. a refusal or a renamed field."""
    by_name = {a.get("name"): a for a in answers if isinstance(a, dict)}
    scores: dict[str, float | None] = {}
    for k in QUESTIONS:
        p = (by_name.get(k) or {}).get("probability")
        scores[k] = float(p) if isinstance(p, (int, float)) else None
    if any(v is None for v in scores.values()):
        return None, scores
    return any(v >= threshold for v in scores.values()), scores  # type: ignore[operator]


def call(body: dict, url: str, key: str, timeout: float = 60) -> dict:
    req = urllib.request.Request(
        f"{url.rstrip('/')}/v1/decisions", data=json.dumps(body).encode(), method="POST",
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    if not isinstance(data, dict):
        raise ValueError("decisions response is not an object")
    return data


def run(args: argparse.Namespace) -> int:
    state = build_state(args.base, args.head, Path.cwd())
    body = request_body(state)
    if args.dry_run:
        print(json.dumps(body, indent=2)[:4000])
        return 0
    resp = call(body, os.environ.get("LITELLM_URL", "http://litellm.ai.svc.cluster.local:4000"),
                os.environ["LITELLM_KEY"])
    blocked, scores = decide(list(resp.get("answers") or []), args.threshold)
    if args.json:
        print(json.dumps({"blocked": blocked, "scores": scores,
                          "candidates": state["candidate_undefined_variables"],
                          "usage": resp.get("usage")}))
    else:
        verdict = {True: "BLOCKED", False: "pass", None: "could not decide (missing answers)"}[blocked]
        print(f"Jev gate (threshold {args.threshold}): {verdict}")
        for k, v in sorted(scores.items(), key=lambda kv: -(kv[1] or 0)):
            mark = "BLOCK" if v is not None and v >= args.threshold else "     "
            print(f"  {mark} {'n/a ' if v is None else f'{v:.2f}'}  {k}")
        for item in state["candidate_undefined_variables"]:
            print(f"  hint: unmatched variable {item}")
    return {True: 1, False: 0, None: 2}[blocked]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--head", required=True)
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true", help="print the raw scores as JSON")
    args = ap.parse_args()
    try:
        return run(args)
    except Exception as exc:  # never "blocked" on our own failure: exit 2 is a notice and a pass
        print(f"Jev gate could not decide: {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
