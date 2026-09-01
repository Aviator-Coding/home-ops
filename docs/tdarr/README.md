# Tdarr: what is database-only, and how to rebuild it

Almost none of Tdarr's behaviour is GitOps. The HelmRelease
(`kubernetes/apps/base/media/tdarr/app/helmrelease.yaml`) owns the container,
its `devic.es/b70-vaapi` GPU resource, its worker counts and its 4Gi memory
limit. **Everything that decides what gets transcoded and how lives in Tdarr's
own SQLite database on the `tdarr-config` PVC**, which Flux cannot see and
cannot restore. This directory holds the recovery copies.

A rebuild of that PVC silently reverts every row below to Tdarr defaults. The
apps stay green, the queue keeps moving, and the guards are simply gone - so
treat this file as a prerequisite checklist for any `tdarr-config` restore.

## What does NOT survive a rebuild

| # | Change | Where it lives | Restore from |
|---|---|---|---|
| 1 | `processTranscodes: false` on the Series library (the only scope boundary that actually holds - `librariesToNotProcess` is a Pro-gated no-op) | `LibrarySettingsJSONDB` row `j5g_Es7sD` | [`../tdarr-errored-remuxes.md`](../tdarr-errored-remuxes.md) section 1.3 |
| 2 | `guard_scope` node + its `input1` rewire | `FlowsJSONDB` row `movies_av1_nvenc_v1` | `flow-movies_av1_nvenc_v1.after.json` (readable excerpt: `flow-nodes/guard_scope.js`) |
| 3 | 5 customFunction nodes rekeyed `inputsDB.function` -> `inputsDB.code` | same row | `flow-movies_av1_nvenc_v1.after.json` |
| 4 | `sub22/23/24` stream conform (`mov_text` -> `srt`, never drop) | same row | `flow-movies_av1_nvenc_v1.after.json` (excerpt: `flow-nodes/subconform.js`) |
| 5 | `cargs22/23/24` encoder-aware tuning **and the 4K CPU guard** | same row | `flow-movies_av1_nvenc_v1.after.json` (excerpt/template: `flow-nodes/cargs_template.js`) |

Row 5 is the one added on 2026-08-31. Without it a 4K file picked up by the CPU
transcode worker runs `libsvtav1`, which peaks at ~7,100 MiB RSS against the
container's 4Gi limit and **OOM-kills the whole node** (exit 137), destroying
whatever the GPU worker was doing at the same time. Measured five times.

## Authority and ordering

- `flow-movies_av1_nvenc_v1.after.json` is the **single restore authority** for
  the flow. Never restore from `*.before.json` - that is the broken pre-#1530
  state, kept only as evidence.
- `flow-nodes/*.js` are readable excerpts, **not** restore inputs. The three
  `cargs2X` bodies are generated from `flow-nodes/cargs_template.js` by
  substituting `__LABEL__` / `__QUALITY__` / `__HDR__`
  (`cargs22` = cq24 HDR, `cargs23` = cq26 HDR, `cargs24` = cq28 SDR); the CI
  gate `scripts/ci/tdarr-flow-nodes-test.py` executes both the template and the
  embedded bodies, so they cannot drift apart silently.
- `node-config.json` is a snapshot for reference only; the node config was not
  changed by this work.

## Restoring the flow

```bash
kubectl -n media exec deploy/tdarr -c app -- \
  curl -s -X POST http://127.0.0.1:8266/api/v2/cruddb \
  -H 'Content-Type: application/json' --data-binary @/tmp/flow.json
```

where `/tmp/flow.json` is

```json
{"data":{"collection":"FlowsJSONDB","mode":"update","docID":"movies_av1_nvenc_v1",
         "obj": <the entire object from flow-movies_av1_nvenc_v1.after.json> }}
```

Then verify, because a restored flow that silently does nothing is the failure
mode this whole directory exists to prevent:

```bash
# 38 nodes / 62 edges, and no customFunction still on the dead "function" key
python3 - <<'PY'
import json; d=json.load(open('docs/tdarr/flow-movies_av1_nvenc_v1.after.json'))
cf=[n for n in d['flowPlugins'] if n['pluginName']=='customFunction']
print(len(d['flowPlugins']), len(d['flowEdges']), len(cf),
      [n['id'] for n in cf if 'function' in (n.get('inputsDB') or {})])
PY
```

and confirm behaviourally, never by reading the flow - a `customFunction` whose
key is wrong runs Tdarr's default stub and returns `outputNumber: 1`
unconditionally while the job report still prints your source. Grep a real job
report for a string only the node can emit, e.g. `4K CPU guard` or
`Stream conform:`.

## Restoring the library scope

`processTranscodes: false` on `j5g_Es7sD` (Series). Check it with:

```bash
kubectl -n media exec deploy/tdarr -c app -- python3 -c "
import sqlite3,json
c=sqlite3.connect('file:/app/server/Tdarr/DB2/SQL/database.db?mode=ro',uri=True)
for r in c.execute('select json_data from librarysettingsjsondb'):
    d=json.loads(r[0]); print(d['_id'], d['name'], 'processTranscodes=', d['processTranscodes'])"
```
