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

## Tests (scripts/ci)

| Source | Fact or invariant | Prevents | Verdict | Lands in |
|---|---|---|---|---|
| `scripts/ci/agent-guard-test.py` | Blocks owner-only cluster, Talos, Terraform and GitHub-settings writes by agents, including through wrappers. | neither |  |  |
| `scripts/ci/ai-review-gate-test.py` | The AI review gate blocks at the threshold and returns no decision on refusal or a missing answer. | neither |  |  |
| `scripts/ci/alert-coverage-gaps-test.py` (# ci: gate) | Backup-age, WAL-archiver and vanished-kopiur-policy alerts are promtool-valid and can actually fire. | data loss |  |  |
| `scripts/ci/b70-bench-harness-test.py` | The B70 depth sweep and compare helpers produce labelled, side-by-side results. | neither |  |  |
| `scripts/ci/b70-vaapi-tdarr-test.py` | The B70 VA-API restore through tdarr and generic-device-plugin stays wired to the renamed DRM nodes. | neither |  |  |
| `scripts/ci/backup-silent-failure-alerting-test.py` (# ci: gate) | Stalled VolSync movers and empty or stale kopiur backups raise alerts. | data loss |  |  |
| `scripts/ci/books-namespace-move-test.py` | Shelfmark lives in downloads, where its RWX claim is. | neither |  |  |
| `scripts/ci/chat-model-id-test.py` | The loaded chat model's name is defined in one place. | neither |  |  |
| `scripts/ci/ci-success-workflow-test.py` (# ci: gate) | The single required CI Success check reflects Flate and Validate and still reports while the cluster is down. | neither |  |  |
| `scripts/ci/ci-test-gate-test.py` (# ci: gate) | Only tests marked ci: gate block PRs, and the python-tests job keeps the data-loss and outage tests. | neither |  |  |
| `scripts/ci/cleanuparr-contract-test.py` | Cleanuparr keeps a small blast radius: it sets no env vars it ignores and injects no secret. | neither |  |  |
| `scripts/ci/corrupt-claim-recreation-contract-test.py` (# ci: gate) | Recreating a corrupt claim from a frozen latestImage is caught, since a PVC delete does not restore from the repository. | data loss |  |  |
| `scripts/ci/cpu-throttle-contract-test.py` | The 2026-09-20 fleet CPU right-sizing stays in place. | neither |  |  |
| `scripts/ci/cwa-reader-sync-test.py` | Calibre-Web-Automated is pinned to 4.0.8 with OPDS, Kobo and KOReader sync and stays LAN-only. | neither |  |  |
| `scripts/ci/doc-links-test.py` | Repo-local links in docs, skills and pointer files resolve. | neither |  |  |
| `scripts/ci/docs-budget-test.py` | Always-loaded agent context, skills and config comments stay under a hard size budget. | neither |  |  |
| `scripts/ci/ducklake-contract-test.py` | Pins the DuckLake parts that fail silently or destroy data. | data loss |  |  |
| `scripts/ci/embedding-gpu-prompt-cache-test.py` | ai/embedding-gpu keeps its host prompt-cache bound and the no-hang load flags. | neither |  |  |
| `scripts/ci/embedding-values-test.py` | The ai/embedding-gpu health probe asserts on returned values, not just liveness. | neither |  |  |
| `scripts/ci/falkordb-lan-browser-access-test.py` | FalkorDB LAN LoadBalancer and gated browser UI match their contract. | neither |  |  |
| `scripts/ci/flux-annotation-values-test.py` (# ci: gate) | Every kustomize.toolkit.fluxcd.io annotation uses a value Flux acts on, such as exactly enabled. | neither |  |  |
| `scripts/ci/flux-not-ready-alert-test.py` | FluxResourceNotReadyTooLong fires for a Flux resource stuck not Ready. | neither |  |  |
| `scripts/ci/flux-suspended-alert-test.py` | FluxResourceSuspendedTooLong covers every declared Flux source kind. | neither |  |  |
| `scripts/ci/fstrim-failed-alert-test.py` | The FstrimJobFailed alert fires when the fstrim CronJob fails. | neither |  |  |
| `scripts/ci/fstrim-pv-selection-test.py` | The fstrim discard path includes kubelet-mounted Ceph PVs, which the old grep filtered out. | neither |  |  |
| `scripts/ci/functional-comments-guard-test.py` | Functional comments such as renovate annotations stay captured by their customManager. | neither |  |  |
| `scripts/ci/goal-metrics-test.py` | The daily goal-metrics recorder behaves as its unit test pins. | neither |  |  |
| `scripts/ci/gpu-node-dashboard-test.py` | The Intel GPU node Grafana dashboard matches its semantic contract. | neither |  |  |
| `scripts/ci/grafana-dashboard-gaps-test.py` | The home, Ceph and LiteLLM Grafana boards exist and the TrueNAS boards are retired. | neither |  |  |
| `scripts/ci/grafana-mcp-deploy-test.py` (# ci: nightly) | The in-cluster grafana-mcp ToolHive MCPServer matches its deploy contract. | neither |  |  |
| `scripts/ci/grafana-sa-provisioner-test.py` | grafana-sa-provisioner behaves and is wired as its behavioral and semantic tests pin. | neither |  |  |
| `scripts/ci/hermes-discord-test.py` | The Hermes Discord bot answers one dedicated channel, for allow-listed users only. | neither |  |  |
| `scripts/ci/hermes-hooks-test.py` | The Hermes terminal guard blocks catastrophic commands, including wiping Hermes' own state. | data loss |  |  |
| `scripts/ci/hermes-media-mcp-test.py` | Hermes can look up media and queue titles through Seerr, never delete or approve. | neither |  |  |
| `scripts/ci/hermes-secret-scope-test.py` | Hermes resolves secrets from the profile secret scope, never from the container env. | neither |  |  |
| `scripts/ci/hermes-state-db-retention-test.py` | The ai/hermes state.db retention keys stay pinned, including vacuum_after_prune false. | neither |  |  |
| `scripts/ci/hostpolicy-ceph-selector-test.py` (# ci: gate) | The Ceph host CCNP selector never default-denies all three nodes and always attaches to host endpoints. | outage |  |  |
| `scripts/ci/igpu-xe-allowids-test.py` (# ci: nightly) | gpu.intel.com/xe is scoped to the iGPU device ID. | neither |  |  |
| `scripts/ci/kopiur-epoch-tuning-test.py` | kopiur ceph epoch tuning matches the measured maintenance clock. | data loss |  |  |
| `scripts/ci/kopiur-projected-secrets-leak-alert-test.py` | The KopiurProjectedCredentialsLeaking alert no longer false-fires on the periodic leader census. | neither |  |  |
| `scripts/ci/kopiur-restore-cache-sizing-test.py` | KOPIUR_CACHE_CAPACITY is sized as a restore correctness constraint, not a tuning knob. | data loss |  |  |
| `scripts/ci/kopiur-stage0-test.py` (# ci: gate) | Stage 0 keeps the kopiur operator CRDs non-replacing and the r2 repository from being re-created. | data loss |  |  |
| `scripts/ci/kopiur-stage1-test.py` (# ci: gate) | The shared kopiur backup component keeps fail-closed restores, ssa IfNotPresent and credential-free mounts. | data loss |  |  |
| `scripts/ci/kopiur-stage2-test.py` | A restore from both kopiur destinations is proven on a volume that holds data, with VolSync retired there. | data loss |  |  |
| `scripts/ci/kopiur-stage3-test.py` (# ci: gate) | Every VolSync-protected claim stays in kopiur coverage under the correct mover identity. | data loss |  |  |
| `scripts/ci/kopiur-stage4-test.py` | The matter-server root-mover kopiur onboarding carries no VolSync component and restores correctly. | data loss |  |  |
| `scripts/ci/kopiur-stage5-test.py` (# ci: gate) | Retired VolSync overlays keep the kopiur pvc Component and no-force rule so live volumes are not replaced. | data loss |  |  |
| `scripts/ci/kopiur-timezone-test.py` | kopiur and VolSync cron schedules carry aligned timezones so DST does not collide dual-engine backups. | data loss |  |  |
| `scripts/ci/kubernetes-renovate-group-test.py` | The Kubernetes Renovate group and its auto-merge guard behave as specified. | neither |  |  |
| `scripts/ci/litellm-ai-hub-test.py` | What the LiteLLM AI Hub publishes and how it renders matches its contract. | neither |  |  |
| `scripts/ci/litellm-anthropic-passthrough-test.py` | LiteLLM's built-in /anthropic pass-through stays closed. | neither |  |  |
| `scripts/ci/litellm-auto-router-test.py` | The D3 complexity-tier auto-router config validates behaviourally. | neither |  |  |
| `scripts/ci/litellm-claude-code-subscription-test.py` | The Claude Code Max/Pro subscription pass-through validates behaviourally. | neither |  |  |
| `scripts/ci/litellm-embedding-limit-test.py` | The embedding-local alias keeps load caps on the shared B70 embedder. | neither |  |  |
| `scripts/ci/litellm-fallback-chain-test.py` | The Phase 5 availability and context fallback chains validate. | neither |  |  |
| `scripts/ci/litellm-passthrough-lockdown-test.py` | LiteLLM's built-in provider pass-throughs stay admin-only. | neither |  |  |
| `scripts/ci/litellm-pgvector-test.py` | The ai/litellm-pgvector vector store stays wired end to end. | neither |  |  |
| `scripts/ci/litellm-pr-reviewer-test.py` (# ci: nightly) | The AI PR reviewer action behaves per its behavioral contract. | neither |  |  |
| `scripts/ci/litellm-request-logs-test.py` | Full prompt and response capture in LiteLLM spend logs validates. | neither |  |  |
| `scripts/ci/litellm-sso-test.py` (# ci: nightly) | LiteLLM UI SSO through Authentik behaves as specified. | neither |  |  |
| `scripts/ci/media-language-audit-test.py` | classify() decides whether a Radarr movie satisfies the English-audio policy. | neither |  |  |
| `scripts/ci/media-profile-migration-test.py` | plan_moves() maps movies on the unmanaged profile to the managed catalogue profiles. | neither |  |  |
| `scripts/ci/media-queue-watchdog-test.py` | The media-queue-watchdog CronJob matches its contract. | neither |  |  |
| `scripts/ci/memory-request-declared-test.py` | No container declares limits.memory without requests.memory, which would reserve the whole limit. | neither |  |  |
| `scripts/ci/mise-install-args-test.py` | mise-action install_args do not re-pin tools that .mise.toml already declares. | neither |  |  |
| `scripts/ci/plex-renovate-versioning-test.py` | The Plex regex-versioning Renovate rule behaves as specified. | neither |  |  |
| `scripts/ci/pvc-mover-readable-check-test.py` (# ci: gate) | The backup mover uid can read each claim it backs up, or the check reports failure. | data loss |  |  |
| `scripts/ci/pvc-writable-check-test.py` (# ci: gate) | An app that cannot write its own claim is caught even while Ready. | data loss |  |  |
| `scripts/ci/readarr-removal-test.py` | Readarr and reading-glasses are gone from Git while their data and databases are kept. | data loss |  |  |
| `scripts/ci/recyclarr-config-readable-check-test.py` | The recyclarr-config readable check runs on a snapshot scratch copy and never touches the live claim. | data loss |  |  |
| `scripts/ci/recyclarr-language-policy-test.py` (# ci: nightly) | Every Radarr movie carries an English audio track. | neither |  |  |
| `scripts/ci/recyclarr-quality-profile-test.py` (# ci: nightly) | The Radarr SQP-1 recyclarr quality-profile fix holds. | neither |  |  |
| `scripts/ci/renovate-binding-conditions-test.py` | Keep-in-cluster Renovate binding conditions evaluate under last-match-wins. | neither |  |  |
| `scripts/ci/risk-tier-test.py` (# ci: gate) | The PR risk-tier classifier never labels a cluster-core path change risk:low. | neither |  |  |
| `scripts/ci/rsshub-renovate-schedule-test.py` | The rsshub digest-update throttle behaves as specified. | neither |  |  |
| `scripts/ci/samba-shared-xml-test.py` (# ci: nightly) | Each LAN Samba share sits on its own ceph-filesystem-rwx claim, not the default csi class. | neither |  |  |
| `scripts/ci/schematic-pcie-port-pm-test.py` (# ci: nightly) | The talos-3 B70 PCIe runtime-PM mitigation is in the schematic. | neither |  |  |
| `scripts/ci/seerr-friends-contract-test.py` | Seerr is reachable by friends from outside the LAN. | neither |  |  |
| `scripts/ci/selfhosted-backup-identity-test.py` | The selfhosted backup identity stays pinned so the mover uid matches the claim's file modes. | data loss |  |  |
| `scripts/ci/shelfmark-usenet-contract-test.py` | Shelfmark runs the upstream lite image, Usenet only, in downloads. | neither |  |  |
| `scripts/ci/silent-failure-gaps-alert-test.py` | ContainerRestartingChronically and ExternalSecretNotReady alerts fire. | neither |  |  |
| `scripts/ci/syncthing-data-capacity-test.py` | The syncthing-data 15Gi right-size contract holds. | neither |  |  |
| `scripts/ci/talos-renovate-pin-test.py` | The Talos Renovate pin excludes exactly v1.13.3 and the auto-merge guard holds. | neither |  |  |
| `scripts/ci/tdarr-flow-nodes-test.py` | CI runs the Tdarr safe-transcode Node harness and fails closed if it or Node is missing. | neither |  |  |
| `scripts/ci/terraform-ci-workflows-test.py` (# ci: nightly) | The terraform-diff and terraform-publish CI workflows meet their behavioral contract. | neither |  |  |
| `scripts/ci/timescaledb-community-test.py` | The TimescaleDB Community extension image build for postgres-18 is guarded. | neither |  |  |
| `scripts/ci/tofu-authentik-stack-test.py` (# ci: nightly) | The Authentik OpenTofu adoption stack passes its behavioral checks. | neither |  |  |
| `scripts/ci/toolhive-session-probe-test.py` | The ToolHive MCP Session Gatus check is semantically correct. | neither |  |  |
| `scripts/ci/toolhive-vmcp-scratch-test.py` | The vmcp tool optimizer builds a SQLite FTS5 index per session. | neither |  |  |
| `scripts/ci/validate-contention-test.py` | validate.yaml's runner-pool contention hardening holds. | neither |  |  |
| `scripts/ci/vllm-fa-onednn-test.py` | ai/vllm keeps llama.cpp's oneDNN SDPA path off. | neither |  |  |
| `scripts/ci/vllm-memory-alert-test.py` | The ai/vllm memory-approach PrometheusRule behaves as specified. | neither |  |  |
| `scripts/ci/vllm-prompt-cache-test.py` | The ai/vllm host prompt-cache bound holds. | neither |  |  |
| `scripts/ci/volsync-retired-expiry-test.py` (# ci: gate) | The retired-repository expiry prefix never matches the live syncthing-data repository. | data loss |  |  |
| `scripts/ci/wizarr-contract-test.py` | Wizarr is a born-kopiur app with its own RWO claim and an invite-only external route. | data loss |  |  |
| `scripts/ci/wizarr-seed-test.py` | Wizarr's setup is seeded from GitOps secrets by an idempotent init container. | neither |  |  |
| `scripts/ci/workflow-hardening-test.py` | The homeops-norms-iac CI workflows meet their hardening contract. | neither |  |  |
| `scripts/ci/xe-gpu-exporter-test.py` | system/xe-gpu-exporter turns DRM fdinfo into VRAM series. | neither |  |  |

## Skills (.agents/skills)

| Source | Fact or invariant | Prevents | Verdict | Lands in |
|---|---|---|---|---|
| `.agents/skills/ai-stack/SKILL.md` §Tripwires | Heading for the nine numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Where things live | Path table for the ai namespace: ToolHive, vmcp, MCP servers and the inventory pointer. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Procedures | Links to the ai inventory, ToolHive, consumers and retirement references. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Verify | Runs the toolhive session probe test and notes that a green /health does not prove a session opens. | neither |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Tripwires | Heading for the seven numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Where things live | Path table for app HelmReleases, the writable check and the linuxserver example. | neither |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Procedures | Links to the pod-security and probes references. | neither |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Verify | Reads securityContext, volumeMounts and strategy from the rendered pod, not from values keys. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Tripwires | Heading for the eight numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Where things live | Path table for the terraform/authentik stack, its vals files and conventions. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Procedures | Links to the apply runbook, state and credentials, and CI plan references. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Verify | Runs tofu validate, the authentik stack and terraform CI tests, then reviews the plan. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires | Heading for the eleven numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Where things live | Path table for the vllm and embedding-gpu HelmReleases, alerts and scripts. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Procedures | Links to the flags, memory, embeddings and contention references. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Verify | Runs the vLLM and embedding prompt-cache, oneDNN and memory-alert tests. | neither |  |  |
| `.agents/skills/cilium-host-policy/SKILL.md` §Tripwires | Heading for the five numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/cilium-host-policy/SKILL.md` §Where things live | Path table for the host policy, the hostFirewall switches and the Cilium chart tag. | neither |  |  |
| `.agents/skills/cilium-host-policy/SKILL.md` §Procedures | Run the gates reference after any policy edit, and roll back first if management traffic drops. | neither |  |  |
| `.agents/skills/cilium-host-policy/SKILL.md` §Verify | Runs the Ceph host-policy selector test and the live CCNP validity check. | neither |  |  |
| `.agents/skills/databases/SKILL.md` §Tripwires | Heading for the six numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/databases/SKILL.md` §Where things live | Path table for FalkorDB, SurrealDB, CNPG clusters and the other database apps. | neither |  |  |
| `.agents/skills/databases/SKILL.md` §Procedures | Links to the FalkorDB, SurrealDB and CNPG references. | neither |  |  |
| `.agents/skills/databases/SKILL.md` §Verify | Runs flux:test:all, GRAPH.LIST for FalkorDB, and CNPG readiness checks. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires | Heading for the eleven numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Where things live | Path table for Flux entry points, app manifests, overlays and the namespace list. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Procedures | Links to the app-shapes and live-testing references. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Verify | Runs flux:test:all unscoped and checks non-Ready Kustomizations and HelmReleases live. | neither |  |  |
| `.agents/skills/flux-substitution/SKILL.md` §Tripwires | Heading for the three numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/flux-substitution/SKILL.md` §Where things live | Path table for postBuild.substitute, the cluster-secrets Secret and the per-resource disable annotation. | neither |  |  |
| `.agents/skills/flux-substitution/SKILL.md` §Verify | Searches built output for ${ tokens that are not Flux variables and checks live Kustomizations. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Tripwires | Heading for the nine numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Where things live | Path table for workflows, the scripts/ci README, runner manifests and the Talos role. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Procedures | Links to the ARC runner, workflow and scripts/ci references. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Verify | Runs one gate, or the ci: gate loop that the PR gate uses. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires | Heading for the sixteen numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Where things live | Path table for the Hermes config, runtime skills and HelmRelease. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Procedures | Links to the state.db, restart and LLM routing references. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Verify | Runs the Hermes state-db, secret-scope and hooks tests, then the live hooks doctor. | neither |  |  |
| `.agents/skills/home-automation/SKILL.md` §Tripwires | Heading for the four numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/home-automation/SKILL.md` §Where things live | Path table for the Zigbee2MQTT ExternalSecret and the Home Assistant HelmRelease. | neither |  |  |
| `.agents/skills/home-automation/SKILL.md` §Procedures | Links to the Zigbee2MQTT and Home Assistant references. | neither |  |  |
| `.agents/skills/home-automation/SKILL.md` §Verify | After a Zigbee identity change, checks the adapter log lines and that configuration.yaml is unchanged. | neither |  |  |
| `.agents/skills/image-generation/SKILL.md` §Models | Table of the three image models, their aliases, uses and costs. | neither |  |  |
| `.agents/skills/image-generation/SKILL.md` §Key, cache and failures | How the image key is read, cached and reported when it fails. | neither |  |  |
| `.agents/skills/image-generation/SKILL.md` §Taste: how to get professional images | Steps for choosing purpose, placement and art direction before writing an image prompt. | neither |  |  |
| `.agents/skills/implement-task/SKILL.md` §Worktree | Creates a fresh worktree from origin/main on a task branch, never on main. | neither |  |  |
| `.agents/skills/implement-task/SKILL.md` §Model (§6) | Maps task type to a model tier, with judgment work on the strongest tier. | neither |  |  |
| `.agents/skills/implement-task/SKILL.md` §Build | Writes or extends the failing test first for a scripts/ci contract, then implements. | neither |  |  |
| `.agents/skills/implement-task/SKILL.md` §Local gates (what CI Success checks) | Runs the ci: gate tests and the doc gates locally before opening a PR. | neither |  |  |
| `.agents/skills/implement-task/SKILL.md` §Review, then PR | Runs an independent review, pushes the branch and opens the PR with the required sections. | neither |  |  |
| `.agents/skills/implement-task/SKILL.md` §After merge | Watches the affected Flux objects and pods read-only until they are healthy. | neither |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Tripwires | Heading for the seven numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Where things live | Path table for the device-plugin config, the iGPU plugin allowIDs and the GPU HelmReleases. | neither |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Procedures | Links to device groups, VA-API check, telemetry, DRA and second-card references. | neither |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Verify | Runs the VA-API check on tdarr-node after any device-plugin or kernel change. | neither |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires | Heading for the sixteen numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Where things live | Path table for the kopiur component, operator and per-claim KOPIUR_ overlays. | neither |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Procedures | Links to onboarding, retirement, restore and cache, and drill references. | neither |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Verify | Runs the kopiur stage 0 to 5 tests and the timezone, epoch, cache and alert tests. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires | Heading for the sixteen numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Where things live | Path table for the proxy CR, model CRs, keys and the operator. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Procedures | Links to the model catalog, governance-keys and fallback references. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Verify | Runs the litellm test scripts offline and task flux:test:all. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires | Heading for the eleven numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Where things live | Path table for the downloads and media apps, Recyclarr config and shared downloads. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Procedures | Links to Recyclarr, backlog search and Plex references. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Verify | Runs the docs-budget and doc-links tests and the Tdarr behavior test. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires | Heading for the ten numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Where things live | Path table for Cilium, Envoy Gateway and the public and internal DNS apps. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Procedures | Links to the Envoy, DNS ownership and BGP references. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Verify | Checks UniFi DNS records through a port-forward and watches Cilium drops for a new NetworkPolicy. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Tripwires | Heading for the eight numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Where things live | Path table for the talos-3 taint, the talos-3 ledger and the sizing method. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Procedures | Links to the sizing method and talos-3 ledger references. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Verify | Runs the memory-request and CPU-throttle tests and reads the live talos-3 taint. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Tripwires | Heading for the nine numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Where things live | Path table for Alertmanager, Gatus, kube-prometheus-stack and Grafana. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Procedures | Links to the rule-traps, Alertmanager, Gatus and scrape-target references. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Verify | Runs the docs-budget and doc-links tests and checks each rule expression against live Prometheus. | neither |  |  |
| `.agents/skills/plan-task/SKILL.md` §Before planning | Checks the open agent PR limit of two and the owner-approval step before planning. | neither |  |  |
| `.agents/skills/plan-task/SKILL.md` §Risk tier | Forecasts the PR's risk tier from its planned paths with risk_tier.py. | neither |  |  |
| `.agents/skills/plan-task/SKILL.md` §The plan comment | Template for the plan comment posted on the issue. | neither |  |  |
| `.agents/skills/plan-task/SKILL.md` §Plan | Lists tier, implementer and reviewer tiers, approach, files and rollback in the plan. | neither |  |  |
| `.agents/skills/plan-task/SKILL.md` §Splitting | Splits a task into two issues when two tiers or subsystems can merge independently. | neither |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Tripwires | Heading for the nine numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Where things live | Table of the writable and mover-readable checks, their paths and schedules. | neither |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Verify | Runs the writable, mover-readable and recyclarr-config readable tests. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires | Heading for the ten numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Where things live | Path table for the Renovate preset chain, rule files and the in-cluster CronJob. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Procedures | Links to the versioning-traps and operations references. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Verify | Runs the Talos Renovate pin test and points to the sibling Renovate harnesses. | neither |  |  |
| `.agents/skills/renovate-review/SKILL.md` §Triage | Lists open Renovate PRs by label and opens an issue per major bump. | neither |  |  |
| `.agents/skills/renovate-review/SKILL.md` §Check one PR | Steps to review release notes and the rendered diff for one Renovate PR. | neither |  |  |
| `.agents/skills/renovate-review/SKILL.md` §Comment, never merge | Posts a recommendation comment with breaking changes and owner steps, and never merges. | neither |  |  |
| `.agents/skills/review-pr/SKILL.md` §Brief template | Template for briefing a fresh independent reviewer with the diff and task. | neither |  |  |
| `.agents/skills/review-pr/SKILL.md` §Focus by change type | Lists what a reviewer checks for manifests, workflows and other change types. | neither |  |  |
| `.agents/skills/review-pr/SKILL.md` §Acting on findings | Rules for fixing blocking findings, verifying disputed ones, and handling nits. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires | Heading for the eleven numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Where things live | Path table for the Ceph cluster HelmRelease, operator chart and backup manifests. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Procedures | Links to the Ceph baseline, decisions and RGW references. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Verify | Runs flux:test:all and read-only ceph status and config dump checks. | neither |  |  |
| `.agents/skills/secrets-1password/SKILL.md` §Tripwires | Heading for the six numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/secrets-1password/SKILL.md` §Where things live | Path table for ExternalSecrets, the shared and single-vault stores, and the Connect token. | neither |  |  |
| `.agents/skills/secrets-1password/SKILL.md` §Procedures | Links to the 1Password item recipes reference. | neither |  |  |
| `.agents/skills/secrets-1password/SKILL.md` §Verify | Checks ExternalSecret Ready and non-zero decoded key lengths, and the PushSecret store. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Tripwires | Heading for the nine numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Where things live | Path table for k8tz, tuppr and the CREATE-only webhook repair reference. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Procedures | Links to the k8tz and webhook-drift references and the node reboot skill. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Verify | Checks k8tz pod layout, CoreDNS Corefile settings and node state after a reboot. | neither |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Tripwires | Heading for the nine numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Where things live | Path table for the shared machine config, per-node overlays, schematic and tuppr CRs. | neither |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Procedures | Links to the operations, power-down-up and talos-3 B70 reboot references. | neither |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Verify | Renders a node config offline and validates it with talosctl. | neither |  |  |
| `.agents/skills/tdarr-transcoding/SKILL.md` §Tripwires | Heading for the six numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/tdarr-transcoding/SKILL.md` §Where things live | Path table for the Tdarr HelmRelease, the rebuild runbook and the flow restore document. | neither |  |  |
| `.agents/skills/tdarr-transcoding/SKILL.md` §Procedures | Links to the mechanisms and masters references and the restore steps. | neither |  |  |
| `.agents/skills/tdarr-transcoding/SKILL.md` §Verify | Runs the tdarr flow-nodes test and requires behavioral markers in a job report. | neither |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Tripwires | Heading for the nine numbered tripwire rows that follow. | neither |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Where things live | Path table for the VolSync component, operator and carve-out overlays. | neither |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Verify | Runs the retired-expiry and corrupt-claim recreation tests and forbids dumping Secrets to check access. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Tripwires 1 | MCPServer is toolhive.stacklok.dev/v1alpha1, never kagent.dev; kagent and kmcp were removed. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Tripwires 2 | Do not point vmcp at ai/embedding-gpu; the GPU path was rejected for batch OOM, NaN and a shared card. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Tripwires 3 | talos-mcp is not live; the ServiceAccount controller rejects namespace ai. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Tripwires 4 | MCPServer.spec.resources lands on the proxy Deployment, so a limit there does not bound the server. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Tripwires 5 | grafana-mcp's Reloader annotation belongs on spec.podTemplateSpec, or the env-injected token never refreshes. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Tripwires 6 | kubectl-mcp-readonly has an aggregationRule, so hand-written rules are discarded; grants go on the -base role. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Tripwires 7 | Before retiring an app, grep scripts/ci/; never delete a kopiur Snapshot CR, since its finalizer deletes the kopia snapshot. | data loss |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Tripwires 8 | The MCP gateway bearer token is enforced by Envoy on the LAN route only; a NetworkPolicy closes the in-cluster Service. | neither |  |  |
| `.agents/skills/ai-stack/SKILL.md` §Tripwires 9 | opencode and repo-wiki call LiteLLM through a LiteLLMVirtualKey, never a provider key. | neither |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Tripwires 1 | A pod-options key the chart does not read is discarded, so the pod ships an empty securityContext while flate stays green. | data loss |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Tripwires 2 | fsGroup re-owns existing volume content, even with OnRootMismatch, and the chown persists after a revert. | neither |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Tripwires 3 | linuxserver images need LSIO_NON_ROOT_USER and LSIO_READ_ONLY_FS set, plus emptyDir /run and /tmp. | neither |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Tripwires 4 | A probe must be able to return non-200; n8n /healthz is unconditional, so the pod stayed Ready through an outage. | neither |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Tripwires 5 | An RWO claim needs strategy Recreate; RollingUpdate leaves the new pod unable to mount it. | neither |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Tripwires 6 | Intentional read-only is volumeMounts readOnly true; the pod-wide writable-check skip annotation is an unused escape hatch. | neither |  |  |
| `.agents/skills/app-workloads/SKILL.md` §Tripwires 7 | Never seed a Secret with client-side kubectl apply; the value lands in last-applied-configuration. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Tripwires 1 | tofu apply and destroy need an explicit, current go-ahead; a green PR and a clean plan are not that approval. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Tripwires 2 | Four traps tofu validate cannot see: grant_types, flow id versus uuid, the S3 backend failing open to AWS, and the write role reaching every flow. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Tripwires 3 | Imported providers omit client_secret, since declaring it rotates the live secret and breaks that app's logins. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Tripwires 4 | Do not turn blueprint data sources into resources; OpenTofu and the Authentik blueprint reconciler would fight. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Tripwires 5 | Authentik state holds plaintext OAuth secrets in a hand-made RGW bucket, not an ObjectBucketClaim, whose Delete reclaim would destroy it. | data loss |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Tripwires 6 | ExtAuth is one domain-wide proxy; a non-empty skip_path_regex is an auth bypass and a new namespace needs a referencegrant entry. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Tripwires 7 | Set the Authentik Base URL system setting before the 2026.11 line, where it becomes mandatory. | neither |  |  |
| `.agents/skills/authentik-terraform/SKILL.md` §Tripwires 8 | Never seed litellm-sso-credentials with client-side kubectl apply. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 1 | Both llama.cpp workloads pin --cache-ram; its absence silently restores 8192 MiB and no limits.memory can bound the leak. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 2 | Chat pins GGML_SYCL_FA_ONEDNN to a numeric "0"; words like "false" leave the leak on. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 3 | Chat must not pin --parallel or --kv-unified; embeddings must pin both. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 4 | Embeddings use --pooling last, no EOS in the input, and ctx, b and ub fixed at 512. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 5 | Never judge an embedding by shape or non-zero count; a null-filled vector passes a naive check, so assert finite values. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 6 | batch_size=4 is the measured-safe embedding point; larger batches OOM the pod. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 7 | Throttle embeddings by request rate, not concurrency, through the embedding-local LiteLLMModel settings. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 8 | vllm-embed stays replicas 0; it is not the live embedder and re-enabling it is a live re-test. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 9 | Chat memory request is 12Gi and limit 48Gi; raising --cache-ram without the alert floor fails CI. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 10 | Inside a devic.es/b70 container, /sys/class/drm/card0 is the iGPU; read the B70 by its PCI device path. | neither |  |  |
| `.agents/skills/b70-llm-serving/SKILL.md` §Tripwires 11 | A probe script under a Flux-reconciled file must not contain $, since envsubst can take down the Kustomization. | neither |  |  |
| `.agents/skills/cilium-host-policy/SKILL.md` §Tripwires 1 | A CCNP with a nodeSelector and ingress must set enableDefaultDeny ingress false, or the host endpoint drops Talos, kubelet and etcd traffic on all three nodes. | outage |  |  |
| `.agents/skills/cilium-host-policy/SKILL.md` §Tripwires 2 | The nodeSelector must match a label present on the host endpoint, not just the Node, or the policy is inert. | neither |  |  |
| `.agents/skills/cilium-host-policy/SKILL.md` §Tripwires 3 | Do not trust the host policy until Deny rows and Allow Ingress ANY both hold on every node; a missing Allow means default-deny. | outage |  |  |
| `.agents/skills/cilium-host-policy/SKILL.md` §Tripwires 4 | Ceph daemons run host-networked, so NetworkPolicy and CiliumNetworkPolicy in rook-ceph do not apply to them. | neither |  |  |
| `.agents/skills/cilium-host-policy/SKILL.md` §Tripwires 5 | hostpolicy-ceph.yaml is the only live host policy; a second one that omits tripwire 1 re-arms default-deny for every host policy. | outage |  |  |
| `.agents/skills/databases/SKILL.md` §Tripwires 1 | FalkorDB's three memory numbers move together with AOF on and the noeviction policy. | neither |  |  |
| `.agents/skills/databases/SKILL.md` §Tripwires 2 | SurrealDB does not roll back and has one writer; replicaCount 1 holds the RocksDB lock. | neither |  |  |
| `.agents/skills/databases/SKILL.md` §Tripwires 3 | A CNPG Cluster has one live barman destination; a second ScheduledBackup still reports completed and writes to the first store. | data loss |  |  |
| `.agents/skills/databases/SKILL.md` §Tripwires 4 | The EMQX operator stays on 2.2.x; 2.3.x drops the v2beta1 API this OSS broker needs. | neither |  |  |
| `.agents/skills/databases/SKILL.md` §Tripwires 5 | Dragonfly is cache-only, so a restart drops its data on purpose. | neither |  |  |
| `.agents/skills/databases/SKILL.md` §Tripwires 6 | A snapshot backup is crash-consistent; a database that has not fsynced restores a stale copy that still starts clean. | data loss |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 1 | healthChecks must target the workload, and a Kustomization owning a kopiur or VolSync claim keeps wait false. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 2 | Suspend the Kustomization before a live HelmRelease edit, or its interval reapplies main and drops the experiment. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 3 | kubectl apply cannot remove a values key on a Flux-managed HelmRelease; use kubectl replace or delete the key. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 4 | Helm cannot unset a field it never rendered, so a hand-added securityContext survives flux reconcile --force. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 5 | Namespace/flux-system is exempt from cluster-apps, so a git-only change to it never lands. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 6 | flate -n is a false green; task flux:test:all unscoped is the gate. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 7 | A stale Ready=False can hide an applied fix; do not restart cloudnative-pg mid-switchover or rook-ceph mid-OSD update. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 8 | A new app is done when it renders; an out-of-band kubectl apply of a new app is the drift GitOps prevents. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 9 | Never seed a Secret with client-side kubectl apply; use kubectl create or server-side apply. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 10 | FluxResourceSuspendedTooLong keys exported_namespace; flux_resource_info is the only live suspended signal. | neither |  |  |
| `.agents/skills/flux-gitops/SKILL.md` §Tripwires 11 | FluxResourceNotReadyTooLong is the state alert for a stuck release or source; a stuck source blocks every upgrade it serves. | neither |  |  |
| `.agents/skills/flux-substitution/SKILL.md` §Tripwires 1 | Neither flate nor task flux:test:all catches an undefined ${VAR}; the cluster runs strict substitution and can freeze the Kustomization. | neither |  |  |
| `.agents/skills/flux-substitution/SKILL.md` §Tripwires 2 | Three fixes exist, chosen by what the token is; do not escape a real missing variable or disable substitution for SECRET_DOMAIN. | neither |  |  |
| `.agents/skills/flux-substitution/SKILL.md` §Tripwires 3 | A declared substitute key that no manifest reads is not documentation; delete it. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Tripwires 1 | talosctl validate is schema-only; invalid enums, bad CIDRs and a missing install disk all pass it. | outage |  |  |
| `.agents/skills/github-ci/SKILL.md` §Tripwires 2 | Keep the fork guard on every self-hosted pull_request job, because the repo is public. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Tripwires 3 | Runners have no Kubernetes API and no Docker, so docker:// actions and docker run fail on the home-ops scale set. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Tripwires 4 | A red image-pull that passes on rerun is ARC pool contention; Flate and Validate must stay off ARC so CI Success reports. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Tripwires 5 | jdx/mise-action install_args names the tool only; a @version pin installs a different copy than the .mise.toml shim. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Tripwires 6 | Grep scripts/ci/ before retiring an app or changing a pin, since task flux:test:all does not run those tests. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Tripwires 7 | A stale runner client cert fails image-pull on every kubernetes/** PR; delete Secret actions-runner so it is reissued. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Tripwires 8 | Only Labeler - Labeler is a required check; the main-branch-protection ruleset is applied with gh api, not Git. | neither |  |  |
| `.agents/skills/github-ci/SKILL.md` §Tripwires 9 | Workflow comments are not logic; leave runs, if, with and @sha pins unchanged unless the task is a workflow change. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 1 | Any byte of config.yaml or a runtime skill restarts the pod uncleanly, and the next boot integrity-checks state.db. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 2 | Pod status and Gatus stay green while the gateway is stuck; read gateway-startup-watchdog.log and add no gateway liveness probe. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 3 | Never set sessions.vacuum_after_prune true; the interval throttle never engages and a full WAL rewrite is retried on every prune. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 4 | sessions.auto_prune must be explicit true, since both call sites default it to False. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 5 | Never set OPENAI_BASE_URL; it is a global OpenAI-SDK override and the custom:litellm provider carries everything. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 6 | Keep the image on the -desktop tag; the plain tag has no Chromium or VNC stack and cannot apt install. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 7 | Never set container args back to gateway run; the image already starts gateway-default, and a second gateway CrashLoops. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 8 | Keep the kopiur/pvc include on the overlay, with strategy Recreate and replicas 1; the claim is single-writer. | data loss |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 9 | Env-injected secrets do not refresh without reloader.stakater.com/auto on the controller. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 10 | Do not name the custom provider after a built-in id; Hermes then skips that entry's base_url. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 11 | Secrets resolve from the profile secret scope, not the container env; env alone caused 401s and blocked cron runs. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 12 | Profiles are not inherited and not in git; removing a provider silently breaks any profile that names it. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 13 | The terminal guard hook is fail-closed; a refused legitimate command means editing the script and its test, never disabling the hook. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 14 | Do not set agent.max_turns; upstream's unlimited default avoids silent mid-task truncation. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 15 | SUMMARY_LLM_MODEL must not be a thinking model, or the commit-watcher digests come back unavailable. | neither |  |  |
| `.agents/skills/hermes-agent/SKILL.md` §Tripwires 16 | Hermes holds no write-capable GitHub credential; its terminal and GitHub MCP render from the read-only token. | neither |  |  |
| `.agents/skills/home-automation/SKILL.md` §Tripwires 1 | The Zigbee2MQTT 1Password fields are the network identity; rotating PAN ID, ext PAN ID or network key re-forms the network and unpairs every device. | neither |  |  |
| `.agents/skills/home-automation/SKILL.md` §Tripwires 2 | Zigbee2MQTT writes its env vars into configuration.yaml on every start, so a non-empty wrong value is persisted. | neither |  |  |
| `.agents/skills/home-automation/SKILL.md` §Tripwires 3 | Home Assistant has a static IP and no MAC pin; a pinned MAC on macvlan races the dying pod's veth. | neither |  |  |
| `.agents/skills/home-automation/SKILL.md` §Tripwires 4 | matter-server's claim env is KOPIUR_CLAIM; a VOLSYNC_CLAIM default names a variable the overlay no longer sets. | data loss |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Tripwires 1 | Never rename a DRM node with generic-device-plugin mountPath; VA-API fails while Level Zero stays green, so transcoding dies silently. | neither |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Tripwires 2 | Device IDs hash every host path in the group, so adding a path to b70 changes all 99 IDs and drops live allocations. | neither |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Tripwires 3 | The device-plugin config is a subPath mount that kubelet never refreshes; keep the configMapGenerator hash enabled. | neither |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Tripwires 4 | There is no per-engine utilization exporter; Prometheus has the xe hwmon chip and kube-state-metrics allocation only. | neither |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Tripwires 5 | Do not migrate GPU scheduling to DRA, and never via adminAccess true; the driver is beta and cannot share one GPU. | neither |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Tripwires 6 | allowIDs 0xa7a0 scopes gpu.intel.com/xe to the iGPU and is already set; do not drop it while the B70 is an xe device. | neither |  |  |
| `.agents/skills/intel-gpu/SKILL.md` §Tripwires 7 | Do not buy a second B70 on the current recommendation; the buy-if conditions are not met. | neither |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 1 | A Snapshot CR owns its kopia snapshot; deleting the CR deletes backup data, and Flux prune true is that shape. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 2 | Retirement is a PVC-ownership swap; pvc must carry ssa IfNotPresent and never force enabled, which deletes the volume. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 3 | Mover identity is measured from the files, not runAsUser; a mismatch fails the backup closed. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 4 | The restore cache is a cliff; crossing its required capacity fails the restore terminally and never retries. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 5 | Deleting a Restore is safe; deleting a Snapshot is not. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 6 | Drills use a new scratch Restore; never set onMissingSnapshot Continue, which backs up empty volumes after a cluster loss. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 7 | Credential projection has three legs, and missing one leaves the CRs green while the mover fails at run time. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 8 | ClusterRepository.spec.parameters is write-only from Git; removing the block does not roll the live repository back. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 9 | IndexBlobHealth=False is epoch tuning, not a broken maintenance run; never commit takeoverPolicy Force. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 10 | One component include protects one volume; a second PVC needs a second Kustomization with its own APP. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 11 | Never put the kopiur component in a wait:true Kustomization; the standing Restore stays Ready=False on a bound claim. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 12 | Cron accepts bare H only, and both schedules pin America/New_York so the VolSync stagger survives DST. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 13 | A Succeeded snapshot proves nothing until .status.stats covers the volume; SecurityContextCompatible is positive-only. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 14 | Both engines snapshot crash-consistently with no application hook; a database that does not fsync restores a stale copy. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 15 | CloudNativePG has one live barman destination per Cluster; a second ScheduledBackup writes to the original store. | data loss |  |  |
| `.agents/skills/kopiur-backups/SKILL.md` §Tripwires 16 | A full-cluster restore from r2 follows its own runbook; five claims have no populator path and are hand-restored. | data loss |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 1 | A config-declared fallback bypasses the calling key's allow-list, so fallbacks sit only on aliases every holder may use. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 2 | A LiteLLMModel with no apiKey falls back to the pod's metered ANTHROPIC_API_KEY and bills the household. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 3 | Metered cloud models carry no info.extra prices, because the cost map is the invoice; local models carry none. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 4 | Registration is not entitlement; a new CR is reachable only once a key allow-lists it. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 5 | Removing rpmLimit, tpmLimit or maxBudget never clears the live key; going unlimited needs an admin /key/update with explicit nulls. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 6 | Key aliases are global with no adopt-by-alias; an existing alias leaves the CR GenerateFailed until the key is deleted. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 7 | Jev is a native decisions model, not a pass-through; allow-list jev and never grant allowed_passthrough_routes. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 8 | Pass-through routes are admin-only by config, and entry order is load-bearing above each dead-loopback catch-all. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 9 | Never name a hand-written HTTPRoute litellm or set spec.route; the operator deletes that route on a later reconcile. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 10 | Thinking models return empty content; without enable_thinking false the router fails open on 100% of traffic. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 11 | Auto-router fail-open reads complexity_router_default_model, not the inner default_model; keep both on chat-local. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 12 | os.environ/VAR is resolved at startup; removing a provider key a model names downs the whole proxy. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 13 | litellm-dragonfly is required; without it limits and budgets are per worker and overshoot even at replicas 1. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 14 | The AI PR reviewer is advisory and never a required check; rotating its key has a silent second GitHub-secret step. | neither |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 15 | Never call GET /spend/logs without a filter (it OOM-killed the proxy), and never zero historical spend. | data loss |  |  |
| `.agents/skills/litellm-proxy/SKILL.md` §Tripwires 16 | Vector store uses one model and one width, qwen3-embedding-8b at 2000 dims, and PG_VECTOR_API_KEY is never exported. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 1 | Usenet only: the torrent client and torrent indexers were deleted, and re-adding a torrent path is a new decision. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 2 | Recyclarr assign_scores_to matches trash_id, never profile name, since a TRaSH rename orphans name-matched scores. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 3 | Radarr never searches for a monitored movie with no file; size a backlog search against free disk first. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 4 | Hardlinks across CephFS and NFS are impossible; imports copy and need double disk for the copy window. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 5 | Do not measure or delete shared-downloads from the SABnzbd pod; its RBD incomplete mount shadows the CephFS path. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 6 | Plex does not see NFS filesystem events; scans come from Radarr/Sonarr Connect and Plex's periodic update. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 7 | The Plex token is a PushSecret into the plex item; an empty PLEX_TOKEN still reports SecretSynced. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 8 | Tdarr library scope and flow state are not in Git; librariesToNotProcess is a no-op here, and transcodecpuWorkers stays at least 1. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 9 | Books run Shelfmark to Calibre-Web-Automated, Usenet only; Readarr was removed and its snapshots, databases and NAS files kept. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 10 | Git defines only the Cleanuparr pod; the queue-watchdog exits non-zero for queue items older than 24h, and Huntarr is not used. | neither |  |  |
| `.agents/skills/media-stack/SKILL.md` §Tripwires 11 | Friend access is Wizarr plus Seerr, set up in the apps; Git declares only the routes and backups. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires 1 | The DNS target comes from the parent Gateway; a route-level external-dns target annotation does not decide the record. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires 2 | A record with no TXT twin is hand-made, and external-dns will not update or delete it. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires 3 | A NetworkPolicy ports entry matches the destination port, so port 53 never admits a DNS reply. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires 4 | Chart-owned policies get a supplemental NetworkPolicy with the same podSelector; do not disable the chart policy. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires 5 | Envoy controller resources live under deployment.envoyGateway; a resources block under config.envoyGateway is silently ignored. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires 6 | Restart Envoy after a GatewayNamespaceMode outage before calling it healthy; stale proxy tokens storm xDS auth. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires 7 | nslookup needs a trailing dot to skip search suffixes; busybox nslookup ignores search for short names. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires 8 | LoadBalancer IPs are pinned with lbipam.cilium.io/ips from the 10.50.0.0/24 pool. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires 9 | Every browser terminal is behind Authentik SSO; code-server runs --auth none, so the SecurityPolicy is the only gate. | neither |  |  |
| `.agents/skills/networking/SKILL.md` §Tripwires 10 | Host-endpoint Cilium policy belongs to the cilium-host-policy skill; do not edit that skill from a networking change. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Tripwires 1 | A missing requests.memory reserves the whole limits.memory, looking identical to a deliberate Guaranteed pod; audit the Git manifest. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Tripwires 2 | A CPU limit is a CFS quota per 100ms period; throttling never crashes or fails a probe, so do not size requests from coarse rates. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Tripwires 3 | Cutting a request on a requests-equals-limits container demotes it to Burstable, so the Talos OOMController can kill it; a cilium-agent kill drops the node network plane. | outage |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Tripwires 4 | limits: {} does not clear a chart default, since Helm merges the empty map as a no-op. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Tripwires 5 | talos-3's taint is PreferNoSchedule, a score penalty; a hard NoSchedule would strand drains of talos-1 or talos-2. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Tripwires 6 | nodeTaintsPolicy Honor filters NoSchedule and NoExecute only, so it does not keep pods off talos-3. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Tripwires 7 | The descheduler will not evict PreferNoSchedule violators; pods leave talos-3 on their next rollout. | neither |  |  |
| `.agents/skills/node-scheduling/SKILL.md` §Tripwires 8 | Do not place a workload on talos-3, raise a request there or drop a toleration without remeasuring; the committed figures contradict each other. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Tripwires 1 | Syntax gates do not prove a rule can fire; evaluate it against live Prometheus both ways. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Tripwires 2 | A missing series is not a dead rule; sparse gauges exist only while the condition holds, so read the exporter first. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Tripwires 3 | severity info never pages; a latched critical needs a recency bound. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Tripwires 4 | Chart-shipped PrometheusRules are all-or-nothing; an inert rule is documented at the HelmRelease. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Tripwires 5 | commonMetadata.labels overwrites app.kubernetes.io/name on raw manifests, so a ServiceMonitor selecting another name matches zero targets. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Tripwires 6 | Gatus is annotation-only plus hand-written endpoints; gatus.io/enabled ConfigMaps are not loaded. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Tripwires 7 | Grafana persistence is emptyDir, so admin password changes and hand-made tokens die on restart. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Tripwires 8 | A probe only helps if its path can return non-200; prefer the stock KubePodNotReady and OOMKilled rules. | neither |  |  |
| `.agents/skills/observability/SKILL.md` §Tripwires 9 | Do not delete an orphan PrometheusRule or PodMonitor left over from Git; deletion is destructive. | neither |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Tripwires 1 | pods/exec is never cluster-wide: the exec ClusterRole has no ClusterRoleBinding, only one RoleBinding per namespace. | neither |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Tripwires 2 | The overlay must not set targetNamespace, since Flux would collapse the per-namespace RoleBindings onto system. | neither |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Tripwires 3 | A new namespace needs a RoleBinding in lockstep with the CronJob's namespace list. | neither |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Tripwires 4 | UNMEASURED is never a pass; unmounted, subPath-only and errored walks are reported, not counted as readable. | data loss |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Tripwires 5 | Alert on kopiur and report-only for VolSync; kopiur fails closed on the uid mismatch that VolSync's writable staging hides. | data loss |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Tripwires 6 | The four walk.sh traps each return a silent false-clean zero if reintroduced, including busybox find lacking -uid. | data loss |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Tripwires 7 | Identities come from the live ReplicationSource or SnapshotPolicy, never from component defaults. | data loss |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Tripwires 8 | Expected read-only is volumeMounts readOnly true; the writable-check skip annotation must not silence a real failure. | neither |  |  |
| `.agents/skills/pvc-integrity-checks/SKILL.md` §Tripwires 9 | Never shell-interpolate a mount path; the walker receives it as an argv element so a pod cannot inject through mountPath. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires 1 | Suspend renovate.yaml before merging a config change, since its push trigger runs a GHA writer beside the CronJob. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires 2 | Last match wins; every automerge false rule sits after the blanket automerge rules. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires 3 | The never-automerge list covers Renovate's own chart, kopiur images, the Talos and Kubernetes packages, LiteLLM, and CloudNativePG. | outage |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires 4 | A version pin uses a negated regex, never a bare ceiling, since allowedVersions filters candidates and a ceiling leaves nothing to propose. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires 5 | Docker isStable ignores -alpha, -beta and -rc; a merged talosupgrade.yaml bump is an unattended powercycle. | outage |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires 6 | Do not re-add in-repo branch automerge; renovate/** pushes get no status check and stay pending. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires 7 | platformAutomerge is false, since GitHub would merge when Labeler alone is green. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires 8 | The # renovate: value stays on the next line, which both the customManager and the functional-comments guard require. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires 9 | quay.io/ceph/ceph stays on stable x.2.z through its allowedVersions regex, and that regex must not apply to ghcr.io/rook/ceph. | neither |  |  |
| `.agents/skills/renovate/SKILL.md` §Tripwires 10 | A merged bump of tuppr's talosupgrade.yaml upgrades nodes with rebootMode powercycle; treat it as a live upgrade. | outage |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 1 | GitOps only: tunables go in cluster/helmrelease.yaml cephConfig, never ceph config set or ceph auth by hand. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 2 | rgw_sigv4_insecure stays until the image bump that removes it, since HEALTH_OK does not show RGW rejecting S3 PUTs. | data loss |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 3 | security.cephx.csi stays aes, and keyGeneration only increases. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 4 | New RWX claims use ceph-filesystem-rwx (group csi-rwx); the default csi subvolumegroup returns EINVAL on create. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 5 | Judge reclaim with rbd du --exact; plain rbd du counts 4 MiB objects that discards already freed. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 6 | osdMaxUpdatesInParallel lives under spec.storage; never restart all OSDs, and check HEALTH_OK before any node reboot. | outage |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 7 | Do not delete csi-driver-rbac.yaml or csi-driver-tolerations.yaml; they are partial manifests for an out-of-band Helm release. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 8 | OSD memory request stays 12Gi and limit 14Gi; an empty limits block does not clear the operator's 512Mi chart default. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 9 | The rook and nfs mgr modules stay off; the rook module crash-looped and blocked node upgrades. | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 10 | StorageClass parameters are immutable, so a change is a new class; alert counters go through increase(). | neither |  |  |
| `.agents/skills/rook-ceph/SKILL.md` §Tripwires 11 | The metadata CronJob is not last-resort DR; ceph-backup-pvc is OpenEBS hostpath and a host wipe destroys it. | data loss |  |  |
| `.agents/skills/secrets-1password/SKILL.md` §Tripwires 1 | Connect cannot see Home-Lab; no ExternalSecret or PushSecret can read or write it, so machine items go in a Connect-visible vault. | neither |  |  |
| `.agents/skills/secrets-1password/SKILL.md` §Tripwires 2 | A write that must land in one vault uses onepassword-automation, since the shared store picks a vault by priority. | neither |  |  |
| `.agents/skills/secrets-1password/SKILL.md` §Tripwires 3 | SecretSynced and Ready=True do not mean non-empty; ESO writes an empty 1Password field as a zero-length value. | neither |  |  |
| `.agents/skills/secrets-1password/SKILL.md` §Tripwires 4 | Never seed a Secret with client-side kubectl apply; the value is stored in last-applied-configuration. | neither |  |  |
| `.agents/skills/secrets-1password/SKILL.md` §Tripwires 5 | The Connect token and the service-account token are different credentials; do not reuse the ESO Connect token for CI. | neither |  |  |
| `.agents/skills/secrets-1password/SKILL.md` §Tripwires 6 | resources/ is not a secret path; Renovate ignores it and plaintext in git is forbidden. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Tripwires 1 | Do not merge system-controller or system-upgrade into system; k8tz strips TZ injection there and the webhook move is not atomic. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Tripwires 2 | k8tz stays requests equal to limits; its failurePolicy Fail sits on the critical path of pod CREATE. | outage |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Tripwires 3 | imageVolume stays off; upstream still recommends the init container because imageVolume does not mount /etc/localtime. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Tripwires 4 | Admission webhooks mutate CREATE only; a CronJob born before the webhook keeps a wrong spec.timeZone, so delete and recreate it. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Tripwires 5 | CoreDNS must keep autopath out while cache serve_stale is set, or external names break network-wide after about 30s. | outage |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Tripwires 6 | fstrim must not filter out kubelet mounts, since every Ceph PV lives under /var/lib/kubelet. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Tripwires 7 | MGLRU is disabled twice, by machine.sysfs and by mglru-disable; do not delete either until a reboot proves the other holds. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Tripwires 8 | Multus tolerations cannot be set through chart values; the dedicated-taint toleration is a JSON6902 postRenderer. | neither |  |  |
| `.agents/skills/system-namespaces/SKILL.md` §Tripwires 9 | The descheduler does not clear PreferNoSchedule drift; the policy lives in the node-scheduling skill. | neither |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Tripwires 1 | apply-node is not upgrade-node; kernel args and extensions in schematic.yaml.j2 take effect only through upgrade-node. | neither |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Tripwires 2 | The six image pins in machineconfig.yaml.j2 copy the tuppr versions; applying a stale template downgrades a node. | outage |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Tripwires 3 | A merged bump of talosupgrade.yaml is an unattended powercycle roll, gated only on VolSync and CephCluster health, not kopiur. | outage |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Tripwires 4 | Before any node reboot, ceph status must be HEALTH_OK and the OSD device-path check clean, one node at a time. | outage |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Tripwires 5 | Never run just talos shutdown-node for planned power work; it hard-codes shutdown --force, which skips cordon and drain. | outage |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Tripwires 6 | talos-3's B70 dock has its own PSU, which a host powercycle does not cycle; power the dock first. | neither |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Tripwires 7 | domains: [] must be explicit, since a nil list keeps the DHCP search domain and breaks external DNS on nodes. | outage |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Tripwires 8 | features.kubernetesTalosAPIAccess must keep system-upgrade and actions-runner-system; moving tuppr needs apply-node on all three nodes first. | neither |  |  |
| `.agents/skills/talos-nodes/SKILL.md` §Tripwires 9 | Use node names, never the VIP 10.10.10.10; roll planned work talos-3, then talos-2, then talos-1. | neither |  |  |
| `.agents/skills/tdarr-transcoding/SKILL.md` §Tripwires 1 | librariesToNotProcess is not a scope boundary; it is a Tdarr Pro feature that never runs on this unlicensed install. | neither |  |  |
| `.agents/skills/tdarr-transcoding/SKILL.md` §Tripwires 2 | A customFunction node reads inputsDB.code; a node keyed function runs the default stub and reports no error. | neither |  |  |
| `.agents/skills/tdarr-transcoding/SKILL.md` §Tripwires 3 | Never enable forceConform on Set Container; it deletes mov_text and other non-mkv tracks in place. | data loss |  |  |
| `.agents/skills/tdarr-transcoding/SKILL.md` §Tripwires 4 | Never set transcodecpuWorkers to 0; a GPU-only node turns a VA-API failure into a total transcoding outage. | neither |  |  |
| `.agents/skills/tdarr-transcoding/SKILL.md` §Tripwires 5 | Dolby Vision on 4K is an open captain choice; av1_qsv drops the RPU, and the encoder that keeps it is refused by the 4K guard. | neither |  |  |
| `.agents/skills/tdarr-transcoding/SKILL.md` §Tripwires 6 | Never bulk-requeue; Tdarr rewrites sources in place, and the restore input is the after.json flow, never before.json. | data loss |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Tripwires 1 | ${APP}-dst.status.latestImage is frozen at first apply; deleting only the PVC restores nothing while every status stays green. | data loss |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Tripwires 2 | Never patch <app>-dst to trigger a restore; create a uniquely named scratch ReplicationDestination instead. | data loss |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Tripwires 3 | A restore relaxes every mode by one group-write bit, so a VolSync restore cannot reproduce a permissions bug. | neither |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Tripwires 4 | Deleting a ReplicationSource never touches the restic repository, so retirement is survivable and retention cannot empty a frozen repo. | data loss |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Tripwires 5 | paperless-ngx still has ${VOLSYNC_CLAIM:-*app}; removing VOLSYNC_CLAIM makes flate fail the Kustomization. | neither |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Tripwires 6 | Retired-repository expiry is NOT IN FORCE, and a prefix match on syncthing also selects live syncthing-data. | data loss |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Tripwires 7 | A uid mismatch does not mean the restic backup is incomplete; measure with a restore before concluding. | data loss |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Tripwires 8 | One component include is one volume; a second PVC is a second Kustomization with its own APP. | data loss |  |  |
| `.agents/skills/volsync-carveouts/SKILL.md` §Tripwires 9 | VOLSYNC_CACHE_CAPACITY is mover scratch space, not the kopiur restore-cache cliff. | neither |  |  |
| `.agents/skills/ai-stack/references/consumers.md` | How OpenCode and repo-wiki, both internal app-template workloads, reach LiteLLM. | neither |  |  |
| `.agents/skills/ai-stack/references/inventory.md` | Live inventory of what runs in the ai namespace and which skill owns each app. | neither |  |  |
| `.agents/skills/ai-stack/references/retirement.md` | Checklist for retiring or reviving an ai app, including what git revert does not restore. | data loss |  |  |
| `.agents/skills/ai-stack/references/toolhive.md` | ToolHive API group, kinds in use, session embed, RBAC and deactivated servers. | neither |  |  |
| `.agents/skills/app-workloads/references/pod-security.md` | Pod securityContext, fsGroup, linuxserver images and RWO strategy rules. | neither |  |  |
| `.agents/skills/app-workloads/references/probes.md` | Probes that can actually fail, and which stock alerts already cover missing readiness. | neither |  |  |
| `.agents/skills/authentik-terraform/references/apply-runbook.md` | Steps for tofu plan, apply, rollback and destroy against live Authentik SSO. | neither |  |  |
| `.agents/skills/authentik-terraform/references/ci-plan.md` | What the two terraform CI workflows plan and how strong each check is. | neither |  |  |
| `.agents/skills/authentik-terraform/references/conventions.md` | OpenTofu conventions: Flux owns Kubernetes objects, OpenTofu owns state inside application APIs. | neither |  |  |
| `.agents/skills/authentik-terraform/references/inventory.md` | What the Authentik stack owns and what the blueprints own. | neither |  |  |
| `.agents/skills/authentik-terraform/references/state-and-credentials.md` | The terraform-state RGW bucket, its credentials and its backend setup. | neither |  |  |
| `.agents/skills/authentik-terraform/references/upgrade-checklist.md` | Checklist for upgrading the Authentik server chart and image together. | neither |  |  |
| `.agents/skills/b70-llm-serving/references/changelog.md` | One line per B70 serving change that still matters, with current pins. | neither |  |  |
| `.agents/skills/b70-llm-serving/references/contention.md` | How ai/vllm and ai/embedding-gpu share one card and how to throttle embeddings. | neither |  |  |
| `.agents/skills/b70-llm-serving/references/embeddings.md` | Embedding correctness: vector space, NaN handling and the CPU server comparison. | neither |  |  |
| `.agents/skills/b70-llm-serving/references/flags.md` | The llama.cpp flags that are load-bearing for both live controllers. | neither |  |  |
| `.agents/skills/b70-llm-serving/references/memory.md` | Host memory leaks on ai/vllm, their bounds, and the post-merge checks. | neither |  |  |
| `.agents/skills/cilium-host-policy/references/evidence.md` | Why the Ceph host policy is shaped as it is, with the CCNP default-deny evidence. | neither |  |  |
| `.agents/skills/cilium-host-policy/references/gates.md` | The bpf gate to run after a host policy edit, and the rollback order. | outage |  |  |
| `.agents/skills/databases/references/cnpg.md` | CloudNativePG operator and cluster operations, backups and restore labels. | data loss |  |  |
| `.agents/skills/databases/references/dragonfly.md` | Dragonfly cache-only operator and its scrape targets. | neither |  |  |
| `.agents/skills/databases/references/ducklake.md` | DuckLake layout: a Postgres catalog plus Parquet files on S3. | neither |  |  |
| `.agents/skills/databases/references/emqx.md` | EMQX operator pin and the rationale for the 2.2.x line. | neither |  |  |
| `.agents/skills/databases/references/falkordb.md` | FalkorDB image pin, args, memory ceiling, durability and probe. | neither |  |  |
| `.agents/skills/databases/references/surrealdb.md` | SurrealDB single-replica and chart traps. | neither |  |  |
| `.agents/skills/flux-gitops/references/app-shapes.md` | The two app directory tiers and the health-check and wait choices per shape. | neither |  |  |
| `.agents/skills/flux-gitops/references/live-testing.md` | How to live-test a HelmRelease edit safely with a suspended Kustomization. | neither |  |  |
| `.agents/skills/flux-substitution/references/fixes.md` | The three fixes for a literal ${...} token and how to pick between them. | neither |  |  |
| `.agents/skills/github-ci/references/arc-runners.md` | ARC runner namespace, pins, failure modes and cert reissue steps. | neither |  |  |
| `.agents/skills/github-ci/references/branch-protection.md` | The main-branch-protection ruleset, its id, target and required check. | neither |  |  |
| `.agents/skills/github-ci/references/scripts-ci.md` | How each scripts/ci test is a standalone script and how the gates run. | neither |  |  |
| `.agents/skills/github-ci/references/workflows.md` | Inventory of GitHub workflows, what each posts and where it runs. | neither |  |  |
| `.agents/skills/hermes-agent/references/discord.md` | The Hermes Discord bot's single server, channel and allowed user setup. | neither |  |  |
| `.agents/skills/hermes-agent/references/llm-routing.md` | Hermes LLM provider routing, fallbacks and auxiliary models. | neither |  |  |
| `.agents/skills/hermes-agent/references/mcp-gateway.md` | How Hermes hydrates MCP gateway env from config.yaml. | neither |  |  |
| `.agents/skills/hermes-agent/references/restarts.md` | What a Hermes restart does and how to tell a gateway is wedged. | neither |  |  |
| `.agents/skills/hermes-agent/references/shared-storage.md` | The chart-owned hermes PVC and its Samba sharing. | neither |  |  |
| `.agents/skills/hermes-agent/references/state-db.md` | Why state.db dominates the Hermes claim and the retention settings. | neither |  |  |
| `.agents/skills/home-automation/references/home-assistant.md` | Home Assistant's multus IoT network, static IP and probe settings. | neither |  |  |
| `.agents/skills/home-automation/references/zigbee2mqtt.md` | Zigbee2MQTT network identity encodings and a safe change procedure. | neither |  |  |
| `.agents/skills/image-generation/references/prompting.md` | Fill-in prompt recipes for professional images, changing one thing per iteration. | neither |  |  |
| `.agents/skills/intel-gpu/references/baseline.md` | The B70 serving baseline the rest of the stack assumes. | neither |  |  |
| `.agents/skills/intel-gpu/references/devices.md` | Device group definitions and why groups cannot split two xe devices. | neither |  |  |
| `.agents/skills/intel-gpu/references/dra.md` | Why DRA stays unshipped for GPU scheduling. | neither |  |  |
| `.agents/skills/intel-gpu/references/second-card.md` | Why a second B70 purchase is deferred and the conditions that would reopen it. | neither |  |  |
| `.agents/skills/intel-gpu/references/telemetry.md` | What Prometheus does and does not have for GPU telemetry. | neither |  |  |
| `.agents/skills/intel-gpu/references/vaapi-check.md` | The VA-API verification procedure to run after any GPU change. | neither |  |  |
| `.agents/skills/kopiur-backups/references/alerts.md` | The kopiur alert rules and what each one means. | data loss |  |  |
| `.agents/skills/kopiur-backups/references/credentials.md` | Credential projection into kopiur mover jobs, including the three required legs. | data loss |  |  |
| `.agents/skills/kopiur-backups/references/full-cluster-restore-r2.md` | Runbook for rebuilding every volume from r2 after a cluster loss. | data loss |  |  |
| `.agents/skills/kopiur-backups/references/kopiur-scratch-drill.md` | How to prove a claim's ceph and r2 snapshots restore byte-identical in a scratch drill. | data loss |  |  |
| `.agents/skills/kopiur-backups/references/onboarding.md` | How to onboard a claim to kopiur, as born-kopiur or migrated from VolSync. | data loss |  |  |
| `.agents/skills/kopiur-backups/references/proof-ledger.md` | Fleet restore proof table, whose stable row numbers overlays cite. | data loss |  |  |
| `.agents/skills/kopiur-backups/references/repository-maintenance.md` | Repository maintenance and index-blob health, including epoch tuning. | data loss |  |  |
| `.agents/skills/kopiur-backups/references/restore-and-cache.md` | Restore cache, populators and drills, and why the cache is a correctness constraint. | data loss |  |  |
| `.agents/skills/kopiur-backups/references/retirement-and-removal.md` | Record of the VolSync retirements and how a kopiur claim is removed safely. | data loss |  |  |
| `.agents/skills/kopiur-backups/references/schedules-timezone.md` | kopiur cron syntax limits and timezone alignment with VolSync. | data loss |  |  |
| `.agents/skills/kopiur-backups/references/stage0-operator.md` | The kopiur operator, its two repositories and cluster-loss background. | data loss |  |  |
| `.agents/skills/litellm-proxy/references/auto-router.md` | The complexity-tier auto-router alias and its classifier settings. | neither |  |  |
| `.agents/skills/litellm-proxy/references/claude-code-subscription.md` | The four subscription pass-through models, which hold no credential and carry $0 prices. | neither |  |  |
| `.agents/skills/litellm-proxy/references/failover-drill.md` | Post-merge drill that proves a fallback chain against a throwaway dead backend. | neither |  |  |
| `.agents/skills/litellm-proxy/references/fallbacks.md` | Fallback chain config and the alerts that watch it. | neither |  |  |
| `.agents/skills/litellm-proxy/references/governance-keys.md` | Virtual key minting, limits, consumers and rotation. | neither |  |  |
| `.agents/skills/litellm-proxy/references/model-catalog.md` | How to register, route and price a model and publish it to the AI Hub. | neither |  |  |
| `.agents/skills/litellm-proxy/references/passthrough-lockdown.md` | Lockdown of the built-in /openrouter and /anthropic pass-through routes. | neither |  |  |
| `.agents/skills/litellm-proxy/references/platform.md` | Proxy operator delivery, scope, route, SSO and pod posture. | neither |  |  |
| `.agents/skills/litellm-proxy/references/pr-reviewer.md` | The advisory AI PR reviewer workflow and its key rotation. | neither |  |  |
| `.agents/skills/litellm-proxy/references/spend-logs.md` | Spend-log content capture, retention, confidentiality and how to read cost. | neither |  |  |
| `.agents/skills/litellm-proxy/references/vector-store.md` | The LiteLLM pgvector vector store and its embedding model and width. | neither |  |  |
| `.agents/skills/media-stack/references/arr-settings.md` | Download paths and the usenet-only decision across the downloads namespace. | neither |  |  |
| `.agents/skills/media-stack/references/backlog-search.md` | Why Radarr has no missing-movie task and how to size a backlog search. | neither |  |  |
| `.agents/skills/media-stack/references/friends.md` | What Git holds for friend access and what lives in the Wizarr and Seerr apps. | neither |  |  |
| `.agents/skills/media-stack/references/plex.md` | Plex library scanning on NFS and the PushSecret token. | neither |  |  |
| `.agents/skills/media-stack/references/recyclarr.md` | How Recyclarr scores match profiles by trash_id. | neither |  |  |
| `.agents/skills/media-stack/references/sabnzbd-disk-space.md` | Human runbook for a SABnzbd disk-full pause on shared-downloads. | neither |  |  |
| `.agents/skills/media-stack/references/stuck-downloads.md` | The two queue safety nets, Cleanuparr and the queue watchdog, which delete no media. | neither |  |  |
| `.agents/skills/networking/references/bgp-unifi.md` | Cilium BGP CRs and the UniFi FRR snippet, plus pinned load-balancer IPs. | neither |  |  |
| `.agents/skills/networking/references/diagnostics.md` | Network diagnostic task recipes. | neither |  |  |
| `.agents/skills/networking/references/dns-ownership.md` | Split DNS ownership and the external-dns TXT-twin rule. | neither |  |  |
| `.agents/skills/networking/references/envoy-gateway.md` | Envoy Gateway chart pin, controller resources and filter layout. | neither |  |  |
| `.agents/skills/networking/references/plex-ingress-policy.md` | The Plex ingress CiliumNetworkPolicy and what it admits. | neither |  |  |
| `.agents/skills/node-scheduling/references/sizing-method.md` | How to set requests, limits and QoS from measured data. | neither |  |  |
| `.agents/skills/node-scheduling/references/talos-3-ledger.md` | Who runs on talos-3, who tolerates its taint, and placement history. | neither |  |  |
| `.agents/skills/observability/references/alertmanager.md` | Alertmanager routing, receivers and the Watchdog ping. | neither |  |  |
| `.agents/skills/observability/references/gatus.md` | Gatus sources, hand-written endpoints and probe limits. | neither |  |  |
| `.agents/skills/observability/references/grafana.md` | Grafana's emptyDir persistence and the removed operator. | neither |  |  |
| `.agents/skills/observability/references/rule-traps.md` | PrometheusRule traps that make rules dead or unable to fire. | neither |  |  |
| `.agents/skills/observability/references/scrape-targets.md` | ServiceMonitor and PodMonitor selector traps. | neither |  |  |
| `.agents/skills/pvc-integrity-checks/references/mover-readable.md` | The mover-readable check: its CronJob, schedule and unmeasured cases. | data loss |  |  |
| `.agents/skills/pvc-integrity-checks/references/writable.md` | The writable check: its CronJob, pod listing and exec behavior. | data loss |  |  |
| `.agents/skills/renovate/references/operations.md` | In-cluster Renovate CronJob operations, alerts and rollback. | neither |  |  |
| `.agents/skills/renovate/references/versioning-traps.md` | Renovate versioning traps for allowedVersions and version schemes. | neither |  |  |
| `.agents/skills/rook-ceph/references/baseline.md` | Ceph baseline versions, OSD map, mclock caps and mon-DB drift. | neither |  |  |
| `.agents/skills/rook-ceph/references/changelog.md` | Ceph change history and where emergency recovery steps live. | neither |  |  |
| `.agents/skills/rook-ceph/references/decisions.md` | Ceph decisions still in force, each a property of current manifests. | neither |  |  |
| `.agents/skills/rook-ceph/references/diagnostics.md` | Toolbox exec, slow-op and PG diagnostics. | neither |  |  |
| `.agents/skills/rook-ceph/references/osd-device-path-recovery.md` | Recovery for an OSD stuck after restart due to device-path drift. | neither |  |  |
| `.agents/skills/rook-ceph/references/osd-store-corruption-recovery.md` | Recovery for an OSD that crash-loops on BlueStore OMAP or local-store corruption. | data loss |  |  |
| `.agents/skills/rook-ceph/references/rgw.md` | How to check RGW writes, since ceph health does not read the RGW access log. | neither |  |  |
| `.agents/skills/secrets-1password/references/recipes.md` | 1Password item recipes: consumers, vaults and reference schemes. | neither |  |  |
| `.agents/skills/system-namespaces/references/k8tz.md` | k8tz, tuppr and why system-controller and system-upgrade stay split. | neither |  |  |
| `.agents/skills/system-namespaces/references/webhook-drift.md` | CREATE-only admission webhook drift and how to repair affected objects. | neither |  |  |
| `.agents/skills/talos-nodes/references/hardware.md` | Hardware facts still in force for the nodes, such as the dock and PCIe power management. | neither |  |  |
| `.agents/skills/talos-nodes/references/operations.md` | Render, apply, upgrade and roll procedures with the matching commands. | neither |  |  |
| `.agents/skills/talos-nodes/references/power-down-up.md` | Whole-cluster power-down and power-on procedure. | neither |  |  |
| `.agents/skills/talos-nodes/references/talos-3-b70-reboot.md` | Procedure for rebooting talos-3 with its B70 dock powered first. | neither |  |  |
| `.agents/skills/tdarr-transcoding/references/masters.md` | Parked masters and open captain choices that must not be closed while editing. | neither |  |  |
| `.agents/skills/tdarr-transcoding/references/mechanisms.md` | How Tdarr's guards, customFunction nodes and Set Container actually behave. | neither |  |  |
| `.agents/skills/volsync-carveouts/references/corrupt-claim-recreation.md` | How to recreate a corrupt claim from a repository without losing the live volume. | data loss |  |  |
| `.agents/skills/volsync-carveouts/references/multi-volume.md` | One VolSync include per volume, named from APP. | data loss |  |  |
| `.agents/skills/volsync-carveouts/references/restore-procedures.md` | VolSync restore procedures through scratch ReplicationDestinations. | data loss |  |  |
| `.agents/skills/volsync-carveouts/references/retired-expiry-apply-plan.md` | Unexecuted apply plan for expiring retired VolSync repositories. | data loss |  |  |
| `.agents/skills/volsync-carveouts/references/retired-repo-expiry.md` | Decision record for retired VolSync repository expiry, which is not in force. | data loss |  |  |
| `.agents/skills/volsync-carveouts/references/volsync-scratch-drill.md` | Drill that proves the in-cluster VolSync restore path end to end. | data loss |  |  |

## AGENTS.md

| Source | Fact or invariant | Prevents | Verdict | Lands in |
|---|---|---|---|---|
| `AGENTS.md` §TRIPWIRES 1 | NEVER put plaintext secrets in Git; app secrets go through ExternalSecret and 1Password, with no SOPS/age path. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 2 | Home-Lab and Homelab are different vaults; Connect cannot see Home-Lab, and SecretSynced does not mean a non-empty value. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 3 | NEVER run an unbounded home search such as find $HOME; a missing kubeconfig in a fresh worktree is normal. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 4 | kubectl and talosctl use the read-only agent credentials; a Forbidden on write is by design. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 5 | NEVER run tofu apply or tofu destroy in terraform/authentik/ without an explicit, current go-ahead; a green PR is not approval. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 6 | NEVER commit without the pre-commit hooks installed by task setup-dev-env. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 7 | NEVER run just bootstrap cluster or apps against a healthy cluster; bootstrap is for DR and first-time setup only. | outage |  |  |
| `AGENTS.md` §TRIPWIRES 8 | A literal ${...} that is not a Flux variable fails the whole Kustomization, and neither flate nor task flux:test:all catches it. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 9 | flate -n filters the exit code; task flux:test:all is the gate. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 10 | healthChecks target the workload, not the HelmRelease; a Kustomization owning a kopiur or VolSync claim keeps wait false. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 11 | Never add a helm postRenderer: bash, which breaks on Helm 4. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 12 | Retiring an app or changing a pinned value can break a scripts/ci gate that flate does not run; grep scripts/ci/ first. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 13 | Merging .renovaterc.json5 or .renovate/** fires the renovate workflow, and a Renovate bump of talosupgrade.yaml is an unattended node upgrade. | outage |  |  |
| `AGENTS.md` §TRIPWIRES 14 | talos/*.j2 changes are not applied by Flux, and a stale machineconfig template can downgrade a node. | outage |  |  |
| `AGENTS.md` §TRIPWIRES 15 | Before any node reboot, ceph status must be HEALTH_OK; never run just talos shutdown-node for planned power work. | outage |  |  |
| `AGENTS.md` §TRIPWIRES 16 | CephX rotation is GitOps-only; never run ceph auth by hand, and keyGeneration only increases. | outage |  |  |
| `AGENTS.md` §TRIPWIRES 17 | The ceph-backup-pvc metadata backup is OpenEBS hostpath, so a host wipe destroys it. | data loss |  |  |
| `AGENTS.md` §TRIPWIRES 18 | talos-3 carries a PreferNoSchedule taint, and a container with limits.memory and no requests.memory reserves the whole limit. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 19 | Do not merge system-controller or system-upgrade into system. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 20 | Deleting a kopiur Snapshot CR deletes the kopia snapshot; the PVC component is ssa IfNotPresent and never force enabled. | data loss |  |  |
| `AGENTS.md` §TRIPWIRES 21 | ${APP}-dst.status.latestImage is frozen at first deploy; recreating a claim from dataSourceRef can restore nothing while everything stays green. | data loss |  |  |
| `AGENTS.md` §TRIPWIRES 22 | Retired VolSync repository expiry is NOT IN FORCE; a prefix match on syncthing also selects live syncthing-data. | data loss |  |  |
| `AGENTS.md` §TRIPWIRES 23 | Both engines snapshot crash-consistently, so a database backup is only as durable as its fsync setting. | data loss |  |  |
| `AGENTS.md` §TRIPWIRES 24 | NEVER add a CiliumClusterwideNetworkPolicy with a nodeSelector and ingress unless enableDefaultDeny ingress is false; the selector label must survive onto the host endpoint. | outage |  |  |
| `AGENTS.md` §TRIPWIRES 25 | A NetworkPolicy ports entry matches the destination port, so port 53 never admits a DNS reply. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 26 | NEVER rename a DRM device node with generic-device-plugin mountPath, and never ship a GPU change without the VA-API check. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 27 | The B70 has two tenants: throttle embeddings by request rate, pin --cache-ram on both llama.cpp workloads, and expect a green embedding endpoint to return NaN. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 28 | LiteLLM: a config fallback bypasses every key allow-list, and a LiteLLMModel with no apiKey bills the household-metered ANTHROPIC_API_KEY. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 29 | NEVER set sessions.vacuum_after_prune true on ai/hermes; any Hermes config byte restarts it uncleanly. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 30 | kagent and kmcp are not deployed; ToolHive MCPServer is toolhive.stacklok.dev/v1alpha1. | neither |  |  |
| `AGENTS.md` §TRIPWIRES 31 | An app can be Ready while it cannot write its PVC, and fsGroup re-owns volume content permanently. | data loss |  |  |
