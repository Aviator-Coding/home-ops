# Goal: agent-maintainable home-ops

Status: **approved by the owner 2026-10-30** · Started 2026-10-30 · Tracking: the checklists in this file

## 1. Task

Turn home-ops into a repo where agents do most of the work and the owner approves it.
Today that agent is a local Claude Code session; later it is Hermes. Required CI and an
independent review stop mistakes before `main`, and the owner's involvement scales with
the risk of each change.

What a normal week looks like when this is done:

1. The owner writes or approves a few GitHub issues.
2. A local agent session takes one issue and posts a plan as an issue comment.
   `risk:high` work waits for the owner's approval of that plan.
3. The agent works in its own worktree and opens one small PR.
4. The required `CI Success` check and the AI review run.
5. `risk:low` PRs merge themselves. `risk:normal` PRs wait for the owner's merge.
   `risk:high` PRs get the owner's review, then merge.
6. Renovate merges patch and digest updates itself. The owner sees only grouped minor
   and major updates.

## 2. Baseline (measured 2026-10-30)

| Area | Measured |
|---|---|
| Required checks on `main` | `Labeler - Labeler` only; ruleset bypass `RepositoryRole 5` (admin), mode `always` |
| CI health, October PR runs | Validate failed 111/192, Flate failed 48/192; 12 of 83 owner PRs merged with a failing check |
| Current red cause | `ghcr.io/home-operations/charts-mirror` returns 403 for cilium, openebs, intel-device-plugins-gpu, intel-device-plugins-operator; the same four OCIRepositories are not Ready in the cluster |
| Custom tests | 92 `scripts/ci/*-test.py`, ~41k lines; 11 pin a finished migration or a retired app; 10 call the network (measured 2026-10-31: Opus code read plus a no-network sandbox run; 3 fail offline, the rest skip or fall back but still call out when online) |
| Renovate | 26 open PRs (oldest 2026-10-18); the owner hand-merged 47 of 131 in October |
| Agent docs | `AGENTS.md` 159 lines; 27 skills, ~11.5k lines with references; 46 `PR #` citations inside skills |
| Skills loading | `.git/config` has `core.symlinks = false`, so `.claude/skills` checks out as a text file; in the main checkout it was replaced by a directory holding only `image-generation` |
| AI review | Advisory, local model, 0 failures in 192 runs; posts `APPROVE` |
| Planning | Gitignored `.claude/PRPs/` and `.claude/tasks/`, last touched 2026-05-31 and 2026-10-18 |
| Branches | 86 remote branches, 32 older than 14 days |

Reference repos compared: onedr0p/home-ops, bjw-s-labs/home-ops, buroa/k8s-gitops,
szinn/k8s-homelab, joryirving/home-ops. Most require one combined job with no bypass,
validate with flate plus standard linters, keep no custom test suite, automerge Renovate
digests and patches, and keep 1 to 3 skills.

## 3. Boundaries

### Never without the owner's explicit go-ahead at that moment

- A cluster write: node reboot, `talos apply` or `upgrade`, `just bootstrap`, `tofu apply`
  or `destroy`, `ceph auth`, a live HelmRelease edit, `flux suspend` or `resume`. Agents
  may read the cluster.
- A change to the GitHub ruleset, labels or repo settings through `gh api`. The repo is
  public and the change is live immediately.
- Retiring an app, or changing a backup engine or a claim's backup. Each needs its own
  approved issue.
- Merging a change to `.renovaterc.json5` or `.renovate/**`. The in-cluster Renovate
  CronJob is suspended first (AGENTS.md tripwire).
- Running `git push --force`, or deleting a branch that has an open PR.

### Always

- One task, one branch from `origin/main` in its own worktree, one PR. Nothing is
  pushed to `main` directly.
- Pre-commit hooks run. No plaintext secrets.
- Before skill text or a `scripts/ci` test is deleted, it gets a row in
  [`knowledge-ledger.md`](knowledge-ledger.md) with a verdict: `enforce` (becomes a
  check), `keep` (stays as prose), or `archive` (git history is the record). Rows that
  prevent silent data loss or an outage cannot be `archive`.
- Rules live where any agent can read them: `AGENTS.md`, `.agents/`, issue templates,
  labels and CI. Nothing in the workflow depends on Claude-only config, so Hermes can
  run the same loop.
- At most 2 open agent PRs at a time. The next task starts only after one merges or
  closes.
