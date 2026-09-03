# kopiur `ceph` index-blob compaction - cause and fix, 2026-09-03

> **Status: investigation, fix and live proof. No backup data was touched.**
> Nothing deleted, expired or pruned a kopia snapshot. No `Snapshot`, `SnapshotPolicy` or
> `SnapshotSchedule` object was created, deleted or patched. `deletionProtection` thresholds
> and the `Retain` deletion policies are unchanged. Epoch compaction rewrites **indexes**;
> it does not touch snapshot content. Snapshot census: every disappearance attributed to
> ordinary GFS retention, none unexplained, and zero Snapshot CRs created by this work -
> see [snapshot safety](#snapshot-safety).

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

The meaningful invariant on this cluster is **not** "the Snapshot census is unchanged" - schedules
and GFS retention move it continuously - but **every snapshot that disappeared is attributed**.

| | |
|---|---|
| Snapshot CRs before (04:37Z) | **322** (234 ceph / 88 r2) |
| Snapshot CRs at 09:15Z | **337** |
| disappeared | 36 - **all attributed** |
| appeared | 51 (36 scheduled + 14 discovered + 1 in-flight) |

The count keeps moving between any two readings, which is exactly why "census unchanged" is the
wrong invariant here - an intermediate reading at 08:44Z showed 336 with 29 disappeared, and the
09:00 backup burst rotated seven more while this was being written.

**Every disappearance is ordinary GFS retention, not this work.** All 36 are dated `20260902`;
all 36 new scheduled snapshots are dated `20260903`; every claim that lost one gained one,
one-for-one. The operator log names them individually - `pruned backup (GFS retention)
config=<claim>-ceph backup=<claim>-ceph-20260902...` - driven by the scheduled backup bursts
creating each claim's next snapshot. (The one snapshot with no `origin` yet is
`selfhosted/syncthing-ceph-20260903091417`, `Pending`/`SourceStaged=False` - an in-flight backup
from the 09:00 burst.) **No `Snapshot` CR was created during
this work**, which matters because creating one *is* destructive under GFS retention (it can
push an older snapshot out of the tier). The only non-retention deletion anywhere in the
operator log is `hermes-r2-verify-20260902`, a hand-made verification snapshot removed at
2026-09-02T23:26Z by the previous day's wave-two re-proof - before this investigation began.

**The 14 appearances are a bonus finding.** They are `origin: discovered` /
`phase: Discovered` CRs the catalog rediscovered when the maintenance run refreshed it (the
catalog had been stale since 2026-08-31T05:09Z; `discoveredBackupCount` went 0 -> 14, and
`storageStats` corrected from a stale 18 snapshots / 3.86 GB to 247 / 24.3 GB). They are the
retained kopia snapshots of two deliberately removed apps - **9 from `downloads/autobrr`**
(`/pvc/autobrr`, `deletionPolicy: Retain`) and **5 from `ai/agentmemory`** (retired in #1527).
That is independent confirmation that both removals preserved their data exactly as
[`autobrr-removal-2026-09-02.md`](autobrr-removal-2026-09-02.md) predicted, and it is the first
time those snapshots have been visible in the catalog.

Only three object kinds were touched at all: the `ceph` `ClusterRepository` (patched live under
a Flux suspend, then reverted), the `ceph` `Maintenance` CR (annotated to request out-of-band
runs), and short-lived read-only diagnostic pods. `r2` was not modified.

## Live proof

`kopiur-repository` was suspended (`flux suspend ks kopiur-repository -n system`) for the
window, `ceph` patched live, and the Kustomization resumed afterwards - the repo's sanctioned
pre-merge validation flow. Out-of-band maintenance runs were requested with the operator's own
`run-requested`/`run-mode` annotations. Times are UTC on 2026-09-03.

| time | event | epoch 3 age (marker / **first blob**) | result |
|---|---|---|---|
| 04:38:35 | manual quick run | - | compacted epoch 1 -> `xs1`; **count 1851 -> 882** |
| 04:44:47 | `minDuration` patched to `5h` | - | **`kopia.repository` blob rewritten** - the parameter reached the repository, not just the CR status |
| 06:05:50 | - | - | condition returned to `False` as the open epoch refilled (expected transient) |
| 06:25:41 | scheduled quick run | 3.29h / **1.79h** | **negative control**: no advance, correctly under the gate on either model |
| 08:10:43 | manual quick run | 5.03h / **3.53h** | **the falsification**: no advance. Marker model predicted one; first-blob model did not |
| 08:17 | `minDuration` re-patched to `4h` | - | live `status.parameters` confirms `4h` |
| 08:40:13 | manual quick run | 5.53h / **4.02h** | **positive control**: `xe4` written - epoch 3 advanced |
| 08:41:33 | manual quick run | - | epoch 2 now settled -> compacted to `xs2` |
| 09:07:53 | health probe | - | **`indexBlobCount` = 144**, `IndexBlobHealth=True Healthy` |

**The headline number: 1851 -> 144.** Independently cross-checked against the bucket, which held
`xs0`+`xs1`+`xs2` (3) + `xn3` (128) + `xn4` (1) = 132 live blobs at 08:44, plus ~12 accumulated
by the 09:07 probe. The status field and the object listing were reconciled rather than either
being taken on trust.

**Why the positive control is decisive.** Epoch 3 advanced at **4.02h measured from its first
blob** (04:38:34). Under the `24h` default it could not have closed until 2026-09-04T04:38:34Z
at the earliest - twenty hours later. And it advanced at 4.02h from the *first blob*, not 4h
from the *marker* (which would have been 07:08:17), re-confirming mechanic 1 on a second epoch.

**What is NOT proven here.** The 1851 -> 882 drop at 04:38 was caused by the manual run
compacting epoch 1, not by any parameter change - the same compaction would have happened at the
next scheduled run regardless. Only the 08:40 advance and everything after it is attributable to
`minDuration`. The long-run steady state (~649 peak) is a projection from the simulation, not a
measurement; confirming it needs a few days of normal operation post-merge.

**Post-merge state, and a trap found on the way out.** Resuming the Kustomization reverted the
CR's `spec.parameters` to what `main` declares (absent) - but **the repository kept `4h`**.
Verified fully reconciled: `spec.parameters` empty, `generation` = `observedGeneration` = 7, and
`status.parameters.epoch.minDuration` still `4h`.

**Removing `spec.parameters` does not restore the default.** The CRD describes the block as
*"re-applied on bootstrap whenever they drift"*, and its `blobRetention` sibling spells the
semantics out: *"Absent means 'don't touch it'"*. So the field is effectively write-only from
Git's side - the operator asserts a declared value and leaves an undeclared one alone. Practical
consequences:

- The cluster is currently running `4h` while `main` declares nothing. That is drift, in the
  intended direction, and merging this change makes it declarative (a no-op against the live
  repository).
- **A `git revert` of this change would NOT restore `24h`.** It would only stop asserting `4h`,
  leaving the repository on `4h` silently. Backing this out for real means declaring
  `minDuration: 24h` explicitly and letting it apply, then removing the block in a follow-up.
- This is the same shape as the `LiteLLMVirtualKey` `rpmLimit` trap already recorded in
  `AGENTS.md`: dropping an optional field stops the operator asserting it but does not clear
  what is already set.

The apply path itself is proven twice over - the 04:44:47 and 08:17 patches both reached the
repository within 30s, the first rewriting the `kopia.repository` blob.
