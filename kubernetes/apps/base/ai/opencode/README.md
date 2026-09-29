# OpenCode

Internal coding workspace. Authentik ext-auth on `envoy-internal` only.
Skill `ai-stack` references/consumers.md.

LLM calls go to LiteLLM
(`http://litellm.ai.svc.cluster.local:4000/v1`) with virtual key
`litellm-consumer-opencode`. Default model `litellm/auto`. Never a
provider key. Skill `litellm-proxy`.

`GITHUB_TOKEN` reuses the `hermes` 1Password item field
`HOMELAB_GH_TOKEN` (`public_repo`, read and write). No new item.

Namespace `ai` is already on the Authentik ReferenceGrant. A new
hostname still needs a real login after merge before treating the route
as covered. Skill `networking` for that inference. Do not add a second
basic-auth API route.
