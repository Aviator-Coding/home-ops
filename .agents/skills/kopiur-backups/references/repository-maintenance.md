# Repository maintenance and index blobs

`IndexBlobHealth=False` almost never means maintenance is broken. On `ceph`
the condition was false while the lease was healthy and quick runs really did
compact. The cause was `parameters.epoch.minDuration` at kopia's `24h`
default.

## Two mechanics

1. **Epoch age is counted from the first index blob written into the epoch**,
   not from the `xe<N>` marker that opens it. Backup bursts leave 0-4h of dead
   time before the clock starts. Measure age from the oldest `xn<N>` blob.
   A `5h` setting failed this test: the marker said 5.03h and the first blob
   said 3.53h, so the epoch did not advance.
2. **Epochs advance only during a maintenance run**, so maturity rounds up to
   the next run. Quick runs are 00/06/12/18 with up to 30m jitter. Full is 03
   with up to 1h.

`epoch length = (wait for first write) + minDuration + (wait for next run)`.

Headroom **steps**. Do not interpolate or round `minDuration` to a tidy
number. Simulated against the real schedules, peak live-blob count on ceph:

| minDuration | peak | headroom band |
|---|---|---|
| 24h | 1980 | default, over threshold |
| 12h | 1201 | |
| 5.0-6.0h | ~812 | 19% |
| **4h (live ceph)** | **649** | **35%, mid-plateau** |
| 3.0-3.25h | ~810 | 19% |
| 1.5-2.75h | 564-616 | 38-44% |

The condition message's suggested `6h` lands in the 19% band. `ceph` runs
`4h`, at least 0.5h from either cliff, three epochs a day. Compaction advances
one epoch per maintenance run; there are five runs a day.

Exactly two epochs are live at once (one closed-but-not-yet-compacted, plus
the open one). `indexBlobCount` counts **live** index blobs, not bucket
objects. Superseded `xn` blobs remain until `cleanupSafetyMargin` (4h) and a
later run. A bucket listing and the status field disagree during that window.

There is no Prometheus series for the index-blob count. Read the bucket.
Prefixes: `xe` epoch marker, `xn` uncompacted index blob (the count), `xs`
single-epoch compaction, `xr` range checkpoint, `xw` deletion watermark.
Many `xn`, few `xs`, no `xr` is stalled compaction.

## `parameters` is write-only from Git

The CRD says parameters are re-applied on bootstrap when they drift, and an
absent `blobRetention` means "don't touch it". Resuming Flux after a live
patch dropped `spec.parameters` to absent while
`status.parameters.epoch.minDuration` stayed at the patched value. A git
revert does not undo a parameters change. To roll back: declare the old value,
let it apply, then remove the block.

`r2` can read `IndexBlobHealth=True` with the same structural shape, because
it takes the daily slot and accumulates slower. Compare structure, not the
condition. `r2` was left untuned on purpose. Raising its cadence or its claim
count several-fold reproduces the warning, and the band must be re-derived
against r2's own offsets.

## Do not commit these "fixes"

- `takeoverPolicy: Force` is a one-shot. A manifest re-applies it forever.
  If a lease is actually stale, patch it live and revert, or commit, confirm,
  and remove it in a follow-up.
- Raising the alert threshold hides a real inefficiency.
- The suggested `6h` `minDuration` is the wrong band.

## Out-of-band run

```sh
kubectl -n system annotate maintenance <repo> \
  kopiur.home-operations.com/run-requested="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  kopiur.home-operations.com/run-mode=quick --overwrite
```

A quick run is index/log work only. It advances the epoch pipeline, so take
the baseline first. `status.manualRun.phase` tracks it.
