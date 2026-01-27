# Troubleshooting

> **Common issues and solutions for AgentGateway deployment and operation.**

## Quick Diagnostics

### Health Check Commands

```bash
# Check all AgentGateway resources
kubectl get gateway,httproute,backend,trafficpolicy -n ai-system

# Check pod status
kubectl get pods -n ai-system -l app.kubernetes.io/name=agentgateway

# Check events for issues
kubectl get events -n ai-system --sort-by='.lastTimestamp' | tail -20

# Check controller logs
kubectl logs -n ai-system -l app.kubernetes.io/name=kgateway-controller --tail=100

# Check proxy logs
kubectl logs -n ai-system -l app.kubernetes.io/name=agentgateway --tail=100
```

---

## Installation Issues

### Problem: Helm Release Stuck in "Not Ready"

**Symptoms:**
```
helmrelease/agentgateway-crds       False   HelmChart 'ai-system/ai-system-agentgateway-crds' is not ready
```

**Diagnosis:**
```bash
kubectl describe helmrelease agentgateway-crds -n ai-system
kubectl get helmchart -n ai-system
kubectl describe ocirepository kgateway -n ai-system
```

**Solutions:**

1. **OCI Repository not synced:**
   ```bash
   # Force reconciliation
   flux reconcile source oci kgateway -n ai-system

   # Check OCI status
   kubectl get ocirepository kgateway -n ai-system -o yaml
   ```

2. **Chart version mismatch:**
   ```yaml
   # Ensure chart versions align
   spec:
     chart:
       spec:
         chart: kgateway-crds
         version: "2.1.2"  # Must match available versions
   ```

3. **Network issues pulling chart:**
   ```bash
   # Test OCI registry access
   kubectl run test --rm -it --image=ghcr.io/kgateway-dev/kgateway:2.1.2 -- echo "success"
   ```

---

### Problem: CRDs Not Installing

**Symptoms:**
```
error: unable to recognize "backend.yaml": no matches for kind "Backend" in version "gateway.kgateway.dev/v1alpha1"
```

**Diagnosis:**
```bash
kubectl get crds | grep kgateway
kubectl get crds | grep gateway.kgateway.dev
```

**Solutions:**

1. **Install CRDs first:**
   ```bash
   # Ensure CRD HelmRelease comes before main release
   flux reconcile helmrelease agentgateway-crds -n ai-system

   # Wait for CRDs
   kubectl wait --for condition=established crd/backends.gateway.kgateway.dev --timeout=60s
   ```

2. **Manual CRD installation:**
   ```bash
   kubectl apply -f https://github.com/kgateway-dev/kgateway/releases/download/v2.1.2/kgateway-crds.yaml
   ```

---

### Problem: Gateway Not Getting IP Address

**Symptoms:**
```
NAME           CLASS          ADDRESS   PROGRAMMED   AGE
agentgateway   agentgateway             Unknown      5m
```

**Diagnosis:**
```bash
kubectl describe gateway agentgateway -n ai-system
kubectl get svc -n ai-system -l app.kubernetes.io/name=agentgateway
kubectl describe svc agentgateway -n ai-system
```

**Solutions:**

1. **Cilium IPAM not configured:**
   ```yaml
   # Add Cilium IPAM annotation to Gateway
   metadata:
     annotations:
       lbipam.cilium.io/ips: "10.50.0.30"  # Must be in CiliumLoadBalancerIPPool
   ```

2. **Check CiliumLoadBalancerIPPool:**
   ```bash
   kubectl get ciliumloadbalancerippool -A
   kubectl describe ciliumloadbalancerippool home-ops-pool
   ```

3. **Service type not LoadBalancer:**
   ```yaml
   # In GatewayParameters
   spec:
     kube:
       service:
         type: LoadBalancer
   ```

---

## Backend Issues

### Problem: Backend Shows "Not Accepted"

**Symptoms:**
```yaml
status:
  conditions:
    - type: Accepted
      status: "False"
      reason: InvalidSpec
```

**Diagnosis:**
```bash
kubectl describe backend openai -n ai-system
kubectl get events -n ai-system --field-selector involvedObject.name=openai
```

**Solutions:**

