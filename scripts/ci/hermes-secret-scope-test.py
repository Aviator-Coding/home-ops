#!/usr/bin/env python3
"""Hermes resolves secrets from the profile secret scope, never from the container env.

The gateway runs with ``gateway.multiplex_profiles``. A provider ``key_env``, an MCP ``${VAR}`` and
a skill's required env are all looked up with ``get_secret`` (``/opt/data/.env`` plus the
``secrets:`` sources), so env injected into the container is invisible to them. Two outages came
from that: every LLM call carried the ``no-key-required`` placeholder (401), and the commit-watcher
cron was blocked at preflight ("missing env") for four days. This gate keeps the wiring whole:

  1. every name the ``secrets.command`` loop reads has a file mounted at ``/secrets/mcp/<name>``,
     and every mounted name is a key the hermes ExternalSecret renders
  2. every provider ``key_env``, every ``${VAR}`` in ``mcp_servers`` and every non-optional
     ``required_environment_variables`` of a seeded skill is emitted by that command
  3. an emitted name a skill script reads via ``os.environ`` is declared in that skill (an
     undeclared var is not passed through to the script)
  4. the SUMMARY_LLM_URL/MODEL literals in the command match the container env (one value, two homes)
  5. ``auxiliary`` keys are real Hermes task slots (a misspelt slot is silently ignored: the call
     falls back to the main model)

Run: python3 scripts/ci/hermes-secret-scope-test.py
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "kubernetes/apps/base/ai/hermes/app"
MOUNT_DIR = "/secrets/mcp/"

# Hermes v0.21.5 DEFAULT_CONFIG["auxiliary"] slots, plus the two tasks it resolves without a default.
KNOWN_AUX_SLOTS = frozenset({
    "approval", "background_review", "compression", "curator", "goal_judge", "kanban_decomposer",
    "mcp", "memory_query_rewrite", "moa_aggregator", "moa_reference", "monitor", "profile_describer",
    "review", "skills_hub", "title_generation", "triage_specifier", "tts_audio_tags", "vision",
    "web_extract", "session_search",
})


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text())


def emitted_and_file_backed(command: str) -> tuple[set[str], set[str]]:
    loop = re.search(r"for k in ([A-Z0-9_ ]+); do", command)
    loop_names = set(loop.group(1).split()) if loop else set()
    # Lookbehind, not \b: in `...%s\nNAME=%s` the shell escape `\n` leaves a letter right before NAME.
    literal_names = set(re.findall(r"(?<![A-Z0-9_])([A-Z][A-Z0-9_]+)=%s", command))
    cat_names = set(re.findall(re.escape(MOUNT_DIR) + r"([A-Z][A-Z0-9_]+)", command))
    return loop_names | literal_names, loop_names | cat_names


def mounted_names(helmrelease: dict) -> set[str]:
    mounts = (helmrelease["spec"]["values"]["persistence"]["mcp-gateway"]
              ["advancedMounts"]["hermes"]["app"])
    names = set()
    for m in mounts:
        require(m["path"] == f"{MOUNT_DIR}{m['subPath']}",
                f"mount {m['path']} must be {MOUNT_DIR}<subPath> ({m['subPath']})")
        names.add(m["subPath"])
    return names


def secret_keys() -> set[str]:
    docs = [d for d in yaml.safe_load_all((APP / "externalsecret.yaml").read_text()) if d]
    es = next(d for d in docs if d["metadata"]["name"] == "hermes")
    return set(es["spec"]["target"]["template"]["data"])


def frontmatter(skill_md: Path) -> dict:
    return yaml.safe_load(skill_md.read_text().split("---")[1])


def skill_envs(skill_md: Path) -> tuple[set[str], set[str]]:
    declared = frontmatter(skill_md).get("required_environment_variables") or []
    required = {e["name"] for e in declared if not e.get("optional")}
    return required, {e["name"] for e in declared}


def test_command_files_are_mounted_and_rendered(command: str, hr: dict) -> None:
    _, file_backed = emitted_and_file_backed(command)
    mounted = mounted_names(hr)
    require(file_backed <= mounted,
            f"secrets.command reads files that helmrelease.yaml does not mount: {sorted(file_backed - mounted)}")
    missing = mounted - secret_keys()
    require(not missing, f"mounted keys the hermes ExternalSecret does not render: {sorted(missing)}")


def test_every_scope_consumer_is_emitted(config: dict, emitted: set[str]) -> None:
    key_envs = {p["key_env"] for p in config["providers"].values() if p.get("key_env")}
    require(key_envs <= emitted, f"provider key_env not in secrets.command: {sorted(key_envs - emitted)}")
    mcp_vars = set(re.findall(r"\$\{([A-Z][A-Z0-9_]+)\}", yaml.safe_dump(config.get("mcp_servers") or {})))
    require(mcp_vars <= emitted, f"mcp_servers ${{VAR}} not in secrets.command: {sorted(mcp_vars - emitted)}")
    for skill_md in sorted((APP / "skills").glob("*/SKILL.md")):
        required, _ = skill_envs(skill_md)
        require(required <= emitted,
                f"{skill_md.parent.name}: required env not in secrets.command "
                f"(cron preflight would block it): {sorted(required - emitted)}")


def test_script_env_is_declared(emitted: set[str]) -> None:
    for skill_md in sorted((APP / "skills").glob("*/SKILL.md")):
        _, declared = skill_envs(skill_md)
        read = set()
        for py in skill_md.parent.glob("*.py"):
            read |= set(re.findall(r"os\.environ\.get\(\s*\"([A-Z][A-Z0-9_]+)\"", py.read_text()))
        undeclared = (read & emitted) - declared
        require(not undeclared,
                f"{skill_md.parent.name}: scripts read {sorted(undeclared)} but SKILL.md does not "
                "declare them, so they are not passed through to the script")


def test_summary_literals_match_container_env(command: str, hr: dict) -> None:
    m = re.search(r"SUMMARY_LLM_URL=%s\\nSUMMARY_LLM_MODEL=%s\\n' (\S+) (\S+)", command)
    require(m is not None, "secrets.command must emit SUMMARY_LLM_URL and SUMMARY_LLM_MODEL literals")
    env = hr["spec"]["values"]["controllers"]["hermes"]["containers"]["app"]["env"]
    require(env.get("SUMMARY_LLM_URL") == m.group(1),
            f"SUMMARY_LLM_URL: container env {env.get('SUMMARY_LLM_URL')!r} != command {m.group(1)!r}")
    require(env.get("SUMMARY_LLM_MODEL") == m.group(2),
            f"SUMMARY_LLM_MODEL: container env {env.get('SUMMARY_LLM_MODEL')!r} != command {m.group(2)!r}")


def test_auxiliary_slots_are_real(config: dict) -> None:
    unknown = set(config.get("auxiliary") or {}) - KNOWN_AUX_SLOTS
    require(not unknown,
            f"unknown auxiliary slot(s) {sorted(unknown)}: Hermes ignores them and the call "
            "falls back to the main model (add to KNOWN_AUX_SLOTS only after checking Hermes)")


def test_github_credentials_are_read_only() -> None:
    """The agent's terminal sees HOMELAB_GH_TOKEN, and Hermes reaches the GitHub MCP. Neither may hold the
    write-capable field (it can create issues and commit files to home-ops; measured with invalid-body probes)."""
    docs = [d for d in yaml.safe_load_all((APP / "externalsecret.yaml").read_text()) if d]
    hermes = next(d for d in docs if d["metadata"]["name"] == "hermes")
    rendered = hermes["spec"]["target"]["template"]["data"]["HOMELAB_GH_TOKEN"]
    require("HOMELAB_GH_READ_TOKEN" in rendered and ".HOMELAB_GH_TOKEN" not in rendered,
            f"Hermes' HOMELAB_GH_TOKEN must render from HOMELAB_GH_READ_TOKEN, got {rendered!r}")
    mcp = ROOT / "kubernetes/apps/base/ai/toolhive/mcp-servers/github-mcp"
    token = yaml.safe_load((mcp / "externalsecret.yaml").read_text())["spec"]["target"]["template"]["data"]["token"]
    require("HOMELAB_GH_READ_TOKEN" in token and ".HOMELAB_GH_TOKEN" not in token,
            f"the GitHub MCP token must render from HOMELAB_GH_READ_TOKEN, got {token!r}")
    env = {e["name"]: e.get("value") for e in yaml.safe_load((mcp / "mcpserver.yaml").read_text())["spec"]["env"]}
    require(env.get("GITHUB_READ_ONLY") == "1", "the GitHub MCP must run with GITHUB_READ_ONLY=1")


def main() -> int:
    config = load_yaml(APP / "resources/config.yaml")
    hr = load_yaml(APP / "helmrelease.yaml")
    command = config["secrets"]["command"]["command"]
    emitted, _ = emitted_and_file_backed(command)
    for name, fn in (
        ("command files mounted and rendered", lambda: test_command_files_are_mounted_and_rendered(command, hr)),
        ("scope consumers emitted", lambda: test_every_scope_consumer_is_emitted(config, emitted)),
        ("script env declared", lambda: test_script_env_is_declared(emitted)),
        ("summary literals match env", lambda: test_summary_literals_match_container_env(command, hr)),
        ("auxiliary slots real", lambda: test_auxiliary_slots_are_real(config)),
        ("github credentials read-only", test_github_credentials_are_read_only),
    ):
        print(f"==> {name}")
        fn()
    print("PASS: every secret Hermes resolves from the profile scope is emitted, mounted and declared")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (Failure, FileNotFoundError, KeyError) as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        sys.exit(1)
