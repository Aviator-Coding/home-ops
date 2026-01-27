# Function Calling

> **End-to-end guide for LLM function calling through AgentGateway to MCP tools and external services.**

## Overview

Function calling enables LLMs to invoke external tools through AgentGateway. The gateway acts as an intelligent router that:

1. Receives function call requests from LLMs
2. Routes to appropriate MCP servers or backends
3. Executes tools securely with RBAC enforcement
4. Returns structured results to the LLM

```
┌─────────────┐     ┌─────────────────┐     ┌─────────────────┐     ┌─────────────┐
│    LLM      │────▶│  AgentGateway   │────▶│   MCP Server    │────▶│   Backend   │
│  (Client)   │◀────│  (Router/Auth)  │◀────│  (Tool Host)    │◀────│  (Resource) │
└─────────────┘     └─────────────────┘     └─────────────────┘     └─────────────┘
```

---

## Request/Response Flow

### 1. Tool Discovery

Before invoking tools, clients discover available tools via the MCP `tools/list` method:

```bash
# SSE connection for tool discovery
curl -N "http://ai.sklab.dev/mcp/k8s-tools" \
  -H "Accept: text/event-stream" \
  -H "Authorization: Bearer $JWT_TOKEN"
```

Response (SSE):
```
event: message
data: {"jsonrpc":"2.0","id":1,"result":{"tools":[
  {"name":"kubectl-get","description":"Get Kubernetes resources","inputSchema":{...}},
  {"name":"kubectl-describe","description":"Describe Kubernetes resources","inputSchema":{...}},
  {"name":"list-pods","description":"List pods in namespace","inputSchema":{...}}
]}}
```

### 2. Tool Invocation

LLM sends a function call through the gateway:

```bash
curl -X POST "http://ai.sklab.dev/mcp/k8s-tools" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "kubectl-get",
      "arguments": {
        "resource": "pods",
        "namespace": "ai-system"
      }
    }
  }'
```

### 3. Gateway Processing

AgentGateway performs these steps:

1. **Authentication**: Validates JWT/API key
2. **Authorization**: Evaluates CEL RBAC rules against tool name
3. **Routing**: Selects appropriate MCP backend
4. **Transformation**: Applies any request transformations
5. **Execution**: Forwards to MCP server
6. **Response**: Returns result with observability metadata

### 4. Response Format

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      {
        "type": "text",
        "text": "NAME                                READY   STATUS    RESTARTS   AGE\nagentgateway-proxy-5f8b9c6d4-x7j2k   1/1     Running   0          2d"
      }
    ],
    "isError": false
  }
}
```

---

## Configuration

### Backend for MCP Function Calling

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: Backend
metadata:
  name: mcp-k8s-tools
  namespace: ai-system
spec:
  type: MCP
  mcp:
    targets:
      - name: kubernetes-tools
        static:
          host: kubernetes-mcp-server.ai-system.svc.cluster.local
          port: 80
          protocol: StreamableHTTP
```

### HTTPRoute for Tool Access

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: mcp-function-routes
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
  rules:
    # Route to Kubernetes MCP tools
    - matches:
        - path:
            type: PathPrefix
            value: /mcp/k8s-tools
      backendRefs:
        - group: gateway.kgateway.dev
          kind: Backend
          name: mcp-k8s-tools
    # Route to GitHub MCP tools
    - matches:
        - path:
            type: PathPrefix
            value: /mcp/github
      backendRefs:
        - group: gateway.kgateway.dev
          kind: Backend
          name: mcp-github-tools
```

### RBAC for Function Calls

Restrict which tools users can invoke:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: function-call-rbac
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: mcp-function-routes
  rbac:
    policy:
      matchExpressions:
        # Admin: all tools
        - 'jwt.role == "admin"'
        # Developer: read-only tools only
        - 'jwt.role == "developer" && mcp.tool.name in ["kubectl-get", "kubectl-describe", "list-pods", "list-services"]'
        # Viewer: list operations only
        - 'jwt.role == "viewer" && mcp.tool.name.startsWith("list-")'
```

---

## Code Examples

### Python SDK

