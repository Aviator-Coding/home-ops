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
- **Do not merge `system-controller` or `system-upgrade` into `system`.** Evaluated in full on 2026-08-31 and rejected; both namespaces exist for a reason and neither is stateless. **k8tz**: the chart unconditionally prepends its own release namespace to the webhook's `ignoredNamespaces` (`k8tz.webhook.ignoredNamespaces` in `_helpers.tpl`), so putting k8tz in `system` *necessarily* strips TZ injection from `system` - there is no opt-out, and `webhook.ignoredNamespaces` only adds. That matters because **VolSync's cron scheduler reads the process `TZ` k8tz injects** into its manager pod (measured: `ai/hermes-r2` `10 2 * * *` -> `nextSyncTime 06:10Z` = 02:10 EDT), so losing it shifts all 90 `ReplicationSource`s by 4h/5h and collides them with kopiur's 60 `timezone`-pinned `SnapshotSchedule`s, undoing PR #1509's engine stagger. The volsync chart exposes no `env`/`extraEnv`, so the only compensation is a per-app postRenderer patch - permanently, for every future workload in `system`. The `k8tz.io/controller-namespace` label is **not** a finer-grained alternative: it excludes the whole namespace exactly like the by-name entry, and the chart defines no `objectSelector`. The move is also not atomic - `MutatingWebhookConfiguration/k8tz` is cluster-scoped and same-named across both releases, so one prune/install ordering fails every pod CREATE in all 21 covered namespaces and the other silently deletes the webhook. **tuppr**: `system-upgrade` is named in `talos/machineconfig.yaml.j2`'s `features.kubernetesTalosAPIAccess.allowedKubernetesNamespaces`, which gates its `ServiceAccount.talos.dev/tuppr-talosconfig` (`os:admin`), so a move needs `just talos apply-node` on all 3 nodes *before* the Flux change, plus recreation of a `rebootMode: powercycle` `TalosUpgrade` CR. Full evidence, the complete by-name reference list (including the `scripts/ci/` gates that hardcode these paths), and the conditions that would reopen it: `docs/system-namespace-consolidation-analysis.md`.
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
- **A container declaring `limits.memory` with no `requests.memory` reserves the WHOLE limit, and this defect CANNOT be audited from the live cluster** - Kubernetes defaults the request to the limit, so an intended ceiling silently becomes a node reservation. The trap for the auditor is that the API server has already written `requests == limits` into every Pod by the time anything can read it, making a defaulted request byte-identical to a deliberate Guaranteed-QoS one; only the Deployment/HelmRelease shows the difference. A 2026-09-19 fleet audit run against the cluster therefore reported the class **closed at zero** while **16 sites were live in Git** - 15 were fixed 2026-09-20 with requests sized above each container's measured 14d peak, returning ~4756Mi. Audit this in Git, never in the cluster; `scripts/ci/memory-request-declared-test.py` holds it closed for every resources block it can see - full manifests plus the embedded body of a Kustomize strategic-merge `patch: |` block - but a JSON6902 op-list patch (`- op: add ...`) stays opaque to it by design, since judging one needs the target object the gate does not have. It allows `request == limit` where there is no headroom - the objection is to the value being implicit, never to its size - and carries two documented exemptions: `database/cloudnative-pg` cluster-17, and the SMP memory-limit patch in `flux-system/flux-instance`'s HelmRelease (verified live: the patched Deployments already inherit `requests.memory: 64Mi` from flux-operator's own base manifest, so the limit-only patch body does not reproduce the defect). **talos-3 is the node where this arithmetic bites, and it is no longer the one with no slack.** It is the GPU node - `devic.es/b70` is capacity 99 there and **0** on talos-1/2, so `ai/vllm` and `media/tdarr-tdarr-node` cannot be scheduled anywhere else - and it also carries 2 of the 6 Ceph OSDs, a mon, and the `postgres-17` instance that required an anti-affinity pin one-per-node. `ai/vllm` requested 39Gi from 2026-09-14 to cover its then-measured 39342Mi peak - that peak was the host prompt-cache leak, **not** `--no-mmap`, which is retracted as a lever (it costs no host memory; see `docs/ai/vllm-host-prompt-cache.md`). PR #1731 bounded the leak on 2026-09-19 and the request was cut to **12Gi** on 2026-09-20 against the measured post-fix steady state (17h, peak 5750Mi, oscillating 4577-5750Mi rather than climbing; 12Gi = baseline 1232Mi + 2.7x the 4096Mi `--cache-ram` bound = 2.14x the peak). **Corrected 2026-09-22:** that 17h window was low-traffic (0.95M prompt tokens), not a proven plateau - a second, independent leak (oneDNN SDPA partition cache) re-grew the pod under real traffic until fixed by `GGML_SYCL_FA_ONEDNN: "0"`; the 12Gi arithmetic itself is unaffected - see `docs/ai/vllm-onednn-sdpa-leak.md`. **That plus the Class-A sweep took talos-3 from 95.3% committed to roughly 64%**, its first real headroom in months - which made the protection weaker, not stronger: a `hostname NotIn talos-3` affinity deny-list **fails open** (5.9GiB drifted onto the node in the four days to 2026-09-19 purely because the arrivals carried no affinity at all). **Replaced 2026-09-26 with a `home-operations.com/dedicated` node taint** (`talos/nodes/talos-3.yaml.j2`), with a matching toleration added to every workload that genuinely belongs on that node (the GPU pair, the 2 OSDs + mon via `CephCluster.spec.placement.all`, the CNPG instance, and the DaemonSets whose chart allows it). **Corrected the same day to `PreferNoSchedule`, not `NoSchedule`:** a hard `NoSchedule` fails closed on more than the deny-list it replaced - once the general fleet drifts off talos-3 on its own next reschedule (the taint's intended effect), draining talos-1 or talos-2 has nowhere to place ~85 of those pods (cert-manager, coredns, authentik, the Rook operator among them) while talos-3 sits on ~13 free cores it cannot receive them into, and tuppr's upgrade hooks have no window to lift a hard taint safely. `PreferNoSchedule` is a scheduler score penalty (`TaintToleration` weight 3 on stock `kube-scheduler`), not a filter, so the general fleet can overflow onto talos-3 during a drain and returns on its next rollout; only the GPU/OSD/mon/CNPG/DaemonSet tolerations (updated to match the new effect) still guarantee placement there. Two DaemonSets could not get their toleration through the normal chart path - the rbd/cephfs CSI nodeplugins (owned by `Driver` CRs from an internal, non-Flux-managed `ceph-csi-drivers` Helm release, same structural gap as the documented ctrlplugin one for the rest of that object) and multus (chart hardcodes its tolerations) - but both were still closeable: the CSI nodeplugin one via a plain partial `Driver` manifest setting just `spec.nodePlugin.tolerations` (traced to the exact operator source line that reads it - `kubernetes/apps/base/rook-ceph/rook-ceph/operator/csi-driver-tolerations.yaml`), multus via a `postRenderers` JSON6902 patch - see `docs/talos-3-scheduling-truth.md` section 9 for the full inventory, citations, and the live commands to verify both after merge. **The taint only takes effect after an operator runs `just talos apply-node talos-3`** - merging the config alone does not apply it (see talos/AGENTS.md). `VLLMMemoryExceedsRequest` is the detector if the 12Gi proves too tight; `VLLMMemoryRetainedAboveBound` separately catches a leak independent of the request value; nothing else in the repo alerts on crossing a request. Do not add a workload to talos-3, raise a request there, or remove one of those affinity blocks without redoing the arithmetic in `docs/talos-3-scheduling-truth.md`. That doc also records the OSD pair's reservation - 25088Mi since the 2026-09-20 cut of each OSD's **request** 14Gi -> 12Gi (limits deliberately left at 14Gi so a spike can still burst). Most of that is genuinely not slack, but do not re-derive the floor from `osd_memory_target: 10 GiB`: that target sizes the BlueStore cache, not total RSS, and the highest observed OSD working set is 9,317Mi, measured mid-degradation. The remaining 12Gi request is sized against **that** peak, not the target - so 10Gi is not an available further cut. It also records that `MCPServer.spec.resources` never reaches the MCP container - the ToolHive operator applies it to the proxy Deployment, so nine manifests appear to bound a server that is in fact unbounded.
- **A CPU limit is an ABSOLUTE CFS quota per 100ms period, not a share of the node - so node-level headroom is no evidence a container is not throttled, and CPU throttling NEVER produces a crash, a restart, a failed probe or an OOMKill, only slow and late work that nothing in this repo alerts on.** That pair is why three containers ran degraded unnoticed until the 2026-09-20 CPU right-sizing: `system/pvc-writable-check` (the unwritable-volume guard) spent **89.1% of its CFS periods throttled on every one of 58 runs in 14d**, 43.4s per run stopped dead, while its node sat at 15% busy; its sibling `pvc-mover-readable-check` 58.0%; and `system-controller/k8tz` 29.6% **while averaging 2-3 millicores** - a `failurePolicy: Fail` admission webhook on the critical path of every pod CREATE in 21 namespaces, whose measured admission latency ran p90 286ms / worst 2.5s against its 10s timeout. **Mean CPU usage is never evidence about a bursty workload's limit** (k8tz's mean was 4% of its quota while it was throttled in a third of its periods), and **low mean usage under a limit is circular** - the quota is what caps it. **Equally, do not size a CPU request from `max_over_time(rate(...[5m])[14d:1h])`: every coarser sampling under-reports a CPU peak, without bound.** Measured on this cluster, the same 14d peak reads 27m / 1012m / 1033m / **1397m** at `[14d:1h]` / `[14d:5m]` / `[14d:1m]` / `[14d:30s]` for `database/postgres-17`, and 169m -> **1441m** for `rook-ceph` rgw; at 30s resolution **48 workloads already peak ABOVE their CPU request**, so "83% requested, 17% used" is a sampling artifact and the fleet is not broadly over-reserved. Two structural exclusions before you change any value: a CPU request is the container's **CFS weight under contention**, and **talos-3 is the contended node** (85.3% busy at 14d peak on the *lowest* request commitment of the three - contention that request accounting cannot see), so uncontended-node reasoning does not transfer there; and cutting a request, or raising a limit, on a `requests == limits` container **silently demotes it from Guaranteed to Burstable** - which several workloads here hold deliberately as eviction/OOMController protection and say so in place (`kubernetes/components/dragonfly/cluster.yaml`, k8tz, rook `mon`/`logcollector`). Measured curves, the one-off-Job probe method that produced them, and the per-workload verdicts: the declaration comments in `kubernetes/apps/base/system/pvc-writable-check/app/cronjob.yaml` and `kubernetes/apps/base/system-controller/k8tz/app/helmrelease.yaml`; gate `scripts/ci/cpu-throttle-contract-test.py`.
- Storage classes: `ceph-block` (RWO), `ceph-filesystem-rwx` (RWX - use this; group `csi-rwx`), `ceph-filesystem` (RWX - default `csi` group still broken, do not use for new claims), `openebs-hostpath` (local). Snapshot class: `csi-ceph-blockpool`. Each node has 2 NVMe disks dedicated to Ceph OSDs. Live RWX consumers and the retirement rule: `docs/ceph-cluster-changelog.md`, `kubernetes/apps/base/rook-ceph/rook-ceph/cluster/cephfs-rwx-subvolumegroup.yaml`.
- **`rbd du` silently under-reports reclaimed space, so it is the wrong instrument for judging Ceph headroom or for verifying the weekly `fstrim` - use `rbd du --exact`.** Plain `rbd du` counts full 4 MiB objects still marked EXISTS while krbd discards at 64 KiB, so sub-object holes free BlueStore extents without moving the object map; the fast-diff/exact gap also exists on never-trimmed images, so only a same-instrument before/after proves reclaim. A node-local `fstrim` reaches mounted claims only, and `mountOptions: [discard]` on `ceph-block` was evaluated and deliberately left out. Evidence, numbers, and reasoning: the 2026-09-06 entry in `docs/ceph-cluster-changelog.md`.
- `.private/` directory for local-only files (gitignored)
- Renovate ignores `**/*.sops.*` and `**/resources/**` paths
- **CephX key rotation is GitOps-only, via `cephClusterSpec.security.cephx` in `kubernetes/apps/base/rook-ceph/rook-ceph/cluster/helmrelease.yaml`** - never `ceph auth` by hand. Two traps: (1) **never** move `security.cephx.csi` to `keyType: aes256k` - the `csi-*` keys are used by the krbd / CephFS *kernel* clients, which need Linux >= 7.0, and Talos ships 6.18.x, so it would break every RBD map and CephFS mount; only `daemon` is safe to rotate. (2) `keyGeneration` is CRD-validated `self >= oldSelf` and cannot be reused, while `keyType` is *pruned* by any CRD older than Rook 1.20.5 - so applying an `aes256k` rotation on an older Rook silently burns a generation on an `aes` rotation. Read the current generation with `kubectl -n rook-ceph get cephcluster rook-ceph -o jsonpath='{.status.cephx}'`. Rationale, risk, and rollback: the 2026-08-23 entry in `docs/ceph-cluster-changelog.md`.
- **`cephConfig.client.rgw.rgw_sigv4_insecure: "true"` is load-bearing on Ceph v20.2.4 - do not remove it on its own.** v20.2.4 is the CVE-2026-54330 release; its SigV4 fix rejects any request sending a header that is absent from `SignedHeaders`, and it wrongly required `content-type` to be signed. minio-go's streaming signer never signs `content-type`, so every minio-go/restic `PutObject` 403s while GET/HEAD keep working - this took all 33 `*-ceph` VolSync sources down on 2026-08-23 with Ceph still `HEALTH_OK`, so **a green `ceph status` does not clear RGW**. Check writes with the RGW verb/status one-liner (`kubectl -n rook-ceph logs -l app=rook-ceph-rgw -c rgw --since=20m | grep -oE '"(GET|PUT|HEAD) [^"]*" [0-9]{3}'`); the giveaway in the access log is `op=put_obj bucket= status=0 http_status=403` with the user unresolved (`-`), which is an *authentication* failure, not a bucket policy or ACL one. The flag also disables the `x-amz-*` check that is the real CVE mitigation, so it is strictly temporary: clearing it requires bumping `cephImage.tag` to a Tentacle release carrying ceph/ceph#71192 in the same change. Full mechanism, evidence, risk, and the removal trigger: the 2026-08-23 `rgw_sigv4_insecure` entry in `docs/ceph-cluster-changelog.md`.
- Ceph metadata CronJob (`rook-ceph-backup` / `ceph-backup-pvc`) is in-cluster `openebs-hostpath`, **not** last-resort DR; complete cluster/host wipe destroys it. Emergency steps: `kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md`.
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

