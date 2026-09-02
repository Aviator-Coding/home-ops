# App directory structure

How an app's manifests are laid out under `kubernetes/apps/base/<ns>/<app>/`, and the traps that
bite when authoring a new one. This was measured, not designed: an audit of all 101 HelmReleases
in the repo (2026-09-01) found 99 already following the shapes below, and recommended writing the
convention down rather than adding a CI linter or restructuring the two outliers' neighbors. Full
evidence: `data/homeops-app-structure-conventions-scout/report.md` in the firstmate home (not in
this repo).

This document is about **directory shape** - what lives where. For the substitute-variable and
backup-component conventions layered on top of any shape, see `AGENTS.md`'s CONVENTIONS and
UNIQUE STYLES sections.

## The two-tier layout

Every app has manifests in two places:

- `kubernetes/apps/base/<ns>/<app>/` - the actual Kubernetes/Flux resources (HelmRelease,
  ExternalSecret, Kustomization, ...).
- `kubernetes/apps/main/<ns>/<app>.yaml` - one or more Flux `Kustomization` CRs (the "overlay")
  that point `spec.path` at a base directory and are what Flux actually reconciles. Multiple
  `Kustomization` documents can live in one overlay file, separated by `---` (see the CRD-split
  shape below).
- `kubernetes/apps/main/<ns>/kustomization.yaml` lists every overlay file for that namespace as a
  plain `resources:` entry.

## The default shape: single-component `app/`

82 of 101 apps are this shape, and it is the right default unless you have a concrete reason for
one of the three shapes below:

```
kubernetes/apps/base/<ns>/<app>/app/
├── kustomization.yaml       # yaml-language-server: $schema=https://json.schemastore.org/kustomization
├── helmrelease.yaml         # $schema=.../app-template/schemas/helmrelease-helm-v2.schema.json
└── externalsecret.yaml      # $schema=https://kubernetes-schemas.pages.dev/external-secrets.io/externalsecret_v1.json  (only if the app has secrets)
```

with one overlay `Kustomization` CR at `kubernetes/apps/main/<ns>/<app>.yaml` whose
`spec.path: ./kubernetes/apps/base/<ns>/<app>/app`. See `renovate/app/` for a clean worked
example.

The `app/` wrapper exists even when the app has only one file set, so that a later second
component (CRDs, an operator sidecar, a second instance) has somewhere to go without a rename.
That is exactly the gap the `searxng` and `dragonfly` renames in this repo's history closed - both
had skipped the wrapper and had to be moved into it later, as a live Flux path edit.

## Three shapes for apps that need more than one deployable

An app graduates out of the plain shape only when it genuinely has more than one
independently-deployable piece, or Flux/Helm itself forces a structural split. Don't reach for
these to organize files within a single deployable - that's what subdirectories under `app/`
(e.g. `app/resources/`, `app/backends/`) are for.

### 1. Multi-component family

Several independently-deployable pieces share one parent directory, each in its own named
sibling directory (`app` alongside the others when one piece is the "main" workload):

```
kubernetes/apps/base/<ns>/<app>/
├── app/           # or a named piece, e.g. operator/, cluster/, exporter/
├── <piece-2>/
└── <piece-3>/
```

Each sibling gets its own overlay `Kustomization` CR (separate files, or separate `---`-separated
documents in the same overlay file), and pieces that depend on each other wire it with
`spec.dependsOn`. Examples: `database/cloudnative-pg/{operator,dashboard,pgadmin,cluster-17}`,
`database/emqx/{operator,cluster,exporter}`, `selfhosted/rsshub/{app,playwright}` (see
`kubernetes/apps/main/selfhosted/rsshub.yaml` for the two-Kustomization overlay file and the
`dependsOn: rsshub-playwright` wiring).

Use this when a namespace holds genuinely separate deployables that happen to relate to the same
product, not merely to group files that could live under one `app/`.

### 2. CRD-split

`<app>/app/` (the workload) plus a sibling `<app>/crds/` (a CRDs-only Kustomization, applied
before the main release with `CreateReplace` install/upgrade semantics):

