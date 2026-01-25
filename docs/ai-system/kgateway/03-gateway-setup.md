# Gateway Setup Guide

> **Configure Gateway resources to expose your agentgateway proxy for LLM, MCP, and agent traffic.**

## Prerequisites

- Kgateway control plane installed (see [02-installation.md](./02-installation.md))
- `kubectl` configured for your cluster

## Creating a Gateway

### Basic Gateway

Create a Gateway using the `agentgateway` GatewayClass:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: kgateway-system
spec:
  gatewayClassName: agentgateway
  listeners:
  - protocol: HTTP
    port: 80
    name: http
    allowedRoutes:
      namespaces:
        from: All
```

Apply:
```bash
kubectl apply -f gateway.yaml
```

### Gateway with TLS

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: kgateway-system
spec:
  gatewayClassName: agentgateway
  listeners:
  - protocol: HTTPS
    port: 443
    name: https
    tls:
      mode: Terminate
      certificateRefs:
      - name: gateway-tls-secret
        kind: Secret
    allowedRoutes:
      namespaces:
        from: All
  - protocol: HTTP
    port: 80
    name: http
    allowedRoutes:
      namespaces:
        from: All
```

Create TLS secret:
```bash
kubectl create secret tls gateway-tls-secret \
  --cert=tls.crt \
  --key=tls.key \
  -n kgateway-system
```

### Gateway with Multiple Listeners

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: kgateway-system
spec:
  gatewayClassName: agentgateway
  listeners:
  # LLM traffic
  - protocol: HTTP
    port: 80
    name: llm-http
    hostname: "llm.example.com"
    allowedRoutes:
      namespaces:
        from: All
  # MCP traffic
  - protocol: HTTP
    port: 8080
    name: mcp-http
    hostname: "mcp.example.com"
    allowedRoutes:
      namespaces:
        from: All
  # Agent traffic
  - protocol: HTTP
    port: 9090
    name: agent-http
    hostname: "agent.example.com"
    allowedRoutes:
      namespaces:
        from: All
```

## Verification Steps

### Check Gateway Status

```bash
kubectl get gateway agentgateway-proxy -n kgateway-system
```

Expected output:
```
NAME                 CLASS          ADDRESS         PROGRAMMED   AGE
agentgateway-proxy   agentgateway   203.0.113.10    True         2m
```

### Check Deployment

```bash
kubectl get deployment agentgateway-proxy -n kgateway-system
```

Expected output:
```
NAME                 READY   UP-TO-DATE   AVAILABLE   AGE
agentgateway-proxy   1/1     1            1           2m
```

### Check Service

```bash
kubectl get svc agentgateway-proxy -n kgateway-system
```

Expected output:
```
NAME                 TYPE           CLUSTER-IP     EXTERNAL-IP     PORT(S)        AGE
agentgateway-proxy   LoadBalancer   10.96.123.45   203.0.113.10    80:30123/TCP   2m
```

### Check Pod

```bash
kubectl get pods -n kgateway-system -l gateway=agentgateway-proxy
```

Expected output:
```
NAME                                  READY   STATUS    RESTARTS   AGE
agentgateway-proxy-xxxxxxxxxx-xxxxx   1/1     Running   0          2m
```

## Accessing the Gateway

### Cloud Provider (LoadBalancer)

Get the external IP or hostname:

```bash
export INGRESS_GW_ADDRESS=$(kubectl get svc -n kgateway-system \
  agentgateway-proxy -o jsonpath="{.status.loadBalancer.ingress[0]['hostname','ip']}")
echo "Gateway address: $INGRESS_GW_ADDRESS"
```

### Local Development (Port Forward)

```bash
kubectl port-forward deployment/agentgateway-proxy -n kgateway-system 8080:80
```

Access at `localhost:8080`.

### NodePort

If using NodePort service:

```bash
export NODE_PORT=$(kubectl get svc agentgateway-proxy -n kgateway-system \
  -o jsonpath='{.spec.ports[0].nodePort}')