1. **Missing secret reference:**
   ```bash
   # Check if secret exists
   kubectl get secret openai-secret -n ai-system

   # Check ExternalSecret status
   kubectl get externalsecret openai-secret -n ai-system
   kubectl describe externalsecret openai-secret -n ai-system
   ```

2. **Invalid provider configuration:**
   ```yaml
   # Anthropic requires apiVersion
   anthropic:
     apiVersion: "2023-06-01"  # Required!
     model: "claude-3-5-sonnet-20241022"
   ```

3. **Wrong secret key:**
   ```yaml
   # Secret must have 'Authorization' key
   target:
     template:
       data:
         Authorization: "{{ .API_KEY }}"  # Not 'api-key' or 'token'
   ```

---

### Problem: LLM Requests Failing with 401

**Symptoms:**
```json
{"error": {"message": "Incorrect API key provided", "type": "invalid_request_error"}}
```

**Diagnosis:**
```bash
# Check secret content (base64 encoded)
kubectl get secret openai-secret -n ai-system -o jsonpath='{.data.Authorization}' | base64 -d

# Check ExternalSecret sync status
kubectl get externalsecret -n ai-system
```

**Solutions:**

1. **Secret not synced from 1Password:**
   ```bash
   # Force sync
   kubectl annotate externalsecret openai-secret -n ai-system force-sync=$(date +%s) --overwrite

   # Check 1Password Connect
   kubectl logs -n security -l app.kubernetes.io/name=onepassword-connect
   ```

2. **Wrong 1Password item reference:**
   ```yaml
   dataFrom:
     - extract:
         key: openai-credentials  # Must match 1Password item name exactly
   ```

3. **API key format issue:**
   ```yaml
   # For OpenAI, key should NOT include "Bearer " prefix
   # The gateway adds it automatically
   target:
     template:
       data:
         Authorization: "{{ .OPENAI_API_KEY }}"  # Just the key, no "Bearer"
   ```

---

### Problem: LLM Requests Timing Out

**Symptoms:**
```
upstream request timeout
```

**Diagnosis:**
```bash
# Check backend connectivity
kubectl exec -n ai-system deployment/agentgateway-proxy -- curl -v https://api.openai.com/v1/models

# Check TrafficPolicy timeout
kubectl get trafficpolicy -n ai-system -o yaml | grep -A5 timeout
```

**Solutions:**

1. **Increase timeout:**
   ```yaml
   apiVersion: gateway.kgateway.dev/v1alpha1
   kind: TrafficPolicy
   spec:
     timeout: 120s  # LLM requests can be slow
   ```

2. **Network policy blocking egress:**
   ```bash
   kubectl get networkpolicy -n ai-system
   # Ensure egress to 0.0.0.0/0:443 is allowed
   ```

3. **DNS resolution failing:**
   ```bash
   kubectl exec -n ai-system deployment/agentgateway-proxy -- nslookup api.openai.com
   ```

---

## MCP Issues

### Problem: MCP Server Not Discoverable

**Symptoms:**
- MCP tools not appearing in agent
- 404 errors when calling MCP endpoints

**Diagnosis:**
```bash
# Check MCP Backend
kubectl get backend -n ai-system -l type=mcp

# Check service exists
kubectl get svc -n ai-system -l app.kubernetes.io/name=kubernetes-mcp-server

# Test MCP endpoint
kubectl exec -n ai-system deployment/agentgateway-proxy -- \
  curl -v http://kubernetes-mcp-server.ai-system.svc.cluster.local:80/sse
```

**Solutions:**

1. **Wrong service port/protocol:**
   ```yaml
   spec:
     mcp:
       targets:
         - name: k8s-tools
           static:
             host: kubernetes-mcp-server.ai-system.svc.cluster.local
             port: 80  # Must match service port
             protocol: StreamableHTTP  # Or SSE depending on server
   ```

2. **Missing appProtocol:**
   ```yaml
   # Service must have appProtocol annotation
   apiVersion: v1
   kind: Service
   spec:
     ports:
       - port: 80
         appProtocol: kgateway.dev/mcp
   ```

