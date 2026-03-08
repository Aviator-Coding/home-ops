# OpenClaw

OpenClaw AI agent gateway running in the `ai` namespace with headless Chrome sidecar and code-server.

## Manual Setup Steps

### 1. 1Password Items

Create the following items in your 1Password vault. The ExternalSecrets will pull from these automatically.

**Item: `openclaw`**

| Field | Description |
|-------|-------------|
| `OPENCLAW_GATEWAY_PASSWORD` | Gateway authentication password |
| `OPENCLAW_GATEWAY_TOKEN` | Gateway API token |

**Item: `ai-keys`** (shared with other AI apps)

| Field | Description |
|-------|-------------|
| `ANTHROPIC_API_KEY_MAX` | Anthropic API key |
| `GEMINI_API_KEY` | Google Gemini API key ([Google AI Studio](https://aistudio.google.com)) |

**Item: `telegram`**

| Field | Description |
|-------|-------------|
| `OPENCLAW_TOKEN` | Telegram bot token for OpenClaw |

**Item: `travily`**

| Field | Description |
|-------|-------------|
| `TAVILY_API_KEY` | Tavily web search API key ([tavily.com](https://tavily.com)) |

**Item: `notion`**

| Field | Description |
|-------|-------------|
| `NOTION_API_KEY` | Notion integration token (starts with `ntn_` or `secret_`). Create at [notion.so/my-integrations](https://www.notion.so/my-integrations) |

**Item: `openclaw-skills`**

| Field | Description |
|-------|-------------|
| `BF_API_KEY` | Banana Farmer API key (free, self-provisioned at [bananafarmer.app](https://bananafarmer.app)) |

### 2. Notion Integration Setup

After creating the 1Password item, you must also share your Notion pages/databases with the integration:

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations) and create an integration
2. Copy the API key into the `notion` 1Password item
3. In Notion, open each page/database you want accessible, click `...` > `Connect to` > select your integration name

### 3. Google Workspace (gog) OAuth Setup

The Google OAuth `client_secret.json` is automatically mounted from 1Password (item: `google`, field: `CLAWBOT_GOOGLE`).
The init container runs `gog auth credentials` automatically.

You only need to add your Google account once (interactive, run inside the pod):

```bash
kubectl -n ai exec -it deploy/openclaw -c openclaw -- bash
export PATH="/home/linuxbrew/.linuxbrew/bin:/home/node/.local/bin:/home/node/go/bin:/home/node/.bun/bin:$PATH"

# Add your Google account
gog auth add you@gmail.com --services gmail,calendar,drive,contacts,sheets,docs

# Verify
gog auth list
```

The OAuth tokens persist in `/home/node` which is backed by the PVC.

### 4. Himalaya Email Setup

If using the `himalaya` email CLI (installed via brew), configure it inside the pod:

```bash
kubectl -n ai exec -it deploy/openclaw -c openclaw -- bash
mkdir -p ~/.config/himalaya
# Create config file per himalaya docs
```

## Installed Skills (ClawHub)

These skills are auto-installed by the init container via `clawhub install`:

| Skill | Description | Secrets Required |
|-------|-------------|-----------------|
| [arun-8687/tavily-search](https://clawhub.ai/arun-8687/tavily-search) | AI-optimized web search via Tavily API | `TAVILY_API_KEY` |
| [JimLiuxinghai/find-skills](https://clawhub.ai/JimLiuxinghai/find-skills) | Discover and install new agent skills | None |
| [steipete/notion](https://clawhub.ai/steipete/notion) | Notion API for pages, databases, and blocks | `NOTION_API_KEY` |
| [TheSethRose/agent-browser](https://clawhub.ai/TheSethRose/agent-browser) | Headless browser automation CLI for agents | None |
| [steipete/nano-banana-pro](https://clawhub.ai/steipete/nano-banana-pro) | Image generation/editing with Gemini 3 Pro | `GEMINI_API_KEY` |
| [steipete/gog](https://clawhub.ai/steipete/gog) | Google Workspace CLI (Gmail, Calendar, Drive, Sheets, Docs) | OAuth (manual setup) |
| [maximeprades/auto-updater](https://clawhub.ai/maximeprades/auto-updater) | Auto-update OpenClaw and installed skills on a schedule | None |
| [YoavRez/openclaw-youtube-transcript](https://clawhub.ai/YoavRez/openclaw-youtube-transcript) | Extract YouTube video transcripts via yt-dlp | None |
| [steipete/frontend-design](https://clawhub.ai/steipete/frontend-design) | Create production-grade frontend interfaces with high design quality | None |
| [adamandjarvis/banana-farmer](https://clawhub.ai/adamandjarvis/banana-farmer) | Stock & crypto momentum scoring for 6,500+ assets | `BF_API_KEY` (free, self-provisioned) |

## Access

| Service | URL |
|---------|-----|
| OpenClaw Gateway | `https://openclaw.${SECRET_DOMAIN}` |
| Code Server | `https://openclaw-code.${SECRET_DOMAIN}` |

Both routes use the `envoy-internal` gateway (private access only).

## Containers

| Container | Purpose | Port |
|-----------|---------|------|
| `openclaw` | Main agent gateway | 18789 |
| `chrome` | Headless Chromium for browser automation | 9222 |
| `code-server` | VS Code in browser for config editing | 12321 |

## Storage

All persistent data is stored on a single PVC (`openclaw`) with subpaths:

- `openclaw` - Home directory (`/home/node`), skills, configs, OAuth tokens
- `linuxbrew` - Homebrew installation and packages

Tools are installed once by the init container and persist across restarts.
