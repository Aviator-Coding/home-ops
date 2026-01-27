# Session Management

> **Managing stateful connections, session lifecycle, reconnection handling, and multi-session patterns for AgentGateway.**

## Overview

AgentGateway supports stateful sessions for persistent agent connections. Sessions enable:

- **State Preservation**: Maintain context across multiple requests
- **Efficient Streaming**: Long-lived SSE connections for real-time updates
- **Reconnection**: Resume sessions after network interruptions
- **Multi-Agent**: Coordinate multiple agents sharing context

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Session Lifecycle                                   │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                             │
│   ┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐          │
│   │  INIT    │────▶│  ACTIVE  │────▶│ SUSPENDED│────▶│  CLOSED  │          │
│   └──────────┘     └──────────┘     └──────────┘     └──────────┘          │
│        │                │ ▲               │                                 │
│        │                │ │               │                                 │
│        │                ▼ │               │                                 │
│        │           ┌──────────┐           │                                 │
│        └──────────▶│RECONNECT │◀──────────┘                                 │
│                    └──────────┘                                             │
│                                                                             │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## Session Architecture

### Token-Encoded Sessions

AgentGateway uses token-encoded session state for stateless horizontal scaling:

```
┌───────────────────────────────────────────────────────────────┐
│                     Session Token                              │
├───────────────────────────────────────────────────────────────┤
│  Header: { "alg": "HS256", "typ": "JWT" }                     │
│  Payload: {                                                    │
│    "sid": "sess_abc123",           // Session ID              │
│    "uid": "user_xyz",              // User identifier         │
│    "created": 1706307600,          // Creation timestamp      │
│    "last_event": "evt_789",        // Last-Event-ID for SSE   │
│    "state": { ... },               // Application state       │
│    "exp": 1706394000               // Expiration              │
│  }                                                             │
│  Signature: HMACSHA256(...)                                   │
└───────────────────────────────────────────────────────────────┘
```

### Session Storage Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **Token** | State encoded in JWT | Stateless, horizontal scaling |
| **Redis** | State stored in Redis | Large state, server-side control |
| **Hybrid** | Token + Redis | Balance of both approaches |

---

## Configuration

### Enable Session Support

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: agentgateway-params
  namespace: ai-system
spec:
  rawConfig:
    config:
      sessions:
        enabled: true
        mode: token  # token, redis, or hybrid
        tokenSecret:
          secretRef:
            name: session-signing-key
        timeout: 1h
        maxIdleTime: 15m
```

### Session Secret

```yaml
apiVersion: external-secrets.io/v1
kind: ExternalSecret
metadata:
  name: session-signing-key
  namespace: ai-system
spec:
  secretStoreRef:
    kind: ClusterSecretStore
    name: onepassword
  target:
    name: session-signing-key
    template:
      data:
        key: "{{ .SESSION_SIGNING_KEY }}"
  dataFrom:
    - extract:
        key: agentgateway-session-key
```

### Redis Session Store (Optional)

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: GatewayParameters
metadata:
  name: agentgateway-params
  namespace: ai-system
spec:
  rawConfig:
    config:
      sessions:
        enabled: true
        mode: redis
        redis:
          address: redis.ai-system.svc.cluster.local:6379
          password:
            secretRef:
              name: redis-password
          db: 0
          poolSize: 10
          keyPrefix: "agw:session:"
        timeout: 24h
        maxIdleTime: 1h
```

---

## Session Lifecycle

### 1. Session Initialization

Create a new session:

```bash
# Initialize session
curl -X POST "http://ai.sklab.dev/session/init" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $JWT_TOKEN" \
  -d '{
    "client_info": {
      "name": "my-agent",
      "version": "1.0.0"
    },
    "capabilities": {
      "streaming": true,
      "tools": ["mcp"]
    }
  }'
```

Response:
```json
{
  "session_id": "sess_abc123def456",
  "session_token": "eyJhbGciOiJIUzI1NiIs...",
  "expires_at": "2026-01-27T12:00:00Z",
  "endpoints": {
    "messages": "/session/sess_abc123def456/messages",
    "sse": "/session/sess_abc123def456/sse",
    "tools": "/session/sess_abc123def456/tools"
  }
}
```

### 2. Active Session Operations

Use the session token for subsequent requests:

```bash
# Send message within session
curl -X POST "http://ai.sklab.dev/session/sess_abc123def456/messages" \
  -H "Content-Type: application/json" \
  -H "X-Session-Token: eyJhbGciOiJIUzI1NiIs..." \
  -d '{
    "role": "user",
    "content": "List all pods in ai-system namespace"
  }'
```