export NODE_IP=$(kubectl get nodes -o jsonpath='{.items[0].status.addresses[?(@.type=="ExternalIP")].address}')
echo "Gateway address: $NODE_IP:$NODE_PORT"
```

## Gateway Parameters

Customize proxy deployment with GatewayParameters:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: agentgateway-params
  namespace: kgateway-system
spec:
  kube:
    agentgateway:
      # Custom agentgateway configuration
      customConfigMapName: agentgateway-config
    deployment:
      replicas: 3
      container:
        resources:
          requests:
            cpu: 500m
            memory: 256Mi
          limits:
            cpu: 2000m
            memory: 1Gi
        securityContext:
          runAsNonRoot: true
          runAsUser: 10101
```

Reference in Gateway:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: kgateway-system
spec:
  gatewayClassName: agentgateway
  infrastructure:
    parametersRef:
      group: gateway.kgateway.dev
      kind: GatewayParameters
      name: agentgateway-params
  listeners:
  - protocol: HTTP
    port: 80
    name: http
    allowedRoutes:
      namespaces:
        from: All
```

## Custom Configuration

### Using ConfigMap

Create a custom agentgateway configuration:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agentgateway-config
  namespace: kgateway-system
data:
  config.yaml: |
    binds:
    - name: main
      port: 3000
    listeners:
    - name: http-listener
      bind: main
      protocol: http
    routes:
    - name: health
      paths:
      - /health
      policies:
      - directResponse:
          status: 200
          body: "OK"
```

Reference via GatewayParameters:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: agentgateway-params
  namespace: kgateway-system
spec:
  kube:
    agentgateway:
      customConfigMapName: agentgateway-config
```

## Namespace-Scoped Routes

### Allow Routes from Same Namespace Only

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: kgateway-system
spec:
  gatewayClassName: agentgateway
  listeners:
  - protocol: HTTP
    port: 80
    name: http
    allowedRoutes:
      namespaces:
        from: Same
```

### Allow Routes from Specific Namespaces

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-proxy
  namespace: kgateway-system
spec:
  gatewayClassName: agentgateway
  listeners:
  - protocol: HTTP
    port: 80
    name: http
    allowedRoutes:
      namespaces:
        from: Selector
        selector:
          matchLabels:
            gateway-access: "true"
```

Label namespaces that should have access:
```bash
kubectl label namespace my-namespace gateway-access=true
```

## High Availability

### Multi-Replica Deployment

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: ha-params
  namespace: kgateway-system
spec:
  kube:
    deployment:
      replicas: 3
    podTemplate:
      spec:
        affinity:
          podAntiAffinity:
            preferredDuringSchedulingIgnoredDuringExecution:
            - weight: 100
              podAffinityTerm:
                labelSelector:
                  matchLabels:
                    gateway: agentgateway-proxy
                topologyKey: kubernetes.io/hostname
```

### Pod Disruption Budget

```yaml
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata:
  name: agentgateway-proxy-pdb
  namespace: kgateway-system
spec:
  minAvailable: 2
  selector:
    matchLabels:
      gateway: agentgateway-proxy
```

## Troubleshooting

### Gateway Not Getting Address

Check controller logs:
```bash
kubectl logs -n kgateway-system deployment/kgateway --tail=100
```

Check events:
```bash
kubectl describe gateway agentgateway-proxy -n kgateway-system
```

### Service Type Issues

If LoadBalancer is pending, check cloud provider configuration:
```bash
kubectl describe svc agentgateway-proxy -n kgateway-system
```

For local clusters, use NodePort or port-forward.

### Proxy Pod CrashLoopBackOff

Check pod logs:
```bash
kubectl logs -n kgateway-system -l gateway=agentgateway-proxy --tail=100
```

Check for configuration errors:
```bash
kubectl describe pod -n kgateway-system -l gateway=agentgateway-proxy
```

---

*See [04-llm-providers.md](./04-llm-providers.md) for configuring LLM provider backends.*
