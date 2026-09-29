# Tdarr: safe transcode path

The flow mechanisms live in skill `tdarr-transcoding`. This file keeps the
proof labels the node harness checks, and the items still awaiting the captain.
Do not edit `docs/tdarr/flow-nodes/`.

## Still awaiting the captain

- Edge `e_dv_bypass` still encodes Dolby Vision. Removing it would start
  skipping DV files. Five parked masters are profile 8; Amelie has no HDR.
- Edges `br_*_bypass` route below-threshold bitrate outputs into the encoder,
  so those checks do nothing.
- There is no stream-count guard.
- `av1_qsv` drops the Dolby Vision RPU. `libsvtav1 -dolbyvision true` keeps it,
  and a 4K `libsvtav1` encode is what the 4Gi node guard refuses. Options
  left open: raise node memory, add a DV exception plus the memory to back
  it, accept HDR10-only output, or leave the five parked.
- Do not queue the parked masters. A bulk requeue rewrites the file in place.

### How to read provenance

Two labels, and they decay differently.

- **One-time live observation.** An operator ran this against the live
  cluster. It is not reproducible in CI. The cluster moves on, so this kind
  decays: a later change can make the observation false while the sentence
  stays.
- **Re-checked by CI.** The node harness executes the committed flow sources
  on every relevant PR. This kind decays only when the sources or the harness
  change, which the same CI run fails.

## 1. Scope

### 1.1 Mechanism

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

The hypothesis that the server pushes work past a node-side filter is
refuted. `get-new-task` is a node poll. `librariesToNotProcess` is applied
only when the licence check `auth` is true. `tdarrKey` is empty here, so the
list is ignored. What holds is `processTranscodes` on the library row.

### 1.4 Proof that it holds

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

Series `j5g_Es7sD` with `processTranscodes: false` is refused by the
transcode queue and still accepted for health checks. Movies AV1 is
`gEUZf7Nx6`.

## 2. Guards that read `code`

### 2.2 Evidence

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

Provenance: re-checked by CI.

A `customFunction` whose `inputsDB` key is `function` instead of `code` runs
the default stub. The committed counterpart is
`docs/tdarr/flow-movies_av1_nvenc_v1.after.json`: every customFunction uses
`inputsDB.code`, and `scripts/ci/tdarr-flow-nodes-test.py` re-executes those
bodies.

## 3. The flow

### 3.1 The CPU worker

Provenance: re-checked by CI.

`cargs22` / `cargs23` / `cargs24` branch on the encoder already chosen.
`av1_qsv` keeps `-preset medium` and `-global_quality`. `libsvtav1` gets
`-preset 8 -crf`. The harness runs both branches from the node sources.

### 3.2 Verified on a real transcode

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

SPF-18, CPU path, `libsvtav1`:

```
before  4,238,088,154 bytes  h264   1 video, 1 audio, 5 subrip
after   1,504,164,956 bytes  av1    1 video, 1 audio, 5 subrip   (35.5%)
```

### 3.3 guard_scope observed refusing

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

Provenance: re-checked by CI.

A file outside library `gEUZf7Nx6` or outside `/media/Movies/` is refused.
The harness replays that refusal from `guard_scope.js`.

### 3.4 Subtitles

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

Provenance: re-checked by CI.

`forceConform: true` on Set Container deletes `mov_text` streams in place.
`cont22`, `cont23`, and `cont24` stay `forceConform: false`. The `sub2X`
nodes convert `mov_text` to `srt`. The harness checks both the setting and
the converter.

### 3.5 What it would do to the seven

Provenance: re-checked by CI.

The harness runs the guard sources against the parked masters' saved probe
data. That is not a live transcode of those files.

## 4. The seven masters

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

Six remain parked. Do not queue them.

| Size | File | State |
|---|---|---|
| 66.08 GB | The Silence of the Lambs (1991) | parked, profile 8 DV |
| 64.95 GB | The Departed (2006) | parked, profile 8 DV |
| 52.70 GB | Gladiator (2000) | parked, profile 8 DV |
| 33.33 GB | Amelie (2001) | parked, no HDR |
| 27.00 GB | Wake Up Dead Man (2025) | parked, profile 8 DV |
| 21.74 GB | The Rip (2026) | parked, profile 8 DV |
| 20.92 GB | A House of Dynamite (2025) | canary; RPU dropped |

### 4.1 The canary

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

`A House of Dynamite (2025)` was encoded out of band on `av1_qsv` after the
CPU worker OOM-killed the node. HDR10 survived. The Dolby Vision RPU did not.
The original is retained outside `/media/Movies/` at
`/media/.tdarr-canary-rollback/A House of Dynamite (2025) {imdb-tt32376165} [NF][WEBDL-2160p][EAC3 Atmos 5.1][DV HDR10][h265]-BEN.mp4`.
Move it back over the `.mkv` to restore the RPU. Delete it only after the
captain accepts that trade.

## 4b. The CPU fallback cannot encode 4K

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

`transcodecpuWorkers` stays at least 1 so a VA-API failure degrades instead
of stopping the queue (PR #1443). A 4K `libsvtav1` encode peaks near 7100 MiB
against the node's 4Gi limit and OOM-kills the container.

### 4b.2 Proof that the guard holds

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

`cargs*` routes a 4K job offered to the CPU worker onto `av1_qsv`. That is
also why Dolby Vision preservation on 4K is impossible with the guard in
place: `av1_qsv` has no `-dolbyvision` flag.

## 5. What lives only in Tdarr

Library toggles and the flow document live in SQLite on the `tdarr-config`
PVC. That state does not survive a rebuild. Pods stay green.

The single restore authority is
`docs/tdarr/flow-movies_av1_nvenc_v1.after.json`. Never restore a
`before.json`. Commands: `docs/tdarr/README.md`.

### Live state at the end of this work

Provenance: one-time live observation, operator-executed on 2026-08-31; not reproducible in CI.

At the end of that pass the six masters above were byte-intact, Series
`processTranscodes` was false, and the canary's original sat in the rollback
path. Re-measure before acting. This block is not a live query.
