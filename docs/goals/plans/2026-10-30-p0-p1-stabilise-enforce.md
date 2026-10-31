# P0 Stabilise + P1 Enforce Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get `main` and the cluster green, make agents unable to write to the cluster or change repo settings, and make one GitHub-hosted `CI Success` check the only required gate on `main`.

**Architecture:**
- **Agent limits** are credential separation, which works for any agent, Hermes included:
  - a read-only kubeconfig (ServiceAccount bound to `view`);
  - an `os:reader` talosconfig;
  - a fine-grained GitHub token without Administration permission.

  Claude Code deny rules and a tested PreToolUse hook add a second layer.
- **CI:** `flate.yaml` and `validate.yaml` become reusable workflows called by a new `ci.yaml`, whose final job `CI Success` is the required check. Everything it waits for runs on `ubuntu-latest`.

**Tech Stack:**
- Cluster and GitOps: Flux v2 (OCIRepository, HelmRepository, HelmRelease), Kubernetes RBAC, Talos roles, PrometheusRule with promtool.
- CI and tooling: GitHub Actions reusable workflows, GitHub rulesets API, pre-commit, mise, Python 3 stdlib test scripts (`scripts/ci/*-test.py` convention).

**Spec:** [`../2026-10-30-agent-maintainable-home-ops.md`](../2026-10-30-agent-maintainable-home-ops.md) (§3 boundaries, §4 D1/D2/D9, §7 P0 and P1)

## Global Constraints

- One task, one branch from `origin/main` in its own worktree (`git worktree add ../home-ops-<slug> -b <type>/<slug> origin/main`), one PR. Never push to `main`.
- Commits follow `.commitlintrc.yaml`: `type(scope): description`. Pre-commit hooks must run (`task setup-dev-env` once per machine).
- Every new or edited manifest keeps its first-line `# yaml-language-server: $schema=...` comment, using `kubernetes-schemas.pages.dev`.
- Agents never merge, never run cluster writes, never change rulesets/labels/settings through `gh api`. Steps marked **OWNER** are run by the owner, or by an agent only after the owner's explicit go-ahead in that moment.
- Before deleting any skill text or `scripts/ci` test, add its row to `docs/goals/knowledge-ledger.md` (verdict `enforce` / `keep` / `archive`). Data-loss/outage rows are never `archive`.
- At most 2 open agent PRs at a time.
- Local gate before every PR: `for t in scripts/ci/*-test.py; do python3 "$t" || echo "FAILED $t"; done` (shows no `FAILED` except the known `.claude/skills` symlink failure until Task 4 lands) and `flate test all --path kubernetes/clusters/main --no-progress` when `kubernetes/**` changed.
- After merge: watch affected Flux objects until Ready (spec §5 step 7); a breaking merge gets a revert PR first.

### Model and tier per task

| Task | Spec item | Tier | Implementer | Reviewer |
|---|---|---|---|---|
| 1 | 0.0 stray commit | low | Haiku | Sonnet |
| 2 | 0.1 four broken charts | high | Sonnet | Opus |
| 3 | 0.1 remaining charts-mirror sources | high | Sonnet (Haiku for the probe step) | Opus |
| 4 | 0.2 symlinks | local only | coordinator inline | none |
| 5 | 0.3 stray-dir root cause | local only | Haiku | coordinator |
| 6 | 0.4 agent credentials + guard | high | Sonnet | Opus |
| 7 | 0.5 Flux alert covers sources | high | Sonnet | Opus |
| 7b | 1.1 gate tests (owner decision 2026-10-31) | high (touches workflows) | Opus (classify) + Sonnet | Opus |
| 8 | 1.1 `CI Success` | high | Sonnet | Opus |
| 9 | 1.2 nightly network tests | normal | Haiku (classify) + Sonnet | Sonnet |
| 10 | 1.3 linters + formatter | high (touches workflows) | Sonnet | Opus |
| 11 | 1.5 daily metrics | high (new workflow) | Sonnet | Sonnet |
| 12 | 1.4 ruleset + break-glass | OWNER | coordinator with owner | none |

Order: 1, 4, 5 → 2 → 3, 6, 7 → 7b → 8 → 9, 10, 11 → 12. Task 12 waits until Tasks 8–11 are merged and `CI Success` has been green on PRs for 3 days.

## Review Focus

1. **A docs-only, Renovate or fork PR must still get exactly one `CI Success` result.** Every called job may skip on path filters. A fork PR skips everything and must not run secrets-bearing jobs. A Renovate PR is authored by `app/mortyops`. Test in Task 8.
2. **A failed or cancelled inner job must turn `CI Success` red, never green-by-skip.** This includes a failing `Flate - Filter`, which makes everything downstream skip. Test in Task 8.
3. **Swapping a chart's source at the same version must not restart Cilium or OpenEBS.** A changed chart digest triggers a Helm upgrade. The rendered manifests must be identical, or the PR states the exact diff. Test in Task 2.
4. **The bypass forms the docs agent listed must not get past the guard:** `kubectl -n ns apply`, `bash -c '…'`, `gh api -XPUT`, `env X=1 kubectl delete`, `git -C . push --force`, and `&&`-chained writes. Test in Task 6.
5. **CI Success must run while the cluster or ARC runner is down.** No job in its `needs` graph may use `gha-runner-scale-set-*`. Test in Task 8.

