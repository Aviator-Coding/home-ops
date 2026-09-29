# Repo wiki

mkdocs of generated pages. A CronJob writes them through LiteLLM
(`chat-local` on virtual key `repo-wiki`) and commits them onto the PVC.
Skill `ai-stack` references/consumers.md. The repo list is only
`app/resources/repos.txt`.

Those resource files are `configMapGenerator` input. A byte change rolls
the pod.

## Prerequisites

1Password item `repo-wiki`, field `GITHUB_TOKEN`: a fine-grained PAT with
account access "Public Repositories (read-only)". Do not reuse
`HOMELAB_GH_TOKEN`. That token can push.

Until the item exists, the ExternalSecret reports `SecretSyncedError`
and the generator sits in `CreateContainerConfigError`. mkdocs still
serves, with an empty site.

The LiteLLM key is minted by the `repo-wiki` `LiteLLMVirtualKey` and its
PushSecret. No key material to paste. The model string and that key's
allow-list have to move together.