- The running stack stays as it is.

### Enforced, not just written

Every "never" above has a technical control (task 0.4), so a mistaken agent is stopped
rather than trusted:

- Agents get a read-only kubeconfig: a ServiceAccount bound to `view`. The admin
  kubeconfig stays with the owner.
- Agent permissions allow only read-only `gh` commands. A deny rule or hook blocks
  `gh pr merge`, `gh api -X PUT|PATCH|DELETE`, `git push --force`, `talosctl
  apply|upgrade|reboot|reset`, `tofu apply|destroy`, and `kubectl
  apply|delete|patch|edit|replace`.
- Hermes gets the same limits from its own ServiceAccount and a fine-grained GitHub
  token.

### Break-glass

The required check must never stop the owner from fixing a broken cluster. When
`CI Success` cannot report (GitHub outage, broken workflow), the owner:

1. Sets the ruleset to `disabled` (`gh api -X PUT repos/Aviator-Coding/home-ops/rulesets/21250320`
   with `enforcement: disabled`).
2. Merges the fix.
3. Sets enforcement back to `active`.
4. Adds an evidence-log row (§8) saying why.

Agents never do this.

### Out of scope

New apps, architecture changes, and setting up Hermes itself. This goal only makes the
repo ready for Hermes to use.

## 4. Definition of done

All nine criteria must hold.

| # | Criterion | Check |
|---|---|---|
| D1 | `CI Success` is the only required status check on `main`, and the bypass list is empty | `gh api repos/Aviator-Coding/home-ops/rulesets/21250320` |
| D2 | At least 95% of PR runs of `CI Success` pass over 14 consecutive days, and no `scripts/ci` test that runs on PRs makes a network call (Flate and Image Pull are the only PR jobs that reach registries) | `gh run list` export; grep of the PR test set |
| D3 | Renovate merges patch and digest updates itself, and no more than 5 Renovate PRs are open on any day of a 14-day window | daily `gh pr list --author app/mortyops` count |
| D4 | `AGENTS.md` ≤ 80 lines; ≤ 4 workflow skills; ≤ 8 subsystem skills; ≤ 15 `scripts/ci/*-test.py`, each naming the data loss or outage it prevents in its docstring | `wc -l`, `ls`, docstring grep |
| D5 | An issue template with goal, risk tier, rollback and verification fields; `risk:low`, `risk:normal` and `risk:high` labels; `.claude/PRPs` and `.claude/tasks` gone (triaged per task 2.5) | repo files, `gh label list` |
| D6 | The AI review fails its check on at least 4 of 5 seeded known-bad PRs, with no more than 1 false failure on 5 known-good PRs | seeded-PR results recorded in §8 |
| D7 | Three consecutive non-Renovate issues, at least one `risk:high`, go issue → plan → PR → merge through the workflow with no manual fix-up commits | the issues linked in §8 |
| D8 | Every skill section and test removed under this goal has a ledger row | ledger rows compared with `git log --diff-filter=D` |
| D9 | Any Flux object (sources, Kustomizations, HelmReleases) not Ready for over 1h raises an alert that reaches the owner, verified by a deliberately broken test source; `CI Success` and everything it waits for run on GitHub-hosted runners | test-alert notification; `runs-on` grep |

## 5. Workflow (target)

### Risk tiers

A `risk-tier` CI job sets the tier from the changed paths, so an agent cannot mislabel
its own PR. The highest-tier path in a PR wins.

| Tier | Paths | Gate |
|---|---|---|
| `risk:high` | `talos/**`, `bootstrap/**`, `terraform/**`, `kubernetes/clusters/**`, `kubernetes/apps/*/flux-system/**`, `kubernetes/apps/*/kube-system/**`, `kubernetes/apps/*/rook-ceph/**`, `kubernetes/apps/*/network/**`, `kubernetes/apps/*/security/**`, `kubernetes/apps/*/cert-manager/**`, `kubernetes/apps/*/system-upgrade/**`, `kubernetes/apps/*/renovate/**`, `kubernetes/components/{kopiur,volsync}/**`, `kubernetes/apps/base/system/kopiur/**`, `.github/workflows/**`, `.renovaterc.json5`, `.renovate/**` | the owner approves the plan, then the PR |
| `risk:normal` | everything else under `kubernetes/**`, `scripts/**`, `.taskfiles/**`, `.justfile`, `*/mod.just`, `.mise.toml` | the owner merges the PR |
| `risk:low` | `docs/**`, `.agents/**`, `*.md` outside `kubernetes/**`, plus Renovate digest and patch updates outside the `risk:high` paths | merges itself once `CI Success` is green |