---

### Task 1: Land or drop the stray image-script commit (spec 0.0)

**Files:** `.agents/skills/image-generation/SKILL.md`, `.agents/skills/image-generation/scripts/generate_image.py` (from `d2fa343c`)

- [ ] **Step 1:** **OWNER decision.** Keep `d2fa343c` (default) or drop it.
- [ ] **Step 2 (keep):** `git worktree add ../home-ops-image-note -b fix/image-key-limit-note origin/main && cd ../home-ops-image-note && git cherry-pick d2fa343c`. Expected: clean pick, 2 files.
- [ ] **Step 3:** Run `python3 scripts/ci/docs-budget-test.py; python3 scripts/ci/doc-links-test.py`. Expected: `0 broken`; budget failures limited to the `.claude/skills` symlink item.
- [ ] **Step 4:** `git push -u origin fix/image-key-limit-note && gh pr create --fill`. Tier low.

### Task 2: Move the four broken charts upstream (spec 0.1, first half)

**Files:**
- Modify: `kubernetes/apps/base/kube-system/cilium/app/ocirepository.yaml` (url → `oci://quay.io/cilium/charts/cilium`, tag stays `1.18.6`)
- Modify: `kubernetes/apps/base/system/openebs-hostpath/app/helmrelease.yaml` (OCIRepository url → `oci://ghcr.io/openebs/charts/openebs`, tag stays `4.4.0`)
- Modify: `kubernetes/apps/base/system/intel-device-plugin-operator/app/helmrelease.yaml` and `.../gpu/helmrelease.yaml`. Intel publishes no OCI chart, so:
  - replace each embedded OCIRepository with one `HelmRepository` named `intel` (url `https://intel.github.io/helm-charts`, interval `1h`), defined once in `app/helmrelease.yaml`;
  - switch both HelmReleases from `chartRef` to `chart.spec` (`chart: intel-device-plugins-operator` / `intel-device-plugins-gpu`, `version: 0.34.1`, `sourceRef: {kind: HelmRepository, name: intel, namespace: system}`).

  The `gpu` Kustomization already `dependsOn` `intel-device-plugin-operator`, so the repository exists first.
- Modify: `scripts/ci/igpu-xe-allowids-test.py:43` (replace `CHART_OCI` with `CHART_REPO = "https://intel.github.io/helm-charts"` and pull with `helm pull intel-device-plugins-gpu --repo CHART_REPO --version CHART_VERSION`)

- [ ] **Step 1:** Run `python3 scripts/ci/igpu-xe-allowids-test.py`. Expected: FAIL, `403 denied` from `charts-mirror`. This is the red test.
- [ ] **Step 2:** Apply the four manifest edits and the test edit above.
- [ ] **Step 3:** Run `python3 scripts/ci/igpu-xe-allowids-test.py`. Expected: PASS.
- [ ] **Step 4:** Run `flate test all --path kubernetes/clusters/main --no-progress`. Expected: `0 failed`. The 8 previous failures are gone.
- [ ] **Step 5 (Review Focus 3):** Run `flate diff hr --path kubernetes/clusters/main --base origin/main --no-progress` and the same for `ks`. Expected: only source and chart-reference lines change. If any rendered workload (DaemonSet, Deployment, ConfigMap) differs, stop and put the diff in the PR for the Opus reviewer.
- [ ] **Step 6:** Run `mise exec -- helmfile -f bootstrap/helmfile/apps.yaml template -l name=cilium >/dev/null`. Expected: exit 0, because bootstrap reads the cilium url from the same `ocirepository.yaml`. If it needs 1Password or `helmfile` isn't in `.mise.toml`, record "not run: <reason>" in the PR, and instead confirm that `bootstrap/helmfile/templates/release.yaml.gotmpl` reads `spec.url` from the edited file.
- [ ] **Step 7:** Commit `fix(flux): pull cilium, openebs and intel charts from upstream registries`, then open the PR (tier high, owner merges).
- [ ] **Step 8 (post-merge, read-only):** Run `flux get sources oci -A` and `flux get sources helm -n system`. Expected: all Ready. Run `kubectl -n kube-system get ds cilium -o jsonpath='{.metadata.generation}'` before and after merge. Expected: unchanged.

### Task 3: Retire the remaining charts-mirror sources (spec 0.1, second half)

**Files:** the 8 still-working `oci://ghcr.io/home-operations/charts-mirror/*` sources: headlamp, external-dns (×2), tailscale-operator, volsync, descheduler, metrics-server, keda. Also `kubernetes/apps/base/network/tailscale-operator/README.md`.

