# Tdarr: safe transcode path

Operational record for the `media/tdarr` transcode path: what enforces scope,
what verifies an encode before it overwrites a master, and the state of the
seven parked 4K remux masters.

**Almost everything here is Tdarr server state (SQLite under
`/app/server/Tdarr/DB2/`), not GitOps.** It does not survive a rebuild of the
Tdarr PVC and is invisible to Flux. `docs/tdarr/*.before.json` are recovery
copies of the flow and node config as they stood on 2026-08-31.

Diagnosis this builds on: the 2026-08-31 scout report on the errored remuxes.

---

## 1. Scope: `librariesToNotProcess` never worked here

**It is a Tdarr Pro feature and is inert on an unlicensed install.** It is
stored, shown in the UI and returned by the API, so it looks live. It has never
excluded anything on this server.

`talos-3` carried `librariesToNotProcess: {"j5g_Es7sD": true}` (Series) and
still transcoded `/media/TV-Shows/The Simpsons/.../S35E05` on 2026-08-31
11:35:30Z.

### 1.1 Mechanism (from the server's own bundled source, 2.86.01)

Both queue readers gate the exclusion on `auth`:

```js
// api/nodeRelay/fileQueues/getQueuedFiles.js
if (auth && librariesToNotProcess.length > 0) {
  wheres.push(`tdqx.${fileColumns.db} NOT IN (${ids})`);
}

// api/nodeRelay/fileQueues/getStagedFiles.js
return auth && librariesToNotProcess.length > 0 && (files = await filter(...)), files;
```

`auth` is `await authStatus(false)`, computed once per dispatch in
`getNextTask.js`. And `authStatus` is a **licence** check, not a login:

```js
// server/auth.js
let authorised = false;
const authUpdate = async () => {
  const { tdarrKey } = await getById('SettingsGlobalJSONDB', 'globalsettings');
  const r = await axios.post(`${tdarrioURL}/api/v2/verify-key`, { tdarrKey });
  if (r.status === 200 && r.data === true) authorised = true;
};
const authStatus = async (saU) =>
  (saU === true && authorised !== true && await authUpdate(), authorised);
```

`/api/v2/auth-status` describes itself verbatim as *"For checking Tdarr Pro
status"*. Called with `saU: false` - which is what `getNextTask` does - it
cannot even trigger verification; it returns the cached flag.

Live, on this server:

```console
$ curl -s -XPOST .../api/v2/auth-status -d '{"data":{"saU":false}}'
false
$ sqlite3 database.db "select json_extract(json_data,'$.tdarrKey') from settingsglobaljsondb"
                       # empty string
```

So the clause is never added, on either path.

### 1.2 The "server push" hypothesis is refuted

The scout report suggested the server *pushes* work to the node, bypassing a
node-side accept filter. It does not.

`api/v2/get-new-task.js` is a **node-initiated poll**. The log line
`Server relay sending job to Node relay: talos-3` is emitted on the response
path, immediately after `insert('StagedJSONDB', ...)`, as the server replies to
the node's own `requestNewItem`. There is no node-side accept filter at all -
`librariesToNotProcess` is evaluated server-side inside the queue query. The
failure is licence-gating, not push-bypass.

### 1.2b `nodeTags` is inert for the same reason - do not reach for it next

The obvious replacement boundary is node tags. It is not one. The tag filter in
`getStagedFiles.js` sits inside the **same** `if (auth)` block as
`librariesToNotProcess`:

```js
if (auth) {                                   // <- Tdarr Pro, false here
  const { nodeTags } = nodes[nodeID] || '';
  if (typeof nodeTags === 'string') { files = await asyncFilter(files, tagsMatch); }
}
```

and `getQueuedFiles.js` never references node tags at all, so they do not
constrain the queued path under any licence.

These three - `librariesToNotProcess`, `nodeTags`, and the per-node accept
behaviour they imply - all share one shape worth recognising: **configuration
that is stored, rendered in the UI, and returned by the API, but never
consumed.** Nothing reports an error, so the only way to tell is to read the
consumer or to observe a refusal. Scope belongs on the library, per §1.3.

### 1.3 What actually holds: the library-level toggles

`server/qb/qbUtils.js` -> `plugins/queueQueryFuncs.js` build the same
`NOT IN` clause with **no `auth` check anywhere**:

