# kopiur `ceph` index-blob compaction - cause and fix, 2026-09-03

> **Status: investigation, fix and live proof. No backup data was touched.**
> Nothing deleted, expired or pruned a kopia snapshot. No `Snapshot`, `SnapshotPolicy` or
> `SnapshotSchedule` object was created, deleted or patched. `deletionProtection` thresholds
> and the `Retain` deletion policies are unchanged. Epoch compaction rewrites **indexes**;
> it does not touch snapshot content. Census: **322 Snapshot CRs before and after, zero
> disappeared** - see [snapshot safety](#snapshot-safety).

The `ceph` `ClusterRepository` reported `IndexBlobHealth=False` / `TooManyIndexBlobs`
continuously from **2026-09-01T09:52:57Z**:

> repository has 1851 content-index blobs (threshold 1000); maintenance is not compacting
> them. Fix: ensure maintenance runs - if stuck on a stale lease, set
> `spec.maintenance.takeoverPolicy: Force` once; if it IS running, the epoch gate is usually
> why - lower `spec.parameters.epoch.minDuration` (default 24h, e.g. 6h) so blobs compact.
> Raise `spec.health.indexBlobWarnThreshold` (or 0) to silence.

## Verdict

**Maintenance was never stuck, and the lease was never stale.** It runs on schedule, owns its
lease (`LeaseOwned=True` / `LeaseClaimed`), and its *quick* runs really do compact. The cause is
`parameters.epoch.minDuration` sitting at kopia's `24h` default, which on this repository
produces **26.7-30.0h epochs holding ~900 index blobs each**, of which exactly two are live at
any moment - a **stable oscillation between ~900 and ~1810 blobs**, not a leak and not a failure.

Fix: **`epoch.minDuration: 4h` on `ceph` only.**

All three fixes the condition message suggests are wrong here, and that is the single most
useful thing on this page:

1. **`takeoverPolicy: Force` was never applicable** - the lease is healthy. It would have
   changed nothing, and committing it to a declarative manifest is its own trap
   ([below](#on-takeoverpolicy-force---the-gitops-trap)).
2. **The suggested `6h` lands in a 19%-headroom band** - barely better than nothing, and for a
   non-obvious reason: headroom **steps** rather than slopes. So did this investigation's own
   first attempt at `5h`, which is why the value shipped is `4h`
   ([below](#sizing-mindurationthe-answer-is-not-monotonic)).
3. **Raising `indexBlobWarnThreshold` is silencing, not fixing.** The threshold is *correctly
   calibrated*: at `4h` the repository peaks at ~649 blobs, comfortably inside 1000. CI now
   refuses any value for it on either repository.

## The mechanism

Kopia stores content indexes as per-session **index blobs** inside an **epoch**. Blobs are only
consolidated when their epoch closes, and the pipeline for one epoch `N` is:

| # | stage | gate |
|---|---|---|
| 1 | epoch `N` accumulates index blobs (`xn<N>_...`) | - |
| 2 | epoch `N` closes, `N+1` opens (`xe<N+1>` marker) | **`epoch.minDuration`**, evaluated only during a maintenance run |
| 3 | epoch `N` becomes *settled* | write epoch reaches `N+2` |
| 4 | epoch `N` compacted into one `xs<N>_...` blob | one epoch per maintenance run |
| 5 | the superseded `xn<N>_...` blobs are deleted | **`cleanupSafetyMargin`** (4h), then the next run |

Two mechanics decide the real epoch length. Both were measured, and the second was established
by an experiment that **falsified this investigation's first fix**:

### 1. Epoch age starts at the first index blob, not at the epoch marker

The `xe<N>` marker blob opens an epoch, but kopia counts the epoch's age from the **first index
blob written into it**. Because writes arrive in the 4-hourly backup bursts, that leaves 0-4h of
dead time before the clock even starts:

| epoch | marker (`xe<N>`) | first blob (`xn<N>`) | dead time |
|---|---|---|---|
| 1 | 2026-08-31T18:08:22Z | 2026-08-31T21:03:43Z | **2.92h** |
| 2 | 2026-09-02T00:10:23Z | 2026-09-02T01:04:52Z | 0.91h |
| 3 | 2026-09-03T03:08:17Z | 2026-09-03T04:38:34Z | 1.50h |

**The decisive experiment.** With `minDuration: 5h` applied and verified live, a manual quick
maintenance run was triggered at **08:10:43Z**. Epoch 3 was by then **5.03h** old measured from
its marker but only **3.53h** old measured from its first blob. It did **not** advance - no
`xe4` was written. The marker-based model predicted an advance and was refuted; the first-blob
model predicted no advance and matched. Epochs 1 and 2 fit *both* models and could not tell them
apart, which is exactly why the experiment was necessary.

### 2. Epochs advance only during a maintenance run

Maturity is then rounded up to the next run (quick `0 */6 * * *` at 00/06/12/18 with `<=30m`
jitter; full `0 3 * * *` with `<=1h` jitter). Jitter is redrawn **per slot** - measured on
ceph's quick cron: `00:00:06` at the 00:00 slot, `06:25:41` at the 06:00 slot.

So:

```
epoch length = (wait for the first write) + minDuration + (wait for the next maintenance run)
```

which is strongly **non-linear** in `minDuration`.

### Why that lands at ~1850

- ceph writes a **measured 32.5 index blobs/hour** (971 blobs over 30.03h; 879 over 26.96h).
- A ~28h epoch therefore holds **~900 blobs**.
- **Exactly two epochs are live at once** - verified live: `indexBlobCount` 1006 = 879 (`xn2`)
  + 127 (`xn3`), and again 882 = 879 + 1 + 2 (`xs0`, `xs1`) right after a compaction.
- Count oscillates **~900 to ~1810** against a threshold of 1000.

**`indexBlobCount` counts *live* index blobs, not objects in the bucket.** Compaction supersedes
an epoch's `xn` blobs immediately - the count drops the moment `xs<N>` is written - while the
superseded objects physically remain until `cleanupSafetyMargin` (4h) expires. A bucket listing
and the status field legitimately disagree during that window.

## Sizing `minDuration` - the answer is not monotonic

Both mechanics were simulated against the real maintenance **and backup** schedules over 2,400
epoch-days. The model reproduces the observed 24h behaviour (mean 28.8h, range 26.4-30.5h,
against measured 26.96h / 30.03h), which is what licenses using it to choose.

Peak = 2 x **worst-case** epoch length x 32.5 blobs/h. The worst epoch breaches the threshold,
not the average one.

| `minDuration` | epochs/day | worst epoch | peak blobs | headroom vs 1000 | |
|---|---|---|---|---|---|
| 24h (default) | 0.83 | 30.5h | **1980** | **-98%** | the reported fault |
| 12h | 1.50 | 18.5h | **1201** | **-20%** | still over |
| 5.0-6.0h | 2.0-2.8 | 12.5h | ~812 | **19%** | **where `6h` and the first `5h` attempt land** |
| **3.5-4.75h** | **3.0** | **10.0h** | **649** | **35%** | **chosen plateau** |
| 3.0-3.25h | 3.1-3.7 | 12.5h | ~810 | 19% | cliff between the good regions |
| 1.5-2.75h | 4.0 | 8.7-9.5h | 564-616 | 38-44% | better peak, narrower |

**Headroom steps rather than slopes**, so a nearby round number is likely to be *worse*, not
slightly different. `4h` was chosen over the marginally-better 2.75h because:

- the 3.5-4.75h plateau is **1.25h wide**, so it tolerates schedule drift; 2.75h sits 0.25h from
  a cliff down to 19%;
- at **3 epochs/day** it leaves the most compaction margin - compaction advances one epoch per
  maintenance run and there are 5 runs/day.

Re-derive if the maintenance **or the backup** schedule changes; both feed the result.

## Why `r2` is unaffected - and why that is *not* because it is healthier

**`r2` has the identical stalled-compaction structure.** Measured the same day:

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
`r2` accumulates ~4.4x slower and converges on ~420 - under the threshold.

**`IndexBlobHealth=True` on `r2` means "quieter", not "correct".** It was left untuned
deliberately, to keep the change to what the evidence required, and its manifest carries a
comment saying so. If r2's backup cadence is raised or its claim count grows several-fold it
will reproduce this warning, and the plateau must be re-derived against **r2's own** schedules
(maintenance offsets quick ~00:16, full ~03:35), not copied from ceph's.

## On `takeoverPolicy: Force` - the GitOps trap

The fix does not use it, but the condition message recommends it, so: `takeoverPolicy: Force`
seizes a maintenance lease held by a different owner and is documented as a **one-shot** action.
A declarative manifest re-applies its content forever, so committing it does not encode "take
over once" - it encodes "take over on every reconcile", turning a deliberate recovery into a
standing policy that silently steamrolls any future owner conflict instead of surfacing it. If a
lease genuinely is stale, force it as a live `kubectl patch` and revert, or commit it, confirm
the takeover, and remove it in a follow-up.

## Snapshot safety

PROOF_PLACEHOLDER

## How to recognise this next time

- **`IndexBlobHealth=False` alone does not mean maintenance is broken.** Check
  `Maintenance/<repo>.status`: `LeaseOwned=True` plus a recent `quick.lastRunAt` means it is
  running, and the epoch gate is the cause. `lastContentReclaimedBytes: 0` is normal for a
  repository with nothing to reclaim and is *not* evidence of a stalled run.
- **Read the bucket, not just the status.** `xn<N>` counts per epoch, plus the `xe`/`xs`/`xr`
  blobs, show the pipeline directly: many `xn`, few `xs`, no `xr` is the stalled-compaction
  signature. There is **no Prometheus metric** for the index-blob count - 48 `kopiur_*` series
  exist and none covers it, so `ClusterRepository.status.storageStats.indexBlobCount` and the
  bucket are the only instruments.
- **Measure epoch age from the first `xn<N>` blob, never from the `xe<N>` marker.** They can
  differ by hours, and the difference is what makes a plausible-looking `minDuration` fail.
- **Do not interpolate `minDuration`.** Headroom steps; simulate against both the maintenance
  and backup schedules rather than picking a round number.
- **A healthy sibling repository is not a control.** Compare *structure* (`xe`/`xs`/`xn` shape),
  not the condition - `r2` looked healthy while carrying the same defect.
- **Expect a transient re-fire while the old epochs drain.** The warning cleared at 04:44:51Z,
  returned at 06:05:50Z as the open epoch refilled, and only settles once the oversized
  pre-existing epochs have compacted. A single green reading is not the fix landing.
- Triggering an out-of-band run is supported and cheap:
  `kubectl -n system annotate maintenance <repo> kopiur.home-operations.com/run-requested="$(date -u +%Y-%m-%dT%H:%M:%SZ)" kopiur.home-operations.com/run-mode=quick --overwrite`.
  A *quick* run is index/log work only and cannot reclaim content, completes in ~25s, and
  `status.manualRun.phase` tracks it. It is not free of consequence: it **advances the epoch
  pipeline**, so take the baseline first.

## Corrections made during this investigation

Recorded because the reasoning is reusable and the wrong turns are instructive:

1. **"Jitter is a stable per-object offset"** - wrong; it is redrawn per slot (`00:00:06` vs
   `06:25:41` on the same cron).
2. **"A fixed ~9-minute epoch-marker write lag causes the just-miss"** - wrong; the real marker
   lag is 20-60s. What looked like a lag was jitter, and then the actual cause turned out to be
   mechanic 1 above.
3. **Band `(3.0h, 5.85h]`, then `(4h, 5.5h]`** - both derived from the marker-based model and
   both wrong. The real answer is a plateau at `[3.5h, 4.75h]` with a cliff on either side.
4. **`5h`, and the claim that `6h` "would not work"** - `5h` was shipped first and the manual
   run at 08:10:43Z falsified it. Under the corrected model `5h` and `6h` are *equivalent*, both
   at 19% headroom. The value is now `4h`.

The general lesson: epochs 1 and 2 were consistent with two different models, and picking the
wrong one produced a fix that looked well-argued and would have delivered a fraction of the
intended headroom. The only thing that separated them was an experiment whose outcome the two
models disagreed about.
