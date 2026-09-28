---
name: app-workloads
description: "Read before writing or changing an app's pod spec: securityContext, fsGroup, app-template pod-options keys, liveness/readiness probes, PVC mounts, linuxserver images, Recreate on RWO claims, or when an app is Ready but cannot write to its volume. Covers the silently discarded pod-options key, fsGroup re-owning content, and probes that can never fail."
---

# App pod specs: securityContext, fsGroup, probes and PVC mounts

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`kubernetes/apps/base/downloads/bazarr/app/helmrelease.yaml`](../../../kubernetes/apps/base/downloads/bazarr/app/helmrelease.yaml) - linuxserver non-root worked example
- [`kubernetes/apps/base/selfhosted/n8n/app/helmrelease.yaml`](../../../kubernetes/apps/base/selfhosted/n8n/app/helmrelease.yaml) - probe that must be able to fail
- [`kubernetes/apps/base/home-automation/home-assistant/app/helmrelease.yaml`](../../../kubernetes/apps/base/home-automation/home-assistant/app/helmrelease.yaml) - probes instead of a bespoke alert
- [`docs/monitoring/exporter-endpoint-repair-2026-09-20.md`](../../../docs/monitoring/exporter-endpoint-repair-2026-09-20.md) - probe and endpoint repair evidence

`AGENTS.md` entries (search for the opening words):

- `linuxserver/*` images need explicit opt-in
- An app can be denied write access to its own PVC
- `fsGroup` does re-own existing content on this cluster
- Helm cannot un-set a field it never set
- Before writing a new PrometheusRule for "is this app down"

## Related skills

- `pvc-integrity-checks`
- `flux-gitops`
