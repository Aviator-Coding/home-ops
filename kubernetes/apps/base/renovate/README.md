# renovate

In-cluster Renovate via the official helm chart (`oci://ghcr.io/renovatebot/charts/renovate`), a CronJob every 4 hours. This is the live writer. Community operators (mogenius / thegeeklab / secustor) are not used.

`.github/workflows/renovate.yaml` is retained as rollback: its `schedule` trigger is commented out. Re-enable that cron to restore the GitHub Actions path in about two minutes. The workflow's `push` trigger (`.renovaterc.json5` / `.renovate/**.json5`) and `workflow_dispatch` stay in place.

## 1Password item

| Vault | Item | Field | Source |
| --- | --- | --- | --- |
| Homelab | `renovate` | `BOT_APP_ID` | GitHub Actions secret `BOT_APP_ID` |
| Homelab | `renovate` | `BOT_APP_PRIVATE_KEY` | GitHub Actions secret `BOT_APP_PRIVATE_KEY` |

Same GitHub App the hosted workflow uses via `actions/create-github-app-token`. The CronJob mints a one-hour installation token at start (`app/resources/github-app-token.js`). The mint script accepts PEM, escaped-newline PEM, or a headerless PKCS#1/PKCS#8 DER body (1Password password fields often drop BEGIN/END lines). The app must keep access to `Aviator-Coding/home-ops` and `Aviator-Coding/mortyops` (preset `extends`).

`RenovateNeverSucceeded` is the first-deploy / missing-secret tripwire. `RenovateRunMissing` only applies after at least one successful run.

## Rollback

1. Uncomment the `schedule` block in `.github/workflows/renovate.yaml` and merge (two-minute job).
2. Optionally re-add `env.RENOVATE_DRY_RUN: full` on `app/helmrelease.yaml` (do not set it to `"false"`) if the in-cluster path should stop writing.

## Alerts

The official chart is a CronJob with no metrics endpoint and no ServiceMonitor. `app/prometheusrule.yaml` therefore watches kube-state-metrics CronJob timestamps (`kube_cronjob_info`, `kube_cronjob_status_last_schedule_time`, `kube_cronjob_status_last_successful_time`), not a Renovate-native series:

- `RenovateJobFailed` - last schedule did not succeed within 40m
- `RenovateRunMissing` - no successful run in 8h after at least one prior success (two missed cycles)
- `RenovateNeverSucceeded` - CronJob exists but has never succeeded (8h)
- `RenovateCronJobAbsent` - CronJob missing for 1h
