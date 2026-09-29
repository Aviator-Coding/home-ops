# Zigbee2MQTT network identity

Item `zigbee2mqtt` in the Homelab vault (Connect can read Homelab; it cannot
read Home-Lab). Skill `secrets-1password`.

| 1Password field | Secret key | Env |
|---|---|---|
| `CONFIG_PAN_ID` | `zigbee_pan_id` | `ZIGBEE2MQTT_CONFIG_ADVANCED_PAN_ID` |
| `CONFIG_EXT_PAN_ID` | `zigbee_ext_pan_id` | `ZIGBEE2MQTT_CONFIG_ADVANCED_EXT_PAN_ID` |
| `CONFIG_NETWORK_KEY` | `zigbee_network_key` | `ZIGBEE2MQTT_CONFIG_ADVANCED_NETWORK_KEY` |

The ExternalSecret refresh is 5m. The Deployment has
`reloader.stakater.com/auto: "true"`, so a secret change rolls the pod.
Do not edit the Reloader annotation as part of an identity change.

## Encodings (image 2.13.0)

`applyEnvironmentVariables()` runs from `write()` and
`writeMinimalDefaults()`, and startup calls `settings.write()`. The env
value is written into `/data/configuration.yaml` and then re-read. Empty
env is ignored (`if (envVariable)`).

All three values go through `JSON.parse`:

- PAN ID: a decimal integer (`6754`). A hex string such as `0x1a62` is not
  valid JSON, stays a string, and fails
  `advanced.pan_id: should be number or 'GENERATE'`.
- Extended PAN ID: a JSON array of 8 decimal bytes, each 0..255.
- Network key: a JSON array of 16 decimal bytes, each 0..255.

`validate()` does not check array length. A 3-element key passes validation
and silently changes the network. Derive a replacement with the image's own
`js-yaml` inside the pod, assert the ranges, and prove it with
`getPersistedSettings` / `validate` / `write` / re-read on a `/dev/shm` copy
before writing 1Password. Never paste the values into a commit, a log, or
a chat.

A correct rewrite of the live values leaves `configuration.yaml`
byte-identical and advances only `network_key.frame_counter` in
`coordinator_backup.json`.

## Changing the network

Editing the 1Password item is a network change, not a secret refresh.
Back up `configuration.yaml`, `coordinator_backup.json`, `database.db`, and
`state.json` from the pod first, outside any git repo, mode `0700`. Prove
the candidate against a copy. Then write the item, force-sync the
ExternalSecret, and let Reloader roll once.

The network had no paired end devices when the fields were first populated,
so "devices still report" could only be checked as "the registry is
unchanged". If devices are paired now, a bad write unpairs them. Treat any
edit as that risk.
