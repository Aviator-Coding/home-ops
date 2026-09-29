# Parked masters and open choices

These items are still awaiting the captain. Do not close them while editing
the flow, the node, or a library toggle.

## Open

1. **`e_dv_bypass` encodes Dolby Vision.** `dv_check` output 1 (the file is
   DV) is wired to `snapshot`, so DV files still enter the encoder. Removing
   the edge would start skipping them. Six of the seven masters are DV HDR10.
2. **`br_*_bypass` makes the bitrate checks inert.** Each below-threshold
   output is routed into the encoder anyway.
3. **There is no stream-count guard.** Size, duration, and HDR are checked.
   Track counts are not.
4. **4K DV preservation and the 4K CPU guard disagree.** Five of the six
   parked masters are Dolby Vision profile 8 (`bl_signal_compatibility_id` 1):
   Silence of the Lambs, The Departed, Gladiator, Wake Up Dead Man, The Rip.
   The base layer is valid HDR10, so losing the RPU degrades them to HDR10.
   None are profile 5. Amelie has no HDR (1080p AVC) and is unaffected.
   `av1_qsv` has no `-dolbyvision` flag and drops the RPU.
   `libsvtav1 -dolbyvision true` keeps it as `dv_profile` 10. A 4K
   `libsvtav1` encode is the job that OOM-kills the node at 4Gi, which is
   why `cargs*` sends every 4K job to `av1_qsv`. The guard makes 4K safe and
   makes DV preservation on 4K impossible. Options left open: raise node
   memory so 4K `libsvtav1` fits; add a DV exception and the memory to back
   it; accept HDR10-only output; or leave the five parked.

## Still parked

| Size | File | State |
|---|---|---|
| 66.08 GB | The Silence of the Lambs (1991) | parked, profile 8 DV |
| 64.95 GB | The Departed (2006) | parked, profile 8 DV |
| 52.70 GB | Gladiator (2000) | parked, profile 8 DV |
| 33.33 GB | Amelie (2001) | parked, no HDR |
| 27.00 GB | Wake Up Dead Man (2025) | parked, profile 8 DV |
| 21.74 GB | The Rip (2026) | parked, profile 8 DV |

Do not queue these. A bulk requeue rewrites the file in place with no
verification. That already replaced Johnny Mnemonic in place.

## Canary rollback

`A House of Dynamite (2025)` was the canary. The encode finished out of band
on `av1_qsv` after the CPU worker OOM-killed the node, then the verified
`.mkv` replaced the master. HDR10 survived; the Dolby Vision RPU did not.
The original master is retained at:

```
/media/.tdarr-canary-rollback/A House of Dynamite (2025) {imdb-tt32376165} [NF][WEBDL-2160p][EAC3 Atmos 5.1][DV HDR10][h265]-BEN.mp4
```

It sits outside `/media/Movies/` so the folder watcher, Plex, and Radarr do
not see a second copy. Moving it back over the `.mkv` restores the RPU.
Delete it only after the captain accepts the DV trade.

The dated inventory and the harness-checked proof (including the SPF-18
byte counts) stay in `docs/tdarr-errored-remuxes.md`. The harness reads that
file; do not delete it, and do not edit `docs/tdarr/flow-nodes/`.
