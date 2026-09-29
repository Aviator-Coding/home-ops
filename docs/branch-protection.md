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

**`ignoreTests: false` does not prevent premature merges — that gate is GitHub's platform
auto-merge, not Renovate's own check.** `ignoreTests: false` governs whether *Renovate itself*
proposes merging; once it enables GitHub's native auto-merge (`platformAutomerge`), it's GitHub
that decides when to merge, and GitHub merges the instant its *required* checks go green — here,
just `Labeler - Labeler` — with no regard for `flate`, `Image Pull`, or `validate`, which are
never required (see below) and are frequently still queued. Measured across 10 merged PRs
(2026-09-12): merges landed 3-23s after Labeler passed while those workflows were still running,
and two of the ten recorded a RED versions check only *after* the merge, because branch deletion
cancelled the in-flight run instead of letting it fail visibly first. Closed by setting
`platformAutomerge: false` in `.renovaterc.json5` — Renovate now merges its own PRs on its
own four-hourly pass instead of via GitHub's instant required-check trigger, so every workflow a
PR touches has a full cycle to post before Renovate re-evaluates it. This doesn't change what's
actually required — the gap in the next section is unaffected — it only removes the mechanism
that let a merge outrun the non-required checks. The full required-checks restructure below was
evaluated and declined in favor of this smaller, reversible change.

## Why only `Labeler - Labeler` is required today — and how that's being closed

Required status checks and path-filtered workflows interact badly: if a required check's
workflow never triggers for a given PR, GitHub leaves that check `Expected` forever and the PR
can never merge. This repo's substantive validation workflows —
[`flate.yaml`](../.github/workflows/flate.yaml),
[`image-pull.yaml`](../.github/workflows/image-pull.yaml), and
[`validate.yaml`](../.github/workflows/validate.yaml) — used to filter on `paths:` **at the
`on: pull_request:` trigger level**, not inside a job. A PR that touched none of those paths
never started the workflow at all — no check run was ever created, skipped or otherwise.
Requiring any of them would have permanently blocked every PR outside their path list.

This was not hypothetical — it is what actually happened, measured live against real merged PRs:

| PR | What it touched | Checks that actually posted |
|---|---|---|
| [#1400](https://github.com/Aviator-Coding/home-ops/pull/1400) (2026-08-23, docs-only) | `docs/ceph-cluster-changelog.md` only | `Labeler - Labeler` only |
| [#1390](https://github.com/Aviator-Coding/home-ops/pull/1390) (talos-only, before `validate.yaml` existed) | `talos/machineconfig.yaml.j2`, `docs/` | `Labeler - Labeler` only — the historical "one check" case `validate.yaml` (#1391) was written to close |
| [#1399](https://github.com/Aviator-Coding/home-ops/pull/1399) (`kubernetes/apps/**` change) | `kubernetes/apps/monitoring/...`, `docs/` | `Labeler`, `Flux Local - *` (incl. `Flux Local - Success`), `Image Pull - *` (incl. `Image Pull - Success`) |

**Fixed:** `flate.yaml`, `image-pull.yaml`, and `validate.yaml` no longer carry a trigger-level
`paths:` filter at all. Each already computed a job-level `filter` step (via
`bjw-s-labs/action-changed-files`) for exactly this kind of path detection; the fix moves the
*only* path decision to that job level and lets the trigger fire unconditionally. `validate.yaml`
additionally gained an aggregate `Validate - Success` job — mirroring the `Flate - Success` /
`Image Pull - Success` jobs the other two workflows already had — since it previously had no
single check summarizing its 7-way fan-out (`talos`, `versions`, `bootstrap`, `renovate-config`,
`terraform`, `python-tests`).

This is the same pattern [`labeler.yaml`](../.github/workflows/labeler.yaml) already used: a
same-repo fork guard (`if: github.event.pull_request.head.repo.full_name == github.repository`,
documented in `AGENTS.md`) gates each job, and a job-level `if` still creates a check run
(`skipped`) rather than no check run at all — GitHub treats a skipped required check as passing.
That is what makes a no-trigger-path-filter workflow *structurally* safe to mark required: its
aggregate check is guaranteed to resolve, one way or another (success, success-via-skip, or
failure), for every PR, including from a fork.

[`ai-pr-review.yaml`](../.github/workflows/ai-pr-review.yaml) also has no trigger-level path
filter and runs on every PR, but stays off the ruleset by design — it is advisory-only
(`publish_mode: comment`, not a merge gate) and must not become one; see
skill `litellm-proxy`
([`references/pr-reviewer.md`](../.agents/skills/litellm-proxy/references/pr-reviewer.md)).
[`terraform-diff.yaml`](../.github/workflows/terraform-diff.yaml) still trigger-path-filters on
`terraform/**` and is out of scope here (optional follow-up, not a reliability gap: a bad
`terraform/` change is caught by `validate.yaml`'s `terraform` job, which now always starts).

**`Labeler - Labeler` remains the only entry in `required_status_checks` today** — see Rollout
below for why the new checks aren't added to the live ruleset yet.

## Rollout

Closing the gap is two steps, deliberately not done in the same change:

1. **This PR:** remove the trigger-level `paths:` filters and add `Validate - Success`, so
   `Flate - Success`, `Image Pull - Success`, and `Validate - Success` become checks that post on
   every PR going forward.
2. **Separately, after this PR has merged and at least one subsequent PR has run these
   workflows** (GitHub's ruleset UI/API can only require a context that has posted at least once),
   add those three contexts to `required_status_checks.required_status_checks` with
   `gh api -X PUT repos/Aviator-Coding/home-ops/rulesets/21250320` (or the ruleset edit UI), with
   explicit go-ahead at that time — this is a live-ruleset mutation on the production repo, not
   something to bundle into the workflow-file change. Update the "Full applied payload" section
   below to match once that lands.

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
