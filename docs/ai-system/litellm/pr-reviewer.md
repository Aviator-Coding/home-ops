# AI PR reviewer

Advisory AI review on every same-repo pull request, running
[`misospace/pr-reviewer-action`](https://github.com/misospace/pr-reviewer-action) on the
in-cluster ARC runner against this cluster's own LiteLLM proxy.

| Piece | Where |
|---|---|
| Workflow | [`.github/workflows/ai-pr-review.yaml`](../../../.github/workflows/ai-pr-review.yaml) |
| Review standards handed to the model | [`.github/ai-review-rules.md`](../../../.github/ai-review-rules.md) |
| Model alias | [`kubernetes/apps/base/ai/litellm/app/models/pr-review-local.yaml`](../../../kubernetes/apps/base/ai/litellm/app/models/pr-review-local.yaml) |
| Consumer key | [`kubernetes/apps/base/ai/litellm/app/virtualkeys/ai-pr-review.yaml`](../../../kubernetes/apps/base/ai/litellm/app/virtualkeys/ai-pr-review.yaml) |
| Action pin | `misospace/pr-reviewer-action@54dfb1aa` (v2.2.1) |

Added 2026-08-29. Captain decisions: home-ops only, and **advisory comments only** as a
deliberate starting posture, so the output can be judged before it is given any teeth.

## 1. What it does, and what it structurally cannot do

It posts (and then edits in place) one managed comment per PR. That is the whole surface.

It **cannot** approve, request changes, or block a merge:

- `publish_mode: comment` publishes with `gh pr comment --edit-last --create-if-none`. The action
  only reaches `gh pr review` on the `review_comment` and `review_verdict` paths, so in this mode
  no native review of any kind can be submitted.
- `allow_approve` and `approve_forks` are false, so no approval path exists even if the mode were
  changed by accident.
- `fail_on_request_changes` is false, so a `request_changes` verdict in the comment text never
  reddens the check.
- The workflow is **not** a required status check and must not become one - see
  [`docs/branch-protection.md`](../../branch-protection.md). "Allow GitHub Actions to create and
  approve pull requests" is not needed for advisory mode and stays off.

It also **cannot spend money**. `ai_model` is `pr-review-local`, a zero-priced local alias with no
cloud fallback, and the `ai-pr-review` virtual key allow-lists that one model and nothing else.
`review_routing_mode` is left `off`, so there is no smart-model escalation path to bound - which
matters because this fires on every PR in a repo with heavy Renovate traffic.

## 2. Fork PRs: excluded, deliberately

The job carries the same unconditional guard as `validate.yaml`, `image-pull.yaml`, `flate.yaml`
and `terraform-diff.yaml`:

```yaml
if: github.event.pull_request.head.repo.full_name == github.repository && !github.event.pull_request.draft
```

Three independent reasons, any one of which is sufficient:

1. This repo is public and this job lands on the in-cluster runner. A fork PR must never execute on
   a runner that can reach cluster-internal services.
2. On a fork PR `GITHUB_TOKEN` is read-only regardless of the `permissions:` block, so the comment
   publish could not work anyway.
3. `pull-requests: write` on a workflow reachable from fork PRs is a textbook privilege-escalation
   shape.

`pull_request_target` is deliberately **not** used - it would run trusted code against untrusted PR
content, which is the exact anti-pattern this guard exists to avoid.

Consequence, stated plainly: **PRs from forks get no AI review.** A maintainer who wants one must
re-run from a same-repo branch. The action's own fork gating (`tool_enable_for_forks`,
`evidence_enable_for_forks`, `linear_enable_for_forks`) is therefore never reached; it is pinned to
its safe defaults anyway.

**Renovate is unaffected and is the highest-value target here.** Renovate pushes branches to this
repo rather than a fork, so its PRs are same-repo and are reviewed. They otherwise receive no review
at all. Nothing in the workflow filters by author, label or path.

## 3. Why a dedicated non-thinking model alias

