# KMCP Controller Setup

The KMCP controller manages the lifecycle of MCP servers in your Kubernetes cluster. It watches for MCPServer Custom Resources and automatically creates and manages the necessary Pods and Services.

## Prerequisites

| Tool | Purpose | Verification |
|------|---------|--------------|
| **Kubernetes cluster** | Target environment | `kubectl cluster-info` |
| **Helm 3.8+** | Chart installation | `helm version` |
| **kubectl** | Cluster access | `kubectl get nodes` |
| **KMCP CLI** | Controller installation | `kmcp --help` |

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                     Kubernetes Cluster                          │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   ┌───────────────────────────────────────────────────────┐     │
│   │                  kmcp-system namespace                 │     │
│   │                                                        │     │
│   │   ┌────────────────────────────────────────────┐      │     │
│   │   │         kmcp-controller-manager             │      │     │
│   │   │                                             │      │     │
│   │   │  • Watches MCPServer resources              │      │     │
│   │   │  • Creates Pods and Services                │      │     │
│   │   │  • Manages lifecycle                        │      │     │
│   │   └────────────────────────────────────────────┘      │     │
│   │                         │                              │     │
│   └─────────────────────────┼──────────────────────────────┘     │
│                             │                                    │
│                             ▼                                    │
│   ┌───────────────────────────────────────────────────────┐     │
│   │                  default namespace                     │     │
│   │                                                        │     │
│   │   ┌─────────────┐    ┌─────────────┐                  │     │
│   │   │  MCPServer  │    │  MCPServer  │                  │     │
│   │   │  (weather)  │    │  (database) │                  │     │
│   │   └──────┬──────┘    └──────┬──────┘                  │     │
│   │          │                   │                         │     │
│   │          ▼                   ▼                         │     │
│   │   ┌─────────────┐    ┌─────────────┐                  │     │
│   │   │    Pod      │    │    Pod      │                  │     │
│   │   │  + Service  │    │  + Service  │                  │     │
│   │   └─────────────┘    └─────────────┘                  │     │
│   │                                                        │     │
│   └────────────────────────────────────────────────────────┘     │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

## Installation Steps

### Step 1: Create or Select Cluster

**Option A: Create a new Kind cluster**

```bash
kind create cluster --name mcp-cluster
```

**Option B: Use existing cluster**

```bash
# List available contexts
kubectl config get-contexts

# Switch to desired context
kubectl config use-context <context-name>

# Verify access
kubectl get nodes
```

### Step 2: Install CRDs via Helm

The CRDs must be installed separately to ensure proper resource definitions:

```bash
helm install kmcp-crds oci://ghcr.io/kagent-dev/kmcp/helm/kmcp-crds \
  --namespace kmcp-system \
  --create-namespace
```

**Verify CRD installation:**

```bash
kubectl get crds | grep kagent
```

Expected output:

```
mcpservers.kagent.dev    2024-01-15T10:30:00Z
```

### Step 3: Deploy Controller

```bash
kmcp install
```

**Expected output:**

```
Installing KMCP controller to namespace kmcp-system...
✓ Deployed KMCP controller
✓ Created ClusterRole and ClusterRoleBinding
Status: deployed

Helpful commands:
  kubectl get pods -n kmcp-system
  kubectl logs -l app.kubernetes.io/name=kmcp -n kmcp-system
```

### Step 4: Verify Deployment

**Check controller pod:**

```bash
kubectl get pods -n kmcp-system
```

Expected output:

```
NAME                                      READY   STATUS    RESTARTS   AGE
kmcp-controller-manager-5d7b9c8f4-x2k9p   1/1     Running   0          30s
```

**Check controller logs:**

```bash
kubectl logs -l app.kubernetes.io/name=kmcp -n kmcp-system --tail=50
```

## Installation Options

### Custom Namespace

```bash
kmcp install --namespace custom-kmcp-namespace
```

### Specific Version

```bash
kmcp install --version v1.2.3
```

### Verbose Output

```bash
kmcp install --verbose
```

## Helm Chart Configuration

For advanced configurations, you can install via Helm directly:

```bash
helm install kmcp oci://ghcr.io/kagent-dev/kmcp/helm/kmcp \
  --namespace kmcp-system \
  --create-namespace \
  --set controller.replicas=2 \
  --set controller.resources.limits.memory=512Mi
```

### Available Helm Values

