# PROJECT KNOWLEDGE BASE

Home-ops GitOps repo for a 3-node Talos Linux Kubernetes cluster managed by Flux v2. `CLAUDE.md` is a symlink to this file. Subsystem knowledge lives in skills (SKILL INDEX below); operator detail also in [`talos/AGENTS.md`](talos/AGENTS.md), [`bootstrap/AGENTS.md`](bootstrap/AGENTS.md) and the runbooks listed in [`docs/README.md`](docs/README.md).

**Core stack**: Talos Linux + Flux v2 + Cilium (CNI, BGP LoadBalancer, kube-proxy replacement, no L2 announcements) + Rook-Ceph + External Secrets Operator/1Password + Cloudflare Tunnel + External-DNS (split: `network/cloudflare-dns` public, `network/unifi-dns` internal) + kopiur (primary backup; VolSync only on 3 carve-outs) + Gateway API (`envoy-internal`/`envoy-external` in `network`) + kube-prometheus-stack/Loki/Tempo/Grafana/Gatus in `monitoring`.

## STRUCTURE

```
.
├── kubernetes/
│   ├── apps/base/<ns>/<app>/   # manifests (19 namespaces)
│   ├── apps/main/<ns>/         # overlay: one Flux Kustomization CR per app + kustomization.yaml
│   ├── clusters/main/          # Flux entry point: meta.yaml + apps.yaml (cluster-meta -> cluster-apps)
│   └── components/             # alerts, common, dragonfly, kopiur (+ kopiur/pvc), volsync (3 claims left)
├── talos/                      # minijinja templates, rendered by `just talos`, not Flux
├── bootstrap/                  # just bootstrap stages
├── .agents/skills/             # one skill per subsystem (.claude/skills is a symlink)
├── .taskfiles/                 # task domains: 1password, k8s, flux, rook, network, actions-runner
├── terraform/                  # OpenTofu stacks outside Flux (Authentik)
├── docs/                       # human runbooks only (docs/README.md)
└── .renovate/                  # Renovate presets
```

## WHERE TO LOOK

| Task | Location |
|------|----------|
| Add app | `kubernetes/apps/base/{ns}/{app}/` + overlay `kubernetes/apps/main/{ns}/{app}.yaml`, then `- ./{app}.yaml` in the overlay `kustomization.yaml` (skill `flux-gitops`) |
| Enable backups | overlay Kustomization, kopiur only; never add `components/volsync` to a new app (skill `kopiur-backups`) |
| Namespace-wide labels | `kubernetes/components/common/namespace.yaml` (one `Namespace` named `not-used`, renamed per overlay) |
| App secrets | `{app}/app/externalsecret.yaml`, ClusterSecretStore `onepassword` (skill `secrets-1password`) |
| Bootstrap secrets | `bootstrap/kustomize/apps/security/`, `vals` resolves `ref+op://Home-Lab/1password/*` |
| Helm/OCI repos | `kubernetes/apps/base/flux-system/meta/repos/` |
| Talos config | `talos/machineconfig.yaml.j2`, `talos/nodes/*.yaml.j2`, `talos/schematic.yaml.j2` (skill `talos-nodes`) |
| Tasks | `Taskfile.yaml` + `.taskfiles/` (`task --list-all`), `.justfile` + `*/mod.just` |
| CI, branch protection | `.github/workflows/`; ruleset on `main` is applied via `gh api`, not in Git (skill `github-ci`) |
| Renovate | `.renovaterc.json5` + `.renovate/`; in-cluster CronJob is the writer (skill `renovate`) |
| Authentik SSO | `terraform/authentik/` (skill `authentik-terraform`) |
| AI stack | `kubernetes/apps/{main,base}/ai/` (skills `ai-stack`, `litellm-proxy`, `hermes-agent`, `agentgateway`) |
| Gatus | `monitoring/gatus`; endpoints come from the `gatus.home-operations.com/endpoint` HTTPRoute annotation (skill `observability`) |
| konflate | `flux-system/konflate`, read-only PR-review UI, internal route only; its README owns the write-back-off invariant |
| Tool versions | `.mise.toml`; resolve CLIs with `mise which <cli>` / `mise exec -- <cli>` |

## CONVENTIONS

