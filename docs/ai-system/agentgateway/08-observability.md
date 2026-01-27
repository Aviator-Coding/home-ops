# Observability

> **Configure metrics, logs, and traces for AgentGateway using OpenTelemetry.**

## Overview

AgentGateway integrates with the OpenTelemetry stack for comprehensive observability:

| Pillar | Tool | Purpose |
|--------|------|---------|
| **Metrics** | Prometheus | Time-series measurements, token usage |
| **Logs** | Loki | Discrete events with context |
| **Traces** | Tempo/Jaeger | Request flow tracking |

---

## Prometheus Metrics

### Control Plane Metrics

Exposed on port `9092`:

| Metric | Type | Description |
|--------|------|-------------|
| `kgateway_controller_reconciliations_total` | Counter | Total reconciliation count |
| `kgateway_controller_reconcile_duration_seconds` | Histogram | Reconciliation latency |
| `kgateway_resources_managed` | Gauge | Active resource count |
| `kgateway_xds_snapshot_resources` | Gauge | xDS resources per gateway |
| `kgateway_translator_translation_duration_seconds` | Histogram | Translation latency |

### ServiceMonitor Configuration

```yaml
# servicemonitor.yaml
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: agentgateway
  namespace: ai-system
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: agentgateway
  endpoints:
    - port: metrics
      path: /metrics
      interval: 30s
      scrapeTimeout: 10s
  namespaceSelector:
    matchNames:
      - ai-system
```

### Data Plane Metrics

AgentGateway exposes AI-specific metrics:

| Metric | Description |
|--------|-------------|
| `agentgateway_llm_requests_total` | Total LLM requests |
| `agentgateway_llm_request_duration_seconds` | Request latency |
| `agentgateway_llm_tokens_input_total` | Total input tokens |
| `agentgateway_llm_tokens_output_total` | Total output tokens |
| `agentgateway_llm_tokens_total` | Combined token count |
| `agentgateway_mcp_requests_total` | Total MCP requests |
| `agentgateway_a2a_messages_total` | Total A2A messages |

---

## Token Usage Tracking

Track LLM token consumption for FinOps:

### Prometheus Queries

```promql
# Total tokens by provider (last 24h)
sum by (provider) (increase(agentgateway_llm_tokens_total[24h]))

# Input vs output tokens
sum(rate(agentgateway_llm_tokens_input_total[5m])) / sum(rate(agentgateway_llm_tokens_total[5m]))

# Estimated cost (example for GPT-4)
sum(increase(agentgateway_llm_tokens_input_total{model="gpt-4"}[24h])) * 0.00003 +
sum(increase(agentgateway_llm_tokens_output_total{model="gpt-4"}[24h])) * 0.00006
```

### PrometheusRule for Cost Alerts

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: agentgateway-cost-alerts
  namespace: ai-system
spec:
  groups:
    - name: llm-costs
      rules:
        - alert: HighTokenUsage
          expr: sum(increase(agentgateway_llm_tokens_total[1h])) > 100000
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "High LLM token usage detected"
            description: "Token usage exceeded 100k in the last hour"

        - alert: ExpensiveModelOveruse
          expr: sum(rate(agentgateway_llm_requests_total{model=~"gpt-4|claude-3-opus.*"}[1h])) > 100
          for: 10m
          labels:
            severity: warning
          annotations:
            summary: "High usage of expensive models"
```

---

## Distributed Tracing

### GatewayParameters for Tracing

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: agentgateway-params
  namespace: ai-system
spec:
  rawConfig:
    config:
      tracing:
        otlpEndpoint: http://opentelemetry-collector.monitoring.svc.cluster.local:4317
        otlpProtocol: grpc
        randomSampling: true
        # Gen AI semantic conventions
        fields:
          add:
            gen_ai.operation.name: '"chat"'
            gen_ai.system: "llm.provider"
            gen_ai.request.model: "llm.requestModel"
            gen_ai.response.model: "llm.responseModel"
            gen_ai.usage.completion_tokens: "llm.outputTokens"
            gen_ai.usage.prompt_tokens: "llm.inputTokens"
```

### OpenTelemetry Collector Configuration

