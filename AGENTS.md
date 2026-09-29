# PROJECT KNOWLEDGE BASE

Home-ops GitOps repo for a 3-node Talos Linux Kubernetes cluster managed by Flux v2. `CLAUDE.md` is a symlink to this file. For narrower operator detail see [`talos/AGENTS.md`](talos/AGENTS.md) and [`bootstrap/AGENTS.md`](bootstrap/AGENTS.md).

**Core stack**: Talos Linux (immutable OS) + Flux v2 (GitOps) + Cilium (CNI, BGP LoadBalancer, kube-proxy replacement, no L2 announcements) + Rook-Ceph (storage) + External Secrets Operator/1Password (secrets) + Cloudflare Tunnel (external access) + External-DNS (split DNS: `network/cloudflare-dns` public, `network/unifi-dns` internal) + kopiur (primary backup; VolSync remains on 3 dual-engine carve-outs only). Gateway API `HTTPRoute`s front `envoy-internal`/`envoy-external` gateways in `network`. Monitoring: kube-prometheus-stack + Loki + Promtail + Tempo + Grafana + Alertmanager + Gatus + kromgo + KEDA + unpoller, all in `monitoring`.

## STRUCTURE

```
.
├── kubernetes/
│   ├── apps/           # 19 namespaces at apps/base/<ns> + overlay apps/main/<ns> (see NOTES)
│   ├── clusters/main/  # Flux entry point: meta.yaml + apps.yaml (see NOTES)
│   └── components/     # alerts, common, dragonfly, kopiur (+ kopiur/pvc), volsync (3 claims left)
├── talos/              # minijinja templates (see talos/AGENTS.md)
├── bootstrap/          # just bootstrap stages (see bootstrap/AGENTS.md)
├── .agents/skills/     # agent skills, one per subsystem (.claude/skills is a symlink) - see SKILL INDEX
├── .taskfiles/         # included: 1password, k8s, flux, rook, network, actions-runner
├── terraform/          # OpenTofu stacks for config outside Flux's reach (see NOTES)
├── docs/               # runbooks, incident history, ceph/network notes
└── .renovate/          # Renovate presets
```

Gatus is an app under `kubernetes/apps/base/monitoring/gatus`, not a component. Endpoints come from the `gatus.home-operations.com/endpoint` HTTPRoute annotation (auto-discovered) or hand-written entries in `app/resources/config.yaml` for non-HTTPRoute checks. The `gatus.io/enabled` ConfigMap pattern is dead: skill `observability`.

## WHERE TO LOOK

| Task | Location | Notes |
|------|----------|-------|
| Add new app | `kubernetes/apps/base/{namespace}/{app}/` + overlay `kubernetes/apps/main/{namespace}/{app}.yaml` | Overlay is a Flux `Kustomization` CR (one yaml per former `ks.yaml`). All 19 namespaces are on this layout. See NOTES. |
| Add app to namespace | `kubernetes/apps/main/{namespace}/kustomization.yaml` | Add `- ./{app}.yaml` |
| Enable backups | overlay `kubernetes/apps/main/{ns}/{app}.yaml` | **kopiur-only is the norm**: new never-VolSync apps get `components/kopiur` only + a chart-owned PVC (example: `database/falkordb.yaml`); `components/kopiur/pvc` is the VolSync-retirement takeover shape only (example: `downloads/sonarr.yaml`). Dual-engine survives on 3 deliberate carve-outs only (`selfhosted/paperless-ngx{,-media}`, `syncthing-data`); do not add `components/volsync` to a new app - owner: `kubernetes/components/kopiur/Readme.md` |
| Namespace-wide labels/annotations | `kubernetes/components/common/namespace.yaml` | One shared `Namespace` (`name: not-used`) renamed by each overlay. skill `flux-gitops`. |
| App secrets | `kubernetes/apps/base/{ns}/{app}/app/externalsecret.yaml` | OnePassword via ClusterSecretStore `onepassword` |
| Bootstrap secrets | `bootstrap/kustomize/apps/security/` | `vals` injects `ref+op://Home-Lab/1password/*` |
| Flux entry point | `kubernetes/clusters/main/{meta,apps}.yaml` | `cluster-meta` -> `cluster-apps` dependency chain |
| Helm/OCI repos | `kubernetes/apps/base/flux-system/meta/repos/` | 2 repo yaml files plus `kustomization.yaml` |
| Talos node config | `talos/machineconfig.yaml.j2` + `talos/nodes/*.yaml.j2` + `talos/schematic.yaml.j2` | Rendered by `just talos`, not Flux |
| Task commands | `Taskfile.yaml` + `.taskfiles/{domain}/` | `task --list-all`. Split with `just`: see UNIQUE STYLES |
| CI workflows | `.github/workflows/` | flate, renovate, codeql, image-pull, label-sync, validate, terraform-diff, terraform-publish, ai-pr-review, plus build-talosctl-busybox, labeler, tag, test-runner |
| Branch protection | GitHub ruleset on `main`, applied via `gh api` (not in Git) | Only `Labeler - Labeler` is required. The aggregate checks post on every PR and are not in the ruleset. skill `github-ci`. |
| Renovate config | `.renovaterc.json5` + `.renovate/` | In-cluster CronJob is the writer. Suspend the GitHub Actions workflow before merging a config change. skill `renovate`. |
| Subsystem deep knowledge | `.agents/skills/<name>/SKILL.md` | One skill per subsystem, listed in SKILL INDEX below. Each `description` line is the load trigger - load the matching skill before touching that subsystem. The always-loaded entries below keep only the tripwire plus a pointer. |
| Tool versions | `.mise.toml` | kubectl, flux, talos, helm, kustomize, vals, 1password-cli, just, minijinja, etc. Resolve with `mise which <cli>` / `mise exec` (see NOTES). |
| Authentik SSO config | `terraform/authentik/` (OpenTofu) | Never `tofu apply` or `tofu destroy` without an explicit current go-ahead. skill `authentik-terraform`. |
| AI stack | `kubernetes/apps/main/ai/` (Flux Kustomizations) + `kubernetes/apps/base/ai/` (manifests) | Hermes + ToolHive (`toolhive.stacklok.dev/v1alpha1` `MCPServer`) + agentgateway + LiteLLM (governance layer, internal route only; skill `litellm-proxy`). kagent/kmcp tombstones: `docs/ai-system/{kagent,kmcp}`. Retired 2026-08-22: `docs/ai-system/retired-2026-08-22.md` |
| konflate (PR review UI) | `kubernetes/apps/base/flux-system/konflate/` | Read-only Flux PR-review UI, internal HTTPRoute only. Write-back is off and no GitHub credential is in-cluster (public-repo anonymous reads). Do not copy the reference repo's write-back / shared GitHub App wiring. |
| PVC write-access check | `kubernetes/apps/base/system/pvc-writable-check/` | CronJob (every 6h) execs `test -w <mountPath>` in every PVC-mounting container and alerts via PrometheusRule on genuine failure - the check that would have caught the autobrr/rsshub-playwright empty-volume bugs (2026-08-30). Cluster-wide pods get/list plus per-namespace `pods/exec: create` RoleBindings that deliberately omit rook-ceph, database, and security (API-server denial, not script-only); design rationale and that RBAC tradeoff are in the app's README. Prefer `volumeMounts[].readOnly: true` for expected read-only (headlamp); the pod-wide skip annotation is an unused escape hatch. |
| PVC mover read-access check | `kubernetes/apps/base/system/pvc-mover-readable-check/` | Sibling of the row above: CronJob (every 6h, :47 so the two exec sweeps never overlap) that resolves each engine's mover uid/gid from the **live** `ReplicationSource`/`SnapshotPolicy` - never component defaults, which are per-claim and span 0..10000 - then walks every backup-covered claim from a container that already mounts it and counts entries that identity could not read (directories also need execute). **Alerts on kopiur, report-only for VolSync**: VolSync stages its clone writable so kubelet's `fsGroup` walk rescues it before restic reads, kopiur stages read-only and fails closed - so the same mismatch is invisible to one engine and fatal to the other. Unmounted claims, subPath-only mounts (`ntfy`), RBAC-excluded namespaces and any walk that hit an error are reported UNMEASURED/INCONCLUSIVE, never as passing. `pods/exec` is bound in only the 5 namespaces holding a covered claim, so `database/pgadmin` is a known permanent gap. Four silent-false-clean traps and the measured evidence: the app's `README.md`. |

