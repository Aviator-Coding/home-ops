# CLI Reference

Complete reference for the kagent command-line interface.

## Overview

The kagent CLI provides:
- Cluster installation and management
- Agent invocation and interaction
- Resource management (agents, sessions, tools)
- Development and deployment workflows

---

## Global Flags

These flags apply to all commands:

| Flag | Short | Default | Description |
|------|-------|---------|-------------|
| `--kagent-url` | | `http://localhost:8083` | kagent server URL |
| `--namespace` | `-n` | `kagent` | Kubernetes namespace |
| `--output-format` | `-o` | `table` | Output format (table, json, yaml) |
| `--timeout` | | `300s` | Operation timeout |
| `--verbose` | `-v` | | Enable verbose output |

---

## Installation Commands

### kagent install

Install kagent to a Kubernetes cluster.

```bash
kagent install [flags]
```

**Flags:**

| Flag | Description |
|------|-------------|
| `--profile` | Installation profile (`minimal` or `demo`) |

**Examples:**

```bash
# Default installation
kagent install

# Minimal installation (no pre-loaded agents)
kagent install --profile minimal

# Full demo installation with pre-loaded agents
kagent install --profile demo

# Custom namespace
kagent install --profile demo -n my-namespace
```

---

### kagent uninstall

Remove kagent from a Kubernetes cluster.

```bash
kagent uninstall [flags]
```

**Examples:**

```bash
# Uninstall from default namespace
kagent uninstall

# Uninstall from specific namespace
kagent uninstall -n my-namespace
```

---

## Dashboard

### kagent dashboard

Open the kagent web dashboard.

```bash
kagent dashboard [flags]
```

**Behavior:**
- Starts port-forwarding to kagent UI service
- Opens browser at `http://localhost:8082`
- Keeps running until interrupted

**Examples:**

```bash
# Open dashboard
kagent dashboard

# Verbose output
kagent dashboard -v
```

---

## Agent Interaction

### kagent invoke

Invoke a kagent agent to perform a task.

```bash
kagent invoke [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--agent` | `-a` | Agent name to invoke |
| `--task` | `-t` | Task description |
| `--file` | `-f` | Read task from file |
| `--session` | `-s` | Session ID (for conversation continuity) |
| `--stream` | `-S` | Stream the response |
| `--url-override` | `-u` | Override agent URL |

**Examples:**

```bash
# Basic invocation
kagent invoke --agent k8s-agent --task "List all pods"

# Short form
kagent invoke -a k8s-agent -t "List all pods"

# With streaming
kagent invoke -a k8s-agent -t "Analyze cluster health" --stream

# From file
echo "List all deployments and their replica counts" > task.txt
kagent invoke -a k8s-agent --file task.txt

# Continue conversation
kagent invoke -a k8s-agent -t "What about in the kube-system namespace?" --session abc123

# Specify namespace
kagent invoke -a k8s-agent -t "Get pods" -n my-namespace
```

---

## Resource Management

### kagent get

Get kagent resources.

```bash
kagent get <resource-type> [resource-name] [flags]
```

**Resource Types:**

| Type | Description |
|------|-------------|
| `agent` | AI agents |
| `session` | Conversation sessions |
| `tool` | Available tools |

**Examples:**

```bash
# List all agents
kagent get agent

# Get specific agent
kagent get agent k8s-agent

# List agents in JSON format
kagent get agent -o json

# List all sessions
kagent get session

# Get specific session
kagent get session abc123

# List all tools
kagent get tool

# Filter tools (using grep)
kagent get tool | grep k8s
```

---

## Development Commands

### kagent init

Create a bootstrap agent project.

```bash
kagent init [project-name] [flags]
```

**Examples:**

```bash
# Create new project
kagent init my-agent

# Initialize in current directory
kagent init .
```

**Generated Structure:**

```
my-agent/
├── kagent.yaml       # Agent configuration
├── Dockerfile        # Container build
├── requirements.txt  # Python dependencies
└── src/
    └── agent.py      # Agent code
```

---

### kagent build

Build a Docker image for an agent project.

```bash
kagent build [project-directory] [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--platform` | | Target platform (e.g., `linux/amd64`) |

**Examples:**

```bash
# Build current directory
kagent build .

# Build specific project
kagent build ./my-agent

# Build for specific platform
kagent build ./my-agent --platform linux/amd64
```

---

### kagent deploy

Deploy an agent to Kubernetes.

```bash
kagent deploy [project-directory] [flags]
```

**Flags:**