```yaml
# otel-collector-config.yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: otel-collector-config
  namespace: monitoring
data:
  config.yaml: |
    receivers:
      otlp:
        protocols:
          grpc:
            endpoint: 0.0.0.0:4317
          http:
            endpoint: 0.0.0.0:4318

    processors:
      batch:
        timeout: 10s

    exporters:
      # Traces to Tempo
      otlp/tempo:
        endpoint: tempo.monitoring.svc.cluster.local:4317
        tls:
          insecure: true

      # Metrics to Prometheus
      prometheusremotewrite:
        endpoint: http://prometheus.monitoring.svc.cluster.local:9090/api/v1/write

      # Logs to Loki
      loki:
        endpoint: http://loki.monitoring.svc.cluster.local:3100/loki/api/v1/push

    service:
      pipelines:
        traces:
          receivers: [otlp]
          processors: [batch]
          exporters: [otlp/tempo]
        metrics:
          receivers: [otlp]
          processors: [batch]
          exporters: [prometheusremotewrite]
        logs:
          receivers: [otlp]
          processors: [batch]
          exporters: [loki]
```

---

## Access Logging

Configure detailed access logs:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: HTTPListenerPolicy
metadata:
  name: access-logs
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: Gateway
      name: agentgateway
  accessLog:
    - fileSink:
        path: /dev/stdout
        jsonFormat:
          start_time: "%START_TIME%"
          method: "%REQ(X-ENVOY-ORIGINAL-METHOD?:METHOD)%"
          path: "%REQ(X-ENVOY-ORIGINAL-PATH?:PATH)%"
          protocol: "%PROTOCOL%"
          response_code: "%RESPONSE_CODE%"
          response_flags: "%RESPONSE_FLAGS%"
          bytes_received: "%BYTES_RECEIVED%"
          bytes_sent: "%BYTES_SENT%"
          duration: "%DURATION%"
          upstream_service_time: "%RESP(X-ENVOY-UPSTREAM-SERVICE-TIME)%"
          x_forwarded_for: "%REQ(X-FORWARDED-FOR)%"
          user_agent: "%REQ(USER-AGENT)%"
          request_id: "%REQ(X-REQUEST-ID)%"
          upstream_host: "%UPSTREAM_HOST%"
          # AI-specific fields
          llm_provider: "%REQ(X-LLM-PROVIDER)%"
          llm_model: "%REQ(X-LLM-MODEL)%"
```

---

## Grafana Dashboards

### Import kgateway Dashboard

```bash
# Download dashboard
curl -o kgateway-dashboard.json \
  https://raw.githubusercontent.com/kgateway-dev/kgateway/main/install/grafana/kgateway-dashboard.json

# Create ConfigMap for Grafana
kubectl create configmap kgateway-dashboard \
  -n monitoring \
  --from-file=kgateway.json=kgateway-dashboard.json

# Label for Grafana sidecar
kubectl label configmap kgateway-dashboard \
  -n monitoring \
  grafana_dashboard=1
```

### Custom AI Metrics Dashboard

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: agentgateway-ai-dashboard
  namespace: monitoring
  labels:
    grafana_dashboard: "1"
data:
  agentgateway-ai.json: |
    {
      "title": "AgentGateway AI Metrics",
      "panels": [
        {
          "title": "LLM Requests/sec",
          "type": "graph",
          "targets": [
            {
              "expr": "sum(rate(agentgateway_llm_requests_total[5m])) by (provider, model)",
              "legendFormat": "{{provider}} - {{model}}"
            }
          ]
        },
        {
          "title": "Token Usage",
          "type": "graph",
          "targets": [
            {
              "expr": "sum(rate(agentgateway_llm_tokens_total[5m])) by (provider)",
              "legendFormat": "{{provider}}"
            }
          ]
        },
        {
          "title": "Request Latency P99",
          "type": "graph",
          "targets": [
            {
              "expr": "histogram_quantile(0.99, sum(rate(agentgateway_llm_request_duration_seconds_bucket[5m])) by (le, provider))",
              "legendFormat": "{{provider}} P99"
            }
          ]
        },
        {
          "title": "Estimated Daily Cost (USD)",
          "type": "stat",
          "targets": [
            {
              "expr": "sum(increase(agentgateway_llm_tokens_input_total[24h])) * 0.00003 + sum(increase(agentgateway_llm_tokens_output_total[24h])) * 0.00006"
            }
          ]
        }
      ]
    }
```

