# Secrets Management

KMCP provides built-in functionality to bootstrap MCP servers with environment variables stored in Kubernetes secrets. This enables secure management of API keys, database credentials, and other sensitive configuration.

## Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                     Secrets Workflow                             │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   .env.staging          kmcp secrets sync         K8s Secret    │
│   ┌──────────────┐      ─────────────────▶      ┌────────────┐ │
│   │ API_KEY=xxx  │                               │  Opaque    │ │
│   │ DB_URL=yyy   │                               │  Secret    │ │
│   └──────────────┘                               └─────┬──────┘ │
│                                                        │        │
│                                                        ▼        │
│                                              ┌─────────────────┐│
│                                              │    MCPServer    ││
│                                              │    (Pod)        ││
│                                              │                 ││
│                                              │ ENV: API_KEY    ││
│                                              │ ENV: DB_URL     ││
│                                              └─────────────────┘│
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Configuration

### kmcp.yaml Secrets Section

```yaml
name: my-mcp-server
version: 0.1.0

secrets:
  local:
    enabled: false
    provider: env
    file: .env.local

  staging:
    enabled: true
    provider: kubernetes
    secretName: my-mcp-server-secrets-staging
    namespace: default

  production:
    enabled: true
    provider: kubernetes
    secretName: my-mcp-server-secrets-production
    namespace: production
```

### Configuration Fields

| Field | Description |
|-------|-------------|
| `enabled` | Whether this environment is active |
| `provider` | Secret provider: `env` (local file) or `kubernetes` |
| `file` | Path to .env file (for local provider) |
| `secretName` | Kubernetes secret name |
| `namespace` | Kubernetes namespace for the secret |

## Setting Up Secrets

### Step 1: Create Environment File

Create a `.env` file for your environment:

**.env.staging:**

```bash
# API Keys
WEATHER_API_KEY=sk-weather-abc123
OPENAI_API_KEY=sk-openai-xyz789

# Database
DATABASE_URL=postgresql://user:pass@host:5432/db
REDIS_URL=redis://redis:6379

# Configuration
LOG_LEVEL=debug
MAX_CONNECTIONS=100
```

### Step 2: Enable Environment in kmcp.yaml

```yaml
secrets:
  staging:
    enabled: true
    provider: kubernetes
    secretName: my-mcp-server-secrets-staging
    namespace: default
```

### Step 3: Sync to Kubernetes

```bash
kmcp secrets sync staging \
  --from-file my-mcp-server/.env.staging \
  --project-dir my-mcp-server
```

### Step 4: Verify Secret Creation

```bash
kubectl get secret my-mcp-server-secrets-staging -o yaml
```

## CLI Commands

### kmcp secrets sync

Sync environment variables to Kubernetes secrets.

```bash
kmcp secrets sync <environment> [flags]
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--from-file` | Source .env file (default: ".env") |
| `--project-dir, -d` | Project directory |
| `--dry-run` | Output YAML without applying |

**Examples:**

```bash
# Sync staging secrets
kmcp secrets sync staging --from-file .env.staging --project-dir my-server

# Sync production secrets
kmcp secrets sync production --from-file .env.production --project-dir my-server

# Preview without applying
kmcp secrets sync staging --from-file .env.staging --dry-run
```

## Using Secrets in Deployments

### Automatic Integration

When deploying with an environment that has secrets enabled:

```bash
kmcp deploy --environment staging --file my-mcp-server/kmcp.yaml --image my-mcp-server:latest
```

The deployment automatically includes:

```yaml
spec:
  containers:
    - name: my-mcp-server
      envFrom:
        - secretRef:
            name: my-mcp-server-secrets-staging
```

### Manual Secret Reference

For direct MCPServer resources:

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: my-mcp-server
  namespace: default
spec:
  deployment:
    image: "my-mcp-server:latest"
    port: 3000
    envFrom:
      - secretRef:
          name: my-mcp-server-secrets-staging
```

### Individual Environment Variables

Reference specific keys:

```yaml
spec:
  deployment:
    env:
      - name: API_KEY
        valueFrom:
          secretKeyRef:
            name: my-mcp-server-secrets-staging
            key: WEATHER_API_KEY
      - name: DB_CONNECTION
        valueFrom:
          secretKeyRef:
            name: my-mcp-server-secrets-staging
            key: DATABASE_URL
```

## Multi-Environment Setup

### Complete Configuration

```yaml
# kmcp.yaml
name: my-mcp-server
version: 0.1.0

secrets:
  # Local development
  local:
    enabled: true
    provider: env
    file: .env.local

  # Development cluster
  development:
    enabled: true
    provider: kubernetes
    secretName: my-mcp-server-secrets-dev
    namespace: dev

  # Staging cluster
  staging:
    enabled: true
    provider: kubernetes
    secretName: my-mcp-server-secrets-staging
    namespace: staging

  # Production cluster
  production:
    enabled: true
    provider: kubernetes
    secretName: my-mcp-server-secrets-production
    namespace: production
