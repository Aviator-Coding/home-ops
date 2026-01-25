# Observability Guide

> **Set up comprehensive monitoring with OpenTelemetry, Prometheus, Grafana, and distributed tracing.**

## Overview

Agentgateway provides full observability through OpenTelemetry integration:

| Component | Purpose | Backend |
|-----------|---------|---------|
| **Metrics** | Performance and usage data | Prometheus |
| **Logs** | Request/response logging | Grafana Loki |
| **Traces** | Distributed request tracing | Grafana Tempo |

### Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Applications                                  │
│                         ↓                                        │
│                 Agentgateway Proxy                               │
│                         ↓                                        │
│              OpenTelemetry Collectors                            │
│           ↙           ↓           ↘                              │
│      Prometheus     Loki       Tempo                             │
│           ↘           ↓           ↙                              │
│                    Grafana                                       │
└─────────────────────────────────────────────────────────────────┘
```

---

## OpenTelemetry Stack Deployment

### Prerequisites

- Helm 3.x installed
- Kubernetes 1.25+
- `kgateway-system` namespace exists

### Step 1: Deploy Grafana Loki (Logs)

```bash
helm upgrade --install loki loki \
  --repo https://grafana.github.io/helm-charts \
  --version 6.24.0 \
  --namespace telemetry \
  --create-namespace \
  --values - <<EOF
loki:
  commonConfig:
    replication_factor: 1
  schemaConfig:
    configs:
      - from: 2024-04-01
        store: tsdb
        object_store: s3
        schema: v13
        index:
          prefix: loki_index_
          period: 24h
  auth_enabled: false
singleBinary:
  replicas: 1
minio:
  enabled: true
gateway:
  enabled: false
deploymentMode: SingleBinary
EOF
```

### Step 2: Deploy Grafana Tempo (Traces)

```bash
helm upgrade --install tempo tempo \
  --repo https://grafana.github.io/helm-charts \
  --version 1.16.0 \
  --namespace telemetry \
  --create-namespace \
  --values - <<EOF
persistence:
  enabled: false
tempo:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
EOF
```

### Step 3: Deploy OpenTelemetry Collectors

#### Metrics Collector

```bash
helm upgrade --install opentelemetry-collector-metrics opentelemetry-collector \
  --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
  --version 0.127.2 \
  --namespace telemetry \
  --values - <<EOF
mode: deployment
replicaCount: 1
image:
  repository: otel/opentelemetry-collector-contrib
config:
  receivers:
    prometheus:
      config:
        scrape_configs:
          - job_name: 'kgateway-dataplane'
            kubernetes_sd_configs:
              - role: pod
            relabel_configs:
              - source_labels: [__meta_kubernetes_pod_label_gateway]
                action: keep
                regex: agentgateway-proxy
          - job_name: 'kgateway-controlplane'
            kubernetes_sd_configs:
              - role: pod
            relabel_configs:
              - source_labels: [__meta_kubernetes_pod_label_app]
                action: keep
                regex: kgateway
  exporters:
    prometheusremotewrite:
      endpoint: "http://kube-prometheus-stack-prometheus.telemetry:9090/api/v1/write"
    debug:
      verbosity: detailed
  service:
    pipelines:
      metrics:
        receivers: [prometheus]
        exporters: [prometheusremotewrite, debug]
ports:
  metrics:
    enabled: true
    containerPort: 8888
    servicePort: 8888
    protocol: TCP
EOF
```

#### Logs Collector

```bash
helm upgrade --install opentelemetry-collector-logs opentelemetry-collector \
  --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
  --version 0.127.2 \
  --namespace telemetry \
  --values - <<EOF
mode: deployment
replicaCount: 1
image:
  repository: otel/opentelemetry-collector-contrib
config:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318
  exporters:
    otlphttp:
      endpoint: "http://loki.telemetry:3100/otlp"
    debug:
      verbosity: detailed
  service:
    pipelines:
      logs:
        receivers: [otlp]
        exporters: [otlphttp, debug]
ports:
  otlp:
    enabled: true
    containerPort: 4317
    servicePort: 4317
    protocol: TCP
  otlp-http:
    enabled: true
    containerPort: 4318
    servicePort: 4318
    protocol: TCP
EOF
```

#### Traces Collector

```bash
helm upgrade --install opentelemetry-collector-traces opentelemetry-collector \
  --repo https://open-telemetry.github.io/opentelemetry-helm-charts \
  --version 0.127.2 \
  --namespace telemetry \
  --values - <<EOF
