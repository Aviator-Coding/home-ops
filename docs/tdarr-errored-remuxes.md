# Tdarr: safe transcode path

Operational record for the `media/tdarr` transcode path: what enforces scope,
what verifies an encode before it overwrites a master, and the state of the
seven 4K remux masters (six still parked; one canary processed 2026-08-31).

**Almost everything here is Tdarr server state (SQLite under
`/app/server/Tdarr/DB2/`), not GitOps.** It does not survive a rebuild of the
Tdarr PVC and is invisible to Flux. The only correct flow recovery source is
`docs/tdarr/flow-movies_av1_nvenc_v1.after.json` (post-fix state). The matching
`*.before.json` dumps are the pre-fix baseline retained for diffing only -
never restore from them: their customFunction nodes still use the dead
`inputsDB.function` key (Guards 1-3 never execute) and their cargs nodes are
QSV-only (every CPU job fails at ffmpeg init). Section 5's restore command is
the single authority.

Diagnosis this builds on: the 2026-08-31 scout report on the errored remuxes.

### How to read provenance

Two kinds of claim appear below. They decay differently, so each proof is
labelled **at the point it is made**:

- **One-time live observation** — operator-executed against the live cluster on
  2026-08-31. Not reproducible in CI. Nothing will re-fire if it stops being
  true; re-verify before relying on it.
- **Re-checked by CI** — exercised on every run from the Git-visible artifacts
  under `docs/tdarr/` (node sources, `flow-movies_av1_nvenc_v1.after.json`, and
  `docs/tdarr/flow-nodes/behavior-test.js`). `scripts/ci/tdarr-flow-nodes-test.py`
  is the validate.yaml `python-tests` entrypoint that executes that harness
  (path filter also covers `docs/tdarr/**`). A CI-verified claim either stays
  true or goes red on the next run, so it defends itself.

Where a live finding has a committed counterpart that CI re-checks, both labels
appear. That is the strongest position (the dead-guard finding is one of these:
observed live in the job reports, pinned going forward by the after-flow
artifact).

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

**Provenance: one-time live observation** — operator-executed against the live
cluster on 2026-08-31; not reproducible in CI. Live, on this server:

```console
$ curl -s -XPOST .../api/v2/auth-status -d '{"data":{"saU":false}}'
false
$ sqlite3 database.db "select json_extract(json_data,'$.tdarrKey') from settingsglobaljsondb"
                       # empty string
```

So the clause is never added, on either path. The licence-gating shape in the
bundled source above is what makes that empty key fatal; the live call only
confirms this install is unauthorised.

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

**Provenance: one-time live observation** — operator-executed against the live
cluster on 2026-08-31; not reproducible in CI. The whole before/after table1
proof below (Series present then refused, Movies positive control staying
present, refused file still `TranscodeDecisionMaker=Queued`, and the same file
accepted by table4 while refused by table1) is live-only.

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

**Provenance: one-time live observation** — operator-executed against the live
cluster on 2026-08-31; not reproducible in CI. The 6514-report sweep (0
executed-only guard signatures vs 1187 code-dump forms) and the `dv_check`
`outputNumber: 1` on a bt709 h264 file are live-only.

**Provenance: re-checked by CI** — the committed counterpart is that every
`customFunction` node in `docs/tdarr/flow-movies_av1_nvenc_v1.after.json` is
keyed `inputsDB.code` (and the before dump still carries `inputsDB.function`).
`docs/tdarr/flow-nodes/behavior-test.js` executes those after-flow bodies and
asserts they jobLog domain-specific output rather than the silent stub. Dead
guards were observed live; the after-flow artifact pins the repair going
forward.

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
successful encode, with zero verification.** Six of the seven masters are
`DV HDR10`, which is exactly what `dv_check` and `hdr_survival` were written
to protect.

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

