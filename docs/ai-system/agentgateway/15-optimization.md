# Cost and capacity

Replica counts, HPA tables, and GPT-3.5 price lists in the old page were generic. Live capacity and prices are GitOps.

## Controller / dataplane

- Controller: `replicaCount: 2`, PDB `minAvailable: 1`, hostname anti-affinity ([`helmrelease.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/helmrelease.yaml)).
- Three Gateway dataplanes (not one scaled Deployment named `agentgateway-proxy`).
- Resources: top-level `resources` requests 100m/128Mi, limits 500m/512Mi. `controller.resources` is ignored by the chart.

## Cost metering

[`rules/cost.yaml`](../../../kubernetes/apps/base/ai/agentgateway/app/rules/cost.yaml) is the GitOps replacement for LiteLLM's DB spend tracker. It multiplies `agentgateway_gen_ai_client_token_usage` by a USD-per-million-token table keyed on the **client `model` string**.

Maintain that file when adding a model. Models without a row still appear on the dashboard "Unpriced models" panel (`ai:gen_ai_tokens_unpriced:rate5m`) so nothing is silent.

OpenCode Go ids are priced at `$0` (flat $10/mo subscription; fee is not in token metrics). Pay-as-you-go OpenCode Zen is not priced (dormant; id collisions with direct providers).

Grafana dashboard: `kubernetes/apps/base/ai/agentgateway-dashboards/app/llm-cost.json`.

## Local vs cloud

- Local chat: exact id `qwen3.6-35b-a3b` -> `llm-chat-failover` (B70 llama.cpp, then kimi-k2.6).
- Do not send `qwen3.x-max/plus` to the local backend; those regexes are disjoint and go to OpenCode Go.
- Default embeddings hit `vllm-embed`, which is scaled to 0. Prefer OpenRouter slug embeddings (`openai/text-embedding-3-small` is in the catalog).

## What not to tune from the old doc

Ollama hostnames, GPT-3.5-turbo as the default test model, and "LiteLLM as the recommended proxy" do not apply.
