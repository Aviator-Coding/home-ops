# home-ops AI review rules

Review standards for the automated reviewer in `.github/workflows/ai-pr-review.yaml`.

**Why this file exists instead of `AGENTS.md`.** The reviewer inlines its standards file whole
and then hard-truncates it to the first 16000 bytes. `AGENTS.md` is ~70kB, so handing it over
would silently deliver an arbitrary head-slice, cut mid-sentence, that drops the entire `NOTES`
section - where most of this repo's load-bearing review knowledge lives. This file is the
review-relevant distillation, kept under that cap on purpose. `AGENTS.md` remains authoritative
for humans and for agents doing the work; if the two disagree, `AGENTS.md` wins.

This repo is a GitOps home cluster: Talos Linux + Flux v2 + Cilium + Rook-Ceph + External Secrets
(1Password) + Gateway API. Almost every file is a Kubernetes manifest that Flux applies to a live
3-node cluster with no staging environment. Review accordingly: a merged mistake is a production
mistake.

## How to review

Prefer a small number of specific, actionable findings over broad commentary. Anchor each finding
to a file and, where possible, quote the offending line. Say plainly when a diff looks fine -
do not invent problems to fill space. You are advisory: you cannot block a merge, so flag risk
clearly rather than hedging.

Renovate opens a large share of this repo's pull requests and they otherwise get no human review.
Treat them as first-class: check that the bump is in scope for its constraint, that it does not
cross a pin documented below, and that a version-pinning comment and the value it guards still
agree. A pure digest or patch bump with nothing else in the diff is normally fine - say so briefly.

## Hard rules - flag any violation as a blocker

- **No plaintext secrets in Git, ever.** App secrets are `ExternalSecret` + 1Password only
  (`ClusterSecretStore` `onepassword`). Bootstrap and Talos secrets are `ref+op://Home-Lab/...`
  resolved by `vals`. A literal token, password, API key, kubeconfig or private key in a diff is a
  blocker even in an example or a comment. There is no SOPS/age path in this repo - do not accept
  one being reintroduced, and do not accept `talos/clusterconfig/` or `talos/talconfig.yaml`.
- **Never a `postRenderer` running `bash`** in a `HelmRelease`. It breaks on Helm 4.
- **Do not store secrets under `**/resources/**`** - Renovate ignores that path.
- **`tofu apply`/`tofu destroy` in `terraform/authentik/` is never a follow-on to a green PR.**
  That stack is the cluster's live SSO. A PR may change it; applying it is a separate, explicitly
  approved operator action. Flag any workflow, script or doc change that would apply it
  automatically.
- **Do not enable "Allow GitHub Actions to create and approve pull requests"**, and do not add a
  workflow that approves PRs.

## Flux and Kustomize

- Every manifest starts with a `# yaml-language-server: $schema=...` comment. Prefer
  `kubernetes-schemas.pages.dev`; the only accepted fallback is `k8s-schemas.home-operations.com`.
  Never `crd.movishell.pl` or `fluxcd-community`, and never a URL that 404s.
- Layout: resources at `kubernetes/apps/base/<ns>/<app>/`, with a Flux `Kustomization` CR overlay at
  `kubernetes/apps/main/<ns>/<app>.yaml`. A new app must also be listed in
  `kubernetes/apps/main/<ns>/kustomization.yaml`. `kubernetes/clusters/main/` is the only Flux entry
  point.
- Naming is all lowercase kebab-case: `helmrelease.yaml`, `kustomization.yaml`, `externalsecret.yaml`,
  overlay `<app>.yaml`. Anchors are `name: &app myapp` / `namespace: &namespace myns`, referenced as
  `*app` / `*namespace`.
- **`postBuild.substitute` collisions are the highest-severity Flux bug in this repo.** Flux runs
  strict envsubst over the *entire* built output, so any literal `${...}` token that is not a Flux
  variable fails the **whole Kustomization**, not just that resource. Grafana dashboard variables
  (`${datasource}`, `${__url_time_range}`) and an app's own unresolved vars are the usual sources.
  This froze four apps for 44 days. CI does **not** catch it - `flate` substitutes leniently while
  the cluster runs strict mode, so a PR can be green and still freeze Flux. If a diff adds a literal
  `${...}`, flag it and name the fix: annotate the resource
  `kustomize.toolkit.fluxcd.io/substitute: disabled`, escape it as `$${...}`, or define the variable
  in the overlay's `substitute` map.
- **`healthChecks:` must target the rendered workload** (`Deployment`/`StatefulSet`/`DaemonSet`/
  `CronJob`, or a CR whose status is the real signal) - never the `HelmRelease`, whose `Ready` stays
  `True` straight through a pod crashloop. `wait:` must be `false` or omitted, because `wait: true`
  makes Flux ignore `spec.healthChecks` entirely. `healthCheckExprs` only runs against objects
  already in the assessment set.
- VolSync: **one component include covers exactly one volume.** A second PVC needs its own Flux
  Kustomization with `path: ./kubernetes/components/volsync/backup` and `APP` set to the claim name.
  `VOLSYNC_CACHE_CAPACITY` must be 20-50% of the PVC size (50-100% for small PVCs).
- Never patch an app's `<app>-dst` `ReplicationDestination` to trigger a restore - it carries
  `ssa: IfNotPresent`, so a manual edit persists forever and silently drifts from Git. Create a
  uniquely-named scratch `ReplicationDestination` instead.

## Networking