**Provenance: re-checked by CI** — `docs/tdarr/flow-nodes/cargs_template.js` and
the embedded `cargs22/23/24` bodies in
`docs/tdarr/flow-movies_av1_nvenc_v1.after.json` are executed by
`docs/tdarr/flow-nodes/behavior-test.js`. The harness asserts `libsvtav1` emits
`-preset 8 -crf N`, `av1_qsv` keeps the QSV args, and an unrecognised encoder
fails closed (`outputNumber: 2`).

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

**Provenance: one-time live observation** — operator-executed against the live
cluster on 2026-08-31; not reproducible in CI. Both real rewrites (SPF-18 CPU
path and Destination Wedding GPU path) and the job-report lines quoted from
them are live-only. The encoder-argument branching those reports show is the
same behaviour CI re-checks from the committed node sources in §3.1.

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

**Provenance: one-time live observation** — operator-executed against the live
cluster on 2026-08-31; not reproducible in CI. The end-to-end refusal of the
`zzflowtest` file through the real Tdarr flow is live-only.

**Provenance: re-checked by CI** — `docs/tdarr/flow-nodes/guard_scope.js` (and
the matching after-flow body) is executed by
`docs/tdarr/flow-nodes/behavior-test.js`: in-scope Movies under `/media/Movies/`
continues (`outputNumber: 1`); Series library, Series path, both-wrong, and
empty input all refuse (`outputNumber: 2`).

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

**Provenance: one-time live observation** — operator-executed against the live
cluster on 2026-08-31; not reproducible in CI. The ffmpeg reproduction of
`Subtitle codec 94213 is not supported` and the end-to-end conform run through
the real Tdarr flow on the disposable `/temp` file are live-only.

**Provenance: re-checked by CI** — `docs/tdarr/flow-nodes/subconform.js` (and
the embedded `sub22/23/24` bodies) plus the after-flow `cont22/23/24`
`forceConform: "false"` pins are executed/asserted by
`docs/tdarr/flow-nodes/behavior-test.js`: synthetic 5 `mov_text` → 5 srt kept;
master-shaped fixtures preserve 14/2/1/19/46/37/35 subtitle tracks and all
audio; Amelie drops only its 7 zero-dimension mjpeg; forceConform stays off on
every Set Container rung.

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

**Provenance: re-checked by CI** — read-only against committed master-shaped
`ffProbeData` fixtures (same counts the unit harness and
`docs/tdarr/flow-nodes/behavior-test.js` assert). None of the seven was queued.

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

## 4. The seven masters: six parked, one canary

**Provenance: one-time live observation** — operator-executed against the live
cluster on 2026-08-31; not reproducible in CI. Size, mtime and inode for all
seven masters were checked on the live NFS media tree; that check cannot run in
CI.

**Six remain parked.** The seventh, `A House of Dynamite (2025)`, was processed
on 2026-08-31 as the captain's single canary - see 4.1 below. All six others
verified byte-intact at the end of that work (size + mtime + inode unchanged
against the baseline taken before it started). None of the six was queued,
retried or processed.

| GB | mtime | File | State |
|---|---|---|---|
| 66.08 | 2023-08-31 | The Silence of the Lambs (1991) | parked |
| 64.95 | 2024-04-28 | The Departed (2006) | parked |
| 52.70 | 2023-09-05 | Gladiator (2000) | parked |
| 33.33 | 2025-12-24 | Amelie (2001) | parked |
| 27.00 | 2025-12-12 | Wake Up Dead Man (2025) | parked |
| 21.74 | 2026-01-18 | The Rip (2026) | parked |
| 20.92 | 2026-06-05 | A House of Dynamite (2025) | **canary, transcoded 2026-08-31** |

### 4.1 The canary: `A House of Dynamite (2025)`

**Provenance: one-time live observation** - operator-executed against the live
cluster on 2026-08-31; not reproducible in CI.