```
kubernetes/apps/base/<ns>/<app>/
├── app/
│   ├── kustomization.yaml
│   └── helmrelease.yaml
└── crds/
    ├── kustomization.yaml
    ├── ocirepository.yaml
    └── helmrelease.yaml
```

Two overlay `Kustomization` CRs, `<app>` depending on `<app>-crds`:
`kubernetes/apps/main/ai/agentgateway.yaml` is the worked example - `agentgateway`'s
`dependsOn: [agentgateway-crds]`, and `agentgateway-crds` itself runs `wait: true` with a short
timeout so the CRDs are actually registered before the app Kustomization applies. Use this only
when a chart's CRDs need to exist before its controller starts, which is a Helm/Flux ordering
requirement, not a style choice - don't split CRDs out "for tidiness" on a chart that doesn't need
it. `ai/toolhive` and `monitoring/prometheus-operator` (CRDs-only, no matching `app/` - its CRDs
are consumed by `kube-prometheus-stack` in a different app directory) are the other live examples.

### 3. Parameterized instance

One chart template, multiple named instances nested *deeper than* `app/`, keyed by what each
instance serves:

```
kubernetes/apps/base/<ns>/<app>/app/<key>/{instance-a,instance-b}/
```

Only one live example: `actions-runner-system/gha-runner-scale-set/app/aviator-coding/{home-ops,ai-k8s-sandbox}`
(two runner scale sets, one per GitHub repo, sharing one chart). Reach for this only when you
have the same chart deployed multiple times with different identity, not different config -
otherwise Helm `values:` overrides within a single HelmRelease are simpler.

## Picking a shape

1. One workload, one HelmRelease → plain `app/`. This is almost always the answer.
2. Chart ships CRDs that must exist before the controller starts → add `crds/` alongside `app/`.
3. Multiple genuinely independent deployables under one product name → family shape, one
   directory per piece.
4. Same chart, multiple named instances of the same kind → parameterized instance, nested under
   `app/`.

## Traps when authoring a new app

These are traps this repo has actually hit, not hypotheticals. The scaffold (below) encodes the
ones it can prevent by construction; the rest need a human or agent to check for them.

- **`postBuild.substitute` literal `${...}` collision.** Flux's strict-mode envsubst runs over the
  *entire* built Kustomization output. Any literal `${...}` token that isn't a Flux substitution
  variable - a Grafana dashboard variable, an app's own var you forgot to add to `substitute:` -
  fails the **whole** Kustomization, not just that resource, and only the first failure per
  Kustomization is reported. Full mechanism and the three fixes (per-resource
  `kustomize.toolkit.fluxcd.io/substitute: disabled`, `$${...}` escaping, or actually defining the
  variable): `AGENTS.md` UNIQUE STYLES, "Flux variable substitution".
- **`healthChecks` must target the workload, not the `HelmRelease`, and `wait` must stay `false`
  (or be omitted).** A `HelmRelease`-kind healthCheck only reflects the last `helm
  install`/`upgrade` action - it reads `Ready` through an ongoing pod crashloop. Point
  `healthChecks` at the rendered `Deployment`/`StatefulSet`/`DaemonSet`/`CronJob` (or a CR whose
  status is the real signal) instead. Flux ignores `spec.healthChecks` entirely when
  `spec.wait: true` and assesses the whole inventory instead, which reopens the same blind spot for
  a helm-app overlay (that inventory is usually just the `HelmRelease`) - so workload
  `healthChecks` need `wait: false`. See `renovate/renovate.yaml`'s `CronJob` healthCheck for a
  worked example.
- **A component include's relative-path depth changes with directory depth.** `components:` paths
  on the overlay `Kustomization` CR (e.g. `../../../../components/dragonfly`,
  `../../../../../components/volsync`) are relative to the **base path** the Kustomization builds,
  not to the overlay file's own location - so a base directory one level deeper (`<ns>/<app>/app/`
  vs. bare `<ns>/<app>/`) needs one more `../`. Measured live in this repo's own
  `searxng` `app/` rename (2026-09-01): moving the base path in without updating the component
  path made `flate` fail with `kustomize build ... : not a valid directory`. Count the segments
  from the base path to `kubernetes/` and match it against a sibling app at the same depth
  (`security/authentik`, `selfhosted/rsshub` both use 5 `../` from an `<ns>/<app>/app/` path).
