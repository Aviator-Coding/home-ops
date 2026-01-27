# Optimization

> **Performance tuning, cost optimization, connection management, and caching strategies for AgentGateway.**

## Overview

Optimizing AgentGateway involves balancing three key dimensions:

| Dimension | Metrics | Trade-offs |
|-----------|---------|------------|
| **Cost** | $/token, $/request | Quality vs. expense |
| **Latency** | P50, P95, P99 | Speed vs. thoroughness |
| **Throughput** | RPS, concurrent connections | Scale vs. resource usage |

---

## Cost Optimization

### Model Selection Matrix

| Model | Input Cost | Output Cost | Speed | Quality | Best For |
|-------|-----------|-------------|-------|---------|----------|
| **GPT-4o** | $2.50/1M | $10.00/1M | Fast | Excellent | Complex reasoning |
| **GPT-4o-mini** | $0.15/1M | $0.60/1M | Very Fast | Good | General tasks |
| **GPT-3.5-turbo** | $0.50/1M | $1.50/1M | Very Fast | Good | Simple tasks |
| **Claude Opus** | $15.00/1M | $75.00/1M | Slow | Excellent | Complex analysis |
| **Claude Sonnet** | $3.00/1M | $15.00/1M | Fast | Very Good | Balanced |
| **Claude Haiku** | $0.25/1M | $1.25/1M | Very Fast | Good | Quick tasks |
| **Gemini Pro** | $1.25/1M | $5.00/1M | Fast | Very Good | Multimodal |
| **Gemini Flash** | $0.075/1M | $0.30/1M | Very Fast | Good | High volume |
| **Llama 3.2 (local)** | $0 | $0 | Varies | Good | Privacy, cost |

### Cost-Optimized Backend Configuration

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: cost-optimized-llm
  namespace: ai-system
spec:
  type: AI
  ai:
    priorityGroups:
    # Priority 1: Free local models
    - providers:
      - name: local-llama
        openai:
          host: ollama.ai.svc.cluster.local
          port: 11434
          model: "llama3.2:8b"
    # Priority 2: Cheapest cloud models
    - providers:
      - name: gemini-flash
        gemini:
          model: "gemini-1.5-flash"
          authToken:
            kind: SecretRef
            secretRef:
              name: google-ai-secret
      - name: gpt-4o-mini
        openai:
          model: "gpt-4o-mini"
          authToken:
            kind: SecretRef
            secretRef:
              name: openai-secret
    # Priority 3: Mid-tier (only if cheaper fail)
    - providers:
      - name: claude-haiku
        anthropic:
          model: "claude-3-5-haiku-20241022"
          apiVersion: "2023-06-01"
          authToken:
            kind: SecretRef
            secretRef:
              name: anthropic-secret
```

### Token Budget Management

Implement per-user token budgets:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: token-budget
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  rateLimit:
    local:
      # Daily token budget per user
      - maxTokens: 1000000        # 1M tokens/day
        tokensPerFill: 1000000
        fillInterval: 24h
        type: tokens
        descriptors:
          - entries:
              - key: user
                value: 'jwt.sub'
      # Burst protection
      - maxTokens: 10000          # 10K tokens per minute burst
        tokensPerFill: 10000
        fillInterval: 1m
        type: tokens
        descriptors:
          - entries:
              - key: user
                value: 'jwt.sub'
```

### Cost Tracking and Alerts

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: cost-alerts
  namespace: ai-system
spec:
  groups:
    - name: finops
      rules:
        # Estimated hourly cost
        - record: agentgateway:estimated_hourly_cost
          expr: |
            sum(
              rate(agentgateway_llm_tokens_total{direction="input"}[1h]) * on(provider, model) group_left
              agentgateway_model_cost_per_million{direction="input"} / 1000000
            ) +
            sum(
              rate(agentgateway_llm_tokens_total{direction="output"}[1h]) * on(provider, model) group_left
              agentgateway_model_cost_per_million{direction="output"} / 1000000
            )

        - alert: HighHourlyCost
          expr: agentgateway:estimated_hourly_cost > 10
          for: 15m
          labels:
            severity: warning
          annotations:
            summary: "LLM costs exceeding $10/hour"
            description: "Current estimated rate: ${{ $value }}/hour"

        - alert: UnexpectedModelUsage
          expr: |
            rate(agentgateway_llm_requests_total{model="gpt-4"}[5m]) > 0
            and on() hour() >= 0 and hour() < 8
          for: 5m
          labels:
            severity: info
          annotations:
            summary: "Premium model usage during off-hours"
```

### Batch vs Streaming Cost Trade-offs

| Mode | Latency | Cost | Use Case |
|------|---------|------|----------|
| **Batch** | Higher | Lower (no streaming overhead) | Background processing |
| **Streaming** | Lower TTFB | Slightly higher | Interactive chat |

```yaml
# Batch processing route (cost-optimized)
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: batch-route
spec:
  rules:
    - matches:
        - path:
            value: /batch
      backendRefs:
        - name: cost-optimized-llm
