# KMCP CLI Reference

Complete reference for all KMCP CLI commands.

## Global Flags

These flags are available for all commands:

| Flag | Short | Description |
|------|-------|-------------|
| `--verbose` | `-v` | Enable verbose output |
| `--help` | `-h` | Display help information |

## Commands Overview

| Command | Description |
|---------|-------------|
| `kmcp init` | Create a new MCP server project |
| `kmcp add-tool` | Add a tool to an existing project |
| `kmcp build` | Build a Docker image |
| `kmcp run` | Run server locally with inspector |
| `kmcp install` | Install KMCP controller to cluster |
| `kmcp deploy` | Deploy server to Kubernetes |
| `kmcp secrets` | Manage Kubernetes secrets |
| `kmcp completion` | Generate shell autocompletion |
| `kmcp help` | Get help for any command |

---

## kmcp init

Create a scaffold for a new MCP server project.

### Syntax

```bash
kmcp init <subcommand> <project-name> [flags]
```

### Subcommands

| Subcommand | Description |
|------------|-------------|
| `python` | Create FastMCP Python project |
| `go` | Create MCP Go project |

### Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--author` | Project author name | - |
| `--email` | Author email address | - |
| `--description` | Project description | - |
| `--namespace` | Default Kubernetes namespace | "default" |
| `--force` | Overwrite existing directory | false |
| `--no-git` | Skip git initialization | false |
| `--non-interactive` | Use defaults without prompts | false |

### Go-Specific Flags

| Flag | Description |
|------|-------------|
| `--go-module-name` | Go module name (required for go) |

### Examples

```bash
# Create Python project
kmcp init python my-mcp-server

# Create Python project non-interactively
kmcp init python my-mcp-server \
  --author "Jane Developer" \
  --email "jane@example.com" \
  --description "My MCP server" \
  --non-interactive

# Create Go project
kmcp init go my-mcp-server --go-module-name github.com/myorg/my-mcp-server

# Create project in specific namespace
kmcp init python weather-server --namespace tools

# Force overwrite existing directory
kmcp init python my-mcp-server --force
```

---

## kmcp add-tool

Add a new tool boilerplate to an existing MCP project.

### Syntax

```bash
kmcp add-tool <tool-name> [flags]
```

### Flags

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--description` | `-d` | Tool description | - |
| `--force` | `-f` | Overwrite existing tool | false |
| `--interactive` | `-i` | Interactive mode | false |
| `--project-dir` | | Project directory | "." |

### Examples

```bash
# Add tool to current project
kmcp add-tool weather

# Add tool to specific project
kmcp add-tool database --project-dir my-mcp-server

# Add tool with description
kmcp add-tool fetch \
  --description "Fetch data from URLs" \
  --project-dir my-mcp-server

# Interactive mode
kmcp add-tool api-client --interactive

# Overwrite existing tool
kmcp add-tool weather --force
```

---

## kmcp build

Build a Docker image for your MCP server.

### Syntax

```bash
kmcp build [flags]
```

### Flags

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--project-dir` | `-d` | Project directory | "." |
| `--tag` | `-t` | Image tag | - |
| `--platform` | | Target platform | local |
| `--push` | | Push to registry | false |
| `--kind-load` | | Load to Kind cluster | false |
| `--kind-load-cluster` | | Kind cluster name | current |

### Examples

```bash
# Build with default tag
kmcp build --project-dir my-mcp-server

# Build with custom tag
kmcp build -d my-mcp-server -t my-mcp-server:v1.0.0

# Build and load to Kind
kmcp build -d my-mcp-server -t my-server:latest --kind-load-cluster kind

# Build for multiple platforms
kmcp build -d my-mcp-server -t my-server:latest --platform linux/amd64,linux/arm64

# Build and push to registry
kmcp build -d my-mcp-server -t ghcr.io/myorg/my-server:v1.0.0 --push
```

---

## kmcp run

Run an MCP server locally with the MCP Inspector.

### Syntax

```bash
kmcp run [flags]
```

### Flags

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--project-dir` | `-d` | Project directory | "." |
| `--no-inspector` | | Skip opening inspector | false |

### Examples

```bash
# Run with inspector
kmcp run --project-dir my-mcp-server

# Run without inspector
kmcp run -d my-mcp-server --no-inspector
```

---

## kmcp install

Install the KMCP controller to a Kubernetes cluster.

### Syntax

```bash
kmcp install [flags]
```

### Flags

| Flag | Description | Default |
|------|-------------|---------|
| `--namespace` | Controller namespace | "kmcp-system" |
| `--version` | Controller version | CLI version |

### Examples

```bash
# Install with defaults
kmcp install

# Install to custom namespace
kmcp install --namespace my-kmcp-namespace

# Install specific version
kmcp install --version v1.2.3