Chosen as the smallest of the seven (20.92 GB → 7.18 GB), which bounds both
blast radius and time-to-signal. **It was never itself queued.** A
byte-identical copy was staged under `/media/Movies/zz-tdarr-canary/` (so
`guard_scope` would admit it). Tdarr ran the flow against that copy and its
decisions executed there - `guard_scope` passed, `sub23` logged
`Stream conform: in v=1 a=7 s=35 | mov_text->srt=35 droppedData=0 dropped0x0Art=0`,
and `cargs23` emitted the full 44-stream mapping (all in the job report) - but
**Tdarr did not complete the encode**. The CPU worker won the queue race and
`libsvtav1` at 4K OOM-killed the node at ffmpeg exec; that is exactly what
motivated the 4K guard (section 4b.1). The encode was finished **out of band**
in a scratch pod with a raised memory limit, running the flow's own emitted
ffmpeg command on the GPU rung (`av1_qsv` branch of `cargs23`), then verified,
then swapped onto the master path by hand. The master was replaced only after
that verification. The staging tree was deleted once the verified `.mkv` was
in place. That is what kept the master intact through five node OOM kills in
the same session - a direct contrast with `Johnny Mnemonic`, which a bulk
requeue rewrote in place on 2026-08-30 with no verification. **After** the
guard shipped, a 4K job offered to the CPU worker does complete inside Tdarr on
`av1_qsv` (observed on `zzGuard 4K d`).

Every stream survived:

| | Before | After |
|---|---|---|
| Container / size | `.mp4`, 22,462,708,376 B | `.mkv`, 7,710,589,817 B (**34.3%**) |
| Video | `hevc` 3840x2160 10-bit | `av1` 3840x2160 10-bit |
| Cover art | `mjpeg` 600x900 | `mjpeg` 600x900 (copied, not re-encoded) |
| Audio | 7x `eac3` | 7x `eac3`, same languages and channel counts |
| Subtitles | **35x `mov_text`** | **35x `subrip`** |
| Duration | 6904.256 s | 6904.256 s (exact) |
| Total streams | 44 | 44 |

The 35 subtitle languages come back in the identical order
(`eng eng spa spa fra fra por por ara cat ces dan deu ell eus fin glg hrv hun
ind ita jpn kor nob nld pol ron rus swe tha tur ukr vie zho zho`), and the first
English track's cues are byte-identical including timings, italics markup and
line breaks. Decode-verified at 5 s, 3450 s and 6835 s across video and all
seven audio streams with no errors.

**One material fidelity loss, and it is not a defect in the conform.** The
source carries a `DOVI configuration record`; the output does not. HDR10 is
fully preserved (10-bit `yuv420p10le`, `bt2020` / `smpte2084` / `bt2020nc`, plus
mastering-display and content-light metadata the source did not carry
explicitly), but the **Dolby Vision RPU layer is gone**. That loss is not a
subtitle-conform defect: it follows from the `e_dv_bypass` edge (section 3.6)
routing `dv_check`'s "IS DV" output into the encoder, combined with this canary
running on `av1_qsv`. Measured: **five of the six remaining masters carry a DV
RPU** (all profile 8), and the loss is **not** inherent to AV1 - it is specific
to `av1_qsv` (`libsvtav1 -dolbyvision true` keeps the RPU). Section 4.2 has the
per-title survey and the encoder comparison; read it before deciding about the
five.

The original master was **not** deleted. It is retained byte-intact (size and
mtime preserved) at:

```
/media/.tdarr-canary-rollback/A House of Dynamite (2025) {imdb-tt32376165} [NF][WEBDL-2160p][EAC3 Atmos 5.1][DV HDR10][h265]-BEN.mp4
```

so the Dolby Vision loss is reversible by moving that file back over the `.mkv`.
It sits outside `/media/Movies/` deliberately, so neither Tdarr's folder watcher
nor Plex/Radarr sees a second copy. Delete it only once the captain has accepted
the trade for good.

An eighth, `Johnny Mnemonic (1995)`, was rewritten in place on 2026-08-30 by a
bulk UI requeue (7.02 GB h264 -> 1.41 GB AV1). Lossy and irreversible.

**Never bulk-requeue.** Tdarr rewrites in place.

---

### 4.2 Dolby Vision: who is affected, and is the loss avoidable

