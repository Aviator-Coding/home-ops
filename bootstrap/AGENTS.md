# BOOTSTRAP

## OVERVIEW

Cluster genesis (and disaster recovery) for the Talos + Kubernetes cluster,
onedr0p-style: a `just` module (`bootstrap/mod.just`, wired via `mod bootstrap`
in the root `.justfile`) that stages Talos install → etcd bootstrap → kubeconfig →
namespaces → bootstrap secrets → CRDs → core Helm apps, after which Flux takes over.

> ⚠️ **DR / first-time only.** The `resources`, `crds`, and `apps` stages
> `kubectl apply` / `helmfile sync` against the cluster. **Never run a full
> bootstrap against a healthy running cluster** — validate offline only
> (`helmfile template`, `kustomize build`, `vals` resolution).

## STRUCTURE

```
bootstrap/
├── mod.just                       # `just bootstrap` recipe module (stages)
├── resources.yaml.j2              # bootstrap-only Secrets, ref+op://Home-Lab/* via vals
├── helmfile.d/
│   ├── 00-crds.yaml               # CRD extraction (--include-crds --no-hooks); filtered in the recipe
│   ├── 01-apps.yaml               # core apps: cilium, coredns, spegel, cert-manager, flux-operator, flux-instance
│   └── templates/values.yaml.gotmpl  # reads .spec.values from each app's kubernetes/apps/.../helmrelease.yaml
```

## WORKFLOW

```bash
# Full bootstrap (DR / first setup)
just bootstrap
# Individual stages
just bootstrap talos      # just --yes talos apply-node talos-{1,2,3} --insecure
just bootstrap k8s        # talosctl bootstrap
just bootstrap resources  # apply 1Password-injected bootstrap secrets
just bootstrap crds       # helmfile template | yq 'select(CRD)' | kubectl apply
just bootstrap apps       # helmfile sync

# Offline validation (safe)
just -l bootstrap
helmfile -f bootstrap/helmfile.d/01-apps.yaml template
helmfile -f bootstrap/helmfile.d/00-crds.yaml template -q | yq ea 'select(.kind=="CustomResourceDefinition")'
just template bootstrap/resources.yaml.j2   # minijinja | vals (resolves ref+op)
```

## SECRETS

Bootstrap breaks the chicken-and-egg with the **minimum** secrets needed before
External Secrets + Flux take over, injected from 1Password `Home-Lab` by `vals`:

| Secret (`data`, base64, byte-exact copy of live) | ref | Consumer |
|---|---|---|
| `onepassword-secret` (ns `security`) `1password-credentials.json`, `token` | `ref+op://Home-Lab/1password/{OP_CREDENTIALS_JSON,OP_CONNECT_TOKEN}` | onepassword-connect / ESO |
| `sops-age-secret` (ns `flux-system`) `age.agekey` | `ref+op://Home-Lab/sops/SOPS_PRIVATE_KEY` | Flux SOPS decryption |

Everything else (e.g. cloudflare tunnel) is seeded **post-bootstrap** by ESO and
must NOT be added here.

## ANTI-PATTERNS

- **NEVER** run `just bootstrap` / `apps` against a healthy cluster — it `helmfile sync`s core components.
- **NEVER** put plaintext secrets in `resources.yaml.j2` — use `ref+op://Home-Lab/*`.
- **NEVER** add a helm `postRenderer: bash` (breaks on Helm 4) — filter CRDs in the recipe with `yq`.
- Keep `helmfile.d/*` chart versions in sync with the Flux-managed `kubernetes/apps/*` OCIRepository tags.

## NOTES

- Depends on `talos/mod.just`: the `talos` stage calls `just --yes talos apply-node` for each node.
- `mod.just` evaluates `controller`/`nodes` from `talosctl config info` at load, so a valid `TALOSCONFIG` must exist for `just -l bootstrap`.