- Every manifest starts with a `# yaml-language-server: $schema=...` comment. Prefer `kubernetes-schemas.pages.dev`, fall back to `k8s-schemas.home-operations.com`, never `crd.movishell.pl` or `fluxcd-community`.
- Overlay Kustomization anchors: `name: &app myapp`, `namespace: &namespace myns`, referenced as `*app`, `*namespace`. Many overlays `dependsOn` `onepassword-store` in `security`.
- Lowercase kebab-case names: `helmrelease.yaml`, `kustomization.yaml`, `externalsecret.yaml`, overlay `<app>.yaml`.
- Commits: `type(scope): description`, types feat/fix/chore/ci/docs/refactor/test, scope is a container, helm, github-action, mise, talos, flux, deps or an app/namespace (`.commitlintrc.yaml`).
- HTTPRoutes carry `gethomepage.dev/*` and `gatus.home-operations.com/endpoint` annotations. The parent Gateway's `external-dns.alpha.kubernetes.io/target` decides the DNS record, not a route annotation (skill `networking`).
- HelmRelease defaults (CRD CreateReplace, rollback recreate, upgrade remediation) are patched in by `cluster-apps`.
- Flux substitution: `postBuild.substituteFrom` `cluster-secrets` plus an inline `substitute` map.
- Task runner split is deliberate: `just` owns Talos, bootstrap and `kube` recipes; `task` owns the Rook suite and network diagnostics. Do not port one to the other.
- Storage classes: `ceph-block` (RWO), `ceph-filesystem-rwx` (RWX), `openebs-hostpath` (local); leave new claims off `ceph-filesystem` (skill `rook-ceph`).
- Nodes `talos-1|2|3` are `10.10.10.11/12/13`; never target the VIP `10.10.10.10`. Bonded LACP, MTU 9000, VLANs 3 and 90.

## TRIPWIRES

Each is a warning needed before deciding to touch the subsystem; detail is in the named skill.

**Secrets and access**
- **NEVER put plaintext secrets in Git.** App secrets are ExternalSecret + 1Password; bootstrap/Talos secrets are `ref+op://Home-Lab/...` via `vals`. Never seed a Secret with client-side `kubectl apply`. There is no SOPS/age path. Do not store secrets in `**/resources/**` (Renovate ignores it).
- **`Home-Lab` and `Homelab` are different vaults; Connect cannot see `Home-Lab`, and `SecretSynced` does not mean a non-empty value.** Skill `secrets-1password`.
- **NEVER run `find /Users/coder`, `find $HOME` or any unbounded home search.** Tools are mise-managed; `kubeconfig` and `talosconfig` are absent in fresh worktrees and that is normal - report and continue.
- **A `kubectl` on the mise shims silently uses `.mise.toml`'s KUBECONFIG (localhost).** Pass `--kubeconfig`; empty output is the symptom.
- **NEVER run `tofu apply` or `tofu destroy` in `terraform/authentik/` without an explicit, current go-ahead.** A green PR is not approval. Skill `authentik-terraform`.
- **NEVER commit without the pre-commit hooks** (`task setup-dev-env`).

**Flux and CI**
- **NEVER run `just bootstrap cluster` / `apps` against a healthy cluster** (DR and first-time only; `bootstrap/AGENTS.md`).
- **A literal `${...}` that is not a Flux variable fails the whole Kustomization, and neither `flate` nor `task flux:test:all` catches it.** Skill `flux-substitution`.
- **`flate -n` filters the exit code; `task flux:test:all` is the gate.** Skill `flux-gitops`.
- **`healthChecks` target the workload, never the HelmRelease, and `wait` stays false.** Suspend a Kustomization before a live HelmRelease edit. `flux-system` is exempt from `cluster-apps`, so a git-only change to it never lands. Skill `flux-gitops`.
- **Never add a helm `postRenderer: bash`** (breaks on Helm 4).
- **Retiring an app or changing a pinned value can break a `scripts/ci` gate `flate` does not run.** Grep `scripts/ci/` first. `validate.yaml` is the only CI signal for `talos/`, `bootstrap/`, `.renovate/` and `terraform/`. Skill `github-ci`.
- **Merging `.renovaterc.json5` or `.renovate/**` fires the renovate workflow.** Suspend it first. A Renovate bump of `talosupgrade.yaml` is an unattended node upgrade. Skill `renovate`.