- [ ] **Step 1 (Haiku):** For each source, find the project's official OCI chart location from its own docs or README, and probe it with `helm show chart <oci-url> --version <current tag>`. Output a table: app, current url, upstream url or "none", probe result.
- [ ] **Step 2:** Switch every source whose probe passed. Sources with no upstream OCI stay on the mirror and are listed in the PR body, with the reason they stay.
- [ ] **Step 3:** Run Task 2 Steps 4–5 again for the changed sources. Expected: same results.
- [ ] **Step 4:** Check the Renovate presets in `.renovaterc.json5`: `Aviator-Coding/mortyops` and the local `home-ops` presets. They are not community presets, so record "no change" in the PR body.
- [ ] **Step 5:** Commit `fix(flux): pull remaining charts from upstream registries`, then open the PR (tier high).

### Task 4: Make symlinks real again (spec 0.2, local only, no PR)

- [ ] **Step 1:** Run `cd /Users/aviator/Projects/home-ops && diff -r .claude/skills/image-generation .agents/skills/image-generation`. Expected: no output, so the stray copy is identical.
- [ ] **Step 2:** Run `git config core.symlinks true && rm -rf .claude/skills && git checkout -- .claude/skills && ls -la .claude/skills`. Expected: `.claude/skills -> ../.agents/skills`.
- [ ] **Step 3:** Repeat Step 2 in every worktree from `git worktree list`; `core.symlinks` is shared, so only the checkout is needed.
- [ ] **Step 4:** Run `python3 scripts/ci/docs-budget-test.py`. Expected: `0 failed`.
- [ ] **Step 5:** Start a fresh Claude Code session. Expected: its skill list shows the 27 repo skills.

### Task 5: Find what replaced the symlink (spec 0.3, local only)

- [ ] **Step 1 (Haiku):** Search the session transcripts for writes to that path: `grep -l '.claude/skills/image-generation' ~/.claude/projects/-Users-aviator-Projects-home-ops/*.jsonl`. For each hit, report the session id, timestamp and tool call (Write, Bash `cp`/`mkdir`, or a skill installer) near 2026-10-30 22:39.
- [ ] **Step 2:** If an agent or tool wrote it, add one `AGENTS.md` sentence under Conventions: "Add skills under `.agents/skills/` only; `.claude/skills` is a symlink." If a tool did it unprompted, record the tool and version in §8 of the spec instead.
- [ ] **Step 3:** Add a §8 evidence row with the root cause. Any `AGENTS.md` edit ships as a tier-low PR.

### Task 6: Agent credentials and command guard (spec 0.4)

**Files:**
- Create: `kubernetes/apps/base/kube-system/agent-readonly/app/{kustomization.yaml,serviceaccount.yaml,clusterrolebinding.yaml}`
  - ServiceAccount `agent-readonly` in `kube-system`.
  - ClusterRoleBinding `agent-readonly-view` → ClusterRole `view`. `view` already aggregates `flux-view-flux-system`, `flux-operator-view` and `external-secrets-view`, and excludes Secrets.
- Create: `kubernetes/apps/main/kube-system/agent-readonly.yaml` (overlay with the usual `&app`/`&namespace` anchors), and add `- ./agent-readonly.yaml` to `kubernetes/apps/main/kube-system/kustomization.yaml`.
- Create: `scripts/agent-credentials.sh`. The owner runs it with admin credentials. It writes:
  - `kubeconfig-agent`: `kubectl create token agent-readonly -n kube-system --duration=720h`, wrapped in a kubeconfig for the same server;
  - `talos/talosconfig-agent`: `talosctl config new talos/talosconfig-agent --roles os:reader --crt-ttl 720h`.

  It exits non-zero if `KUBECONFIG`/`TALOSCONFIG` point at the agent files, because admin credentials are needed to mint new ones. If the API server caps token lifetime below 720h, the script prints the real expiry from the token's `exp` claim.
- Modify: `.mise.toml:9-10`. `KUBECONFIG` becomes `{{config_root}}/kubeconfig-agent` and `TALOSCONFIG` becomes `{{config_root}}/talos/talosconfig-agent`.
- Modify: `.gitignore`. Add `kubeconfig-agent` and `talos/talosconfig-agent`. Add `!.claude/settings.json` and `!.claude/hooks/` after `.claude/*`.
- Create: `.claude/settings.json` (tracked). It contains:
  - the existing `enabledPlugins`;
  - `permissions.deny` with exactly these rules:
    - `Bash(gh pr merge *)`, `Bash(gh api *-X*)`, `Bash(gh api *--method*)`;
    - `Bash(git push *--force*)`, `Bash(git push * -f*)`;
    - `Bash(talosctl apply-config *)`, `Bash(talosctl upgrade *)`, `Bash(talosctl reboot *)`, `Bash(talosctl reset *)`;
    - `Bash(tofu apply *)`, `Bash(tofu destroy *)`;
    - `Bash(kubectl apply *)`, `Bash(kubectl delete *)`, `Bash(kubectl patch *)`, `Bash(kubectl edit *)`, `Bash(kubectl replace *)`;
    - `Read(~/.kube/home-ops-admin)`, `Read(~/.talos/home-ops-admin)`, `Read(/kubeconfig)`, `Read(/talos/talosconfig)`;
  - a PreToolUse hook with matcher `Bash` that runs `python3 ${CLAUDE_PROJECT_DIR}/.claude/hooks/guard.py`.

  The deny rule `gh api *-X*` deliberately also blocks `-X GET`, because agents read with plain `gh api <path>`. The hook allows `-X GET`, since it is also the guard Hermes will reuse.
