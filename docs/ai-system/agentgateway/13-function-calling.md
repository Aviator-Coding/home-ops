# Function calling

Not wired through AgentGateway in this cluster.

The old guide described LLM tool invocation via AgentGateway MCP Backends (`kind: Backend`, `type: MCP`). Those CRs do not exist here. MCP is ToolHive (`05-mcp-connectivity.md`).

OpenAI-style `tools` / `tool_calls` in Chat Completions still pass through to whatever backend the unified `/v1` router selected - that is provider function calling, not gateway-federated MCP. Configure tools on the client; do not look for an AgentGateway MCP federation layer.
