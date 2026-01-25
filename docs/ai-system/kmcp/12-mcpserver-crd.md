# MCPServer CRD Reference

Complete API reference for the MCPServer Custom Resource Definition.

## Overview

The MCPServer CRD defines MCP servers that run in Kubernetes. The KMCP controller watches these resources and manages their lifecycle.

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: my-mcp-server
  namespace: default
spec:
  # Specification fields
status:
  # Status fields (managed by controller)
```

## Full Specification

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: my-mcp-server
  namespace: default
  labels:
    app: my-mcp-server
    kagent.dev/discovery: "enabled"  # or "disabled"
  annotations:
    description: "My MCP server"
spec:
  # Deployment configuration
  deployment:
    # Container image
    image: "my-mcp-server:latest"

    # Command and arguments
    cmd: "python"
    args:
      - "src/main.py"
      - "--transport"
      - "http"

    # Container port
    port: 3000

    # Number of replicas
    replicas: 1

    # Environment variables
    env:
      - name: LOG_LEVEL
        value: "info"
      - name: API_KEY
        valueFrom:
          secretKeyRef:
            name: api-secrets
            key: key

    # Environment from ConfigMaps/Secrets
    envFrom:
      - secretRef:
          name: my-secrets
      - configMapRef:
          name: my-config

    # Resource limits
    resources:
      limits:
        cpu: "500m"
        memory: "512Mi"
      requests:
        cpu: "100m"
        memory: "128Mi"

    # Volume mounts
    volumeMounts:
      - name: data
        mountPath: /data
      - name: config
        mountPath: /etc/config
        readOnly: true

    # Health checks
    livenessProbe:
      httpGet:
        path: /health
        port: 3000
      initialDelaySeconds: 10
      periodSeconds: 10
      timeoutSeconds: 5
      failureThreshold: 3

    readinessProbe:
      httpGet:
        path: /ready
        port: 3000
      initialDelaySeconds: 5
      periodSeconds: 5
      timeoutSeconds: 3
      failureThreshold: 3

    # Node scheduling
    nodeSelector:
      node-type: compute

    tolerations:
      - key: "dedicated"
        operator: "Equal"
        value: "mcp-servers"
        effect: "NoSchedule"

    affinity:
      podAntiAffinity:
        preferredDuringSchedulingIgnoredDuringExecution:
          - weight: 100
            podAffinityTerm:
              labelSelector:
                matchLabels:
                  app: my-mcp-server
              topologyKey: kubernetes.io/hostname

  # Volumes
  volumes:
    - name: data
      persistentVolumeClaim:
        claimName: data-pvc
    - name: config
      configMap:
        name: server-config

  # Service account
  serviceAccount: my-mcp-server-sa

  # Transport type
  transportType: "http"  # stdio, http

  # stdio transport options
  stdioTransport: {}

  # HTTP transport options
  httpTransport:
    path: "/mcp"
    streamable: true
    sse: false
```

## Spec Fields Reference

### deployment

Container deployment configuration.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `image` | string | No | Container image (required if not using cmd) |
| `cmd` | string | Yes* | Command to run |
| `args` | []string | No | Command arguments |
| `port` | int | No | Container port (default: 3000) |
| `replicas` | int | No | Number of replicas (default: 1) |
| `env` | []EnvVar | No | Environment variables |
| `envFrom` | []EnvFromSource | No | Environment from sources |
| `resources` | ResourceRequirements | No | CPU/memory limits |
| `volumeMounts` | []VolumeMount | No | Volume mounts |
| `livenessProbe` | Probe | No | Liveness probe config |
| `readinessProbe` | Probe | No | Readiness probe config |
| `nodeSelector` | map[string]string | No | Node selector labels |
| `tolerations` | []Toleration | No | Node tolerations |
| `affinity` | Affinity | No | Pod affinity rules |

### env

Environment variable configuration.