- Create: `.claude/hooks/guard.py`. Stdlib only. It reads the hook JSON from stdin, takes `.tool_input.command`, and exits `2` with a one-line reason on stderr when `is_blocked(command: str) -> str | None` returns a reason; otherwise it exits `0`. `is_blocked` matches the whole command string, so wrappers and chains are covered. It blocks:
  - `kubectl` with any flags before `apply|delete|patch|edit|replace|scale|rollout restart|annotate|label|cordon|drain`;
  - `talosctl` with `apply-config|upgrade|reboot|reset|shutdown|edit|patch`;
  - `tofu|terraform` with `apply|destroy`;
  - `gh pr merge`;
  - `gh api` with `-X` or `--method` set to anything but `GET`;
  - `gh ruleset`, `gh label (create|edit|delete)`, `gh repo edit`;
  - `git push` with `--force`, `-f` or `+refspec`;
  - `flux (suspend|resume|reconcile)`.
- Create: `scripts/ci/agent-guard-test.py`. It tests `is_blocked` by importing the hook file through `importlib`.
- Local only: `.claude/settings.local.json`. Remove the `Bash(gh api *)` and `Bash(gh pr *)` allows, and add `Bash(gh pr view *)`, `Bash(gh pr list *)`, `Bash(gh pr diff *)`, `Bash(gh pr checks *)`, `Bash(gh run *)`.
- Modify: `AGENTS.md` COMMANDS. One line: "Default credentials are read-only agent credentials. Owner writes use `KUBECONFIG=~/.kube/home-ops-admin TALOSCONFIG=~/.talos/home-ops-admin`."

**Interfaces:** Produces `is_blocked(command: str) -> str | None` in `.claude/hooks/guard.py`.

- [ ] **Step 1: Write the failing test** `scripts/ci/agent-guard-test.py`.
  - `test_blocks_writes` asserts a non-None reason for each of:
    `kubectl apply -f x.yaml`, `kubectl -n ai delete pod x`, `kubectl --kubeconfig kubeconfig patch hr x`, `env FOO=1 kubectl delete ns x`, `bash -c 'kubectl apply -f -'`, `ls && kubectl replace -f x`, `talosctl -n 10.10.10.11 reboot`, `talosctl apply-config -f c.yaml`, `tofu -chdir=terraform/authentik apply`, `gh pr merge 12 --squash`, `gh api -XPUT repos/a/b/rulesets/1`, `gh api repos/a/b/rulesets/1 --method=PATCH`, `gh api -X DELETE repos/a/b/git/refs/heads/x`, `git -C . push --force`, `git push origin +main`, `flux suspend ks renovate`.
  - `test_allows_reads` asserts `None` for each of:
    `kubectl get pods -A`, `kubectl -n ai logs deploy/x`, `kubectl auth can-i create pods`, `talosctl -n 10.10.10.11 get members`, `gh pr view 12`, `gh api repos/a/b/pulls`, `gh api -X GET repos/a/b/rulesets`, `git push -u origin feat/x`, `flux get ks -A`, `tofu plan`, `grep -r "kubectl apply" docs/`.
  - The last item checks that the word appearing inside a quoted grep pattern is still allowed. If that proves impossible to separate cleanly, the test accepts a block there, and the PR documents it.
- [ ] **Step 2:** Run `python3 scripts/ci/agent-guard-test.py`. Expected: FAIL, guard.py not found.
- [ ] **Step 3:** Implement `is_blocked` and the hook entrypoint in `.claude/hooks/guard.py`. Approach: `shlex.split` each segment after splitting on `&&`, `||`, `;`, `|`, newline, and inside `bash -c`/`sh -c` arguments; drop leading `VAR=val`, `env`, `command`, `timeout N`, `nice`, `mise exec --`. Then match the first word plus the first non-flag subcommand.
- [ ] **Step 4:** Run `python3 scripts/ci/agent-guard-test.py`. Expected: PASS.
- [ ] **Step 5:** Write the RBAC manifests, the overlay, `scripts/agent-credentials.sh`, the `.mise.toml`/`.gitignore`/`.claude/settings.json` changes and the `AGENTS.md` line. Run `flate test all --path kubernetes/clusters/main --no-progress`. Expected: `0 failed`.
- [ ] **Step 6:** Commit `feat(security): read-only agent credentials and a command guard`, then open the PR (tier high).
- [ ] **Step 7 (OWNER, post-merge):**
  1. Move admin credentials: `mkdir -p ~/.kube ~/.talos && mv kubeconfig ~/.kube/home-ops-admin && mv talos/talosconfig ~/.talos/home-ops-admin`.
  2. Run `KUBECONFIG=~/.kube/home-ops-admin TALOSCONFIG=~/.talos/home-ops-admin scripts/agent-credentials.sh`.
  3. Create a fine-grained GitHub token for agents, scoped to `Aviator-Coding/home-ops`: Contents RW, Pull requests RW, Issues RW, Actions R, Metadata R, no Administration. Store it in 1Password, and export it as `GH_TOKEN` for agent sessions through a gitignored `.mise.local.toml` that runs `op read`.
