# Adding Tools to MCP Servers

This guide covers creating custom MCP tools for both FastMCP Python and MCP Go projects.

## Using the kmcp CLI

### Add Tool Boilerplate

```bash
kmcp add-tool <tool-name> --project-dir <project-directory>
```

### Available Flags

| Flag | Short | Description |
|------|-------|-------------|
| `--description` | `-d` | Tool description |
| `--force` | `-f` | Overwrite existing tool file |
| `--interactive` | `-i` | Interactive tool creation mode |
| `--project-dir` | | Project directory (default: current) |

### Examples

```bash
# Basic tool creation
kmcp add-tool weather --project-dir my-mcp-server

# With description
kmcp add-tool database-query \
  --project-dir my-mcp-server \
  --description "Execute database queries"

# Interactive mode
kmcp add-tool api-client \
  --project-dir my-mcp-server \
  --interactive
```

## Python Tool Patterns

### Basic Tool

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("basic-tools")

@mcp.tool()
def hello(name: str) -> str:
    """Say hello to someone."""
    return f"Hello, {name}!"
```

### Tool with Optional Parameters

```python
@mcp.tool()
def search(
    query: str,
    limit: int = 10,
    offset: int = 0,
    sort_by: str = "relevance"
) -> list[dict]:
    """
    Search for items.

    Args:
        query: Search query string
        limit: Maximum results to return (default: 10)
        offset: Number of results to skip (default: 0)
        sort_by: Sort order - "relevance" or "date" (default: "relevance")

    Returns:
        List of matching items
    """
    # Implementation
    return []
```

### Tool with Complex Types

```python
from pydantic import BaseModel
from typing import Optional

class SearchFilter(BaseModel):
    category: str
    min_price: Optional[float] = None
    max_price: Optional[float] = None
    in_stock: bool = True

class SearchResult(BaseModel):
    id: str
    name: str
    price: float
    category: str

@mcp.tool()
def advanced_search(
    query: str,
    filters: SearchFilter
) -> list[SearchResult]:
    """
    Advanced search with filters.

    Args:
        query: Search query
        filters: Search filter options

    Returns:
        List of search results
    """
    # Implementation using Pydantic models
    return []
```

### Async Tool

```python
import httpx

@mcp.tool()
async def fetch_data(url: str) -> dict:
    """
    Fetch data from a URL asynchronously.

    Args:
        url: The URL to fetch

    Returns:
        JSON response data
    """
    async with httpx.AsyncClient() as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.json()
```

### Tool with Error Handling

```python
from typing import Union

@mcp.tool()
def safe_divide(
    numerator: float,
    denominator: float
) -> dict[str, Union[float, str]]:
    """
    Safely divide two numbers.

    Args:
        numerator: The number to divide
        denominator: The number to divide by

    Returns:
        Result with either value or error message
    """
    if denominator == 0:
        return {
            "success": False,
            "error": "Cannot divide by zero"
        }

    return {
        "success": True,
        "result": numerator / denominator
    }
```

### Tool with Environment Variables

```python
import os

@mcp.tool()
def authenticated_request(endpoint: str) -> dict:
    """
    Make an authenticated API request.

    Args:
        endpoint: API endpoint path

    Returns:
        API response
    """
    api_key = os.environ.get("API_KEY")
    base_url = os.environ.get("API_BASE_URL", "https://api.example.com")

    if not api_key:
        return {"error": "API_KEY environment variable not set"}

    # Make authenticated request
    response = httpx.get(
        f"{base_url}/{endpoint}",
        headers={"Authorization": f"Bearer {api_key}"}
    )
    return response.json()
```

### Tool with File Operations

```python
import os
from pathlib import Path

@mcp.tool()
def read_config(config_name: str) -> dict:
    """
    Read a configuration file.

    Args:
        config_name: Name of the config file (without extension)

    Returns:
        Configuration data
    """
    config_dir = os.environ.get("CONFIG_DIR", "/etc/myapp")
    config_path = Path(config_dir) / f"{config_name}.yaml"

    if not config_path.exists():
        return {"error": f"Config {config_name} not found"}

    import yaml
    with open(config_path) as f:
        return yaml.safe_load(f)
```

## Go Tool Patterns

### Basic Tool

```go
package tools

import (
    "context"
    "fmt"

    "github.com/mark3labs/mcp-go/mcp"
)

type HelloTool struct{}

func NewHelloTool() *HelloTool {
    return &HelloTool{}
}

func (t *HelloTool) Definition() mcp.Tool {
    return mcp.Tool{
        Name:        "hello",
        Description: "Say hello to someone",
        InputSchema: mcp.ToolInputSchema{
            Type: "object",
            Properties: map[string]any{
                "name": map[string]any{
                    "type":        "string",
                    "description": "Name of the person to greet",
                },
            },
            Required: []string{"name"},
        },
    }
}

