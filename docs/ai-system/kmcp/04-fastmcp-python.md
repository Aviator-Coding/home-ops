# FastMCP Python Development Guide

FastMCP Python is a lightweight, high-performance Python framework that implements the Model Context Protocol (MCP). Using KMCP, developers can rapidly scaffold MCP projects with sample servers and tools as boilerplate for custom development.

## Prerequisites

### Required Tools

| Tool | Purpose | Installation |
|------|---------|--------------|
| **uv** | Python package manager | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| **MCP Inspector** | Testing MCP servers | `npx @modelcontextprotocol/inspector` |
| **Docker** | Container builds | [docker.com](https://docker.com) |

### Verify uv Installation

```bash
uv --version
# uv 0.x.x
```

## Project Initialization

### Create a New Project

```bash
kmcp init python my-mcp-server
```

Interactive prompts:

```
? Description (optional): A custom MCP server
? Author (optional): Jane Developer
? Email (optional): jane@example.com
```

### Non-Interactive Mode

```bash
kmcp init python my-mcp-server \
  --author "Jane Developer" \
  --email "jane@example.com" \
  --description "A custom MCP server" \
  --non-interactive
```

### Available Flags

| Flag | Description |
|------|-------------|
| `--author` | Set project author |
| `--email` | Set author email |
| `--description` | Set project description |
| `--namespace` | Default Kubernetes namespace (default: "default") |
| `--force` | Overwrite existing directory |
| `--no-git` | Skip git initialization |
| `--non-interactive` | Use defaults without prompts |

## Project Structure

```
my-mcp-server/
├── src/
│   ├── core/                 # MCP server core utilities
│   │   ├── __init__.py
│   │   └── server.py         # Server configuration
│   ├── tools/                # Tool implementations
│   │   ├── __init__.py
│   │   └── echo.py           # Sample echo tool
│   └── main.py               # Entry point
├── tests/                    # Test suite
│   ├── __init__.py
│   └── test_echo.py
├── Dockerfile                # Container definition
├── kmcp.yaml                 # Project configuration
├── pyproject.toml            # Python dependencies
├── .env.example              # Environment template
├── .gitignore
└── README.md
```

## Understanding the Sample Echo Tool

**src/tools/echo.py:**

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("echo-server")

@mcp.tool()
def echo(message: str) -> str:
    """
    Echo a message back to the user.

    Args:
        message: The message to echo back

    Returns:
        The same message that was sent
    """
    return f"Echo: {message}"
```

**Key Components:**

- `FastMCP("echo-server")` - Creates an MCP server instance
- `@mcp.tool()` - Decorator that registers a function as an MCP tool
- Docstring - Used for tool description and schema generation
- Type hints - Define input/output schemas

## Creating Custom Tools

### Add Tool Boilerplate

```bash
kmcp add-tool weather --project-dir my-mcp-server
```

### Review Generated Code

```bash
cat my-mcp-server/src/tools/weather.py
```

Generated template:

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

@mcp.tool()
def weather(message: str) -> str:
    """
    A sample tool that echoes the input message.

    Args:
        message: The input message

    Returns:
        The echoed message
    """
    return f"weather: {message}"
```

### Implement Your Logic

```python
import os
import httpx
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("weather")

@mcp.tool()
def get_current_weather(city: str, units: str = "celsius") -> dict:
    """
    Get current weather for a city.

    Args:
        city: The city name (e.g., "San Francisco")
        units: Temperature units - "celsius" or "fahrenheit"

    Returns:
        Weather data including temperature, conditions, and humidity
    """
    api_key = os.environ.get("WEATHER_API_KEY")
    if not api_key:
        return {"error": "WEATHER_API_KEY not configured"}

    response = httpx.get(
        f"https://api.weather.com/v1/current",
        params={"city": city, "units": units, "key": api_key}
    )
    return response.json()

@mcp.tool()
def get_forecast(city: str, days: int = 5) -> list:
    """
    Get weather forecast for a city.

    Args:
        city: The city name
        days: Number of days to forecast (1-14)

    Returns:
        List of daily forecasts
    """
    # Implementation here
    pass
```

### Tool Registration

Tools are automatically registered when imported. Update `src/main.py`:

```python
from mcp.server.fastmcp import FastMCP

# Import tools to register them
from tools.echo import mcp as echo_mcp
from tools.weather import mcp as weather_mcp

# Create main server
app = FastMCP("my-mcp-server")

# Mount tool servers
app.mount("/echo", echo_mcp)
app.mount("/weather", weather_mcp)

if __name__ == "__main__":
    app.run()
```

## Local Development

### Run the Server

```bash
kmcp run --project-dir my-mcp-server
```

This command:
1. Builds the Docker image
2. Starts the MCP server
3. Opens the MCP Inspector

### Skip Inspector

```bash
kmcp run --project-dir my-mcp-server --no-inspector
```

### MCP Inspector Configuration

| Field | Value |
|-------|-------|
| **Transport Type** | STDIO |
| **Command** | `uv` |
| **Arguments** | `run python src/main.py` |
| **Proxy Session Token** | (from kmcp run output) |

### Testing Tools

1. Click **Connect** in the inspector
2. Go to **Tools** tab
3. Click **List Tools**
4. Select your tool
5. Enter test values
6. Click **Run Tool**

## Configuration File

**kmcp.yaml:**

```yaml
name: my-mcp-server
version: 0.1.0
description: A custom MCP server
author: Jane Developer
email: jane@example.com

# Server configuration
server:
  port: 3000
  transport: stdio

# Deployment settings
deployment:
  namespace: default
  replicas: 1

# Resource limits
resources:
  limits:
    cpu: 500m
    memory: 512Mi
  requests:
    cpu: 100m
    memory: 128Mi

# Secrets configuration
secrets:
  local:
    enabled: false
    provider: env
    file: .env.local
  staging:
    enabled: false
    provider: kubernetes
    secretName: my-mcp-server-secrets-staging
    namespace: default
  production:
    enabled: false
    provider: kubernetes
    secretName: my-mcp-server-secrets-production
    namespace: default
```

## Working with Dependencies

### Add Python Packages

Edit `pyproject.toml`:

```toml
[project]
name = "my-mcp-server"
version = "0.1.0"
dependencies = [
    "mcp>=1.0.0",
    "httpx>=0.25.0",
    "pydantic>=2.0.0",
]

[tool.uv]
dev-dependencies = [
    "pytest>=7.0.0",
    "pytest-asyncio>=0.21.0",
]
```

### Install Dependencies

```bash
cd my-mcp-server
uv sync
```

## Testing

### Run Unit Tests

```bash
cd my-mcp-server
uv run pytest
```

### Test File Example

**tests/test_weather.py:**

```python
import pytest
from tools.weather import get_current_weather

def test_weather_requires_api_key(monkeypatch):
    monkeypatch.delenv("WEATHER_API_KEY", raising=False)
    result = get_current_weather("San Francisco")
    assert "error" in result

def test_weather_valid_response(monkeypatch):
    monkeypatch.setenv("WEATHER_API_KEY", "test-key")
    # Mock API response
    result = get_current_weather("San Francisco")
    assert "temperature" in result
```

## Building for Production

### Build Docker Image

```bash
kmcp build --project-dir my-mcp-server -t my-mcp-server:latest
```

### Multi-Platform Build

```bash
kmcp build --project-dir my-mcp-server \
  -t my-mcp-server:latest \
  --platform linux/amd64,linux/arm64
```

### Push to Registry

```bash
kmcp build --project-dir my-mcp-server \
  -t ghcr.io/myorg/my-mcp-server:latest \
  --push
```

### Load to Kind Cluster

```bash
kmcp build --project-dir my-mcp-server \
  -t my-mcp-server:latest \
  --kind-load-cluster kind
```

## Deployment

See [Deploying Servers](./08-deploying-servers.md) for full deployment instructions.

### Quick Deploy

```bash
kmcp deploy --file my-mcp-server/kmcp.yaml --image my-mcp-server:latest
```

### With Environment Variables

```bash
kmcp deploy \
  --file my-mcp-server/kmcp.yaml \
  --image my-mcp-server:latest \
  --env "WEATHER_API_KEY=abc123"
```

## Best Practices

### 1. Type Hints

Always use type hints for tool parameters and returns:

```python
@mcp.tool()
def process_data(
    data: list[dict],
    filter_key: str,
    limit: int = 100
) -> dict[str, any]:
    """Process data with proper typing."""
    pass
```

### 2. Descriptive Docstrings

Docstrings become tool descriptions:

```python
@mcp.tool()
def analyze_text(text: str) -> dict:
    """
    Analyze text for sentiment and key phrases.

    This tool uses NLP to extract:
    - Sentiment score (-1 to 1)
    - Key phrases and entities
    - Language detection

    Args:
        text: The text to analyze (max 10,000 characters)

    Returns:
        Analysis results with sentiment, phrases, and language
    """
    pass
```

### 3. Error Handling

Return structured errors:

```python
@mcp.tool()
def fetch_data(url: str) -> dict:
    """Fetch data from a URL."""
    try:
        response = httpx.get(url, timeout=30)
        response.raise_for_status()
        return {"success": True, "data": response.json()}
    except httpx.HTTPError as e:
        return {"success": False, "error": str(e)}
```

### 4. Environment Variables

Use environment variables for secrets:

```python
import os

@mcp.tool()
def authenticate(user_id: str) -> dict:
    """Authenticate a user."""
    api_key = os.environ.get("AUTH_API_KEY")
    if not api_key:
        raise ValueError("AUTH_API_KEY environment variable required")
    # ...
```

## Next Steps

- [Adding Tools](./06-adding-tools.md) - More tool patterns
- [Secrets Management](./11-secrets-management.md) - Configure environment variables
- [Deploying Servers](./08-deploying-servers.md) - Production deployment
- [HTTP Transport](./10-http-transport.md) - HTTP-based servers
