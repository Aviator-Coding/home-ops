# MCP Go Development Guide

MCP Go uses the native Go SDK to implement the Model Context Protocol (MCP). The framework provides type-safe tool definitions and is best suited for high-throughput and high-performance services, system-level integrations, and performance-critical applications.

## Prerequisites

### Required Tools

| Tool | Purpose | Installation |
|------|---------|--------------|
| **Go 1.23+** | Go runtime | [go.dev](https://go.dev/doc/install) |
| **MCP Inspector** | Testing MCP servers | `npx @modelcontextprotocol/inspector` |
| **Docker** | Container builds | [docker.com](https://docker.com) |

### Verify Go Installation

```bash
go version
# go version go1.23.x ...
```

## Project Initialization

### Create a New Project

```bash
kmcp init go my-mcp-server --go-module-name my-mcp-server
```

### With Full Module Path

```bash
kmcp init go my-mcp-server --go-module-name github.com/myorg/my-mcp-server
```

### Available Flags

| Flag | Description |
|------|-------------|
| `--go-module-name` | Go module name (required for go projects) |
| `--author` | Set project author |
| `--email` | Set author email |
| `--description` | Set project description |
| `--namespace` | Default Kubernetes namespace |
| `--force` | Overwrite existing directory |
| `--no-git` | Skip git initialization |
| `--non-interactive` | Use defaults without prompts |

## Project Structure

```
my-mcp-server/
├── main.go              # Server entry point
├── go.mod               # Go module definition
├── go.sum               # Dependency checksums
├── tools/               # Tool implementations
│   ├── all_tools.go     # Tool registration
│   ├── echo.go          # Sample echo tool
│   └── tool.go          # Tool template
├── Dockerfile           # Container definition
├── kmcp.yaml            # Project configuration
├── .gitignore
└── README.md
```

## Understanding the Sample Echo Tool

**tools/echo.go:**

```go
package tools

import (
    "context"
    "fmt"

    "github.com/mark3labs/mcp-go/mcp"
    "github.com/mark3labs/mcp-go/server"
)

// EchoTool implements a simple echo tool
type EchoTool struct{}

// NewEchoTool creates a new EchoTool instance
func NewEchoTool() *EchoTool {
    return &EchoTool{}
}

// Definition returns the tool definition for MCP
func (t *EchoTool) Definition() mcp.Tool {
    return mcp.Tool{
        Name:        "echo",
        Description: "Echo a message back to the user",
        InputSchema: mcp.ToolInputSchema{
            Type: "object",
            Properties: map[string]any{
                "message": map[string]any{
                    "type":        "string",
                    "description": "The message to echo back",
                },
            },
            Required: []string{"message"},
        },
    }
}

// Execute runs the echo tool
func (t *EchoTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    message, ok := args["message"].(string)
    if !ok {
        return nil, fmt.Errorf("message must be a string")
    }

    return &mcp.CallToolResult{
        Content: []any{
            mcp.TextContent{
                Type: "text",
                Text: fmt.Sprintf("Echo: %s", message),
            },
        },
    }, nil
}
```

**Key Components:**

- `Definition()` - Returns the MCP tool schema
- `Execute()` - Implements the tool logic
- `InputSchema` - Defines expected input parameters
- `CallToolResult` - Structured response format

## Creating Custom Tools

### Add Tool Boilerplate

```bash
kmcp add-tool weather --project-dir my-mcp-server
```

### Review Generated Code

```bash
cat my-mcp-server/tools/weather.go
```

### Implement Your Logic

**tools/weather.go:**

```go
package tools

import (
    "context"
    "encoding/json"
    "fmt"
    "net/http"
    "os"

    "github.com/mark3labs/mcp-go/mcp"
)

// WeatherTool fetches weather data
type WeatherTool struct {
    client *http.Client
    apiKey string
}

// NewWeatherTool creates a new WeatherTool
func NewWeatherTool() *WeatherTool {
    return &WeatherTool{
        client: &http.Client{},
        apiKey: os.Getenv("WEATHER_API_KEY"),
    }
}

// Definition returns the tool definition
func (t *WeatherTool) Definition() mcp.Tool {
    return mcp.Tool{
        Name:        "get_weather",
        Description: "Get current weather for a city",
        InputSchema: mcp.ToolInputSchema{
            Type: "object",
            Properties: map[string]any{
                "city": map[string]any{
                    "type":        "string",
                    "description": "The city name (e.g., 'San Francisco')",
                },
                "units": map[string]any{
                    "type":        "string",
                    "description": "Temperature units: 'celsius' or 'fahrenheit'",
                    "enum":        []string{"celsius", "fahrenheit"},
                    "default":     "celsius",
                },
            },
            Required: []string{"city"},
        },
    }
}

// Execute fetches weather data
func (t *WeatherTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    city, ok := args["city"].(string)
    if !ok {
        return nil, fmt.Errorf("city must be a string")
    }

    units := "celsius"
    if u, ok := args["units"].(string); ok {
        units = u
    }

    if t.apiKey == "" {
        return &mcp.CallToolResult{
            Content: []any{
                mcp.TextContent{
                    Type: "text",
                    Text: "Error: WEATHER_API_KEY not configured",
                },
            },
            IsError: true,
        }, nil
    }

    // Make API request
    url := fmt.Sprintf("https://api.weather.com/v1/current?city=%s&units=%s&key=%s",
        city, units, t.apiKey)

    resp, err := t.client.Get(url)
    if err != nil {
        return nil, fmt.Errorf("failed to fetch weather: %w", err)
    }
    defer resp.Body.Close()

    var data map[string]any
    if err := json.NewDecoder(resp.Body).Decode(&data); err != nil {
        return nil, fmt.Errorf("failed to parse response: %w", err)
    }

    result, _ := json.MarshalIndent(data, "", "  ")
    return &mcp.CallToolResult{
        Content: []any{
            mcp.TextContent{
                Type: "text",
                Text: string(result),
            },
        },
    }, nil
}
```

### Register the Tool

**tools/all_tools.go:**

```go
package tools

import (
    "github.com/mark3labs/mcp-go/server"
)

// RegisterAllTools registers all available tools with the server
func RegisterAllTools(s *server.MCPServer) {
    // Register echo tool
    echoTool := NewEchoTool()
    s.AddTool(echoTool.Definition(), echoTool.Execute)

    // Register weather tool
    weatherTool := NewWeatherTool()
    s.AddTool(weatherTool.Definition(), weatherTool.Execute)
}
```

## Server Entry Point

**main.go:**

```go
package main

import (
    "log"
    "os"

    "github.com/mark3labs/mcp-go/server"
    "my-mcp-server/tools"
)

func main() {
    // Create MCP server
    s := server.NewMCPServer(
        "my-mcp-server",
        "1.0.0",
        server.WithLogging(),
    )

    // Register all tools
    tools.RegisterAllTools(s)

    // Start server
    if err := s.ServeStdio(); err != nil {
        log.Fatalf("Server error: %v", err)
    }
}
```

## Local Development

### Run the Server

```bash
kmcp run --project-dir my-mcp-server
```

### MCP Inspector Configuration

| Field | Value |
|-------|-------|
| **Transport Type** | STDIO |
| **Command** | `go` |
| **Arguments** | `run main.go` |
| **Proxy Session Token** | (from kmcp run output) |

### Run Without Inspector

```bash
kmcp run --project-dir my-mcp-server --no-inspector
```

### Run Directly with Go

```bash
cd my-mcp-server
go run main.go
```

## Working with Dependencies

### Add Dependencies

```bash
cd my-mcp-server
go get github.com/some/package
```

### Common Dependencies

```bash
# HTTP client
go get github.com/go-resty/resty/v2

# JSON handling
go get github.com/tidwall/gjson

# Environment variables
go get github.com/joho/godotenv

# Logging
go get go.uber.org/zap
```

### Update go.mod

```go
module my-mcp-server

go 1.23

require (
    github.com/mark3labs/mcp-go v0.10.0
    github.com/go-resty/resty/v2 v2.11.0
    go.uber.org/zap v1.27.0
)
```

## Testing

### Run Tests

```bash
cd my-mcp-server
go test ./...
```

### Test File Example

**tools/weather_test.go:**

```go
package tools

import (
    "context"
    "os"
    "testing"
)

func TestWeatherTool_NoAPIKey(t *testing.T) {
    os.Unsetenv("WEATHER_API_KEY")

    tool := NewWeatherTool()
    result, err := tool.Execute(context.Background(), map[string]any{
        "city": "San Francisco",
    })

    if err != nil {
        t.Fatalf("unexpected error: %v", err)
    }

    if !result.IsError {
        t.Error("expected error result when API key is missing")
    }
}

func TestWeatherTool_Definition(t *testing.T) {
    tool := NewWeatherTool()
    def := tool.Definition()

    if def.Name != "get_weather" {
        t.Errorf("expected name 'get_weather', got '%s'", def.Name)
    }

    if def.Description == "" {
        t.Error("description should not be empty")
    }
}
```

### Benchmarks

**tools/echo_test.go:**

```go
func BenchmarkEchoTool(b *testing.B) {
    tool := NewEchoTool()
    ctx := context.Background()
    args := map[string]any{"message": "test"}

    for i := 0; i < b.N; i++ {
        tool.Execute(ctx, args)
    }
}
```

Run benchmarks:

```bash
go test -bench=. ./tools/
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

### Custom Dockerfile

The generated Dockerfile uses multi-stage builds:

```dockerfile
# Build stage
FROM golang:1.23-alpine AS builder

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download

COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o server .

# Runtime stage
FROM alpine:3.19

RUN apk --no-cache add ca-certificates
WORKDIR /root/

COPY --from=builder /app/server .

CMD ["./server"]
```

## Deployment

### Quick Deploy

```bash
kmcp deploy --file my-mcp-server/kmcp.yaml --image my-mcp-server:latest
```

### Generated MCPServer Resource

```yaml
apiVersion: kagent.dev/v1alpha1
kind: MCPServer
metadata:
  name: my-mcp-server
spec:
  deployment:
    image: "my-mcp-server:latest"
    port: 3000
    cmd: "./server"
  transportType: "stdio"
```

## Performance Considerations

### Connection Pooling

```go
import "net/http"

var httpClient = &http.Client{
    Transport: &http.Transport{
        MaxIdleConns:        100,
        MaxIdleConnsPerHost: 10,
        IdleConnTimeout:     90 * time.Second,
    },
    Timeout: 30 * time.Second,
}
```

### Context Handling

Always respect context for cancellation:

```go
func (t *MyTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    select {
    case <-ctx.Done():
        return nil, ctx.Err()
    default:
    }

    // Long-running operation with context
    result, err := t.client.DoWithContext(ctx, request)
    if err != nil {
        return nil, err
    }

    return &mcp.CallToolResult{...}, nil
}
```

### Resource Cleanup

```go
type DatabaseTool struct {
    db *sql.DB
}

func (t *DatabaseTool) Close() error {
    return t.db.Close()
}

// In main.go
func main() {
    dbTool := NewDatabaseTool()
    defer dbTool.Close()

    // Register and run server
}
```

## Best Practices

### 1. Structured Logging

```go
import "go.uber.org/zap"

func NewWeatherTool() *WeatherTool {
    logger, _ := zap.NewProduction()
    return &WeatherTool{
        logger: logger,
    }
}

func (t *WeatherTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    t.logger.Info("fetching weather",
        zap.String("city", args["city"].(string)),
    )
    // ...
}
```

### 2. Input Validation

```go
func (t *MyTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    // Validate required fields
    name, ok := args["name"].(string)
    if !ok || name == "" {
        return &mcp.CallToolResult{
            Content: []any{mcp.TextContent{Type: "text", Text: "name is required"}},
            IsError: true,
        }, nil
    }

    // Validate numeric ranges
    count, ok := args["count"].(float64) // JSON numbers are float64
    if !ok || count < 1 || count > 100 {
        return &mcp.CallToolResult{
            Content: []any{mcp.TextContent{Type: "text", Text: "count must be 1-100"}},
            IsError: true,
        }, nil
    }

    // ...
}
```

### 3. Error Wrapping

```go
import "fmt"

func (t *MyTool) Execute(ctx context.Context, args map[string]any) (*mcp.CallToolResult, error) {
    result, err := t.fetchData(ctx, args)
    if err != nil {
        return nil, fmt.Errorf("failed to fetch data: %w", err)
    }
    // ...
}
```

## Next Steps

- [Adding Tools](./06-adding-tools.md) - More tool patterns
- [Secrets Management](./11-secrets-management.md) - Configure environment variables
- [Deploying Servers](./08-deploying-servers.md) - Production deployment
- [HTTP Transport](./10-http-transport.md) - HTTP-based servers