## CONVENTIONS

- **YAML schemas**: Every manifest starts with `# yaml-language-server: $schema=...` comment
- **Kustomization anchors**: `name: &app myapp`, `namespace: &namespace myns` - referenced via `*app`, `*namespace` on overlay `kubernetes/apps/main/<ns>/<app>.yaml`
- **Schema URL**: Prefer `kubernetes-schemas.pages.dev` - never `crd.movishell.pl` or `fluxcd-community`. When that host does not serve the kind as JSON, use the existing fallback `k8s-schemas.home-operations.com` (do not invent a third host or point at a URL that 404s).
- **Naming**: All lowercase, kebab-case dirs/files. `helmrelease.yaml`, `kustomization.yaml`, overlay `<app>.yaml`, `externalsecret.yaml`
- **Commit format**: `type(scope): description` - types: feat, fix, chore, ci, docs, refactor, test. Authoritative rules: `.commitlintrc.yaml` (commit-msg hook in `.pre-commit-config.yaml`)
- **Commit scopes**: container, helm, github-action, mise, talos, flux, deps, github-release, or app/namespace names
- **Gateway API**: `envoy-internal` (private) and `envoy-external` (public) in `network` namespace
- **DNS target**: For `gateway-httproute`, the parent Gateway's `external-dns.alpha.kubernetes.io/target` decides the record. A route-level annotation does not. skill `networking`.
- **Homepage annotations**: `gethomepage.dev/*` annotations on HTTPRoutes for dashboard integration
- **Gatus monitoring**: `gatus.home-operations.com/endpoint` annotation with conditions on HTTPRoutes
- **HelmRelease defaults**: Auto-patched by cluster-apps Kustomization - CRD CreateReplace, rollback recreate, upgrade remediation

## ANTI-PATTERNS (THIS PROJECT)

