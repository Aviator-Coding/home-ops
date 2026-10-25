# AI stack: model naming

Subsystem detail lives in skills `ai-stack`, `litellm-proxy`, `hermes-agent`, `agentgateway` and `b70-llm-serving` (`.agents/skills/<name>/SKILL.md`). This page is the naming rule only.

## Two kinds of name

| Kind | Example | Where it is allowed |
|---|---|---|
| **Real model name** | `Qwen3.8-35B-A3B-Distill-MTP-APEX-I-MiniPlus-V2.1-Abliterated` | Only as `${CHAT_MODEL_ID}`. Never typed literally. |
| **Role alias** | `chat-local` | Everywhere a consumer picks a model. |

The real name is defined once: ConfigMap `chat-model`, key `CHAT_MODEL_ID` (`kubernetes/apps/main/ai/chat-model.yaml`). Flux substitutes it (`postBuild.substituteFrom`) into the `vllm`, `litellm` and `agentgateway` Kustomizations:

- the llama-server `--alias` (vllm HelmRelease) and the ServiceMonitor `model` label
- `params.model: openai/${CHAT_MODEL_ID}` in the local LiteLLM CRs
- the agentgateway `vllm` backend provider model and the `root` field of the `chat-local` entry in `/v1/models`

## Rules

1. **Consumers use `chat-local`, never the real name.** Hermes `model.default`, Linkwarden `OPENAI_MODEL`, agentgateway local routes, LiteLLM tiers and fallbacks. A model swap must not touch them.
2. **`CHAT_MODEL_ID` is one token with no `/`.** A slash matches agentgateway's OpenRouter vendor-slug route and misroutes the request. No `org/` prefix, no `-GGUF` suffix.
3. **Do not add a literal model name to a new file.** Use `${CHAT_MODEL_ID}` where the real name is needed, `chat-local` everywhere else. `scripts/ci/chat-model-id-test.py` guards the wiring.
4. **The five local LiteLLM aliases share one backend**, each with one distinct property: `chat-local` (zero price, no cloud fallback), `chat-ha` (cloud fallback), `chat-local-classifier` and `pr-review-local` (thinking off), `demo-priced-local` (synthetic prices, `demo` key only). Detail: skill `litellm-proxy`, `references/model-catalog.md`.

## Swapping the chat model

1. Change the `-hf` repo in the vllm HelmRelease.
2. Change `CHAT_MODEL_ID` in `chat-model.yaml`.
3. Merge. Chat restarts once (Recreate, about 4 minutes).

Full steps and the verification list: skill `b70-llm-serving`, `references/flags.md` ("Swapping the chat model").

## Side effects of a swap

- Prometheus series keyed on the `model` label split once at the change.
- Anything calling the old real name directly gets a 404. Callers using `chat-local` are unaffected.
- A Kustomization that reconciles before the ConfigMap exists fails substitution and retries. It cannot apply a blank alias.

## Verify

```bash
kubectl -n ai get cm chat-model -o jsonpath='{.data.CHAT_MODEL_ID}'
kubectl -n ai get deploy vllm -o jsonpath='{.spec.template.spec.containers[0].args}' | grep -A1 alias
# gateway: chat-local with root = real name
kubectl -n ai port-forward svc/internal-noauth 18080:80 &
curl -s localhost:18080/v1/models | jq '.data[] | select(.id=="chat-local")'
```