```js
const { disabledLibrariesFromSchedules, disabledLibraries,
        disabledTranscodeLibraries, disabledHealthCheckLibraries }
      = await getAllDisabledLibraries();
excluded.push(...disabledLibrariesFromSchedules, ...disabledLibraries);
if (type === 'transcode')   excluded.push(...disabledTranscodeLibraries);
if (type === 'healthcheck') excluded.push(...disabledHealthCheckLibraries);
if (excluded.length) wheres.push(`${fileColumns.db} NOT IN (...)`);
```

| Library field | Excludes from | Licence-gated? |
|---|---|---|
| `processLibrary: false` | transcodes **and** health checks | no |
| `processTranscodes: false` | transcodes only | no |
| `processHealthChecks: false` | health checks only | no |
| `schedule[i].checked: false` | both, that hour slot | no |

**Applied 2026-08-31: `processTranscodes: false` on Series (`j5g_Es7sD`).**
Health checks and folder scanning stay on; only transcoding is refused.

### 1.4 Proof that it holds

Node paused first (`nodePaused: true`), so no dispatch was possible during the
test. Both files set to `TranscodeDecisionMaker: Queued`. The probe is
`POST /api/v2/client/status-tables {"opts":{"table":"table1"}}`, which runs the
**same** `reqTableDataDirect` + `getAdditionalQueries({type:'transcode'})`
chain the node's `getQueuedFiles` uses.

```
BEFORE (processTranscodes: true)            table1 totalCount = 2
  [SERIES] .../The Simpsons/Season 35/S35E05 - Treehouse of Horror XXXIV
  [MOVIES] .../Everyone Is Lying to You for Money (2026)

AFTER  (processTranscodes: false)           table1 totalCount = 1
  [MOVIES] .../Everyone Is Lying to You for Money (2026)
```

Same query, same moment. The Movies file is the **positive control**: it proves
the query still works and the exclusion is library-specific, not a global
break.

This is a **refusal, not an absence**. The Series file was still genuinely
queued at the moment it was refused:

```console
$ cruddb getById FileJSONDB "<series file>"
  TranscodeDecisionMaker = "Queued"
  DB                     = "j5g_Es7sD"
```

And the boundary is precisely transcode-scoped - the *same file*, at the *same
moment*, is accepted by the health-check queue and refused by the transcode
queue:

```
table4 (health check) totalCount = 1   [SERIES] ...S35E05...   <- accepted
table1 (transcode)    totalCount = 1   [MOVIES] ...            <- Series refused
```

Both test files were restored to `Transcode error` / `HealthCheck: Success`
afterwards and both queues returned to 0.

`librariesToNotProcess` was **left in place** on the node. It is harmless, but
it is decorative - do not add to it and do not trust it.

---

## 2. The three "guards" in the flow have never run

`movies_av1_nvenc_v1` contains five `customFunction` nodes: `dv_check`,
`snapshot`, and Guards 1-3 (`size_check`, `duration_check`, `hdr_survival`).
Their JavaScript is written, stored, and shipped to the node on every job.
**None of it has ever executed.**

### 2.1 Root cause

The Community `customFunction` plugin reads its code from `args.inputs.code`:

```js
// FlowPlugins/CommunityFlowPlugins/tools/customFunction/1.0.0/index.js
inputs: [ { label: 'JS Code', name: 'code', type: 'string', defaultValue: "<stub>" } ]
...
args.inputs = lib.loadDefaultValues(args.inputs, details);
code = String(args.inputs.code);
```

Every node in this flow stores it under `inputsDB.**function**`. `code` is
therefore missing, `loadDefaultValues` substitutes Tdarr's **default stub**,
and the stub ends:

```js
return { outputFileObj: args.inputFileObj, outputNumber: 1, variables: args.variables };
```

so every one of the five nodes unconditionally returns **output 1**.

### 2.2 Evidence

Job reports dump the resolved inputs, and show both keys side by side - the
default in `code`, the real code stranded in `function`:

```
"code":     "\nmodule.exports = async (args) => {\n\n// see args object data here ...
"function": "// Node: \"Is Dolby Vision? (P5 + P7)\"\n// Place: AFTER guard_home ...
```

Behavioural confirmation, from the 2026-08-31 Series job: on a plain
`h264` / `bt709` file, `dv_check` returned `outputNumber: 1`. Its written code
returns `isDV ? 1 : 2`, so it should have returned 2. The stub returns 1.

