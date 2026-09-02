---
name: flux-substitution
description: Read before adding or editing any Flux-reconciled file that can carry a literal ${...} token that is NOT a Flux substitution variable - Grafana dashboard JSON, Helm values, ConfigMaps, container env - and when a Flux Kustomization is stuck with an envsubst error. Covers the three fixes and why neither flate nor task flux:test:all catches this.
---

# Flux postBuild.substitute and literal ${...} tokens

Relocated verbatim from `AGENTS.md` on 2026-09-01 so it loads only when this subsystem is in play.
The text below is unchanged; only line breaks were inserted. `AGENTS.md` keeps a one-sentence pointer.
Add new findings here or to the owning document, not back into `AGENTS.md` - see its
"Maintaining this file" section for the rule.

- **`postBuild.substitute` collision (literal `${...}` tokens)**: Flux's strict-mode envsubst runs over the *entire* built Kustomization output, so any literal `${...}` token that isn't a Flux substitution variable - a Grafana dashboard variable like `${__url_time_range}`/`${datasource}`, or an app's own unresolved var like `${TIMEZONE}` - gets treated as a missing Flux variable and fails the **whole Kustomization**, not just the offending resource. Flux only reports the first failure per Kustomization, so fixing one collision can reveal another underneath it. This caused a 44-day, 4-app outage (coder, grafana, rsshub, rsshub-playwright frozen at once commit) before being fixed in #1371 - see `git show e5a43b1b`. `task flux:test:*` / CI's `flate` do **not** catch this, and the 2026-08-29 flux-local -> flate migration did **not** change that - do not let flate's use of Flux's own libraries suggest otherwise. flux-local never ran a real envsubst pass at all (`postbuild_substitute` only fed Helm values). flate *does* call `fluxcd/pkg/envsubst`, the exact engine kustomize-controller uses, and honours `kustomize.toolkit.fluxcd.io/substitute: disabled` - but only in **lenient** mode, with no strict flag: `Substitute` (`pkg/kustomize/substitute.go`) returns `exists=true` for every lookup, so an undefined `${VAR}` expands to empty rather than failing. Our cluster runs `StrictPostBuildSubstitutions=true` (opt-out since kustomize-controller v1.9). Measured on this repo against flate v0.6.1: injecting a literal `${UNDEFINED_COLLISION_TOKEN}` renders it as empty and still reports `299 passed`, exit 0. So a PR can go green and still freeze the cluster. Detect it live with `flux get ks -A --status-selector ready=false` (message names the offending variable, e.g. `envsubst error: variable not set (strict mode): "datasource"`). Three fixes, pick by situation:

  - Annotate the specific resource `kustomize.toolkit.fluxcd.io/substitute: disabled` when it's a whole resource full of non-Flux tokens (e.g. a Grafana dashboard ConfigMap) - `kubernetes/apps/base/coder/app/kustomization.yaml` (`coder-dashboards` configMapGenerator).

  - Escape with `$${...}` when the same resource legitimately mixes real Flux variables with literal ones - `kubernetes/apps/base/monitoring/grafana/app/helmrelease.yaml` (`$${datasource}` next to real `${SECRET_DOMAIN}` substitution).

  - Define the variable properly when it's a genuinely missing Flux var, not a false-positive match - `kubernetes/apps/base/selfhosted/rsshub/playwright/helmrelease.yaml` renamed `${TIMEZONE}` to `${CONFIG_TIMEZONE}`, defined in `kubernetes/apps/main/selfhosted/rsshub.yaml`'s `substitute` map.
