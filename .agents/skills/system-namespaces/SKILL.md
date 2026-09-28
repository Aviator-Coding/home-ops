---
name: system-namespaces
description: "Read before changing system-controller/k8tz, system-upgrade/tuppr, kube-system add-ons (coredns, multus, descheduler, reloader, spegel, mglru-disable) or system/fstrim, or moving anything between the system* namespaces. Covers k8tz excluding its own namespace, the non-atomic webhook move, tuppr pinned by the Talos namespace allowlist, and CREATE-only admission webhook drift."
---

# System namespaces: k8tz, tuppr and kube-system add-ons

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`docs/system-namespace-consolidation-analysis.md`](../../../docs/system-namespace-consolidation-analysis.md) - why the namespaces stay split
- [`docs/k8tz-imagevolume-evaluation.md`](../../../docs/k8tz-imagevolume-evaluation.md) - k8tz imageVolume decision
- [`docs/admission-webhook-create-only-drift.md`](../../../docs/admission-webhook-create-only-drift.md) - CREATE-only webhook drift

`AGENTS.md` entries (search for the opening words):

- Do not merge `system-controller` or `system-upgrade` into `system`

## Related skills

- `talos-nodes`
- `networking`