Agents never merge. The 0.4 controls block `gh pr merge` outright. A `risk:low` PR merges
because the `risk-tier` job turns on GitHub auto-merge for it, and GitHub merges once
`CI Success` is green. Every other PR is merged by the owner. Agents use the owner's
GitHub token, so the owner cannot formally approve an agent's PR yet. Task 2.6 moves
agent PRs to a separate bot identity. `CI Success` then requires an approving review
from `Aviator-Coding` on `risk:normal` and `risk:high` PRs.

### The loop each agent session runs

1. Read this file and the issue, then classify the risk tier from the paths the issue
   will touch.
2. Post a plan comment on the issue: approach, files, rollback, verification commands.
   For `risk:high`, stop until the owner approves it.
3. Create a worktree from `origin/main` and implement with the matching subagent
   model (§6).
4. Run the local gates, the same set `CI Success` runs, and attach their output to
   the PR.
5. Run an independent review subagent that sees only the issue and the diff. Fix what
   it finds.
6. Open the PR linked to the issue. The tier gate decides who merges; the agent never
   does.
7. After the merge, watch the affected Flux Kustomizations and HelmReleases until they
   reconcile and the workload is healthy. Then tick the task's checkbox and close the
   issue. If the merge broke something, open a revert PR first; a forward fix waits
   until the revert has merged.

## 6. Subagent model selection

| Work | Model | Examples |
|---|---|---|
| Mechanical inventory and sweeps | Haiku | branch lists, test and skill inventory, network-call scan, CI statistics |
| Standard implementation | Sonnet | chart source moves, the `CI Success` workflow, linter setup, Renovate rules, issue templates |
| Judgment | Opus | ledger verdicts, skill rebuild, risk-tier paths, AI-review evaluation, any `risk:high` plan |
| Independent PR review | Sonnet; Opus for `risk:high` | a fresh agent given only the issue and the diff |

The coordinating session checks each subagent's result against this file's exit checks
before it reaches the owner. It runs one implementer at a time. Parallel subagents are
only for independent Haiku inventory work.

## 7. Phases

Each phase starts once the previous phase's exit check holds, except P2, which the
owner started early on 2026-10-31. P4's Renovate work, and
P3's `scripts/ci` test triage (3.1 to 3.3 for tests), can start once P1's exit check
holds. Target dates are for planning; a missed date gets a dated
row in §8 and a new date, not silent drift.

### P0 Stabilise (target 2026-11-06)

- [x] 0.0 Merge or drop `d2fa343c` (the image script's OpenRouter key-limit note, on
      `feat/image-generation-skill` but not on `main`), so P0 starts from a clean `main`.
- [x] 0.1 Point the cilium, openebs and both intel-device-plugins OCIRepositories at
      their upstream OCI registries (onedr0p uses `oci://quay.io/cilium/charts/cilium`;
      confirm each tag exists upstream before switching). Tier `risk:high`. In the same
      issue, list every remaining `home-operations/charts-mirror` source and Renovate
      community preset, and switch each one with an official registry.
- [x] 0.2 Set `core.symlinks = true` in the main checkout and restore the
      `.claude/skills` symlink there. The stray directory matches
      `.agents/skills/image-generation` exactly.
- [x] 0.3 Find what wrote the stray `image-generation` directory under `.claude/skills` on
      2026-10-30 at 22:39, and stop it from recurring.
- [ ] 0.4 Put the §3 technical controls in place: an agent kubeconfig bound to `view`,
      read-only `gh` allow rules replacing `Bash(gh api *)` and `Bash(gh pr *)` in
      `.claude/settings.local.json`, and the deny rules or hook. Prove each block with
      one denied command recorded in §8.
- [ ] 0.5 Widen `FluxResourceNotReadyTooLong`
      (`flux-system/flux-instance/app/prometheusrule.yaml`) from `HelmRelease|Kustomization`
      to every Flux kind, sources included. Confirm the alert reaches the owner.

Exit: Flate and Validate are green on a fresh PR. `flux get sources oci -A` shows 0
sources not Ready. The 0.4 denials and a 0.5 test alert are recorded in §8.

### P1 Enforce (target 2026-11-15)

