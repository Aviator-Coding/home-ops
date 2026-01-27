# Glossary

> **Terminology reference for AgentGateway concepts, protocols, and resource types.**

---

## A

### A2A (Agent-to-Agent)
Protocol enabling direct communication between autonomous AI agents. Uses JSON-RPC 2.0 over HTTP/SSE for agent discovery, capability exchange, and task delegation.

### AgentGateway
The Rust-based AI-first data plane component of kgateway that handles routing for AI workloads including LLMs, MCP tools, and agent communication.

### API Key
Authentication credential used to access LLM providers. Stored in Kubernetes Secrets and referenced by Backend resources.

### Auth Token
Generic term for authentication credentials (API keys, JWT tokens) used in Backend configurations. Can be `SecretRef` (gateway-managed) or `Passthrough` (client-provided).

---

## B

### Backend
Kubernetes custom resource defining an upstream service. Types include:
- **AI**: LLM provider endpoint
- **MCP**: Model Context Protocol server
- **Static**: Fixed host/port endpoint
- **A2A**: Agent-to-agent endpoint

### BackendConfigPolicy
Resource for configuring backend behavior like connection pooling, TLS, circuit breakers, and health checks.

### Bedrock
Amazon's managed AI service providing access to foundation models including Claude, Llama, and Titan. Requires AWS IAM authentication.

---

## C

### CEL (Common Expression Language)
Google-developed expression language used for RBAC policy evaluation. Enables fine-grained access control based on request attributes.

### Circuit Breaker
Fault tolerance pattern that prevents cascade failures by "tripping" when error thresholds are exceeded, temporarily rejecting requests.

### Control Plane
The kgateway controller component that manages proxy lifecycle, translates Gateway API resources to xDS configuration, and handles CRD reconciliation.

### CRD (Custom Resource Definition)
Kubernetes extension mechanism used by AgentGateway to define resources like Backend, TrafficPolicy, and GatewayParameters.

---

## D

### Data Plane
The AgentGateway proxy component that processes actual AI traffic. Written in Rust, handles request routing, protocol translation, and policy enforcement.

### DirectResponse
Resource for returning static responses without forwarding to backends. Used for maintenance pages, health checks, or error responses.

---

## E

### External Secrets
Kubernetes operator that syncs secrets from external providers (1Password, AWS Secrets Manager, HashiCorp Vault) into Kubernetes Secrets.

### Envoy
High-performance proxy that kgateway's traditional data plane is based on. AgentGateway shares architectural patterns but is a separate Rust implementation.

---

## F

### Failover
Automatic routing to backup providers when primary providers fail. Configured via `priorityGroups` in Backend resources.

### Function Calling
LLM capability to invoke external tools. AgentGateway routes function calls to appropriate MCP servers or backends.

---

## G

### Gateway
Core Kubernetes Gateway API resource defining entry points for traffic. AgentGateway creates Gateway resources with the `agentgateway` GatewayClass.

### GatewayClass
Kubernetes resource defining a class of Gateways. `agentgateway` class indicates the gateway should be handled by AgentGateway.

### GatewayParameters
kgateway-specific resource for configuring gateway deployment details like replicas, resources, service type, and raw proxy configuration.

### Gemini
Google's family of multimodal AI models. Accessed via Google AI API or Vertex AI.

---

## H

### Health Check
Periodic probes to determine backend availability. Unhealthy backends are removed from load balancing rotation.

### Helm
Kubernetes package manager used to install kgateway and AgentGateway.

### HTTPRoute
Gateway API resource defining HTTP routing rules. Matches requests by path, headers, or methods and routes to backends.

---

## I

### Inference
The process of generating predictions/outputs from an AI model. "Inference routing" refers to routing requests to model serving endpoints.

### InferencePool
Gateway API Inference Extension resource grouping pods serving the same model for load balancing.

### InferenceModel
Gateway API Inference Extension resource defining a model available for inference, including criticality and pool reference.

---

## J

### JSON-RPC
Remote procedure call protocol using JSON. Both MCP and A2A protocols use JSON-RPC 2.0 for message formatting.

### JWT (JSON Web Token)
Standard for securely transmitting information as a JSON object. Used for authentication and carrying claims for RBAC.

### JWKS (JSON Web Key Set)
Standard format for publishing public keys used to verify JWT signatures.

---

## K

### kgateway
CNCF Sandbox project providing Kubernetes-native gateway functionality. Includes both traditional Envoy-based gateway and AgentGateway for AI workloads.

### Kubernetes Gateway API
Standard Kubernetes API for managing ingress traffic. AgentGateway implements Gateway API with AI-specific extensions.

---

## L

### Last-Event-ID
HTTP header used in SSE to enable reconnection. Client sends the ID of the last received event to resume streaming.

### Load Balancing
Distribution of requests across multiple backends. AgentGateway supports round-robin, least-request, and priority-based load balancing.

### LLM (Large Language Model)
AI model trained on large text corpora capable of understanding and generating natural language. Examples: GPT-4, Claude, Gemini.

---

## M

### MCP (Model Context Protocol)
Open protocol for connecting AI models to external data sources and tools. Defines how models discover and invoke tools.