**Nodes and storage**
- **`talos/*.j2` changes are not applied by Flux.** `apply-node` restages only; kernel args and extensions need `just talos upgrade-node`. `machineconfig.yaml.j2`'s 6 version pins can drift from the live cluster (tuppr drives upgrades), so applying a stale template can downgrade a node: check `kubectl get nodes -o wide` first. Skill `talos-nodes`.
- **Before any node reboot: `ceph status` HEALTH_OK and `task rook:check-osd-device-paths` clean, one node at a time.** Never `just talos shutdown-node` for planned power work (`--force`, no drain); runbook skill `talos-nodes` `references/power-down-up.md`. A talos-3 reboot can drop the B70: power the dock first (`references/talos-3-b70-reboot.md`). Skills `rook-ceph`, `talos-nodes`.
- **CephX rotation is GitOps-only: never `ceph auth` by hand, never `security.cephx.csi: aes256k`, `keyGeneration` only increases.** Leave `rgw_sigv4_insecure: "true"` in place until the image bump that removes it (S3 PUTs fail with `HEALTH_OK`). Skill `rook-ceph`.
- **The Ceph metadata backup (`ceph-backup-pvc`) is OpenEBS hostpath; a host wipe destroys it.** Emergency steps: `kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md`.
- **talos-3 carries a `PreferNoSchedule` taint; a hard `NoSchedule` strands drains.** A container with `limits.memory` and no `requests.memory` reserves the whole limit, so audit the Git manifest. Skill `node-scheduling`.
- **Do not merge `system-controller` or `system-upgrade` into `system`.** Skill `system-namespaces`.

**Backups (silent data loss)**
- **Deleting a kopiur Snapshot CR deletes the kopia snapshot.** The PVC component is `ssa: IfNotPresent` and never sets `force: enabled`. Measure mover identity from the files or the backup fails closed. One component include covers one volume. Skill `kopiur-backups`.
- **`${APP}-dst.status.latestImage` is frozen at first deploy: recreating a claim from `dataSourceRef` can restore nothing while everything stays green.** Delete the ReplicationDestination with the PVC. Never patch `<app>-dst` to trigger a restore. Do not add `components/volsync` to a new app. Skill `volsync-carveouts`.
- **Retired VolSync repository expiry is NOT IN FORCE; a prefix match on `syncthing` also selects live `syncthing-data`.** Skill `volsync-carveouts`.
- **Both engines snapshot crash-consistently, so a database backup is only as durable as its fsync setting.** CNPG takes one barman destination per Cluster. Skill `databases`.

**Network**
- **NEVER add a `CiliumClusterwideNetworkPolicy` with a `nodeSelector` and ingress unless `enableDefaultDeny.ingress` is false, and the selector label must survive onto the host endpoint** (`kubernetes.io/os`, `kubernetes.io/hostname` are stripped). Skill `cilium-host-policy`.
- **A NetworkPolicy `ports:` entry matches the destination port, so `port: 53` never admits a DNS reply.** Skill `networking`.

**AI and GPU**
- **NEVER rename a DRM device node with `generic-device-plugin`'s `mountPath`, and never ship a GPU change without the VA-API check.** VA-API consumers use `devic.es/b70-vaapi`. No DRA migration, never `adminAccess: true`. Skill `intel-gpu`.
- **The B70 has two tenants: throttle the embedding endpoint by request rate. Both llama.cpp workloads pin `--cache-ram` (chat non-zero, embeddings `0`) and `vllm` sets `GGML_SYCL_FA_ONEDNN: "0"` or host RAM leaks with no OOM. An embedding endpoint can return pure NaN with green health.** Skill `b70-llm-serving`.
- **LiteLLM: a config fallback bypasses every key allow-list, and a `LiteLLMModel` with no `apiKey` bills the household-metered `ANTHROPIC_API_KEY`.** Skill `litellm-proxy`.
- **NEVER set `sessions.vacuum_after_prune: true` on `ai/hermes`.** Any Hermes config byte restarts it uncleanly. Skill `hermes-agent`.
- kagent/kmcp are not deployed; ToolHive `MCPServer` is `toolhive.stacklok.dev/v1alpha1`. Skill `ai-stack`.

**Workloads**
- **An app can be Ready while it cannot write its PVC (a discarded pod-options key), and `fsGroup` re-owns volume content permanently.** Skill `app-workloads`.

## COMMANDS