Sweep of **all 6514 job reports** for strings only the real code can emit:

| Executed-only signature | reports |
|---|---|
| `Size check: <digit>` | **0** |
| `Duration check: <digit>` | **0** |
| `Snapshot: size=<digit>` | **0** |
| `DV detect: codec_tag="[` | **0** |
| (the same strings *as source text* in the inputs dump) | 1187 |

### 2.3 Consequence

| Node | Intended | Actual |
|---|---|---|
| `dv_check` | Dolby Vision dead-ends | always output 1; and edge `e_dv_bypass` routes output 1 -> `snapshot`, so DV files encode anyway |
| `snapshot` | capture source size/duration/HDR | never sets `variables.user.*` |
| `size_check` | reject <5% or >=95% of source | always "pass" |
| `duration_check` | reject >0.5% duration drift | always "pass" |
| `hdr_survival` | reject lost HDR side data | always "pass" |

**`Replace Original File` is therefore reached unconditionally after any
successful encode, with zero verification.** Six of the seven parked masters
are `DV HDR10`, which is exactly what `dv_check` and `hdr_survival` were
written to protect.

This contradicts section 4.1 of the scout report, which described the guards as
live. They are not, and no manifest test or CI gate in this repo can see it -
the flow is Tdarr database state.

---

## 3. The flow: what changed and what proves it

All four changes are Tdarr database state. `docs/tdarr/flow-*.json` hold the
before and after; `docs/tdarr/flow-nodes/*.js` hold the node sources.

| Node | Change |
|---|---|
| `guard_scope` (new, first node) | refuses anything not both library `gEUZf7Nx6` and `/media/Movies/`; fails closed, output 2 dead-ends |
| `dv_check`, `snapshot`, `size_check`, `duration_check`, `hdr_survival` | `inputsDB.function` -> `inputsDB.code`, so the written code actually runs |
| `cargs22/23/24` | `ffmpegCommandCustomArguments` -> encoder-aware `customFunction` |
| `sub22/23/24` (new) | `mov_text` -> `srt`; drop only `data`/`bin_data` and 0x0 mjpeg |

### 3.1 The CPU worker: encoder-aware arguments

The three `cargs` nodes appended QSV-only options unconditionally:

```
-color_primaries bt2020 -color_trc smpte2084 -colorspace bt2020nc -color_range tv \
-preset medium -global_quality 28 -look_ahead 1
```

On a CPU job `getEncoder` returns `libsvtav1` (`FlowHelpers/1.0.0/hardwareUtils.js`),
which needs a **numeric** preset and `-crf`, so ffmpeg died at encoder init:
`Unable to parse option value "medium"`. 88 of 88 CPU jobs failed from
2026-08-29.

They now read back the encoder `SetVideoEncoder` actually chose - it pushes
`['-c:{outputIndex}', <encoder>]` onto the video stream's `outputArgs` - and
emit tuning to match. An unrecognised encoder dead-ends rather than shipping
arguments it may reject.

| Encoder | Emitted |
|---|---|
| `av1_qsv` | `-preset medium -global_quality N -look_ahead 1` (unchanged) |
| `libsvtav1` | `-preset 8 -crf N` |

`-preset 8` rather than 6: this is the **fallback** worker, whose job is to keep
throughput during a GPU/VA-API outage. Tunable in `cargs*`.

### 3.2 Verified on a real transcode

Low-value, non-master file, CPU worker forced (`transcodegpu: 0`):

```
/media/Movies/SPF-18 (2017)/...[NF][WEBDL-1080p][AC3 5.1][x264]-SiGMA.mkv
  before  4,238,088,154 bytes  h264   1 video, 1 audio, 5 subrip
  after   1,504,164,956 bytes  av1    1 video, 1 audio, 5 subrip   (35.5%)
```

Job report, all of it executed code that had never run before:

```
input1 guard_scope guard_home dv_check snapshot is_hevc is_av1 res_legacy
start24 cont24 enc24 cargs24 exec24 size_check duration_check hdr_survival replace

Scope guard: library="gEUZf7Nx6" libOk=true pathOk=true -> IN SCOPE (continue)
DV detect: codec_tag="[0][0][0][0]" p5=false p7=false -> NOT DV (continue)
AV1 tuning: encoder="libsvtav1" quality=28 hdr=false args=[-preset 8 -crf 28]
Size check: 3.85GiB -> 1.37GiB (35.5%)
Duration check: 4529.3s -> 4529.3s (100.00%)
HDR survival: source was SDR - PASS
Transcode success
```

