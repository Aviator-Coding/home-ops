# Knowledge ledger

One row per skill section or `scripts/ci` test that the
[agent-maintainable goal](2026-10-30-agent-maintainable-home-ops.md) removes or rewrites
(task 3.1 inventories them, task 3.2 assigns verdicts).

Verdicts:

- `enforce`: becomes a linter rule or one of the kept tests (name it in "Lands in").
- `keep`: stays as prose in a subsystem skill or `AGENTS.md` (name the file).
- `archive`: dropped; git history is the record. Never allowed when "Prevents" is data
  loss or an outage.

| Source | Fact or invariant | Prevents | Verdict | Lands in |
|---|---|---|---|---|
| `scripts/ci/validate-contention-test.py` `jobs_still_on_home_ops_runner` | every validate.yaml job runs on the ARC scale set | neither (runner placement) | archive | — (inverted by `ci-success-workflow-test.py`: the gate runs on `ubuntu-latest`) |
| `scripts/ci/validate-contention-test.py` concurrency part of `no_workflow_level_job_serialization` | validate.yaml's own concurrency cancels in progress | neither | enforce | `workflow-hardening-test.py` (now checks `ci.yaml`, which owns concurrency) |