| Flag | Short | Description |
|------|-------|-------------|
| `--api-key` | | API key (creates secret) |
| `--api-key-secret` | | Existing secret name |
| `--image` | `-i` | Container image |
| `--namespace` | | Target namespace |
| `--platform` | | Build platform |
| `--dry-run` | | Output YAML without applying |

**Examples:**

```bash
# Deploy with existing secret
kagent deploy ./my-agent --api-key-secret my-secret

# Deploy creating new secret
kagent deploy ./my-agent --api-key "sk-..."

# Custom image
kagent deploy ./my-agent --api-key-secret my-secret --image myregistry/myagent:v1

# Dry run (preview YAML)
kagent deploy ./my-agent --api-key "sk-..." --dry-run > manifests.yaml

# Specific namespace
kagent deploy ./my-agent --api-key-secret my-secret --namespace production

# Cross-platform build
kagent deploy ./my-agent --api-key-secret my-secret --platform linux/amd64
```

---

### kagent run

Run an agent project locally with docker-compose.

```bash
kagent run [project-directory] [flags]
```

**Examples:**

```bash
# Run current project
kagent run .

# Run specific project
kagent run ./my-agent
```

---

## MCP Server Management

### kagent mcp

MCP server management commands.

```bash
kagent mcp <subcommand> [flags]
```

### kagent add-mcp

Add an MCP server entry to kagent.yaml.

```bash
kagent add-mcp [flags]
```

**Examples:**

```bash
# Add MCP server
kagent add-mcp --name slack-mcp --url http://slack-mcp:3000
```

---

## Utility Commands

### kagent version

Print version information.

```bash
kagent version
```

**Output:**

```
kagent version 0.7.0
```

---

### kagent help

Get help for any command.

```bash
kagent help [command]
```

**Examples:**

```bash
# General help
kagent help

# Command-specific help
kagent help invoke
kagent help deploy
kagent help get
```

---

### kagent completion

Generate shell autocompletion scripts.

```bash
kagent completion <shell>
```

**Supported Shells:**
- `bash`
- `zsh`
- `fish`
- `powershell`

**Examples:**

```bash
# Bash completion
kagent completion bash > /etc/bash_completion.d/kagent

# Zsh completion
kagent completion zsh > "${fpath[1]}/_kagent"

# Fish completion
kagent completion fish > ~/.config/fish/completions/kagent.fish
```

---

### kagent bug-report

Generate a bug report for troubleshooting.

```bash
kagent bug-report [flags]
```

**Examples:**

```bash
# Generate bug report
kagent bug-report

# Save to file
kagent bug-report > bug-report.txt
```

**Collected Information:**
- kagent version
- Kubernetes cluster info
- Agent configurations
- Recent logs
- Resource status

---

## Common Workflows

### Quick Start

```bash
# Install kagent
export OPENAI_API_KEY="sk-..."
kagent install --profile demo

# Open dashboard
kagent dashboard

# Invoke pre-built agent
kagent invoke -a k8s-agent -t "List all pods"
```

### Custom Agent Development

```bash
# Initialize project
kagent init my-custom-agent
cd my-custom-agent

# Edit kagent.yaml and src/agent.py

# Build image
kagent build .

# Deploy to cluster
kagent deploy . --api-key-secret my-secret

# Test
kagent invoke -a my-custom-agent -t "Test query"
```

### Debugging

```bash
# Verbose invocation
kagent invoke -a k8s-agent -t "Debug issue" -v

# Check agent status
kagent get agent k8s-agent -o yaml

# List sessions
kagent get session

# Generate bug report
kagent bug-report
```

---

## Environment Variables

| Variable | Description |
|----------|-------------|
| `KAGENT_URL` | Default kagent server URL |
| `KAGENT_NAMESPACE` | Default namespace |
| `KAGENT_OUTPUT_FORMAT` | Default output format |

**Example:**

```bash
export KAGENT_URL="http://kagent.example.com:8083"
export KAGENT_NAMESPACE="production"

# Commands now use these defaults
kagent get agent
kagent invoke -a k8s-agent -t "List pods"
```

---

## Exit Codes

| Code | Meaning |
|------|---------|
| `0` | Success |
| `1` | General error |
| `2` | Invalid arguments |
| `3` | Connection error |
| `4` | Timeout |

---

## Next Steps

- [Pre-built Agents](./09-prebuilt-agents.md) - Ready-to-use agents
- [Examples](./10-examples.md) - Practical examples
- [FAQ](./11-faq.md) - Frequently asked questions
