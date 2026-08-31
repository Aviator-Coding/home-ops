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

## The 2026-08-22 keep-in-cluster decision's four binding conditions

The captain's 2026-08-22 decision to keep Renovate in-cluster (overriding the investigation's
recommendation to drop it) was conditioned on four requirements, written against the
`mogenius/renovate-operator` the investigation had evaluated - not the official chart + GitHub-App
CronJob that actually shipped. Two shipped correctly; two shipped silently missing. Re-measured
2026-08-31 against what actually runs, not against the original tool's shape:

1. **Metrics + ServiceMonitor** - live and correct as a deliberate substitution: the official chart
   has no metrics endpoint to scrape, so `app/prometheusrule.yaml` watches kube-state-metrics
   CronJob timestamps instead (see Alerts above). No further action.
2. **Alert on silence** - live and correct: `RenovateRunMissing` / `RenovateNeverSucceeded` /
   `RenovateCronJobAbsent` above.
3. **Docker Hub registry credentials in `RENOVATE_HOST_RULES`** - **formally retired, not
   satisfied.** No dockerconfigjson secret or host rule exists; every `docker.io` lookup is
   anonymous. The risk the condition guarded against was Docker Hub's anonymous pull-rate limit
   stalling or failing Renovate's own dependency lookups. Measured directly against the live
   endpoint rather than inferred from the condition's wording: a full-repo run (`RENOVATE_DRY_RUN=full`,
   `LOG_LEVEL=debug`, same image/config/identity as the real CronJob, run manually 2026-08-31 to
   avoid waiting on a scheduled slot) issued 50 unique anonymous `getManifestResponse` calls to
   `index.docker.io` across ~19 unique `docker.io` images in a single ~2m40s pass - the entire
   burst the current ~315-dependency repository produces - with **zero** `429`/`toomanyrequests`
   responses. The three most recent live (non-debug) scheduled runs show the same: zero rate-limit
   errors in their logs. This is the actual worst case, not a steady-state sample: every run
   processes the full pending-branch set in one pass (`:disableRateLimiting` from the shared
   `mortyops` preset removes Renovate's own internal PR/branch throttling), so there is no
   larger burst this design produces between cold-start runs than the one measured. Retired
   because the measured exposure - one ~50-request burst per 4h CronJob cycle, well clear of
   failure in direct testing - does not match the failure mode the condition was written to
   prevent. If the tracked `docker.io` dependency count grows several-fold from today's ~19,
   re-measure rather than assume the margin still holds; the fix then is the RENOVATE_HOST_RULES
   the original condition specified, which needs only a Docker Hub account credential in the
   existing `renovate` 1Password item and an `existingSecret`/`hostRules` entry, not a redesign.
4. **Exact chart-pin exclusion from automerge** - **was genuinely missing; closed 2026-08-31.**
   Renovate tracks its own chart (`ghcr.io/renovatebot/charts/renovate`, from
   `app/ocirepository.yaml`) as an ordinary `docker`/`helm`-datasource dependency, and
   `.renovate/autoMerge.json5`'s blanket patch/minor/digest automerge rules had no exclusion for
   it. Confirmed exploitable, not just theoretical: this repo's branch protection requires 0
   approving reviews and only the `Labeler` status check
   (`docs/branch-protection.md`), and at measurement time a real minor chart update
   (`46.255.0` -> `46.265.0`) was sitting unexcluded in the Renovate dashboard, next in line to
   automerge with zero human review once its 3-day `minimumReleaseAge` cleared - exactly the
   self-update failure mode the condition existed to prevent. Closed with the smallest available
   change: a `matchFileNames: ["kubernetes/apps/base/renovate/**"]` / `automerge: false` rule
   in `.renovate/autoMerge.json5`, placed after the three blanket automerge rules and
   immediately before the existing kopiur package-name exclusion (that kopiur rule stays last -
   `scripts/ci/kopiur-stage0-test.py` pins `rules[-1]`). The two exclusions match disjoint
   dependencies, so their relative order to each other does not matter; both must sit after the
   blankets so last-match-wins actually disables automerge. Any change to this app's own
   manifests - the chart pin today, the CronJob's `image.tag` too if Renovate's helm-values
   manager ever starts tracking it - always lands as a reviewable PR instead of a package-name
   match that a rename or re-publish could silently stop matching.
