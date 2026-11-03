# Branch protection

Ruleset `main-branch-protection`, id `21250320`, target `refs/heads/main`,
enforcement `active`. It is a GitHub setting applied with `gh api`, not a
manifest.

```bash
gh api repos/skrumbach/home-ops/rulesets/21250320
```

| Rule | Effect |
|---|---|
| `deletion` | `main` cannot be deleted |
| `non_fast_forward` | force-push rejected |
| `pull_request` | changes land through a PR. `required_approving_review_count: 0` |
| `required_status_checks` | `CI Success` only (`integration_id` 15368, GitHub Actions). `strict_required_status_checks_policy: false`. **Pending owner apply (2026-11-02):** the live ruleset still requires `Labeler - Labeler` until the payload below is PUT |

Bypass: repository role Admin (`actor_id` 5), `bypass_mode: always`. On this
repo that role is the owner. `mortyops[bot]` is not a bypass actor.

## Why only CI Success

A required check whose workflow never starts stays `Expected` and the PR can
never merge. `ci.yaml` has no trigger-level `paths:` filter, so `CI Success`
posts on every PR: success, success-via-skip, or failure. It runs `always()`
and fails on any failed or cancelled job and on fork PRs
(`workflow-contract-test.py`). Image Pull and Terraform Diff need the cluster,
so they stay advisory: a broken cluster must not block the PR that fixes it.

Changing the ruleset is a live mutation (`gh api -X PUT .../rulesets/21250320`
with the payload below) and needs the owner's go-ahead.

## platformAutomerge

`.renovaterc.json5` sets `platformAutomerge: false`. With it true, GitHub
merges as soon as the required check is green, while Image Pull may still be
queued, and branch deletion then cancels it. Renovate merges on its own pass,
which waits for every check on the PR.

The payload is kept below so a rebuild does not depend on the API
response. Update that JSON only when the live ruleset changes.

## Applied payload

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
          { "context": "CI Success", "integration_id": 15368 }
        ]
      }
    }
  ]
}
```
