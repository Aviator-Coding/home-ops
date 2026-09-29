---
name: renovate
description: "Read before editing .renovaterc.json5, .renovate/**, any # renovate: annotation or the renovate app, and before merging a Renovate PR that touches Talos, tuppr upgrade CRs, Ceph, LiteLLM or kopiur. Covers suspending the GHA workflow before a config merge, negated-regex pins instead of <X.Y.Z, docker isStable ignoring -alpha, and talosupgrade.yaml bumps being unattended node upgrades."
---

# Renovate

The live writer is the in-cluster CronJob
(`kubernetes/apps/base/renovate/`, chart `oci://ghcr.io/renovatebot/charts/renovate`,
every 4 hours). `.github/workflows/renovate.yaml` is the rollback path: its
`schedule` is commented out. Its `push` trigger on `.renovaterc.json5` and
`.renovate/**.json5` is still active.

## Tripwires

1. **Suspend `.github/workflows/renovate.yaml` before merging a config change.**
   The `push` trigger runs a GHA writer beside the CronJob. Resume after the
   in-cluster pass (or `kubectl -n renovate create job --from=cronjob/renovate`
   if a pass is needed immediately). Comment-only edits of `.renovate/**` still
   fire it.
2. **Last match wins.** A later `packageRule` overrides an earlier one.
   Every `automerge: false` rule sits after the three blanket automerge rules.
   The "MUST stay after" comments are the ordering constraint. Sibling
   exclusions match disjoint packages, so their order relative to each other
   does not matter. None of them may turn automerge back on.
3. **Never-automerge list** (proposals still open as PRs): Renovate's own chart
   (`matchFileNames: kubernetes/apps/base/renovate/**`), kopiur images, the four
   Talos packages, the five Kubernetes control-plane/kubelet packages,
   `ghcr.io/berriai/litellm-non_root`, and the CloudNativePG chart and operator
   image (1.31.0 removes the in-tree barman backup; skill `databases`). rsshub
   digests stay `automerge: true` on `before 6am on monday` (UTC).
4. **A version pin uses a negated regex, never a bare `<X.Y.Z`.**
   `allowedVersions` filters candidates. Once the tracked value passes a
   ceiling, Renovate proposes nothing. Talos excludes only v1.13.3:
   `!/^v?1\.13\.3$/`. [versioning-traps.md](references/versioning-traps.md)
5. **Docker `isStable` ignores `-alpha` / `-beta` / `-rc`.** It keeps the
   dash-separated prefix. `v1.14.0-alpha.0` is a stable minor. The Talos
   automerge exclusion covers stable bumps too, because a merge of
   `talosupgrade.yaml` is an unattended powercycle.
6. **No in-repo branch automerge.** Do not re-add `:automergeBranch` or
   `automergeType: "branch"`. `renovate/**` pushes get no status check, so
   `ignoreTests: false` leaves them pending. mortyops' mise and
   github-actions presets set `ignoreTests: true`; later home-ops rules force
   it back to false, so those updates stay PRs.
7. **`platformAutomerge: false`.** GitHub would otherwise merge when Labeler
   alone is green. Skill `github-ci`.
8. **`# renovate:` value stays on the next line.** The customManager and
   `functional-comments-guard` both require that. Do not edit those lines
   while condensing comments.
9. **`quay.io/ceph/ceph` stays on stable `x.2.z`**
   (`allowedVersions: "/^v?\\d+\\.2\\.\\d+$/"`). Do not apply that regex to
   `ghcr.io/rook/ceph`. Those stable tags still match the blanket docker
   automerge rules (as PRs).
10. **A merged bump of `tuppr/upgrades/talosupgrade.yaml` upgrades nodes.**
    tuppr consumes that CR with `rebootMode: powercycle`. Treat it as a live
    upgrade. The same shape applies to `kubernetesupgrade.yaml` (kubelet).
    Skill `talos-nodes`.

## Where things live

| What | Path |
|---|---|
| Preset chain | `.renovaterc.json5` `extends` (mortyops, then local files, last-match) |
| Rules | `.renovate/{autoMerge,customManagers,grafanaDashboards,groups,labels,overrides,talos}.json5` |
| In-cluster app | `kubernetes/apps/base/renovate/` |
| GitHub App token | 1Password `Homelab/renovate` (`BOT_APP_ID`, `BOT_APP_PRIVATE_KEY`) |
| Alerts | `app/prometheusrule.yaml` (kube-state-metrics CronJob timestamps; the chart has no metrics endpoint) |

## Procedures

- Pins, versioning schemes, ceph/plex/EMQX: [versioning-traps.md](references/versioning-traps.md).
- Rollback, alerts, self-update exclusion: [operations.md](references/operations.md).

## Verify

- `python3 scripts/ci/talos-renovate-pin-test.py` (needs node and renovate; the
  test installs it or uses `RENOVATE_NODE_PATH`).
- Sibling harnesses: `kubernetes-renovate-group-test.py`,
  `plex-renovate-versioning-test.py`, `rsshub-renovate-schedule-test.py`,
  `renovate-binding-conditions-test.py`.