```yaml
env:
  # Simple value
  - name: LOG_LEVEL
    value: "info"

  # From Secret
  - name: API_KEY
    valueFrom:
      secretKeyRef:
        name: my-secrets
        key: api-key

  # From ConfigMap
  - name: CONFIG_VALUE
    valueFrom:
      configMapKeyRef:
        name: my-config
        key: config-value

  # From field
  - name: POD_NAME
    valueFrom:
      fieldRef:
        fieldPath: metadata.name
```

### envFrom

Load all keys from a source.

```yaml
envFrom:
  # From Secret
  - secretRef:
      name: my-secrets
    prefix: "SECRET_"  # Optional prefix

  # From ConfigMap
  - configMapRef:
      name: my-config
    prefix: "CONFIG_"
```

### resources

Resource limits and requests.

```yaml
resources:
  limits:
    cpu: "1"
    memory: "1Gi"
    ephemeral-storage: "10Gi"
  requests:
    cpu: "100m"
    memory: "128Mi"
    ephemeral-storage: "1Gi"
```

### volumes

Volume definitions.

```yaml
volumes:
  # PersistentVolumeClaim
  - name: data
    persistentVolumeClaim:
      claimName: data-pvc

  # ConfigMap
  - name: config
    configMap:
      name: server-config
      items:
        - key: config.yaml
          path: config.yaml

  # Secret
  - name: certs
    secret:
      secretName: tls-certs
      defaultMode: 0400

  # EmptyDir
  - name: cache
    emptyDir:
      sizeLimit: 1Gi

  # HostPath (use with caution)
  - name: host-data
    hostPath:
      path: /data
      type: Directory
```

### volumeMounts

Mount volumes into containers.

```yaml
volumeMounts:
  - name: data
    mountPath: /data
    readOnly: false
    subPath: subdir  # Optional

  - name: config
    mountPath: /etc/config
    readOnly: true
```

### livenessProbe / readinessProbe

Health check configuration.

```yaml
livenessProbe:
  # HTTP check
  httpGet:
    path: /health
    port: 3000
    scheme: HTTP
    httpHeaders:
      - name: X-Custom-Header
        value: "value"

  # TCP check
  tcpSocket:
    port: 3000

  # Exec check
  exec:
    command:
      - /bin/sh
      - -c
      - "curl -f http://localhost:3000/health"

  # Timing
  initialDelaySeconds: 10
  periodSeconds: 10
  timeoutSeconds: 5
  successThreshold: 1
  failureThreshold: 3
```

### transportType

MCP transport protocol.

| Value | Description |
|-------|-------------|
| `stdio` | Standard input/output (default) |
| `http` | HTTP/REST protocol |

### stdioTransport

Options for stdio transport.

```yaml
stdioTransport: {}
```

Currently no additional options for stdio transport.

### httpTransport

Options for HTTP transport.

```yaml
httpTransport:
  path: "/mcp"          # MCP endpoint path
  streamable: true      # Enable streaming
  sse: false            # Use Server-Sent Events
```

## Status Fields

The controller manages these status fields:

```yaml
status:
  phase: Running        # Pending, Running, Failed
  conditions:
    - type: Ready
      status: "True"
      lastTransitionTime: "2024-01-15T10:30:00Z"
      reason: DeploymentReady
      message: "All replicas are ready"
  replicas: 1
  readyReplicas: 1
  observedGeneration: 1
```

### phase

| Value | Description |
|-------|-------------|
| `Pending` | Resource created, deployment starting |
| `Running` | Deployment ready |
| `Failed` | Deployment failed |

### conditions

| Type | Description |
|------|-------------|
| `Ready` | All replicas are ready |
| `Available` | Minimum replicas available |
| `Progressing` | Deployment in progress |

## Labels and Annotations

### Standard Labels

