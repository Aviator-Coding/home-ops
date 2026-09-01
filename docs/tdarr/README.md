# Tdarr: rebuild runbook and node sources

Almost none of Tdarr's behaviour is GitOps. The HelmRelease
(`kubernetes/apps/base/media/tdarr/app/helmrelease.yaml`) owns the container,
its `devic.es/b70-vaapi` GPU resource, its worker counts and its 4Gi memory
limit. **Everything that decides what gets transcoded and how lives in Tdarr's
own SQLite database on the `tdarr-config` PVC**, which Flux cannot see, cannot
restore and cannot validate.

A rebuild of that PVC silently reverts every row below to Tdarr defaults. Pods
stay green, the queue keeps moving, Gatus stays happy - and three guards are
simply gone. This directory exists so that state can be restored deliberately
instead of rediscovered the hard way, which is how each of them was found.

## The three guards, and what their absence costs

| # | Guard | Where it lives | If it is missing |
|---|---|---|---|
| 1 | **Series scope** - `processTranscodes: false` on the Series library | `LibrarySettingsJSONDB` row `j5g_Es7sD` | Tdarr transcodes the TV library. `librariesToNotProcess` on the node looks like the boundary and is a Pro-gated no-op, so nothing warns you. |
| 2 | **Encoder-argument fix** - `cargs22/23/24` emit tuning matched to the encoder actually chosen | `FlowsJSONDB` row `movies_av1_nvenc_v1` | Every CPU transcode fails at ffmpeg init (`Unable to parse option value "medium"`); 88/88 jobs failed this way from 2026-08-29. |
| 3 | **4K CPU guard** - the same `cargs2X` nodes refuse `libsvtav1` above a measured memory budget | same row | A 4K file on the CPU worker peaks at ~7,100 MiB RSS against a 4Gi limit and **OOM-kills the whole container** (exit 137), destroying whatever the GPU worker was doing too. Measured five times on 2026-08-31. |

Two more flow-level protections live in the same row and are equally
non-durable: `guard_scope` (fails closed unless the file is both library
`gEUZf7Nx6` and under `/media/Movies/`) and `sub22/23/24` (converts `mov_text`
to `srt` instead of deleting 1-46 subtitle tracks per file).

## Restore order

1. **Library scope first.** Until guard 1 is back, restoring the flow just makes
   a wider set of files eligible.
2. **Then the flow** (guards 2, 3, `guard_scope`, `sub2X`) in one `cruddb`
   update - they are all fields of the same document.
3. **Then verify behaviourally.** Never by reading the flow: see "Verify" below.

### 1. Library scope

```bash
kubectl -n media exec deploy/tdarr -c app -- python3 -c "
import sqlite3,json
c=sqlite3.connect('file:/app/server/Tdarr/DB2/SQL/database.db?mode=ro',uri=True)
for r in c.execute('select json_data from librarysettingsjsondb'):
    d=json.loads(r[0]); print(d['_id'], d['name'], 'processTranscodes=', d['processTranscodes'])"
```

Expected: `j5g_Es7sD Series processTranscodes= False` and
`gEUZf7Nx6 Movies AV1 processTranscodes= True`. Health checks and scanning stay
**on** for Series; only transcoding is refused. Fix it in the library's own
options panel, or with a `cruddb` update on `LibrarySettingsJSONDB`.

### 2. The flow

`flow-movies_av1_nvenc_v1.after.json` is the **single restore authority**. Never
restore from `*.before.json` - that is the broken pre-#1530 state, kept only as
evidence.

```bash
python3 - <<'EOF' > /tmp/flow.json
import json
d=json.load(open('docs/tdarr/flow-movies_av1_nvenc_v1.after.json'))
json.dump({"data":{"collection":"FlowsJSONDB","mode":"update",
                   "docID":"movies_av1_nvenc_v1","obj":d}}, open('/tmp/flow.json','w'))
EOF
kubectl -n media cp /tmp/flow.json media/$(kubectl -n media get pod -l app.kubernetes.io/name=tdarr -o name | head -1 | cut -d/ -f2):/tmp/flow.json -c app
kubectl -n media exec deploy/tdarr -c app -- \
  curl -s -X POST http://127.0.0.1:8266/api/v2/cruddb \
  -H 'Content-Type: application/json' --data-binary @/tmp/flow.json
```

## Node sources

`flow-nodes/*.js` are readable, reviewable copies of what is embedded in the
flow document. They are **not** restore inputs - restore from the JSON - but
every one is byte-checked against it by CI, so they cannot drift silently.

| Node(s) | Source file | Role |
|---|---|---|
| `guard_scope` | `flow-nodes/guard_scope.js` | Library + path scope, fails closed |
| `dv_check` | `flow-nodes/dv_check.js` | Dolby Vision detection |
| `snapshot` | `flow-nodes/snapshot.js` | Records pre-transcode state for the guards |
| `sub22` `sub23` `sub24` | `flow-nodes/subconform.js` | `mov_text` -> `srt`, drop only unmuxable/0x0 streams |
| `cargs22` `cargs23` `cargs24` | `flow-nodes/cargs_template.js` | Encoder-aware tuning **and the 4K CPU guard** |
| `size_check` | `flow-nodes/size_check.js` | Guard 1: output must be materially smaller |
| `duration_check` | `flow-nodes/duration_check.js` | Guard 2: duration must match |
| `hdr_survival` | `flow-nodes/hdr_survival.js` | Guard 3: HDR must survive |

The three `cargs2X` bodies are generated from `cargs_template.js` by
substituting `__LABEL__` / `__QUALITY__` / `__HDR__`:

