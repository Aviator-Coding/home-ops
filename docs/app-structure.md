# App directory shapes

Skill `flux-gitops` ([app-shapes.md](../.agents/skills/flux-gitops/references/app-shapes.md))
is the map of base vs overlay, component depth, one-volume includes, and
health checks. Scaffold: `scripts/add-app/generate-app.sh`.

## Parameterized instance

Rare. One HelmRelease template rendered twice with different substitutes
(`actions-runner-system/gha-runner-scale-set/app/aviator-coding/`). Read the
skill before adding another.

## A pod-options key the chart doesn't recognize is silently discarded

`defaultPodOptions` is a sibling of `controllers:` / `service:`. A different
spelling or a nest under `controllers:` is dropped by Helm. flate does not
warn. Skill `app-workloads`.