# Verbose installation
kmcp install --verbose
```

---

## kmcp deploy

Deploy an MCP server to Kubernetes.

### Syntax

```bash
kmcp deploy [name] [flags]
```

### Subcommands

| Subcommand | Description |
|------------|-------------|
| `package` | Deploy from npm/PyPI package |

### Flags

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--file` | `-f` | Path to kmcp.yaml | "." |
| `--image` | | Docker image | - |
| `--namespace` | `-n` | Kubernetes namespace | "default" |
| `--environment` | | Target environment | "staging" |
| `--command` | | Override command | - |
| `--args` | | Command arguments | - |
| `--port` | | Container port | from config |
| `--target-port` | | Target port (HTTP) | same as port |
| `--transport` | | Transport type | "stdio" |
| `--env` | | Environment variables | - |
| `--dry-run` | | Generate without applying | false |
| `--output` | `-o` | Output file | stdout |
| `--force` | | Force deploy | false |
| `--no-inspector` | | Skip inspector | false |

### Package Flags

| Flag | Description |
|------|-------------|
| `--deployment-name` | Deployment name |
| `--manager` | Package manager (npx, uvx) |
| `--args` | Package arguments |

### Examples

```bash
# Deploy from project config
kmcp deploy --file my-mcp-server/kmcp.yaml --image my-server:latest

# Deploy to specific namespace
kmcp deploy -f my-mcp-server/kmcp.yaml --image my-server:latest -n tools

# Deploy with environment variables
kmcp deploy -f kmcp.yaml --image my-server:latest \
  --env "API_KEY=abc123" \
  --env "LOG_LEVEL=debug"

# Deploy with HTTP transport
kmcp deploy -f kmcp.yaml --image my-server:latest --transport http --port 8080

# Generate manifest only (dry-run)
kmcp deploy -f kmcp.yaml --image my-server:latest --dry-run -o deployment.yaml

# Deploy to production environment
kmcp deploy -f kmcp.yaml --image my-server:v1.0.0 --environment production

# Deploy npm package
kmcp deploy package \
  --deployment-name github-server \
  --manager npx \
  --args "@modelcontextprotocol/server-github"

# Deploy Python package
kmcp deploy package \
  --deployment-name fetch-server \
  --manager uvx \
  --args "mcp-server-fetch"
```

---

## kmcp secrets

Manage Kubernetes secrets for MCP servers.

### Syntax

```bash
kmcp secrets <subcommand> [flags]
```

### Subcommands

| Subcommand | Description |
|------------|-------------|
| `sync` | Sync secrets to Kubernetes |

### Sync Flags

| Flag | Short | Description | Default |
|------|-------|-------------|---------|
| `--from-file` | | Source .env file | ".env" |
| `--project-dir` | `-d` | Project directory | "." |
| `--dry-run` | | Show YAML without applying | false |

### Examples

```bash
# Sync staging secrets
kmcp secrets sync staging --from-file .env.staging --project-dir my-mcp-server

# Sync production secrets
kmcp secrets sync production --from-file .env.production -d my-mcp-server

# Preview secret YAML
kmcp secrets sync staging --from-file .env.staging --dry-run

# Default .env file
kmcp secrets sync staging -d my-mcp-server
```

---

## kmcp completion

Generate shell autocompletion scripts.

### Syntax

```bash
kmcp completion <shell>
```

### Shells

| Shell | Command |
|-------|---------|
| bash | `kmcp completion bash` |
| zsh | `kmcp completion zsh` |
| fish | `kmcp completion fish` |
| powershell | `kmcp completion powershell` |

### Installation

**Bash:**

```bash
kmcp completion bash > /etc/bash_completion.d/kmcp
source /etc/bash_completion.d/kmcp
```

**Zsh:**

```bash
kmcp completion zsh > "${fpath[1]}/_kmcp"
source ~/.zshrc
```

**Fish:**

```bash
kmcp completion fish > ~/.config/fish/completions/kmcp.fish
```

**PowerShell:**

```powershell
kmcp completion powershell > kmcp.ps1
. ./kmcp.ps1
```

---

## kmcp help

Get help for any command.

### Syntax

```bash
kmcp help [command]
```

### Examples

```bash
# General help
kmcp help

# Help for specific command
kmcp help init
kmcp help deploy
kmcp help secrets sync
```

---

## Environment Variables

KMCP respects these environment variables:

| Variable | Description |
|----------|-------------|
| `KUBECONFIG` | Kubernetes config file path |
| `KMCP_NAMESPACE` | Default namespace |
| `KMCP_VERBOSE` | Enable verbose output |
| `DOCKER_HOST` | Docker daemon socket |

---

## Exit Codes

| Code | Description |
|------|-------------|
| 0 | Success |
| 1 | General error |
| 2 | Invalid arguments |
| 3 | Configuration error |
| 4 | Kubernetes error |
| 5 | Docker error |

---

## Quick Reference

```bash
# Create project
kmcp init python my-server

# Add tool
kmcp add-tool weather -d my-server

# Run locally
kmcp run -d my-server

# Build image
kmcp build -d my-server -t my-server:latest --kind-load-cluster kind

# Install controller
helm install kmcp-crds oci://ghcr.io/kagent-dev/kmcp/helm/kmcp-crds \
  --namespace kmcp-system --create-namespace
kmcp install

# Deploy
kmcp deploy -f my-server/kmcp.yaml --image my-server:latest

# Deploy package
kmcp deploy package --deployment-name test --manager npx --args "@mcp/server"

# Sync secrets
kmcp secrets sync staging --from-file .env.staging -d my-server

# Generate completion
kmcp completion zsh > ~/.zsh/completions/_kmcp
```
