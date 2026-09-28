---
name: home-automation
description: "Read before changing zigbee2mqtt secrets or config, Home Assistant networking or probes, matter-server or esphome under kubernetes/apps/base/home-automation/**. Covers the Zigbee2MQTT network key being the network identity (a wrong encoding re-forms the network), its env being written to disk on every start, and Home Assistant needing no MAC pin."
---

# Home automation: Zigbee2MQTT, Home Assistant and friends

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`docs/home-automation/zigbee2mqtt-network-key-pinning-2026-09-25.md`](../../../docs/home-automation/zigbee2mqtt-network-key-pinning-2026-09-25.md) - Zigbee2MQTT network key pinning
- [`kubernetes/apps/base/home-automation/home-assistant/app/helmrelease.yaml`](../../../kubernetes/apps/base/home-automation/home-assistant/app/helmrelease.yaml) - Home Assistant probes

`AGENTS.md` entries (search for the opening words):

- 1Password vaults: `Home-Lab` and `Homelab` are two DIFFERENT vaults

## Related skills

- `secrets-1password`
- `app-workloads`