```python
import httpx
import json

class AgentGatewayClient:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url
        self.token = token
        self.request_id = 0

    def _next_id(self) -> int:
        self.request_id += 1
        return self.request_id

    async def list_tools(self, mcp_path: str) -> list[dict]:
        """Discover available tools from an MCP server."""
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{self.base_url}{mcp_path}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "tools/list",
                    "params": {}
                }
            )
            result = response.json()
            return result.get("result", {}).get("tools", [])

    async def call_tool(
        self,
        mcp_path: str,
        tool_name: str,
        arguments: dict
    ) -> dict:
        """Invoke a tool on an MCP server."""
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}{mcp_path}",
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                },
                json={
                    "jsonrpc": "2.0",
                    "id": self._next_id(),
                    "method": "tools/call",
                    "params": {
                        "name": tool_name,
                        "arguments": arguments
                    }
                }
            )
            return response.json()

# Usage
async def main():
    client = AgentGatewayClient(
        base_url="http://ai.sklab.dev",
        token="eyJhbGciOiJSUzI1NiIs..."
    )

    # Discover tools
    tools = await client.list_tools("/mcp/k8s-tools")
    print(f"Available tools: {[t['name'] for t in tools]}")

    # Call a tool
    result = await client.call_tool(
        mcp_path="/mcp/k8s-tools",
        tool_name="kubectl-get",
        arguments={
            "resource": "pods",
            "namespace": "ai-system"
        }
    )

    if "error" in result:
        print(f"Error: {result['error']}")
    else:
        print(f"Result: {result['result']}")

import asyncio
asyncio.run(main())
```

### TypeScript/Node.js

```typescript
import axios, { AxiosInstance } from 'axios';

interface Tool {
  name: string;
  description: string;
  inputSchema: object;
}

interface ToolCallResult {
  content: Array<{ type: string; text: string }>;
  isError: boolean;
}

class AgentGatewayClient {
  private client: AxiosInstance;
  private requestId = 0;

  constructor(baseUrl: string, token: string) {
    this.client = axios.create({
      baseURL: baseUrl,
      headers: {
        'Authorization': `Bearer ${token}`,
        'Content-Type': 'application/json',
      },
      timeout: 60000,
    });
  }

  private nextId(): number {
    return ++this.requestId;
  }

  async listTools(mcpPath: string): Promise<Tool[]> {
    const response = await this.client.post(mcpPath, {
      jsonrpc: '2.0',
      id: this.nextId(),
      method: 'tools/list',
      params: {},
    });
    return response.data.result?.tools || [];
  }

  async callTool(
    mcpPath: string,
    toolName: string,
    args: Record<string, unknown>
  ): Promise<ToolCallResult> {
    const response = await this.client.post(mcpPath, {
      jsonrpc: '2.0',
      id: this.nextId(),
      method: 'tools/call',
      params: {
        name: toolName,
        arguments: args,
      },
    });

    if (response.data.error) {
      throw new Error(response.data.error.message);
    }

    return response.data.result;
  }
}

// Usage
const client = new AgentGatewayClient(
  'http://ai.sklab.dev',
  process.env.JWT_TOKEN!
);

const tools = await client.listTools('/mcp/k8s-tools');
console.log('Available tools:', tools.map(t => t.name));

const result = await client.callTool('/mcp/k8s-tools', 'kubectl-get', {
  resource: 'pods',
  namespace: 'ai-system',
});
console.log('Result:', result);
```

### Go

