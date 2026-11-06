# In-cluster Renovate

mogenius `renovate-operator` (chart `oci://ghcr.io/mogenius/helm-charts/renovate-operator`),
the same shape as bjw-s, joryirving and ishioni. Replaced the official-chart
CronJob on 2026-11-05. One `RenovateJob` (`home-ops`, hourly, UTC) discovers
`skrumbach/home-ops` and runs it.

| Path | What |
|---|---|
| `renovate-operator/app/` | operator HelmRelease, internal UI route, ServiceMonitor, dashboard, alerts |
| `renovate-operator/jobs/` | `RenovateJob` + GitHub App ExternalSecret |
| `jobs/renovatejob.yaml` `renovateConfig.inline` | global self-hosted config (`requireConfig`, `platformCommit`, `binarySource`) |

## GitHub App

The operator mints the installation token itself (`githubAppReference`); it
refreshes it before the 1h expiry. The App is `mortyops` (ID in 1Password),
installation `78901211` on `skrumbach`. The App needs access to
`skrumbach/home-ops`; the base preset `home-operations/renovate-presets` is public.

| Vault | Item | Field |
|---|---|---|
| Homelab | `renovate` | `BOT_APP_ID` |
| Homelab | `renovate` | `BOT_APP_PRIVATE_KEY` |

`BOT_APP_PRIVATE_KEY` is a headerless one-line PKCS#1 body. The operator only
parses a PEM (`-----BEGIN` prefix), so the ExternalSecret template wraps it. The
secret carries `renovate-operator.mogenius.com/allow-ref: "true"`, which the
chart's policy engine requires once enabled.

## Run now

- UI: `renovate.${SECRET_DOMAIN}` (internal only, Authentik OIDC; `authorization` off, so
  every Authentik user is admin). Client: `terraform/authentik/renovate-operator.tofu`;
  secrets `Homelab/renovate-operator-oidc` (`client-secret`, `session-secret`).
- CLI: `kubectl -n renovate annotate renovatejob home-ops renovate-operator.mogenius.com/schedule-all=true`
  (admin kubeconfig). The operator picks it up within a minute.

## Alerts

`renovate-operator/app/prometheusrule.yaml`, operator metrics:

| Alert | Meaning |
|---|---|
| `RenovateRunFailed` | `renovate_operator_run_failed` for 2h (two hourly runs) |
| `RenovateRunMissing` | no `completed` execution in 8h, operator reporting for 8h |
| `RenovateDiscoveryEmpty` | discovery saw no repo for 2h (App access or token) |
| `RenovateJobSuspended` | `spec.suspend` true for 12h |
| `RenovateOperatorAbsent` | no series for the `home-ops` job for 1h |

## Rollback to GitHub Actions

1. Set `spec.suspend: true` on `jobs/renovatejob.yaml` and merge. Do not suspend
   from the UI (`authorization.suspendFromUI` is off; Flux would undo it).
2. Dispatch, or add a `schedule` to, `.github/workflows/renovate.yaml`.
   Never run both writers at once: they race on the same branches.

## Self-update

The path exclusion `kubernetes/apps/base/renovate/**` with `automerge: false`
keeps both the operator chart and the runner image (`RenovateJob.spec.image`,
kubernetes manager) as reviewed PRs. A package-name match would go stale on a
rename. The rule must stay after the blanket automerge rules.

Docker Hub credentials are not configured. Lookups are anonymous. Re-measure
if the `docker.io` image count grows several-fold. The fix then is
`RENOVATE_HOST_RULES` in `extraEnv` plus a credential on the `renovate` item,
not a new writer.

## Config merge

Merge, then let the next hourly run pick it up or trigger one (Run now). The
workflow has no `push` trigger, so nothing else writes.
