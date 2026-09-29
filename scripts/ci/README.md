# scripts/ci

Each `*-test.py` is a standalone script. The docstring is the invariant it
pins. CI globs the directory, so a new file needs no workflow edit.

```bash
python3 scripts/ci/<name>-test.py
```

Shell gates `talos-validate.sh`, `version-consistency.sh` and
`tofu-validate.sh` are invoked by `.github/workflows/validate.yaml`. Read
each file's header for what a green run does not catch.

How the gates fail, the three pin shapes, and local setup: skill `github-ci`
([scripts-ci.md](../../.agents/skills/github-ci/references/scripts-ci.md)).

`docs-budget-test.py`, `doc-links-test.py` and
`functional-comments-guard-test.py` also run in the `docs-guards` job.
The functional-comment guard needs `DIFF_GUARD_BASE`.
