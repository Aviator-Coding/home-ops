# kopiur `ceph` index-blob compaction - cause and fix, 2026-09-03

> **Status: investigation, fix and live proof. No backup data was touched.**
> Nothing deleted, expired or pruned a kopia snapshot. No `Snapshot`, `SnapshotPolicy` or
> `SnapshotSchedule` object was created, deleted or patched. `deletionProtection` thresholds
> and the `Retain` deletion policies are unchanged. Epoch compaction rewrites **indexes**;
> it does not touch snapshot content.

The `ceph` `ClusterRepository` reported `IndexBlobHealth=False` / `TooManyIndexBlobs`
continuously from **2026-09-01T09:52:57Z**:

> repository has 1851 content-index blobs (threshold 1000); maintenance is not compacting
> them. Fix: ensure maintenance runs - if stuck on a stale lease, set
> `spec.maintenance.takeoverPolicy: Force` once; if it IS running, the epoch gate is usually
> why - lower `spec.parameters.epoch.minDuration` (default 24h, e.g. 6h) so blobs compact.
> Raise `spec.health.indexBlobWarnThreshold` (or 0) to silence.

## Verdict

**Maintenance was never stuck, and the lease was never stale.** Maintenance runs on schedule,
owns its lease, and its *quick* runs really do compact. The cause is
`parameters.epoch.minDuration` sitting at kopia's `24h` default, which - against a maintenance
schedule whose runs are 6h apart - produces **27-30h epochs holding ~900 index blobs each**,
of which exactly two are live at any moment. That is a **stable oscillation between ~900 and
~1810 blobs**, not a leak and not a failure.

Two things in the condition message are actively misleading, and both are recorded here
because the next person will read the same message:

1. **`takeoverPolicy: Force` was never applicable.** The lease is healthy
   (`LeaseOwned=True`, `LeaseClaimed`). Applying it would have changed nothing.
