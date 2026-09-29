---
name: home-automation
description: "Read before changing zigbee2mqtt secrets or config, Home Assistant networking or probes, matter-server or esphome under kubernetes/apps/base/home-automation/**. Covers the Zigbee2MQTT network key being the network identity (a wrong encoding re-forms the network), its env being written to disk on every start, and Home Assistant needing no MAC pin."
---

# Home automation: Zigbee2MQTT, Home Assistant, matter-server, ESPHome

## Tripwires

1. **The Zigbee2MQTT 1Password fields are the network identity.** Rotating
   `CONFIG_PAN_ID`, `CONFIG_EXT_PAN_ID`, or `CONFIG_NETWORK_KEY` re-forms the
   network and unpairs every device. Encodings are decimal, and array length
   is not validated. [zigbee2mqtt.md](references/zigbee2mqtt.md)
2. **Zigbee2MQTT writes those env vars into `configuration.yaml` on every
   start.** An empty env var is ignored, which is why a `SecretSynced` secret
   with empty fields used to leave the on-disk network alone. A non-empty
   wrong value is persisted. [zigbee2mqtt.md](references/zigbee2mqtt.md)
3. **Home Assistant has a static IP and no MAC pin.** A pinned MAC on the
   macvlan network races the dying pod's veth and the new pod never comes up.
   IoT devices key on `10.40.0.100`. [home-assistant.md](references/home-assistant.md)
4. **matter-server's claim env is `KOPIUR_CLAIM`.** A `VOLSYNC_CLAIM` default
   names a variable the overlay no longer sets. Skill `kopiur-backups` before
   any backup edit. ESPHome compiles in its own container; do not shrink that
   memory limit to the idle working set.

## Where things live

| What | Path |
|---|---|
| Zigbee2MQTT ExternalSecret | `kubernetes/apps/base/home-automation/zigbee2mqtt/app/externalsecret.yaml` |
| Home Assistant | `kubernetes/apps/base/home-automation/home-assistant/app/helmrelease.yaml` |
| matter-server | `kubernetes/apps/base/home-automation/matter-server/` |
| ESPHome | `kubernetes/apps/base/home-automation/esphome/` |
| MQTT broker those apps use | skill `databases`, EMQX |

## Procedures

- Network identity encodings and a safe change: [zigbee2mqtt.md](references/zigbee2mqtt.md).
- Home Assistant address and probes: [home-assistant.md](references/home-assistant.md).

## Verify

- After any Zigbee identity change, the new pod logs
  `Adapter network matches config` and `zigbee-herdsman started (resumed)`,
  and `configuration.yaml` matches the pre-change file when the values were
  unchanged. Do not print the key, PAN ID, or extended PAN.
- Home Assistant readiness is `GET /manifest.json` on the in-cluster Service
  port 8123, not the public HTTPRoute. Skill `observability` for why the
  public route can answer before the app does.