---

## Trace Integration Platforms

### Jaeger

```yaml
spec:
  rawConfig:
    config:
      tracing:
        otlpEndpoint: http://jaeger-collector.monitoring.svc.cluster.local:4317
        otlpProtocol: grpc
```

### Langfuse (LLM Observability)

```yaml
spec:
  rawConfig:
    config:
      tracing:
        otlpEndpoint: https://us.cloud.langfuse.com/api/public/otel
        otlpProtocol: http
        headers:
          Authorization: "Basic ${LANGFUSE_AUTH}"
```

### Phoenix (Arize)

```yaml
spec:
  rawConfig:
    config:
      tracing:
        otlpEndpoint: http://phoenix.monitoring.svc.cluster.local:4317
        otlpProtocol: grpc
        # Use OpenInference span format
```

---

## Debugging Endpoints

### Control Plane Debug (Port 9095)

```bash
# Port forward
kubectl port-forward -n ai-system svc/agentgateway 9095:9095 &

# View transformed resources
curl http://localhost:9095/snapshots/krt

# View xDS configuration
curl http://localhost:9095/snapshots/xds

# Adjust log level
curl -X POST http://localhost:9095/logging?level=debug
```

### Data Plane Debug (Port 15000)

```bash
# Port forward
kubectl port-forward -n ai-system deployment/agentgateway-proxy 15000:15000 &

# View configuration
curl http://localhost:15000/config_dump

# Access UI
open http://localhost:15000/ui
```

---

## Health Checks

### Readiness and Liveness Probes

```yaml
# Already configured in HelmRelease
spec:
  template:
    spec:
      containers:
        - name: agentgateway
          livenessProbe:
            httpGet:
              path: /healthz
              port: 9093
            initialDelaySeconds: 10
            periodSeconds: 10
          readinessProbe:
            httpGet:
              path: /readyz
              port: 9093
            initialDelaySeconds: 5
            periodSeconds: 5
```

### Gatus Health Check

```yaml
# In gateway.yaml annotations
annotations:
  gatus.home-operations.com/endpoint: |-
    client:
      dns-resolver: tcp://1.1.1.1:53
    group: ai-system
    conditions:
      - "[STATUS] == 200"
      - "[RESPONSE_TIME] < 500"
```

---

## Alerting Rules

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: agentgateway-alerts
  namespace: ai-system
spec:
  groups:
    - name: agentgateway
      rules:
        # Gateway availability
        - alert: AgentGatewayDown
          expr: up{job="agentgateway"} == 0
          for: 2m
          labels:
            severity: critical
          annotations:
            summary: "AgentGateway is down"

        # High error rate
        - alert: HighLLMErrorRate
          expr: |
            sum(rate(agentgateway_llm_requests_total{status="error"}[5m])) /
            sum(rate(agentgateway_llm_requests_total[5m])) > 0.05
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "LLM error rate > 5%"

        # Latency
        - alert: HighLLMLatency
          expr: |
            histogram_quantile(0.95, sum(rate(agentgateway_llm_request_duration_seconds_bucket[5m])) by (le)) > 30
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "LLM P95 latency > 30s"

        # Token budget
        - alert: DailyTokenBudgetExceeded
          expr: sum(increase(agentgateway_llm_tokens_total[24h])) > 1000000
          for: 1m
          labels:
            severity: warning
          annotations:
            summary: "Daily token budget (1M) exceeded"
```

---

## References

- [OTel Stack Setup](https://kgateway.dev/docs/agentgateway/latest/observability/otel-stack/)
- [Control Plane Metrics](https://kgateway.dev/docs/agentgateway/latest/observability/control-plane-metrics/)
- [LLM Tracing](https://kgateway.dev/docs/agentgateway/latest/llm/tracing/)
- [OpenTelemetry Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/)

---

*See [09-advanced-features.md](./09-advanced-features.md) for failover and inference routing.*
