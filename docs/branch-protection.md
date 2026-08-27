# Branch protection (`main`)

Before 2026-08-23, `main` carried zero branch protection and zero rulesets (`GET
/repos/Aviator-Coding/home-ops/branches/main/protection` returned 404 "Branch not
protected", `GET /repos/Aviator-Coding/home-ops/rulesets` returned `[]`). Nothing stopped a
direct push to `main`, and nothing stopped merging a pull request whose checks were red. One
such PR had already landed.

This is now closed by a repository ruleset, applied via `gh api` (branch protection is a
GitHub repo setting, not a Flux/Git-managed resource, so it lives outside `kubernetes/` and is
documented here instead of expressed as YAML in the repo).

## What's live

**Ruleset:** `main-branch-protection` (id `21250320`), target `refs/heads/main`, enforcement
`active`. Verify any time with:

```bash
gh api repos/Aviator-Coding/home-ops/rulesets/21250320
```

Rules:

| Rule | Effect |
|---|---|
| `deletion` | `main` cannot be deleted |
| `non_fast_forward` | force-pushes to `main` are rejected |
| `pull_request` | changes must land through a PR (`required_approving_review_count: 0` — this is a solo-maintainer repo; the owner merges their own PRs) |
| `required_status_checks` | `Labeler - Labeler` (GitHub Actions app, `integration_id: 15368`) must pass; `strict_required_status_checks_policy: false` (branch need not be up to date) |

**Bypass:** `actor_type: RepositoryRole`, `actor_id: 5` (Admin), `bypass_mode: always`. On this
repo the only collaborator with admin permission is the owner (`Aviator-Coding`, verified via
`GET /repos/.../collaborators`), so this is equivalent to "the repo owner is never
hard-locked" without hardcoding a user id — it also still applies correctly if the owner ever
adds another admin collaborator.

**`mortyops[bot]` (Renovate) is deliberately *not* in the bypass list.** It has no standing
exemption, so it is gated by `required_status_checks` exactly like a human-authored PR — this
is the intended effect of turning protection on. Separately, `.renovate/autoMerge.json5`
already sets `ignoreTests: false` on every automerge rule, so Renovate itself already refuses
to automerge a branch with failing checks; the ruleset adds enforcement at the platform level
on top of that self-restraint. Requiring a PR (`pull_request` rule) may also change some
`automergeType: "branch"` updates (currently digest/patch) from a direct branch merge into a
PR that auto-merges once green — same outcome, more visible in the PR list.

## Why only `Labeler - Labeler` is required — and why that's deliberate, not incomplete

Required status checks and path-filtered workflows interact badly: if a required check's
workflow never triggers for a given PR, GitHub leaves that check `Expected` forever and the PR
can never merge. This repo's substantive validation workflows —
[`flux-local.yaml`](../.github/workflows/flux-local.yaml),
[`image-pull.yaml`](../.github/workflows/image-pull.yaml),
[`validate.yaml`](../.github/workflows/validate.yaml), and
[`terraform-diff.yaml`](../.github/workflows/terraform-diff.yaml) — all filter on `paths:` **at the
`on: pull_request:` trigger level**, not inside a job. A PR that touches none of those paths
never starts the workflow at all — no check run is ever created, skipped or otherwise. Requiring
any of them today would permanently block every PR outside their path list.

This is not hypothetical — it is what actually happens, measured live against real merged PRs:

| PR | What it touched | Checks that actually posted |
|---|---|---|
| [#1400](https://github.com/Aviator-Coding/home-ops/pull/1400) (2026-08-23, docs-only) | `docs/ceph-cluster-changelog.md` only | `Labeler - Labeler` only |
| [#1390](https://github.com/Aviator-Coding/home-ops/pull/1390) (talos-only, before `validate.yaml` existed) | `talos/machineconfig.yaml.j2`, `docs/` | `Labeler - Labeler` only — the historical "one check" case `validate.yaml` (#1391) was written to close |
| [#1399](https://github.com/Aviator-Coding/home-ops/pull/1399) (`kubernetes/apps/**` change) | `kubernetes/apps/monitoring/...`, `docs/` | `Labeler`, `Flux Local - *` (incl. `Flux Local - Success`), `Image Pull - *` (incl. `Image Pull - Success`) |

`validate.yaml` itself demonstrably works for its own scoped paths (e.g. runs 7-8, triggered on
the open `renovate/kubectl-1.x` PR which touches `.mise.toml`) — the problem isn't that it's
broken, it's that its trigger-level path filter means it simply never runs, and posts nothing,
for PRs outside `talos/**`, `bootstrap/**`, `.renovate/**`, `.renovaterc.json5`,
`kubernetes/apps/base/system-upgrade/**`, `kubernetes/apps/main/system-upgrade/**`, `scripts/ci/**`, `.mise.toml`, or its own workflow file.
The same is true of `flux-local.yaml` / `image-pull.yaml` outside `kubernetes/**`, and of
`terraform-diff.yaml` outside `terraform/**`. Root-level docs, `README.md`, `Taskfile.yaml`,
`.taskfiles/**`, and most of `docs/**` are covered by none of them.

`labeler.yaml` is the only PR-triggered workflow with **no path filter on its trigger** — it
runs on every PR to `main`, full stop. Its job does carry a same-repo fork guard
(`if: github.event.pull_request.head.repo.full_name == github.repository`, mirrored from the
`image-pull`/`validate`/`terraform-diff` fork guards documented in `AGENTS.md`), but a job-level `if` still
creates a check run (`skipped`), and GitHub treats a skipped required check as passing — unlike
a trigger-level `paths:` mismatch, which creates no check run at all. That's what makes it the
only check in this repo that is safe to mark required today: it is guaranteed to resolve, one
way or another, for every PR.

**This means the ruleset does not yet gate the checks that actually catch a broken
Kustomization or a bad Talos config** — that gap is real and is the direct consequence of how
`flux-local.yaml` / `image-pull.yaml` / `validate.yaml` / `terraform-diff.yaml` are triggered, not a gap in this task.

## Follow-up to close the gap

To safely require `Flux Local - Success` / `Image Pull - Success` / a `validate.yaml`
aggregate check (and, if desired later, `terraform-diff`'s success job), those path-filtered
workflows need their path filtering moved from the `on: pull_request: paths:` trigger down into
a job-level check (they already compute a `filter` job internally for exactly this kind of path
detection — see each workflow's `filter` job) so the workflow — and therefore its check run —
always starts, and reports success-via-skip when nothing relevant changed, the same way
`labeler.yaml`'s fork guard already does. Once that's done, add those check contexts to this
ruleset's `required_status_checks.required_status_checks` array with
`gh api -X PUT repos/Aviator-Coding/home-ops/rulesets/21250320` (or via the ruleset edit UI) and
this document should be updated to match.

## Full applied payload

```json
{
  "name": "main-branch-protection",
  "target": "branch",
  "enforcement": "active",
  "bypass_actors": [
    { "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "always" }
  ],
  "conditions": {
    "ref_name": { "include": ["refs/heads/main"], "exclude": [] }
  },
  "rules": [
    { "type": "deletion" },
    { "type": "non_fast_forward" },
    {
      "type": "pull_request",
      "parameters": {
        "required_approving_review_count": 0,
        "dismiss_stale_reviews_on_push": false,
        "require_code_owner_review": false,
        "require_last_push_approval": false,
        "required_review_thread_resolution": false
      }
    },
    {
      "type": "required_status_checks",
      "parameters": {
        "strict_required_status_checks_policy": false,
        "do_not_enforce_on_create": false,
        "required_status_checks": [
          { "context": "Labeler - Labeler", "integration_id": 15368 }
        ]
      }
    }
  ]
}
```
