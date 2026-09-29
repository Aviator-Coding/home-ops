# Tdarr mechanisms

## Scope is a library toggle

`librariesToNotProcess` is stored on the node, shown in the UI, and returned
by `GET /api/v2/get-nodes`. Both queue readers (`getQueuedFiles.js`,
`getStagedFiles.js`) apply it only when `auth` is true. `auth` is the licence
check in `Tdarr_Server` (`authStatus`), a POST of `tdarrKey` to tdarr.io, not
a login. `tdarrKey` is empty here, so the check is false and the list is
ignored.

`get-new-task` is a node poll. There is no node-side accept filter, and
`nodeTags` is inert for the same licence gate. Do not add either as a
substitute scope.

What does hold, with no licence check, is the library row in
`LibrarySettingsJSONDB`: `processLibrary`, `processTranscodes`,
`processHealthChecks`, and the per-hour `schedule`. Series (`j5g_Es7sD`) is
held by `processTranscodes: false`. Health checks and scanning stay on.
Movies AV1 is `gEUZf7Nx6` and `processTranscodes: true`. `guard_scope` in the
flow fails closed unless the file is that library and under `/media/Movies/`.

## The `function` key runs the stub

`customFunction` reads `args.inputs.code`. `lib.loadDefaultValues` fills a
missing `code` with the built-in example, which returns `outputNumber: 1`.
The job report prints the real source under `"function"` next to the stub
under `"code"`. Nothing reports an error. While the five guards in
`movies_av1_nvenc_v1` were in that state, every transcode replaced its source
with no size, duration, or HDR check.

The flow document is `FlowsJSONDB` docID `movies_av1_nvenc_v1`. Restore it
from `docs/tdarr/flow-movies_av1_nvenc_v1.after.json` with one `cruddb`
update. Never restore a `*.before.json`. `docs/tdarr/flow-nodes/*.js` are
review copies, byte-checked by `scripts/ci/tdarr-flow-nodes-test.py`. They
are not the restore payload. Commands are in `docs/tdarr/README.md`.

A structural check of the JSON: 38 nodes, 62 edges, and no `customFunction`
whose `inputsDB` still has a `function` key.

## `forceConform` deletes streams

`ffmpegCommandSetContainer` sets `stream.removed` for `mov_text`, `eia_608`,
`timed_id3`, and `data` when the target is mkv and `forceConform` is true.
On the parked 4K masters that is the subtitle set, destroyed in place.
`cont22`, `cont23`, and `cont24` must stay
`{"container": "mkv", "forceConform": "false"}`.

`sub22`/`sub23`/`sub24` (source `flow-nodes/subconform.js`) push
`['-c:{outputIndex}', 'srt']` onto each `mov_text` stream and mark `removed`
only for `data`/`bin_data` and zero-dimension `mjpeg` cover art.

## Encoder arguments and the 4K guard

`-preset medium`, `-global_quality`, and `-look_ahead` are `av1_qsv` options.
Passed to `libsvtav1` they fail ffmpeg at init. `cargs22`/`cargs23`/`cargs24`
(from `flow-nodes/cargs_template.js`) read the encoder already chosen on the
video stream and branch: `av1_qsv` keeps the QSV flags; `libsvtav1` uses
`-preset 8 -crf N`.

`transcodecpuWorkers` stays at least `"1"` so a VA-API failure degrades
instead of stopping the queue (PR #1443). A 4K `libsvtav1` encode peaks near
7100 MiB against the node's 4Gi limit and OOM-kills the whole container,
GPU worker included. The same `cargs*` nodes refuse `libsvtav1` above that
budget and send 4K to `av1_qsv`. Do not set the CPU worker count to 0, and
do not remove the guard to "let the CPU encode 4K".

`enc2X` stays `hardwareType: qsv`. `guard_home` stays `codec: av1` (already
AV1, skip).

## What a real job report must contain

| Guard | String only that node emits |
|---|---|
| `guard_scope` | `Scope guard: library=` |
| `sub2X` | `Stream conform: in v=` |
| `cargs2X` | `AV1 tuning: encoder="` |
| 4K CPU guard | `AV1 tuning: 4K CPU guard` |
| size / duration / HDR | `Size check:` / `Duration check:` / `HDR survival:` |
