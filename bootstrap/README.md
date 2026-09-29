# Bootstrap

Cluster genesis and disaster recovery. `bootstrap/mod.just` (root `.justfile` module `bootstrap`) stages Talos install, etcd bootstrap, kubeconfig, base secrets and CRDs, then the core Helm apps. Flux takes over after that.

Never run `just bootstrap cluster` or `just bootstrap apps` against a healthy cluster. Those stages `kubectl apply` and `helmfile sync`. Offline checks are `helmfile template`, `kustomize build`, and `vals` resolution.

Agent procedures for Talos itself are skill `talos-nodes`.

## Layout

```
bootstrap/
├── mod.just
├── helmfile/
│   ├── apps.yaml          # cilium, coredns, spegel, cert-manager, flux-operator, flux-instance
│   ├── crds.yaml          # CRD extraction, filtered in the recipe
│   ├── default.yaml       # chart URL and version come from kubernetes/apps/base OCIRepository files
│   └── templates/
└── kustomize/
    ├── apps/security/     # onepassword-secret only
    └── components/namespace/
```

## Commands

```bash
just bootstrap cluster     # DR / first setup only
just bootstrap nodes       # Talos config, insecure/maintenance
just bootstrap k8s         # talosctl bootstrap etcd
just bootstrap base        # kustomize secrets + helmfile CRDs
just bootstrap apps        # helmfile sync core apps

helmfile -f bootstrap/helmfile/apps.yaml template --dry-run
kustomize build bootstrap/kustomize/apps | vals eval -f -    # needs op signin
```

`default.yaml` reads `spec.url` and `spec.ref.tag` from the Flux OCIRepository. Do not pin `chart:` / `version:` here. Do not re-add a grafana-operator release or `GrafanaDashboard` CRs (skill `observability`, `references/grafana.md`).

## Secrets

The only pre-ESO secret is `onepassword-secret` in `security` (`1password-credentials.json` and `token`), from `ref+op://Home-Lab/1password/...`. It is annotated `kustomize.toolkit.fluxcd.io/prune: disabled` because ESO cannot recreate it. Do not add other secrets here.

## After Flux

A rebuild after Ceph data loss restores no volume by itself. Land the DR-mode commit before bootstrapping and follow `.agents/skills/kopiur-backups/references/full-cluster-restore-r2.md`.

`mod.just` runs `talosctl config info` at load, so `just -l bootstrap` needs a `TALOSCONFIG`. The `base` stage depends on `ready`.