mode: deployment
replicaCount: 1
image:
  repository: otel/opentelemetry-collector-contrib
config:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318
  exporters:
    otlp:
      endpoint: "tempo.telemetry:4317"
      tls:
        insecure: true
    debug:
      verbosity: detailed
  service:
    pipelines:
      traces:
        receivers: [otlp]
        exporters: [otlp, debug]
ports:
  otlp:
    enabled: true
    containerPort: 4317
    servicePort: 4317
    protocol: TCP
  otlp-http:
    enabled: true
    containerPort: 4318
    servicePort: 4318
    protocol: TCP
EOF
```

### Step 4: Deploy Prometheus and Grafana

```bash
helm upgrade --install kube-prometheus-stack kube-prometheus-stack \
  --repo https://prometheus-community.github.io/helm-charts \
  --version 75.6.1 \
  --namespace telemetry \
  --create-namespace \
  --values - <<EOF
alertmanager:
  enabled: false
prometheus:
  prometheusSpec:
    enableFeatures:
      - native-histograms
    enableRemoteWriteReceiver: true
grafana:
  enabled: true
  defaultDashboardsEnabled: true
  additionalDataSources:
    - name: Loki
      type: loki
      url: http://loki.telemetry:3100
    - name: Tempo
      type: tempo
      url: http://tempo.telemetry:3100
EOF
```

---

## Control Plane Metrics

### Available Metrics

The kgateway control plane exposes the following metrics:

| Metric | Type | Description |
|--------|------|-------------|
| `kgateway_translation_count` | Counter | Total translation operations |
| `kgateway_reconciliation_count` | Counter | Total reconciliation operations |
| `kgateway_sync_rate` | Gauge | Rate of config synchronization |
| `kgateway_xds_resources` | Gauge | Number of XDS resources |
| `kgateway_translation_latency` | Histogram | Translation operation latency |
| `kgateway_reconciliation_latency` | Histogram | Reconciliation latency |

### Prometheus Query Examples

**Translation Rate:**
```promql
rate(kgateway_translation_count[5m])
```

**Reconciliation Latency (p99):**
```promql
histogram_quantile(0.99, rate(kgateway_reconciliation_latency_bucket[5m]))
```

**XDS Resource Count:**
```promql
kgateway_xds_resources
```

---

## Data Plane Metrics

### AI/LLM Specific Metrics

| Metric | Description |
|--------|-------------|
| `agentgateway_llm_requests_total` | Total LLM requests |
| `agentgateway_llm_tokens_total` | Total tokens processed |
| `agentgateway_llm_latency_seconds` | LLM request latency |
| `agentgateway_llm_errors_total` | LLM request errors |

### General Proxy Metrics

| Metric | Description |
|--------|-------------|
| `agentgateway_http_requests_total` | Total HTTP requests |
| `agentgateway_http_request_duration_seconds` | Request duration |
| `agentgateway_active_connections` | Current active connections |
| `agentgateway_bytes_received_total` | Total bytes received |
| `agentgateway_bytes_sent_total` | Total bytes sent |

---

## Access Logging

### Enable Access Logs

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: HTTPListenerPolicy
metadata:
  name: access-logging
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: agentgateway-proxy
  accessLog:
  - fileSink:
      path: /dev/stdout
      jsonFormat:
        timestamp: "%START_TIME%"
        request_id: "%REQ(X-REQUEST-ID)%"
        method: "%REQ(:METHOD)%"
        path: "%REQ(:PATH)%"
        protocol: "%PROTOCOL%"
        response_code: "%RESPONSE_CODE%"
        response_flags: "%RESPONSE_FLAGS%"
        bytes_received: "%BYTES_RECEIVED%"
        bytes_sent: "%BYTES_SENT%"
        duration: "%DURATION%"
        upstream_host: "%UPSTREAM_HOST%"
        upstream_cluster: "%UPSTREAM_CLUSTER%"
        user_agent: "%REQ(USER-AGENT)%"
```

### Send Logs to OTel Collector

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: HTTPListenerPolicy
metadata:
  name: otel-access-logging
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: agentgateway-proxy
  accessLog:
  - grpcService:
      logName: otel_access_log
      staticClusterName: otel-collector