func (t *HelloTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    name := args["name"].(string)

    return &mcp.CallToolResult{
        Content: []any{
            mcp.TextContent{
                Type: "text",
                Text: fmt.Sprintf("Hello, %s!", name),
            },
        },
    }, nil
}
```

### Tool with Optional Parameters

```go
func (t *SearchTool) Definition() mcp.Tool {
    return mcp.Tool{
        Name:        "search",
        Description: "Search for items",
        InputSchema: mcp.ToolInputSchema{
            Type: "object",
            Properties: map[string]any{
                "query": map[string]any{
                    "type":        "string",
                    "description": "Search query",
                },
                "limit": map[string]any{
                    "type":        "integer",
                    "description": "Maximum results",
                    "default":     10,
                },
                "sort_by": map[string]any{
                    "type":        "string",
                    "description": "Sort order",
                    "enum":        []string{"relevance", "date", "price"},
                    "default":     "relevance",
                },
            },
            Required: []string{"query"},
        },
    }
}

func (t *SearchTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    query := args["query"].(string)

    // Handle optional parameters with defaults
    limit := 10
    if l, ok := args["limit"].(float64); ok {
        limit = int(l)
    }

    sortBy := "relevance"
    if s, ok := args["sort_by"].(string); ok {
        sortBy = s
    }

    // Implementation
    results := t.performSearch(query, limit, sortBy)

    jsonResult, _ := json.Marshal(results)
    return &mcp.CallToolResult{
        Content: []any{
            mcp.TextContent{
                Type: "text",
                Text: string(jsonResult),
            },
        },
    }, nil
}
```

### Tool with HTTP Client

```go
type APITool struct {
    client  *http.Client
    baseURL string
    apiKey  string
}

func NewAPITool() *APITool {
    return &APITool{
        client: &http.Client{
            Timeout: 30 * time.Second,
        },
        baseURL: os.Getenv("API_BASE_URL"),
        apiKey:  os.Getenv("API_KEY"),
    }
}

func (t *APITool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    endpoint := args["endpoint"].(string)

    req, err := http.NewRequestWithContext(
        ctx,
        http.MethodGet,
        fmt.Sprintf("%s/%s", t.baseURL, endpoint),
        nil,
    )
    if err != nil {
        return nil, fmt.Errorf("failed to create request: %w", err)
    }

    req.Header.Set("Authorization", fmt.Sprintf("Bearer %s", t.apiKey))
    req.Header.Set("Content-Type", "application/json")

    resp, err := t.client.Do(req)
    if err != nil {
        return nil, fmt.Errorf("request failed: %w", err)
    }
    defer resp.Body.Close()

    body, _ := io.ReadAll(resp.Body)

    return &mcp.CallToolResult{
        Content: []any{
            mcp.TextContent{
                Type: "text",
                Text: string(body),
            },
        },
    }, nil
}
```

### Tool with Error Results

```go
func (t *DivideTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    numerator := args["numerator"].(float64)
    denominator := args["denominator"].(float64)

    if denominator == 0 {
        return &mcp.CallToolResult{
            Content: []any{
                mcp.TextContent{
                    Type: "text",
                    Text: "Error: Cannot divide by zero",
                },
            },
            IsError: true,
        }, nil
    }

    result := numerator / denominator
    return &mcp.CallToolResult{
        Content: []any{
            mcp.TextContent{
                Type: "text",
                Text: fmt.Sprintf("%.4f", result),
            },
        },
    }, nil
}
```

### Tool with Database Connection

```go
type DatabaseTool struct {
    db *sql.DB
}

func NewDatabaseTool() (*DatabaseTool, error) {
    connStr := os.Getenv("DATABASE_URL")
    db, err := sql.Open("postgres", connStr)
    if err != nil {
        return nil, fmt.Errorf("failed to connect to database: %w", err)
    }

    return &DatabaseTool{db: db}, nil
}

func (t *DatabaseTool) Definition() mcp.Tool {
    return mcp.Tool{
        Name:        "query_users",
        Description: "Query users from the database",
        InputSchema: mcp.ToolInputSchema{
            Type: "object",
            Properties: map[string]any{
                "filter": map[string]any{
                    "type":        "string",
                    "description": "Filter condition (e.g., 'active=true')",
                },
                "limit": map[string]any{
                    "type":        "integer",
                    "description": "Maximum rows to return",
                    "default":     100,
                },
            },
        },
    }
}

