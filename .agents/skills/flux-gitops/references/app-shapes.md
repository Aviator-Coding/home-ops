# App directory shapes

Two tiers. `kubernetes/apps/base/<ns>/<app>/` holds the resources.
`kubernetes/apps/main/<ns>/<app>.yaml` holds the Flux `Kustomization` CRs
that point `spec.path` at that base and are what Flux reconciles.
`kubernetes/apps/main/<ns>/kustomization.yaml` lists each overlay file.
A new top-level namespace is added only on
`kubernetes/apps/main/kustomization.yaml`.

`scripts/add-app/generate-app.sh` emits the skeleton. It does not wire
kopiur or VolSync. Backup onboarding is skill `kopiur-backups`.

## Default: `app/`

One workload, one HelmRelease:

```
kubernetes/apps/base/<ns>/<app>/app/
├── kustomization.yaml
├── helmrelease.yaml
└── externalsecret.yaml    # only when the app has secrets
```

Overlay `spec.path: ./kubernetes/apps/base/<ns>/<app>/app`. Keep the `app/`
wrapper even for a single file set so a later sibling does not rename a live
Flux path. Files that belong to the same deployable go in subdirectories
(`app/resources/`, `app/models/`), not a new shape.

## When to leave the default

1. **CRD split.** Chart CRDs must exist before the controller starts. Add
   `<app>/crds/` and a second overlay Kustomization. The workload
   `dependsOn` the CRDs Kustomization. The CRDs one may set `wait: true`
   with a short timeout so registration finishes first. Do not split CRDs
   for tidiness. Worked example: `kubernetes/apps/main/ai/agentgateway.yaml`.
2. **Family.** Several independently deployable pieces under one product
   (`operator/`, `cluster/`, `exporter/`). Each piece gets its own overlay
   Kustomization and `dependsOn` where one piece must precede another.
   Example: `database/cloudnative-pg/{operator,dashboard,pgadmin,cluster-17}`.
3. **Parameterized instance.** The same chart, many named instances, nested
   under `app/<key>/<instance>/`. The only live case is
   `actions-runner-system/gha-runner-scale-set/app/aviator-coding/{home-ops,ai-k8s-sandbox}`.
   Different config on one chart is Helm `values:`, not this shape.

## Authoring traps

- **Component path depth follows the base path, not the overlay file.**
  From `<ns>/<app>/app/` the include is `../../../../../components/<name>`
  (five `../`). One level deeper needs one more. `flate` fails with
  `not a valid directory` when the count is wrong. Copy a sibling at the
  same depth (`security/authentik`, `selfhosted/rsshub`).
- **One `components/kopiur` or `components/volsync` include is one volume.**
  Objects are named from `${APP}`, and a Kustomization has one
  `postBuild.substitute` map. A second PVC is a second Kustomization.
  VolSync is not added to new apps. Skill `kopiur-backups`.
- **`components/kopiur` plus `wait: true` never becomes Ready.** The
  standing Restore stays `AwaitingPvcDataSourceRef` until a claim uses it.
  Leave `wait` false and health-check the workload.
- **Do not declare a `postBuild.substitute` key nothing reads.** Only
  `KOPIUR_PUID`/`PGID` and `VOLSYNC_PUID`/`PGID` drive mover identity.
  A dead `APP_UID` pair looks like documentation and is not.
- **Literal `${...}` that is not a Flux variable fails the whole
  Kustomization.** Skill `flux-substitution`.
- **Health checks and `wait: false`.** See the skill tripwires. Worked
  CronJob check: `kubernetes/apps/main/renovate/renovate.yaml`.
- **A pod-options key the chart does not read is discarded with no flate
  error.** Skill `app-workloads`.
- **Schema URL.** `kubernetes-schemas.pages.dev`. If that host does not
  serve the kind, use `k8s-schemas.home-operations.com`. No third host.
- **Secrets.** ExternalSecret plus 1Password. Nothing under `resources/`
  (Renovate ignores that path). No SOPS. Skill `secrets-1password`.
- **Namespace object.** Overlays include `components/common`. The shared
  Namespace is `name: not-used` and the overlay renames it. Per-namespace
  labels are a patch on that same object. PodSecurity on it is warn/audit,
  never enforce.

## Anchors

```yaml
name: &app myapp
namespace: &namespace myns
```

The overlay references `*app` and `*namespace`. `commonMetadata.labels`
rewrites labels on resources built from the Kustomization path. It does
not rewrite a HelmRelease's chart output. A ServiceMonitor that selects
`app.kubernetes.io/name` against something other than `*app` goes dark.
Skill `observability`.