```

---

## Distributed Tracing

### Enable Tracing

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: HTTPListenerPolicy
metadata:
  name: tracing
  namespace: kgateway-system
spec:
  targetRefs:
  - group: gateway.networking.k8s.io
    kind: Gateway
    name: agentgateway-proxy
  tracing:
    provider:
      otel:
        grpcAddress: otel-collector.telemetry:4317
    samplingRate: 100  # 100% sampling for testing, reduce in production
    propagators:
    - W3C_TRACE_CONTEXT
    - B3
```

### Trace Context Propagation

Agentgateway supports multiple trace context formats:

| Propagator | Header Format |
|------------|---------------|
| `W3C_TRACE_CONTEXT` | `traceparent`, `tracestate` |
| `B3` | `X-B3-TraceId`, `X-B3-SpanId`, `X-B3-Sampled` |
| `B3_SINGLE_HEADER` | `b3` |

### Custom Span Attributes

```yaml
tracing:
  provider:
    otel:
      grpcAddress: otel-collector.telemetry:4317
  customTags:
    environment:
      literal: "production"
    cluster:
      environment: "CLUSTER_NAME"
    request_id:
      requestHeader: "x-request-id"
```

---

## Grafana Dashboards

### Import Agentgateway Dashboard

Create a ConfigMap with the dashboard JSON:

```bash
kubectl create configmap kgateway-dashboard \
  --namespace telemetry \
  --from-file=dashboard.json \
  --dry-run=client -o yaml | \
  kubectl label -f - grafana_dashboard=1 --local -o yaml | \
  kubectl apply -f -
```

### Dashboard Panels

The Agentgateway dashboard displays:

| Panel | Description |
|-------|-------------|
| Active Translations | Current translation operations |
| Reconciliations | Reconciliation rate and status |
| Resource Counts | XDS resources by type |
| Translation Latency | p70, p90, p99 latency |
| Reconciliation Latency | p70, p90, p99 latency |
| Sync Rate | Configuration sync frequency |
| Error Rate | Translation and sync errors |

---

## Alerting

### Prometheus AlertManager Rules

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: agentgateway-alerts
  namespace: telemetry
spec:
  groups:
  - name: agentgateway
    rules:
    - alert: AgentgatewayHighErrorRate
      expr: rate(agentgateway_llm_errors_total[5m]) > 0.1
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: High LLM error rate
        description: "Error rate is {{ $value }} errors/sec"

    - alert: AgentgatewayHighLatency
      expr: histogram_quantile(0.99, rate(agentgateway_llm_latency_seconds_bucket[5m])) > 10
      for: 5m
      labels:
        severity: warning
      annotations:
        summary: High LLM latency
        description: "p99 latency is {{ $value }} seconds"

    - alert: ControlPlaneReconciliationFailures
      expr: rate(kgateway_reconciliation_count{status="failed"}[5m]) > 0
      for: 2m
      labels:
        severity: critical
      annotations:
        summary: Control plane reconciliation failures
        description: "Reconciliation failures detected"
```

---

## Cleanup

Remove the observability stack:

```bash
# Remove dashboards
kubectl delete configmap kgateway-dashboard -n telemetry

# Remove collectors
helm uninstall opentelemetry-collector-metrics -n telemetry
helm uninstall opentelemetry-collector-logs -n telemetry
helm uninstall opentelemetry-collector-traces -n telemetry

# Remove backends
helm uninstall loki -n telemetry
helm uninstall tempo -n telemetry
helm uninstall kube-prometheus-stack -n telemetry

# Remove namespace
kubectl delete namespace telemetry
```

---

## Best Practices

### 1. Sampling Strategy

For production:
- Use 10-25% trace sampling for high-traffic routes
- Use 100% sampling for error traces
- Use head-based sampling for consistent trace completeness

### 2. Log Retention

Configure appropriate retention:
- Access logs: 7-14 days
- Error logs: 30-90 days
- Traces: 7-14 days

### 3. Dashboard Organization

Create dashboards for:
- **Overview**: High-level health and traffic
- **LLM Performance**: Per-provider latency and tokens
- **Security**: Access denials, prompt guard triggers
- **Troubleshooting**: Error details and traces

### 4. Remove Debug Exporters

Remove `debug` exporters in production to prevent performance degradation:

```yaml
# Remove this in production
exporters:
  debug:
    verbosity: detailed
```

---

*See [09-advanced-features.md](./09-advanced-features.md) for model failover, function calling, and inference routing.*