**Do not repoint `ai_model` at `chat-local`.** Qwen3.6-35B-A3B is a reasoning model: it returns its
chain of thought in `reasoning_content` and leaves `content` **empty**. The action parses
`choices[0].message.content`, so it would receive an empty string, fail its JSON parse, burn its
retry budget and post nothing.

This is the same trap [`auto-router.md`](auto-router.md) documents for the D3 classifier, and it has
the same fix: declare `extra_body.chat_template_kwargs.enable_thinking: false` in `litellm_params`.

Measured live 2026-08-29 through the proxy, identical prompt, `response_format: json_object`,
`max_tokens: 800`:

| Alias | Completion tokens | `content` | Result |
|---|---|---|---|
| `chat-local` (thinking on) | 800 (all reasoning) | `""` | JSON parse **fails** |
| `enable_thinking: false` | 78, ~1s | full JSON | parses to `{"verdict","review_markdown"}` |

Passing `chat_template_kwargs` per **request** does not work - the same call through the proxy came
back byte-identical, still with empty content. The flag only takes effect declared on the model.

`qwen3.6-35b-a3b-classifier` was not reused: it carries `num_retries: 0` because its timeout rides
in front of every routed `auto` request, and its PrometheusRule alerts select on its `modelName`.
PR-review traffic would pollute that series and inherit a retry posture chosen for another job.

## 4. Why `model_context_tokens` is 65536 and not the served 262144

The served window really is 262144 (llama.cpp `--ctx-size` in the vllm HelmRelease, confirmed live
as `n_ctx: 262144` on `/props`). Setting that here would be actively harmful.

The action derives its byte budgets as `(ctx - ai_max_tokens - 2000) * 3`. At 262144 that authorizes
a ~756kB prompt. Measured on the B70 on 2026-08-29:

- prefill **~310-350 tok/s**
- decode **~11 tok/s** (decode, not prefill, is the bottleneck)

So a full 262144-token prompt is **~14 minutes of prefill alone**, before a single output token, on
a card shared with hermes, opencode, repo-wiki and the D3 classifier.

Undershooting the real window is safe - the action truncates and reports it in the step summary.
Overshooting is what produces the context-overflow parse failures upstream warns about.

65536 was chosen against this repo's actual PR sizes (last 40 merged PRs): **p50 24kB, p75 59kB,
p90 117kB, max 160kB**. It yields a ~180kB corpus / ~108kB diff budget, so roughly 85% of PRs are
reviewed with no truncation, while one review is bounded to ~6.5 minutes of GPU.

Measured end-to-end for scale, through the non-thinking alias with `json_object`:

| Diff | Prompt tok | Completion tok | Wall |
|---|---|---|---|
| 6.5kB | 1772 | 211 | 29s |
| 26.7kB (this repo's median) | 8957 | 979 | 118s |

If reviews feel too slow, the cheapest lever is `review_verbosity: concise` - decode dominates, so
shortening the output matters more than shrinking the prompt.

## 5. Why a curated standards file instead of `AGENTS.md`

The action `cat`s the standards file whole and then hard-truncates it to the **first 16000 bytes**
(`std_cap` in `scripts/sections/corpus.sh`, head truncation). `AGENTS.md` is ~70kB, so it would
arrive cut mid-sentence inside `UNIQUE STYLES`, with three of eight top-level sections missing -
including all of `NOTES`, where most of this repo's load-bearing review knowledge lives.

`.github/ai-review-rules.md` is the review-relevant distillation, kept under that cap on purpose
(~11kB, leaving room to grow). `standards_file` names it explicitly, because the default candidate
order would otherwise pick `AGENTS.md` first.

`AGENTS.md` stays authoritative for humans and for agents doing the work. When the two disagree,
`AGENTS.md` wins, and the review rules file should be corrected.

## 6. The credential, and its two-step rotation

The key is the `ai-pr-review` `LiteLLMVirtualKey` (D4, one file per consumer). It is minted by the
litellm-operator and mirrored to the 1Password item `litellm-consumer-ai-pr-review` by its
`PushSecret`.

**Unlike every other consumer, this one also needs a hand-set GitHub Actions repository secret,
`LITELLM_PR_REVIEW_KEY`.** The ARC runner runs with `automountServiceAccountToken: false` and no
ServiceAccount, so it has no Kubernetes API access and cannot read the minted Secret. The PushSecret
is what makes the copy maintainable: it gives the value a durable home to re-copy from.

### Why not fetch it from 1Password Connect at run time

The runner *can* reach 1Password Connect, and `OP_CONNECT_TOKEN` already exists for
`terraform-diff.yaml`, so this was considered and rejected. That token reads **every item in the
`Automation` vault** ([`docs/authentik/terraform.md`](../../authentik/terraform.md) section 9 says
so in those words), including the `authentik-terraform` state-backend and Authentik API
credentials. Handing it to a workflow whose entire job is to feed attacker-influenceable PR diffs to
an LLM would widen that token's blast radius to the cluster's live SSO tooling in order to save one
manual secret. A dedicated GitHub secret holding only this key is both simpler and strictly
narrower: the worst it can do is spend free local GPU time.

### Setup (one time)

The workflow ships inert and safe: with the secret unset it emits a `::warning::` and skips, and the
check stays green. It activates itself once the secret exists, with no code change.

1. Merge, and let Flux reconcile `ai/litellm` so the operator mints the key.
2. Read it: `kubectl -n ai get secret litellm-key-ai-pr-review -o jsonpath='{.data.key}' | base64 -d`
   (or take it from the 1Password item `litellm-consumer-ai-pr-review`).
3. Set it as the repository secret `LITELLM_PR_REVIEW_KEY`.

### Rotation - this is the part that breaks silently

Rotating this key has **two** steps, and skipping the second leaves the reviewer authenticating with
a dead key: every run posts "AI review could not run" while the cluster side looks perfectly
healthy.

1. Cluster side: the operator re-mints and the PushSecret updates 1Password.
2. **GitHub side: update `LITELLM_PR_REVIEW_KEY` from the new value.**

Note the alias-collision trap in `AGENTS.md`: LiteLLM key aliases are globally unique and the
operator has no adopt-by-alias path, so if a key named `ai-pr-review` already exists at the proxy
when the CR is first reconciled, the CR sits `Ready=False`/`GenerateFailed` forever while the proxy
looks healthy. Delete the colliding key through the admin API first.

## 7. Verification evidence (2026-08-29)

Re-run **2026-08-29T23:39:06Z** from inside the cluster by exec-ing into a live runner pod on the
target scale set - pod
`gha-runner-scale-set-aviator-coding-home-ops-lj6tl-runner-dbx8w` on
`gha-runner-scale-set-aviator-coding-home-ops` - against
`http://litellm.ai.svc.cluster.local:4000` over in-cluster Service DNS. That is the only place that
exercises the network path the workflow actually uses.
`https://litellm.sklab.dev/health/liveliness` (the internal ingress route) was **not** used as a
substitute: it exercises a different path than the in-cluster Service DNS the workflow uses.

The final-named objects were deliberately **not** created early. `pr-review-local` does not exist
on the proxy yet (it currently serves 34 models), and creating it would roll the live proxy for no
added evidence. Non-thinking behaviour was therefore exercised through
`qwen3.6-35b-a3b-classifier`, which carries the identical
`extra_body.chat_template_kwargs.enable_thinking: false` configuration that `pr-review-local`
declares. The checks below prove the mechanism and the path; they do **not** claim the final-named
objects were live-tested.

1. `GET /health/liveliness` -> **HTTP 200**, body `I'm alive!`.
2. `GET /v1/models` with the virtual key -> exactly
   `['chat-local', 'qwen3.6-35b-a3b-classifier']`, confirming the allow-list is what the key grants.
3. `POST /v1/chat/completions` with `response_format: json_object` against the non-thinking alias
   -> served model `qwen3.6-35b-a3b-classifier`, `completion_tokens` 78, `reasoning_len` 0, content
   parsed cleanly to JSON with keys exactly `["verdict","review_markdown"]`, `verdict`
   `"approved"`, `review_markdown` 249 chars.
4. **CONTROL (new evidence this re-run adds)**: same prompt and same settings against `chat-local`
   with thinking ON -> `completion_tokens` 600, `reasoning_len` 2231, `content_len` 0, JSON parse
   fails with `Expecting value: line 1 column 1 (char 0)`. This reproduces the empty-content trap
   in-cluster, in the same run as check 3, and is the direct proof that the dedicated non-thinking
   `pr-review-local` alias is required rather than a preference. The earlier session did not have
   this control in this form.
5. `POST /v1/chat/completions` for `claude-sonnet-5` on the same key -> **HTTP 403**, error type
   `key_model_access_denied`, message
   `key not allowed to access model. This key can only access models=['chat-local', 'qwen3.6-35b-a3b-classifier']. Tried to access claude-sonnet-5`.
   This is the proof the reviewer cannot reach a paid model.

Throwaway key handling: the checks used a temporary `LiteLLMVirtualKey` under the alias
`ai-pr-review-recheck` - deliberately distinct from **both** the final `ai-pr-review` and the
earlier `ai-pr-review-preflight` - so that a failed cleanup could not collide with the real key
alias (LiteLLM key aliases are globally unique and the operator has no adopt-by-alias path). It was
deleted immediately afterwards and the deletion verified at **2026-08-29T23:41:09Z**: the CR is
gone, the Secret `litellm-key-ai-pr-review-recheck` is gone, the key itself is rejected by the proxy
with **HTTP 401** `token_not_found_in_db`, and the remaining virtual keys are exactly the original
six (`claude-code-subscription`, `demo`, `ha-demo`, `opencode`, `repo-wiki`, `router-demo`) with no
residue.

Also verified declaratively: `task flux:test:all` passes (302 resources), `actionlint` reports
nothing beyond the known self-hosted runner-label notice that every workflow in this repo produces,
`scripts/ci/workflow-hardening-test.py` passes, all 31 action inputs used by the workflow exist in
`action.yml` at the pinned SHA, `scripts/ci/litellm-pr-reviewer-test.py` covers the captain-decision
contracts for this surface, and `scripts/ci/litellm-fallback-chain-test.py` treats `pr-review-local`
as terminal (no cloud fallback).

**Proven at runtime, on this very PR.** The workflow triggers on same-repo pull requests, so it ran
against its own PR before merge: run
[`33281811111`](https://github.com/Aviator-Coding/home-ops/actions/runs/33281811111) completed
**green on the in-cluster runner**, taking the credential-absent skip path - `Resolve Model
Credential` emitted the `LITELLM_PR_REVIEW_KEY` warning and the `AI Review` step was skipped, with
no comment posted. The graceful degradation described in section 6 is therefore observed behaviour,
not a design claim: this change can merge and sit inert without reddening a single check.

### What is NOT yet proven

The workflow itself has now run (above), but **no model-backed review has ever executed** - that
needs the `LITELLM_PR_REVIEW_KEY` secret, so every run so far has stopped at the skip path.
Specifically still unproven:

- The action's own behaviour on a real PR: diff collection, corpus assembly, the managed-comment
  publish, and the incremental-review path on a second push.
- `pr-review-local` and `ai-pr-review` under their final names. The mechanism is proven, but those
  exact objects do not exist until Flux reconciles them - creating them early would have rolled the
  live proxy for no added evidence.
- Real review quality on real home-ops PRs, which is the entire reason the captain chose an
  advisory-only starting posture.

## 8. Turning it off

Delete or disable `.github/workflows/ai-pr-review.yaml`. Nothing else depends on it. The virtual key
and model alias are inert on their own - the alias is one more zero-priced entry in the catalog, and
the key grants access to nothing else.