### 3. SSE Connection with Session

Establish streaming connection:

```bash
# Connect to session SSE stream
curl -N "http://ai.sklab.dev/session/sess_abc123def456/sse" \
  -H "Accept: text/event-stream" \
  -H "X-Session-Token: eyJhbGciOiJIUzI1NiIs..."
```

### 4. Session Termination

Explicitly close a session:

```bash
curl -X DELETE "http://ai.sklab.dev/session/sess_abc123def456" \
  -H "X-Session-Token: eyJhbGciOiJIUzI1NiIs..."
```

---

## Reconnection Handling

### Last-Event-ID for SSE

AgentGateway supports automatic reconnection using SSE's `Last-Event-ID`:

```bash
# Initial connection
curl -N "http://ai.sklab.dev/session/sess_abc123/sse" \
  -H "Accept: text/event-stream" \
  -H "X-Session-Token: $SESSION_TOKEN"

# Events include IDs
# event: message
# id: evt_001
# data: {"type":"assistant","content":"Processing..."}

# event: message
# id: evt_002
# data: {"type":"tool_use","name":"kubectl-get"}

# On reconnect, resume from last event
curl -N "http://ai.sklab.dev/session/sess_abc123/sse" \
  -H "Accept: text/event-stream" \
  -H "X-Session-Token: $SESSION_TOKEN" \
  -H "Last-Event-ID: evt_002"
```

### Reconnection Policy

Configure reconnection behavior:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: session-reconnect
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: session-routes
  ai:
    session:
      reconnect:
        enabled: true
        maxReplayEvents: 100      # Max events to replay on reconnect
        replayWindow: 5m          # How far back to replay
        deduplicate: true         # Prevent duplicate events
```

### Client-Side Reconnection (JavaScript)

```javascript
class SessionClient {
  constructor(baseUrl, sessionToken) {
    this.baseUrl = baseUrl;
    this.sessionToken = sessionToken;
    this.lastEventId = null;
    this.reconnectAttempts = 0;
    this.maxReconnectAttempts = 5;
    this.reconnectDelay = 1000;
  }

  connect(sessionId) {
    const url = `${this.baseUrl}/session/${sessionId}/sse`;
    const headers = {
      'X-Session-Token': this.sessionToken,
    };

    if (this.lastEventId) {
      headers['Last-Event-ID'] = this.lastEventId;
    }

    this.eventSource = new EventSource(url, { headers });

    this.eventSource.onmessage = (event) => {
      this.lastEventId = event.lastEventId;
      this.reconnectAttempts = 0;
      this.onMessage(JSON.parse(event.data));
    };

    this.eventSource.onerror = (error) => {
      this.eventSource.close();
      this.scheduleReconnect(sessionId);
    };
  }

  scheduleReconnect(sessionId) {
    if (this.reconnectAttempts >= this.maxReconnectAttempts) {
      this.onError(new Error('Max reconnection attempts reached'));
      return;
    }

    const delay = this.reconnectDelay * Math.pow(2, this.reconnectAttempts);
    this.reconnectAttempts++;

    console.log(`Reconnecting in ${delay}ms (attempt ${this.reconnectAttempts})`);

    setTimeout(() => {
      this.connect(sessionId);
    }, delay);
  }

  onMessage(data) {
    console.log('Received:', data);
  }

  onError(error) {
    console.error('Session error:', error);
  }
}
```

### Client-Side Reconnection (Python)

```python
import asyncio
import aiohttp
import json
from typing import Optional, Callable, Any

class SessionClient:
    def __init__(
        self,
        base_url: str,
        session_token: str,
        on_message: Callable[[dict], Any],
        max_retries: int = 5,
        base_delay: float = 1.0
    ):
        self.base_url = base_url
        self.session_token = session_token
        self.on_message = on_message
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.last_event_id: Optional[str] = None
        self._running = False

    async def connect(self, session_id: str):
        """Connect to session with automatic reconnection."""
        self._running = True
        retry_count = 0

        while self._running and retry_count < self.max_retries:
            try:
                await self._stream(session_id)
                retry_count = 0  # Reset on successful connection
            except aiohttp.ClientError as e:
                retry_count += 1
                delay = self.base_delay * (2 ** retry_count)
                print(f"Connection lost, reconnecting in {delay}s (attempt {retry_count})")
                await asyncio.sleep(delay)
            except Exception as e:
                print(f"Fatal error: {e}")
                break

    async def _stream(self, session_id: str):
        """Establish SSE stream."""
        url = f"{self.base_url}/session/{session_id}/sse"
        headers = {
            "X-Session-Token": self.session_token,
            "Accept": "text/event-stream",
        }

        if self.last_event_id:
            headers["Last-Event-ID"] = self.last_event_id

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as response:
                async for line in response.content:
                    line = line.decode('utf-8').strip()

                    if line.startswith('id:'):
                        self.last_event_id = line[3:].strip()
                    elif line.startswith('data:'):
                        data = json.loads(line[5:].strip())
                        await self.on_message(data)

    def disconnect(self):
        """Stop the connection."""
        self._running = False