- **An empty `resources.limits: {}` override in a HelmRelease's `values` does NOT clear a chart's own default limit - it merges as a no-op, so the chart default silently applies.** `rook-ceph-operator` carried `limits: {}` (intending "no limit") while the chart's own `values.yaml` sets `resources.limits.memory: 512Mi`; the operator was OOMKilled by that unnoticed default 2026-09-20T11:45:13Z mid a six-OSD reconcile (PR #1736), 40 minutes into which Prometheus showed it at 455Mi one scrape before the kill. Fixed with an explicit `limits.memory: 1Gi`, measurement and rationale in the comment at `kubernetes/apps/base/rook-ceph/rook-ceph/operator/helmrelease.yaml`. Before trusting any `limits: {}` (or other empty-map override) to mean "unset", check the chart's own default for that key.
- **Before any node reboot** (`just talos upgrade-node` / `reboot-node` / `reset-node`, or an operator-driven OSD update), which restarts that node's Ceph OSDs: confirm `ceph status` is `HEALTH_OK` and run `task rook:check-osd-device-paths` first. Rook bug [#17224](https://github.com/rook/rook/issues/17224) bakes unstable `/dev/nvmeXn1` names into OSD deployments; on reboot an OSD relies on a relocate fallback that can fail if the cluster is already degraded. Reboot one node at a time, waiting for `HEALTH_OK` between nodes. `cephClusterSpec.storage.osdMaxUpdatesInParallel` is pinned to `1` (CRD default is 20, ≈ all 6 OSDs at once) so operator-driven updates also roll one OSD at a time. If an OSD is stuck `Init` afterward, see `docs/ceph/osd-device-path-recovery.md`. Never restart all OSDs at once.
- **`just talos shutdown-node` hard-codes `talosctl shutdown --force`, which explicitly skips cordon/drain - do not use it for planned electrical/power work.** A hard, undrained power cut to all 3 nodes (2026-09-14) cost 115min of Prometheus data and killed ~23 kopiur movers mid-run (orphaned credential Secrets, a false `KopiurProjectedCredentialsLeaking` page); the same night's plain `talosctl shutdown` (no `--force`) drain lost nothing. Full ordered pre-power-work shutdown/power-on runbook, including the confirmed CNPG cordon-reactive switchover mechanism (confirmed to work but cannot quiesce the last node, which has nowhere to switchover to): `docs/hardware-incidents.md` [2026-09-14].
- **A talos-3 reboot additionally risks losing the Arc Pro B70.** Its OCuLink dock has its own PSU that a host powercycle does not touch (`SLTCAP PowerController=0`), and the root port cannot rediscover a card that trains late (`HotPlugCapable=0`). `talos/schematic.yaml.j2` carries `pcie_port_pm=off` to close that runtime-PM race, but it is not retroactive and does not replace the dock-PSU-first power-on order. Baseline capture, the attended `upgrade-node` runbook, and the verification commands: `docs/hardware-incidents.md` [2026-08-24].
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