```go
package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"sync/atomic"
	"time"
)

type AgentGatewayClient struct {
	baseURL   string
	token     string
	client    *http.Client
	requestID int64
}

type JSONRPCRequest struct {
	JSONRPC string      `json:"jsonrpc"`
	ID      int64       `json:"id"`
	Method  string      `json:"method"`
	Params  interface{} `json:"params"`
}

type JSONRPCResponse struct {
	JSONRPC string          `json:"jsonrpc"`
	ID      int64           `json:"id"`
	Result  json.RawMessage `json:"result,omitempty"`
	Error   *RPCError       `json:"error,omitempty"`
}

type RPCError struct {
	Code    int    `json:"code"`
	Message string `json:"message"`
}

type ToolCallParams struct {
	Name      string                 `json:"name"`
	Arguments map[string]interface{} `json:"arguments"`
}

func NewClient(baseURL, token string) *AgentGatewayClient {
	return &AgentGatewayClient{
		baseURL: baseURL,
		token:   token,
		client: &http.Client{
			Timeout: 60 * time.Second,
		},
	}
}

func (c *AgentGatewayClient) nextID() int64 {
	return atomic.AddInt64(&c.requestID, 1)
}

func (c *AgentGatewayClient) CallTool(mcpPath, toolName string, args map[string]interface{}) (json.RawMessage, error) {
	reqBody := JSONRPCRequest{
		JSONRPC: "2.0",
		ID:      c.nextID(),
		Method:  "tools/call",
		Params: ToolCallParams{
			Name:      toolName,
			Arguments: args,
		},
	}

	body, err := json.Marshal(reqBody)
	if err != nil {
		return nil, err
	}

	req, err := http.NewRequest("POST", c.baseURL+mcpPath, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}

	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Content-Type", "application/json")

	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}

	var rpcResp JSONRPCResponse
	if err := json.Unmarshal(respBody, &rpcResp); err != nil {
		return nil, err
	}

	if rpcResp.Error != nil {
		return nil, fmt.Errorf("RPC error %d: %s", rpcResp.Error.Code, rpcResp.Error.Message)
	}

	return rpcResp.Result, nil
}

func main() {
	client := NewClient("http://ai.sklab.dev", "your-jwt-token")

	result, err := client.CallTool("/mcp/k8s-tools", "kubectl-get", map[string]interface{}{
		"resource":  "pods",
		"namespace": "ai-system",
	})
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		return
	}

	fmt.Printf("Result: %s\n", result)
}
```

### curl Examples

```bash
# List available tools
curl -X POST "http://ai.sklab.dev/mcp/k8s-tools" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'

# Call kubectl-get tool
curl -X POST "http://ai.sklab.dev/mcp/k8s-tools" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{
    "jsonrpc": "2.0",
    "id": 2,
    "method": "tools/call",
    "params": {
      "name": "kubectl-get",
      "arguments": {
        "resource": "deployments",
        "namespace": "ai-system",
        "output": "wide"
      }
    }
  }'

# Call with streaming response (SSE)
curl -N "http://ai.sklab.dev/mcp/k8s-tools" \
  -H "Accept: text/event-stream" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"kubectl-logs","arguments":{"pod":"agentgateway-proxy-5f8b9c6d4-x7j2k","namespace":"ai-system","follow":true}}}'
```

---

## Error Handling

### Common Error Codes

| Code | Meaning | Resolution |
|------|---------|------------|
| `-32600` | Invalid Request | Check JSON-RPC format |
| `-32601` | Method not found | Tool doesn't exist |
| `-32602` | Invalid params | Check argument schema |
| `-32603` | Internal error | Check MCP server logs |
| `401` | Unauthorized | Invalid/missing token |
| `403` | Forbidden | RBAC denies tool access |
| `429` | Rate limited | Reduce request frequency |
| `504` | Gateway timeout | Increase timeout or check backend |

### Error Response Format

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "error": {
    "code": -32602,
    "message": "Invalid params: missing required field 'namespace'",
    "data": {
      "field": "namespace",
      "expected": "string"
    }
  }
}
```

### Retry Strategy

```python
import asyncio
from httpx import HTTPStatusError

async def call_tool_with_retry(
    client: AgentGatewayClient,
    mcp_path: str,
    tool_name: str,
    arguments: dict,
    max_retries: int = 3,
    backoff_base: float = 1.0
) -> dict:
    """Call tool with exponential backoff retry."""
    last_error = None

    for attempt in range(max_retries):
        try:
            result = await client.call_tool(mcp_path, tool_name, arguments)

            # Check for retryable JSON-RPC errors
            if "error" in result:
                error_code = result["error"].get("code", 0)
                if error_code in [-32603]:  # Internal error - retryable
                    raise Exception(f"Retryable error: {result['error']}")

            return result

        except HTTPStatusError as e:
            if e.response.status_code in [429, 502, 503, 504]:
                last_error = e
                wait_time = backoff_base * (2 ** attempt)
                await asyncio.sleep(wait_time)
                continue
            raise
        except Exception as e:
            last_error = e
            wait_time = backoff_base * (2 ** attempt)
            await asyncio.sleep(wait_time)

    raise last_error