- [x] 1.1 Add a `CI Success` workflow job that `needs` Flate and Validate and fails if any
      of them failed. Skipped jobs pass. `CI Success`, Flate and Validate move to
      `ubuntu-latest` so they run when the cluster is down. Image Pull and the AI review
      stay on the cluster runner and stay advisory. Validate's required `python-tests`
      runs only the tests marked `# ci: gate`: at most 15, each guarding data loss or an
      outage and naming it in its docstring (Opus picks the set, the owner approves it).
- [x] 1.2 Every unmarked test leaves the PR gate. Network-calling tests run nightly and
      open an issue on failure. The rest run in an advisory PR job that never blocks,
      until P3 keeps (`enforce`) or deletes each one.
- [x] 1.3 Add `.editorconfig`, a YAML formatter, and yamllint, actionlint, zizmor and
      shellcheck to pre-commit and to `CI Success`. Fix or explicitly ignore the
      existing findings.
- [ ] 1.4 With the owner's go-ahead, replace `Labeler - Labeler` with `CI Success` as the
      required check and empty the bypass list. Before enabling it, the owner does one
      dry run of the §3 break-glass procedure.
- [x] 1.5 Add a daily scheduled workflow that records the D2 pass rate and the D3
      open-Renovate count in a pinned issue or a committed CSV, so the 14-day windows
      measure themselves.

Exit: D1 holds, and `CI Success` passes on at least 95% of PR runs for 7 days.

### P2 Workflow (target 2026-11-30)

- [x] 2.1 Add an issue template (goal, risk tier, rollback, verification) and the three
      `risk:*` labels.
- [x] 2.2 Add the `risk-tier` CI job from §5. It labels the PR with its tier and turns on
      auto-merge only for `risk:low`.
- [x] 2.3 Write four workflow skills, `plan-task`, `implement-task`, `review-pr` and
      `renovate-review`, which encode §5 and §6.
- [x] 2.4 Rebuild the AI review so it reports specific findings and fails its check on a
      blocking one. Seed 5 known-bad and 5 known-good PRs and record the results in §8.
- [x] 2.5 Triage `.claude/PRPs` and `.claude/tasks` with the owner. Live plans become a
      tracked `docs/goals/` file, or stay local in `.private/` if they must not be
      public. Stale plans are deleted.
- [ ] 2.6 Give agent PRs a bot identity and require the owner's approval on
      `risk:normal` and `risk:high` PRs.

Exit: D5 and D6 hold.

### P3 Lean knowledge (target 2026-12-20, test triage 2026-12-06; each task runs through the P2 loop)

- [x] 3.1 Inventory every skill section and every `scripts/ci` test into
      [`knowledge-ledger.md`](knowledge-ledger.md) (Haiku).
- [ ] 3.2 Assign every ledger row a verdict (Opus). Work one skill per PR: its ledger
      rows, its rewrite and its test changes together, so each owner review of
      `archive` rows stays small.
- [ ] 3.3 Turn the `enforce` rows into linter rules or the ≤ 15 kept tests.
- [ ] 3.4 Rebuild the skills to ≤ 8 subsystem skills plus the P2 workflow skills, and
      trim `AGENTS.md` to a map plus the data-loss and outage tripwires.
- [ ] 3.5 Delete the 2025 `.github/copilot/instructions.md` and anything else the
      ledger archives.

Exit: D4 and D8 hold.

### P4 Autonomy (target 2026-12-31)

- [ ] 4.1 Turn on Renovate automerge for digest and patch updates outside the
      `risk:high` paths, requiring passing checks (`ignoreTests: false`). Group
      Kubernetes, Talos, Rook-Ceph, Cilium and Flux. Suspend the in-cluster CronJob
      while this merges.
- [ ] 4.2 Clear the open Renovate backlog. Each major bump gets its own issue.
- [ ] 4.3 Delete remote branches older than 14 days that have no open PR. The owner
      approves the list first.
- [ ] 4.4 Check that Hermes can run the §5 loop from the repo files alone. Record any gaps
      as issues.
- [ ] 4.5 Run D7: three consecutive issues through the workflow.

Exit: D2, D3, D7 and D9 hold. With all nine criteria holding, this goal is done.

## 8. Evidence log

Add a dated row whenever an exit check or a definition-of-done criterion is measured.

