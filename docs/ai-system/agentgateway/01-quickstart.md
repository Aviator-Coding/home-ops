# Quick Start Guide

> **Deploy AgentGateway in Kubernetes and send your first LLM request in under 5 minutes.**

## Prerequisites

Before you begin, ensure you have:

- A functioning Kubernetes cluster
- `kubectl` installed and configured
- `helm` installed
- An API key from at least one LLM provider (OpenAI, Anthropic, etc.)

---

## Step 1: Deploy Gateway API CRDs

Install the Kubernetes Gateway API Custom Resource Definitions:

```bash
kubectl apply -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/standard-install.yaml
```

For experimental features (optional):

```bash
kubectl apply --server-side -f https://github.com/kubernetes-sigs/gateway-api/releases/download/v1.4.0/experimental-install.yaml
```

---

## Step 2: Install kgateway CRDs

Deploy the kgateway Custom Resource Definitions via Helm:

```bash
helm upgrade -i kgateway-crds oci://cr.kgateway.dev/kgateway-dev/charts/kgateway-crds \
  --create-namespace \
  --namespace kgateway-system \
  --version v2.1.2
```

---

## Step 3: Deploy the Control Plane

Install the kgateway control plane with AgentGateway enabled:

```bash
helm upgrade -i kgateway oci://cr.kgateway.dev/kgateway-dev/charts/kgateway \
  --namespace kgateway-system \
  --version v2.1.2 \
  --set agentgateway.enabled=true \
  --set controller.image.pullPolicy=Always
```

---

## Step 4: Verify Deployment

Check that all pods are running:

```bash
kubectl get pods -n kgateway-system
```

Expected output:
```
NAME                        READY   STATUS    RESTARTS   AGE
kgateway-xxxxxxxxxx-xxxxx   1/1     Running   0          30s
```

Verify the GatewayClass was created:

```bash
kubectl get gatewayclass agentgateway
```

Expected output:
```
NAME           CONTROLLER                ACCEPTED   AGE
agentgateway   kgateway.dev/kgateway     True       30s
```

---

## Step 5: Create a Gateway

Deploy a Gateway resource using the `agentgateway` GatewayClass:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: ai-system
  labels:
    app: agentgateway
spec:
  gatewayClassName: agentgateway
  listeners:
    - name: http
      protocol: HTTP
      port: 8080
      allowedRoutes:
        namespaces:
          from: All
```

Apply and wait for ready:

```bash
kubectl apply -f gateway.yaml
kubectl get gateway agentgateway -n ai-system -w
```

---

## Step 6: Configure an LLM Provider

### Create an API Key Secret

```bash
export OPENAI_API_KEY="your-api-key-here"

kubectl create secret generic openai-secret \
  -n ai-system \
  --from-literal=Authorization=$OPENAI_API_KEY
```

### Create a Backend

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: openai
  namespace: ai-system
spec:
  type: AI
  ai:
    llm:
      openai:
        authToken:
          kind: SecretRef
          secretRef:
            name: openai-secret
        model: "gpt-3.5-turbo"
```

### Create an HTTPRoute

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: openai
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
      namespace: ai-system
  rules:
  - matches:
    - path:
        type: PathPrefix
        value: /openai
    filters:
    - type: URLRewrite
      urlRewrite:
        path:
          type: ReplaceFullPath
          replaceFullPath: /v1/chat/completions
    backendRefs:
    - name: openai
      namespace: ai-system
      group: gateway.kgateway.dev
      kind: Backend
```

---

## Step 7: Test the Configuration

### Port Forward (Local Testing)

```bash
kubectl port-forward svc/agentgateway -n ai-system 8080:8080 &
```

### Send a Test Request

```bash
curl "localhost:8080/openai" \
  -H "content-type: application/json" \
  -d '{
    "model": "gpt-3.5-turbo",
    "messages": [
      {"role": "system", "content": "You are a helpful assistant."},
      {"role": "user", "content": "What is Kubernetes in one sentence?"}
    ]
  }' | jq
```

Expected response:
```json
{
  "id": "chatcmpl-xxxxx",
  "object": "chat.completion",
  "choices": [
    {
      "message": {
        "role": "assistant",
        "content": "Kubernetes is an open-source container orchestration platform..."
      }
    }
  ],
  "usage": {
    "prompt_tokens": 25,
    "completion_tokens": 30,
    "total_tokens": 55
  }
}
```

---

## Next Steps

- [Configure additional LLM providers](./04-llm-providers.md)
- [Set up MCP connectivity](./05-mcp-connectivity.md)
- [Add security with RBAC and External Secrets](./07-security.md)
- [Enable observability](./08-observability.md)

---

## Cleanup

Remove all deployed resources:

```bash
kubectl delete httproute openai -n ai-system
kubectl delete backend openai -n ai-system
kubectl delete secret openai-secret -n ai-system
kubectl delete gateway agentgateway -n ai-system
helm uninstall kgateway kgateway-crds -n kgateway-system
```

---

## References

- [kgateway Quickstart](https://kgateway.dev/docs/agentgateway/latest/quickstart/)
- [Gateway API Documentation](https://gateway-api.sigs.k8s.io/)

---

*See [02-installation.md](./02-installation.md) for Flux CD GitOps installation.*