# Usage
async def handle_message(data: dict):
    print(f"Received: {data}")

client = SessionClient(
    base_url="http://ai.sklab.dev",
    session_token="eyJhbGciOiJIUzI1NiIs...",
    on_message=handle_message
)

await client.connect("sess_abc123def456")
```

---

## Session Timeout Configuration

### Timeout Types

| Timeout | Description | Default |
|---------|-------------|---------|
| **Session Timeout** | Maximum session duration | 1 hour |
| **Idle Timeout** | Max time without activity | 15 minutes |
| **SSE Keepalive** | Heartbeat interval | 30 seconds |
| **Reconnect Window** | Time to allow reconnection | 5 minutes |

### TrafficPolicy Configuration

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: session-timeouts
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: session-routes
  ai:
    session:
      timeout: 2h              # Max session duration
      idleTimeout: 30m         # Idle timeout
      keepalive:
        interval: 30s          # SSE heartbeat
        timeout: 90s           # Heartbeat timeout
```

### Per-Route Timeout Override

```yaml
apiVersion: gateway.networking.k8s.io/v1
kind: HTTPRoute
metadata:
  name: long-session-route
  namespace: ai-system
spec:
  parentRefs:
    - name: agentgateway
  rules:
    - matches:
        - path:
            type: PathPrefix
            value: /session/long
      backendRefs:
        - kind: Backend
          name: long-session-backend
      timeouts:
        request: 4h
```

---

## Multi-Session Patterns

### Session Sharing (Agent Collaboration)

Multiple agents can share a session context:

```yaml
apiVersion: gateway.kgateway.dev/v1alpha1
kind: TrafficPolicy
metadata:
  name: shared-session
  namespace: ai-system
spec:
  targetRefs:
    - group: gateway.networking.k8s.io
      kind: HTTPRoute
      name: collaborative-route
  ai:
    session:
      sharing:
        enabled: true
        maxParticipants: 5
        isolation: shared    # shared, isolated, or broadcast
```

### Multi-Session Client Pattern

```python
class MultiSessionManager:
    """Manage multiple concurrent sessions."""

    def __init__(self, base_url: str, auth_token: str):
        self.base_url = base_url
        self.auth_token = auth_token
        self.sessions: dict[str, dict] = {}

    async def create_session(self, name: str, purpose: str) -> str:
        """Create a new named session."""
        async with aiohttp.ClientSession() as client:
            response = await client.post(
                f"{self.base_url}/session/init",
                headers={"Authorization": f"Bearer {self.auth_token}"},
                json={
                    "client_info": {"name": name},
                    "metadata": {"purpose": purpose}
                }
            )
            data = await response.json()

            self.sessions[name] = {
                "id": data["session_id"],
                "token": data["session_token"],
                "purpose": purpose
            }

            return data["session_id"]

    async def send_to_session(self, name: str, message: str) -> dict:
        """Send message to a specific session."""
        session = self.sessions[name]

        async with aiohttp.ClientSession() as client:
            response = await client.post(
                f"{self.base_url}/session/{session['id']}/messages",
                headers={"X-Session-Token": session["token"]},
                json={"role": "user", "content": message}
            )
            return await response.json()

    async def broadcast(self, message: str) -> dict[str, dict]:
        """Send message to all sessions."""
        results = {}
        tasks = [
            self.send_to_session(name, message)
            for name in self.sessions
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

        for name, response in zip(self.sessions.keys(), responses):
            results[name] = response

        return results


# Usage
manager = MultiSessionManager("http://ai.sklab.dev", jwt_token)

# Create specialized sessions
await manager.create_session("researcher", "information gathering")
await manager.create_session("coder", "implementation")
await manager.create_session("reviewer", "code review")

# Coordinate work
research = await manager.send_to_session("researcher", "Find best practices for X")
implementation = await manager.send_to_session("coder", f"Implement based on: {research}")
review = await manager.send_to_session("reviewer", f"Review: {implementation}")
```

