# BOOTSTRAP

## OVERVIEW

Cluster genesis (and disaster recovery) for the Talos + Kubernetes cluster,
onedr0p-style: a `just` module (`bootstrap/mod.just`, wired via `mod bootstrap`
in the root `.justfile`) that stages Talos install → etcd bootstrap → kubeconfig →
base secrets + CRDs → core Helm apps, after which Flux takes over.

> ⚠️ **DR / first-time only.** The `base` and `apps` stages `kubectl apply` /
> `helmfile sync` against the cluster. **Never run a full bootstrap against a
> healthy running cluster** — validate offline only (`helmfile template`,
> `kustomize build`, `vals` resolution).

## STRUCTURE

```
bootstrap/
├── mod.just                              # `just bootstrap` recipe module (stages)
├── helmfile/
│   ├── apps.yaml                         # core apps: cilium → coredns → spegel → cert-manager → flux-operator → flux-instance
│   ├── crds.yaml                         # CRD extraction (--include-crds --no-hooks); filtered in the recipe
│   ├── default.yaml                      # DRY base: derives chart URL + version from kubernetes/apps OCIRepository files
│   └── templates/
│       ├── release.yaml.gotmpl           # reads spec.url + spec.ref.tag from ocirepository.yaml
│       └── values.yaml.gotmpl            # reads spec.values from helmrelease.yaml
└── kustomize/
    ├── apps/
    │   ├── kustomization.yaml
    │   ├── security/                     # onepassword-secret (1password-credentials.json + token)
    │   │   ├── kustomization.yaml
    │   │   └── secret.yaml
    │   └── flux-system/                  # sops-age-secret (age.agekey)
    │       ├── kustomization.yaml
    │       └── secret.yaml
    └── components/
        └── namespace/                    # reusable namespace creation component
            ├── kustomization.yaml
            └── namespace.yaml
```

## WORKFLOW

```bash
# Full bootstrap (DR / first setup)
just bootstrap cluster
# Individual stages (all private — invoke via just bootstrap <stage>)
just bootstrap nodes      # apply Talos config to all nodes (insecure/maintenance)
just bootstrap k8s        # talosctl bootstrap etcd
just bootstrap base       # kustomize secrets + helmfile CRDs
just bootstrap apps       # helmfile sync core apps

# Offline validation (safe, no cluster needed)
helmfile -f bootstrap/helmfile/apps.yaml template --dry-run
helmfile -f bootstrap/helmfile/crds.yaml template -q | yq ea 'select(.kind=="CustomResourceDefinition")'
kustomize build bootstrap/kustomize/apps          # structure only
kustomize build bootstrap/kustomize/apps | vals eval -f -   # needs op signin
```

## HOW THE DRY HELMFILE WORKS

`default.yaml` + `release.yaml.gotmpl` derive chart URL and version from the
OCIRepository manifest that Flux already manages in `kubernetes/apps/`. No version
duplication — Renovate bumps the OCIRepository tag and helmfile picks it up automatically.

Example: `cilium` in `kube-system` → reads `kubernetes/apps/kube-system/cilium/app/ocirepository.yaml`.

> `grafana-operator` has no OCIRepository in `kubernetes/apps/` so its `crds.yaml`
> entry keeps explicit `chart:` / `version:` fields.

## SECRETS

Bootstrap breaks the chicken-and-egg with the **minimum** secrets needed before
External Secrets + Flux take over, injected from 1Password `Home-Lab` by `vals`:

| Secret | namespace | ref | Consumer |
|---|---|---|---|
| `onepassword-secret` `1password-credentials.json` + `token` | `security` | `ref+op://Home-Lab/1password/{OP_CREDENTIALS_JSON,OP_CONNECT_TOKEN}` | onepassword-connect / ESO |
| `sops-age-secret` `age.agekey` | `flux-system` | `ref+op://Home-Lab/sops/SOPS_PRIVATE_KEY` | Flux SOPS decryption |

Secrets use `data:` (base64-encoded values stored in 1Password). Everything else
is seeded post-bootstrap by ESO and must NOT be added here.

## ANTI-PATTERNS

- **NEVER** run `just bootstrap cluster` / `apps` against a healthy cluster.
- **NEVER** put plaintext secrets in `kustomize/apps/*/secret.yaml` — use `ref+op://Home-Lab/*`.
- **NEVER** add a helm `postRenderer: bash` (breaks on Helm 4) — filter CRDs in the recipe with `yq`.
- Keep `helmfile/apps.yaml` release names + namespaces in sync with `kubernetes/apps/` paths.

## NOTES

- `mod.just` evaluates `controller`/`nodes` from `talosctl config info` at load, so a valid
  `TALOSCONFIG` must exist for `just -l bootstrap`.
- The `base` stage depends on `ready` (waits for nodes to be not-Ready before proceeding).
