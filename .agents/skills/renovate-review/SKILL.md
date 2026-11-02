---
name: renovate-review
description: "Read before acting on a Renovate PR that did not merge itself (minor, major, or risk:high): reading release notes, the risky groups (Kubernetes, Talos, Rook-Ceph, Cilium, Flux), checking rendered diffs and CI Success, and the recommendation comment. Never merge."
---

# Review a Renovate PR

Two mechanisms merge Renovate PRs without the owner: `risk-tier.yaml` auto-merges a
verified Renovate digest or patch PR once `CI Success` is required, unless a path is
risk:high, cluster-wide (`kubernetes/components/**`, an `apps/*/kustomization.yaml`, the
system, system-controller, actions-runner-system or database namespaces) or agent-steering
(`scripts/ci/risk_tier.py` has the list); and Renovate's own
`.renovate/autoMerge.json5` merges digest, patch and (after 3 days) minor updates except the
never-merge packages. Narrowing the second to match goal D3 is goal task 4.1. Everything
else waits for the owner; this skill prepares the decision. Skill `renovate` covers the
config and its tripwires.

## Triage

```bash
gh pr list --author app/mortyops --state open --json number,title,labels \
  --jq '.[] | "\(.number) \([.labels[].name] | join(",")) \(.title)"'
```

- `type/major`: put the breaking changes and a migration plan in the one recommendation
  comment on the PR; the PR waits for the owner. Do not open an issue.
- The tier comes from the changed paths, not the group (check the PR's `risk:*` label).
  Treat the `Kubernetes`, `Talos`, `Rook-Ceph`, `Cilium` and `Flux Operator` groups
  (`.renovate/groups.json5`) with extra care whatever their label: a `talosupgrade.yaml` or
  tuppr bump is an unattended node upgrade (skill `talos-nodes`).

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
