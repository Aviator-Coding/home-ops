# Branch protection

Ruleset `main-branch-protection`, id `21250320`, target `refs/heads/main`,
enforcement `active`. It is a GitHub setting applied with `gh api`, not a
manifest.

```bash
gh api repos/Aviator-Coding/home-ops/rulesets/21250320
```

| Rule | Effect |
|---|---|
| `deletion` | `main` cannot be deleted |
| `non_fast_forward` | force-push rejected |
| `pull_request` | changes land through a PR. `required_approving_review_count: 0` |
| `required_status_checks` | `Labeler - Labeler` only (`integration_id` 15368). `strict_required_status_checks_policy: false` |

Bypass: repository role Admin (`actor_id` 5), `bypass_mode: always`. On this
repo that role is the owner. `mortyops[bot]` is not a bypass actor.

## Why only Labeler is required

A required check whose workflow never starts stays `Expected` and the PR can
never merge. Trigger-level `paths:` filters did that. `flate.yaml`,
`image-pull.yaml` and `validate.yaml` now start on every PR and post an
aggregate Success job (success, success-via-skip, or failure). A skipped job
still counts as a posted check. That is what makes those aggregates safe to
require. Each Success job must also `needs:` its `filter` job, or a failed
filter skips everything behind it and the aggregate goes green
(`workflow-hardening-test.py` enforces this).

They are still not in the ruleset. Adding them is a live mutation
(`gh api -X PUT .../rulesets/21250320`) and needs an explicit go-ahead after
the contexts have posted at least once. Until then, Labeler alone is the merge
gate.

`terraform-diff.yaml` still trigger-filters on `terraform/**`. A bad Terraform
change is still seen by `validate.yaml`'s terraform job. `ai-pr-review.yaml`
stays off the ruleset: it is advisory.

## platformAutomerge

`.renovaterc.json5` sets `platformAutomerge: false`. With it true, GitHub
merges as soon as Labeler is green, while flate, Image Pull and validate are
still queued, and branch deletion then cancels them. Measured on 10 merged PRs
(PR discussion in that file): merges landed seconds after Labeler, and a red
versions check was recorded after merge. Renovate now merges on its own pass,
which gives the other workflows a cycle to post. That does not make those
checks required.

The applied payload (Admin bypass, four rules, Labeler only) is kept below so a
rebuild does not depend on the API response. Update that JSON only when the
live ruleset changes.

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
          { "context": "Labeler - Labeler", "integration_id": 15368 }
        ]
      }
    }
  ]
}
```