3. **MCP server not running:**
   ```bash
   kubectl get pods -n ai-system -l app=kubernetes-mcp-server
   kubectl logs -n ai-system -l app=kubernetes-mcp-server
   ```

---

### Problem: MCP Tool Calls Failing

**Symptoms:**
```
error executing tool: permission denied
```

**Diagnosis:**
```bash
# Check MCP server RBAC
kubectl get clusterrole,clusterrolebinding -l app=kubernetes-mcp-server

# Check service account
kubectl get serviceaccount -n ai-system kubernetes-mcp-server
```

**Solutions:**

1. **MCP server lacks permissions:**
   ```yaml
   apiVersion: rbac.authorization.k8s.io/v1
   kind: ClusterRole
   metadata:
     name: kubernetes-mcp-server
   rules:
     - apiGroups: [""]
       resources: ["pods", "services", "configmaps"]
       verbs: ["get", "list", "watch"]
   ```

2. **TrafficPolicy RBAC blocking:**
   ```yaml
   # Check TrafficPolicy CEL expressions
   rbac:
     policy:
       matchExpressions:
         - 'mcp.tool.name in ["kubectl-get"]'  # Whitelist tools
   ```

---

## HTTPRoute Issues

### Problem: Routes Not Matching

**Symptoms:**
- Requests returning 404
- Wrong backend being selected

**Diagnosis:**
```bash
kubectl describe httproute llm-routes -n ai-system
kubectl get httproute -n ai-system -o yaml | grep -A20 rules
```

**Solutions:**

1. **Path matching issues:**
   ```yaml
   # Use PathPrefix for flexibility
   matches:
     - path:
         type: PathPrefix  # Not Exact
         value: /openai
   ```

2. **Header matching:**
   ```yaml
   # Case-sensitive header matching
   matches:
     - headers:
         - name: x-llm  # Lowercase!
           value: openai
   ```

3. **Missing parentRef:**
   ```yaml
   spec:
     parentRefs:
       - name: agentgateway
         namespace: ai-system  # Required if in different namespace
   ```

---

### Problem: HTTPS Redirect Not Working

**Symptoms:**
- HTTP requests not redirecting to HTTPS
- Mixed content warnings

**Diagnosis:**
```bash
curl -v http://ai.sklab.dev/openai
```

**Solutions:**

1. **Add redirect filter:**
   ```yaml
   apiVersion: gateway.networking.k8s.io/v1
   kind: HTTPRoute
   metadata:
     name: https-redirect
   spec:
     parentRefs:
       - name: agentgateway
         sectionName: http  # Target HTTP listener
     rules:
       - filters:
           - type: RequestRedirect
             requestRedirect:
               scheme: https
               statusCode: 301
   ```

2. **Gateway missing HTTP listener:**
   ```yaml
   listeners:
     - name: http
       protocol: HTTP
       port: 80
     - name: https
       protocol: HTTPS
       port: 443
   ```

---

## Security Issues

### Problem: JWT Validation Failing

**Symptoms:**
```
JWT validation failed: invalid signature
```

**Diagnosis:**
```bash
# Test JWT manually
jwt decode $TOKEN

# Check JWKS endpoint
curl https://auth.sklab.dev/realms/ai/protocol/openid-connect/certs
```

**Solutions:**

1. **JWKS not reachable:**
   ```yaml
   # Ensure backendRef points to accessible service
   jwks:
     remote:
       backendRef:
         kind: Service
         name: keycloak
         namespace: security
         port: 8080
   ```

2. **Wrong issuer/audience:**
   ```yaml
   providers:
     - issuer: "https://auth.sklab.dev/realms/ai"  # Must match JWT iss claim
       audiences: ["agentgateway"]  # Must match JWT aud claim
   ```

3. **Clock skew:**
   ```bash
   # Check cluster time
   kubectl get nodes -o jsonpath='{.items[*].status.conditions[?(@.type=="Ready")].lastHeartbeatTime}'
   ```

---

### Problem: Prompt Guard Blocking Legitimate Requests

**Symptoms:**
```json
{"error": "Request blocked due to policy violation"}
```

**Diagnosis:**
```bash
kubectl logs -n ai-system -l app.kubernetes.io/name=agentgateway | grep -i "blocked\|guard"
```