```

---

## Observability

### Tracing Function Calls

Each function call is traced with OpenTelemetry:

```
Trace: function-call
├── Span: gateway.receive (5ms)
├── Span: gateway.authenticate (10ms)
├── Span: gateway.authorize (3ms)
│   └── Attribute: mcp.tool.name = "kubectl-get"
│   └── Attribute: rbac.result = "allowed"
├── Span: gateway.route (2ms)
├── Span: mcp.tool.execute (150ms)
│   └── Attribute: mcp.server = "kubernetes-mcp-server"
│   └── Attribute: mcp.tool.arguments = {"resource":"pods"}
└── Span: gateway.respond (3ms)
```

### Metrics

Key metrics for function calling:

```promql
# Tool call success rate
sum(rate(agentgateway_mcp_tool_calls_total{status="success"}[5m]))
/ sum(rate(agentgateway_mcp_tool_calls_total[5m]))

# Tool call latency (p99)
histogram_quantile(0.99,
  sum(rate(agentgateway_mcp_tool_duration_seconds_bucket[5m])) by (le, tool)
)

# Most used tools
topk(10, sum(rate(agentgateway_mcp_tool_calls_total[1h])) by (tool))

# RBAC denials
sum(rate(agentgateway_rbac_denials_total{type="mcp"}[5m])) by (tool, role)
```

### Logging

Enable debug logging for function calls:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: agentgateway-params
  namespace: ai-system
spec:
  rawConfig:
    config:
      logging:
        level: debug
        components:
          mcp: debug
          rbac: info
```

---

## Best Practices

### 1. Validate Tool Inputs

Always validate arguments before calling tools:

```python
def validate_kubectl_get_args(args: dict) -> bool:
    """Validate kubectl-get arguments."""
    required = ["resource"]
    for field in required:
        if field not in args:
            raise ValueError(f"Missing required field: {field}")

    # Prevent injection
    if ";" in str(args) or "|" in str(args):
        raise ValueError("Invalid characters in arguments")

    return True
```

### 2. Use Specific Tool Routes

Don't expose all tools on one route:

```yaml
# Good: Separate routes by function
rules:
  - matches:
      - path:
          value: /mcp/k8s-read
    backendRefs:
      - name: mcp-k8s-readonly
  - matches:
      - path:
          value: /mcp/k8s-write
    backendRefs:
      - name: mcp-k8s-write

# Bad: Single route for everything
rules:
  - matches:
      - path:
          value: /mcp
    backendRefs:
      - name: mcp-all-tools
```

### 3. Set Appropriate Timeouts

Different tools need different timeouts:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: tool-timeouts
spec:
  targetRefs:
    - kind: HTTPRoute
      name: mcp-routes
  # Fast tools (list, get)
  timeout: 30s
---
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: slow-tool-timeouts
spec:
  targetRefs:
    - kind: HTTPRoute
      name: mcp-heavy-routes
  # Slow tools (logs, exec)
  timeout: 300s
```

### 4. Implement Idempotency

For write operations, use idempotency keys:

```python
import hashlib
import json

def generate_idempotency_key(tool_name: str, arguments: dict) -> str:
    """Generate idempotency key for tool call."""
    payload = json.dumps({"tool": tool_name, "args": arguments}, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()[:16]

# Include in request
headers = {
    "X-Idempotency-Key": generate_idempotency_key("create-configmap", args)
}
```

---

## References

- [MCP Specification](https://modelcontextprotocol.io/specification)
- [JSON-RPC 2.0](https://www.jsonrpc.org/specification)
- [AgentGateway MCP Docs](https://kgateway.dev/docs/agentgateway/latest/mcp/)
- [Tool Security](./07-security.md#tool-poisoning-protection)

---

*See [14-session-management.md](./14-session-management.md) for managing persistent connections.*
