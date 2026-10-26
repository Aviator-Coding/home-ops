#!/usr/bin/env python3
"""Hermes' Discord bot answers one dedicated channel, for allow-listed users, and nothing else.

The bot is a door into an agent that can run commands, so it must never be open to a whole server. This gate
pins the layers (skill hermes-agent, references/discord.md):

  1. CHANNEL: config.yaml ``discord.allowed_channels`` is a non-empty whitelist of snowflake ids (17-20
     digits), and ``free_response_channels`` (answered without an @mention) is a subset of it. A
     free-response channel outside the whitelist would be dead config that looks like it works.
  2. USERS: wherever DISCORD_BOT_TOKEN reaches Hermes (ExternalSecret, file mount, secrets.command),
     DISCORD_ALLOWED_USERS travels with it. A token without an allow-list is an open bot.
  3. TOOLS: the Discord surface has an explicit ``platform_toolsets`` entry (durable, not a default).
  4. ROUTES: every ``gateway.profile_routes`` entry uses quoted string ids (PyYAML reads bare ids as ints
     and a float can never match) and targets a profile that is seeded from git, so a route can never point
     at a profile that exists only on the volume (that is how ``tia-portal`` broke).

Run: python3 scripts/ci/hermes-discord-test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/base/ai/hermes/app"
SNOWFLAKE = re.compile(r"^\d{17,20}$")
MOUNT_DIR = "/secrets/mcp/"
GATED = ("DISCORD_BOT_TOKEN", "DISCORD_ALLOWED_USERS")


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def csv_ids(value) -> list[str]:
    return [p.strip() for p in str(value or "").split(",") if p.strip()]


def test_channel_whitelist(config: dict) -> None:
    discord = config.get("discord") or {}
    allowed = csv_ids(discord.get("allowed_channels"))
    require(allowed, "discord.allowed_channels must list the dedicated channel(s): empty means every channel")
    bad = [c for c in allowed if not SNOWFLAKE.match(c)]
    require(not bad, f"allowed_channels entries are not snowflake ids: {bad}")
    require(isinstance(discord.get("allowed_channels"), str), "allowed_channels must be a quoted string")
    free = csv_ids(discord.get("free_response_channels"))
    require(set(free) <= set(allowed),
            f"free_response_channels {sorted(set(free) - set(allowed))} are outside allowed_channels")


def test_token_travels_with_allow_list(config: dict) -> None:
    command = config["secrets"]["command"]["command"]
    for name in GATED:
        require(name in command, f"secrets.command must emit {name}")
    hr = next(d for d in yaml.safe_load_all((APP / "helmrelease.yaml").read_text()) if d)
    mounts = hr["spec"]["values"]["persistence"]["mcp-gateway"]["advancedMounts"]["hermes"]["app"]
    mounted = {m["subPath"] for m in mounts}
    es = next(d for d in yaml.safe_load_all((APP / "externalsecret.yaml").read_text())
              if d and d["metadata"]["name"] == "hermes")
    rendered = set(es["spec"]["target"]["template"]["data"])
    for name in GATED:
        require(name in mounted, f"{name} must be mounted at {MOUNT_DIR}{name}")
        require(name in rendered, f"{name} must be rendered by the hermes ExternalSecret")


def test_platform_toolset(config: dict) -> None:
    toolsets = (config.get("platform_toolsets") or {}).get("discord")
    require(toolsets == ["hermes-discord"], f"platform_toolsets.discord must be [hermes-discord], got {toolsets!r}")


def test_routes(config: dict) -> None:
    routes = ((config.get("gateway") or {}).get("profile_routes")) or []
    for route in routes:
        for key in ("guild_id", "chat_id", "thread_id", "user_id"):
            if key in route:
                require(isinstance(route[key], str), f"route {route.get('name')}: {key} must be a quoted string")
        profile = route.get("profile")
        require(profile and (APP / "profiles" / profile / "config.yaml").is_file(),
                f"route {route.get('name')}: profile {profile!r} must be seeded from git "
                f"(app/profiles/{profile}/config.yaml), not exist only on the volume")


def main() -> int:
    config = yaml.safe_load((APP / "resources/config.yaml").read_text())
    for name, fn in (("channel whitelist", test_channel_whitelist),
                     ("token travels with allow-list", test_token_travels_with_allow_list),
                     ("platform toolset", test_platform_toolset), ("profile routes", test_routes)):
        print(f"==> {name}")
        fn(config)
    print("PASS: the Discord bot is limited to its channel and its allowed users, and routes target seeded profiles")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Failure, FileNotFoundError, KeyError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
