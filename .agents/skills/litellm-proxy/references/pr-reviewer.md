# AI PR reviewer (`.github/workflows/ai-pr-review.yaml`)

Advisory AI review on every same-repo pull request:
`misospace/pr-reviewer-action` (SHA-pinned) on the in-cluster ARC runner,
against `http://litellm.ai.svc.cluster.local:4000/v1`. Standards file:
`.github/ai-review-rules.md`. Model: `models/pr-review-local.yaml`. Key:
`virtualkeys/ai-pr-review.yaml`. CI: `scripts/ci/litellm-pr-reviewer-test.py`.

## Advisory only, by construction (captain decision)

- `publish_mode: comment` publishes with `gh pr comment`; the action reaches
  `gh pr review` only in `review_comment`/`review_verdict` modes, so it cannot
  approve or request changes.
- `allow_approve`, `approve_forks`, `fail_on_request_changes` stay false.
- **Never a required status check** (branch protection:
  `docs/branch-protection.md`), and "Allow GitHub Actions to create and approve
  pull requests" stays off.
- `on_model_failure: notice`: a GPU outage posts "AI review could not run"
  instead of a red check.

## Cannot spend money

- `ai_model: pr-review-local`: zero-priced, no cloud fallback, thinking off.
  The key allow-lists only it and `embedding-local`; anything else is 403
  (verified). Never give this key `auto`: it fires unattended on every PR,
  including heavy Renovate traffic.
- `review_routing_mode: "off"` (no smart-model escalation). All outward
  features (`tool_mode`, evidence, Linear, search, `*_enable_for_forks`) are
  pinned off explicitly so an upstream default flip cannot widen a job that
  holds `pull-requests: write`.
- `ai_fallback_model: pr-review-local`, an availability retry on the same free
  alias. Do not clear it: the pinned action exports
  `AI_FALLBACK_BASE_URL = ai_fallback_base_url || ai_base_url` and refuses to
  run when that is set and the fallback model is empty.

## Why a dedicated non-thinking alias

The action parses `choices[0].message.content`. `chat-local` is a reasoning
model that puts everything in `reasoning_content` and returns empty
`content` (measured: 800/800 tokens on reasoning, JSON parse fails). The fix
is `extra_body.chat_template_kwargs.enable_thinking: false` declared on the
model; per-request kwargs change nothing. The classifier alias is not reused:
it carries `num_retries: 0` and its metrics series feeds the router alerts.

## Sizing (shared B70)

- `model_context_tokens: "65536"`, not the served 262144: the action budgets
  `(ctx - reserve) * 3` bytes, and 262144 would authorize ~14 minutes of
  prefill at ~310-350 tok/s. 65536 reviews ~85% of PRs untruncated and bounds
  one review to ~6.5 minutes of GPU. Undershooting is safe (the action
  truncates and says so).
- Decode (~11 tok/s) is the bottleneck: `ai_max_tokens: "2048"`; if reviews
  feel slow, `review_verbosity: concise` helps more than a smaller prompt.
- `ai_response_format: json_object` (upstream advice for LiteLLM; json_schema
  makes some models emit one-line walls).
- Timeouts: 10s connect, 600s request, one retry; job `timeout-minutes: 25`.
- `skip_if_diff_unchanged` and `review_scope: auto` keep re-runs cheap; the
  concurrency group cancels superseded runs of the same PR. Key limits
  (rpm 10, tpm 200000) allow a few concurrent Renovate reviews.

## Fork PRs never run it

Job guard: `github.event.pull_request.head.repo.full_name ==
github.repository && !github.event.pull_request.draft`. The repo is public and
the runner reaches cluster-internal services; a fork `GITHUB_TOKEN` is
read-only anyway; `pull-requests: write` on a fork-reachable workflow is a
privilege-escalation shape. `pull_request_target` is deliberately not used.
Fork PRs get no review; Renovate PRs are same-repo and are reviewed. Drafts
are reviewed when marked ready.

## Standards file, not AGENTS.md

The action hard-truncates the standards file to its first 16000 bytes
(`STANDARDS_HARD_TRUNC_BYTES` in the CI test). `AGENTS.md` is far over that,
so it would arrive as a mid-sentence head-slice. `standards_file` names
`.github/ai-review-rules.md` explicitly (the default candidate order picks
`AGENTS.md` first). Keep that file under the cap; CI asserts it. When the two
disagree, `AGENTS.md` wins and the rules file is corrected.

## The credential: two-step rotation

The ARC runner has no Kubernetes API access, so it cannot read the minted
Secret. The key is **also** a hand-set repository secret
`LITELLM_PR_REVIEW_KEY`, copied from 1Password
`litellm-consumer-ai-pr-review` (written by the PushSecret).

- Setup: after Flux reconciles `ai/litellm`,
  `kubectl -n ai get secret litellm-key-ai-pr-review -o jsonpath='{.data.key}' | base64 -d`
  (or 1Password), then set the repository secret. Until it exists the
  workflow emits a warning and skips green.
- **Rotation has two steps; skipping the second breaks the reviewer
  silently** (every run posts "AI review could not run" while the cluster
  looks healthy): (1) the operator re-mints and the PushSecret updates
  1Password; (2) update `LITELLM_PR_REVIEW_KEY` on GitHub.
- Not fetched from 1Password Connect at run time: `OP_CONNECT_TOKEN` reads
  every item in the `Automation` vault, including the Authentik terraform
  credentials. A dedicated secret is narrower.
- Alias collision applies ([governance-keys.md](governance-keys.md)): if an
  `ai-pr-review` key already exists in the proxy DB, delete it first.

## Turning it off

Delete or disable the workflow. The key and alias are inert on their own.