---
# Streaming route (latency-optimized)
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: stream-route
spec:
  rules:
    - matches:
        - path:
            value: /stream
      backendRefs:
        - name: fast-llm
```

---

## Latency Optimization

### Connection Pooling

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: BackendConfigPolicy
metadata:
  name: connection-pool
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.kgateway.dev
      kind: Backend
      name: openai
  connectionPool:
    tcp:
      maxConnections: 1000
      connectTimeout: 5s
    http:
      h2UpgradePolicy: UPGRADE
      http1MaxPendingRequests: 1000
      http2MaxRequests: 1000
      maxRequestsPerConnection: 100
      maxRetries: 3
```

### HTTP/2 Configuration

Enable HTTP/2 for multiplexed connections:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: BackendConfigPolicy
metadata:
  name: http2-config
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.kgateway.dev
      kind: Backend
      name: openai
  http2ProtocolOptions:
    maxConcurrentStreams: 100
    initialStreamWindowSize: 65536
    initialConnectionWindowSize: 1048576
```

### TCP Keepalive

Maintain warm connections to reduce latency:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: BackendConfigPolicy
metadata:
  name: keepalive
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.kgateway.dev
      kind: Backend
      name: openai
  tcpKeepalive:
    keepaliveTime: 60s       # Start probes after 60s idle
    keepaliveInterval: 30s   # Probe interval
    keepaliveProbes: 3       # Probes before giving up
```

### DNS Caching

Reduce DNS lookup latency:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: agentgateway-params
  namespace: ai-system
spec:
  rawConfig:
    config:
      dns:
        refreshRate: 60s
        respectDnsTtl: false     # Use our refresh rate
        cacheMaxSize: 1000
```

### Latency-Based Routing

Route to lowest-latency provider:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: latency-routing
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.kgateway.dev
      kind: Backend
      name: multi-provider
  loadBalancer:
    type: LEAST_REQUEST
    healthCheck:
      interval: 10s
      timeout: 5s
      healthyThreshold: 2
      unhealthyThreshold: 3
```

---

## Throughput Optimization

### Horizontal Scaling

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: agentgateway-hpa
  namespace: ai-system
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: agentgateway-proxy
  minReplicas: 3
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Pods
      pods:
        metric:
          name: agentgateway_active_connections
        target:
          type: AverageValue
          averageValue: "500"
```

### Resource Allocation

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: agentgateway-params
  namespace: ai-system
spec:
  kube:
    deployment:
      replicas: 3
      container:
        resources:
          requests:
            cpu: "1"
            memory: "1Gi"
          limits:
            cpu: "4"
            memory: "4Gi"
```

### Circuit Breaker Configuration

Prevent cascade failures:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: BackendConfigPolicy
metadata:
  name: circuit-breaker
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.kgateway.dev
      kind: Backend
      name: openai
  outlierDetection:
    consecutive5xxErrors: 5          # Trip after 5 consecutive 5xx
    interval: 10s                    # Check interval
    baseEjectionTime: 30s            # Initial ejection duration
    maxEjectionPercent: 50           # Max % of hosts ejected
    splitExternalLocalOriginErrors: true
```

---

## Caching Strategies

### Response Caching

Cache identical requests:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: response-cache
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  caching:
    enabled: true
    mode: safe                       # safe, aggressive
    ttl: 1h
    maxSize: 100Mi
    keyGenerator:
      headers:
        - Authorization              # Include auth in cache key
      body:
        jsonPaths:
          - $.model
          - $.messages
          - $.temperature
```

### Semantic Caching

Cache based on semantic similarity (requires external service):

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: semantic-cache
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: llm-routes
  ai:
    semanticCache:
      enabled: true
      similarityThreshold: 0.95      # 95% similarity required
      backend:
        kind: Service
        name: semantic-cache-service
        port: 8080
      ttl: 24h
```

### MCP Tool Result Caching

Cache tool results that don't change frequently:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: tool-cache
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mcp-routes
  caching:
    enabled: true
    rules:
      # Cache list operations for 5 minutes
      - match:
          jsonPath: '$.params.name'
          values: ['kubectl-get', 'list-pods', 'list-services']
        ttl: 5m
      # Don't cache write operations
      - match:
          jsonPath: '$.params.name'
          values: ['kubectl-apply', 'kubectl-delete']
        enabled: false
```

---

## Performance Tuning Checklist

### Gateway Configuration

- [ ] Enable HTTP/2 for all backends
- [ ] Configure connection pooling
- [ ] Set appropriate timeouts
- [ ] Enable TCP keepalive
- [ ] Configure circuit breakers
- [ ] Set up health checks