2. **The suggested `6h` does not do what it says.** It loses to the same rounding effect that
   causes the bug and silently delivers **11.85h** epochs, not 6h. It would clear the warning,
   but at ~770 peak against a 1000 threshold - 23% headroom, which this fleet would spend by
   growing from 29 claims to ~38. The fix uses **`5h`**, derived from this repository's own
   maintenance offsets, which delivers the epoch length it asks for and 62% headroom. See
   [why 5h and not 6h](#why-5h-and-not-6h).

The third suggestion - raising `indexBlobWarnThreshold` - was explicitly not taken. The
threshold is **correctly calibrated**: at the fixed epoch length the repository peaks at
~380 blobs, comfortably inside it, so the warning was reporting a real inefficiency rather
than being mis-set.

## The mechanism

Kopia stores content indexes as per-session **index blobs** inside an **epoch**. Blobs are only
consolidated when their epoch closes, and the pipeline for one epoch `N` is:

| # | stage | gate |
|---|---|---|
| 1 | epoch `N` accumulates index blobs (`xn<N>_...`) | - |
| 2 | epoch `N` closes, `N+1` opens (`xe<N+1>` marker) | **`epoch.minDuration`**, evaluated only during a maintenance run |
| 3 | epoch `N` becomes *settled* | write epoch reaches `N+2` (2-epoch lag) |
| 4 | epoch `N` compacted into one `xs<N>_...` blob | one epoch per maintenance run |
| 5 | the superseded `xn<N>_...` blobs are deleted | **`cleanupSafetyMargin`** (4h), then the next run |

`minDuration` is documented in the CRD as *"the advance **gate** - no blob count closes an epoch
younger than this"*, and `advanceOnCount` (20) / `advanceOnSizeMiB` (10) are explicitly
*"once older than `minDuration`"*. With ~900 index blobs written per day, `advanceOnCount` is
exceeded within minutes of every epoch opening, so **`minDuration` is the only binding
constraint** and the count thresholds never get a say.

### The rounding effect that makes 24h behave like 27-30h

kopiur advances epochs **only during a maintenance run**, and it writes the epoch marker
*~9 minutes into* that run. So an epoch opened by a run is, at the run exactly one period
later, ~9 minutes **short** of maturity - and has to wait a whole further period.

Measured on `ceph` (maintenance quick `0 */6 * * *` at ~00:00/06:00/12:00/18:00, full
`0 3 * * *` at ~03:07):

| epoch | opened | closed | length | age at the run that "should" have closed it |
|---|---|---|---|---|
| 0 | 2026-08-30T15:29:17Z | 2026-08-31T18:08:22Z | 26.65h | - |
| 1 | 2026-08-31T18:08:22Z | 2026-09-02T00:10:23Z | **30.03h** | 23.86h at the 2026-09-01 18:00 run |
| 2 | 2026-09-02T00:10:23Z | 2026-09-03T03:08:17Z | **26.96h** | 23.83h at the 2026-09-03 00:00 run |

Both epochs missed the 24h mark by **eight to ten minutes** and stretched to the next slot.
A simulation of the maintenance schedule with a 9-minute marker lag reproduces both observed
epoch boundaries exactly.

### Why that lands at ~1850

- ceph writes a **measured 32.5 index blobs/hour** (971 blobs over 30.03h; 879 over 26.96h).
- A 27.85h average epoch therefore holds **~900 blobs**.
- **Exactly two epochs are live at once**: one closed-but-not-yet-compacted, plus the open one.
  A closed epoch waits one full epoch period to settle (write epoch must reach `N+2`) and is
  compacted at that run, so the closed-uncompacted set is never deeper than one.
- The count therefore oscillates between **~900** (just after a compaction) and **~1810**
  (just before the next one). The model predicts a 1809 peak; the observed maximum was
  **1851** - a 2.3% error.

That two-epoch model is not inferred, it is read directly: immediately after epoch 1 was
compacted, `indexBlobCount` was **882 = 879 (`xn2`) + 1 (`xn3`) + 2 (`xs0`, `xs1`)**.

**`indexBlobCount` counts *live* index blobs, not objects in the bucket.** Compaction supersedes
an epoch's `xn` blobs immediately - the count drops the moment `xs<N>` is written - while the
superseded objects physically remain until `cleanupSafetyMargin` (4h) expires and a later run
deletes them. Do not expect a bucket listing and the status field to agree during that window.

Measured bucket state at 2026-09-03T04:30Z (7,896 objects total):

```
xe1, xe2, xe3      3 epoch markers
xs0_...            1 single-epoch compaction (epoch 0 only), written 2026-09-02T03:09:02Z
xn1_...          971 uncompacted index blobs (epoch 1)
xn2_...          879 uncompacted index blobs (epoch 2)
xr                 0 range checkpoints (checkpointFrequency 7; only at epoch 3)
xw1788314929       1 deletion watermark
```

## Why `r2` is unaffected - and why that is *not* because it is healthier

This is the part the alert's asymmetry hides. **`r2` has the identical stalled-compaction
structure.** Measured the same day:

| | `ceph` | `r2` |
|---|---|---|
| epoch markers | `xe1`/`xe2`/`xe3` | `xe1`/`xe2`/`xe3` (within ~15 min of ceph's) |
| single-epoch compactions | 1 (`xs0`) | 1 (`xs0`) |
| range checkpoints | 0 | 0 |
| uncompacted `xn` blobs | 971 + 879 = **1850** | 308 + 113 = **421** |
| `indexBlobCount` | **1851** | **422** |
| `IndexBlobHealth` | `False` | `True` |

Same operator, same defaults, same epoch parameters, same two-epoch backlog. The **only**
difference is write volume: `ceph` takes the 4-hourly backup slot and `r2` the daily one, so
`r2` accumulates ~4.4x slower and converges on ~420 rather than ~1850 - under the fixed 1000
threshold.

**`IndexBlobHealth=True` on `r2` therefore means "quieter", not "correct".** It was left
untuned deliberately, to keep the change to what the evidence required, and its manifest now
carries a comment saying so. If r2's backup cadence is raised, or its claim count grows
several-fold, it will reproduce this warning and needs the same treatment - re-derived against
**r2's own** maintenance offsets (quick ~00:16, full ~03:35), not copied from ceph's.

## Why 5h and not 6h

The condition message suggests `6h`. **It does not work**, for the same reason `24h` does not:
the ~9-minute marker lag means an epoch is 5.85h old at the quick run 6h later, just short of a
6h gate, so it waits a further 6h.

Simulating ceph's actual run offsets (quick 00/06/12/18, full ~03:07) with a 9-minute marker
lag, against the measured 32.5 blobs/hour and the verified two-epoch live model:

| `minDuration` | epochs/day | actual epoch | blobs/epoch | live peak | headroom vs 1000 |
|---|---|---|---|---|---|
| 24h (default) | 0.75 | 27.85h | 904 | **1809** | **-81%** |
| 12h | 1.25 | 16.65h | 541 | **1081** | **-8%** |
| 8h | 1.75 | 11.85h | 385 | 770 | 23% |
| **6h (as suggested)** | **1.75** | **11.85h** | **385** | **770** | **23%** |
| **5h (chosen)** | **3.75** | **5.85h** | **190** | **380** | **62%** |
| 4h | 3.75 | 5.85h | 190 | 380 | 62% |

`6h` would clear the warning - the earlier claim in this document that it would not was wrong,
and is corrected here. It is still the worse buy: it delivers **11.85h** epochs rather than the
6h it asks for, and 23% headroom is about nine more claims on a 29-claim fleet. `5h` delivers
the epoch length it names and 62% headroom.

The usable band is **(3.0h, 5.85h]** - low enough to advance at every quick run, high enough
never to advance at the full run (which sits only ~3.1h after the 00:00 quick and would
otherwise create a fifth epoch per day and eat the compaction headroom). `5h` sits inside it
with ~2h of margin below and ~0.85h above. Going below 4h buys nothing, because run spacing
quantises the result.

Compaction capacity is the other half of the check: compaction is **one epoch per maintenance
run** and ceph has **5 runs/day**, so 4 epochs/day is sustainable with one run of headroom.

## The fix

`kubernetes/apps/base/system/kopiur/repository/clusterrepository.yaml`, `ceph` only:

```yaml
  parameters:
    epoch:
      minDuration: 5h
```

Nothing else changed. `advanceOnCount`, `advanceOnSizeMiB`, `checkpointFrequency`,
`deleteParallelism` and `refreshFrequency` stay at kopia defaults - none of them is binding
here. The maintenance schedule is untouched.

### On `takeoverPolicy: Force` - the trap the brief asked about

The fix does **not** use it, but the trap is worth naming because the condition message
recommends it. `takeoverPolicy: Force` is documented as a **one-shot** action: it seizes a
maintenance lease held by a different owner. A declarative GitOps manifest re-applies its
content forever, so committing `Force` does not encode "take over once" - it encodes "take
over on every reconcile, permanently". That converts a deliberate one-time recovery into a
standing policy that would let *any* future owner conflict be silently steamrolled instead of
surfaced. If a stale lease ever does need forcing, do it as a live `kubectl patch` and revert,
or commit it, confirm the takeover, and remove it in a follow-up - never leave it in Git.

## Live proof

`kopiur-repository` was suspended (`flux suspend ks kopiur-repository -n system`) for the
window, `ceph` patched live, and the Kustomization resumed afterwards - the repo's sanctioned
pre-merge validation flow.

PROOF_PLACEHOLDER

## How to recognise this next time

- **`IndexBlobHealth=False` alone does not mean maintenance is broken.** Check
  `Maintenance/<repo>.status`: `LeaseOwned=True` plus a recent `quick.lastRunAt`/`full.lastRunAt`
  means it is running, and the epoch gate is the cause. `lastContentReclaimedBytes: 0` is
  normal for a repository with nothing to reclaim and is *not* evidence of a stalled run.
- **Read the bucket, not just the status.** `xn<N>` counts per epoch, plus the `xe`/`xs`/`xr`
  blobs, show the epoch pipeline directly: many `xn`, few `xs`, no `xr` is the stalled-compaction
  signature. There is no Prometheus metric for the index-blob count - it exists only in
  `ClusterRepository.status.storageStats.indexBlobCount`.
- **Compare epoch marker timestamps against the maintenance schedule.** If epochs are
  consistently *longer* than `minDuration` by roughly one maintenance period, this rounding
  effect is why.
- **Never size `minDuration` as a multiple of the maintenance period.** It will just-miss every
  time. Size it strictly *below* the shortest inter-run gap, minus the marker lag.
- **A healthy sibling repository is not a control.** Compare structure (`xe`/`xs`/`xn` shape),
  not the condition - `r2` looked healthy while carrying the same defect.
- Triggering an out-of-band run is supported and cheap:
  `kubectl -n system annotate maintenance <repo> kopiur.home-operations.com/run-requested="$(date -u +%Y-%m-%dT%H:%M:%SZ)" kopiur.home-operations.com/run-mode=quick --overwrite`.
  A *quick* run is index/log work only and cannot reclaim content.
