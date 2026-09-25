# Pinning the zigbee2mqtt network identity from 1Password (2026-09-25)

`home-automation/zigbee2mqtt`'s ExternalSecret has always wired three 1Password fields on the
`Homelab/zigbee2mqtt` item into container env:

| 1Password field | Secret key | Container env |
|---|---|---|
| `CONFIG_PAN_ID` | `zigbee_pan_id` | `ZIGBEE2MQTT_CONFIG_ADVANCED_PAN_ID` |
| `CONFIG_EXT_PAN_ID` | `zigbee_ext_pan_id` | `ZIGBEE2MQTT_CONFIG_ADVANCED_EXT_PAN_ID` |
| `CONFIG_NETWORK_KEY` | `zigbee_network_key` | `ZIGBEE2MQTT_CONFIG_ADVANCED_NETWORK_KEY` |

All three fields were empty while the ExternalSecret reported `SecretSynced`
(`docs/monitoring/exporter-endpoint-repair-2026-09-20.md` section 3). The network identity lived
only in `/data/configuration.yaml` on the PVC, so a volume restored empty or recreated would have
let zigbee2mqtt form a new network. Captain decision (D1): populate 1Password from the live
configuration rather than drop the env wiring. Done 2026-09-25. The manifests were already
correct and did not change.

## How zigbee2mqtt 2.13.0 consumes these variables

Read from the running image's `dist/util/settings.js` and `dist/util/onboarding.js`. None of it is
guesswork.

- **Env vars are applied on write, not on read.** `applyEnvironmentVariables()` is called only
  from `write()` and `writeMinimalDefaults()`. At every start, `onboarding.js` calls
  `settings.write()` ("trigger initial writing of `ZIGBEE2MQTT_CONFIG_*` ENVs"). The env value
  is therefore **persisted into `configuration.yaml`** and then re-read. A wrong value does not
  stay in memory only: it is written to disk.
- **An empty env var is ignored** (`if (envVariable)`), which is why the app ran fine with the
  empty fields.
- **All three fields go through `JSON.parse`.** Their schemas are `oneOf` with no top-level
  `type`, so the loader treats them as `object` and parses the value as JSON (falling back to
  the raw string on a parse error). The required encodings are:
  - `CONFIG_PAN_ID`: a **decimal** integer, e.g. `6754`. A hex string such as `0x1a62` is not
    valid JSON, stays a string, and fails `validate()` with
    `advanced.pan_id: should be number or 'GENERATE'`.
  - `CONFIG_EXT_PAN_ID`: a JSON array of 8 **decimal** bytes, e.g. `[221,221,221,221,221,221,221,221]`.
  - `CONFIG_NETWORK_KEY`: a JSON array of 16 **decimal** bytes.
- **`validate()` does not check array length.** A 3-element network key passes validation, so
  a truncated or mistyped key raises no error and silently changes the network. That is why
  the values were derived by machine from the live file (below) and never copied by hand.

## What was done, and the evidence

1. **Backup first.** Before anything was written, `configuration.yaml`, `coordinator_backup.json`,
   `database.db` and `state.json` were copied from the running pod to a private mode-`0700`
   directory outside every git repo on the operator workstation.
2. **Values derived in the pod.** A `node` one-liner read `/data/configuration.yaml` with the
   image's own `js-yaml`, asserted the types and ranges (integer pan ID in 1..65534, 8 and 16
   integer bytes 0..255), and emitted `String(pan_id)` and `JSON.stringify(array)` straight to a
   `0600` file. No value was printed.
3. **Proven with zigbee2mqtt's own code before any write**, on `/dev/shm` copies of the config.
   The test followed the startup order: `getPersistedSettings()`, `validateNonRequired()`,
   `write()`, `reRead()`, `get()`, `validate()`:

   | Case | pan / ext / key equal to live | config file changed | validate errors |
   |---|---|---|---|
   | dummy network in config, empty env (control) | false / false / false | no | none |
   | dummy network in config, candidate env | **true / true / true** | yes (overridden) | none |
   | real config, candidate env | **true / true / true** | **no (byte-identical)** | none |
   | real config, hex pan `0xd179` (negative) | false / - / - | yes | pan_id error |
   | real config, 3-byte key (negative) | - / - / false | yes | **none** |

   The first two rows show the env alone reproduces the live network. The third shows the next
   real start is a no-op on disk. The two negative rows show the test can fail. The last one is
   also the silent-corruption case described above.
4. **1Password written through the in-cluster Connect API** (`PATCH /v1/vaults/{vault}/items/{item}`
   JSON-patching the three field values). The desktop-bound `op` CLI is not usable from an agent
   session. The GET read-back was byte-compared against the derived values: equal.
5. **ExternalSecret force-synced** (`force-sync` annotation). All three keys in
   `zigbee2mqtt-secret` byte-compared equal to the live config.
6. **One roll.** The controller carries `reloader.stakater.com/auto: "true"`, so Reloader rolled
   the pod once, seconds after the Secret changed. The value had to be proven correct before the
   1Password write, not after the sync. The new pod:
   - env vars byte-equal to the derived values;
   - `/data/configuration.yaml` byte-identical to the pre-change backup;
   - `[INIT TC] Adapter network matches config.` then `zigbee-herdsman started (resumed)`, the
     same lines as the previous pod's start, with no errors or warnings;
   - `coordinator_backup.json` identical in coordinator IEEE, PAN ID, extended PAN ID, channel,
     security level and network key, with only `network_key.frame_counter` advanced (monotonic,
     expected);
   - MQTT connected, frontend `200`.

## Worth knowing

- **The network had no paired end devices on 2026-09-25.** `database.db` held only the
  `Coordinator` entry, both before and after the roll, and the coordinator backup's `devices` list
  was empty. "Devices report in normally" could therefore only be checked as "the registry is
  unchanged".
- **Rotating or re-entering these fields is a network change.** Every start writes the env value
  into `configuration.yaml`. Editing the 1Password item therefore rolls the pod through Reloader
  within one ESO refresh (`5m`) and imposes the new value. Repeat steps 2-3 against any new value
  before saving it.
