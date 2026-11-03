# scripts/ci

Each `*-test.py` is a standalone script. The docstring is the invariant it
pins. CI globs the directory, so a new file needs no workflow edit.

Only files with the line `# ci: gate` right after the shebang block a PR
(`ci.yaml` `gate-tests`). There are at most 15, and each docstring has a `Prevents:` line naming
the data loss or outage it guards. Every other file runs in
`.github/workflows/tests.yaml` (nightly, on dispatch, and when `scripts/ci/**`
changes on main), which never blocks and reports to a `ci-nightly` issue.
`ci-test-gate-test.py` enforces the gate list. A new test is non-gate unless it
guards data loss or an outage, so run it locally before you push.

```bash
python3 scripts/ci/<name>-test.py
```

Shell gates `talos-validate.sh`, `version-consistency.sh` and
`tofu-validate.sh` are invoked by `.github/workflows/ci.yaml`. Read
each file's header for what a green run does not catch.

How the gates fail, the three pin shapes, and local setup: skill `github-ci`
([scripts-ci.md](../../.agents/skills/github-ci/references/scripts-ci.md)).

`docs-budget-test.py`, `doc-links-test.py` and
`functional-comments-guard-test.py` run only in `ci.yaml`'s `lint` job.
The functional-comment guard needs `DIFF_GUARD_BASE`.