- [ ] **Step 8: Record denials in spec §8**, using the agent credentials:
  - `kubectl auth can-i create pods -A` → `no`
  - `kubectl auth can-i get secrets -A` → `no`
  - `kubectl get hr -A` → lists
  - `talosctl config info` → `Roles: os:reader`
  - `gh api repos/Aviator-Coding/home-ops/rulesets/21250320` → 403 or 404 (no Administration)
  - a Claude session asked to run `kubectl -n ai delete pod x` → the hook's block reason

  Residual risk to record: a script that opens `~/.kube/home-ops-admin` directly is only stopped by the OS sandbox, which this plan does not enable.

### Task 7: Alert on any not-Ready Flux object, sources included (spec 0.5, D9)

**Files:**
- Modify: `kubernetes/apps/base/flux-system/flux-instance/app/prometheusrule.yaml:57`. `kind=~"HelmRelease|Kustomization"` becomes `kind=~"HelmRelease|Kustomization|GitRepository|OCIRepository|HelmRepository|HelmChart|Bucket"`. Keep `for: 30m`, which is stricter than D9's 1h. Add one line to the description: an OCIRepository stuck on an auth or 404 error blocks every upgrade of its chart.
- Modify: `scripts/ci/flux-not-ready-alert-test.py`. The `OCIRepository src flux-system` series at line 132 moves from never-fire to `firing`. Add an `HelmRepository intel system ready=False` series that fires too. Keep `Alert an-alert` as the out-of-scope kind that never fires, and update the docstring list.

- [ ] **Step 1:** Edit the test expectations as above.
- [ ] **Step 2:** Run `python3 scripts/ci/flux-not-ready-alert-test.py`. Expected: FAIL, OCIRepository and HelmRepository not firing.
- [ ] **Step 3:** Edit the PrometheusRule.
- [ ] **Step 4:** Run the test again. Expected: PASS.
- [ ] **Step 5:** Grep for the old kind list in skill text: `grep -rn 'HelmRelease|Kustomization' .agents/skills/observability .agents/skills/flux-gitops`. Update any sentence claiming that sources are excluded.
- [ ] **Step 6:** Commit `fix(flux): alert on not-Ready Flux sources`, then open the PR (tier high).
- [ ] **Step 7 (OWNER, D9 proof):** After merge, have the owner create a throwaway `OCIRepository` named `alert-probe` in `flux-system` with url `oci://ghcr.io/home-operations/does-not-exist`, wait 35m, confirm the notification arrived, then delete it. Record it in §8. This is a cluster write, so only the owner does it.

### Task 7b: Gate on a few tests (spec 1.1 and 1.2, owner decision 2026-10-31)

In 59 of the last 60 Validate PR runs, `python-tests` was the only failing job, so the whole suite is not gated. Only a few tests are.

**Files:** the chosen `scripts/ci/*-test.py` (marker line), `.github/workflows/validate.yaml`, `scripts/ci/ci-test-gate-test.py` (new).

- [ ] **Step 1 (Opus, classify):** Read each of the 92 tests' docstring and assertions. Put each in one bucket:
  - `gate`: it guards silent data loss or a cluster outage, it is offline, and it is deterministic. At most 15.
  - `nightly`: it needs the network.
  - `advisory`: everything else.

  Output a table with test, bucket, and the data loss or outage it prevents. It goes in the PR body for the owner to approve.
- [ ] **Step 2: Write the failing test** `ci-test-gate-test.py`:
  - at most 15 files carry the exact line `# ci: gate` after the shebang;
  - each gate file's docstring names what it prevents;
  - validate's required `python-tests` loop runs only gate files;
  - an advisory job runs the rest with `continue-on-error: true` and is not in `success.needs`.
- [ ] **Step 3:** Add the markers and the two jobs. The advisory job keeps today's dependency setup.
- [ ] **Step 4:** Run the gate test (PASS) and every gate file locally (PASS).
- [ ] **Step 5:** Commit `ci(github-action): gate PRs on the data-loss and outage tests only`, then open the PR (tier high).

