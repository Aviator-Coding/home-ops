# Pre-built Agents

Catalog of ready-to-use agents included with kagent.

## Overview

Kagent provides pre-built agents for common DevOps and platform engineering tasks. These agents are available when installing with `--profile demo`.

---

## Agent Catalog

### k8s-agent

**Purpose:** General Kubernetes cluster operations and troubleshooting.

**Capabilities:**
- Query cluster resources
- Retrieve pod logs
- Describe resource details
- List and filter resources

**Tools:**
- `k8s_get_resources`
- `k8s_get_pod_logs`
- `k8s_describe_resource`
- `k8s_get_available_api_resources`

**Example Queries:**
```
"List all pods in the default namespace"
"Show me the logs from the api-server pod"
"What deployments are failing?"
"Describe the nginx service"
```

---

### helm-agent

**Purpose:** Helm chart and release management.

**Capabilities:**
- List Helm releases
- Get release values and history
- Troubleshoot failed releases
- Compare release versions

**Tools:**
- `helm_list_releases`
- `helm_get_values`
- `helm_get_history`
- `helm_rollback`
- `helm_get_manifest`

**Example Queries:**
```
"What Helm charts are installed in my cluster?"
"Show me the values for the prometheus release"
"What changed between version 1 and 2 of nginx?"
"Why did the last helm upgrade fail?"
```

---

### istio-agent

**Purpose:** Istio service mesh operations and troubleshooting.

**Capabilities:**
- Configure traffic management
- Debug service connectivity
- Analyze mesh configuration
- Troubleshoot routing issues

**Tools:**
- `istio_get_virtual_services`
- `istio_get_destination_rules`
- `istio_get_gateways`
- `istio_get_service_entries`
- `istio_analyze`

**Example Queries:**
```
"Show me all virtual services in the mesh"
"Why isn't traffic routing to my canary deployment?"
"What destination rules apply to the api service?"
"Analyze my Istio configuration for issues"
```

---

### observability-agent

**Purpose:** Monitoring and observability using Prometheus, Grafana, and Kubernetes.

**Capabilities:**
- Query Prometheus metrics
- Create and explain PromQL queries
- Analyze alerts
- Manage Grafana dashboards

**Tools:**
- `prometheus_query`
- `prometheus_query_range`
- `prometheus_alerts`
- `grafana_list_dashboards`
- `grafana_get_dashboard`
- `k8s_get_resources`

**Example Queries:**
```
"What's the CPU usage across all nodes?"
"Write a PromQL query for request latency p99"
"What alerts are currently firing?"
"Show me the Grafana dashboards available"
```

---

### promql-agent

**Purpose:** Generate PromQL queries from natural language descriptions.

**Capabilities:**
- Convert natural language to PromQL
- Explain existing queries
- Optimize query performance
- Suggest relevant metrics

**Tools:**
- `prometheus_query`
- `prometheus_metrics_list`

**Example Queries:**
```
"Write a query to show memory usage per pod"
"Create a query for HTTP error rate"
"What does this PromQL query do: rate(http_requests_total[5m])"
"Find all metrics related to network"
```

---

### cilium-crd-agent

**Purpose:** Create Cilium network policies from natural language.

**Capabilities:**
- Generate CiliumNetworkPolicy resources
- Create cluster-wide network policies
- Explain policy effects
- Validate policy syntax

**Tools:**
- `cilium_create_network_policy`
- `cilium_get_policies`
- `cilium_validate_policy`

**Example Queries:**
```
"Create a policy to allow only HTTPS traffic to the api service"
"Block all egress traffic from the frontend namespace"
"Show me all network policies affecting the payment service"
"Explain what this Cilium policy does"
```

---

### argo-rollouts-conversion-agent

**Purpose:** Convert Kubernetes Deployments to Argo Rollouts.

**Capabilities:**
- Analyze existing Deployments
- Generate Rollout resources
- Configure canary strategies
- Set up blue-green deployments

**Tools:**
- `k8s_get_resources`
- `argo_create_rollout`
- `argo_get_rollouts`

**Example Queries:**
```
"Convert my nginx deployment to an Argo Rollout"
"Set up a canary deployment with 10% traffic split"
"Add blue-green deployment to the api service"
"What's the status of my current rollout?"
```

---

### kgateway-agent

**Purpose:** kgateway (cloud-native API gateway) configuration and troubleshooting.

**Capabilities:**
- Configure Gateway API resources
- Debug HTTPRoutes
- Analyze traffic routing
- Troubleshoot connectivity

**Tools:**
- `k8s_get_resources`
- `kgateway_get_routes`
- `kgateway_analyze`