- **One `components/volsync` or `components/kopiur` include covers exactly one volume.** Every
  object it emits is named from `${APP}`, and Flux allows only one `postBuild.substitute` map per
  Kustomization. An app with a second PVC needs a second Flux `Kustomization` in the overlay yaml
  with its own `APP` substitute value. Pattern: `kubernetes/components/volsync/Readme.md` "Apps
  with more than one volume".
- **A pod-options key the chart doesn't recognize is silently discarded, not rejected.** The
  app-template chart's values schema is permissive enough that a typo'd or wrong-cased
  top-level key (e.g. a securityContext nested under a key the chart doesn't read) produces an
  empty effective `securityContext` with no warning from `flate` and no error from Flux - the pod
  just runs with defaults. This produced a real empty-volume outage (`selfhosted/rsshub-playwright`,
  `AGENTS.md` NOTES). Verify a new app's rendered pod spec (`kustomize build` the base path, or
  check the live pod's `securityContext` post-deploy) rather than trusting that a values key was
  accepted because nothing complained.
- **Don't declare a `postBuild.substitute` key that nothing reads.** A leftover `APP_UID`/`APP_GID`
  pair with no manifest referencing it is dead weight that looks like documentation and isn't -
  found on six apps in a 2026-08-31 audit (`AGENTS.md` NOTES, "declared APP_UID/GID... liability").
  Only `KOPIUR_PUID`/`PGID` and `VOLSYNC_PUID`/`PGID` actually drive a mover's security context;
  anything else you add to `substitute:` should be consumed by a manifest in the same
  Kustomization's build output.
- **A `components/kopiur` include with an implicit `wait: true` can never go `Ready`.** The
  component ships a standing `Restore` in passive populator mode that reports
  `Ready=False`/`AwaitingPvcDataSourceRef` until a rebuilt claim actually claims it - which is
  correct, but it means the owning Kustomization can't use inline `wait: true` (the Flux default)
  or Flux blocks forever on that one object. Split kopiur into its own `wait: false` Kustomization
  pointed at `components/kopiur/backup`, the way `database/cloudnative-pg.yaml` and
  `media/calibre-web-automated.yaml` do, when an overlay would otherwise wait on the whole
  inventory. Full mechanism: `kubernetes/components/kopiur/Readme.md`, trap (4).
- **Don't infer a backup mover's identity from the pod's `runAsUser`/`fsGroup` - measure the files
  it must read.** `KOPIUR_PUID`/`PGID` and `VOLSYNC_PUID`/`PGID` default to `1000`; any file
  without a matching read bit makes kopiur fail closed (VolSync survives the same mismatch only
  because its clone is staged writable, which is not something a new app should rely on). 10 of 30
  measured claims needed a non-default identity that the pod spec alone didn't suggest. Full
  mechanism: `AGENTS.md` NOTES, kopiur trap (0).

## The scaffold

`scripts/add-app/generate-app.sh` generates the base + overlay skeleton for whichever shape you
pick, with the schema headers filled in and the substitute/healthCheck/component-depth traps above
encoded directly into the generated files rather than left as something to remember. See
`scripts/add-app/README.md` for usage; it does not wire up VolSync/kopiur backup blocks itself
(those depend on per-app measurement, not a template) but points at the two components' own
Readmes for that step.

## What this deliberately does not include

No CI linter checks this convention, and no restructure of the 99 conforming apps was done or is
planned. A generic shape check would have to correctly special-case all three shapes above or it
produces false failures on legitimate structures, and the evidence doesn't support the cost: 2
cosmetic deviations across the whole repo's history (both fixed 2026-09-01), no case of an actual
incident traced to directory shape, and neither reference repo (`joryirving/home-ops`,
`mirceanton/home-ops`) runs one either.
