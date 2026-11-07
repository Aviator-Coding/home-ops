---
name: renovate-review
description: "Read before acting on a Renovate PR that did not merge itself (minor, major): reading release notes, the risky groups (Kubernetes, Talos, Rook-Ceph, Cilium, Flux), checking rendered diffs and CI Success, and the recommendation comment. Never merge."
---

# Review a Renovate PR

Renovate's own `.renovate/autoMerge.json5` merges digest, patch and (after 3 days) minor
updates except the never-merge packages. Everything else waits for the owner; this skill
prepares the decision. Skill `renovate` covers the config and its tripwires.

## Triage

```bash
gh pr list --author app/mortyops --state open --json number,title,labels \
  --jq '.[] | "\(.number) \([.labels[].name] | join(",")) \(.title)"'
```

- `type/major`: put the breaking changes and a migration plan in the one recommendation
  comment on the PR; the PR waits for the owner. Do not open an issue.
- Treat the `Kubernetes`, `Talos`, `Rook-Ceph`, `Cilium` and `Flux Operator` groups
  (`.renovate/groups.json5`) with extra care whatever their label: a `talosupgrade.yaml` or
  tuppr bump is an unattended node upgrade (skill `talos-nodes`).
- A Cilium bump that carries cilium/cilium#47914 (the fix for #47913, skill `networking`)
  makes the agent refuse netkit + endpoint routes + L7 proxy. The live `cilium-config` has
  `enable-l7-proxy: true` (chart default, unset in `helmrelease.yaml`), so every agent would
  fail to start. Land `l7Proxy: false` (no L7 or FQDN policy exists) or
  `endpointRoutes.enabled: false` in its own PR first. Search the release notes for 47914.
  Once it is in, the no-port reply rules in the CiliumNetworkPolicies can go.
- A Python minor (`.renovate/autoMerge.json5` never automerges it) is judged per consumer,
  not per version. Blocked: `.mise.toml` (CI installs litellm's test deps) and any image
  with native wheels until each dep ships wheels for it (litellm `requires-python`,
  `fastuuid` on PyPI). Not blocked: a stdlib-only script image such as `queue-watchdog`;
  check its imports, then merge on green CI. Review each file of a mixed PR on its own.

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
