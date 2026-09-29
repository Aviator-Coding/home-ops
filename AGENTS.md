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
| Enable backups | overlay `kubernetes/apps/main/{ns}/{app}.yaml` | kopiur-only. `components/kopiur/pvc` is the VolSync-retirement takeover shape only. Dual-engine survives on `paperless-ngx`, `paperless-ngx-media`, and `syncthing-data`. Do not add `components/volsync` to a new app. Skill `kopiur-backups`.
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
| AI stack | `kubernetes/apps/main/ai/` (Flux Kustomizations) + `kubernetes/apps/base/ai/` (manifests) | Hermes + ToolHive (`toolhive.stacklok.dev/v1alpha1` `MCPServer`) + agentgateway + LiteLLM (governance layer, internal route only; skill `litellm-proxy`). Inventory, the ToolHive API group, and retirement: skill `ai-stack`. |
| konflate (PR review UI) | `kubernetes/apps/base/flux-system/konflate/` | Read-only Flux PR-review UI, internal HTTPRoute only. Write-back is off and no GitHub credential is in-cluster (public-repo anonymous reads). Do not copy the reference repo's write-back / shared GitHub App wiring. |
| PVC write-access check | `kubernetes/apps/base/system/pvc-writable-check/` | Every 6h, `test -w` on each PVC mount. `pods/exec` RoleBindings omit rook-ceph, database, and security. Skill `pvc-integrity-checks`.
| PVC mover read-access check | `kubernetes/apps/base/system/pvc-mover-readable-check/` | Every 6h at minute 47. Alerts on kopiur; report-only for VolSync. UNMEASURED is never a pass. `database/pgadmin` is a permanent gap. Skill `pvc-integrity-checks`.

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
- **NEVER rename a DRM device node with `generic-device-plugin`'s `mountPath`, and never ship a GPU change without running the VA-API check.** Remapping the `b70` group to `card0`/`renderD128` kills VA-API (libdrm reopens the canonical `DEVNAME`) while Level Zero stays green, so transcoding dies with the AI stack still healthy. VA-API consumers must use `devic.es/b70-vaapi`; allocatable capacity is not proof. Skill `intel-gpu`, `docs/media-stack.md` "Verifying VA-API after a GPU change", `docs/ai-gpu-changelog.md`.
- **The B70 has two tenants and no compute partition: driving the embedding endpoint hard throttles the captain's live chat model.** Throttle by request rate. Skill `b70-llm-serving`.
- **Both B70 llama.cpp workloads must pin `--cache-ram` (chat a bounded non-zero value; embeddings stay `0`) and `vllm` must set `GGML_SYCL_FA_ONEDNN: "0"` numerically, or host RAM leaks past any `limits.memory` with no OOM signal.** Skill `b70-llm-serving`.
- **An embedding endpoint can return vectors of pure NaN while every health signal is green.** Verify by a finite non-zero value, never by shape, length, or non-zero count. Skill `b70-llm-serving`.
- **kagent / kmcp are not deployed.** Live AI stack is Hermes + ToolHive + agentgateway. ToolHive `MCPServer` is `toolhive.stacklok.dev/v1alpha1`, never kagent.dev's same kind name. Skill `ai-stack`.
- **NEVER add a `CiliumClusterwideNetworkPolicy` with a `nodeSelector` and an ingress section unless `enableDefaultDeny.ingress` is false.** Default-deny is OR-ed across policies and locks the host endpoint. skill `cilium-host-policy`.
- **A CCNP `nodeSelector` must use a label that survives onto the host endpoint.** `kubernetes.io/os` and `kubernetes.io/hostname` are stripped, so an unmatched selector fails open. skill `cilium-host-policy`.
- **NEVER run `tofu apply` or `tofu destroy` in `terraform/authentik/` without an explicit, current go-ahead.** A green PR, a clean plan, and a passing CI run are not that approval. skill `authentik-terraform`.
- **Do not migrate GPU scheduling to DRA yet, and never via `adminAccess: true`.** The Intel DRA driver cannot share one GPU across pods, and `adminAccess: true` is a monitor allocation the scheduler does not count. `vllm` and `tdarr-node` both need the single B70. Skill `intel-gpu`.
- **Do not merge `system-controller` or `system-upgrade` into `system`.** k8tz would stop injecting TZ into `system` (the three remaining VolSync claims read that process TZ), the webhook move is not atomic, and tuppr's Talos API allowlist names `system-upgrade`. skill `system-namespaces`.
- **A NetworkPolicy `ports:` entry matches the destination port, so `port: 53` never admits a DNS reply.** Drop the `ports:` restriction on that rule. skill `networking`.
- **NEVER set `sessions.vacuum_after_prune: true` on `ai/hermes`, and never turn it back on to reclaim disk.** `last_vacuum` is absent, so the interval throttle never engages and a full rewrite through the WAL retries on every prune that deletes rows. Filling the volume stops Hermes persisting anything. Skill `hermes-agent`.

