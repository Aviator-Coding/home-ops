# In-cluster Renovate

CronJob every 4 hours. Community operators are not used. The GitHub App must
keep access to `Aviator-Coding/home-ops` and `Aviator-Coding/mortyops` (preset
`extends`). The CronJob mints a one-hour installation token
(`app/resources/github-app-token.js`). The script accepts PEM, escaped-newline
PEM, or a headerless PKCS#1/PKCS#8 body.

| Vault | Item | Field |
|---|---|---|
| Homelab | `renovate` | `BOT_APP_ID` |
| Homelab | `renovate` | `BOT_APP_PRIVATE_KEY` |

Same App the hosted workflow uses via `actions/create-github-app-token`.

## Alerts

No ServiceMonitor. `app/prometheusrule.yaml` watches kube-state-metrics:

| Alert | Meaning |
|---|---|
| `RenovateJobFailed` | last schedule did not succeed within 40m |
| `RenovateRunMissing` | no success in 8h after at least one prior success |
| `RenovateNeverSucceeded` | CronJob exists, never succeeded (8h). First-deploy / missing-secret tripwire |
| `RenovateCronJobAbsent` | CronJob missing for 1h |

`RenovateRunMissing` does not apply before the first success.

## Rollback to GitHub Actions

1. Uncomment the `schedule` block in `.github/workflows/renovate.yaml` and merge.
2. Optionally set `env.RENOVATE_DRY_RUN: full` on the HelmRelease if the
   in-cluster path should stop writing. Do not set that env to `"false"`.

## Self-update

Renovate tracks its own chart as an ordinary dependency. The path exclusion
`kubernetes/apps/base/renovate/**` with `automerge: false` is what keeps a
chart bump as a reviewed PR. A package-name match would go stale on a rename.
The rule must stay after the blanket automerge rules.

Docker Hub credentials are not configured. Lookups are anonymous. Re-measure
if the `docker.io` image count grows several-fold. The fix then is
`RENOVATE_HOST_RULES` plus a credential on the `renovate` item, not a new
writer.

## Config merge

1. Disable or suspend the `Renovate` GitHub Actions workflow.
2. Merge.
3. Let the CronJob run, or start one job from it.
4. Re-enable the workflow (the `push` trigger is the overlap; the schedule stays commented).
