---
name: review-pr
description: "Read before reviewing a PR, your own before opening it or anyone's: a fresh reviewer that sees only the issue and the diff, the review focus per tier, severities, the verdict, and how to act on findings. Workflow step 5 of the agent-maintainable goal (§5, §6)."
---

# Review a PR

The reviewer must not be the session that wrote the change. Spawn a fresh agent: Sonnet,
or Opus for `risk:high`. Give it **only** the task (the issue text) and how to see the
diff (`git diff origin/main...HEAD` in the worktree, or `gh pr diff <n>`), plus pointers to
AGENTS.md and the subsystem skills. Do not give it your conclusions or your test results;
an agent handed a hypothesis tends to confirm it.

## Brief template

```text
Independent review. Do not edit, commit, push, comment, or run cluster writes.
Worktree: <path> (branch <b>). Diff: git -C <path> diff origin/main...HEAD.
TASK: <issue text>.
Check in particular: <3-6 concrete risks for this change>.
Allowed: read files, run scripts/ci tests, linters, flate, read-only kubectl/gh.
Report: findings (blocking / should-fix / nit, file:line, concrete scenario, fix),
then APPROVE or REQUEST_CHANGES. Under N words. Say what you ran vs inferred.
```

## Focus by change type

- **Manifests:** rendered output (`flate diff hr|ks`), restart risk (changed ConfigMap
  hash, chart digest), the subsystem skill's tripwires, healthChecks and `wait`.
- **Workflows:** a failed or cancelled inner job must not go green; fork and
  `pull_request_target` safety; token scopes; `${{ }}` only via `env:`; the job must run
  while the cluster is down if it is in `CI Success`.
- **Guards and classifiers:** try to bypass them; list false positives on common reads.
- **Tests:** the test fails before the fix and passes after; no network on PRs; no
  hard-coded canary values that break on the next Renovate bump.

## Acting on findings

- Blocking and should-fix: fix, re-run the gates, say in the PR body what changed.
- Disagree with a finding? Verify it (run it); if it is wrong, say why in the PR.
- Nits: fix the cheap ones. A finding that needs an owner decision goes in the PR body
  under "Owner decisions".
- Re-review only if a fix changed behaviour substantially.