### Resource Sizing

| Component | CPU Request | Memory Request | CPU Limit | Memory Limit |
|-----------|-------------|----------------|-----------|--------------|
| **Control Plane** | 500m | 512Mi | 2 | 2Gi |
| **Data Plane (per replica)** | 1 | 1Gi | 4 | 4Gi |
| **Redis (sessions)** | 500m | 1Gi | 2 | 4Gi |

### Network Optimization

- [ ] Use cluster-local DNS for internal services
- [ ] Enable DNS caching
- [ ] Configure appropriate MTU
- [ ] Use network policies to reduce unnecessary traffic

---

## Load Testing

### k6 Load Test Script

```javascript
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const latency = new Trend('llm_latency');

export const options = {
  stages: [
    { duration: '2m', target: 10 },   // Ramp up
    { duration: '5m', target: 50 },   // Sustained load
    { duration: '2m', target: 100 },  // Peak
    { duration: '2m', target: 0 },    // Ramp down
  ],
  thresholds: {
    http_req_duration: ['p(95)<30000'],  // 95% under 30s
    errors: ['rate<0.01'],                // Error rate < 1%
  },
};

const BASE_URL = __ENV.GATEWAY_URL || 'http://ai.sklab.dev';
const TOKEN = __ENV.JWT_TOKEN;

export default function() {
  const payload = JSON.stringify({
    model: 'gpt-4o-mini',
    messages: [
      { role: 'user', content: 'Say hello in exactly 5 words.' }
    ],
    max_tokens: 20,
  });

  const params = {
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${TOKEN}`,
    },
    timeout: '60s',
  };

  const start = Date.now();
  const res = http.post(`${BASE_URL}/openai/v1/chat/completions`, payload, params);
  const duration = Date.now() - start;

  latency.add(duration);

  const success = check(res, {
    'status is 200': (r) => r.status === 200,
    'has response': (r) => r.json('choices.0.message.content') !== undefined,
  });

  errorRate.add(!success);

  sleep(1);
}
```

### Running Load Tests

```bash
# Install k6
brew install k6

# Run test
k6 run \
  -e GATEWAY_URL=http://ai.sklab.dev \
  -e JWT_TOKEN=$JWT_TOKEN \
  loadtest.js

# Run with Prometheus output
k6 run \
  -e GATEWAY_URL=http://ai.sklab.dev \
  -e JWT_TOKEN=$JWT_TOKEN \
  -o experimental-prometheus-rw \
  loadtest.js
```

### Benchmark Results Template

| Metric | Target | Actual | Status |
|--------|--------|--------|--------|
| P50 Latency | < 5s | | |
| P95 Latency | < 15s | | |
| P99 Latency | < 30s | | |
| Throughput | > 100 RPS | | |
| Error Rate | < 1% | | |
| CPU Usage | < 70% | | |
| Memory Usage | < 80% | | |

---

## Optimization Decision Trees

### Which Model Should I Use?

```
Start
  │
  ├─ Need highest quality? ─── Yes ──▶ Claude Opus / GPT-4
  │
  ├─ Complex reasoning? ─────── Yes ──▶ Claude Sonnet / GPT-4o
  │
  ├─ Multimodal (images)? ──── Yes ──▶ GPT-4o / Gemini Pro
  │
  ├─ Cost is primary concern? ─ Yes ──▶ Gemini Flash / GPT-4o-mini
  │
  ├─ Privacy required? ──────── Yes ──▶ Local Llama
  │
  └─ General purpose? ────────────────▶ Claude Haiku / GPT-4o-mini
```

### How Many Replicas Do I Need?

```
Start
  │
  ├─ Concurrent users < 10? ────────▶ 1-2 replicas
  │
  ├─ Concurrent users 10-50? ───────▶ 3-5 replicas
  │
  ├─ Concurrent users 50-200? ──────▶ 5-10 replicas
  │
  ├─ Concurrent users > 200? ───────▶ HPA with 10+ min
  │
  └─ Production critical? ──────────▶ Min 3 replicas (HA)
```

### Which Timeout Values?

| Request Type | Recommended Timeout |
|--------------|---------------------|
| Simple completion | 30s |
| Complex reasoning | 60s |
| Code generation | 90s |
| Long document analysis | 120s |
| Streaming chat | 300s |
| Tool execution | Varies by tool |

---

## References

- [LLM Pricing Comparison](https://llmpricecheck.com/)
- [Envoy Performance Tuning](https://www.envoyproxy.io/docs/envoy/latest/faq/configuration/latency)
- [k6 Load Testing](https://k6.io/docs/)
- [Kubernetes HPA](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/)

---

*See [12-troubleshooting.md](./12-troubleshooting.md) for diagnosing performance issues.*