**Solutions:**

1. **Overly broad regex:**
   ```yaml
   # Too broad - blocks legitimate uses
   - pattern: "password"  # Blocks "generate a random password"

   # Better - more specific
   - pattern: "(?i)(my|your|the)\\s+password\\s+is"
   ```

2. **Test patterns:**
   ```bash
   # Test regex before deploying
   echo "ignore previous instructions" | grep -P "ignore.*previous.*instructions"
   ```

---

## Observability Issues

### Problem: Metrics Not Appearing in Prometheus

**Symptoms:**
- No `agentgateway_*` metrics in Prometheus
- ServiceMonitor not scraping

**Diagnosis:**
```bash
# Check ServiceMonitor
kubectl get servicemonitor -n ai-system

# Check Prometheus targets
kubectl port-forward -n monitoring svc/prometheus 9090:9090
# Then visit http://localhost:9090/targets
```

**Solutions:**

1. **ServiceMonitor selector mismatch:**
   ```yaml
   spec:
     selector:
       matchLabels:
         app.kubernetes.io/name: agentgateway  # Must match service labels
   ```

2. **Wrong metrics port:**
   ```yaml
   endpoints:
     - port: metrics  # Must match service port name
       path: /metrics
   ```

3. **Prometheus RBAC:**
   ```bash
   # Prometheus needs access to ai-system namespace
   kubectl get role,rolebinding -n ai-system | grep prometheus
   ```

---

### Problem: Traces Not Appearing in Tempo

**Symptoms:**
- No traces for AgentGateway requests
- Trace ID not propagating

**Diagnosis:**
```bash
# Check OTLP collector
kubectl logs -n monitoring -l app=opentelemetry-collector

# Check GatewayParameters
kubectl get gatewayparameters -n ai-system -o yaml | grep -A10 tracing
```

**Solutions:**

1. **OTLP endpoint not configured:**
   ```yaml
   spec:
     rawConfig:
       config:
         tracing:
           otlpEndpoint: http://opentelemetry-collector.monitoring.svc.cluster.local:4317
           otlpProtocol: grpc
   ```

2. **Sampling disabled:**
   ```yaml
   tracing:
     randomSampling: true  # Enable sampling
   ```

---

## Performance Issues

### Problem: High Latency

**Symptoms:**
- P99 latency > 30s
- Slow first-byte time

**Diagnosis:**
```bash
# Check proxy resource usage
kubectl top pods -n ai-system -l app.kubernetes.io/name=agentgateway

# Check connection pools
kubectl exec -n ai-system deployment/agentgateway-proxy -- curl localhost:15000/clusters | grep -i active
```

**Solutions:**

1. **Insufficient resources:**
   ```yaml
   resources:
     requests:
       cpu: "500m"
       memory: "512Mi"
     limits:
       cpu: "2"
       memory: "2Gi"
   ```

2. **Connection pooling:**
   ```yaml
   apiVersion: gateway.kgateway.dev/v1alpha1
   kind: BackendConfigPolicy
   spec:
     http2ProtocolOptions:
       maxConcurrentStreams: 100
   ```

3. **Enable keepalive:**
   ```yaml
   tcpKeepalive:
     keepaliveTime: 60s
     keepaliveInterval: 30s
   ```

---

### Problem: Rate Limiting Too Aggressive

**Symptoms:**
- Legitimate requests getting 429
- Token bucket depleting too fast

**Diagnosis:**
```bash
# Check rate limit metrics
kubectl exec -n ai-system deployment/agentgateway-proxy -- \
  curl localhost:15000/stats | grep ratelimit
```

**Solutions:**

1. **Increase limits:**
   ```yaml
   rateLimit:
     local:
       - maxTokens: 1000  # Increase bucket size
         tokensPerFill: 100
         fillInterval: 1s
   ```

2. **Per-user limits instead of global:**
   ```yaml
   descriptors:
     - entries:
         - key: user
           value: 'request.headers["x-user-id"]'
   ```

---

## Debugging Commands

### View xDS Configuration