- **A `CiliumClusterwideNetworkPolicy` with a `nodeSelector` and an `ingress:` or `ingressDeny:`
  section must also set `enableDefaultDeny: {ingress: false}`.** Otherwise it puts the *host
  endpoint* into ingress default-deny and drops the Talos API, Kubernetes API, kubelet, etcd and BGP.
  On 3 Talos nodes with no SSH that is a physical-console recovery. Default-deny is OR-ed across
  policies, so one careless policy re-arms it for every host policy at once. Treat a missing
  `enableDefaultDeny` here as a blocker.
- A CCNP `nodeSelector` must use a label that survives into the **host endpoint's** label set, which
  is not the node's. Cilium strips `kubernetes.io/os` and `kubernetes.io/hostname`, so those selectors
  fail silently and open.
- Gateway API only: `envoy-internal` (private) and `envoy-external` (public) in `network`. Check the
  `external-dns.alpha.kubernetes.io/target` annotation matches the gateway actually used - an
  internal-only service on the external listener is a disclosure bug. LiteLLM in particular is
  internal-only and must never be routed on the public listener.
- Gatus monitoring comes from the `gatus.home-operations.com/endpoint` annotation on an HTTPRoute, or
  a hand-written entry in the Gatus config. The `gatus.io/enabled` ConfigMap-label pattern is dead and
  must not be copied.

## GitHub Actions

- **Pin every action to a full commit SHA with a trailing `# vX.Y.Z` comment.** A floating tag is a
  finding, and more so on any job holding write permissions.
- `permissions:` must be the narrowest set the job needs; flag any widening, especially
  `contents: write` or `pull-requests: write` appearing where it was not needed before.
- **This repo is public, and jobs on `gha-runner-scale-set-aviator-coding-home-ops` run inside the
  cluster.** Every `pull_request`-reachable job on that runner must carry
  `if: ${{ github.event.pull_request.head.repo.full_name == github.repository }}`. A missing fork
  guard on an in-cluster job is a blocker. Do not add a `pull_request_target` trigger.
- The in-cluster runner has no Docker daemon and no Kubernetes API access (`automountServiceAccountToken:
  false`, no ServiceAccount). Anything needing `docker run`, a `docker://` action, or `kubectl` will
  fail there.
- `.github/workflows/validate.yaml` is the only CI signal for `talos/`, `bootstrap/` and `.renovate/`.

## Talos, Renovate and pins

- `talos/*.j2` is **not** applied by Flux. Machine-config changes need `just talos apply-node`;
  `schematic.yaml.j2` (kernel args, extensions) needs `just talos upgrade-node`. A PR touching these
  is a proposal, not a deployment - flag any claim that merging applies it.
- `talos/machineconfig.yaml.j2` carries six version pins that can drift from the live cluster, which
  tuppr actually drives. Applying a stale template can downgrade a running node.
- The Talos `allowedVersions: "<1.13.3"` pin in `.renovate/overrides.json5` gates a live, unattended
  upgrade: `tuppr`'s `TalosUpgrade` CR is Flux-applied with `rebootMode: powercycle`. Raising that
  ceiling is not a routine dependency bump.
- `quay.io/ceph/ceph` is constrained to stable `x.2.z` tags because the registry also publishes RCs.
  Do not apply that regex to `ghcr.io/rook/ceph`.
- Merging a change to `.renovaterc.json5` or `.renovate/**` fires the Renovate workflow's `push`
  trigger immediately, alongside the in-cluster writer.

## Storage and GPU

- Never restart all Ceph OSDs at once, and never ship a change that would. Node reboots go one at a
  time, `HEALTH_OK` between them.
- `security.cephx.csi` must never move to `keyType: aes256k` - the CSI keys are used by kernel clients
  that need Linux >= 7.0 and Talos ships 6.18.x. Only `daemon` is safe to rotate. `keyGeneration` is
  monotonic and cannot be reused.
- `cephConfig.client.rgw.rgw_sigv4_insecure: "true"` is load-bearing on Ceph v20.2.4 and must not be
  removed on its own - only together with a Ceph image bump carrying the upstream fix.
- **Never rename a DRM device node via `generic-device-plugin`'s `mountPath`.** libdrm re-opens the
  canonical `DEVNAME` from sysfs, so a renamed node kills VA-API while Level Zero workloads stay
  green - a 3-day silent transcoding outage. VA-API consumers use `devic.es/b70-vaapi`. Adding a host
  path to an existing device group changes every device ID in it and invalidates live kubelet
  allocations - add a new group instead.
- Do not migrate GPU scheduling to DRA, and never via `adminAccess: true`.

## AI stack (`kubernetes/apps/base/ai/`)

- **Registering a `LiteLLMModel` grants nothing.** Entitlement is a separate `LiteLLMVirtualKey`
  `models` allow-list. But a **config-declared fallback bypasses that allow-list**, so adding an alias
  to `routerSettings.fallbacks` or `context_window_fallbacks` is a cloud entitlement no matter what
  the keys say. Flag any fallback added to a zero-priced local alias, and any unattended consumer
  (cron, CI) whose key gains a cloud-backed model.
- A model id is only valid on the route it came from - Anthropic spells it `claude-opus-4-8` where
  OpenRouter spells it `anthropic/claude-opus-4.8`. `apiKey: os.environ/<VAR>` is load-bearing at
  proxy startup: removing a key while a model still names it downs the proxy for everyone.
- Never name a hand-written `HTTPRoute` after the `LiteLLMProxy` CR - the operator deletes it as an
  orphan on a later reconcile.
- kagent and kmcp are not deployed. ToolHive `MCPServer` is `toolhive.stacklok.dev/v1alpha1`.

## Commits

`type(scope): description` with type in feat, fix, chore, ci, docs, refactor, test. Scope is
`container`, `helm`, `github-action`, `mise`, `talos`, `flux`, `deps`, `github-release`, or an
app/namespace name. Never add an agent as commit co-author.
