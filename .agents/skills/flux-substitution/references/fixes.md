# Three fixes for a literal ${...} token

Flux treats every `${...}` in the built Kustomization as a substitution
variable when strict mode is on. Grafana's `${datasource}` and an app's
own `${TIMEZONE}` are the usual false matches.

## 1. Disable substitution on that resource

Use when the whole resource is full of non-Flux tokens, typically a
Grafana dashboard ConfigMap.

```yaml
kustomize.toolkit.fluxcd.io/substitute: disabled
```

Worked example: `kubernetes/apps/base/coder/app/kustomization.yaml`
(`coder-dashboards` configMapGenerator).

Do not put this on a resource that also needs `${SECRET_DOMAIN}` or
another real variable. The annotation turns substitution off for the
entire object.

## 2. Escape the literal

Use when the same resource mixes real Flux variables with literals.
Write `$${datasource}` next to a real `${SECRET_DOMAIN}`. Flux collapses
`$$` to `$` and leaves the inner name alone.

Worked example: `kubernetes/apps/base/monitoring/grafana/app/helmrelease.yaml`.

## 3. Define the variable

Use when the token is a real missing Flux variable, not a false match.
Rename the app's own placeholder so it cannot collide, and set it in the
overlay `substitute` map.

Worked example: `kubernetes/apps/base/selfhosted/rsshub/playwright/helmrelease.yaml`
uses `${CONFIG_TIMEZONE}`, defined in
`kubernetes/apps/main/selfhosted/rsshub.yaml`.

## What does not catch it

`task flux:test:all` and CI `flate` expand an undefined token to empty
and exit 0. The outage this caused froze coder, grafana, rsshub, and
rsshub-playwright together until PR #1371. Search the reconcile message
for `variable not set (strict mode)`.
