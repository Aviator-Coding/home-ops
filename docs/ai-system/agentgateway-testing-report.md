# AgentGateway Testing Report

**Date:** 2026-03-31
**Chart Version:** v2.3.0-main (kgateway-dev)
**Namespace:** ai-system

## Summary

Comprehensive verification of all 14 AgentGateway backends across 10 providers. Testing validated routing, TLS, authentication injection, and path handling through the `internal-noauth` gateway at `10.50.0.28`.

## Issues Found and Fixed

### 1. Gemini Naming Inconsistency

**Commit:** `13da2784`

- Backend was named `google` but HTTPRoute path was `/gemini`
- Renamed all resources (ExternalSecret, AgentgatewayBackend, HTTPRoute) from `google` to `gemini`
- Maintains consistent naming convention: backend name = route path prefix

### 2. Anthropic Backend Consolidation

**Commit:** `13da2784`

- 5 separate files (`anthropic.yaml`, `anthropic-haiku.yaml`, `anthropic-opus.yaml`, `anthropic-opus-4-6.yaml`, `anthropic-sonnet-4-6.yaml`) consolidated into single `anthropic.yaml`
- All 5 backends shared the same API key (`ANTHROPIC_API_KEY_MAX`) but had 5 duplicate ExternalSecrets
- Consolidated to 1 shared ExternalSecret (`anthropic-secret`) referenced by all 5 backends
- Reduced ExternalSecret count from 15 to 11

### 3. Missing TLS for OpenAI-Compatible Backends

**Commit:** `ff30d9b5`

- 7 backends with custom `host:443` were sending plain HTTP to HTTPS ports
- Error: "The plain HTTP request was sent to HTTPS port"
- Fix: Added `policies.tls: {}` to enable TLS origination with system CA certificates
- Affected backends: groq, mistral, deepseek, xai, togetherai, openrouter, perplexity
- Native providers (openai, anthropic, gemini) handle TLS automatically

### 4. Path Prefix Not Stripped for Custom-Host Backends

**Commit:** `637afad6`

- Custom-host backends forwarded the full request path including the route prefix
- Example: `/groq/v1/chat/completions` sent to `api.groq.com/groq/v1/chat/completions` causing 404
- Native providers (openai, gemini, anthropic) handle path stripping internally
- Fix: Added `URLRewrite` filter with `ReplacePrefixMatch` to strip the prefix

### 5. Provider-Specific API Path Differences

**Commit:** `19777a2c`, `bdc7024d`

Not all OpenAI-compatible providers use the same base path:

| Provider | Expected Path | Default OpenAI Path | Fix |
|----------|--------------|---------------------|-----|
| Groq | `/openai/v1/chat/completions` | `/v1/chat/completions` | `replacePrefixMatch: /openai` |
| OpenRouter | `/api/v1/chat/completions` | `/v1/chat/completions` | `replacePrefixMatch: /api` |
| Perplexity | `/chat/completions` | `/v1/chat/completions` | Provider-level `path: /chat/completions` |
| Others | `/v1/chat/completions` | `/v1/chat/completions` | `replacePrefixMatch: /` (default) |

### 6. Anthropic Secret Key Name

**Commit:** `2aaea7b9` (revert of failed attempt)

- Attempted to change secret template key from `Authorization` to `x-api-key` for Anthropic
- AgentGateway requires the secret key to be `Authorization` and handles provider-specific header mapping internally
- Error when changed: `TranslationError: secret missing Authorization value`
- All provider secrets must use `Authorization` as the key name regardless of the provider's actual auth header

## Endpoint Test Results

Tested via busybox curl pod against `http://10.50.0.28` (internal-noauth gateway).

### Working Endpoints (200)

| Backend | Route | Model | Response |
|---------|-------|-------|----------|
| openai | `/openai/v1/chat/completions` | gpt-5.4 | Real completion |
| mistral | `/mistral/v1/chat/completions` | mistral-large-latest | Real completion |
| xai | `/xai/v1/chat/completions` | grok-4-1-fast | Real completion |
| deepseek | `/deepseek/v1/chat/completions` | deepseek-chat | Real completion |
| perplexity | `/perplexity/v1/chat/completions` | sonar-pro | Real completion |
| gemini | `/gemini/v1/chat/completions` | gemini-2.5-pro | Real completion |