GPU path re-confirmed afterwards on a separate low-value file - the *same*
node, the *same* rung, correct alternate arguments:

```
AV1 tuning: encoder="av1_qsv" quality=28 hdr=false args=[-preset medium -global_quality 28 -look_ahead 1]
ffmpeg: -c:0 av1_qsv -c:1 copy      (338 fps)
```

### 3.3 guard_scope observed refusing a file

A file in a temporary library on `/temp` (never on the NAS) was queued with the
node running:

```
Found next plugin: input1
Found next plugin: guard_scope
Scope guard: library="zzflowtest" libOk=false pathOk=false -> OUT OF SCOPE (refused, flow ends here)
-> Not required
```

The flow stopped at the second node. `guard_home` and every encoder node were
never reached. That is the flow-layer counterpart to the queue-layer refusal in
section 1.4.

### 3.4 Subtitles: convert, never drop

`Set Container mkv` has a `forceConform` input. **Do not turn it on.** It does
not convert - it deletes (`ffmpegCommandSetContainer/1.0.0/index.js`):

```js
if (newContainer === 'mkv') {
  if (codecType === 'data' || ['mov_text','eia_608','timed_id3'].includes(codecName)) {
    stream.removed = true;
  }
}
```

On the seven masters that is 14, 2, 1, 46, 37 and 35 subtitle tracks destroyed
in an irreversible in-place rewrite, and it would not help Amelie at all.

`sub22/23/24` convert instead. Reproduced and fixed at ffmpeg level on a
synthetic file:

```console
# current behaviour - the masters' exact error
$ ffmpeg -i in.mp4 -map 0:2 -c:2 copy ... out.mkv
[matroska] Subtitle codec 94213 is not supported.
Could not write header (incorrect codec parameters ?): Function not implemented

# with conversion
$ ffmpeg -i in.mp4 -map 0:2 -c:2 srt ... out.mkv
  before: subtitles=5 audio=1 video=1   (mov_text)
  after : subtitles=5 audio=1 video=1   (subrip, eng/fre/ger/spa/ita, text intact)
```

Then end-to-end through the real Tdarr flow on that disposable file:

```
... cont24 sub24 enc24 cargs24 exec24 ...
Stream conform: in v=1 a=1 s=5 other=0 | mov_text->srt=5 droppedData=0 dropped0x0Art=0
ffmpeg: -c:0 libsvtav1 -c:1 copy -c:2 srt -c:3 srt -c:4 srt -c:5 srt -c:6 srt
```

### 3.5 What it would do to the seven - unit-proved, not run

`docs/tdarr/flow-nodes/unit-test-conform-against-masters.js` runs the node
against each master's **real** `ffProbeData`, read-only. None was queued.

| File | subtitles | audio | dropped |
|---|---|---|---|
| The Silence of the Lambs | 14 -> **14** (14 converted) | 1 -> 1 | 1 data/bin_data |
| The Departed | 2 -> **2** (2 converted) | 5 -> 5 | none |
| Gladiator | 1 -> **1** (1 converted) | 3 -> 3 | 1 data/bin_data |
| Amelie | 19 -> **19** (0 converted) | 3 -> 3 | 7 x mjpeg 0x0 (valid 1719x2023 kept) |
| Wake Up Dead Man | 46 -> **46** (46 converted) | 8 -> 8 | 1 data/bin_data |
| The Rip | 37 -> **37** (37 converted) | 11 -> 11 | 1 data/bin_data |
| A House of Dynamite | 35 -> **35** (35 converted) | 7 -> 7 | none |

Every subtitle and audio track survives on every one of the seven.

### 3.6 Still open - deliberately not changed

- **`dv_check`'s skip output is wired to continue.** Edge `e_dv_bypass` routes
  output 1 (IS DV) to `snapshot`, so Dolby Vision files still encode. That was
  harmless while the node always returned 1; now that it works, removing the
  edge would actually start skipping DV. Six of the seven masters are DV HDR10,
  so this is a real behaviour choice and is left to the captain.
- Same for the `br_*_bypass` edges, which route the below-threshold output of
  each bitrate check into the encoder anyway, making those checks inert.
