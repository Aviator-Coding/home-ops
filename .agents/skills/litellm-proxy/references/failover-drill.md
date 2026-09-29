# Failover drill (post-merge proof of a fallback change)

Proves a fallback chain against a throwaway dead-backend model and a
throwaway key. **Never degrade `vllm-app` to force a failover**: it serves
real traffic and a restart costs minutes of GGUF reload. With a local
fallback target the drill spends $0; a cloud leg costs ~$0.00003 per
`max_tokens: 1` call. Run it only with a go-ahead; it edits live objects.

## 0. Suspend Flux

```bash
flux suspend ks litellm -n ai   # otherwise the next reconcile reverts the probe
```

## 1. Probe model and a key scoped to it only

```bash
kubectl apply -f - <<'EOF'
apiVersion: litellm.home-operations.com/v1alpha1
kind: LiteLLMModel
metadata: {name: fallback-probe, namespace: ai}
spec:
  modelName: fallback-probe
  proxyRef: litellm
  params:
    model: openai/probe-dead
    apiBase: http://fallback-probe-dead.ai.svc.cluster.local:9/v1  # nothing listens
    apiKey: "not-needed"
    additional: {num_retries: 0, timeout: 5}
---
apiVersion: litellm.home-operations.com/v1alpha1
kind: LiteLLMVirtualKey
metadata: {name: fallback-probe-key, namespace: ai}
spec:
  proxyRef: litellm
  keyAlias: fallback-probe-key          # must be unused in the proxy DB
  secretName: litellm-key-fallback-probe
  secretKey: key
  models: [fallback-probe]              # deliberately NOT the fallback target
  maxBudget: "0.20"
  budgetDuration: 30d
  rpmLimit: 20
  tpmLimit: 20000
EOF
```

## 2. Point a fallback at it (local target = free)

```bash
kubectl -n ai patch litellmproxy litellm --type=merge -p \
  '{"spec":{"routerSettings":{"routing_strategy":"simple-shuffle","fallbacks":[{"fallback-probe":["qwen3.6-35b-a3b"]}]}}}'
kubectl -n ai rollout status deploy/litellm --timeout=180s
```

## 3. Exercise it

```bash
kubectl -n ai port-forward svc/litellm 4000:4000 &
PK=$(kubectl -n ai get secret litellm-key-fallback-probe -o jsonpath='{.data.key}' | base64 -d)
for i in $(seq 1 8); do
  curl -s localhost:4000/v1/chat/completions -H "Authorization: Bearer $PK" \
    -H 'Content-Type: application/json' \
    -d '{"model":"fallback-probe","messages":[{"role":"user","content":"say ok"}],"max_tokens":4}' \
    | python3 -c 'import sys,json;print("served by:",json.load(sys.stdin).get("model"))'
  sleep 18   # >1 scrape interval apart so rate() sees it
done
```

Expect `served by: qwen3.6-35b-a3b`, HTTP 200, every time.

**3b. The `auto` leg (optional).** Clone `auto` as `auto-probe` with
`complexity_router_default_model`, all four `tiers`, the classifier model and
`default_model` all set to `fallback-probe`; patch
`routerSettings.fallbacks` to `[{"auto-probe":["qwen3.6-35b-a3b"]}]`; add
`auto-probe` to the probe key's `models`; call `model: auto-probe`. A local
answer proves auto-router failures reach the fallback layer. An HTTP 500
would mean the `auto:` chain entries are decorative and should be removed.

## 4. Context-overflow string (optional)

Send a prompt comfortably over 262,144 tokens to the real local alias with the
master key; it is refused in about a second:

```bash
python3 -c 'import json;json.dump({"model":"qwen3.6-35b-a3b","messages":[{"role":"user","content":"alpha "*400000}],"max_tokens":1},open("/tmp/big.json","w"))'
MK=$(kubectl -n ai get secret litellm-secret -o jsonpath='{.data.LITELLM_MASTER_KEY}' | base64 -d)
curl -s -m 900 localhost:4000/v1/chat/completions -H "Authorization: Bearer $MK" \
  -H 'Content-Type: application/json' --data-binary @/tmp/big.json | head -c 400
```

The body must contain `exceeds the available context size`. If it does not,
`context_window_fallbacks` is inert and [fallbacks.md](fallbacks.md) must be
corrected.

## 5. Check the alerts in Prometheus, not the YAML

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090:9090 &
q(){ curl -s --get --data-urlencode "query=$1" localhost:9090/api/v1/query | python3 -m json.tool | head -30; }
q 'sum by (requested_model, fallback_model) (rate(litellm_deployment_successful_fallbacks_total[15m])) > 0'
q '(litellm_deployment_state >= 1) and on (model_id) (sum by (model_id) (rate(litellm_deployment_failure_responses_total[15m])) > 0)'
```

For `LiteLLMFallbackChainExhausted`, point the fallback at a second dead
model, fire 3 requests, expect HTTP 500 and
`litellm_deployment_failed_fallbacks_total > 0`.

## 6. Tear down (do not skip)

```bash
kubectl -n ai delete litellmvirtualkey fallback-probe-key --ignore-not-found
kubectl -n ai delete litellmmodel fallback-probe fallback-probe-2 auto-probe --ignore-not-found
kubectl -n ai delete secret litellm-key-fallback-probe --ignore-not-found
flux resume ks litellm -n ai   # one immediate reconcile back to Git
kubectl -n ai get cm litellm-config -o jsonpath='{.data.config\.yaml}' | grep -A12 router_settings
```

The last command must show the committed chains, not the probe.
