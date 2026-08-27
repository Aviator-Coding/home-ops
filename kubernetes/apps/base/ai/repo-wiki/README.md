# Repo Wiki

[mkdocs-material](https://squidfunk.github.io/mkdocs-material/) serving
AI-generated per-repo documentation, adapted 2026-08-26 from
`joryirving/home-ops` `kubernetes/apps/base/llm/repo-wiki/` (commit `3d3b700`)
per captain request. A 12-hourly CronJob (`generate.py`) walks the repos in
[`app/resources/repos.txt`](app/resources/repos.txt), plans a small set of
wiki pages per repo, and writes each page through our in-cluster LiteLLM
governance proxy (`http://litellm.ai.svc.cluster.local:4000/v1`) - never a
direct model provider key. Generated pages are committed into a local git
repo on the app's PVC, which mkdocs serves `--dirty` (incremental rebuild).

## What changed vs the reference

- **Namespace/path**: reference ships under its own `llm` namespace at
  `kubernetes/apps/base/llm/repo-wiki/`; this repo's convention is one
  namespace directory per app, so this lives at `kubernetes/apps/base/ai/repo-wiki/`
  in the existing `ai` namespace instead.
- **Chart source**: reference pins its own per-app `OCIRepository` for
  `app-template`; this repo already shares one pinned `app-template`
  `OCIRepository` (`kubernetes/components/common/repos/app-template/`) via
  `chartRef`, so no per-app `OCIRepository` was added.
- **Config delivery**: reference inlines `repos.txt`/`mkdocs.yml`/`generate.py`
  as a hand-written `ConfigMap` resource; this repo's convention
  (`kustomization.yaml`'s `configMapGenerator`, e.g. `ai/hermes`,
  `monitoring/kromgo`) generates the `ConfigMap` from real files under
  `app/resources/`, which is what's used here.
- **`repos.txt`**: replaced the reference's own repo list. The live set is
  only [`app/resources/repos.txt`](app/resources/repos.txt) (do not restate it
  here) - currently this account's repos plus selected upstream public repos
  under `joryirving` and `misospace`.
- **GITHUB_TOKEN**: reference uses its own `github-miso` 1Password item.
  No existing 1Password-sourced GitHub credential in this repo is read-only:
  `hermes`'s `HOMELAB_GH_TOKEN` is `public_repo` scope (read **and** write),
  Renovate/actions-runner use GitHub App credentials (broad, wrong shape). A
  new item is required - see Prerequisites below.
- **LiteLLM key**: reference reads a static `LITELLM_REPO_WIKI_API_KEY`
  property from 1Password item `litellm`. This repo mints the key from a
  `LiteLLMVirtualKey` CR (`kubernetes/apps/base/ai/litellm/app/virtualkeys/repo-wiki.yaml`,
  captain decision D4's pattern), the same mechanism `demo`/`router-demo` use
  post-migration (#1455) - no manual key material to create.
- **Model**: `qwen3.6-35b-a3b` (the local default), matching the reference's
  own direct-local choice; no reason found to pick differently.
- **Budget knobs tightened**: `MAX_REPOS_PER_RUN` 2->1 and `MAX_PAGES_PER_REPO`
  20->8, `PAGE_CTX_CHARS` 120000->60000, to keep per-tick governance spend
  small (see the `LiteLLMVirtualKey` comment for per-repo cost and backfill
  math against the current `repos.txt` length).
- **Timezone**: `America/New_York` (this cluster's convention, e.g.
  `ai/hermes`'s `CONFIG_TIMEZONE`), not the reference's `America/Edmonton`.
- **Persistence**: reference has no backup for this PVC. This repo's
  convention backs up stateful app data with VolSync; added here even though
  the wiki content is regenerable, since a full backfill costs real governance
  budget and generation time to reproduce.

## Prerequisites (before first sync)

1Password item **`repo-wiki`** (`onepassword` ClusterSecretStore vault):

| Field | How to generate |
| --- | --- |
| `GITHUB_TOKEN` | A GitHub fine-grained PAT with **"Public Repositories (read-only)"** account access - no repository selection needed, since it never grants access beyond what's already public, and it raises the GitHub API rate limit for `generate.py`'s repo listing/tarball fetches. Do **not** reuse `hermes`'s `HOMELAB_GH_TOKEN` (that PAT is `public_repo` scope - read **and** write - a broader grant than this read-only generator needs). |

> Until the `repo-wiki` item exists, this app's `ExternalSecret` reports
> `SecretSyncedError` and the generator CronJob's pods fail at
> `Init:CreateContainerConfigError`. mkdocs itself starts fine either way -
> it just serves an empty wiki until the first successful generation run.

No 1Password item is needed for the LiteLLM key: `litellm-consumer-repo-wiki`
is created automatically by the `PushSecret` paired with this app's
`LiteLLMVirtualKey` CR the same way `litellm-consumer-demo` and
`litellm-consumer-router-demo` already are.
