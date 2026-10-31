---
name: renovate-review
description: "Read before acting on a Renovate PR that did not merge itself (minor, major, or risk:high): reading release notes, the risky groups (Kubernetes, Talos, Rook-Ceph, Cilium, Flux), checking rendered diffs and CI Success, the recommendation comment, and a separate issue per major bump. Never merge."
---

# Review a Renovate PR

Digest and patch updates outside risk:high paths merge themselves (`risk-tier.yaml`). The
rest wait for the owner; this skill prepares the decision. Skill `renovate` covers the
config and its tripwires.

## Triage

```bash
gh pr list --author app/mortyops --state open --json number,title,labels \
  --jq '.[] | "\(.number) \([.labels[].name] | join(",")) \(.title)"'
```

- `type/major`: open one issue per major bump (`gh issue create`) with the breaking
  changes and a migration plan; the PR waits for it.
- Groups `Kubernetes`, `Talos`, `Rook-Ceph`, `Cilium`, `Flux` are always risk:high.
  A `talosupgrade.yaml` or tuppr bump is an unattended node upgrade (skill `talos-nodes`).

## Check one PR

1. Release notes between the two versions: breaking changes, removed values, CRD
   changes, migration steps (`gh release view <tag> -R <repo>` or the chart CHANGELOG).
2. The rendered change: `flate diff hr|ks` from the PR's checks (Flate - Diff comment).
   Look for changed CRDs, new required values, and restarts of stateful workloads.
3. `CI Success` and the advisory checks on the PR (`gh pr checks <n>`).
4. The subsystem skill's tripwires (Ceph, kopiur, LiteLLM, Talos, Cilium host policy).

## Comment, never merge

```markdown
**Recommendation:** merge | merge after <step> | hold (<reason>) | close (<reason>)
**Breaking changes:** none | <list>
**Rendered diff:** <summary>
**Owner steps:** <e.g. suspend the renovate workflow first for .renovate/** changes>
```

Post with `gh pr comment <n> --body-file rec.md`. Merging, closing and Renovate config
changes are the owner's.