| Node | `__LABEL__` | `__QUALITY__` | `__HDR__` |
|---|---|---|---|
| `cargs22` | cq24 HDR | 24 | true |
| `cargs23` | cq26 HDR | 26 | true |
| `cargs24` | cq28 SDR | 28 | false |

The three `sub2X` bodies are byte-identical to `subconform.js`.

### Non-customFunction nodes

These carry no code, only settings, and a rebuild resets them to plugin
defaults. The load-bearing ones are `cont2X` (`forceConform` **must** stay
`false` - `true` deletes subtitle tracks), `enc2X` (`hardwareType: qsv`), and
`guard_home` (`codec: av1`, i.e. "already AV1, skip").

| Node | Plugin | Settings |
|---|---|---|
| `br_4k` | `checkOverallBitrate` | `{"greaterThan": "12", "lessThan": "100000", "unit": "mbps"}` |
| `br_4k_sdr` | `checkOverallBitrate` | `{"greaterThan": "10", "lessThan": "100000", "unit": "mbps"}` |
| `br_hdr1080` | `checkOverallBitrate` | `{"greaterThan": "6", "lessThan": "100000", "unit": "mbps"}` |
| `br_sdr1080` | `checkOverallBitrate` | `{"greaterThan": "6", "lessThan": "100000", "unit": "mbps"}` |
| `cont22` | `ffmpegCommandSetContainer` | `{"container": "mkv", "forceConform": "false"}` |
| `cont23` | `ffmpegCommandSetContainer` | `{"container": "mkv", "forceConform": "false"}` |
| `cont24` | `ffmpegCommandSetContainer` | `{"container": "mkv", "forceConform": "false"}` |
| `enc22` | `ffmpegCommandSetVideoEncoder` | `{"ffmpegPreset": "slow", "ffmpegPresetEnabled": "false", "ffmpegQuality": "22", "ffmpegQu...` |
| `enc23` | `ffmpegCommandSetVideoEncoder` | `{"ffmpegPreset": "slow", "ffmpegPresetEnabled": "false", "ffmpegQuality": "23", "ffmpegQu...` |
| `enc24` | `ffmpegCommandSetVideoEncoder` | `{"ffmpegPreset": "slow", "ffmpegPresetEnabled": "false", "ffmpegQuality": "24", "ffmpegQu...` |
| `exec22` | `ffmpegCommandExecute` | (defaults) |
| `exec23` | `ffmpegCommandExecute` | (defaults) |
| `exec24` | `ffmpegCommandExecute` | (defaults) |
| `guard_home` | `checkVideoCodec` | `{"codec": "av1"}` |
| `hdr_1080` | `checkHdr` | (defaults) |
| `hdr_4k` | `checkHdr` | (defaults) |
| `input1` | `inputFile` | (defaults) |
| `is_av1` | `checkVideoCodec` | `{"codec": "av1"}` |
| `is_hevc` | `checkVideoCodec` | `{"codec": "hevc"}` |
| `is_remux` | `checkFileNameIncludes` | `{"terms": "Remux,REMUX,remux"}` |
| `replace` | `replaceOriginalFile` | (defaults) |
| `res_hevc` | `checkVideoResolution` | (defaults) |
| `res_legacy` | `checkVideoResolution` | (defaults) |
| `start22` | `ffmpegCommandStart` | (defaults) |
| `start23` | `ffmpegCommandStart` | (defaults) |
| `start24` | `ffmpegCommandStart` | (defaults) |

## Verify - behaviourally, never by reading

A `customFunction` whose key is wrong runs Tdarr's default stub and returns
`outputNumber: 1` unconditionally, while the job report still prints your
source. That is how three guards sat dead across 6,514 job reports. So confirm
each guard by a string only it can emit, in a real job report:

| Guard | Grep a job report for |
|---|---|
| `guard_scope` | `Scope guard: library=` |
| `sub2X` | `Stream conform: in v=` |
| `cargs2X` encoder-aware | `AV1 tuning: encoder="` |
| 4K CPU guard | `AV1 tuning: 4K CPU guard` |
| `size_check` / `duration_check` / `hdr_survival` | `Size check:` / `Duration check:` / `HDR survival:` |

Structural check that the document itself is intact (38 nodes, 62 edges, and no
`customFunction` left on the dead `inputsDB.function` key):

```bash
python3 - <<'EOF'
import json; d=json.load(open('docs/tdarr/flow-movies_av1_nvenc_v1.after.json'))
cf=[n for n in d['flowPlugins'] if n['pluginName']=='customFunction']
print(len(d['flowPlugins']), 'nodes', len(d['flowEdges']), 'edges', len(cf), 'customFunction')
print('still on dead key:', [n['id'] for n in cf if 'function' in (n.get('inputsDB') or {})])
EOF
```

The offline half of all of this is pinned by
`scripts/ci/tdarr-flow-nodes-test.py`, which executes the node sources and the
bodies embedded in the flow artifact. It cannot check the live database.

For that pin to mean anything, CI has to actually run it on a change to these
files. `.github/workflows/validate.yaml` filters at **two** levels and the
trigger wins: from PR #1530 until 2026-09-01 its `pythontests` per-job filter
listed `docs/tdarr/**` while `on.pull_request.paths` did not, so a
`docs/tdarr`-only PR started no workflow at all and this byte-check never ran -
a dead filter pattern that read as coverage. Both levels now list it, and
`scripts/ci/validate-contention-test.py::test_trigger_paths_cover_job_filters`
fails if any per-job filter pattern is ever unreachable from the trigger paths
again.

## Also not GitOps

`node-config.json` is a reference snapshot of the node's own options; the node
config was not changed by this work. `librariesToNotProcess` is still set on the
node and is inert - do not add to it and do not trust it.