func (t *DatabaseTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    limit := 100
    if l, ok := args["limit"].(float64); ok {
        limit = int(l)
    }

    query := "SELECT id, name, email FROM users"
    if filter, ok := args["filter"].(string); ok && filter != "" {
        query += fmt.Sprintf(" WHERE %s", filter)
    }
    query += fmt.Sprintf(" LIMIT %d", limit)

    rows, err := t.db.QueryContext(ctx, query)
    if err != nil {
        return nil, fmt.Errorf("query failed: %w", err)
    }
    defer rows.Close()

    var results []map[string]any
    for rows.Next() {
        var id int
        var name, email string
        if err := rows.Scan(&id, &name, &email); err != nil {
            continue
        }
        results = append(results, map[string]any{
            "id":    id,
            "name":  name,
            "email": email,
        })
    }

    jsonResult, _ := json.MarshalIndent(results, "", "  ")
    return &mcp.CallToolResult{
        Content: []any{
            mcp.TextContent{
                Type: "text",
                Text: string(jsonResult),
            },
        },
    }, nil
}

func (t *DatabaseTool) Close() error {
    return t.db.Close()
}
```

## Tool Registration

### Python Registration

**src/main.py:**

```python
from mcp.server.fastmcp import FastMCP

# Import tool modules
from tools.echo import mcp as echo_mcp
from tools.weather import mcp as weather_mcp
from tools.database import mcp as database_mcp

# Create main server
app = FastMCP("my-mcp-server")

# Mount all tools
app.mount("/echo", echo_mcp)
app.mount("/weather", weather_mcp)
app.mount("/database", database_mcp)

if __name__ == "__main__":
    app.run()
```

### Go Registration

**tools/all_tools.go:**

```go
package tools

import (
    "github.com/mark3labs/mcp-go/server"
)

func RegisterAllTools(s *server.MCPServer) error {
    // Echo tool
    echoTool := NewEchoTool()
    s.AddTool(echoTool.Definition(), echoTool.Execute)

    // Weather tool
    weatherTool := NewWeatherTool()
    s.AddTool(weatherTool.Definition(), weatherTool.Execute)

    // Database tool (with error handling)
    dbTool, err := NewDatabaseTool()
    if err != nil {
        return fmt.Errorf("failed to initialize database tool: %w", err)
    }
    s.AddTool(dbTool.Definition(), dbTool.Execute)

    return nil
}
```

## Testing Tools

### Python Tests

```python
import pytest
from tools.weather import get_weather

def test_weather_returns_data():
    result = get_weather("San Francisco")
    assert "temperature" in result
    assert "conditions" in result

def test_weather_invalid_city():
    result = get_weather("")
    assert "error" in result

@pytest.mark.asyncio
async def test_async_fetch():
    from tools.fetch import fetch_data
    result = await fetch_data("https://api.example.com/data")
    assert result is not None
```

### Go Tests

```go
func TestWeatherTool_Execute(t *testing.T) {
    tool := NewWeatherTool()

    result, err := tool.Execute(context.Background(), map[string]any{
        "city": "San Francisco",
    })

    if err != nil {
        t.Fatalf("unexpected error: %v", err)
    }

    if len(result.Content) == 0 {
        t.Error("expected content in result")
    }
}

func TestWeatherTool_InvalidCity(t *testing.T) {
    tool := NewWeatherTool()

    result, _ := tool.Execute(context.Background(), map[string]any{
        "city": "",
    })

    if !result.IsError {
        t.Error("expected error for empty city")
    }
}
```

## Best Practices

### 1. Clear Descriptions

Write clear, actionable descriptions:

```python
# Good
"""
Send an email to specified recipients.

Args:
    to: List of recipient email addresses
    subject: Email subject line (max 200 chars)
    body: Email body in plain text or HTML

Returns:
    Confirmation with message ID
"""

# Bad
"""Send email."""
```

### 2. Validate Input

Always validate inputs before processing:

```go
func (t *MyTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    email, ok := args["email"].(string)
    if !ok || !isValidEmail(email) {
        return &mcp.CallToolResult{
            Content: []any{mcp.TextContent{Type: "text", Text: "Invalid email format"}},
            IsError: true,
        }, nil
    }
    // ...
}
```

### 3. Handle Timeouts

Respect context cancellation:

```python
import asyncio

@mcp.tool()
async def long_operation(data: str) -> dict:
    """Perform a long-running operation."""
    try:
        result = await asyncio.wait_for(
            process_data(data),
            timeout=30.0
        )
        return {"success": True, "result": result}
    except asyncio.TimeoutError:
        return {"success": False, "error": "Operation timed out"}
```

### 4. Return Structured Data

Return structured data for programmatic consumption:

```python
@mcp.tool()
def analyze_data(data: list) -> dict:
    """Analyze data and return structured results."""
    return {
        "status": "success",
        "results": {
            "count": len(data),
            "summary": {...},
            "details": [...]
        },
        "metadata": {
            "processed_at": datetime.now().isoformat(),
            "version": "1.0"
        }
    }
```

## Next Steps

- [Secrets Management](./11-secrets-management.md) - Configure environment variables
- [Deploying Servers](./08-deploying-servers.md) - Deploy to Kubernetes
- [HTTP Transport](./10-http-transport.md) - HTTP-based servers