- **NEVER** put plaintext secrets in Git. App secrets are ExternalSecret + 1Password only. Bootstrap/Talos secrets are `ref+op://Home-Lab/...` resolved by `vals`.
- **NEVER** add a helm `postRenderer: bash` (breaks on Helm 4)
- **NEVER** run `just bootstrap cluster` / `apps` against a healthy cluster. See `bootstrap/AGENTS.md`
- **NEVER** commit without pre-commit file/security hooks - `task setup-dev-env` to install them
- **DO NOT** store secrets in `**/resources/**` - Renovate ignores this path
- There is no SOPS/age path and no `talos/clusterconfig/` or `talos/talconfig.yaml`. Do not reintroduce them.
- **NEVER rename a DRM device node with `generic-device-plugin`'s `mountPath`, and never ship a GPU change without running the VA-API check.** The `b70` group's remap to `card0`/`renderD128` is fatal to VA-API (libdrm reopens the canonical `DEVNAME` from sysfs, which the container does not have), while Level Zero is unaffected - so **the AI stack stays green while transcoding is completely dead**, an asymmetry that hid a total 3-day Tdarr outage. VA-API consumers must use `devic.es/b70-vaapi`, and allocatable capacity is not proof that transcoding works. Mechanism, the two related device-ID and `subPath` traps, and the verification commands: skill `intel-gpu`, `docs/media-stack.md` "Verifying VA-API after a GPU change", `docs/ai-gpu-changelog.md` (2026-08-29).
- **The B70 has two tenants and no compute partition: driving the embedding endpoint hard throttles the captain's live chat model** (measured 0.5-4 req/s costs chat 30-84%) - curve, VRAM arithmetic and throttling rule: skill `intel-gpu`.
- **Both B70 llama.cpp workloads must pin `--cache-ram` non-default (and `vllm` also needs `GGML_SYCL_FA_ONEDNN: "0"`) or HOST-RAM leaks past any `limits.memory`, silently and uncatchable by the kernel's OOM path** - mechanism, per-workload values and the two CI gates: skill `intel-gpu`.
- **An embedding endpoint can return vectors of pure NaN while every health signal is green - never verify by response shape, length or non-zero count, only by finiteness/non-zero value** (2026-09-16 incident, 202,582 tasks silently corrupted) - detection, recovery and the mutation-proven CI gate: skill `intel-gpu`.
- **kagent / kmcp are not deployed** (removed 2026-06-07, #941/#942). Live AI stack is Hermes + ToolHive + agentgateway. `docs/ai-system/{kagent,kmcp}` are tombstones. ToolHive `MCPServer` is `toolhive.stacklok.dev/v1alpha1` - never kagent.dev's same kind name.
- **A `CiliumClusterwideNetworkPolicy` with a `nodeSelector` and an `ingress:` or `ingressDeny:` section must set `enableDefaultDeny: {ingress: false}`.** Otherwise the host endpoint drops Talos API, Kubernetes API, kubelet, etcd, Cilium health, and BGP, and one such policy re-arms default-deny for every host policy. skill `cilium-host-policy`.
- **A CCNP `nodeSelector` must match a label on the host endpoint.** Cilium strips `kubernetes.io/os` and `kubernetes.io/hostname`, so an unmatched selector stays `VALID: True` with `policy-enabled: none` and no Deny rows. skill `cilium-host-policy`.
- **NEVER run `tofu apply` or `tofu destroy` in `terraform/authentik/` without an explicit, current go-ahead.** A green PR, a clean plan, and a passing CI run are not that approval. skill `authentik-terraform`.
- **Do not migrate GPU scheduling to DRA yet, and never via `adminAccess: true`.** The reference repo (`joryirving/home-ops`) schedules Intel GPUs with `resource.k8s.io/v1` claims and it looks like a clean upgrade over our `devic.es/b70` + `gpu.intel.com/xe` device plugins - it was evaluated in full on 2026-08-26 and rejected. The blocker is not our cluster (v1.36.3 serves `resource.k8s.io/v1`; `DynamicResourceAllocation` + `DRAConsumableCapacity` are enabled) and not our hardware (the driver enumerates sysfs PCI generically, so `0xe223` and `0xa7a0` are both found). It is that the only vendor driver, `intel/intel-resource-drivers-for-kubernetes`, says verbatim on `main` *"CAUTION: This is a beta / non-production software, do not use on production clusters"*, and **cannot share one GPU across pods** (upstream issue #79: *"strict 1-to-1 mapping OR SR-IOV"*) - while `vllm` and `tdarr-node` both need the single B70, so 1-to-1 strands one of them `Pending` no matter how many iGPUs the fleet has. The reference repo's claims use `adminAccess: true` + `allocationMode: All` - the only combination that lets more than one pod onto a device with this driver, but one Intel documents for *monitor* deployments, whose allocations are *"not counted by scheduler as consumed resource"*, and which needs `resource.k8s.io/admin-access: "true"` on every GPU namespace. Full design, five-stage cutover plan, alert-migration mapping, and the two upstream conditions that reopen it: `docs/ai/gpu-dra-migration-design.md`.
- **Do not merge `system-controller` or `system-upgrade` into `system`.** k8tz would stop injecting TZ into `system` (the three remaining VolSync claims read that process TZ), the webhook move is not atomic, and tuppr's Talos API allowlist names `system-upgrade`. skill `system-namespaces`.
- **A NetworkPolicy `ports:` entry matches the destination port, so `port: 53` never admits a DNS reply.** Drop the `ports:` restriction on that rule. skill `networking`.
- **NEVER set `sessions.vacuum_after_prune: true` on `ai/hermes`, and never "fix" it back to upstream's default to reclaim disk - the claim growing to 40Gi on 2026-09-20 did NOT retire this.** `last_vacuum` is absent from `state_meta`, so `since_vacuum is None` holds forever, the `min_vacuum_interval_days` throttle never engages, and a full ~9.7 GiB rewrite **through the WAL** would be retried on **every** prune pass that deletes rows. Free space was only ever the *second* argument (it was 6.0 GiB against a 9.31 GiB database when this was measured, and the volume hit 83% before it was grown); the throttle defect is independent of capacity, and filling the volume stops Hermes persisting anything. Pruning alone still bounds growth (freed pages go on the freelist and SQLite reuses them), so the file plateaus rather than shrinking; reclaiming is an offline, operator-driven `hermes sessions optimize-storage` that needs free space first. Measurements, the per-table attribution, and why `retention_days` (not `auto_prune`, which was already on) was the real lever: `docs/ai-system/hermes-state-db-growth.md`.

## UNIQUE STYLES

- **Flux variable substitution**: `postBuild.substituteFrom` references `cluster-secrets` Secret + inline `substitute` map
- **A literal `${...}` that is not a Flux substitution variable fails the whole Kustomization, and neither flate nor `task flux:test:all` catches it.** skill `flux-substitution`.
- **Task runner split (deliberate, not migrated)**: `just` (`.justfile` + `talos/mod.just`, `bootstrap/mod.just`, `kubernetes/mod.just`) owns Talos, bootstrap, and `kube` lifecycle recipes. `task` (`Taskfile.yaml` + `.taskfiles/{domain}/`) owns the Rook operational suite and network diagnostics. Both are installed by `.mise.toml` and neither is CI-invoked. Do not port `task`'s Rook/network recipes onto `just` or vice versa - the split is intentional, not a migration in progress.
- **Component composition**: Namespace overlay `kustomization.yaml` includes common + alerts as components (`../../../components/{common,alerts}` from `apps/main/<ns>/`)
- **Volsync triple-backup (3 carve-outs only)**: Do not add to a new app. The three remaining dual-engine claims still get 3 ReplicationSources (ceph/minio/r2) + 1 ReplicationDestination via `components/volsync`. Defaults/schedules and the multi-volume `path: ./kubernetes/components/volsync/backup` pattern: `kubernetes/components/volsync/Readme.md`. New apps use kopiur-only - see Enable backups above and `kubernetes/components/kopiur/Readme.md`.
- **One component include covers ONE volume.** Every kopiur/volsync object is named from `${APP}` and Flux allows one `postBuild.substitute` map per Kustomization, so an app with a second PVC needs a second Flux Kustomization with `APP` set to the claim name. Pattern: `kubernetes/components/kopiur/Readme.md` / `kubernetes/components/volsync/Readme.md` "Apps with more than one volume"
- **VOLSYNC_CACHE_CAPACITY** (VolSync carve-outs only): Must be sized 20-50% of PVC size - small PVCs need 50-100%
- **dependsOn chains**: Many apps use `dependsOn` in the overlay Kustomization yaml - typically `onepassword-store` in `security` namespace

## COMMANDS

```bash
task setup-dev-env                 # Install tools + pre-commit hooks
task reconcile                     # Force Flux sync from Git
task cleanup-all                   # Remove failed/completed pods + old replicasets
task flux:test:all                 # Validate Flux manifests with flate (whole tree, ~0.3s warm)
task rook:check-disks              # Check Ceph disk status
just talos render-config talos-1   # Render a node's machine config
just talos apply-node talos-1      # Apply config (node names, not IPs)
just bootstrap cluster             # DR / first-time only
```

Talos nodes are `talos-1|talos-2|talos-3` mapping to `10.10.10.11/12/13`. Do not target the VIP `10.10.10.10`. Full recipes: `talos/AGENTS.md`, `bootstrap/AGENTS.md`.

Debugging: `flux get sources git -A`, `flux get ks -A`, `flux get hr -A`, `kubectl -n {ns} get pods -o wide`, `kubectl -n {ns} logs {pod} -f`, `kubectl -n {ns} describe pod {pod}`, `kubectl get replicationsource,replicationdestination -A`.

## SKILL INDEX

Skills live in `.agents/skills/<name>/SKILL.md` (`.claude/skills` is a symlink). Harnesses that do not auto-load them: read the matching `SKILL.md` before touching its subsystem. Stubs point at today's docs until their content lands.

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

## NOTES

- **NEVER run `find /Users/coder`, `find $HOME`, or any unbounded search of the home directory or parent paths.** Validation agents wedge for 19-96 minutes on these searches because the home directory contains hundreds of cached clones and tool installations. When a tool or file is not found, report it and continue - do not hunt for it. The correct pattern: (1) Tools are mise-managed, resolved with `mise which <name>` / `mise exec -- <name>` and live under `~/.local/share/mise/`. (2) Cluster credentials (`kubeconfig`, `talosconfig`) do not exist in fresh worktrees and their absence is **expected and normal** - a pipeline sandbox has no cluster access. If code needs them, it should report the missing file and skip, not search for one. (3) If a tool is not mise-installed, report it explicitly rather than trying to locate it.

- **`kubernetes/clusters/main/` is the only Flux entry point** (`cluster-meta`, then `cluster-apps`). skill `flux-gitops`.

- **`flate -n` filters the exit code, so a broken Kustomization in another namespace still exits 0.** `task flux:test:all` is the gate, and it also misses a literal `${...}` collision. skill `flux-gitops`.

- **`validate.yaml` is the only CI signal for `talos/`, `bootstrap/`, `.renovate/` and credential-less `terraform/`, and `talosctl validate` is schema-only.** skill `github-ci`.
- **Never pin a version in `jdx/mise-action` `install_args` for a tool `.mise.toml` already declares.** Name the tool only. skill `github-ci`.
- **Retiring an app or changing a pinned value can break a `scripts/ci` gate that `flate` does not run.** Grep `scripts/ci/` first and assert shape, not a frozen literal. skill `github-ci`.
- **A red `validate.yaml` check that passes on rerun is runner-pool contention.** Do not raise `maxRunners`, drop the workflow from its own filters, or serialize the jobs. skill `github-ci`.

- `talos/*.j2` changes are not applied by Flux. `machineconfig.yaml.j2` / node overlays: render, `--dry-run`, then `just talos apply-node` per node. `schematic.yaml.j2` (kernel args + extensions) needs `just talos upgrade-node` - `apply-node` only restages the install image reference and does not boot it. Offline validation and the apply vs upgrade distinction: `talos/AGENTS.md`.
- **`talos/machineconfig.yaml.j2`'s 6 version pins (installer, kubelet, kube-apiserver/-controller-manager/-proxy/-scheduler) can drift from the live cluster.** They are a second, Renovate-managed copy of the versions that `kubernetes/apps/base/system-upgrade/tuppr/upgrades/{talosupgrade,kubernetesupgrade}.yaml` actually drive via tuppr - a Renovate PR bumping the template does **not** upgrade the cluster, and a tuppr-driven upgrade does **not** update the template. Applying a stale template with `just talos apply-node` can downgrade a running node. Before any `apply-node`, confirm the template matches `kubectl get nodes -o wide` / the tuppr CRs.
- **Merging `.renovaterc.json5` or any file under `.renovate/` fires `.github/workflows/renovate.yaml` on push.** Suspend that workflow before the merge. Do not re-add branch automerge. skill `renovate`.
- **`quay.io/ceph/ceph` stays on stable `x.2.z` tags only.** Do not apply that regex to `ghcr.io/rook/ceph`. skill `renovate`.
- **A Renovate bump of `talosupgrade.yaml` is an unattended node upgrade.** A bare `<X.Y.Z` pin goes silent once the tracked version passes it (use a negated regex, PR #867), and docker `isStable` treats `-alpha` as a normal minor. skill `renovate`.
- **kustomize, helm, and the other CLIs are mise-managed** (pins in `.mise.toml`). Resolve them with `mise which kustomize` / `mise exec -- kustomize ...` (same for helm, kubectl, flux, talos, ...). See the opening of NOTES above for why unbounded `find` searches are forbidden and what to do when a tool is not found.
- **A `kubectl` on the mise shims uses `.mise.toml`'s `KUBECONFIG` and talks to localhost.** Empty output is the symptom. Pass `--kubeconfig`.
- `kubeconfig` and `talos/talosconfig` are gitignored
- **`Home-Lab` and `Homelab` are different vaults: Connect cannot see `Home-Lab`, and ExternalSecret `SecretSynced` does not mean the value is non-empty.** Never seed a Secret with client-side `kubectl apply` (the value lands in `last-applied-configuration`). skill `secrets-1password`.
- Cluster control plane VIP: `10.10.10.10`
- Nodes use bonded interfaces (802.3ad LACP), MTU 9000, VLANs 3 and 90
- **A container with `limits.memory` and no `requests.memory` reserves the whole limit, and the live Pod already shows the request equal to the limit, so audit the Git manifest.** Gate: `scripts/ci/memory-request-declared-test.py`. skill `node-scheduling`.
- **talos-3 carries `home-operations.com/dedicated: PreferNoSchedule`, a scheduler score penalty (`TaintToleration` weight 3).** A hard `NoSchedule` strands a drain of talos-1 or talos-2, a toleration matches only when its `effect` matches, and the taint applies only after `just talos apply-node talos-3`. skill `node-scheduling`.
- **`MCPServer.spec.resources` never reaches the MCP container.** The ToolHive operator applies it to the proxy Deployment, so those manifests look bounded while the server process is unbounded.
- **A CPU limit is a CFS quota per 100ms period, and throttling never crashes, restarts, or fails a probe.** Do not size a request from a coarse `max_over_time(rate(...[5m]))`, and keep request equal to limit where Guaranteed QoS is the point. skill `node-scheduling`.
- **Storage classes:** `ceph-block` (RWO), `ceph-filesystem-rwx` (RWX, group `csi-rwx`), `ceph-filesystem` (default group `csi` still returns EINVAL; leave new claims off it), `openebs-hostpath` (local). Snapshot class: `csi-ceph-blockpool`. skill `rook-ceph`.
- **Judge Ceph reclaim with `rbd du --exact`.** Plain `rbd du` counts 4 MiB objects that still exist after 64 KiB discards, and `mountOptions: [discard]` on `ceph-block` stays off. skill `rook-ceph`.
- `.private/` directory for local-only files (gitignored)
- Renovate ignores `**/*.sops.*` and `**/resources/**` paths
- **CephX rotation is GitOps-only: never `ceph auth` by hand, never set `security.cephx.csi` to `aes256k` (kernel clients need Linux >= 7.0; these nodes run 6.18.x), and `keyGeneration` only increases.** A CRD older than Rook 1.20.5 prunes `keyType` and burns a generation on an `aes` rotation. skill `rook-ceph`.
- **Leave `rgw_sigv4_insecure: "true"` in place until the image bump that removes it.** On Ceph v20.2.4, minio-go PUTs fail while GET and HEAD succeed, so `HEALTH_OK` does not mean RGW writes work. Clear the flag only in the same change as a Tentacle image that contains ceph/ceph#71192. skill `rook-ceph`.
- **The Ceph metadata CronJob (`rook-ceph-backup` / `ceph-backup-pvc`) is OpenEBS hostpath on this cluster.** A host wipe destroys it. Emergency steps: `kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md`. skill `rook-ceph`.
- **VolSync restore is proven end-to-end from both the Ceph and MinIO destinations** (drill and exact procedure: `docs/backups/restore-drill-2026-08-23.md`) - a small app restores and mount-verifies in ~50s. **Never patch an app's own `<app>-dst` `ReplicationDestination` to trigger a restore.** It's created once by the `volsync` component with `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent`, so Flux never reconciles it again - any manual `trigger`/spec edit persists forever and can silently drift `spec.restic.repository` away from what Git declares (live example found 2026-08-23: `home-automation/esphome`'s `esphome-dst` still points at the MinIO secret from an October 2025 manual restore). Always create a new, uniquely-named scratch `ReplicationDestination` + PVC against the app's existing (read-only) credential Secret instead; the runbook has working manifests, verification, and cleanup steps.

- **`${APP}-dst.status.latestImage` is frozen at first-deploy time, so recreating a claim from its `dataSourceRef` can silently restore NOTHING.** The `volsync` component's `pvc.yaml` populates a claim from the ReplicationDestination's `latestImage`, **not** from the restic repository - and `${APP}-dst` is `trigger: {manual: restore-once}` plus `ssa: IfNotPresent`, so it runs exactly once at first deploy and Flux never re-runs it. For any app onboarded **before its repository had a snapshot**, that one run logged `No eligible snapshots found` / `No data will be restored` and `latestImage` is a snapshot of an **empty** volume that stays the populator's source forever. Deleting such a claim to rebuild it then restores nothing while PVC `Bound`, app `Running` and Flux `Ready` all report success - total silent data loss. Measured on `ai/opencode` 2026-08-31, where `latestImage` was still the empty 2026-08-27 image four days later (its 4,749 files were the app's own first-boot writes, never restored content). Re-measured empty on 2026-09-06 for all three surviving VolSync destinations (`selfhosted/paperless-ngx`, `paperless-ngx-media`, `syncthing-data`); the same day `syncthing-data-dst` was deleted and Flux-recreated with `restore-once` Success from populated snapshot `9dd34c26`, so afterward only `paperless-ngx` and `paperless-ngx-media` remain empty, and only `paperless-ngx` still points `dataSourceRef` at one. **Always read `status.latestMoverStatus.logs` and `status.lastSyncTime` before trusting `dataSourceRef`.** The fix is to delete the `ReplicationDestination` *together with* the PVC so Flux recreates it as a new object and `restore-once` fires against the now-populated repository - never to patch `spec.trigger.manual`, which `IfNotPresent` would make permanent drift (previous entry). Two further things that bite in the same operation: VolSync **re-stages a new clone within seconds** of each mover Job delete, so the `ReplicationSource`s must be deleted (harmless - it never touches the restic repository) rather than fought; and a kopiur `Snapshot` CR left `Running` **blocks every later scheduled backup** for that claim under `concurrencyPolicy: Forbid`. Full procedure: `docs/backups/corrupt-claim-recreation-runbook.md`; measured evidence: `docs/backups/opencode-volume-recreation-2026-08-31.md`.

- **A VolSync restore permanently relaxes every file mode by one group-write bit, `0600` credentials included.** The mover stages its destination PVC **writable**, so kubelet's recursive `fsGroup` walk runs before restic writes: a restored volume comes back `644→664`, `755→775`, `2755→2775`, `600→660`, `444→664`. Measured across all 4,749 files of `ai/opencode` on 2026-08-31, including its `.git-credentials`. Ownership is unaffected. This is the same staging asymmetry that makes an identity mismatch invisible to VolSync and fatal to kopiur (see the kopiur trap 0 entry) - read here it means **a VolSync restore is not mode-faithful**, so do not use one to reproduce a permissions bug, and expect the relaxation to persist on the live volume afterwards. kopiur restores, which stage read-only, reproduce the original modes.
- **kopiur is live on all 29 of the fleet's 29 VolSync-derived claims and is now the ONLY engine on 26 of them (Stage 5 complete 2026-09-04; VolSync survives on `selfhosted/paperless-ngx{,-media}` and `syncthing-data` alone; born-kopiur claims like `database/falkordb` sit outside that 29); three of its traps lose data or silently fail a backup.** (a) A kopiur `Snapshot` CR **owns** its kopia snapshot through a finalizer, so **deleting kopiur CRs deletes backup data** - unlike a `ReplicationSource`, which never touches the restic repository - and we run Flux with `prune: true`, which is that exact shape. What makes a GitOps removal survivable is `deletion.onPolicyDelete`/`onScheduleDelete: Retain`, pinned on every policy and schedule in the component, and it retains **the kopia data, NOT the CRs**: every Snapshot carries a `controller: true` ownerReference to its `SnapshotSchedule`, so a prune GC-cascades the CRs away and the catalog rediscovers the surviving snapshots as `origin: discovered`. That zeroed CR count is the documented success path, not data loss - so never verify a removal with a CR census (measured against the live CRDs 2026-09-02: `docs/backups/autobrr-removal-2026-09-02.md`). Separately, **creating** a `Snapshot` deletes data too whenever it pushes an older one past GFS retention, which on every `r2` policy (no `keepHourly` tier) evicts that day's newest snapshot - `docs/backups/kopiur-wave-two-reproof-2026-09-02.md`. (b) **The mover's identity must match the workload that owns the claim's files or the backup FAILS closed**; `KOPIUR_PUID`/`KOPIUR_PGID` default to 1000 and are not cosmetic, so measure the claim's **file** ownership and never infer it from the pod's `runAsUser`. VolSync survives the identical mismatch only because it stages its clone writable; kopiur stages read-only. (c) **Retiring a volume is a PVC-ownership swap and getting it wrong deletes the volume** - `kubernetes/components/kopiur/pvc` must carry `kustomize.toolkit.fluxcd.io/ssa: IfNotPresent` and must NEVER carry `force: enabled`. Everything else - the ten onboarding traps, `credentialProjection` and its three required legs, schedules and timezone, per-claim identities, the Stage 2-5 history: skill `kopiur-backups`, `kubernetes/components/kopiur/Readme.md`.

- **A kopiur `Restore` whose `KOPIUR_CACHE_CAPACITY` is smaller than `min(snapshot sizeBytes, ~6.2 GiB)` fails TERMINALLY and never retries, and it is a cliff rather than a slope - a claim sitting just under its capacity is one growth spurt from a restore that can only be discovered broken during an actual disaster** (measured 2026-09-02; `media/tdarr` 10Gi is r2-proven at that value, and `ai/hermes` was too at 16Gi until its claim grew 25Gi -> 40Gi on 2026-09-20 and took the cache to 48Gi - sized off the claim's usable ceiling, above the proven figure rather than below it, as conservatism against the plateau being an unpinned kopia default and NOT because 16Gi had become insufficient: `min(snapshot, ~6.2 GiB)` means a snapshot outgrowing its cache does not by itself raise the requirement past the plateau). **A standing populator can also silently disagree with Git forever** - `components/kopiur/ceph/restore.yaml` carries `ssa: IfNotPresent`, so Flux creates it once and never reconciles it, and raising a `KOPIUR_*` value needs a one-time delete of that `Restore` to take effect (done for `media/tdarr` and `downloads/radarr` 2026-09-02; the 2026-09-02 audit closed drift at 0 of the then-30 against `main`). That, the fleet audit, and the two traps that change how you read any kopiur backup - kopia's `CACHEDIR.TAG` omissions, and reading "live" through the app pod - are in skill `kopiur-backups`, `docs/backups/kopiur-r2-restore-cache-gate-2026-09-02.md` and `docs/backups/kopiur-populator-drift-2026-09-02.md`.

- **Both backup engines snapshot a volume CRASH-CONSISTENTLY with no application hook, so a database must be configured to fsync at the durability you actually want or its backup is silently stale - measured on `database/falkordb`, where one crash-consistent snapshot restored 2174 of 2199 nodes with AOF on and 200 of 2199 with the image's stock RDB-only durability, both reporting a completely healthy start: `docs/backups/falkordb-snapshot-restorability-2026-09-04.md`.**

- **CNPG can express only one live barman destination per `Cluster` - a second `ScheduledBackup` carrying its own `barmanObjectName` is accepted, runs, and reports completed while silently writing to the original store** (upstream cloudnative-pg#7778, closed as not planned). Worked alternative - mirror the archive instead: `kubernetes/apps/base/database/cloudnative-pg/offsite-mirror/README.md`; fuller evidence: `docs/backups/postgres-offsite-destination-design-2026-09-12.md`.

- **A kopiur `IndexBlobHealth=False` warning is an epoch-tuning problem, not a broken maintenance run, and all three fixes the condition message suggests are wrong: `takeoverPolicy: Force` is a one-shot action a declarative manifest would re-apply forever, raising the threshold hides a real inefficiency, and its suggested `6h` `minDuration` lands in a 19%-headroom band because epoch age is counted from the first index blob (not the epoch marker) and headroom therefore STEPS rather than slopes - never interpolate or round this value - skill `kopiur-backups`, `docs/backups/kopiur-ceph-index-blob-compaction-2026-09-03.md`.**

- **Nothing expires a retired VolSync restic repository, and the obvious way to expire one destroys live data while looking correct in review.** Deleting a `ReplicationSource` never touches its restic repository, and restic's `--keep-*` tiers are all relative to the repo's NEWEST snapshot - so on a frozen repo every policy keeps at least one snapshot forever and can never reach zero. Expiring a retired repository is therefore a whole-prefix deletion on a date, never a retention policy (`Expiration.Days` is likewise wrong: it counts from each object's creation time and would delete everything immediately). The trap: repositories are `<bucket>/<APP>`, and `volsync/syncthing` (RETIRED) is a strict string prefix of `volsync/syncthing-data` (LIVE) - a `startswith`, glob, unanchored regex or bare S3 `Filter.Prefix` of `syncthing` selects all 107 live objects too. Match the path segment EXACTLY (`<name>/`, which cannot reach a longer segment) and never by prefix. The 2026-09-12 expiry decision (126.03 GiB across 48 retired repositories on ceph+r2+minio, tiered 2027-03-31 / 2027-09-30), the proof, and why no declarative path here can apply it - Rook's OBC controller has no update path, so `bucketLifecycle` on the 341-day-old `volsync` OBC is a silent no-op: `docs/backups/volsync-retired-repository-expiry.md`, gate `scripts/ci/volsync-retired-expiry-test.py`. **The decided expiry is NOT in force** - no declarative path can apply it, so it lands only when an operator runs `docs/backups/volsync-retired-expiry-apply-plan.md` per destination (r2 additionally needs a Cloudflare token in the `Workers R2 Storage Write` group; the in-cluster one is object-scoped and gets `AccessDenied`).
- **An app/mover identity mismatch does NOT mean the VolSync backups are incomplete - measure before you believe it, because the two engines differ exactly here.** Measured on `selfhosted/changedetection-config` 2026-08-31, where the app ran as root and wrote 2292 mode-`0600` root-owned files against a `1000:1000` mover: the ceph restic backup was nonetheless **complete and byte-identical** - a restore of the current snapshot into a scratch PVC reproduced all 3058 files with a matching per-file sha256 manifest, `changedetection.json` included. The reason is the trap-0 asymmetry read in the *other* direction: VolSync stages its clone **writable**, so kubelet's `fsGroup` walk adds the group-read bit before restic ever opens a file (the restored copies come back mode `660`/`664` where live is `600`/`644` - that permission inflation is the fingerprint). kopiur stages **read-only**, gets no `fsGroup` fixup, and fails closed. So the same mismatch is invisible to VolSync and fatal to kopiur. Do not infer either engine's state from the other, and do not report a VolSync integrity defect without a restore to back it: the cheap direct probe is to mount the claim read-only in a pod running as the mover uid and count `find <mount> -type f ! -readable`.
- **`Snapshot.status` `SecurityContextCompatible` is positive-only and much narrower than its wording suggests - its absence is not a failure signal.** It requires the mover uid to match **every container of every pod mounting the claim, initContainers included**, even containers that mount nothing; and it is not reliably emitted even then (`autobrr`'s r2 run carried it while its ceph run did not - same claim, same pod, same identity, no operator restart between). Several live claims lack it for entirely benign reasons (CronJob-only claims with no pod at snapshot time; root `fix-permissions` initContainers on `pgadmin` and `falkordb`; `changedetection`'s browserless sidecar pinned to uid 999 that **cannot** be moved - Chrome fails to launch as 1000 while the container still reports `Ready`). The real proof that a mover identity works is that kopia read the volume: a `Succeeded` snapshot whose `.status.stats` covers the whole volume, plus the read-only-mount probe above. Measured table and the browserless evidence: `kubernetes/components/kopiur/Readme.md` "SecurityContextCompatible".
- **A declared `APP_UID`/`APP_GID` in `postBuild.substitute` that no manifest reads is a liability, not documentation - delete it, don't wire it in.** `changedetection`'s retired 2000:2000 (PR #1512) was not a one-off: fleet-audited 2026-08-31, `selfhosted/{n8n,paperless-ngx,ntfy,syncthing,linkwarden}` all carried the identical dead pair, and `obsidian-livesync` carried a live one (5984:5984) that was still unconsumed by any template - repo-wide, only `KOPIUR_PUID`/`PGID` and `VOLSYNC_PUID`/`PGID` actually drive a mover's `moverSecurityContext`/`podSecurityContext`. All six were removed, each with a comment pointing back to `changedetection.yaml`'s precedent. The audit's three-way check (declared vs. live workload `id` vs. live mover `moverSecurityContext`/`podSecurityContext`) also caught a real mismatch this pattern was hiding: `obsidian-livesync`'s VolSync mover ran the unmatched 1000:1000 component default while its workload and kopiur mover both run 5984:5984 (CouchDB) - invisible only because every file on that claim is mode 644/755. Fixed by pinning `VOLSYNC_PUID`/`PGID: "5984"` (the same variables `media/calibre` already uses for a non-default identity), verified safe (5984 owns 100% of the volume's 8 files) and validated live via suspend/patch/resume before merging - the ceph `ReplicationSource` synced successfully at the new identity with an unchanged file count. No fleet-wide dead-variable linter was added (one-time cleanup, not a recurring shape); `scripts/ci/kopiur-stage3-test.py`'s `EXPECTED_IDENTITY` table already pins the real per-claim kopiur identity, and `scripts/ci/selfhosted-backup-identity-test.py` pins this cleanup plus the obsidian-livesync VolSync 5984 alignment (including a fail-closed default-path check).

- **An empty `resources.limits: {}` in Helm values leaves the chart default in place.** The Rook operator chart defaults to 512Mi; the manifest sets an explicit 1Gi (PR #1751). skill `rook-ceph`.
- **Before any node reboot, confirm `ceph status` is `HEALTH_OK` and `task rook:check-osd-device-paths` is clean, then reboot one node at a time.** `osdMaxUpdatesInParallel` stays 1. A stuck OSD: `docs/ceph/osd-device-path-recovery.md`. skill `rook-ceph`, skill `talos-nodes`.
- **Never use `just talos shutdown-node` for planned power work.** The recipe hard-codes `talosctl shutdown --force` and skips cordon and drain. Runbook: `docs/runbooks/power-down-up.md`. skill `talos-nodes`.
- **A talos-3 reboot can drop the Arc Pro B70: the dock has its own PSU, and `pcie_port_pm=off` does not apply until the next schematic upgrade.** Power the dock before the host. Runbook: `docs/runbooks/talos-3-b70-reboot.md`. skill `talos-nodes`.
- **The self-hosted ARC runners have no Kubernetes API, fork PRs never reach them, and the home-ops set has no Docker.** skill `github-ci`.

- **Not every LAN record is external-dns's: a record with no registry TXT twin is hand-made, and the parent Gateway decides the DNS target.** skill `networking`.

- **An Envoy Gateway `HTTPRouteFilter` directResponse blocks a path before any backend.** skill `networking`.

- **A controller that has been failing does not retry the moment the cause is fixed; `Ready=False` stays stale until the backoff elapses.** skill `flux-gitops`.

- **`tofu validate` misses four Authentik traps:** the S3 backend fails open to AWS, a created provider needs `grant_types`, declaring `client_secret` on an imported provider rotates the live secret, and `authentik_flow` ids are slugs. skill `authentik-terraform`.

- **`healthChecks` must target the workload, not the HelmRelease, and `wait` must stay false.** skill `flux-gitops`.

- **The `flux-system` Namespace is exempt from `cluster-apps`, so a git-only change to it can pass every gate and never land.** skill `flux-gitops`.
- **Suspend a Flux Kustomization before a live HelmRelease edit.** `kubectl apply` cannot remove a values key on that object. skill `flux-gitops`.

- **A stale `actions-runner` Talos client cert fails image-pull on every PR that touches kubernetes manifests.** `remote error: tls: expired certificate` means the node rejected the runner cert. Delete Secret `actions-runner` in `actions-runner-system`. skill `github-ci`.

- **linuxserver images need `LSIO_NON_ROOT_USER` and `LSIO_READ_ONLY_FS`, plus `/run` and `/tmp` emptyDirs, under this repo's non-root convention.** skill `app-workloads`.

- **LiteLLM: a config-declared fallback bypasses every virtual key's model allow-list, and a `LiteLLMModel` with no `apiKey` silently bills the household-metered `ANTHROPIC_API_KEY`** - so a cloud fallback may only sit on an alias every holder is already cloud-entitled to, and the subscription pass-through CRs keep their placeholder key and `$0` prices. Everything else about `ai/litellm*`, keys, models and the AI PR reviewer: skill `litellm-proxy`.

- **No xpu-smi/level-zero/DCGM-equivalent GPU exporter is deployed**, so per-engine busy %, VRAM utilization and clocks are not queryable in Prometheus for either Intel GPU. What does exist - the B70's `xe` hwmon chip (temps, fan RPM, energy counters to `rate()` for live watts) and allocation-only kube-state-metrics for the iGPU, which has no hwmon chip at all: skill `intel-gpu`, `kubernetes/apps/base/ai/gpu-node-dashboard/app/gpu-node.json`.

- **ExtAuth is one domain-wide proxy provider, so a new hostname does not need its own Authentik application; a new namespace needs a ReferenceGrant `from` entry.** An unauthenticated 302 is not proof of coverage. skill `authentik-terraform`.

- **An app can be Ready while it cannot write its PVC: a pod-options key the chart does not read is discarded, and `flate` stays green.** skill `app-workloads`.
- **`fsGroup` re-owns existing volume content on the next mount, including under `OnRootMismatch`, and the chown persists after revert.** skill `app-workloads`.
- **Helm cannot unset a field it never rendered, so a hand-added `securityContext` or volume survives `flux reconcile`.** skill `flux-gitops`.

## Maintaining this file

`CLAUDE.md` is a symlink to this file - there is only one copy to maintain, and edits here are
automatically visible under either name. Do not recreate `CLAUDE.md` as a separate file.

Keep this file for knowledge useful to almost every future agent session in this project.
Do not repeat what the codebase already shows; point to the authoritative file or command instead.
Prefer rewriting or pruning existing entries over appending new ones.
When updating this file, preserve this bar for all agents and keep entries concise.

### Where a new finding goes

> A finding lands in its own document or a subsystem skill. It earns an `AGENTS.md` line only when
> it fits in one sentence plus a pointer AND an agent who has not yet decided to touch that
> subsystem needs it.

This file is loaded into every session in this repo before any work starts, so every line here is
paid for by every agent, including the ones that never go near the subsystem it describes. It grew
about fifteen-fold in eleven days entirely as depth *inside* existing bullets, which is why the
rule is about depth rather than entry count.

The other cost is duplication: roughly 95% of this file is original re-phrasing rather than
quotation, so each summary here is a second copy that has to be maintained independently of the
document it summarises, and it drifts. That already happened - this file and
`docs/backups/kopiur-stage5-pilot-retirement-2026-09-01.md` both carried the kopiur restore
prerequisite as "raise the cache AND prove an r2 restore" after `media/plex`'s raise to 10Gi had
already landed, while the source proof document recorded the standing values correctly.

So: write the finding once, where it is owned. Add a subsystem skill under `.agents/skills/` when
the audience is "an agent working on that subsystem" - the `description` line is the load trigger,
so state the trigger condition precisely; a vague one means the skill never loads when it is
needed. Keep an always-loaded line here only for a tripwire: silent data loss, a cluster-wide
outage, or an unrecoverable state, where an agent needs the warning *before* deciding to touch the
subsystem at all.

`scripts/ci/docs-budget-test.py` enforces this: it fails when this file, a skill, a skill
`references/*.md` file or a YAML/json5 comment block grows past its budget or its recorded
baseline. Raising one needs an allowlist entry with a reason in `scripts/ci/docs-budget.json`;
lower the baseline whenever you shrink a file.