| Value | Default | Description |
|-------|---------|-------------|
| `controller.replicas` | 1 | Number of controller replicas |
| `controller.image.repository` | ghcr.io/kagent-dev/kmcp | Controller image |
| `controller.image.tag` | (chart version) | Controller image tag |
| `controller.resources.limits.cpu` | 500m | CPU limit |
| `controller.resources.limits.memory` | 256Mi | Memory limit |
| `controller.resources.requests.cpu` | 100m | CPU request |
| `controller.resources.requests.memory` | 64Mi | Memory request |

## RBAC Configuration

The controller creates the following RBAC resources:

### ClusterRole

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: kmcp-controller-manager
rules:
  - apiGroups: ["kagent.dev"]
    resources: ["mcpservers"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["kagent.dev"]
    resources: ["mcpservers/status"]
    verbs: ["get", "update", "patch"]
  - apiGroups: [""]
    resources: ["pods", "services", "secrets"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
  - apiGroups: ["apps"]
    resources: ["deployments"]
    verbs: ["get", "list", "watch", "create", "update", "patch", "delete"]
```

### ClusterRoleBinding

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: kmcp-controller-manager
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: kmcp-controller-manager
subjects:
  - kind: ServiceAccount
    name: kmcp-controller-manager
    namespace: kmcp-system
```

## Production Considerations

### High Availability

For production deployments, run multiple controller replicas:

```bash
helm upgrade kmcp oci://ghcr.io/kagent-dev/kmcp/helm/kmcp \
  --namespace kmcp-system \
  --set controller.replicas=3
```

### Resource Limits

Adjust resource limits based on the number of MCP servers:

```bash
helm upgrade kmcp oci://ghcr.io/kagent-dev/kmcp/helm/kmcp \
  --namespace kmcp-system \
  --set controller.resources.limits.memory=1Gi \
  --set controller.resources.limits.cpu=1
```

### Monitoring

The controller exposes Prometheus metrics on port 8080:

```bash
# Port-forward for local access
kubectl port-forward -n kmcp-system svc/kmcp-controller-manager 8080:8080

# Access metrics
curl http://localhost:8080/metrics
```

### Logging

Configure log level via environment variable:

```yaml
# In Helm values
controller:
  env:
    - name: LOG_LEVEL
      value: "debug"  # info, debug, warn, error
```

## Troubleshooting

### Controller Not Starting

**Check pod status:**

```bash
kubectl describe pod -l app.kubernetes.io/name=kmcp -n kmcp-system
```

**Common issues:**

| Issue | Cause | Solution |
|-------|-------|----------|
| `ImagePullBackOff` | Cannot pull image | Check image name and registry access |
| `CrashLoopBackOff` | Controller crash | Check logs for errors |
| `Pending` | Insufficient resources | Check node resources |

### CRD Not Found

If MCPServer resources can't be created:

```bash
# Reinstall CRDs
helm uninstall kmcp-crds -n kmcp-system
helm install kmcp-crds oci://ghcr.io/kagent-dev/kmcp/helm/kmcp-crds \
  --namespace kmcp-system
```

### Permission Issues

Verify RBAC:

```bash
kubectl auth can-i get mcpservers --as=system:serviceaccount:kmcp-system:kmcp-controller-manager
```

### Controller Logs

```bash
# All logs
kubectl logs -l app.kubernetes.io/name=kmcp -n kmcp-system

# Follow logs
kubectl logs -l app.kubernetes.io/name=kmcp -n kmcp-system -f

# Previous container logs (if restarted)
kubectl logs -l app.kubernetes.io/name=kmcp -n kmcp-system --previous
```

## Upgrading

### Upgrade Controller

```bash
kmcp install --version v2.0.0
```

### Upgrade via Helm

```bash
helm upgrade kmcp oci://ghcr.io/kagent-dev/kmcp/helm/kmcp \
  --namespace kmcp-system \
  --reuse-values
```

### Upgrade CRDs

CRDs may need separate upgrade:

```bash
helm upgrade kmcp-crds oci://ghcr.io/kagent-dev/kmcp/helm/kmcp-crds \
  --namespace kmcp-system
```

## Uninstalling

### Remove Controller

```bash
kubectl delete namespace kmcp-system
```

### Remove CRDs

**Warning:** This deletes all MCPServer resources.

```bash
helm uninstall kmcp-crds -n kmcp-system
kubectl delete crd mcpservers.kagent.dev
```

## Next Steps

- [Deploying Servers](./08-deploying-servers.md) - Deploy MCP servers
- [Package Deployment](./09-package-deployment.md) - Deploy using npx/uvx
- [Secrets Management](./11-secrets-management.md) - Configure secrets
