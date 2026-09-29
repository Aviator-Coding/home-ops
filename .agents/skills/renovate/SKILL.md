---
name: renovate
description: "Read before editing .renovaterc.json5, .renovate/**, any # renovate: annotation or the renovate app, and before merging a Renovate PR that touches Talos, tuppr upgrade CRs, Ceph, LiteLLM or kopiur. Covers suspending the GHA workflow before a config merge, negated-regex pins instead of <X.Y.Z, docker isStable ignoring -alpha, and talosupgrade.yaml bumps being unattended node upgrades."
---

# Renovate: config, annotations and risky auto-merges

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`kubernetes/apps/base/renovate/README.md`](../../../kubernetes/apps/base/renovate/README.md) - in-cluster writer and rollback
- [`.renovate/overrides.json5`](../../../.renovate/overrides.json5) - version pins and their reasoning
- [`.renovate/autoMerge.json5`](../../../.renovate/autoMerge.json5) - auto-merge rules

`AGENTS.md` entries (search for the opening words):

- Merging changes to `.renovaterc.json5`
- `quay.io/ceph/ceph` is constrained to stable `x.2.z` tags
- The `.renovate/overrides.json5` Talos pin is not just version-awareness hygiene

## Related skills

- `talos-nodes`
- `github-ci`