```yaml
metadata:
  labels:
    # Managed by KMCP
    app.kubernetes.io/managed-by: kmcp
    app.kubernetes.io/name: my-mcp-server
    app.kubernetes.io/version: "1.0.0"

    # kagent discovery
    kagent.dev/discovery: "enabled"  # or "disabled"

    # Custom labels
    team: platform
    environment: staging
```

### Annotations

```yaml
metadata:
  annotations:
    # Documentation
    description: "Weather data MCP server"

    # Reloader integration
    reloader.stakater.com/auto: "true"

    # Prometheus scraping
    prometheus.io/scrape: "true"
    prometheus.io/port: "8080"
    prometheus.io/path: "/metrics"
```

## Complete Examples

### Python FastMCP Server

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: weather-server
  namespace: mcp-tools
  labels:
    app: weather-server
    team: data
spec:
  deployment:
    image: "ghcr.io/myorg/weather-server:v1.2.0"
    cmd: "python"
    args: ["src/main.py"]
    port: 3000
    replicas: 2
    env:
      - name: WEATHER_API_KEY
        valueFrom:
          secretKeyRef:
            name: weather-secrets
            key: api-key
    resources:
      limits:
        cpu: "500m"
        memory: "512Mi"
      requests:
        cpu: "100m"
        memory: "128Mi"
    livenessProbe:
      httpGet:
        path: /health
        port: 3000
      initialDelaySeconds: 10
      periodSeconds: 10
    readinessProbe:
      httpGet:
        path: /health
        port: 3000
      initialDelaySeconds: 5
      periodSeconds: 5
  transportType: stdio
  stdioTransport: {}
```

### Go MCP Server with HTTP

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: database-server
  namespace: mcp-tools
spec:
  deployment:
    image: "database-server:latest"
    cmd: "./server"
    args: ["--transport", "http", "--port", "8080"]
    port: 8080
    env:
      - name: DATABASE_URL
        valueFrom:
          secretKeyRef:
            name: db-secrets
            key: url
    resources:
      limits:
        cpu: "1"
        memory: "1Gi"
  transportType: http
  httpTransport:
    path: "/mcp"
    streamable: true
```

### npx Package Server

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: github-server
  namespace: default
spec:
  deployment:
    cmd: npx
    args:
      - "-y"
      - "@modelcontextprotocol/server-github"
    port: 3000
    env:
      - name: GITHUB_TOKEN
        valueFrom:
          secretKeyRef:
            name: github-secrets
            key: token
  transportType: stdio
  stdioTransport: {}
```

### High-Availability Configuration

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: ha-server
  namespace: production
spec:
  deployment:
    image: "my-server:latest"
    cmd: "python"
    args: ["src/main.py"]
    port: 3000
    replicas: 3
    resources:
      limits:
        cpu: "1"
        memory: "2Gi"
      requests:
        cpu: "500m"
        memory: "1Gi"
    affinity:
      podAntiAffinity:
        requiredDuringSchedulingIgnoredDuringExecution:
          - labelSelector:
              matchLabels:
                app.kubernetes.io/name: ha-server
            topologyKey: kubernetes.io/hostname
    livenessProbe:
      httpGet:
        path: /health
        port: 3000
      initialDelaySeconds: 15
      periodSeconds: 10
      failureThreshold: 3
    readinessProbe:
      httpGet:
        path: /ready
        port: 3000
      initialDelaySeconds: 5
      periodSeconds: 5
      failureThreshold: 2
  transportType: http
  httpTransport:
    path: "/mcp"
```

## Validation

The controller validates MCPServer resources:

| Check | Error |
|-------|-------|
| Missing cmd and image | "Either image or cmd must be specified" |
| Invalid transportType | "transportType must be 'stdio' or 'http'" |
| Port out of range | "port must be between 1 and 65535" |
| Negative replicas | "replicas must be non-negative" |

## Next Steps

- [CLI Reference](./13-cli-reference.md) - All CLI commands
- [Deploying Servers](./08-deploying-servers.md) - Deployment guide
- [HTTP Transport](./10-http-transport.md) - HTTP configuration