```

### Environment Files

```
my-mcp-server/
├── .env.local          # Local development
├── .env.development    # Dev cluster
├── .env.staging        # Staging cluster
├── .env.production     # Production cluster
└── .env.example        # Template with no values
```

### Sync All Environments

```bash
# Development
kmcp secrets sync development --from-file .env.development

# Staging
kmcp secrets sync staging --from-file .env.staging

# Production
kmcp secrets sync production --from-file .env.production
```

## Secret Management Best Practices

### 1. Never Commit Secrets

**.gitignore:**

```gitignore
# Environment files with secrets
.env
.env.*
!.env.example

# Kubernetes secret manifests
*-secrets.yaml
```

### 2. Use Secret Templates

**.env.example:**

```bash
# Copy to .env.<environment> and fill in values

# Required API Keys
WEATHER_API_KEY=
OPENAI_API_KEY=

# Database Configuration
DATABASE_URL=

# Optional Configuration
LOG_LEVEL=info
MAX_CONNECTIONS=50
```

### 3. Validate Required Secrets

In your MCP server:

```python
import os
import sys

REQUIRED_SECRETS = [
    "WEATHER_API_KEY",
    "DATABASE_URL",
]

def validate_environment():
    missing = [key for key in REQUIRED_SECRETS if not os.environ.get(key)]
    if missing:
        print(f"ERROR: Missing required environment variables: {missing}")
        sys.exit(1)

validate_environment()
```

### 4. Use Kubernetes Secret Encryption

Enable encryption at rest:

```yaml
# encryption-config.yaml
apiVersion: apiserver.config.k8s.io/v1
kind: EncryptionConfiguration
resources:
  - resources:
      - secrets
    providers:
      - aescbc:
          keys:
            - name: key1
              secret: <base64-encoded-key>
      - identity: {}
```

## External Secret Management

### HashiCorp Vault Integration

Use External Secrets Operator with Vault:

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: my-mcp-server-secrets
  namespace: default
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: SecretStore
    name: vault-backend
  target:
    name: my-mcp-server-secrets-staging
  data:
    - secretKey: WEATHER_API_KEY
      remoteRef:
        key: secret/data/mcp-servers/weather
        property: api_key
    - secretKey: DATABASE_URL
      remoteRef:
        key: secret/data/mcp-servers/database
        property: url
```

### AWS Secrets Manager

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: my-mcp-server-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: SecretStore
    name: aws-secretsmanager
  target:
    name: my-mcp-server-secrets-staging
  dataFrom:
    - extract:
        key: mcp-servers/my-server/staging
```

### Azure Key Vault

```yaml
apiVersion: external-secrets.io/v1beta1
kind: ExternalSecret
metadata:
  name: my-mcp-server-secrets
spec:
  refreshInterval: 1h
  secretStoreRef:
    kind: SecretStore
    name: azure-keyvault
  target:
    name: my-mcp-server-secrets-staging
  data:
    - secretKey: WEATHER_API_KEY
      remoteRef:
        key: weather-api-key
```

## Rotating Secrets

### Manual Rotation

```bash
# Update .env file with new values
vim .env.staging

# Sync updated secrets
kmcp secrets sync staging --from-file .env.staging

# Restart pods to pick up new secrets
kubectl rollout restart deployment/my-mcp-server
```

### Automatic Rotation with Reloader

Deploy [Reloader](https://github.com/stakater/Reloader) for automatic restarts:

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: my-mcp-server
  annotations:
    reloader.stakater.com/auto: "true"
spec:
  # ...
```

## Troubleshooting

### Secret Not Found

```bash
# Check secret exists
kubectl get secret my-mcp-server-secrets-staging

# Check namespace
kubectl get secret my-mcp-server-secrets-staging -n staging
```

### Environment Variable Not Set

```bash
# Check pod environment
kubectl exec deploy/my-mcp-server -- env | grep API_KEY

# Check secret content
kubectl get secret my-mcp-server-secrets-staging -o jsonpath='{.data.API_KEY}' | base64 -d
```

### Permission Denied

```bash
# Check RBAC
kubectl auth can-i get secrets --as=system:serviceaccount:default:default
```

## Security Audit

### List All Secrets Used

```bash
# Find all secrets referenced by MCPServers
kubectl get mcpserver -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.deployment.envFrom[*].secretRef.name}{"\n"}{end}'
```

### Check Secret Access

```bash
# Who can read secrets?
kubectl auth can-i --list | grep secrets
```

## Next Steps

- [MCPServer CRD](./12-mcpserver-crd.md) - Full API reference
- [CLI Reference](./13-cli-reference.md) - All CLI commands
- [Deploying Servers](./08-deploying-servers.md) - Deployment guide
