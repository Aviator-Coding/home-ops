# Gateway Setup

> **Configure Gateway resources for AgentGateway with HTTP, HTTPS, and TLS termination.**

## Overview

The Gateway resource defines the entry point for AI traffic. AgentGateway uses the Kubernetes Gateway API with the `agentgateway` GatewayClass.

---

## Basic HTTP Gateway

Minimal Gateway configuration for development:

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

---

## HTTPS Gateway with TLS Termination

Production Gateway with TLS using existing cert-manager certificate:

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
    - name: https
      protocol: HTTPS
      port: 443
      allowedRoutes:
        namespaces:
          from: All
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: sklab-dev-production-tls
```

---

## Home-Ops Cluster Gateway

Complete Gateway configuration for the Home-Ops cluster:

```yaml
# kubernetes/apps/ai-system/agentgateway/app/gateway.yaml
---
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: ai-system
  labels:
    app: agentgateway
  annotations:
    # External DNS for DNS record creation
    external-dns.alpha.kubernetes.io/target: "ai.${SECRET_DOMAIN}"
    # Gatus health check configuration
    gatus.home-operations.com/endpoint: |-
      group: ai-system
      guarded: true
      ui:
        hide-hostname: true
        hide-url: true
spec:
  gatewayClassName: agentgateway
  infrastructure:
    annotations:
      # Cilium LoadBalancer IP assignment
      lbipam.cilium.io/ips: "10.50.0.30"
      external-dns.alpha.kubernetes.io/hostname: "ai.${SECRET_DOMAIN}"
  listeners:
    - name: http
      protocol: HTTP
      port: 80
      allowedRoutes:
        namespaces:
          from: Same
    - name: https
      protocol: HTTPS
      port: 443
      allowedRoutes:
        namespaces:
          from: All
      tls:
        mode: Terminate
        certificateRefs:
          - kind: Secret
            name: sklab-dev-production-tls
```

---

## GatewayParameters for Custom Configuration

Configure proxy deployment settings:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: agentgateway-params
  namespace: ai-system
spec:
  kube:
    agentgateway:
      enabled: true
      # Reference custom ConfigMap for advanced configuration
      customConfigMapName: agentgateway-config

    deployment:
      replicas: 2

    podTemplate:
      spec:
        containers:
        - name: agentgateway
          resources:
            requests:
              cpu: "100m"
              memory: "128Mi"
            limits:
              cpu: "500m"
              memory: "512Mi"

    service:
      type: LoadBalancer
      annotations:
        lbipam.cilium.io/ips: "10.50.0.30"
```

Reference GatewayParameters in Gateway:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway
  namespace: ai-system
spec:
  gatewayClassName: agentgateway
  infrastructure:
    parametersRef:
      name: agentgateway-params
      group: gateway.kgateway.dev
      kind: GatewayParameters
  listeners:
    # ... listener configuration
```

---

## Custom AgentGateway Configuration

For advanced scenarios, use a ConfigMap:

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agentgateway-config
  namespace: ai-system
data:
  config.yaml: |
    binds:
    - address: 0.0.0.0
      port: 8080
      listeners:
      - name: http
        protocol: HTTP
        routes:
        - name: health-check
          matches:
          - path:
              pathPrefix: /healthz
          policies:
            directResponse:
              body: "OK"
              status: 200
```

---

## Listener Configuration Options

### Protocol Types

| Protocol | Port | Use Case |
|----------|------|----------|
| `HTTP` | 80, 8080 | Development, internal traffic |
| `HTTPS` | 443 | Production with TLS termination |
| `TLS` | 443 | TLS passthrough |

### Allowed Routes

Control which namespaces can attach routes:

```yaml
listeners:
  - name: https
    protocol: HTTPS
    port: 443
    allowedRoutes:
      namespaces:
        from: All  # Any namespace
        # OR
        from: Same  # Same namespace only
        # OR
        from: Selector
        selector:
          matchLabels:
            ai-workload: "true"
```

---

## HTTP Redirect

Redirect HTTP to HTTPS:

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: https-redirect
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
      sectionName: http
  rules:
    - filters:
        - type: RequestRedirect
          requestRedirect:
            scheme: https
            statusCode: 301
```

---

## Multiple Gateways

For environment separation (internal vs external):

### Internal Gateway

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-internal
  namespace: ai-system
spec:
  gatewayClassName: agentgateway
  infrastructure:
    annotations:
      lbipam.cilium.io/ips: "10.50.0.31"
  listeners:
    - name: http
      protocol: HTTP
      port: 8080
      allowedRoutes:
        namespaces:
          from: Selector
          selector:
            matchLabels:
              internal-ai: "true"
```

### External Gateway

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: Gateway
metadata:
  name: agentgateway-external
  namespace: ai-system
spec:
  gatewayClassName: agentgateway
  infrastructure:
    annotations:
      lbipam.cilium.io/ips: "10.50.0.32"
  listeners:
    - name: https
      protocol: HTTPS
      port: 443
      tls:
        certificateRefs:
          - name: sklab-dev-production-tls
      allowedRoutes:
        namespaces:
          from: Same
```

---

## Gateway Status

Check Gateway status:

```bash
kubectl get gateway agentgateway -n ai-system -o yaml
```

Key status fields:
- `addresses`: Assigned IP/hostname
- `conditions`: Gateway health
- `listeners`: Per-listener status

```yaml
status:
  addresses:
    - type: IPAddress
      value: 10.50.0.30
  conditions:
    - type: Accepted
      status: "True"
    - type: Programmed
      status: "True"
  listeners:
    - name: https
      attachedRoutes: 5
      conditions:
        - type: Accepted
          status: "True"
        - type: Programmed
          status: "True"
```

---

## Troubleshooting

### Gateway Not Accepting

```bash
# Check GatewayClass exists
kubectl get gatewayclass agentgateway

# Check controller logs
kubectl logs -n ai-system -l app.kubernetes.io/name=agentgateway
```

### No Address Assigned

```bash
# Check Cilium IPAM
kubectl get ippools -A

# Check service
kubectl get svc -n ai-system -l app=agentgateway
```

### TLS Issues

```bash
# Check certificate secret exists
kubectl get secret sklab-dev-production-tls -n ai-system

# Check certificate validity
kubectl get certificate -n ai-system
```

---

## References

- [Gateway API Specification](https://gateway-api.sigs.k8s.io/reference/spec/)
- [kgateway Gateway Setup](https://kgateway.dev/docs/agentgateway/latest/gateway/)
- [Cilium LoadBalancer IPAM](https://docs.cilium.io/en/stable/network/lb-ipam/)

---

*See [04-llm-providers.md](./04-llm-providers.md) for configuring LLM provider backends.*