```bash
# Port forward to admin interface
kubectl port-forward -n ai-system deployment/agentgateway-proxy 15000:15000 &

# View clusters
curl localhost:15000/clusters

# View routes
curl localhost:15000/config_dump?resource=dynamic_route_configs

# View listeners
curl localhost:15000/config_dump?resource=dynamic_listeners
```

### Control Plane Debug

```bash
# Port forward
kubectl port-forward -n ai-system svc/kgateway-controller 9095:9095 &

# View KRT snapshots
curl localhost:9095/snapshots/krt

# View xDS snapshots
curl localhost:9095/snapshots/xds

# Adjust log level
curl -X POST "localhost:9095/logging?level=debug"
```

### Test Backend Connectivity

```bash
# Test from proxy pod
kubectl exec -n ai-system deployment/agentgateway-proxy -- \
  curl -v https://api.openai.com/v1/models \
  -H "Authorization: Bearer $OPENAI_API_KEY"

# Test internal MCP
kubectl exec -n ai-system deployment/agentgateway-proxy -- \
  curl -v http://kubernetes-mcp-server.ai-system.svc.cluster.local:80/sse
```

---

## Common Error Messages

| Error | Cause | Solution |
|-------|-------|----------|
| `upstream connect error` | Backend unreachable | Check service/endpoint exists |
| `no healthy upstream` | All backends failing | Check Backend status, health checks |
| `invalid_api_key` | Wrong/missing secret | Check ExternalSecret sync |
| `rate limit exceeded` | Token bucket empty | Increase limits or add per-user |
| `JWT validation failed` | Token invalid/expired | Check issuer, audience, JWKS |
| `request timeout` | LLM slow response | Increase timeout in TrafficPolicy |
| `no route matched` | HTTPRoute not matching | Check path/header matching |

---

## Getting Help

### Resources

