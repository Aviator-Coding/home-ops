---
name: implement-task
description: "Read before writing code for an approved plan: worktree from origin/main, model choice, TDD for scripts/ci, the local gates CI Success runs, the PR body format, never merging, and the post-merge watch with revert-first. Workflow steps 3-7 of the agent-maintainable goal (§5)."
---

# Implement a task

Prerequisite: a plan from `plan-task` (approved by the owner if `risk:high`).

## Worktree

```bash
git fetch origin && git worktree add ../home-ops-<slug> -b <type>/<slug> origin/main
```

Never work on `main` or another task's branch. Fresh worktrees have no `kubeconfig`; agent
credentials live in `~/.kube/home-ops-agent` and `~/.talos/home-ops-agent` (read-only).

## Model (§6)

| Work | Model tier (Claude example) |
|---|---|
| Inventory, sweeps, CI statistics | cheap (Haiku) |
| Standard implementation | standard (Sonnet) |
| Judgment: ledger verdicts, risk paths, AI-review evaluation, any risk:high plan | strongest (Opus) |

One implementer at a time. Parallel helpers only for independent read-only inventory.
A harness without subagents does the work itself at the strongest model it has.

## Build

- **Tests first** when the change has a `scripts/ci` contract: write or extend the test, see
  it fail, then implement. A new test is advisory unless it guards data loss or an outage
  (`# ci: gate`, owner decision; `ci-test-gate-test.py` pins the set). Network-calling
  tests get `# ci: nightly`.
- **Byte-sensitive files.** configMapGenerator/secretGenerator inputs (`resources/`,
  `config/`) are embedded byte-for-byte: any change restarts the app (Hermes uncleanly).
- **Executable files:** `core.filemode` is on; new scripts need
  `git update-index --chmod=+x <file>`.
- **Commits** follow `.commitlintrc.yaml`; pre-commit hooks always run (never `--no-verify`).
- **Deleting a test or skill text:** ledger row first (`plan-task`, step 4).

## Local gates (what CI Success checks)

Needs `pyyaml` and `boto3`, plus kubectl, kustomize, promtool and jq from mise.

```bash
for f in $(grep -lx '# ci: gate' scripts/ci/*-test.py); do python3 "$f" || echo "FAILED $f"; done
for f in docs-budget doc-links functional-comments-guard; do python3 "scripts/ci/$f-test.py" || echo "FAILED $f"; done
mise exec -- pre-commit run --all-files   # yamllint, actionlint, zizmor, shellcheck, yamlfmt
mise exec -- yamllint -s .
# if kubernetes/** changed:
mise exec -- flate test all --path kubernetes/clusters/main --no-progress
for k in hr ks; do mise exec -- flate diff "$k" --path kubernetes/clusters/main --base origin/main --no-progress; done
```

CI also runs path-filtered validate jobs (talos, versions, bootstrap, renovate-config,
terraform); for those paths run the matching `scripts/ci/*.sh` (skill `github-ci`). Run the
advisory tests your paths touch too. Put the result summary in the PR body.

## Review, then PR

1. Run `review-pr` (an independent reviewer). Fix what it finds; re-run gates.
2. `git push -u origin <branch>` and `gh pr create` with these sections: **Summary**,
   **Tier**, **Verification** (commands and results), **Independent review** (verdict and
   what was fixed), **Rollback**, and **Owner steps** if any. Link the issue with `Refs #n`,
   not `Fixes`: the issue closes only after the post-merge watch.
3. **Wait for checks.** `gh pr checks <n>` until `CI Success` and the AI review report. A red
   check is yours to fix (skill `github-ci`); a flaky ARC check gets one rerun.
4. **Never run `gh pr merge`** (the Claude Code guard blocks it; other harnesses must not
   either). The tier decides who merges.
5. Need to prove CI goes red? Use a separate draft PR titled "DO NOT MERGE" and close it
   yourself; never push a breaking commit to a PR the owner may merge.
6. Update a pushed branch with `git merge origin/main`, never rebase plus force-push.

## After merge

Watch the affected objects until healthy (read-only):

```bash
flux get ks -A | grep -v True; flux get hr -A | grep -v True; flux get sources all -A | grep -v True
kubectl -n <ns> get pods -o wide
```

If the merge broke something, open a **revert PR first** (it counts toward the 2-PR
limit, and a `risk:high` revert still needs the owner's merge); a forward fix waits until
the revert merges. Then close the issue, and put the goal checkbox tick and any §8
evidence row in a later `risk:low` docs PR.
