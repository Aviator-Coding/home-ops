# Hermes on Discord

One bot, one server, one dedicated channel, one allowed user. The default
profile (the homelab operator) answers there for now; the channel is meant to
move to a `home-assistant` profile later.

| What | Value |
|---|---|
| Server (guild) id | `1507870874752127226` |
| Dedicated channel id | `1564049687051370687` (`discord.allowed_channels` and `free_response_channels` in `config.yaml`) |
| Bot token, allowed users | 1Password item `hermes`: `DISCORD_BOT_TOKEN`, `DISCORD_ALLOWED_USERS` (numeric user ids, comma separated) |
| Tools | `platform_toolsets.discord: [hermes-discord]` (same shape as Telegram) |

Ids are not secrets. The token is on Hermes' terminal-env scrub list
(`tools/environments/local_env_policy.py`), like the Telegram one.

## Gating, in layers

1. `discord.allowed_channels` is a whitelist: the bot ignores every other channel.
2. `DISCORD_ALLOWED_USERS` limits who it obeys. A token without it is an open bot.
3. The channel answers without an @mention (`free_response_channels`); elsewhere an
   @mention would be required, but layer 1 already drops those messages.

`scripts/ci/hermes-discord-test.py` pins all of it.

## Setup traps

- **Invite the bot.** Creating the application does not put it in a server. Symptom
  from the Discord API: `GET /guilds/<id>` is 404 `Unknown Guild`, the channel is 403
  `Missing Access`, and `GET /users/@me/guilds` is empty. Invite URL (application id
  `1564037711340380250`, permissions View Channels, Send Messages, Read Message History,
  Add Reactions, Embed Links, Attach Files = `117824`):
  `https://discord.com/oauth2/authorize?client_id=1564037711340380250&scope=bot&permissions=117824`
- **Message Content Intent** must be on (Bot tab). "Limited" is the normal state for a
  bot that is not verified; it is enough here.
- **Turn off "Public Bot"** (Bot tab) after inviting, so only the owner can add it elsewhere.
- Two profiles cannot share one bot token (the duplicate adapter is parked), so a second
  profile is reached by a route, not a second login.

## Moving the channel to another profile

Add under `gateway:` in `config.yaml`, with the profile seeded from git
(`app/profiles/<name>/config.yaml`; the CI test refuses a route to a volume-only profile):

```yaml
gateway:
  profile_routes:
    - name: home-assistant
      platform: discord
      profile: home-assistant
      guild_id: "1507870874752127226"
      chat_id: "1564049687051370687"
```

Ids must be quoted strings. A channel route also matches threads under it.

## Verify

- `grep -i discord /opt/data/logs/gateway.log`: expect `discord connected`.
- Post in the channel as an allowed user; the reply should arrive without an @mention.
- A message from anyone else, or in any other channel, must get no reply.
