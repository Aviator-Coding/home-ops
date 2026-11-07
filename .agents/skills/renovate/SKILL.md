---
name: renovate
description: "Read before editing .renovaterc.json5, .renovate/**, any # renovate: annotation or the renovate app, and before merging a Renovate PR that touches Talos, tuppr upgrade CRs, Ceph, LiteLLM or kopiur. Covers config merges, negated-regex pins instead of <X.Y.Z, docker isStable ignoring -alpha, and talosupgrade.yaml bumps being unattended node upgrades."
---

# Renovate

The live writer is the in-cluster mogenius renovate-operator
(`kubernetes/apps/base/renovate/renovate-operator/`, one `RenovateJob` `home-ops`,
hourly). `.github/workflows/renovate.yaml` is the rollback path and is
manual-dispatch only (no schedule, no `push` trigger), so it never runs beside the operator.

## Tripwires

1. **A config change lands on the next hourly run.** The owner can start one at
   once: `kubectl -n renovate annotate renovatejob home-ops
   renovate-operator.mogenius.com/schedule-all=true`, or the UI. Do not dispatch
   `renovate.yaml` unless the RenovateJob is suspended: two writers race on the same branches.
2. **Last match wins.** A later `packageRule` overrides an earlier one.
   Every `automerge: false` rule sits after the three blanket automerge rules.
   The "MUST stay after" comments are the ordering constraint. Sibling
   exclusions match disjoint packages, so their order relative to each other
   does not matter. None of them may turn automerge back on.
3. **Never-automerge list** (proposals still open as PRs): Renovate's own chart
   (`matchFileNames: kubernetes/apps/base/renovate/**`), kopiur images, Cilium
   Rook-Ceph and `quay.io/ceph/ceph` (docker/helm, any update type), the four Talos packages, the five Kubernetes control-plane/kubelet packages,
   `ghcr.io/berriai/litellm-non_root`, and the CloudNativePG chart and operator
   image (1.31.0 removes the in-tree barman backup; skill `databases`). rsshub
   digests stay `automerge: true` on `before 6am on monday` (America/New_York,
   set in `.renovaterc.json5`). 0.x minors open PRs but never automerge.
4. **A version pin uses a negated regex, never a bare `<X.Y.Z`.**
   `allowedVersions` filters candidates. Once the tracked value passes a
   ceiling, Renovate proposes nothing. Talos excludes only v1.13.3:
   `!/^v?1\.13\.3$/`. [versioning-traps.md](references/versioning-traps.md)
5. **Docker `isStable` ignores `-alpha` / `-beta` / `-rc`.** It keeps the
   dash-separated prefix. `v1.14.0-alpha.0` is a stable minor. The Talos
   automerge exclusion covers stable bumps too, because a merge of
   `talosupgrade.yaml` is an unattended powercycle.
6. **No branch automerge.** The base preset extends `:automergeBranch`, but
   `renovate/**` pushes get no status check, so a branch automerge with
   `ignoreTests: false` sits pending forever with no PR. Every automerge rule
   in `autoMerge.json5` sets `automergeType: "pr"`; a new one must too.
   `rebaseWhen: "conflicted"` is required too: `auto` rebases automerge PRs
   on every run, restarting CI, so they never merge.
7. **`platformAutomerge: false`.** GitHub would otherwise merge on the required
   checks alone, before the advisory Image Pull posts. Renovate's own merge waits
   for every check. Skill `github-ci`.
8. **`# renovate:` value stays on the next line.** The customManager and
   `functional-comments-guard` both require that. Do not edit those lines
   while condensing comments.
9. **`quay.io/ceph/ceph` stays on stable `x.2.z`**
   (`allowedVersions: "/^v?\\d+\\.2\\.\\d+$/"`). Do not apply that regex to
   `ghcr.io/rook/ceph`. Those stable tags open PRs but never automerge: the
   Ceph version must be one the running Rook release supports.
10. **A merged bump of `tuppr/upgrades/talosupgrade.yaml` upgrades nodes.**
    tuppr consumes that CR with `rebootMode: powercycle`. Treat it as a live
    upgrade. The same shape applies to `kubernetesupgrade.yaml` (kubelet).
    Skill `talos-nodes`.

11. **GHCR and quay publish no release timestamp.** Renovate's default
    `minimumReleaseAgeBehaviour` is timestamp-required, so a `minimumReleaseAge`
    on a docker rule without `timestamp-optional` stalls the update silently in
    the dashboard's "Pending Status Checks" (about 50 minors sat there until
    November 2026).
12. **The base preset is pinned (`#11.0.0`) and replaces mortyops.** Bump the
    tag as a reviewed PR and re-check `functional-comments-guard-test.py`,
    which mirrors the preset's annotated-dependency regex. PRs still come from
    the `app/mortyops` GitHub App; the preset does not set the author.

13. **`pinDigests` is on for docker images in `kubernetes/**/helmrelease.yaml`**
    (helm-values manager only; operator CRs and plain manifests stay tag-only
    until each operator is verified to accept a digest; rsshub,
    browserless/chrome and `quay.io/ceph/ceph` excluded, rule in `overrides.json5`). Pins land in one "Pin Dependencies"
    PR and automerge as `pinDigest`. A pin changes the pod spec, so each
    newly pinned workload rolls once with the same image.

## Where things live

| What | Path |
|---|---|
| Preset chain | `.renovaterc.json5` `extends` (`home-operations/renovate-presets` pinned by tag, then local files, last-match) |
| Rules | `.renovate/{autoMerge,customManagers,grafanaDashboards,groups,labels,overrides,talos}.json5` |
| In-cluster app | `kubernetes/apps/base/renovate/` |
| GitHub App token | 1Password `Homelab/renovate` (`BOT_APP_ID`, `BOT_APP_PRIVATE_KEY`) |
| Alerts | `renovate-operator/app/prometheusrule.yaml` (operator metrics, ServiceMonitor) |

## Procedures

- Pins, versioning schemes, ceph/plex/EMQX: [versioning-traps.md](references/versioning-traps.md).
- Rollback, alerts, self-update exclusion: [operations.md](references/operations.md).

## Verify

- `python3 scripts/ci/talos-renovate-pin-test.py` (needs node and renovate; the
  test installs it or uses `RENOVATE_NODE_PATH`).
- Sibling harnesses: `kubernetes-renovate-group-test.py`,
  `rsshub-renovate-schedule-test.py`, `renovate-binding-conditions-test.py`.
