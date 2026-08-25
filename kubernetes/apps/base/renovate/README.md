# renovate

In-cluster Renovate via the official helm chart (`oci://ghcr.io/renovatebot/charts/renovate`), a CronJob every 4 hours matching `.github/workflows/renovate.yaml`. Community operators (mogenius / thegeeklab / secustor) are not used.

This path ships in **observation / dry-run** (`RENOVATE_DRY_RUN=full`). The GitHub Actions workflow stays enabled until a follow-up PR proves the CronJob and flips writes on.

## 1Password item (captain)

The ExternalSecret expects a Homelab vault item that does not exist yet:

| Vault | Item | Field | Source |
| --- | --- | --- | --- |
| Homelab | `renovate` | `BOT_APP_ID` | GitHub Actions secret `BOT_APP_ID` |
| Homelab | `renovate` | `BOT_APP_PRIVATE_KEY` | GitHub Actions secret `BOT_APP_PRIVATE_KEY` (PEM) |

Same GitHub App the hosted workflow already uses via `actions/create-github-app-token`. The CronJob mints a one-hour installation token at start (`app/resources/github-app-token.js`). The app must keep access to `Aviator-Coding/home-ops` and `Aviator-Coding/mortyops` (preset `extends`).

Until that item exists, the ExternalSecret will stay non-ready and the first CronJob will fail token minting. The `RenovateNeverSucceeded` alert is the tripwire on first deploy (no prior success). `RenovateRunMissing` only applies after at least one successful run.

## Cutover (follow-up PR, not this one)

1. Confirm a dry-run Job completes (`kubectl -n renovate get jobs`) and logs show the repo was processed.
2. Remove `env.RENOVATE_DRY_RUN` from `app/helmrelease.yaml` entirely. Do not set it to `"false"`.
3. After a live write run looks healthy, suspend or delete `.github/workflows/renovate.yaml`.

## Alerts

The chart has no metrics Service, so there is no ServiceMonitor. `app/prometheusrule.yaml` watches kube-state-metrics CronJob timestamps:

- `RenovateJobFailed` - last schedule did not succeed within 40m
- `RenovateRunMissing` - no successful run in 8h after at least one prior success (two missed cycles)
- `RenovateNeverSucceeded` - CronJob exists but has never succeeded (8h); first-deploy / missing-secret tripwire
- `RenovateCronJobAbsent` - CronJob missing for 1h