| Date | Criterion | Result | Link |
|---|---|---|---|
| 2026-10-30 | Baseline | §2 | this file |
| 2026-10-30 | Goal approved | owner approved this file in-session; P0 started | this file |
| 2026-10-30 | 0.2 | `core.symlinks = true`; `.claude/skills -> ../.agents/skills` and `CLAUDE.md -> AGENTS.md` restored in both worktrees; `docs-budget-test.py` 1148 passed, 0 failed; a live session's skill list shows the 27 repo skills | local only |
| 2026-10-30 | 0.3 | Root cause: `.git/config` held `core.symlinks = false` and `core.filemode = false`, the defaults of a clone made on Windows (`settings.local.json` still has a `PowerShell(...)` rule), so `.claude/skills` checked out as a 17-byte text file. The owner's shell removed it (`rm skills` in zsh history), and the directory with a copy of `image-generation` appeared at 22:39:40–44 outside any agent: no tool call in any session transcript between 22:37:09 (session `c082f6f6`) and 22:40:24 (session `f0936570`, slash commands only). Recurrence is stopped by `core.symlinks = true`, and `docs-budget-test.py` fails if the symlink regresses. No `AGENTS.md` change | local only |
| 2026-10-31 | 0.0 | `d2fa343c` landed on `main` as #2146 | #2146 |
| 2026-10-31 | 0.1 (remaining sources) | The 8 other `charts-mirror` sources (headlamp, external-dns ×2, tailscale-operator, volsync, descheduler, metrics-server, keda) all pull fine from the mirror. None has an anonymously pullable official OCI chart: ghcr.io and quay.io return 403, and registry.k8s.io returns not found. Each has an official HTTPS Helm repo (kubernetes-sigs.github.io/{headlamp,external-dns,descheduler,metrics-server}, pkgs.tailscale.com/helmcharts, backube.github.io/helm-charts, kedacore.github.io/charts). Per the plan they stay on the mirror; `FluxResourceNotReadyTooLong` (task 0.5) will catch the next 403. Renovate presets (`Aviator-Coding/mortyops`, local `.renovate/`) are not community presets: no change | fix/upstream-charts PR |
| 2026-10-31 | Plan change | Owner decision: gate on a few tests now. Evidence: in 59 of the last 60 Validate PR runs, `python-tests` was the only failing job (talos, bootstrap, terraform, renovate-config and docs-guards never failed). Recent causes were brittleness, not catches: the plex tag canary (#2149), the charts-mirror 403 and a factory.talos.dev SSL error. Tasks 1.1 and 1.2 now gate only `# ci: gate` tests; P3 test triage moves up to follow P1 | this file |
| 2026-10-31 | Plan change | Owner decision: start P2 now, in parallel with the P1 exit window (D1 and 7 days). P3 and P4 stay gated | this file |
| 2026-10-31 | 0.1 | #2147 merged. `flux get sources oci -A` shows 0 not Ready (cilium `1.18.6@sha256:e6af2ea4`, openebs `4.4.0@sha256:5aaf304b`); HelmRepository `system/intel` Ready; all 4 HelmReleases Ready on the same versions; `kube-system/ds/cilium` generation 13 before and after (3/3 ready); GpuDevicePlugin `xe` 3/3 | #2147 |
| 2026-10-31 | 0.5 / D9 | #2148 merged first, so the 4 real charts-mirror OCIRepositories became the test alert: `FluxResourceNotReadyTooLong` pending at 00:37, firing at 01:07 for all 4, resolved after #2147. Alertmanager routes it to `pushover-warning`; Pushover sent 17 notifications in the last 90m with 0 failures. Still open: the owner's confirmation that the notification arrived | #2148 |
| 2026-10-31 | 1.1 gate set | The owner approved the 12-test gate list from the Opus classification (gate 12, nightly 11, advisory 69). With the network denied, 11 of 12 pass offline locally (`volsync-retired-expiry` needs `boto3`, which the gate job installs) | Task 7b PR |
| 2026-10-31 | 1.2 | 10 network-calling tests marked `# ci: nightly` and moved to `nightly-tests.yaml`; a sandbox calibration (`igpu-xe-allowids` fails offline, `docs-budget` passes) found no other locally passing advisory test that needs the network | Task 9 PR |
| 2026-10-31 | 1.1 / P0 exit | First `CI Success` run (#2155, GitHub-hosted): Flate - Success and Validate - Success green, the 14-file gate green. `python-tests-advisory` failed while `Validate - Success` and `CI Success` stayed green, which proves `continue-on-error` inside a called workflow | #2155 |
| 2026-10-31 | 1.1 red proof | Draft #2158 removed `kind: DaemonSet` from mglru-disable: `Flate - Test` failed and `CI Success` went red. Closed, branch deleted | #2158 |
| 2026-10-31 | 1.2 | First manual `nightly-tests.yaml` run: only `litellm-sso-test.py` failed (live Authentik), and the workflow opened #2159 with the `ci-nightly` label | #2159 |
| 2026-10-31 | 0.4 (RBAC) | #2154 merged and reconciled. Impersonating `kube-system:agent-readonly`: create pods no, get secrets no, create pods/exec no, list helmreleases yes, list nodes yes. Still open: the owner's credential migration and mint, then the guard denial in a live session | #2154 |
| 2026-10-31 | Plan change / 3.1 | Owner decision: start P3.1 (inventory) and P4.2 (Renovate triage comments) early; both delete and merge nothing. 3.1 done: the ledger lists 98 tests, 121 skill sections, 235 skill tripwires, 114 reference files and 31 AGENTS.md tripwires (599 rows; 87 data loss, 24 outage), with verdicts empty for 3.2 | this PR |
| 2026-10-31 | 4.2 (triage, early) | All 31 open Renovate PRs have one recommendation comment (renovate-review format): 24 merge (some after a pre-flight), 7 hold, 0 close. Major bumps each have an issue: #2188 node 26 (PRs 2115, 2116), #2189 ceph v21 (needs Rook 1.21 first, one-way), #2190 n8n 3.0 (pre-release), #2191 ha-mcp 9, #2192 Node Exporter dashboard 46. Holds also cover #2114 and #2087 (each an unattended tuppr roll) and #1991 (python 3.15, no fastuuid wheel). The owner merges | #2188-#2192 |
| 2026-10-31 | 1.3 / 1.5 | Linters gate `CI Success` (#2162: actionlint 132→0, zizmor 21→0, yamllint 18→0, shellcheck 6→0); repo-wide yamlfmt with empty `flate diff` (#2163). Daily metrics in issue #2161 (first row: 75.0%, 4 runs, 28 open Renovate PRs) | #2162, #2163, #2157 |
| 2026-10-31 | 2.1-2.3 | Issue form and `risk:*` labels (#2164); `risk-tier.yaml` labels PRs from paths with main's classifier, auto-merge only for risk:low once `CI Success` is required (#2165; first live run labelled #2166 risk:normal); four workflow skills (#2166) | #2164, #2165, #2166 |
| 2026-10-31 | D6 baseline (local model) | `pr-review-local` with the blocker rules (#2167): **2 of 5 bad caught, 1 of 5 good falsely failed: D6 not met.** Caught: healthChecks on a HelmRelease (#2172), CCNP default-deny (#2177). Missed: kopiur `wait: true` (#2168, include outside the diff), literal `${HOME}` (#2175, model called it a Flux variable), no fork guard on an in-cluster workflow (#2179, explicit rule). False failure: subtractive CCNP (#2178, invented selector). Good passes: #2169, #2173, #2176, #2180. Next: a stronger decision model (owner choosing Jev or Claude) | #2168-#2180 |
| 2026-10-31 | D6 | **Holds.** The local model failed (2 of 5, 1 false), so the owner chose Jev as a decision gate (#2183): one predicate per blocker rule on LiteLLM `/v1/decisions`, with the full changed files, the defined Flux variables and unmatched `${NAME}` hints. Offline on the 10 seed diffs (two runs, +-0.03): bad 0.98 / 0.97 / 0.82 / 0.91 / 0.93, good at most 0.21, threshold 0.6, so **5 of 5 caught, 0 of 5 false**. Live after merge: #2184 (bad-1) BLOCKED at 0.98, #2185 (good-4, the local model's false failure) passed at 0.20 | #2183, #2184, #2185 |
| 2026-10-31 | 2.5 / D5 | Owner triage: the 12 finished or stale files under `.claude/PRPs` and `.claude/tasks` were deleted; the live media-platform PRD and the open Phase 5/6 plans moved to the local-only `.private/media-platform/`. Both directories are gone. With the issue form and `risk:*` labels (#2164), **D5 holds** | local only |
| 2026-10-31 | P2 exit | D5 and D6 hold. Open: 2.6 (bot identity, owner) | this file |