```bash
task setup-dev-env                 # Install tools + pre-commit hooks
task reconcile                     # Force Flux sync from Git
task flux:test:all                 # Validate Flux manifests with flate
task rook:check-disks              # Check Ceph disk status
just talos render-config talos-1   # Render a node's machine config
just talos apply-node talos-1      # Apply config (node names, not IPs)
for t in scripts/ci/*-test.py; do python3 "$t"; done   # CI gates, including docs-budget
```

Debugging: `flux get ks -A`, `flux get hr -A`, `kubectl -n {ns} get pods -o wide`, `kubectl get replicationsource,replicationdestination -A`. `kubeconfig` and `talos/talosconfig` are gitignored; `.private/` is local-only.

## SKILL INDEX

Skills live in `.agents/skills/<name>/SKILL.md` (`.claude/skills` is a symlink). Harnesses that do not auto-load them: read the matching `SKILL.md` before touching its subsystem.

| Skill | Load when touching |
|---|---|
| `flux-gitops` | apps, overlays, Flux healthChecks/wait/dependsOn, live HelmRelease tests |
| `flux-substitution` | literal `${...}` in Flux-reconciled files, envsubst failures |
| `app-workloads` | pod securityContext, fsGroup, probes, PVC mounts, linuxserver images |
| `node-scheduling` | requests/limits, tolerations, affinity, talos-3 placement, throttling/OOM |
| `secrets-1password` | ExternalSecret, PushSecret, 1Password items, `ref+op://` |
| `github-ci` | workflows, `scripts/ci` gates, ARC runners, branch protection |
| `renovate` | Renovate config, `# renovate:` annotations, risky Renovate merges |
| `talos-nodes` | `talos/*.j2`, `just talos`, tuppr upgrades, reboots, power work |
| `rook-ceph` | Rook/Ceph config, CephX, RGW, OSD health, storage classes |
| `system-namespaces` | k8tz, tuppr namespace, kube-system add-ons, fstrim |
| `cilium-host-policy` | CiliumClusterwideNetworkPolicy, host firewall, nodeSelector policies |
| `networking` | HTTPRoute, Gateway, SecurityPolicy, external-dns, BGP, NetworkPolicy |
| `authentik-terraform` | `terraform/**`, Authentik SSO apps/providers, ExtAuth routes |
| `kopiur-backups` | any kopiur component, CR, restore, alert or backup failure |
| `volsync-carveouts` | the 3 VolSync carve-out claims, RS/RD, restores, retired repos |
| `pvc-integrity-checks` | pvc-writable-check, pvc-mover-readable-check, their RBAC and alerts |
| `litellm-proxy` | LiteLLM proxy/operator CRs, models, keys, fallbacks, PR reviewer |
| `hermes-agent` | Hermes config, restarts, state.db, LLM/MCP routing, samba |
| `agentgateway` | agentgateway routes, listeners, backends, API keys, cost table |
| `ai-stack` | ai namespace apps, ToolHive, opencode/repo-wiki, AI app retirement |
| `b70-llm-serving` | vllm and embedding-gpu flags, memory, alerts, embedding checks |
| `intel-gpu` | GPU device plugins, `devic.es/b70*`, VA-API, GPU telemetry |
| `observability` | PrometheusRule, Service/PodMonitor, Alertmanager, Gatus, Grafana, probes |
| `media-stack` | *arr apps, SABnzbd, Recyclarr, Bazarr, Plex, backlog searches |
| `tdarr-transcoding` | Tdarr libraries, flows, customFunction nodes, errored remuxes |
| `databases` | FalkorDB, SurrealDB, CloudNativePG, EMQX, Dragonfly |
| `home-automation` | Zigbee2MQTT, Home Assistant, matter-server, esphome |

## Maintaining this file

This file is loaded into every session, so it holds only structure, conventions, commands and tripwires (silent data loss, a cluster-wide outage, an unrecoverable state) plus the SKILL INDEX. A new finding goes in the subsystem skill or its `references/`; it earns a line here only when it fits one sentence plus a pointer AND an agent who has not yet decided to touch that subsystem needs it. Write a finding once where it is owned; a summary here is a second copy that drifts. Do not recreate `CLAUDE.md` as a separate file.

`scripts/ci/docs-budget-test.py` enforces absolute budgets (this file, skill and reference sizes, YAML comment blocks). Raising one needs an allowlist entry with a reason in `scripts/ci/docs-budget.json`.