**Provenance: one-time live observation** - operator-executed against the live
cluster on 2026-08-31; not reproducible in CI. Both the survey and the encoder
comparison were run against the real masters.

**Who is affected: five of the six remaining, not all seven.** Surveyed with
`ffprobe -show_streams` on the live files:

| Master | Dolby Vision |
|---|---|
| The Silence of the Lambs (1991) | **yes** - profile 8, `bl_compat_id` 1, `rpu_present` 1 |
| The Departed (2006) | **yes** - profile 8, `bl_compat_id` 1 |
| Gladiator (2000) | **yes** - profile 8, `bl_compat_id` 1 |
| Wake Up Dead Man (2025) | **yes** - profile 8, `bl_compat_id` 1 |
| The Rip (2026) | **yes** - profile 8, `bl_compat_id` 1 |
| Amelie (2001) | **no** - carries no HDR at all (1080p AVC) |

So the finding is **not** moot: it applies to five of the six. `Amelie` is
unaffected and can be judged purely on the subtitle result.

All five are **profile 8 with `bl_signal_compatibility_id: 1`**, which matters:
the base layer is itself valid HDR10, so losing the RPU degrades them to
correct HDR10 rather than breaking them. (Profile 5 would have no usable base
layer and losing the RPU there would be catastrophic. None of these are
profile 5.) What is lost is Dolby Vision's per-scene dynamic metadata.

**Is the loss inherent? No - it is specific to the `av1_qsv` encoder.** Measured
on a 10-second segment of the canary's own retained original, same source, two
encoders:

| Encoder | Result |
|---|---|
| `av1_qsv -preset medium` (what the canary actually ran) | **RPU dropped.** Output side data is `Content light level` + `Mastering display` only. `av1_qsv` exposes no `-dolbyvision` option at all. |
| `libsvtav1 -dolbyvision true` | **RPU preserved.** Output carries a `DOVI configuration record`, `dv_profile: 10` (the AV1 Dolby Vision profile), `rpu_present_flag: 1`, `bl_signal_compatibility_id: 1`, alongside the HDR10 metadata. |

`ffmpeg -h encoder=...` on the running node confirms the asymmetry directly:
`libsvtav1` and `libx265` both advertise
`-dolbyvision <boolean> ... Enable Dolby Vision RPU coding`; `av1_qsv` and
`hevc_qsv` do not. A `dovi_rpu` bitstream filter is also present.

**This is in direct tension with the 4K CPU guard in section 4b.1, and the
captain should see that before deciding about the five.** The only encoder on
this box that can carry the RPU is `libsvtav1`, and a 4K `libsvtav1` encode is
exactly the ~7,100 MiB job that OOM-kills the node at its 4Gi limit - which is
why the guard now routes every 4K job to `av1_qsv`. As things stand:

- the guard makes 4K transcoding **safe**, and
- it simultaneously makes DV preservation on 4K **impossible**.

Reconciling them needs one of: raise `tdarr-node` memory so 4K `libsvtav1` fits
(the option A rejected on 2026-08-31, now with a second reason in its favour);
add a DV-aware exception to the guard **and** the memory to back it; accept
HDR10-only output for the five; or leave the five parked. Nothing here has been
changed to force that choice - the guard as shipped keeps the node alive, which
was the decision actually taken.

---

## 4b. The CPU fallback cannot encode 4K at the node's 4Gi limit

**Provenance: one-time live observation** - operator-executed against the live
cluster on 2026-08-31; not reproducible in CI. Peak-RSS figures, the OOM kills
and the node restart counts are live-only.