Task 9 then classifies only the advisory tests: gate tests are offline by construction. Advisory tests are deleted or promoted in P3, ledger row first.

### Task 8: One GitHub-hosted `CI Success` check (spec 1.1, D1 prerequisite)

**Files:**
- Create: `.github/workflows/ci.yaml`.
  - `name: CI`; `on: pull_request` (types opened/synchronize/reopened, branches main); concurrency `group: ci-${{ github.event.number }}`, `cancel-in-progress: true`; `permissions: contents: read, pull-requests: write`.
  - Jobs:
    - `flate: uses: ./.github/workflows/flate.yaml`, `secrets: inherit`
    - `validate: uses: ./.github/workflows/validate.yaml`, `secrets: inherit`
    - `success`: `name: CI Success`, `runs-on: ubuntu-latest`, `if: ${{ !cancelled() }}`, `needs: [flate, validate]`, step fails when `contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled')`
- Modify: `.github/workflows/flate.yaml` and `.github/workflows/validate.yaml`.
  - `on:` becomes only `workflow_call: {}`.
  - Delete their top-level `concurrency` blocks, which would cancel each other under the caller's name.
  - Every `runs-on` becomes `ubuntu-latest`.
  - Keep the fork guards and the inner `success` jobs.
  - Rewrite the header comments that say "self-hosted runner".
- Delete (ledger row first): `scripts/ci/validate-contention-test.py`. It pins ARC runner contention that no longer applies. Ledger verdict: `archive` (it prevents neither data loss nor an outage).
- Modify: `scripts/ci/terraform-ci-workflows-test.py` (`test_flate_untouched`), `scripts/ci/workflow-hardening-test.py` (flate concurrency assertions at ~318–336) and `scripts/ci/mise-install-args-test.py`. They assert the new shape: concurrency lives in `ci.yaml`, and the called workflows have none.
- Create: `scripts/ci/ci-success-workflow-test.py`.
- Modify: `.agents/skills/github-ci/SKILL.md`, `references/workflows.md`, `references/arc-runners.md`, `.github/workflows/README.md`. State that `CI Success` is the gate, runs on GitHub-hosted runners, and that only Image Pull and AI review use ARC.

**Interfaces:** Produces the check context `CI Success` (GitHub Actions app id `15368`), which Task 12 consumes.

- [ ] **Step 1: Write the failing test** `scripts/ci/ci-success-workflow-test.py`. It loads the workflows with `yaml.safe_load`, treating key `True` as `on`.
  - `test_ci_calls_both`: `ci.yaml` jobs `flate.uses == "./.github/workflows/flate.yaml"`, `validate.uses == "./.github/workflows/validate.yaml"`, both `secrets == "inherit"`.
  - `test_success_job`: `jobs.success.name == "CI Success"`, `needs == ["flate", "validate"]`, `if` contains `!cancelled()`, `runs-on == "ubuntu-latest"`, and some step `if` contains both `'failure'` and `'cancelled'`.
  - `test_called_workflows_are_call_only`: for flate and validate, `set(on) == {"workflow_call"}` and `"concurrency" not in data`.
  - `test_no_arc_in_gate` (Review Focus 5): every job's `runs-on` in ci/flate/validate is `ubuntu-latest`.
  - `test_fork_guard_kept`: every job with a secrets reference in flate/validate has an `if` containing `head.repo.full_name == github.repository`.