### MCP Server
Service implementing MCP protocol, exposing tools for AI models to use. Example: kubernetes-mcp-server exposes kubectl operations as tools.

### Mutual TLS (mTLS)
TLS authentication where both client and server present certificates. Used for secure service-to-service communication.

---

## N

### Namespace
Kubernetes resource for organizing and isolating resources. AgentGateway typically deploys to `ai-system` namespace.

---

## O

### OAuth 2.0
Authorization framework for token-based access. MCP protocol (as of March 2026 spec) uses OAuth for authentication.

### Ollama
Local LLM serving solution providing OpenAI-compatible API. Enables self-hosted model inference.

### OpenAI
AI company and API provider. OpenAI API format is the de facto standard for LLM APIs.

### OpenTelemetry (OTEL)
Observability framework for metrics, logs, and traces. AgentGateway exports telemetry in OpenTelemetry format.

### Outlier Detection
Health checking mechanism that ejects backends showing error patterns (consecutive 5xx, high latency).

---

## P

### Passthrough
Auth mode where client provides credentials directly, gateway forwards without modification.

### PII (Personally Identifiable Information)
Data that can identify an individual. Prompt guards can mask PII in LLM responses.

### Priority Group
Set of providers at the same priority level. Load balanced within group; failover between groups.

### Prompt Guard
Security feature that filters request/response content using regex or built-in patterns.

### Prompt Injection
Attack attempting to manipulate LLM behavior by including malicious instructions in input.

---

## R

### RBAC (Role-Based Access Control)
Authorization model where permissions are assigned to roles. AgentGateway uses CEL-based RBAC policies.

### Rate Limiting
Controlling request frequency. Can be request-based (RPS) or token-based (tokens/hour).

### Retry
Automatic re-attempt of failed requests. Configured with backoff, max attempts, and retriable conditions.

---

## S

### SecretRef
Reference to a Kubernetes Secret for credential storage. Preferred over inline credentials.

### Session
Stateful connection maintaining context across requests. Supports reconnection and state persistence.

### SSE (Server-Sent Events)
HTTP-based protocol for server-to-client streaming. Used by MCP for tool notifications and streaming responses.

### Streamable HTTP
MCP protocol upgrade from SSE. Provides bidirectional streaming over HTTP.

### Streaming
Incremental response delivery. LLMs stream tokens as generated rather than waiting for complete response.

---

## T

### TLS (Transport Layer Security)
Cryptographic protocol for secure communication. Automatic for external LLM providers.

### Token (LLM)
Unit of text processed by LLMs. Roughly 4 characters or 0.75 words for English text.

### Token Bucket
Rate limiting algorithm. Bucket fills with tokens over time; requests consume tokens.

### Tool
MCP concept for an operation an AI can invoke. Defined with name, description, and input schema.

### Tool Poisoning
Attack where malicious tool definitions or responses manipulate AI behavior.

### TrafficPolicy
kgateway resource for configuring traffic behavior including rate limits, RBAC, caching, and AI-specific features.

---

## V

### Vertex AI
Google Cloud's ML platform providing access to Gemini models with enterprise features.

### vLLM
High-throughput LLM serving engine using PagedAttention for efficient memory management.

---

## X

### xDS
Protocol for dynamic configuration in Envoy-style proxies. Control plane translates Gateway API to xDS.

---

## Protocol Reference

### Backend Types

| Type | Description | Use Case |
|------|-------------|----------|
| `AI` | LLM provider | Cloud AI APIs |
| `MCP` | Tool server | External tools |
| `Static` | Fixed endpoint | Internal services |
| `A2A` | Agent endpoint | Agent communication |

### MCP Protocols

| Protocol | Transport | Direction | Status |
|----------|-----------|-----------|--------|
| `SSE` | HTTP + SSE | Server→Client | Deprecated |
| `StreamableHTTP` | HTTP bidirectional | Both | Current |
| `Stdio` | Standard I/O | Local | Development |

### Auth Modes

| Mode | Description | Credential Location |
|------|-------------|---------------------|
| `SecretRef` | Gateway manages | Kubernetes Secret |
| `Passthrough` | Client provides | Request header |
| `Inline` | In resource | Backend spec (not recommended) |

---

## Resource Quick Reference

| Resource | API Group | Description |
|----------|-----------|-------------|
| `Gateway` | `gateway.networking.k8s.io` | Entry point definition |
| `HTTPRoute` | `gateway.networking.k8s.io` | Routing rules |
| `GatewayClass` | `gateway.networking.k8s.io` | Gateway implementation class |
| `Backend` | `gateway.kgateway.dev` | Upstream service |
| `TrafficPolicy` | `gateway.kgateway.dev` | Traffic configuration |
| `GatewayParameters` | `gateway.kgateway.dev` | Gateway deployment config |
| `BackendConfigPolicy` | `gateway.kgateway.dev` | Backend behavior config |
| `DirectResponse` | `gateway.kgateway.dev` | Static response |
| `ExternalSecret` | `external-secrets.io` | Secret synchronization |
| `InferencePool` | `inference.ai.networking.k8s.io` | Model serving pool |
| `InferenceModel` | `inference.ai.networking.k8s.io` | Model definition |

---

*See [10-api-reference.md](./10-api-reference.md) for complete CRD specifications.*
