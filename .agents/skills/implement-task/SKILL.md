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

| Work | Model |
|---|---|
| Inventory, sweeps, CI statistics | Haiku |
| Standard implementation | Sonnet |
| Judgment: ledger verdicts, risk paths, AI-review evaluation, any risk:high plan | Opus |

One implementer at a time. Parallel subagents only for independent read-only inventory.

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

## Local gates (the same set CI Success runs)

```bash
for f in $(grep -lx '# ci: gate' scripts/ci/*-test.py); do python3 "$f" || echo "FAILED $f"; done
mise exec -- pre-commit run --all-files          # yamllint, actionlint, zizmor, shellcheck, yamlfmt
mise exec -- flate test all --path kubernetes/clusters/main --no-progress   # if kubernetes/** changed
mise exec -- flate diff hr --path kubernetes/clusters/main --path-orig <origin/main checkout>/kubernetes/clusters/main --no-progress
```

Run the advisory tests your paths touch too. Attach the output summary to the PR.

## Review, then PR

1. Run `review-pr` (fresh reviewer, issue + diff only). Fix what it finds; re-run gates.
2. `git push -u origin <branch>` and `gh pr create` with these sections: **Summary**,
   **Tier**, **Verification** (commands and results), **Independent review** (verdict and
   what was fixed), **Rollback**, and **Owner steps** if any. Link the issue (`Fixes #n`).
3. **Never merge** (`gh pr merge` is blocked). The tier decides who merges.
4. Need to prove CI goes red? Use a separate draft PR titled "DO NOT MERGE" and close it
   yourself; never push a breaking commit to a PR the owner may merge.
5. Update a pushed branch with `git merge origin/main`, never rebase plus force-push.

## After merge

Watch the affected objects until healthy (read-only):

```bash
flux get ks -A | grep -v True; flux get hr -A | grep -v True; flux get sources all -A | grep -v True
kubectl -n <ns> get pods -o wide
```

If the merge broke something, open a **revert PR first**; a forward fix waits until the
revert merges. Then tick the goal checkbox, add a §8 evidence row if it measures a
criterion, and close the issue.