**Example Queries:**
```
"Show me all HTTPRoutes in the cluster"
"Why isn't my route matching requests?"
"Create an HTTPRoute for the api service"
"What gateways are configured?"
```

---

## Installation

### Demo Profile (Includes All Agents)

```bash
kagent install --profile demo
```

### Minimal Profile (Base Only)

```bash
kagent install --profile minimal
```

Then manually deploy specific agents.

---

## Using Pre-built Agents

### Via Dashboard

1. Open `kagent dashboard`
2. Select agent from the agent list
3. Start chatting

### Via CLI

```bash
# List available agents
kagent get agent

# Invoke specific agent
kagent invoke -a k8s-agent -t "List all pods"
kagent invoke -a helm-agent -t "Show installed charts"
kagent invoke -a observability-agent -t "What alerts are firing?"
```

---

## Customizing Pre-built Agents

### Export and Modify

```bash
# Get agent configuration
kubectl get agent k8s-agent -n kagent -o yaml > my-k8s-agent.yaml

# Edit the configuration
# - Change name
# - Modify systemMessage
# - Add/remove tools

# Apply modified agent
kubectl apply -f my-k8s-agent.yaml
```

### Example: Extended k8s-agent

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: k8s-agent-extended
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're an extended Kubernetes agent with additional capabilities.

      In addition to standard k8s queries, you can:
      - Analyze Helm releases
      - Query Prometheus metrics
      - Check Istio configuration

      Always provide context and explanations with your responses.
    tools:
      # Kubernetes tools
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
            - k8s_describe_resource
      # Helm tools
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - helm_list_releases
            - helm_get_values
      # Prometheus tools
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - prometheus_query
            - prometheus_alerts
```

---

## Agent Tool Reference

### Kubernetes Tools (21)

| Tool | Description |
|------|-------------|
| `k8s_get_available_api_resources` | List available API resources |
| `k8s_get_resources` | List resources by type |
| `k8s_describe_resource` | Get detailed resource info |
| `k8s_get_pod_logs` | Retrieve pod logs |
| `k8s_apply_resource` | Apply YAML manifest |
| `k8s_delete_resource` | Delete a resource |
| `k8s_patch_resource` | Patch a resource |
| `k8s_get_events` | Get cluster events |
| ... | (additional tools) |

### Prometheus Tools (21)

| Tool | Description |
|------|-------------|
| `prometheus_query` | Execute instant query |
| `prometheus_query_range` | Query over time range |
| `prometheus_alerts` | Get active alerts |
| `prometheus_rules` | List alerting rules |
| `prometheus_targets` | List scrape targets |
| `prometheus_metrics_list` | List available metrics |
| ... | (additional tools) |

### Helm Tools (6)

| Tool | Description |
|------|-------------|
| `helm_list_releases` | List all releases |
| `helm_get_values` | Get release values |
| `helm_get_history` | Get release history |
| `helm_rollback` | Rollback release |
| `helm_get_manifest` | Get release manifest |
| `helm_get_notes` | Get release notes |

### Istio Tools (13)

| Tool | Description |
|------|-------------|
| `istio_get_virtual_services` | List virtual services |
| `istio_get_destination_rules` | List destination rules |
| `istio_get_gateways` | List gateways |
| `istio_get_service_entries` | List service entries |
| `istio_analyze` | Analyze configuration |
| ... | (additional tools) |

### Grafana Tools (9)

| Tool | Description |
|------|-------------|
| `grafana_list_dashboards` | List dashboards |
| `grafana_get_dashboard` | Get dashboard details |
| `grafana_list_datasources` | List data sources |
| `grafana_create_dashboard` | Create dashboard |
| ... | (additional tools) |

### Cilium Tools (58)

| Tool | Description |
|------|-------------|
| `cilium_get_policies` | List network policies |
| `cilium_create_network_policy` | Create policy |
| `cilium_get_endpoints` | List Cilium endpoints |
| `cilium_get_identity` | Get identity info |
| ... | (additional tools) |

### Argo Tools (7)

| Tool | Description |
|------|-------------|
| `argo_get_rollouts` | List rollouts |
| `argo_get_rollout_status` | Get rollout status |
| `argo_promote_rollout` | Promote rollout |
| `argo_abort_rollout` | Abort rollout |
| ... | (additional tools) |

---

## Contributing Agents

Share your agents with the community:

1. Fork the kagent repository
2. Add your agent to `agents/community/`
3. Submit a pull request

**Agent Requirements:**
- Clear documentation
- Example queries
- Tested tool integrations
- Security review passed

---

## Next Steps

- [Examples](./10-examples.md) - Practical implementation examples
- [FAQ](./11-faq.md) - Frequently asked questions
- [CLI Reference](./08-cli-reference.md) - Command-line usage
