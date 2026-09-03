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
| `kopiur-stage1-test.py` | kopiur Stage 1: reusable backup component (ceph+r2 only, Ephemeral cache, pinned Retain deletion), and the credential contract - all three `credentialProjection` legs wired plus zero standing per-namespace credential objects (fleet coverage is owned by stage3). The Stage 1 pilot app `downloads/autobrr` was removed 2026-09-02, so both renders run from recorded substitute maps and the pilot assertions inverted to require its absence |
| `kopiur-stage2-test.py` | kopiur Stage 2: the fidelity volume stays onboarded (`sabnzbd`; the exact fleet set moved to stage3, and `autobrr`'s app was removed 2026-09-02), `KOPIUR_CLAIM=sabnzbd-config` + load-bearing `KOPIUR_PUID/PGID=2000`, volsync RETIRED from this claim (Stage 5) with the claim surviving via `components/kopiur/pvc`, drill doc result contract (both-destination sha256 digest, findings 1-2, proved VolSync simultaneity with observed lastSync times) |
| `schematic-pcie-port-pm-test.py` | Arc B70 `pcie_port_pm=off` schematic kernel-arg contract |
| `igpu-xe-allowids-test.py` | scoping `gpu.intel.com/xe` to the iGPU |
| `b70-vaapi-tdarr-test.py` | B70 VA-API restore: `b70-vaapi` native mounts, hashed plugin config rollout, tdarr-node resource/CPU fallback, libdrm DEVNAME reopen trap |
| `tdarr-flow-nodes-test.py` | Tdarr safe-transcode recovery harness: runs `docs/tdarr/flow-nodes/behavior-test.js` (guard_scope, encoder-aware cargs, subconform, after-flow `inputsDB.code`, provenance labels) so the doc's "Re-checked by CI" claims actually self-defend |
| `litellm-auto-router-test.py` | D3 complexity-tier auto-router config |
| `litellm-claude-code-subscription-test.py` | Claude Code Max/Pro subscription pass-through |
| `litellm-fallback-chain-test.py` | Phase 5 LiteLLM availability/context fallback chains |
| `litellm-anthropic-passthrough-test.py` | Gateway-level close of LiteLLM `/anthropic` pass-through (HTTPRouteFilter directResponse 404, catch-all backend preserved) |
| `litellm-pr-reviewer-test.py` | AI PR reviewer workflow + `pr-review-local` / `ai-pr-review` D4 contracts |
| `litellm-request-logs-test.py` | full prompt/response capture in LiteLLM spend logs |
| `litellm-sso-test.py` | LiteLLM UI SSO through Authentik |
| `terraform-ci-workflows-test.py` | terraform-diff / terraform-publish CI contract |
| `tofu-authentik-stack-test.py` | the Authentik OpenTofu adoption stack |
| `validate-contention-test.py` | validate.yaml runner-pool contention timeouts + python-tests ordering; every per-job filter pattern must also be reachable from `on.pull_request.paths` (dead-filter regression that left `docs/tdarr/**` untested until 2026-09-01) |
| `workflow-hardening-test.py` | GitHub Actions workflow permissions/concurrency hardening |
| `recyclarr-quality-profile-test.py` | Radarr SQP-1 recyclarr fix (guide min_format_score, trash_id matching, 1080p profile) |
| `renovate-binding-conditions-test.py` | 2026-08-22 keep-in-cluster Renovate conditions vs shipped chart+CronJob: autoMerge last-match-wins exclusion for `kubernetes/apps/base/renovate/**`, silence alerts present, no hostRules/RENOVATE_HOST_RULES (condition 3 retired) |
| `recyclarr-config-readable-check-test.py` | recyclarr-config kopiur mover readability procedure (2026-08-31): executes measure.sh from the procedure doc against fixtures (empty-file / lost+found / walk-error traps), pins live 2913/2913 verdict + overlay/Readme pointers |
| `pvc-writable-check-test.py` | system/pvc-writable-check: real CronJob script vs mock kubectl (motivating unwritable bugs, clean sweep, readOnly/skip/excluded-NS/shell-less/kubectl-error skips, RO-rootfs large JSON, split-role RBAC, PrometheusRule, headlamp readOnly mount) |
| `pvc-mover-readable-check-test.py` | system/pvc-mover-readable-check: walk.sh traps (busybox find/stat, RO /tmp, lost+found, no-mutate) + CronJob vs mock kubectl (live-CR identities, kopiur-alert/VolSync-report-only, UNMEASURED/INCONCLUSIVE split, ordered exec-failure classifier) + split-role RBAC (5 NS, no cluster-wide exec) + PrometheusRule + overlay wiring |
| `backup-silent-failure-alerting-test.py` | VolSync stalled-sync + kopiur empty-backup PrometheusRules: promtool unit-tests fire for stuck ai/opencode, stay silent for healthy fleet / recyclarr / jellyfin leaked series, and KopiurBackupEmpty on zero files |
| `corrupt-claim-recreation-contract-test.py` | ai/opencode volume recreation contract: rendered volsync PVC `dataSourceRef`→RD (not restic) + RD `restore-once`/`IfNotPresent`/`fsGroup` empty-latestImage trap, ceph-block `reclaimPolicy: Delete`, runbook+evidence result gates, both AGENTS.md findings, no credential contents |
| `kopiur-stage3-test.py` | kopiur fleet parallel-run pin: 30/30 onboarded set + measured mover identity (`EXPECTED_IDENTITY`, includes changedetection-config 1000:1000 and matter-server 0:0), per-namespace r2 hour, empty `DEFERRED_CLAIMS`, and the EXACT dual-engine set - VolSync on every claim except `RETIRED_CLAIMS` (the Stage 5 four), checked both ways so neither an unrecorded retirement nor a half-reverted one passes |
| `selfhosted-backup-identity-test.py` | selfhosted dead `APP_UID`/`APP_GID` cleanup + `obsidian-livesync` VolSync mover 1000→5984 latent-defect pin: substitute maps lack the dead keys, kustomize+envsubst renders volsync/kopiur movers to the measured identity, and dropping `VOLSYNC_PUID` collapses obsidian back to the 1000 component default |
| `kopiur-timezone-test.py` | kopiur/VolSync cron timezone alignment: rendered `SnapshotSchedule.spec.schedule.timezone` default + override, pre-fix EST total ceph collision, post-fix zero collision across all dual-engine claims in both DST seasons |
| `kopiur-projected-secrets-leak-alert-test.py` | `KopiurProjectedCredentialsLeaking` multi-pass census semantics: live PrometheusRule must use `min_over_time(...[13h])` (not bare `> 0` / `for: 1h`); promtool unit-tests silent on the 6h benign plateau, fire on a permanent leak across 2+ sweeps, silent on healthy zero |
| `kopiur-stage4-test.py` | kopiur Stage 4: `home-automation/matter-server` onboarded with explicit `KOPIUR_PUID/PGID: 0`, GitOps privileged-mover annotation on the overlay Namespace, sibling movers unchanged |
| `kopiur-stage5-test.py` | kopiur Stage 5 retirement pilot: the four retired overlays (`ai/repo-wiki`, `downloads/recyclarr-config`, `downloads/sabnzbd-config`, `media/seerr`) carry no volsync Component / dependency / `VOLSYNC_*` key, keep their claim via `components/kopiur/pvc` (rendered per-app: `ssa: IfNotPresent`, never the `force` label, `dataSourceRef` -> the kopiur `Restore`, capacity == the live claim), sabnzbd's restore cache raised per restore-proof finding 2, the retired set agrees with stage3's `RETIRED_CLAIMS`, no dual-engine app adds the pvc Component, and the pilot/proof/Readme docs tell one story |
| `kopiur-restore-cache-sizing-test.py` | kopiur r2 restore cache gate (2026-09-02): parsed Flux `postBuild.substitute` pins `ai/hermes` 16Gi and `media/plex` 10Gi, self-checks pins clear the measured ~6.2 GiB plateau, fleet-wide Gi/Mi parse guard, proof doc present |
| `kopiur-epoch-tuning-test.py` | kopiur `ceph` epoch tuning (2026-09-03): parsed manifest pins `parameters.epoch.minDuration` inside the measured `[3.5h, 4.75h]` plateau, self-checks the plateau's worst-case epoch still clears the 1000-blob threshold with >=30% headroom at the measured 32.5 blobs/h, pins `r2` as deliberately untuned WITH its explanatory comment, and refuses any `spec.health.indexBlobWarnThreshold` on either repository. Headroom STEPS rather than slopes (epoch age runs from the first index blob, not the epoch marker), so this catches rounding the value to a tidier number - 5h and the 6h the alert message suggests both fall off the plateau into the same ~19% band - and catches silencing the warning instead of fixing it |
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

Wired into `.github/workflows/validate.yaml`'s `python-tests` job. Path filtering is
two-level and the trigger wins: a path must appear in both `on.pull_request.paths` and
the job's own filter, or the workflow never starts and the per-job pattern is dead.
`python-tests` covers `scripts/ci/**`, `docs/tdarr/**`, `docs/tdarr-errored-remuxes.md`,
`.github/workflows/validate.yaml`, and `.mise.toml` at both levels (the shell-script
jobs share the `scripts/ci/**` / workflow / `.mise.toml` subset only).
`validate-contention-test.py::test_trigger_paths_cover_job_filters` fails if any
per-job pattern becomes unreachable from the trigger again. It installs `python-hcl2` and `litellm[proxy]==1.98.0` (matching
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