- **There is still no stream-count guard.** Guards 1-3 check size, duration and
  HDR, not track counts.

## 4. The seven parked masters

All seven verified byte-intact on 2026-08-31 (size + mtime + inode). None was
queued, retried or processed by this work.

| GB | mtime | File |
|---|---|---|
| 66.08 | 2023-08-31 | The Silence of the Lambs (1991) |
| 64.95 | 2024-04-28 | The Departed (2006) |
| 52.70 | 2023-09-05 | Gladiator (2000) |
| 33.33 | 2025-12-24 | Amelie (2001) |
| 27.00 | 2025-12-12 | Wake Up Dead Man (2025) |
| 21.74 | 2026-01-18 | The Rip (2026) |
| 20.92 | 2026-06-05 | A House of Dynamite (2025) |

An eighth, `Johnny Mnemonic (1995)`, was rewritten in place on 2026-08-30 by a
bulk UI requeue (7.02 GB h264 -> 1.41 GB AV1). Lossy and irreversible.

**Never bulk-requeue.** Tdarr rewrites in place.

---

## 5. What lives only in Tdarr's database

None of the following is GitOps. It does not survive a rebuild of the
`tdarr-config` PVC and Flux cannot see or restore it. `docs/tdarr/` holds
recovery copies.

| Change | Where | Recovery copy |
|---|---|---|
| `processTranscodes: false` on Series | `LibrarySettingsJSONDB` `j5g_Es7sD` | this document, §1.3 |
| `guard_scope` node + `input1` rewire | `FlowsJSONDB` `movies_av1_nvenc_v1` | `docs/tdarr/flow-*.json`, `flow-nodes/guard_scope.js` |
| 5 nodes rekeyed `function` -> `code` | same flow | `docs/tdarr/flow-*.json` |
| `cargs22/23/24` encoder-aware | same flow | `flow-nodes/cargs_template.js` |
| `sub22/23/24` stream conform | same flow | `flow-nodes/subconform.js` |

Restore with `POST /api/v2/cruddb`:

```json
{"data":{"collection":"FlowsJSONDB","mode":"update","docID":"movies_av1_nvenc_v1",
         "obj":{"_id":"movies_av1_nvenc_v1","flowPlugins":[...],"flowEdges":[...]}}}
```

taking `flowPlugins` / `flowEdges` from
`docs/tdarr/flow-movies_av1_nvenc_v1.after.json`. `librariesToNotProcess` was
left on the node untouched; the node config itself was not changed
(`docs/tdarr/node-config.json` is a current snapshot, for reference only).

### Live state at the end of this work

```
libraries   j5g_Es7sD  Series      processTranscodes=False  processHealthChecks=True
            gEUZf7Nx6  Movies AV1  processTranscodes=True   processHealthChecks=True
flow        movies_av1_nvenc_v1   38 nodes / 62 edges
            12 customFunction nodes, 0 still on the dead "function" key
node        talos-3  unpaused  workerLimits {gpu 1, cpu 1, hc-cpu 1, hc-gpu 1}
queues      table1 (transcode) 0     table4 (health check) 0
error table 45  =  44 Movies + 1 Series
```

The error table fell from 47 to 45 because the two low-value verification files
transcoded successfully. It will keep falling as the 40 files that were only
ever blocked by the CPU-argument bug are retried. The 7 masters remain parked
and were never queued; all seven verified byte-identical (size, mtime and inode
unchanged) at the end of this work.

### Files this work rewrote

Two low-value, non-master files were transcoded as the Phase 2 verification the
work required. Tdarr rewrites in place, so both are now AV1 and the originals
are gone. Neither is one of the seven.

| File | Before | After | Path |
|---|---|---|---|
| SPF-18 (2017) | 4,238,088,154 B h264 | 1,504,164,956 B av1 (35.5%) | CPU / `libsvtav1` |
| Destination Wedding (2018) | 3,254,265,921 B h264 | 644,553,479 B av1 (19.8%) | GPU / `av1_qsv` |

Both kept every stream (SPF-18: 1 video, 1 audio, 5 subrip, unchanged) and both
passed the now-live size and duration guards before replacement.

A third file, a synthetic 30-second clip, was built in `/temp/flowtest` to prove
the subtitle conversion and was deleted afterwards along with its temporary
library. Nothing under `/media` was created or deleted by this work.
