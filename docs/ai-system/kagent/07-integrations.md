# Integrations

Guide to integrating kagent with external platforms like Slack and Discord.

## Overview

Kagent supports bidirectional integration with chat platforms:

| Direction | Description |
|-----------|-------------|
| **Platform → Kagent** | Invoke agents from chat commands |
| **Kagent → Platform** | Send notifications and responses to channels |

---

## Slack Integration

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                     Slack Integration                               │
│                                                                     │
│  ┌─────────────┐         ┌─────────────┐         ┌─────────────┐   │
│  │   Slack     │────────►│  Slack Bot  │────────►│   kagent    │   │
│  │  Workspace  │◄────────│   (Python)  │◄────────│   Agent     │   │
│  └─────────────┘         └─────────────┘         └─────────────┘   │
│        │                                                │           │
│        │                                                │           │
│        │   ┌─────────────────────────────────────────┐  │           │
│        └──►│        Slack MCP Server                 │◄─┘           │
│            │   (For agent → Slack messaging)        │              │
│            └─────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────────┘
```

### Prerequisites

- Slack workspace with app creation permissions
- Kagent installed with kmcp CRDs
- Python environment with Bolt framework
- kubectl access to cluster

---

### Part 1: Slack App Setup

#### Step 1: Create Slack App

1. Navigate to [api.slack.com/apps](https://api.slack.com/apps)
2. Click **Create New App**
3. Select **From scratch**
4. Name your app (e.g., "kagent-bot")
5. Select your workspace

#### Step 2: Configure Bot Permissions

Navigate to **OAuth & Permissions** → **Bot Token Scopes**:

| Scope | Purpose |
|-------|---------|
| `chat:write` | Post messages to channels |
| `commands` | Support slash commands |

#### Step 3: Create App-Level Token

1. Go to **Basic Information** → **App-Level Tokens**
2. Click **Generate Token and Scopes**
3. Add `connections:write` scope
4. Save the token (starts with `xapp-`)

#### Step 4: Enable Socket Mode

1. Go to **Socket Mode**
2. Enable Socket Mode
3. This allows local development without public URLs

#### Step 5: Create Slash Command

1. Go to **Slash Commands**
2. Click **Create New Command**
3. Configure:
   - Command: `/mykagent`
   - Description: "Interact with kagent AI agents"
   - Usage Hint: `[your question]`

#### Step 6: Install to Workspace

1. Go to **Install App**
2. Click **Install to Workspace**
3. Authorize the app
4. Copy the **Bot User OAuth Token** (starts with `xoxb-`)

---

### Part 2: Deploy Slack Bot

#### Clone Template

```bash
git clone https://github.com/kagent-dev/a2a-slack-template.git
cd a2a-slack-template
```

#### Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:
```bash
# Slack tokens
SLACK_BOT_TOKEN=xoxb-your-bot-token
SLACK_APP_TOKEN=xapp-your-app-token

# kagent A2A endpoint
KAGENT_A2A_URL=http://127.0.0.1:8083/api/a2a/kagent/my-k8s-agent/
```

#### Run Bot

```bash
# Install dependencies
uv sync

# Start bot
uv run main.py
```

---

### Part 3: Deploy Kubernetes Agent

#### Create Agent with A2A

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: my-k8s-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a Kubernetes assistant accessible via Slack.
      Keep responses concise and formatted for chat.
    a2aConfig:
      skills:
        - id: answer-questions-about-your-cluster
          name: Answer Questions About Your Cluster
          description: Query and manage Kubernetes cluster resources
          inputModes: [text]
          outputModes: [text]
          examples:
            - "Show me the pods in the default namespace"
            - "What's the status of my deployments?"
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
            - k8s_describe_resource
```

#### Port-Forward Controller

```bash
kubectl port-forward -n kagent svc/kagent-controller 8083:8083
```

#### Test from Slack

In Slack, use the slash command:
```
/mykagent show me the pods in the cluster
```

---

### Part 4: Agent → Slack Notifications

Enable agents to send messages to Slack channels.

#### Create Slack Credentials Secret

```bash
# Get your workspace/team ID from Slack settings
export SLACK_BOT_TOKEN="xoxb-..."
export SLACK_TEAM_ID="T0123456789"
export SLACK_CHANNEL_IDS="C0123456789,C9876543210"

kubectl create secret generic slack-credentials -n kagent \
  --from-literal=SLACK_BOT_TOKEN="$SLACK_BOT_TOKEN" \
  --from-literal=SLACK_TEAM_ID="$SLACK_TEAM_ID" \
  --from-literal=SLACK_CHANNEL_IDS="$SLACK_CHANNEL_IDS"
```

#### Deploy Slack MCP Server

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: slack-mcp
  namespace: kagent
spec:
  deployment:
    image: "node:latest"
    port: 3000
    cmd: "npx"
    args:
      - "-y"
      - "@modelcontextprotocol/server-slack"
    secretRefs:
      - name: slack-credentials
  transportType: stdio
  stdioTransport: {}
```

#### Add to Agent

```yaml
tools:
  # ... other tools ...
  - type: McpServer
    mcpServer:
      name: slack-mcp
      kind: MCPServer
      toolNames:
        - send_message_to_slack
        - list_channels