## UNIQUE STYLES

- **Flux variable substitution**: `postBuild.substituteFrom` references `cluster-secrets` Secret + inline `substitute` map
- **A literal `${...}` that is not a Flux substitution variable fails the whole Kustomization, and neither flate nor `task flux:test:all` catches it.** skill `flux-substitution`.
- **Task runner split (deliberate, not migrated)**: `just` (`.justfile` + `talos/mod.just`, `bootstrap/mod.just`, `kubernetes/mod.just`) owns Talos, bootstrap, and `kube` lifecycle recipes. `task` (`Taskfile.yaml` + `.taskfiles/{domain}/`) owns the Rook operational suite and network diagnostics. Both are installed by `.mise.toml` and neither is CI-invoked. Do not port `task`'s Rook/network recipes onto `just` or vice versa - the split is intentional, not a migration in progress.
- **Component composition**: Namespace overlay `kustomization.yaml` includes common + alerts as components (`../../../components/{common,alerts}` from `apps/main/<ns>/`)
- **VolSync stays on three carve-outs** (`paperless-ngx`, `paperless-ngx-media`, `syncthing-data`). Do not add `components/volsync` to a new app. Skill `volsync-carveouts`.
- **One component include covers one volume.** A second PVC needs a second Flux Kustomization with `APP` set to the claim name. Skill `kopiur-backups`.
- **`VOLSYNC_CACHE_CAPACITY`** is 20-50% of the PVC, 50-100% for a small claim. Skill `volsync-carveouts`.
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
- **Never patch an app's own `<app>-dst` ReplicationDestination to trigger a restore.** `ssa: IfNotPresent` makes the edit permanent and the repository can drift. Skill `volsync-carveouts`.

- **`${APP}-dst.status.latestImage` is frozen at first deploy, so recreating a claim from `dataSourceRef` can restore nothing while Bound, Running, and Ready stay green.** Delete the ReplicationDestination together with the PVC. Skill `volsync-carveouts`.

- **A VolSync restore relaxes every file mode by one group-write bit (`600` to `660`), and that relaxation stays on the volume.** kopiur restores keep the original modes. Skill `volsync-carveouts`.
- **Deleting a kopiur Snapshot CR deletes the kopia snapshot. `Retain` keeps the backup data, not the CRs.** Measure mover identity from the files (`KOPIUR_PUID` defaults to 1000) or the backup fails closed. The PVC component is `ssa: IfNotPresent` and must never set `force: enabled`. Skill `kopiur-backups`.

- **A kopiur Restore whose cache is under `min(snapshot sizeBytes, ~6.2 GiB)` fails terminally and never retries.** Raising `KOPIUR_*` does not update a standing Restore; delete that Restore once the new value is on main. Skill `kopiur-backups`.

- **Both backup engines snapshot crash-consistently, with no application hook, so a database backup is only as durable as its fsync setting.** FalkorDB on one snapshot: AOF everysec recovered 2174 of 2199 nodes, RDB-only recovered 200, and both starts looked healthy. Skill `databases`.

- **CNPG accepts only one live barman destination per Cluster. A second ScheduledBackup reports completed while still writing to the first store** (cloudnative-pg#7778). Mirror the archive. Skill `databases`.

- **`IndexBlobHealth=False` is epoch tuning, not a failed maintenance run.** Do not commit `takeoverPolicy: Force`, and do not interpolate `minDuration`: headroom steps. Skill `kopiur-backups`.

- **Retired VolSync repository expiry is NOT IN FORCE. A prefix match on `syncthing` also selects live `syncthing-data`.** Match the path segment exactly (`<name>/`). Skill `volsync-carveouts`.
- **A uid mismatch does not mean a VolSync restic backup is incomplete. kopiur fails closed on the same mismatch.** Measure with a restore, or mount the claim read-only as the mover uid. Skill `volsync-carveouts`.
- **`Snapshot.status.SecurityContextCompatible` is positive-only. Its absence is not a failure.** Proof is a Succeeded snapshot whose stats cover the volume. Skill `kopiur-backups`.
- **A declared `APP_UID`/`APP_GID` that no manifest reads is a liability. Only `KOPIUR_PUID`/`PGID` and `VOLSYNC_PUID`/`PGID` set the mover identity.** Skill `kopiur-backups`.

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