- [ ] **Step 2:** Run `python3 scripts/ci/ci-success-workflow-test.py`. Expected: FAIL, `ci.yaml` missing.
- [ ] **Step 3:** Make the workflow, test and doc changes listed above. Add the `validate-contention-test.py` ledger row before deleting it.
- [ ] **Step 4:** Run the full local gate. Expected: no `FAILED` lines.
- [ ] **Step 5:** Run `mise exec -- actionlint .github/workflows/ci.yaml .github/workflows/flate.yaml .github/workflows/validate.yaml`. Expected: no findings. If actionlint isn't installed yet, use `mise x aqua:rhysd/actionlint -- actionlint …`.
- [ ] **Step 6:** Commit `ci(github-action): add a GitHub-hosted CI Success gate`, then open the PR (tier high).
- [ ] **Step 7 (Review Focus 1–2):** On the PR itself, check that the `CI Success` check appears once and is green.
  1. Push a throwaway commit that breaks one manifest, for example by removing a `kind:` line. Expected: `CI Success` turns red.
  2. Revert the commit.
  3. Open a docs-only PR (Task 9's or a typo fix). Expected: `CI Success` green, with flate jobs skipped.
  4. Check the next Renovate PR. Expected: `CI Success` posted.

  Record all three in §8.

### Task 9: Network-dependent tests move to a nightly run (spec 1.2, D2)

**Files:**
- Modify: each `scripts/ci/*-test.py` that the classification marks as networked. Add the exact line `# ci: nightly` directly after the shebang.
- Modify: the `python-tests` loop in `.github/workflows/validate.yaml`. Skip files containing a line equal to `# ci: nightly`.
- Create: `.github/workflows/nightly-tests.yaml`.
  - `schedule: cron "0 5 * * *"`, plus `workflow_dispatch`, on `ubuntu-latest`, with the same Setup Tools and dependency steps as `python-tests`.
  - Runs only the marked files.
  - On failure, `gh issue create --title "Nightly network tests failed $(date -I)" --label ci-nightly`, or comments on the open issue with that label. Needs `permissions: issues: write`.
- Modify: `scripts/ci/ci-success-workflow-test.py`. Add `test_nightly_marker_split`: the validate loop's run script mentions `# ci: nightly`; `nightly-tests.yaml` has a schedule and only runs marked files.

- [ ] **Step 1 (Haiku, classify):** Run every test under a no-network macOS sandbox:
  `sandbox-exec -p '(version 1)(allow default)(deny network-outbound (remote ip "*:*"))(allow network-outbound (remote ip "localhost:*"))' python3 "$t"`.
  Calibrate it first: `igpu-xe-allowids-test.py` must FAIL inside the sandbox and PASS outside it; `docs-budget-test.py` must PASS inside. Output the list of tests that pass outside and fail inside. That list is the networked set, replacing the spec's grep estimate of 13.
- [ ] **Step 2:** Add `test_nightly_marker_split` and run it. Expected: FAIL.
- [ ] **Step 3:** Add the markers, the loop skip and `nightly-tests.yaml`. If the `ci-nightly` label doesn't exist, it's created in **OWNER** Task 12, or the workflow falls back to an unlabeled issue.
- [ ] **Step 4:** Run the test (PASS) and the full local gate.
- [ ] **Step 5:** Update spec §2 baseline: replace "13 call the network" with the measured count and the date.
- [ ] **Step 6:** Commit `ci(github-action): run network-dependent tests nightly, not on PRs`, then open the PR (tier high, since it touches workflows).

### Task 10: Standard linters and a formatter (spec 1.3)

**Decision taken here:** linters gate the whole repo in CI. The formatter runs only in pre-commit, after one mechanical repo-wide format PR. Formatting in CI would fail every Renovate PR that touches an unformatted file.

**Files:**
- Create: `.editorconfig`. `root = true`; `[*]` with `charset = utf-8`, `end_of_line = lf`, `insert_final_newline = true`, `trim_trailing_whitespace = true`, `indent_style = space`, `indent_size = 2`. `[*.{py,sh}]` gets `indent_size = 4`; `[Makefile,.justfile,*.just]` gets `indent_style = tab` only if those files already use tabs (check first).
- Modify: `.mise.toml`. Add `"aqua:rhysd/actionlint"`, `"pipx:zizmor"`, `"aqua:koalaman/shellcheck"`, `"pipx:yamllint"` and `"aqua:google/yamlfmt"`, pinned to current releases with `# renovate:` comments like the existing pins.
- Create: `.yamllint.yaml`. Extend `relaxed`; disable `line-length` and `document-start`; ignore `*.j2`, `*.gotmpl`, `**/templates/**` and `.github/docker/**`. Then tighten rules only as far as the current tree passes.
- Create: `.yamlfmt.yaml`. `formatter.type: basic`, `retain_line_breaks_single: true`, `include_document_start: true`, `scan_folded_as_literal: true`, same excludes as `.yamllint.yaml`, plus `kubernetes/**/resources/**`.
- Modify: `.pre-commit-config.yaml`. Add a `repo: local` block with `language: system` hooks: `yamllint`, `actionlint` (files `^\.github/workflows/`), `zizmor .github/workflows` (`pass_filenames: false`), `shellcheck` (types `[shell]`) and `yamlfmt` (fixes files).
- Modify: `.github/workflows/validate.yaml`. Add a `lint` job (`ubuntu-latest`, no path filter): mise-action installs the five tools, then `yamllint -s .`, `actionlint`, `zizmor --min-severity medium .github/workflows`, `shellcheck $(git ls-files '*.sh')`. Add it to the inner `success` `needs`.
- Modify: `scripts/ci/ci-success-workflow-test.py`. Add `test_lint_job_gated`: validate has job `lint`, it's in `success.needs`, and it runs on `ubuntu-latest`.

- [ ] **Step 1:** Add `test_lint_job_gated`; run it. Expected: FAIL.
- [ ] **Step 2:** Add the tools and configs. Run each linter over the repo and save finding counts to `/tmp/lint-baseline.txt`.
- [ ] **Step 3:** Fix every actionlint and zizmor finding. They are few and live in workflows only. For yamllint and shellcheck, fix mechanical findings; disable a rule in config only when it has more than 20 findings, with a comment naming the rule and the count.
- [ ] **Step 4:** Add the `lint` job; run `test_lint_job_gated` (PASS) and the full local gate.
- [ ] **Step 5:** Commit `ci(github-action): add standard linters to pre-commit and CI`, then open the PR (tier high).
- [ ] **Step 6 (separate PR, after Step 5 merges):** Run `yamlfmt .` repo-wide in its own worktree. Gate: `flate diff hr` and `flate diff ks` against `origin/main` must both be empty, and the talos/bootstrap validate jobs must be green. Any file whose rendered output changes is reverted and added to `.yamlfmt.yaml` excludes. Commit `chore(repo): format YAML with yamlfmt` (tier high, owner merges). The pre-commit `yamlfmt` hook stays disabled (`stages: [manual]`) until this PR merges, then becomes active in the same PR's last commit.

### Task 11: Daily goal metrics (spec 1.5, D2/D3 evidence)

**Files:**
- Create: `scripts/ci/goal_metrics.py`.
  - Stdlib only.
  - `pass_rate(conclusions: list[str]) -> float`: success ÷ (success + failure), cancelled and skipped ignored, `1.0` for an empty list.
  - `render_row(day: str, rate: float, runs: int, open_renovate: int) -> str`: the Markdown row `| {day} | {rate:.1%} | {runs} | {open_renovate} |`.
  - `main()` uses `gh` CLI calls:
    - `gh run list --workflow CI --event pull_request --created ">=<today-14d>" --json conclusion --limit 1000`
    - `gh pr list --author app/mortyops --state open --json number`
  - `main()` then updates the pinned issue titled `Goal metrics: agent-maintainable home-ops`, appending the row under a header `| Date | CI Success pass rate (14d) | Runs | Open Renovate PRs |`. It creates the issue if missing, without pinning it; pinning is done by the **OWNER**.
- Create: `scripts/ci/goal-metrics-test.py`.
- Create: `.github/workflows/goal-metrics.yaml`. `schedule: cron "15 6 * * *"`, plus `workflow_dispatch`, on `ubuntu-latest`; `permissions: {actions: read, issues: write, pull-requests: read}`; `GH_TOKEN: ${{ github.token }}`; runs `python3 scripts/ci/goal_metrics.py`.

**Interfaces:** Produces `pass_rate(list[str]) -> float` and `render_row(str, float, int, int) -> str`.

- [ ] **Step 1: Write the failing test** `scripts/ci/goal-metrics-test.py`.
  - `pass_rate(["success","success","failure","cancelled","skipped"]) == 2/3`
  - `pass_rate([]) == 1.0`
  - `render_row("2026-11-20", 0.9567, 230, 3) == "| 2026-11-20 | 95.7% | 230 | 3 |"`
- [ ] **Step 2:** Run it. Expected: FAIL, module missing.
- [ ] **Step 3:** Implement `goal_metrics.py` and the workflow.
- [ ] **Step 4:** Run the test (PASS). Run `actionlint .github/workflows/goal-metrics.yaml` (clean) and the full local gate.
- [ ] **Step 5:** Commit `ci(github-action): record daily goal metrics`, then open the PR (tier high).
- [ ] **Step 6 (post-merge):** Trigger with `gh workflow run goal-metrics.yaml`. Expected: the issue exists with one row. **OWNER** pins it.

### Task 12: Make `CI Success` required, rehearse break-glass (spec 1.4, D1, OWNER)

Prerequisite: Tasks 8–11 merged, and `CI Success` green on PRs for 3 consecutive days.

- [ ] **Step 1:** Run `gh api repos/Aviator-Coding/home-ops/rulesets/21250320 > /tmp/ruleset-before.json`. Keep this file for rollback.
- [ ] **Step 2 (OWNER, break-glass dry run):** Rehearse the spec §3 procedure on a no-op docs PR:
  1. `gh api -X PUT repos/Aviator-Coding/home-ops/rulesets/21250320 -f enforcement=disabled`
  2. Confirm the PR shows mergeable without checks.
  3. `gh api -X PUT repos/Aviator-Coding/home-ops/rulesets/21250320 -f enforcement=active`
  4. Add a §8 row.
- [ ] **Step 3 (OWNER):** `PUT` the ruleset with:
  - `bypass_actors: []`;
  - the existing `deletion`, `non_fast_forward` and `pull_request` rules unchanged;
  - `required_status_checks` set to `[{"context": "CI Success", "integration_id": 15368}]`, with `strict_required_status_checks_policy: false`.

  Send the full JSON body from `/tmp/ruleset-before.json` with those two fields changed (`--input`).
- [ ] **Step 4:** Run `gh api repos/Aviator-Coding/home-ops/rulesets/21250320 --jq '{bypass_actors, checks: [.rules[]|select(.type=="required_status_checks")|.parameters.required_status_checks[].context]}'`. Expected: `{"bypass_actors":[],"checks":["CI Success"]}`. Record D1 in §8.
- [ ] **Step 5:** Tick spec checkboxes 0.0–0.5 and 1.1–1.5 as each PR merges. P1 exits when `goal-metrics` shows ≥ 95% for 7 consecutive days. Record the exit in §8.