```

Now your agent can proactively send messages to Slack channels.

---

## Discord Integration

### Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Discord Integration                              │
│                                                                     │
│  ┌─────────────┐         ┌─────────────┐         ┌─────────────┐   │
│  │   Discord   │────────►│ Discord Bot │────────►│   kagent    │   │
│  │   Server    │◄────────│  (Python)   │◄────────│   Agent     │   │
│  └─────────────┘         └─────────────┘         └─────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

### Prerequisites

- Discord server with bot creation permissions
- Kagent installed in cluster
- Python environment

---

### Part 1: Discord App Setup

#### Step 1: Create Discord Application

1. Go to [Discord Developer Portal](https://discord.com/developers/applications)
2. Click **New Application**
3. Name your application

#### Step 2: Create Bot

1. Go to **Bot** tab
2. Click **Reset Token**
3. Copy and save the token

#### Step 3: Enable Intents

Under **Privileged Gateway Intents**, enable:
- **Message Content Intent** (required to receive message content)

#### Step 4: Configure OAuth2

1. Go to **OAuth2** → **URL Generator**
2. Select scopes: `bot`
3. Select permissions:
   - Send Messages
   - Read Message History
4. Set **Integration Type** to **Guild Install**
5. Copy the generated URL

#### Step 5: Invite Bot

1. Open the generated URL
2. Select your Discord server
3. Authorize the bot

---

### Part 2: Deploy Discord Bot

#### Clone Repository

```bash
git clone https://github.com/lekkerelou/kagent-a2a-discord.git
cd kagent-a2a-discord
```

#### Configure Environment

Create `.env`:
```bash
# Discord bot token
DISCORD_BOT_TOKEN=your-discord-bot-token

# kagent A2A endpoint
KAGENT_A2A_URL=http://127.0.0.1:8083/api/a2a/kagent/my-k8s-agent

# Optional: Restrict to specific channels
# DISCORD_CHANNEL_ONLY=123456789,987654321

# Optional: Only respond to @mentions
# DISCORD_MENTION_ONLY=true
```

#### Run Locally

```bash
# Install dependencies
uv sync

# Start bot
python main.py
```

#### Run with Docker

```bash
# Pull image
docker pull ghcr.io/lekkerelou/kagent-a2a-discord:latest

# Run
docker run --env-file .env ghcr.io/lekkerelou/kagent-a2a-discord:latest
```

---

### Part 3: Configure Agent

Use the same agent configuration as Slack:

```yaml
apiVersion: kagent.dev/v1alpha2
kind: Agent
metadata:
  name: my-k8s-agent
  namespace: kagent
spec:
  type: Declarative
  declarative:
    modelConfig: default-model-config
    systemMessage: |
      You're a Kubernetes assistant accessible via Discord.
      Keep responses concise and use Discord-friendly formatting.
    a2aConfig:
      skills:
        - id: cluster-management
          name: Cluster Management
          description: Query and manage Kubernetes resources
          inputModes: [text]
          outputModes: [text]
    tools:
      - type: McpServer
        mcpServer:
          name: kagent-tool-server
          kind: RemoteMCPServer
          toolNames:
            - k8s_get_resources
            - k8s_get_pod_logs
```

---

### Configuration Options

| Variable | Description | Default |
|----------|-------------|---------|
| `DISCORD_BOT_TOKEN` | Bot authentication token | Required |
| `KAGENT_A2A_URL` | Agent A2A endpoint | Required |
| `DISCORD_MENTION_ONLY` | Only respond to @mentions | `false` |
| `DISCORD_CHANNEL_ONLY` | Comma-separated channel IDs | All channels |

---

## Production Considerations

### Slack Production Deployment

For production, switch from Socket Mode to URL-based events:

1. Deploy bot with public URL
2. Configure Request URL in Slack app
3. Use kgateway or ingress for exposure

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: slack-bot
spec:
  rules:
    - host: slack-bot.example.com
      http:
        paths:
          - path: /slack/events
            pathType: Prefix
            backend:
              service:
                name: slack-bot
                port:
                  number: 3000
```

### Discord Production Deployment

Deploy as Kubernetes workload:

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: discord-bot
  namespace: kagent
spec:
  replicas: 1
  selector:
    matchLabels:
      app: discord-bot
  template:
    metadata:
      labels:
        app: discord-bot
    spec:
      containers:
        - name: bot
          image: ghcr.io/lekkerelou/kagent-a2a-discord:latest
          envFrom:
            - secretRef:
                name: discord-bot-secrets
```

### Security Best Practices

1. **Store tokens in secrets**, never in code
2. **Restrict channel access** using configuration options
3. **Rate limit** bot responses
4. **Audit log** all agent invocations
5. **Use RBAC** to limit agent capabilities

---

## Troubleshooting

### Slack Issues

**Bot not responding:**
```bash
# Check bot logs
# Verify SLACK_BOT_TOKEN and SLACK_APP_TOKEN
# Ensure Socket Mode is enabled
```

**Permission errors:**
```bash
# Verify bot has required scopes
# Re-install app to workspace
```

### Discord Issues

**Bot offline:**
```bash
# Verify DISCORD_BOT_TOKEN
# Check Message Content Intent is enabled
# Verify bot is invited to server
```

**Not receiving messages:**
```bash
# Check DISCORD_CHANNEL_ONLY configuration
# Verify bot has Read Message History permission
```

### Connection Issues

```bash
# Verify kagent controller is accessible
kubectl port-forward -n kagent svc/kagent-controller 8083:8083

# Test A2A endpoint
curl http://localhost:8083/api/a2a/kagent/my-k8s-agent/.well-known/agent.json
```

---

## Next Steps

- [CLI Reference](./08-cli-reference.md) - Complete CLI documentation
- [Pre-built Agents](./09-prebuilt-agents.md) - Ready-to-use agents
- [Examples](./10-examples.md) - More integration examples
