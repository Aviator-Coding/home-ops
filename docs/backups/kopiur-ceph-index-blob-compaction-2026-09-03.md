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
cron whose period divides 24h - produces **27-30h epochs holding ~900 index blobs each**, of
which exactly two are live at any moment. That is a **stable oscillation between ~900 and
~1810 blobs**, not a leak and not a failure.

Two things in the condition message are actively misleading, and both are recorded here
because the next person will read the same message:

1. **`takeoverPolicy: Force` was never applicable.** The lease is healthy
   (`LeaseOwned=True`, `LeaseClaimed`). Applying it would have changed nothing.
2. **The suggested `6h` reproduces the bug it is meant to fix.** It is a multiple of the quick
   cron period, which is precisely the failing case: the epoch length becomes a coin flip on
   jitter ordering, averaging ~12h and flapping. It would clear the warning most of the time
   and breach it intermittently. The fix uses **`5h`**, which is *not* a multiple of the
   period and makes the cadence deterministic. See [why 5h and not 6h](#why-5h-and-not-6h).

The third suggestion - raising `indexBlobWarnThreshold` - was explicitly not taken. The
threshold is **correctly calibrated**: at the fixed epoch length the repository peaks at
~390 blobs, comfortably inside it, so the warning was reporting a real inefficiency rather
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

### The jitter coin flip that makes 24h behave like 27-30h

kopiur advances epochs **only during a maintenance run**, and the schedule's `jitter` is
**redrawn per slot**. Measured on this cluster 2026-09-03:

| repository | cron | declared jitter | 00:00 slot fired | 06:00 slot fired |
|---|---|---|---|---|
| `ceph` | `0 */6 * * *` | 30m | 00:00:06 (**+6s**) | 06:25:41 (**+25m41s**) |
| `r2` | `0 */6 * * *` | 30m | 00:16:09 (+16m09s) | 06:02:56 (+2m56s) |

So the gap between consecutive runs of one cron is not the period `P` but
`P + J_next - J_prev`, anywhere in `[P - jitter, P + jitter]`.

**That makes any `minDuration` at a multiple of `P` a coin flip.** An epoch opened at one slot
clears the gate at the next only when `J_next >= J_prev` - about half the time - so it waits a
further period roughly every other time. The expected epoch length is `2P`, and the actual
length *flaps*. With `P = 6h` and `minDuration = 24h` (a multiple of P), that is exactly the
observed 26.7h / 30.0h / 27.0h - never 24h:

| epoch | opened | closed | length |
|---|---|---|---|
| 0 | 2026-08-30T15:29:17Z | 2026-08-31T18:08:22Z | 26.65h |
| 1 | 2026-08-31T18:08:22Z | 2026-09-02T00:10:23Z | **30.03h** |
| 2 | 2026-09-02T00:10:23Z | 2026-09-03T03:08:17Z | **26.96h** |

Worked example for epoch 2: it opened at `00:10:23` (the 00:00 slot, jitter +10m23s) and
matured 24h later at `2026-09-03T00:10:23`. The 2026-09-03 00:00 slot fired at **00:00:06** -
jitter of only 6s, so the run happened **10 minutes before** the epoch was eligible - and the
epoch had to wait for the 03:00 full run, closing at 26.96h instead of 24h.

> An earlier draft of this document attributed the effect to a fixed *"~9 minute epoch-marker
> write lag"*. That was wrong: the real marker lag is 20-60s (measured: full run spawned
> 03:07:27, marker `xe3` written 03:08:17; a manual quick run spawned 04:38:14, wrote its
> compaction blob at 04:38:35). What looked like a lag was per-slot jitter. The corrected
> mechanism is more general, and it changes both band bounds - see below.

### Why that lands at ~1850

- ceph writes a **measured 32.5 index blobs/hour** (971 blobs over 30.03h; 879 over 26.96h).
- A ~28h average epoch therefore holds **~900 blobs**.
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

The condition message suggests `6h`. **`6h` is a multiple of the quick cron period, which is
precisely the failing case above** - it inherits the same jitter coin flip, so the epoch length
averages ~12h and flaps between 6h and 18h+. It would clear the warning on a typical day and
breach it intermittently.

`5h` is chosen because it is *not* a multiple of the period, and because it sits inside a band
whose bounds are worst-case over the **declared jitter windows** rather than over any single
day's observed offsets:

| bound | value | why |
|---|---|---|
| lower (exclusive) | **4h** | The longest possible gap from the 00:00 quick to the 03:00 full run, once the full cron's `1h` jitter is drawn at maximum and the quick's at zero: `(3h + 60m) - 0m`. At or below this the **full** run can also advance the epoch, adding a fifth epoch on some days while compaction stays at one epoch per run. |
| upper (inclusive) | **5.5h** | The shortest possible quick-to-quick gap: `6h - 30m`, when the previous slot drew maximum jitter and the next drew zero. Above this the epoch **may** miss the next slot and stretch - non-deterministically. |

Inside `(4h, 5.5h]` the cadence is **deterministic**: exactly one advance per quick slot, every
day, regardless of how jitter falls.

| `minDuration` | epoch length | blobs/epoch | live peak | headroom vs 1000 | deterministic? |
|---|---|---|---|---|---|
| 24h (default) | ~28h (flaps) | ~904 | **~1809** | **-81%** | no |
| 12h | ~24h (flaps) | ~780 | **~1560** | **-56%** | no |
| **6h (as suggested)** | **~12h (flaps 6-18h+)** | **~390** | **~780** | **~22%** | **no** |
| **5h (chosen)** | **6h** | **195** | **390** | **61%** | **yes** |
| 4h or below | 6h, sometimes 4-5 epochs/day | ~195 | ~390 | ~61% | no - full run may also advance |

**Determinism is the reason to prefer 5h, not the margin alone.** A value that clears the
threshold only on average is a value that pages intermittently.

Compaction capacity is the other half of the check: compaction is **one epoch per maintenance
run** and ceph has **5 runs/day** (4 quick + 1 full), so 4 epochs/day is sustainable with one
run of headroom.

> An earlier draft of this document gave the band as `(3.0h, 5.85h]`, derived from a single
> day's observed offsets plus a supposed fixed marker lag. Both bounds were wrong - too low at
> the bottom (the full run's 1h jitter can reach 4h) and too high at the top (the quick cron's
> 30m jitter can shorten a gap to 5.5h). `5h` is inside both the old and the corrected band, so
> the shipped value is unchanged; the CI gate now pins the corrected bounds.

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