---

## Session State Management

### Reading Session State

```bash
curl "http://ai.sklab.dev/session/sess_abc123/state" \
  -H "X-Session-Token: $SESSION_TOKEN"
```

Response:
```json
{
  "session_id": "sess_abc123",
  "created_at": "2026-01-26T10:00:00Z",
  "last_activity": "2026-01-26T10:15:30Z",
  "message_count": 42,
  "tool_calls": 8,
  "state": {
    "context": "kubernetes cluster management",
    "current_namespace": "ai-system",
    "history_summary": "User asked about pod status..."
  }
}
```

### Updating Session State

```bash
curl -X PATCH "http://ai.sklab.dev/session/sess_abc123/state" \
  -H "X-Session-Token: $SESSION_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "state": {
      "current_namespace": "monitoring",
      "custom_data": {"key": "value"}
    }
  }'
```

---

## Monitoring Sessions

### Session Metrics

```promql
# Active sessions
agentgateway_sessions_active

# Session creation rate
rate(agentgateway_sessions_created_total[5m])

# Session duration histogram
histogram_quantile(0.95,
  sum(rate(agentgateway_session_duration_seconds_bucket[1h])) by (le)
)

# Reconnection rate
rate(agentgateway_session_reconnects_total[5m])

# Session errors by type
sum(rate(agentgateway_session_errors_total[5m])) by (error_type)
```

### Session Alerts

```yaml
apiVersion: monitoring.coreos.com/v1
kind: PrometheusRule
metadata:
  name: session-alerts
  namespace: ai-system
spec:
  groups:
    - name: sessions
      rules:
        - alert: HighSessionCount
          expr: agentgateway_sessions_active > 1000
          for: 5m
          labels:
            severity: warning
          annotations:
            summary: "High number of active sessions"

        - alert: SessionReconnectSpike
          expr: rate(agentgateway_session_reconnects_total[5m]) > 10
          for: 2m
          labels:
            severity: warning
          annotations:
            summary: "Elevated session reconnection rate"

        - alert: SessionTimeouts
          expr: rate(agentgateway_session_timeouts_total[5m]) > 5
          for: 5m
          labels:
            severity: info
          annotations:
            summary: "Sessions timing out frequently"
```

---

## Best Practices

### 1. Always Handle Reconnection

```python
# Bad: No reconnection handling
async def simple_connect():
    response = await client.get(sse_url)
    # If connection drops, session is lost

# Good: Robust reconnection
async def robust_connect():
    client = SessionClient(
        base_url=url,
        session_token=token,
        on_message=handler,
        max_retries=5
    )
    await client.connect(session_id)
```

### 2. Persist Last-Event-ID

```python
# Store last event ID for recovery
import json
from pathlib import Path

def save_checkpoint(session_id: str, last_event_id: str):
    checkpoint = {"session_id": session_id, "last_event_id": last_event_id}
    Path(f"/tmp/session_{session_id}.json").write_text(json.dumps(checkpoint))

def load_checkpoint(session_id: str) -> Optional[str]:
    path = Path(f"/tmp/session_{session_id}.json")
    if path.exists():
        checkpoint = json.loads(path.read_text())
        return checkpoint.get("last_event_id")
    return None
```

### 3. Use Appropriate Session Timeouts

| Use Case | Session Timeout | Idle Timeout |
|----------|-----------------|--------------|
| Interactive chat | 1h | 15m |
| Long-running agent | 24h | 1h |
| Batch processing | 4h | 30m |
| Quick queries | 15m | 5m |

### 4. Clean Up Sessions

Always close sessions when done:

```python
async def with_session(base_url: str, token: str):
    """Context manager for session lifecycle."""
    session_id = None
    session_token = None

    try:
        # Initialize
        init_resp = await init_session(base_url, token)
        session_id = init_resp["session_id"]
        session_token = init_resp["session_token"]

        yield session_id, session_token

    finally:
        # Always clean up
        if session_id and session_token:
            await close_session(base_url, session_id, session_token)
```

---

## References

- [SSE Specification](https://html.spec.whatwg.org/multipage/server-sent-events.html)
- [MCP Session Protocol](https://modelcontextprotocol.io/specification/session)
- [AgentGateway Sessions](https://kgateway.dev/docs/agentgateway/latest/sessions/)

---

*See [15-optimization.md](./15-optimization.md) for performance tuning.*
