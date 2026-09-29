# Branch protection (`main`)

Ruleset `main-branch-protection` (id `21250320`), target `refs/heads/main`,
enforcement `active`. Applied with `gh api`. It is not a manifest.

```bash
gh api repos/Aviator-Coding/home-ops/rulesets/21250320
```

| Rule | Effect |
|---|---|
| `deletion` | `main` cannot be deleted |
| `non_fast_forward` | force-pushes rejected |
| `pull_request` | PRs required. Approving review count is 0 |
| `required_status_checks` | `Labeler - Labeler` only (`integration_id` 15368). Branch need not be up to date |

Bypass: repository role Admin (`actor_id` 5), always. `mortyops[bot]` is not bypassed.

`flate.yaml`, `image-pull.yaml` and `validate.yaml` post aggregate Success
checks on every PR (a skipped job still posts). They are not in this ruleset.
Adding them is a live mutation and needs an explicit go-ahead. Until then,
GitHub can merge when Labeler alone is green, which is why
`.renovaterc.json5` sets `platformAutomerge: false`.

Why the aggregates used to be unsafe, and the current follow-up: skill
`github-ci` ([branch-protection.md](../.agents/skills/github-ci/references/branch-protection.md)).

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