- [kgateway Documentation](https://kgateway.dev/docs/agentgateway/latest/)
- [Gateway API Specification](https://gateway-api.sigs.k8s.io/)
- [kgateway GitHub Issues](https://github.com/kgateway-dev/kgateway/issues)
- [CNCF Slack #kgateway](https://cloud-native.slack.com/channels/kgateway)

### Collecting Debug Information

```bash
# Create debug bundle
kubectl get all,gateway,httproute,backend,trafficpolicy -n ai-system -o yaml > agentgateway-debug.yaml
kubectl logs -n ai-system -l app.kubernetes.io/name=kgateway-controller --tail=500 > controller.log
kubectl logs -n ai-system -l app.kubernetes.io/name=agentgateway --tail=500 > proxy.log
kubectl get events -n ai-system --sort-by='.lastTimestamp' > events.log
```

---

## Health Dashboard Patterns

### Key Health Indicators

Configure a Grafana dashboard with these panels:

```yaml
# Grafana Dashboard JSON (abbreviated)
panels:
  - title: "Gateway Health Score"
    type: stat
    targets:
      - expr: |
          (
            (1 - (sum(rate(agentgateway_requests_total{status=~"5.."}[5m])) / sum(rate(agentgateway_requests_total[5m])))) * 0.4 +
            (1 - clamp_max(avg(agentgateway_request_duration_seconds{quantile="0.99"}) / 30, 1)) * 0.3 +
            (sum(agentgateway_backends_healthy) / sum(agentgateway_backends_total)) * 0.3
          ) * 100
    thresholds:
      - color: red
        value: 0
      - color: yellow
        value: 80
      - color: green
        value: 95

  - title: "Request Rate"
    type: graph
    targets:
      - expr: sum(rate(agentgateway_requests_total[1m])) by (status)

  - title: "Latency Distribution"
    type: heatmap
    targets:
      - expr: sum(rate(agentgateway_request_duration_seconds_bucket[5m])) by (le)

  - title: "Backend Health"
    type: table
    targets:
      - expr: agentgateway_backend_health_status
```

### Traffic Light Status

| Indicator | Green | Yellow | Red |
|-----------|-------|--------|-----|
| Error Rate | < 1% | 1-5% | > 5% |
| P99 Latency | < 10s | 10-30s | > 30s |
| Backend Health | 100% | 80-99% | < 80% |
| Active Connections | < 80% capacity | 80-95% | > 95% |

---

## SLO/SLI Definitions

### Recommended SLOs

| SLO | Target | Measurement Window |
|-----|--------|-------------------|
| **Availability** | 99.9% | 30 days rolling |
| **Latency (P50)** | < 5s | 5 minute windows |
| **Latency (P99)** | < 30s | 5 minute windows |
| **Error Rate** | < 0.1% | 1 hour windows |
| **Throughput** | > 100 RPS sustained | 5 minute windows |

### SLI Queries

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: agentgateway-slos
  namespace: ai-system
spec:
  groups:
    - name: sli-recording
      rules:
        # Availability SLI
        - record: agentgateway:availability:ratio_rate5m
          expr: |
            sum(rate(agentgateway_requests_total{status!~"5.."}[5m]))
            / sum(rate(agentgateway_requests_total[5m]))

        # Latency SLI (requests under 30s)
        - record: agentgateway:latency_target:ratio_rate5m
          expr: |
            sum(rate(agentgateway_request_duration_seconds_bucket{le="30"}[5m]))
            / sum(rate(agentgateway_request_duration_seconds_count[5m]))

        # Error budget remaining (30-day window)
        - record: agentgateway:error_budget:remaining
          expr: |
            1 - (
              (1 - avg_over_time(agentgateway:availability:ratio_rate5m[30d]))
              / (1 - 0.999)
            )

    - name: slo-alerts
      rules:
        - alert: SLOBudgetBurnRateHigh
          expr: |
            (
              sum(rate(agentgateway_requests_total{status=~"5.."}[1h]))
              / sum(rate(agentgateway_requests_total[1h]))
            ) > (14.4 * (1 - 0.999))
          for: 5m
          labels:
            severity: critical
          annotations:
            summary: "Error budget burn rate is too high"
            description: "At current rate, monthly error budget will be exhausted in < 2 days"

        - alert: SLOBudgetLow
          expr: agentgateway:error_budget:remaining < 0.25
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "Error budget below 25%"
            description: "{{ $value | humanizePercentage }} of monthly error budget remaining"
```

### Error Budget Policy

| Budget Remaining | Action |
|------------------|--------|
| > 50% | Normal operations, can deploy freely |
| 25-50% | Increased monitoring, limit risky changes |
| 10-25% | Freeze non-critical deployments |
| < 10% | Incident response mode, focus on reliability |

---

## Canary Deployment

### Progressive Rollout

```yaml
apiVersion: flagger.app/v1beta1
kind: Canary
metadata:
  name: agentgateway
  namespace: ai-system
spec:
  targetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: agentgateway-proxy
  progressDeadlineSeconds: 600
  service:
    port: 8080
  analysis:
    interval: 1m
    threshold: 5
    maxWeight: 50
    stepWeight: 10
    metrics:
      - name: request-success-rate
        thresholdRange:
          min: 99
        interval: 1m
      - name: request-duration
        thresholdRange:
          max: 10000  # 10s in milliseconds
        interval: 1m
    webhooks:
      - name: load-test
        url: http://flagger-loadtester.test/
        metadata:
          cmd: "hey -z 1m -q 10 -c 2 http://agentgateway-canary.ai-system:8080/health"
```

### Manual Canary Testing

```bash
# Deploy canary version
kubectl set image deployment/agentgateway-proxy \
  agentgateway=ghcr.io/kgateway-dev/kgateway:2.1.3-canary \
  -n ai-system

# Route 10% traffic to canary
kubectl patch httproute llm-routes -n ai-system --type merge -p '
spec:
  rules:
    - backendRefs:
        - name: agentgateway-stable
          weight: 90
        - name: agentgateway-canary
          weight: 10
'

# Monitor canary metrics
watch -n5 'kubectl exec -n monitoring deploy/prometheus -- \
  promtool query instant http://localhost:9090 \
  "sum(rate(agentgateway_requests_total{version=\"canary\",status=~\"5..\"}[5m]))"'

# Promote or rollback
kubectl patch httproute llm-routes -n ai-system --type merge -p '
spec:
  rules:
    - backendRefs:
        - name: agentgateway-stable
          weight: 100
'
```

---

*See [README.md](./README.md) for documentation index.*