`transcodecpuWorkers: 1` exists so a VA-API regression degrades instead of
causing a total transcoding outage (`kubernetes/apps/base/media/tdarr/app/helmrelease.yaml`,
PR #1443). On 4K that fallback is not a degraded path - it is a **node killer**.

Measured on the parked canary copy, same input, same flow-emitted command,
isolated in a scratch pod with 24Gi so nothing was competing:

| Encoder rung | ffmpeg peak RSS | fps | Fits the node's 4Gi limit? |
|---|---|---|---|
| CPU `libsvtav1 -preset 8 -crf 26` | **~7,100 MiB** | 43 | **No** - about 1.8x the entire container limit |
| GPU `av1_qsv -preset medium -global_quality 26 -look_ahead 1` | **~1,225 MiB** | 54 | Yes, with room to spare |

So a 4K file picked up by the **CPU** transcode worker OOM-kills the whole
`tdarr-tdarr-node` container (exit 137) about 90 s in, right after the flow
finishes building the ffmpeg command and execs it. Reproduced four times on
2026-08-31, including on a completely idle node (108 MiB baseline).

Three consequences worth knowing before touching the transcode path:

- **It is not confined to the file that triggered it.** The OOM kills the
  container, so it also destroys whatever the *GPU* worker was doing. The first
  kill in this series kills a GPU job that had reached 100%.
- **It is a pre-existing hazard, not something the canary introduced.** Both
  transcode workers poll the same queue, so ANY 4K file that the CPU worker
  happens to win is enough. Six of the seven masters are 4K, and ordinary
  library imports are too - `Scream (2022)` (4K HDR10+) was auto-queued by the
  folder watcher during this work and is exactly the same shape.
- **A node restart re-stages the killed jobs but never resumes them.** The rows
  sit in `stagedjsondb` with `status: processing` while no worker and no ffmpeg
  exist, and because every queue query is `LEFT JOIN stagedjsondb` +
  `stagedjsondb.id IS NULL`, those files are then invisible to the queue until
  the stale row is removed (`POST /api/v2/cruddb`, `mode: removeOne`,
  `collection: StagedJSONDB`). A crash therefore silently parks its own victims.

That is the canary sequence in full (same facts as §4.1, restated here for the
OOM cause): Tdarr **did** run the flow on the staged copy and its decisions
executed there (`guard_scope` passed, `sub23` logged
`Stream conform: in v=1 a=7 s=35 | mov_text->srt=35 droppedData=0 dropped0x0Art=0`,
`cargs23` emitted the full 44-stream mapping - all in the job report). Tdarr
**did not** complete the encode: the CPU worker won the queue race and
`libsvtav1` at 4K OOM-killed the node at ffmpeg exec, which is exactly what
motivated the 4K guard below. The encode was finished **out of band** in a
scratch pod with a raised memory limit, running the flow's own emitted ffmpeg
command on the GPU rung (`av1_qsv` branch of `cargs23`), then verified, then
swapped onto the master path by hand. The master was replaced only after that
verification. **After** the guard shipped, a 4K job offered to the CPU worker
does complete inside Tdarr on `av1_qsv` (observed on `zzGuard 4K d`). The GPU
rung is the one this file's path (`start23`/`enc23`, `hardwareType: qsv`) is
designed for and fits the current limit comfortably - the pre-guard problem was
purely that nothing stopped the CPU worker taking a 4K job.

### 4b.1 Resolution: option C, a flow guard inside `cargs22/23/24`

Captain's decision 2026-08-31: **option C**. Not A (spending ~6Gi on talos-3,
which also hosts the B70 and `vllm`, to enable a path slower than the GPU it
backs up is the wrong trade) and not B (`transcodecpuWorkers: 0` reverses
PR #1443's anti-outage rationale). C preserves both intents: 1080p keeps its CPU
fallback, 4K goes to the GPU.

The guard lives in the three `cargs2X` nodes, which are already the single place
that reads back the encoder `SetVideoEncoder` chose. When that encoder is
`libsvtav1` and the video's own pixel count implies more RSS than the container
can hold, it rewrites the encoder to `av1_qsv` before ffmpeg runs. The B70 is
mounted into this container unconditionally (`devic.es/b70-vaapi`), so the GPU
encoder is reachable from either worker.

It decides on **measured memory need derived from the video's pixel count** -
never filename, library or path - so 4K arriving by any route is covered:

| Source | Estimate at 856 MiB/Mpx | Budget 2800 MiB | Encoder used on a CPU worker |
|---|---|---|---|
| 1920x1080 | 1,775 MiB | under | `libsvtav1` (fallback preserved) |
| 2560x1440 | 3,155 MiB | over | `av1_qsv` |
| 3840x2160 | 7,100 MiB | over | `av1_qsv` |
| dimensions unknown | n/a | fails closed | `av1_qsv` |

**Two mechanisms were implemented first and PROVEN NOT TO WORK.** Both looked
correct, both were silently inert, and both are the same shape as the
`librariesToNotProcess` trap in section 1 - configuration that is stored,
accepted and rendered, but never consumed:

1. **`tagsRequeue` cannot hand the file to a GPU worker.** The node does set the
   staged status to `queued:requireGPU` (observed directly in `stagedjsondb`),
   and the `require*` routing in `getStagedFiles.js` is genuinely **not**
   Pro-gated - unlike `nodeTags`, it sits outside the `if (auth)` block, so only
   a `transcodegpu` worker can match it. But when the flow then *ends*, the job
   is finalised `Not required`, the staged row is deleted, and the file leaves
   the queue permanently. Measured three times, the last in isolation with
   health checks suppressed: `Queued` -> staged `queued:requireGPU` -> `Not
   required` within 24 s, and it never came back. A refusal that silently
   un-queues 4K is worse than the OOM it prevents.
2. **`args.workerType` mutated in an earlier `customFunction` does not reach
   `ffmpegCommandSetVideoEncoder`.** Flipping it to `transcodegpu` upstream is
   the obvious way to make `getEncoder` pick QSV, and the guard logged the flip
   - but the job still ran `-c:0 libsvtav1` and OOM-killed the node anyway
   (restart #5, 03:05:25Z). Each node is handed its own worker context.

### 4b.2 Proof that the guard holds

**Provenance: one-time live observation** - operator-executed against the live
cluster on 2026-08-31; not reproducible in CI.

A configured guard is not a guard. Three real jobs, same flow, same moment,
observed from the job reports:

```
zzGuard 4K d    worker=transcodecpu
  AV1 tuning: 4K CPU guard - pixels=8294400 estCpuRss=7100MiB budget=2800MiB
              -> libsvtav1 would OOM this container, encoding av1_qsv instead
  -c:0 av1_qsv                                            Transcode success

zzGuard 4K c    worker=transcodegpu
  -c:0 av1_qsv                                            Transcode success

zzGuard 1080p x worker=transcodecpu
  AV1 tuning: encoder="libsvtav1" quality=26 ...
  -c:0 libsvtav1                                          Transcode success
```

So the CPU rung was genuinely offered a 4K job and declined to encode it itself,
a GPU job still succeeded, and 1080p still ran on the CPU - the PR #1443
fallback survives. The node did **not** restart during these three jobs, against
five OOM kills earlier the same evening on the same 4K shape.

The offline half is pinned by `scripts/ci/tdarr-flow-nodes-test.py`, which
executes `flow-nodes/cargs_template.js` and the three embedded `cargs2X` bodies
and asserts the 1080p / 1440p / 4K / unknown-dimension outcomes above.

---

## 5. What lives only in Tdarr's database

None of the following is GitOps. It does not survive a rebuild of the
`tdarr-config` PVC and Flux cannot see or restore it. `docs/tdarr/` holds
recovery copies. Authoritative flow restore source on every flow row below is
`docs/tdarr/flow-movies_av1_nvenc_v1.after.json`; `docs/tdarr/flow-nodes/*.js`
are readable excerpts of the node bodies, not restore inputs. Never restore a
flow from `*.before.json`.

| Change | Where | Recovery copy |
|---|---|---|
| `processTranscodes: false` on Series | `LibrarySettingsJSONDB` `j5g_Es7sD` | this document, §1.3 |
| `guard_scope` node + `input1` rewire | `FlowsJSONDB` `movies_av1_nvenc_v1` | `docs/tdarr/flow-movies_av1_nvenc_v1.after.json` (readable excerpt: `flow-nodes/guard_scope.js`) |
| 5 nodes rekeyed `function` -> `code` | same flow | `docs/tdarr/flow-movies_av1_nvenc_v1.after.json` |
| `cargs22/23/24` encoder-aware **plus the 4K CPU guard** (section 4b.1) | same flow | `docs/tdarr/flow-movies_av1_nvenc_v1.after.json` (readable excerpt/template: `flow-nodes/cargs_template.js`) |
| `sub22/23/24` stream conform | same flow | `docs/tdarr/flow-movies_av1_nvenc_v1.after.json` (readable excerpt: `flow-nodes/subconform.js`) |

Rebuild checklist, restore commands and the behavioural verification that a
restored flow is actually running (rather than silently executing Tdarr's
default stub): [`tdarr/README.md`](./tdarr/README.md).

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

**Provenance: one-time live observation** — operator-executed against the live
cluster on 2026-08-31; not reproducible in CI. Library toggles, flow node/edge
counts, queue depths, error-table composition, and the final byte-intact check
of the six still-parked masters (plus the canary rollback copy) are live-only
Tdarr/NFS state.

**Provenance: re-checked by CI (flow half only)** — the after-flow artifact
still carries 12 `customFunction` nodes all keyed `inputsDB.code` and is the
single restore authority above; CI re-checks that contract, not the live queue
or error-table numbers.

```
libraries   j5g_Es7sD  Series      processTranscodes=False  processHealthChecks=True
            gEUZf7Nx6  Movies AV1  processTranscodes=True   processHealthChecks=True
flow        movies_av1_nvenc_v1   38 nodes / 62 edges
            12 customFunction nodes, 0 still on the dead "function" key
node        talos-3  unpaused  workerLimits {gpu 1, cpu 1, hc-cpu 1, hc-gpu 1}
queues      table1 (transcode) 0     table4 (health check) 0
error table 45  =  44 Movies + 1 Series   # after the two low-value verifications;
                                         # see §4.1 for the separate canary
```

The error table fell from 47 to 45 because the two low-value verification files
transcoded successfully. It will keep falling as the 40 files that were only
ever blocked by the CPU-argument bug are retried. **Six of the seven masters
remain parked** and were never queued; those six verified byte-identical (size,
mtime and inode unchanged) at the end of this work. The seventh,
`A House of Dynamite (2025)`, is the §4.1 canary: the live Movies path is the
verified AV1 output, and the untouched original is retained at
`/media/.tdarr-canary-rollback/`.

### Files this work rewrote

Two low-value, non-master files were transcoded as the Phase 2 verification the
work required. Tdarr rewrites in place, so both are now AV1 and the originals
are gone. Neither is one of the seven masters.

| File | Before | After | Path |
|---|---|---|---|
| SPF-18 (2017) | 4,238,088,154 B h264 | 1,504,164,956 B av1 (35.5%) | CPU / `libsvtav1` |
| Destination Wedding (2018) | 3,254,265,921 B h264 | 644,553,479 B av1 (19.8%) | GPU / `av1_qsv` |

Both kept every stream (SPF-18: 1 video, 1 audio, 5 subrip, unchanged) and both
passed the now-live size and duration guards before replacement.

Exactly one master was rewritten, under the §4.1 canary procedure (staged copy
first, replace only after verification): `A House of Dynamite (2025)`,
20.92 GB → 7.18 GB (22,462,708,376 B hevc mp4 → 7,710,589,817 B av1 mkv,
34.3%), all 44 streams retained. Original master **retained** (not deleted) at
`/media/.tdarr-canary-rollback/` pending a captain decision on the remaining
six. So `/media` was changed in both directions by this work: a staging copy
was created under `/media/Movies/zz-tdarr-canary/` and deleted again after
verification, and the retained original was created and still exists.

A synthetic 30-second clip was also built in `/temp/flowtest` to prove the
subtitle conversion and was deleted afterwards along with its temporary
library.
