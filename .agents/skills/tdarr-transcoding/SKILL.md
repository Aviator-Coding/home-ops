---
name: tdarr-transcoding
description: "Read before changing anything in the Tdarr transcoding stack: library toggles or schedules, node scheduling and librariesToNotProcess, flow definitions, customFunction nodes, the Set Container node, or encoder-specific output arguments. Also read when investigating errored remuxes or missing subtitle tracks."
---

# Tdarr libraries, flows and flow nodes

The HelmRelease owns the container, the `devic.es/b70-vaapi` device, worker
counts and the node memory limit. What gets transcoded, and how, lives in
Tdarr's SQLite database on the `tdarr-config` PVC. A rebuild of that volume
reverts those rows to defaults while the pod stays green.

## Tripwires

1. **`librariesToNotProcess` is not a scope boundary.** It is a Tdarr Pro
   feature. This install has no licence, so the check never runs. Scope is
   the library toggle `processTranscodes`. [mechanisms.md](references/mechanisms.md)
2. **A `customFunction` node reads `inputsDB.code`.** A node whose key is
   `function` runs Tdarr's default stub, which returns `outputNumber: 1`
   unconditionally, prints your source in the job report, and reports no
   error. Confirm a guard by a string only its code can emit.
   [mechanisms.md](references/mechanisms.md)
3. **Never enable `forceConform` on `Set Container`.** It sets
   `stream.removed` on `mov_text` and other non-mkv codecs and deletes those
   tracks in place. Convert `mov_text` to `srt`. [mechanisms.md](references/mechanisms.md)
4. **Never set `transcodecpuWorkers` to 0.** A GPU-only node turns a VA-API
   failure into a total outage (PR #1443). A 4K `libsvtav1` job at the node's
   4Gi limit OOM-kills the container, so the flow routes 4K work to
   `av1_qsv`. [mechanisms.md](references/mechanisms.md)
5. **Dolby Vision on 4K is an open captain choice.** `e_dv_bypass` still
   encodes DV files. `av1_qsv` drops the RPU; the encoder that keeps it is
   the one the 4K guard refuses. Do not close that edge while editing the
   flow. [masters.md](references/masters.md)
6. **Never bulk-requeue.** Tdarr rewrites the source in place. The flow
   restore input is `docs/tdarr/flow-movies_av1_nvenc_v1.after.json`, never a
   `before.json`. Node sources under `docs/tdarr/flow-nodes/` are not restore
   inputs. Do not edit that directory; CI byte-checks it.

## Where things live

| What | Path |
|---|---|
| Server and node | `kubernetes/apps/base/media/tdarr/app/helmrelease.yaml` |
| Human rebuild runbook | `docs/tdarr/README.md` |
| Flow restore document | `docs/tdarr/flow-movies_av1_nvenc_v1.after.json` |
| Node sources and harness | `docs/tdarr/flow-nodes/` (do not edit the harness) |
| Parked masters and the DV choice | [masters.md](references/masters.md) and `docs/tdarr-errored-remuxes.md` |
| VA-API check | skill `intel-gpu`, [vaapi-check.md](../intel-gpu/references/vaapi-check.md) |

## Procedures

- Why a guard looks enabled and is not: [mechanisms.md](references/mechanisms.md).
- Parked files, the canary rollback path, and the open edges: [masters.md](references/masters.md).
- Restore library scope, then the flow: `docs/tdarr/README.md`.

## Verify

- `python3 scripts/ci/tdarr-flow-nodes-test.py` (runs `docs/tdarr/flow-nodes/behavior-test.js`).
- Behavioural, not by reading the flow: a job report must contain
  `Scope guard: library=`, `Stream conform: in v=`, `AV1 tuning: encoder="`,
  and `Size check:` / `Duration check:` / `HDR survival:`.
- Library rows: Series `j5g_Es7sD` has `processTranscodes` false; Movies AV1
  `gEUZf7Nx6` has it true. Health checks and scanning stay on for Series.