### Working Routes (non-200 but reaching provider)

| Backend | Route | HTTP | Issue |
|---------|-------|------|-------|
| togetherai | `/togetherai/v1/chat/completions` | 400 | Model name validation (route works) |
| openrouter | `/openrouter/v1/chat/completions` | 400 | Min tokens validation (route works) |
| groq | `/groq/v1/chat/completions` | 000/timeout | Intermittent - may be rate limiting |
| anthropic | `/anthropic/v1/messages` | 401 | API key expired in 1Password |
| anthropic-haiku | `/anthropic-haiku/v1/messages` | 401 | Same credential issue |
| anthropic-opus | `/anthropic-opus/v1/messages` | 401 | Same |
| anthropic-opus-4-6 | `/anthropic-opus-4-6/v1/messages` | 401 | Same |
| anthropic-sonnet-4-6 | `/anthropic-sonnet-4-6/v1/messages` | 401 | Same |

## Linkwarden Integration

Linkwarden (selfhosted bookmark manager) was switched from Anthropic Claude Haiku to OpenRouter via AgentGateway:

- **Previous**: `ANTHROPIC_MODEL=claude-haiku-4-5-20251001` with expired `ANTHROPIC_API_KEY_MAX`
- **Current**: `OPENAI_MODEL=google/gemini-2.0-flash-lite-001` (free) via `CUSTOM_OPENAI_BASE_URL=http://internal-noauth.ai-system.svc.cluster.local/openrouter/v1`
- **API Key**: `OPENROUTER_API_KEY` from 1Password `ai-keys` vault
- **Status**: Working

## Outstanding Action Items

1. **Update Anthropic API Key**: Refresh `ANTHROPIC_API_KEY_MAX` in the 1Password `ai-keys` vault
2. **Investigate Groq Timeout**: The Groq backend intermittently times out - may be rate limiting or DNS resolution

## Architecture Notes

### Secret Management

- All secrets use `Authorization` as the key name - AgentGateway maps to provider-specific headers internally
- All API keys sourced from 1Password vault `ai-keys` via ClusterSecretStore `onepassword`
- Refresh interval: 1 hour

### Provider Types

| Type | Providers | TLS | Path Handling |
|------|-----------|-----|---------------|
| Native `openai` | OpenAI | Automatic | Automatic |
| Native `anthropic` | Anthropic (x5) | Automatic | Automatic |
| Native `gemini` | Gemini | Automatic | Automatic |
| Custom-host `openai` | Groq, Mistral, DeepSeek, xAI, TogetherAI, OpenRouter, Perplexity | Requires `policies.tls: {}` | Requires HTTPRoute `URLRewrite` filter |

### Gateway Topology

| Gateway | IP | Auth | Used By |
|---------|-----|------|---------|
| `internal` | 10.50.0.27 | OAuth2 (Authentik) | All backends |
| `internal-noauth` | 10.50.0.28 | None | All backends |
| `public` | 10.50.0.29 | OAuth2 (Authentik) | All backends |

### File Organization

After consolidation:

```
agentgateway/app/backends/
├── anthropic.yaml       # 5 Anthropic models (1 ExternalSecret + 5 backends + 5 routes)
├── openai.yaml          # OpenAI native
├── gemini.yaml          # Google Gemini native
├── groq.yaml            # Groq (OpenAI-compat, custom host)
├── mistral.yaml         # Mistral (OpenAI-compat, custom host)
├── deepseek.yaml        # DeepSeek (OpenAI-compat, custom host)
├── xai.yaml             # xAI/Grok (OpenAI-compat, custom host)
├── togetherai.yaml      # Together AI (OpenAI-compat, custom host)
├── openrouter.yaml      # OpenRouter (OpenAI-compat, custom host)
├── perplexity.yaml      # Perplexity (OpenAI-compat, custom host, non-standard path)
└── kustomization.yaml
```
