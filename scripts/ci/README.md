# scripts/ci

Two kinds of script live here.

## Shell scripts: CI-run, wired today

`talos-validate.sh`, `version-consistency.sh`, `tofu-validate.sh` are invoked by
`.github/workflows/validate.yaml` (jobs `talos`, `versions`, `terraform`). Each has a
WHAT-THIS-CATCHES / WHAT-THIS-DOES-NOT-CATCH header explaining its scope - read that before
trusting a green run for more than it claims.

## Python regression tests

The `*-test.py` files are hand-written semantic/behavioral regression tests, each pinning
one specific captain decision or invariant against the live repo state (not a source grep -
most render the real manifests via `kubectl kustomize`/`kustomize build`, parse the result,
and assert on the parsed structure; the `litellm-*` ones additionally exercise the real
`litellm` library the cluster's pinned proxy image runs). Every file's docstring names the
decision it pins - read it first if you're touching the app or feature it covers.

| file | pins |
|---|---|
| `gpu-node-dashboard-test.py` | Intel GPU node Grafana dashboard (hwmon/device-plugin PromQL contract) |
| `grafana-mcp-deploy-test.py` | in-cluster grafana-mcp ToolHive `MCPServer` deployment |
| `grafana-sa-provisioner-test.py` | grafana-sa-provisioner (captain option C) |
| `hostpolicy-ceph-selector-test.py` | Ceph LAN-isolation host CCNP selector |
| `kopiur-stage0-test.py` | kopiur Stage 0: operator pin/CRDs/monitoring, ceph+r2 ClusterRepositories + deletion protection, no SnapshotPolicy/Schedule, Renovate automerge exclusion, VolSync untouched |
| `kopiur-stage1-test.py` | kopiur Stage 1: reusable backup component (ceph+r2 only, Ephemeral cache, pinned Retain deletion), `downloads/autobrr` dual-backup alongside untouched VolSync, and the credential contract - all three `credentialProjection` legs wired plus zero standing per-namespace credential objects (fleet coverage is owned by stage3) |
| `kopiur-stage2-test.py` | kopiur Stage 2: both pilot volumes stay onboarded (`autobrr` + `sabnzbd`; the exact fleet set moved to stage3), `KOPIUR_CLAIM=sabnzbd-config` + load-bearing `KOPIUR_PUID/PGID=2000`, volsync still triple-dest, drill doc result contract (both-destination sha256 digest, findings 1-2, proved VolSync simultaneity with observed lastSync times) |
| `schematic-pcie-port-pm-test.py` | Arc B70 `pcie_port_pm=off` schematic kernel-arg contract |
| `igpu-xe-allowids-test.py` | scoping `gpu.intel.com/xe` to the iGPU |
| `b70-vaapi-tdarr-test.py` | B70 VA-API restore: `b70-vaapi` native mounts, hashed plugin config rollout, tdarr-node resource/CPU fallback, libdrm DEVNAME reopen trap |
| `litellm-auto-router-test.py` | D3 complexity-tier auto-router config |
| `litellm-claude-code-subscription-test.py` | Claude Code Max/Pro subscription pass-through |
| `litellm-fallback-chain-test.py` | Phase 5 LiteLLM availability/context fallback chains |
| `litellm-anthropic-passthrough-test.py` | Gateway-level close of LiteLLM `/anthropic` pass-through (HTTPRouteFilter directResponse 404, catch-all backend preserved) |
| `litellm-pr-reviewer-test.py` | AI PR reviewer workflow + `pr-review-local` / `ai-pr-review` D4 contracts |
| `litellm-request-logs-test.py` | full prompt/response capture in LiteLLM spend logs |
| `litellm-sso-test.py` | LiteLLM UI SSO through Authentik |
| `terraform-ci-workflows-test.py` | terraform-diff / terraform-publish CI contract |
| `tofu-authentik-stack-test.py` | the Authentik OpenTofu adoption stack |
| `validate-contention-test.py` | validate.yaml runner-pool contention timeouts + python-tests ordering |
| `workflow-hardening-test.py` | GitHub Actions workflow permissions/concurrency hardening |
| `recyclarr-quality-profile-test.py` | Radarr SQP-1 recyclarr fix (guide min_format_score, trash_id matching, 1080p profile) |
| `renovate-binding-conditions-test.py` | 2026-08-22 keep-in-cluster Renovate conditions vs shipped chart+CronJob: autoMerge last-match-wins exclusion for `kubernetes/apps/base/renovate/**`, silence alerts present, no hostRules/RENOVATE_HOST_RULES (condition 3 retired) |
| `recyclarr-config-readable-check-test.py` | recyclarr-config kopiur mover readability procedure (2026-08-31): executes measure.sh from the procedure doc against fixtures (empty-file / lost+found / walk-error traps), pins live 2913/2913 verdict + overlay/Readme pointers |
| `pvc-writable-check-test.py` | system/pvc-writable-check: real CronJob script vs mock kubectl (motivating unwritable bugs, clean sweep, readOnly/skip/excluded-NS/shell-less/kubectl-error skips, RO-rootfs large JSON, split-role RBAC, PrometheusRule, headlamp readOnly mount) |
| `pvc-mover-readable-check-test.py` | system/pvc-mover-readable-check: walk.sh traps (busybox find/stat, RO /tmp, lost+found, no-mutate) + CronJob vs mock kubectl (live-CR identities, kopiur-alert/VolSync-report-only, UNMEASURED/INCONCLUSIVE split, ordered exec-failure classifier) + split-role RBAC (5 NS, no cluster-wide exec) + PrometheusRule + overlay wiring |
| `backup-silent-failure-alerting-test.py` | VolSync stalled-sync + kopiur empty-backup PrometheusRules: promtool unit-tests fire for stuck ai/opencode, stay silent for healthy fleet / recyclarr / jellyfin leaked series, and KopiurBackupEmpty on zero files |
| `corrupt-claim-recreation-contract-test.py` | ai/opencode volume recreation contract: rendered volsync PVC `dataSourceRef`→RD (not restic) + RD `restore-once`/`IfNotPresent`/`fsGroup` empty-latestImage trap, ceph-block `reclaimPolicy: Delete`, runbook+evidence result gates, both AGENTS.md findings, no credential contents |
| `kopiur-stage3-test.py` | kopiur fleet parallel-run pin: 30/30 onboarded set + measured mover identity (`EXPECTED_IDENTITY`, includes changedetection-config 1000:1000 and matter-server 0:0), per-namespace r2 hour, empty `DEFERRED_CLAIMS`, and VolSync still on every volume |
| `selfhosted-backup-identity-test.py` | selfhosted dead `APP_UID`/`APP_GID` cleanup + `obsidian-livesync` VolSync mover 1000→5984 latent-defect pin: substitute maps lack the dead keys, kustomize+envsubst renders volsync/kopiur movers to the measured identity, and dropping `VOLSYNC_PUID` collapses obsidian back to the 1000 component default |
| `kopiur-timezone-test.py` | kopiur/VolSync cron timezone alignment: rendered `SnapshotSchedule.spec.schedule.timezone` default + override, pre-fix EST total ceph collision, post-fix zero collision across all dual-engine claims in both DST seasons |
| `kopiur-projected-secrets-leak-alert-test.py` | `KopiurProjectedCredentialsLeaking` multi-pass census semantics: live PrometheusRule must use `min_over_time(...[13h])` (not bare `> 0` / `for: 1h`); promtool unit-tests silent on the 6h benign plateau, fire on a permanent leak across 2+ sweeps, silent on healthy zero |
| `kopiur-stage4-test.py` | kopiur Stage 4: `home-automation/matter-server` onboarded with explicit `KOPIUR_PUID/PGID: 0`, GitOps privileged-mover annotation on the overlay Namespace, sibling movers unchanged |
| `talos-renovate-pin-test.py` | Talos Renovate pin unfreeze: `allowedVersions: "!/^v?1\\.13\\.3$/"` excludes only v1.13.3 (not a `<1.13.3` ceiling); requires node + renovate@44.52.1 (CI installs both; locally set `RENOVATE_NODE_PATH` or let the test `npm install`) so getRegexPredicate/filterVersions/docker-isStable/applyPackageRules prove old pin freezes, new pin proposes, alpha is docker-stable+minor, and autoMerge last-rule blocks automerge for all four Talos packages |
| `syncthing-data-capacity-test.py` | `syncthing-data` 15Gi right-size: plain PVC + VolSync `VOLSYNC_CAPACITY`/`CACHE` substitutes + rendered ReplicationDestination capacity (not live claim create); config claim stays 1Gi |

New `scripts/ci/*-test.py` files need no separate wiring: CI globs `scripts/ci/*-test.py`,
so any file matching that pattern is picked up automatically.

### How to run them locally

Each file is a standalone script, not a pytest module pytest can discover normally: `main()`
calls its `test_*` functions itself, prints one `[PASS]`/`[FAIL]` line per assertion plus a
summary, and the file's own `if __name__ == "__main__":` block is the only entrypoint.
Run one directly:

```bash
python3 scripts/ci/<name>-test.py
```

Setup, once per environment:

```bash
# .venv is provisioned by .mise.toml's _.python.venv; activate it first
source .venv/bin/activate
uv pip install python-hcl2 "litellm[proxy]==1.98.0"

# mise-managed CLIs some tests shell out to (kubectl, kustomize, tofu, yq,
# promtool via aqua:prometheus/prometheus, minijinja-cli for
# schematic-pcie-port-pm-test.py) - put the venv's own bin/ FIRST or its
# python3 gets shadowed by mise's bare interpreter. grafana-sa-provisioner-test.py
# prefers native promtool and only falls back to podman run when none is on PATH.
export PATH="$(mise bin-paths | tr '\n' ':')$PATH"
export PATH="$VIRTUAL_ENV/bin:$PATH"

for f in scripts/ci/*-test.py; do python3 "$f" || echo "FAILED: $f"; done
```

`python-hcl2` is needed by `litellm-sso-test.py` (hard import) and
`tofu-authentik-stack-test.py` (guarded import). `litellm[proxy]==1.98.0` matches the
pinned cluster image (`ghcr.io/berriai/litellm-non_root:v1.98.0`, see
`kubernetes/apps/base/ai/litellm/app/litellmproxy.yaml`) and is required by
`litellm-auto-router-test.py` and `litellm-request-logs-test.py`, which hard-fail without
it; `litellm-claude-code-subscription-test.py` and `litellm-fallback-chain-test.py` degrade
to a soft pass-with-note instead.

### CI status

Wired into `.github/workflows/validate.yaml`'s `python-tests` job, gated by the same
`scripts/ci/**` (plus `.github/workflows/validate.yaml` / `.mise.toml`) path filter as the
shell-script jobs above. It installs `python-hcl2` and `litellm[proxy]==1.98.0` (matching
the pinned cluster proxy image), installs native `promtool` via
`aqua:prometheus/prometheus@3.2.1` for PromQL rule evaluation (this runner has no podman),
`aqua:mitsuhiko/minijinja` so `schematic-pcie-port-pm-test.py` can render
`talos/schematic.yaml.j2` the same way the talos job does, and Node 24 + pinned
`renovate@44.52.1` (via the same `actions/setup-node` pin as `renovate-config`, with
`RENOVATE_NODE_PATH` exported) so `talos-renovate-pin-test.py` can drive Renovate's own
compiled matchers rather than grepping config text, then runs every
`scripts/ci/*-test.py` in a loop and fails the job if any of them fails.
This is a deliberately slower job in exchange for the tests exercising the real `litellm`
library rather than a stub or a soft-skip - see the job's own comments in `validate.yaml`
before changing that trade-off. It also `needs:` the lighter validate jobs so that pip
install does not overlap their `mise-action` Setup Tools; measured contention and the
timeout numbers live in the workflow header.
