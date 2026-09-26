# Hardware Incident Log

Tracked hardware and infrastructure incidents across cluster nodes. Each entry documents root cause, evidence, and resolution for future reference.

Current node memory (as of 2026-08-21): **96 GB per node** on all 3 Talos nodes. talos-1 Stick B RMA closed; see [2026-06-19].

---

## [2026-09-14] ALL 3 nodes — unannounced breaker-change power cut → Prometheus gap, kopiur movers killed mid-run; graceful shutdown runbook added

| Field | Value |
|-------|-------|
| **Node** | ALL 3 (talos-1/2/3) — every node is control plane and runs etcd; Ceph mon/OSD/mgr are also spread across all 3 |
| **Component** | Electrical (breaker), not hardware — mains power was cut to the whole cluster with no cordon, no drain, no Ceph `noout`, and no backup quiescing of any kind |
| **Affected service** | Prometheus TSDB (unflushed head), kopiur backup movers, several stranded pod/Job records |
| **Severity** | **high** — no permanent data loss and no Ceph/CNPG damage, but a real monitoring gap plus backup-credential cleanup that a graceful shutdown would have avoided entirely |

### Root cause

The captain cut power to the whole cluster to change a breaker. All three nodes lost power simultaneously — within **1.8 seconds** of each other — with nothing in the cluster told to stop first. This is electrically identical to yanking the plug on all three machines at once: no pod got a `SIGTERM`, no Ceph flag was set, and no in-flight kopiur/VolSync mover got a chance to finish or clean up after itself.

### Evidence

- **115 minutes of Prometheus data lost** (`21:30:53Z`–`23:25:53Z`) — the unflushed TSDB head block at the moment of the power cut.
- **~23 kopiur mover Jobs killed mid-run**, leaving **42 projected credential Secrets** behind for the operator to reap (see the `credentialProjection` mechanism in `kubernetes/components/kopiur/Readme.md` "Credentials" — each mover mints a short-lived Secret in the workload namespace and reaps it itself on a clean exit; a killed mover never reaches the reap step).
- **4 pods stranded** as `Init:ContainerStatusUnknown` orphan records.
- **2 Jobs left `Failed`.**
- A **false `critical` `KopiurProjectedCredentialsLeaking` page** (`min_over_time(kopiur_projected_secrets_live[13h]) > 0` — see `.claude/skills/kopiur-backups/SKILL.md`) was still firing hours later: the alert is correctly reporting real orphaned Secrets, but they are leftover mover debris from the unclean kill, not an active credential leak, and nothing auto-reaps them once the mover that owned them is gone.

**The same night's controlled counter-example proves a graceful path avoids all of it.** The planned `just talos upgrade-node talos-3` roll at `00:38Z` (closing the `pcie_port_pm=off` gap — see [2026-08-24] below) drains the node properly and produced **zero** Prometheus data loss, verified with `query_range count(up)` across a 90-minute window spanning the reboot. Same class of event (a node going away), opposite outcome, because one drained first and one did not.

### Impact

- ~2 hours of missing cluster-wide metrics history.
- Manual cleanup required: reap the 42 orphaned projected credential Secrets, clear the 4 orphaned `Init:ContainerStatusUnknown` pods and 2 failed Jobs, and silence/resolve the resulting `KopiurProjectedCredentialsLeaking` page once the Secrets are confirmed to be mover debris and not a real leak.
- No Ceph data damage (BlueStore/RocksDB are crash-consistent by design; see [2026-06-30] below for the same property holding across a much worse triple-node event) and no CNPG corruption — the risk here is entirely in things that only checkpoint cleanly on a **received** signal (Prometheus's TSDB head, a kopiur mover's own cleanup step), not in things that already tolerate a hard kill.

### Resolution

No live recovery beyond the routine cleanup above was needed. The durable fix is the runbook below: a clear, ordered pre-power-work shutdown checklist so the next planned electrical outage stops the cluster on purpose instead of by accident.

### Runbook: pre-power-work graceful shutdown and power-on

Everything that needs the Kubernetes API or Ceph mon quorum must happen **before** the first `talosctl shutdown` — once two of the three nodes are down, etcd and Ceph mon quorum are both gone (2-of-3 majority), and `kubectl`/`ceph` stop being reliable. `talosctl` itself keeps working throughout, because it talks to each node's own Talos API (`apid`) directly rather than through the Kubernetes control plane — that is what makes the shutdown sequence below safe to keep issuing after quorum is lost.

Live-checked against this cluster on 2026-09-14: 3/3 control-plane nodes, 3 etcd members, Ceph `HEALTH_OK` with 6/6 OSDs (2 per node, symmetric) and mon quorum `h,i,m`, and CNPG `postgres-17` running 3/3 instances one per node (primary on talos-1 at check time — it moves).

#### 0. Before touching any node (API/quorum still needed)

```sh
# Ceph must be healthy before you take anything down
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph -s        # expect HEALTH_OK, 6/6 OSDs up, quorum of 3 mons
task rook:check-osd-device-paths                                    # standing AGENTS.md reboot-safety gate

# Flux should not be mid-rollout on anything
flux get ks -A ; flux get hr -A                                     # nothing should be reconciling/failing

# Prevent a rebalance from starting while a node is briefly down mid-sequence
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd set noout
kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph osd dump | grep flags   # confirm noout is set

# Check for kopiur/VolSync activity already in flight
kubectl get snapshot -A -o json | jq -r '.items[] | select(.status.phase=="Running") | "\(.metadata.namespace)/\(.metadata.name)"'
kubectl get jobs -A -l app.kubernetes.io/created-by=volsync -o json | jq -r '.items[] | select(.status.active>0) | "\(.metadata.namespace)/\(.metadata.name)"'
# If either returns rows, wait for them to finish before proceeding (kopiur/VolSync runs are
# normally a few minutes) rather than shutting down through them.
```

**Why `noout` alone, not the fuller Ceph/Rook flag set.** Ceph's own docs (and Rook's node-maintenance guide) additionally list `norecover`, `nobackfill`, `norebalance`, `nodown`, and `pause` for a full-cluster shutdown, plus scaling every `rook-ceph-{osd,mon,mgr}` and CSI-provisioner Deployment to zero in a specific order. That fuller procedure is built for an outage measured in hours with the Ceph *deployments* themselves staying down. This cluster's own history already answers the question for a short, all-3-nodes-together outage: the 2026-07-03 full 3-node reboot recovery (see below) used `noout` alone — no deployment scaling — and came back to `HEALTH_OK` with all 6 OSDs up within ~60 seconds. `noout` is what stops OSDs going down from being marked "out" and triggering a rebalance; for a breaker-change window measured in minutes, not hours, that is proportionate. Reach for the fuller flag set only if the planned outage is going to last hours, not minutes.

**Prevent a new backup run from starting mid-sequence (recommended, not verified live).** kopiur and VolSync are both controller-runtime operators (`system/kopiur-controller`, `system/volsync`, 2 replicas each, Flux-managed `HelmRelease`s with a 1h reconcile interval) that create new Snapshot/mover Jobs on their own schedule watch. Scaling the operator itself to zero should stop any *new* run from starting during the shutdown window without having to touch every individual `SnapshotPolicy`/`SnapshotSchedule`:

```sh
flux suspend hr kopiur -n system  && kubectl -n system scale deploy/kopiur-controller --replicas=0
flux suspend hr volsync -n system && kubectl -n system scale deploy/volsync --replicas=0
```

**Unverified:** this was reasoned from the operator architecture (no running controller, no schedule evaluation, no new Job), not executed against the live cluster — that is a state-changing action out of scope for a read-only session. If it turns out not to hold, the per-claim fallback is already documented in `kubernetes/components/kopiur/Readme.md` ("Suspend" section: `kubectl -n <ns> patch snapshotpolicy <name> --type=merge -p '{"spec":{"suspend":true}}'`).

#### 1. Shut down two of the three nodes normally

Pick an order — it does not matter which two go first, since Ceph's CRUSH placement and CNPG's pod anti-affinity are symmetric across all 3 nodes (verified: 2 OSDs per node, one CNPG instance per node). Recommend `talos-1`, `talos-2`, saving `talos-3` for last since it is already the node that needs special handling on the way back up (its GPU dock).

```sh
talosctl -n 10.10.10.11 shutdown   # talos-1 — no --force
talosctl -n 10.10.10.12 shutdown   # talos-2 — no --force
```

**Do not use `just talos shutdown-node <node>` for this.** That recipe (`talos/mod.just`) hard-codes `talosctl shutdown --force`, and `--force` means exactly "shut down **without** a cordon/drain" (`talosctl shutdown --help`). That is the wrong tool for a planned shutdown — it is the same as the breaker cut this runbook exists to avoid, just issued from a keyboard instead of a breaker panel. Plain `talosctl shutdown` (no `--force`) cordons the node and drains it through the Kubernetes eviction API first, which is what let the [2026-08-24] `upgrade-node` roll finish with zero Prometheus loss. It also lets `postgres-17`'s own PodDisruptionBudget do its job: CNPG watches the node cordon via a dedicated Node reconcile watch (`ghcr.io/cloudnative-pg/cloudnative-pg:1.30.1`, source v1.30.1, `internal/controller/cluster_predicates.go:99-135 nodesPredicate`, `cluster_controller.go:1712-1743 mapNodeToClusters`, `replicas.go:69-83 reconcileTargetPrimaryFromPods`) and performs its own switchover to a replica on a still-healthy node *before* the drain's eviction call is retried. This mechanism is independent of `nodeMaintenanceWindow` and happens automatically on cordon. Verified live, the `postgres-17-primary` PDB currently allows **zero** direct evictions of the primary pod (`minAvailable: 1` with exactly one pod matching that role) — a plain drain only succeeds because this switchover happens first, not because the PDB is lenient. Each `talosctl shutdown` call blocks until that node is fully off (`--wait` defaults true) before you move to the next command.

#### 2. Shut down the third (last) node — do not rely on a plain drain here

The trick in step 1 only works because a healthy node is still standing by to receive the CNPG switchover. On the last node there is nowhere left to switch over to, so `postgres-17-primary`'s PDB (and `rook-ceph-mon-pdb`, once down to one surviving mon) become **unsatisfiable**, not just temporarily blocked. A plain eviction-based drain does not hang forever on this — Talos does not strictly enforce PDBs during its own drain step (an open upstream report, siderolabs/talos#9882, describes a blocked eviction timing out after ~5 minutes and the operation proceeding anyway; **unverified on this cluster's Talos version (v1.13.10) — that issue was filed and last touched against 1.7–1.8.3**) — but that path gives the stranded pod an ordinary, uncoordinated termination rather than the clean one CNPG is built to perform, which is exactly the outcome this runbook is trying to avoid.

The last node's CNPG instance is therefore left to take an ordinary, uncoordinated termination - via Talos's own drain/kubelet `SIGTERM` path, or (per siderolabs/talos#9882 above) an abrupt kill if that path doesn't complete before power-off - rather than a coordinated switchover-then-delete. This is an **accepted residual risk, not an oversight**: `postgres-17` is a 3-instance CNPG cluster replicated across all 3 nodes with continuous WAL archiving to the barman object store (`kubernetes/apps/base/database/cloudnative-pg/cluster-17/cluster-17.yaml`). By the time only the last node's instance remains, the other two replicas are already powered off anyway, so there is no "protect a surviving replica" case left to guard on the primary side - the actual guarantee for this data comes from WAL archiving having already completed up to that point, not from how the final in-memory instance's process happens to stop. An ordinary termination of one already-isolated instance is a materially smaller risk than what a manual quiesce step would be protecting against (an uncoordinated kill of the *active, in-quorum* primary while a replica could still receive a clean handoff) - and that risk is still fully closed for the first two nodes shut down in step 1, where a real switchover target exists.

```sh
talosctl -n 10.10.10.13 shutdown   # talos-3 - no --force
```

**Known gap (2026-09-15, accepted by the captain):** the last node's database instance is not specially quiesced before this shutdown. The CNPG cordon-triggered switchover mechanism described above is **confirmed** and works as documented — but it cannot quiesce the last node. By the time only the last node's instance remains, the other two replicas are already powered off, so there is nowhere to switch over to. The mechanism that makes steps 1 and 2's drains succeed (CNPG watches Node `spec.unschedulable` and switches to a healthy replica on another schedulable node) has no replica target on the last node. Encoding a switchover into an emergency shutdown procedure for the node where it cannot occur would just add complexity and failure modes to an already-constrained situation — so this is deliberately left as an accepted residual risk rather than a gap in CNPG's behavior itself.

#### 3. Power is now safe to cut

All three nodes are fully powered off (`talosctl shutdown` without `--debug`/`--wait=false` blocks until the node is down). The breaker work can proceed — it is now de-energizing already-halted hardware, not live-running services.

#### 4. Power-on order

```
1. GPU/dock PSU for talos-3 ON first. Wait 5-10s, confirm fans spin / card LED lit.
   (Unchanged rule from [2026-08-24] below — pcie_port_pm=off closes the runtime-PM
   race but does not remove this order; see the confirmation entry under that incident.)
2. Power on talos-1 and talos-2 (either order, in parallel with step 1's wait) —
   2-of-3 is what restores etcd and Ceph mon quorum.
3. Only after step 1's wait has elapsed, power on talos-3.
4. kubectl get nodes                          # wait for all 3 Ready
5. ceph -s                                    # HEALTH_OK, 6/6 OSDs up, 3/3 mons in quorum
6. ceph osd unset noout
7. flux resume hr kopiur -n system            # restores Git-declared replica count itself
   flux resume hr volsync -n system
8. kubectl -n database get cluster postgres-17   # "Cluster in healthy state", 3/3 ready
9. flux get ks -A ; flux get hr -A            # confirm nothing stuck from the outage
10. kubectl get nodes -o custom-columns='NAME:.metadata.name,B70:.status.allocatable.devic\.es/b70'
    # expect 99 on talos-3 — standard post-any-GPU-touch check (media-stack.md)
11. Check Alertmanager for anything that should have self-cleared once movers resumed
    normally (e.g. no repeat KopiurProjectedCredentialsLeaking, since nothing was
    killed mid-run this time).
```

**Why no `just` recipe for this.** The sequence is long, but every judgment call in it (is Ceph actually healthy, is anything still `Running` that should be waited out) reads better as visible `kubectl`/`ceph` output an operator checks between steps than as a script's exit code. This is also infrequent, planned work, not a repeated operation, so it does not clear the bar for baking into automation.

### Lessons

- **A cordon+drain-based shutdown (`talosctl shutdown`, no `--force`) is not optional ceremony — it is the entire difference between this incident and the zero-data-loss `upgrade-node` roll the same night.** Same class of event, opposite outcome.
- **`just talos shutdown-node` is the wrong default for planned work.** It hard-codes `--force`, i.e. "skip the drain" — appropriate for an already-unresponsive node, not for taking a healthy cluster down on purpose.
- **A PodDisruptionBudget that looks like it should block a drain forever usually doesn't — because the thing it is protecting reacts to the cordon and steps aside first.** CNPG's primary-protecting PDB is the clearest example: it looks unsatisfiable read in isolation (`ALLOWED DISRUPTIONS: 0`), but only because a switchover is expected to happen ahead of the eviction, not because the primary is supposed to be undrainable.
- **The last node in any "take the whole thing down" sequence is categorically different from the others** — there is no peer left to switch over to or reschedule onto. For CNPG specifically that residual risk is accepted rather than mitigated by hand (see the "Known gap" note in step 2 above): a confirmed cordon-reactive switchover mechanism doesn't exist on this cluster to build a safe manual quiesce on, so encoding one would just be a different, silent way to fail.

---

## [2026-08-24] talos-3 — Arc Pro B70 disappears from PCIe bus → root port stuck D3hot

| Field | Value |
|-------|-------|
| **Node** | talos-3 (10.10.10.13) — Meigao Venus (Minisforum MS-01, board `AHWSA`), root port `0000:00:01.0` (CPU PEG, physical slot 1, OCuLink to an external dock/PSU) |
| **Component** | PCIe root port runtime power management (Linux, not BIOS) — the port was runtime-suspended to **D3hot 218 ms after boot** while bus 01 was still empty, so a card that trained late was never rediscovered |
| **Affected service** | `gpu.intel.com/xe` allocatable on talos-3: 99 → 0; `jellyfin`/`plex`/`tdarr-node`/`vllm`/`comfyui` pods Pending |
| **Severity** | **high** — 5 GPU pods Pending; no data risk, no other node affected; **discovered late**, same as the 2026-06 incident below — closed by `B70GpuLost`/`XeGpuLost` in `kubernetes/apps/base/monitoring/kube-prometheus-stack/app/alerts/gpu-loss.yaml` |

### Root cause

Same platform pathology as every other Meigao Venus incident in this log (`i915` GuC race, NVMe/PCIe ASPM): **a PM mechanism wins a race against the device it is managing.** This time it is Linux **runtime PM**, one layer above the BIOS-level PM already worked around with `pcie_aspm=off`.

At boot the link was down (`DLLLA=0`); the kernel's `pcie_failed_link_retrain()` quirk retrained at 2.5 GT/s, failed, and reverted the target speed. The scan of bus 01 found nothing. **218 ms in, with zero children, Linux runtime-suspended `00:01.0` to D3hot** (`power/control=auto`). Sometime after that the card actually finished training — full register decode showed it sitting at **Gen4 x4 with 8 GT/s equalization complete (all 3 phases) and `RemoteDLFSupportedValid=1`**, i.e. a real link partner, alive, on the far end. Nothing could ever notice: `SLTCAP HotPlugCapable=0`, `SLTCTL=0x0000` (every hotplug/PDC/DLLSC interrupt disabled), and the only PCIe port service bound anywhere on the box is bandwidth control (`pcie010`) — there is no mechanism in this system that discovers a card arriving after the port has gone to sleep. A same-PDU host power cycle on 2026-08-24 did not recover it: this port has **no power controller** (`SLTCAP PowerController=0`), so cycling the host never cycles the card's own PSU.

### Evidence

Two PCIe register signatures distinguish this from the [2026-06-10] bus-number incident below — do not confuse them:

```
This incident (late enumeration / runtime-PM race):
  dmesg (boot):  pci 0000:00:01.0: broken device, retraining non-functional downstream link at 2.5GT/s
                 pci 0000:00:01.0: busn_res: [bus 01-fe] end is updated to 01
  live decode:   power_state=D3hot  runtime_status=suspended  runtime_active_time=218ms
                 LNKSTA: CurSpeed=16GT/s(4) NegWidth=x4  DLLLA=1  LNKSTA2 EQ8GT_Complete=1 (Ph1/2/3=1)
                 SLTSTA: PresenceDetectChanged=1 DataLinkLayerStateChanged=1  SLTCAP HotPlugCapable=0
```

Full remote-probe evidence, register decode, and confidence-ranked causal analysis: dossier scout report (2026-08-26), summarized in the PR that added this doc entry and the `XeGpuLost`/`B70GpuLost` PrometheusRule (`kubernetes/apps/base/monitoring/kube-prometheus-stack/app/alerts/gpu-loss.yaml`).

### Impact

- 5 GPU-scheduled pods Pending (`jellyfin`, `plex`, `tdarr-node`, `vllm`, `comfyui`) — no scheduling fallback since `gpu.intel.com/xe`/`devic.es/b70` have no CPU-only equivalent.
- No Ceph/data impact — talos-3's OSDs are on separate PCIe root ports (`00:06.0`/`00:07.0`), unaffected.
- **Discovered late** (a human noticed something else) — same detection gap as the 2026-06 incident, closed by `B70GpuLost`/`XeGpuLost` in `gpu-loss.yaml`.

### Resolution

**Power-on order is the recovery** when the card is already gone (external corroboration: RIITOP OCuLink eGPU Dock FAQ, Minisforum DEG1 guidance - both name "dock PSU first" as the standard remedy for exactly this symptom). The durable race mitigation is `pcie_port_pm=off` below; it does not replace this order once the card is lost:

1. Power talos-3 fully **off** (`talosctl reboot` never touches the dock's PSU — it must be a full power-off).
2. **GPU/dock PSU ON first.**
3. **Wait ~5-10 seconds.** Confirm GPU fans spin and any card LED is lit.
4. **Only then power on talos-3.**
5. Never power the dock and host together, and never power the dock after the host.

Verify recovery: `kubectl get nodes -o custom-columns='NAME:.metadata.name,XE:.status.allocatable.gpu\.intel\.com/xe,B70:.status.allocatable.devic\.es/b70'` expects `99`/`99` on talos-3; NICs moving back to `enp6s0f*` is itself confirmation the switch reclaimed its bus numbers (see the 2026-06-10 entry). Before powering talos-3 back on, follow the Ceph reboot-safety rule in `AGENTS.md` (`HEALTH_OK` + `task rook:check-osd-device-paths` first, one node at a time) — talos-3 hosts live OSDs.

### Confirmed recovered (2026-08-26)

**The power-on order above was performed and it worked.** talos-3 booted at **2026-08-26T11:21:12Z** with the B70 enumerated, and the card has run stably under production load for the three days since (uptime at time of writing 3.03 d, matching that boot). Every failure signature in the Evidence table is cleared:

| Signature at failure | State after recovery |
|---|---|
| `8086:e2ff` absent | present at `01:00.0` |
| `8086:e223` absent | present at `03:00.0`, `DRIVER=xe` |
| `00:01.0` `power_state=D3hot` / `runtime_status=suspended` | `D0` / `active` |
| `runtime_active_time=218` ms | full uptime, `runtime_suspended_time=0` (never suspended this boot) |
| bus 01 empty, `busn_res ... end is updated to 01` | `[bus 01-04]` populated, `subordinate_bus_number=4` |
| NICs at `enp3s0f*` | back to `enp6s0f0np0` / `enp6s0f1np1` |

It is not merely enumerated but working: hwmon `xe` reports 69 °C package / 68 °C VRAM at 2388 RPM, with a live draw of **182 W** derived from the `energy1` counter, and `vllm` decoding tokens on it. `devic.es/b70` is back to `99` on talos-3 and all five previously-Pending GPU pods are Running.

Note the recovery boot was the `just talos apply-node talos-3` maintenance for #1444 (`68c8e2c6`, restoring `siderolabs/i915` firmware), 41 minutes after that commit. Neither that PR's kernel args nor #1443's can explain it: enumeration is a *pre-driver* event and both args target the **iGPU** `a7a0`, not the discrete `e223`. The dock-PSU-first order is the credible cause. The physical sequence cannot be proven from remote telemetry; what is proven is that the card returned at that boot and has been stable since.

### Mitigation: `pcie_port_pm=off`

`talos/schematic.yaml.j2` carries `pcie_port_pm=off`, which closes the race itself rather than working around it. `pcie_port_pm_setup()` sets `pci_bridge_d3_disable`, making `pci_bridge_d3_possible()` return `false` for `PCI_EXP_TYPE_ROOT_PORT`, so `portdrv` never calls `pm_runtime_allow()` on `00:01.0` and the port cannot runtime-suspend to D3hot while it waits for a slow card. Verified against Linux v6.18 (nodes run `6.18.44-talos`): `Documentation/admin-guide/kernel-parameters.txt:5082` (`off` = "Disable power management of all PCIe ports"), `drivers/pci/pci.c:173` and `:2998`, `drivers/pci/pcie/portdrv.c:719`.

**The pre-existing `pcie_aspm=off` does not cover this and never did.** It governs ASPM *link* power states; runtime D-state suspension of the *port* is a separate mechanism one layer up. That distinction is exactly why this failure survived an argument that looks like it should have caught it.

Be clear about the limits of this mitigation:

- It **prevents the race on future boots**. It is **not retroactive** and does nothing for the current boot until the node reboots into an image built from the new schematic.
- It **does not remove the need for the power-on order**. Once a card has already been lost, `pcie_port_pm=off` cannot recover it: the port still has `SLTCAP PowerController=0`, so a host power cycle still never cycles the dock's PSU. The Resolution steps above remain the recovery procedure.
- The schematic is **cluster-wide**, so all three nodes get the argument even though only talos-3 has the affected card. The cost is marginally higher idle power from PCIe ports staying in D0.

### Runbook: attended reboot to activate `pcie_port_pm=off`

**Kernel args ride in the factory schematic, not in machine config, so `just talos apply-node` cannot activate this.** `apply-node` only stages config; the argument lands only when the node boots an image built from the new schematic. That is `just talos upgrade-node talos-3`, which resolves the new schematic ID from `talos/schematic.yaml.j2` via `factory.talos.dev` and runs `talosctl upgrade -i <image> -m powercycle` (`talos/mod.just`). The schematic ID is derived automatically, but the render path needs `vals`/1Password context.

**This reboot is the risk event.** It is also when talos-3 picks up `e22cfb20` (#1479, dropping `siderolabs/thunderbolt`), which is already on `main` and rides the same image. The two were deliberately sequenced into one maintenance window: do not split them into two reboots.

#### 0. What this reboot changes

talos-3 is currently two schematics behind, and this one reboot closes both gaps:

| Schematic | Contents |
|---|---|
| `b1a6b2ff…` | **live on talos-3 today** (#1444). `thunderbolt` still loaded, no `pcie_port_pm` |
| `7f25ace8…` | `main` after `e22cfb20` (#1479): `siderolabs/thunderbolt` dropped |
| `a46161e7…` | this change: thunderbolt dropped **and** `pcie_port_pm=off` |

`upgrade-node` computes the target ID itself from the template, so these are for
confirmation only. Recompute after any edit to `schematic.yaml.j2` the same way
`_schematic-id` does:

```sh
just template talos/schematic.yaml.j2 \
  | curl -sX POST --data-binary @- https://factory.talos.dev/schematics | jq -r .id
```

#### 1. Before: Ceph safety gate

talos-3 hosts live OSDs (2 and 4). Per `AGENTS.md`, confirm `HEALTH_OK` and run `task rook:check-osd-device-paths` first, and roll one node at a time.

#### 2. Before: capture the PCIe baseline

Capture this **while the card is still up**, so there is something to compare against afterwards. All reads are read-only.

```sh
export TALOSCONFIG=talos/talosconfig
N=10.10.10.13   # talos-3

talosctl -n $N read /sys/bus/pci/devices/0000:01:00.0/device                       # 0xe2ff  (on-card switch)
talosctl -n $N read /sys/bus/pci/devices/0000:03:00.0/device                       # 0xe223  (Arc Pro B70)
talosctl -n $N read /sys/bus/pci/devices/0000:03:00.0/uevent                       # DRIVER=xe
talosctl -n $N read /sys/bus/pci/devices/0000:00:01.0/power_state                  # D0
talosctl -n $N read /sys/bus/pci/devices/0000:00:01.0/power/runtime_status         # active
talosctl -n $N read /sys/bus/pci/devices/0000:00:01.0/power/runtime_suspended_time # 0
talosctl -n $N read /sys/bus/pci/devices/0000:00:01.0/subordinate_bus_number       # 4
talosctl -n $N read /proc/cmdline | tr ' ' '\n' | grep pcie                        # no pcie_port_pm yet
kubectl get nodes -o custom-columns='NAME:.metadata.name,XE:.status.allocatable.gpu\.intel\.com/xe,B70:.status.allocatable.devic\.es/b70'
```

#### 3. The upgrade

```sh
just talos upgrade-node talos-3
```

#### 4. If the card does not come back

**A host powercycle does not cycle the dock's PSU** (`SLTCAP PowerController=0`), so `-m powercycle` alone cannot guarantee the dock restarts in the right order. If the B70 is absent after the reboot, the recovery is the documented power-on order, unchanged:

1. Power talos-3 fully **off**.
2. **Dock/GPU PSU ON first.**
3. **Wait 5-10 seconds.** Confirm GPU fans spin and the card LED is lit.
4. **Only then** power on talos-3.

Never power the dock and host together, and never host-first.

#### 5. Verify success

```sh
talosctl -n $N read /proc/cmdline | tr ' ' '\n' | grep pcie_port_pm   # pcie_port_pm=off  <- arg took effect
talosctl -n $N read /sys/bus/pci/devices/0000:01:00.0/device          # 0xe2ff
talosctl -n $N read /sys/bus/pci/devices/0000:03:00.0/device          # 0xe223
talosctl -n $N read /sys/bus/pci/devices/0000:03:00.0/uevent          # DRIVER=xe
talosctl -n $N read /sys/bus/pci/devices/0000:00:01.0/power_state     # D0  <- port stayed awake
talosctl -n $N read /sys/bus/pci/devices/0000:00:01.0/power/runtime_suspended_time  # 0
kubectl get nodes -o custom-columns='NAME:.metadata.name,XE:.status.allocatable.gpu\.intel\.com/xe,B70:.status.allocatable.devic\.es/b70'
```

Expect `99`/`99` for talos-3. Also confirm `siderolabs/thunderbolt` is gone (`talosctl -n $N get extensions`) and that the five GPU pods (`vllm`, `tdarr-node`, `jellyfin`, `plex`, `comfyui`) reschedule.

#### 6. Record the result

**This reboot doubles as the test of whether the mitigation works.** The interesting signal is `00:01.0` staying in `D0` with `runtime_suspended_time=0` through a boot where the card trains late. Record the outcome back into this entry either way - a success confirms the fix, and a failure means the D3hot race was not the whole story and the investigation reopens.

### Activated and confirmed working (2026-09-15)

**The runbook was executed 2026-09-15 ~00:38-00:46Z, firstmate-executed with the captain present**, the same night as the [2026-09-14] unannounced power cut above (that event happened first, hours earlier; this was the already-planned, separately-attended `upgrade-node` maintenance). `just talos upgrade-node talos-3` moved the node from schematic `b1a6b2ff…` straight to `a46161e7…` in **one** reboot, closing both gaps the runbook set out to close (thunderbolt drop + `pcie_port_pm=off`) in a single pass.

Verified live post-reboot:

| Check | Result |
|---|---|
| `pcie_port_pm=off` on `/proc/cmdline` | present |
| `siderolabs/thunderbolt` extension | gone |
| `8086:e2ff` / `8086:e223` (B70) | both present, `DRIVER=xe` |
| `00:01.0` power state | `D0`, `runtime_status=active`, `runtime_suspended_time=0` |
| `devic.es/b70` allocatable | `99` on talos-3 |

**The B70 came back with no physical intervention** — no dock-PSU-first sequencing was needed this time; every signature above matches the pre-roll baseline exactly. Ceph dipped to 4/6 OSDs (33% degraded) during the roll and self-healed to `HEALTH_OK`, 6/6, 393 `active+clean` in about nine minutes; Flux had nothing unready afterwards; `vllm` reloaded the 35B model cleanly with zero restarts.

**Important nuance — this does not close the question the runbook was testing.** The unattended power cut hours earlier ([2026-09-14] above) also power-cycled talos-3, *without* `pcie_port_pm=off` live yet, and the card returned then too. So the night produced **two** survivals of a talos-3 power cycle, not one — and neither proves the timing-dependent D3hot race is closed, since both happened to land on the lucky side of the race by chance rather than by the mitigation being present (the mitigation was only live for the second one). Read this as "the fix has not yet been disproven," not "the problem is solved." The dock-PSU-first power-on order in the Resolution section above remains the documented recovery and is unchanged by either survival — keep following it.

### Lessons

- **This is the same "PM wins the race" pathology as every prior Meigao Venus incident** ([2026-04-14] iGPU GuC race, 2026-03-16 NVMe/ASPM) — one layer up the stack, from BIOS PM to Linux runtime PM. `pcie_aspm=off` does not cover runtime D-state suspension of an idle port; they are different mechanisms.
- **Both GPU losses on this node were discovered late** by a human noticing something else, with no alert ever firing. Closed by `B70GpuLost`/`XeGpuLost` in `gpu-loss.yaml` — `kube_node_status_allocatable{resource="devic_es_b70"|"gpu_intel_com_xe"}` dropping to 0 for 5m now pages.
- **A root port with `HotPlugCapable=0` can never self-heal from a late-training card.** Once such a port runtime-suspends before its device answers, only a full power cycle (in the correct order) recovers it — a warm `talosctl reboot` will not, since it never touches the dock's PSU.

---

## [2026-06-30] talos-1 + talos-2 — OOMController kill storm → 4-OSD outage / 100% PG inactive

| Field | Value |
|-------|-------|
| **Node** | talos-1 (10.10.10.11) + talos-2 (10.10.10.12) at onset; the 2026-07-03 recovery-day kill storms hit **ALL 3 nodes** |
| **Component** | **Talos v1.12 userspace OOM controller (`runtime.OOMController`)** — default PSI trigger far too aggressive for Ceph-hosting nodes. **NOT hardware**: RAM/disks/stores exonerated (zero corruption signatures through ~6 kill/restart cycles + reboots) |
| **Affected service** | 4/6 OSDs down (osd.0 + osd.3 on talos-1; osd.5 + osd.6 on talos-2) → **100% PGs inactive** (393/393, 66.667% objects degraded) → ALL RBD/CephFS client IO frozen ~3 days; MDS metadata IO blocked >37 h; collateral kills of cilium-agent, kube-apiserver, radarr; cluster-wide kubelet/D-state wedge |
| **Severity** | **critical** — total storage outage for ~3 days, ran **UNDETECTED** (no alerts fired: node-exporter was disabled and VictoriaMetrics not deployed — monitoring was blind to node memory/PSI and to the OSD pod deaths) |

### Timeline (UTC)

- **2026-06-30 ~17:07** — simultaneous OOMController kill activity on talos-1 + talos-2. osd.0 and osd.3 (talos-1) exit 137 at 17:08:26–28Z; talos-2 destabilized (osd.6 fast-shutdown on 07-01; osd.5 wedged `Init:0/5` for 2d21h).
- **06-30 → 07-03** — **100% PGs inactive** (347 undersized+degraded+peered + 46 undersized+peered), MDS slow metadata IO blocked >134,000 s (~37 h). Nobody noticed: no node metrics, no alerts, VictoriaMetrics scrapes died with the storage.
- **07-03 ~13:30–13:38** — recovery starts: `noout` set; talos-1 emergency load shed (crash-loopers scaled to 0, dead pods purged — see `talos1-shed-load` record); last pre-shed OOMController kill 13:36:13Z (cilium-agent ×4 → ~40 s VIP outage).
- **07-03 afternoon** — osd.3/osd.0 pod restarts hit the **RBD/activate circular deadlock**: ceph-volume device scan parked in kernel D-state on frozen `/dev/rbdX` devices (kernel-proven fd/stack evidence) — OSDs can't start because storage is dead, storage is dead because OSDs can't start. A **brief blockpool `min_size=1`** window broke the loop; 4 OSDs came up briefly.
- **07-03 14:46–15:03** — **OOMController kill storms on ALL 3 nodes** (trigger fired ~every 1 s): victims = cilium-agent (talos-1/2), radarr (talos-3), kube-apiserver-talos-2; collateral = all 6 OSD pods crashed, EPERM device-cgroup errors + runc sandbox-race containerd damage.
- **07-03 15:04** — live `OOMConfig` patch (trigger `memory_full_avg10 > 50.0`, holdoff `30s`) applied to all 3 nodes via `talosctl patch mc` → **zero OOMController events from then on**.
- **07-03 15:04–~19:50** — cluster-wide kubelet wedge remained: talos-2 kubelet D-state in `Stopping` >1 h, SIGKILL-immune; even force-unmap of rbd devices blocked in-kernel waiting on the dead OSDs.
- **07-03 ~19:50** — **user-initiated full reboot of all 3 nodes** cleared all D-state/debris: **all 6 OSDs up in ~60 s** (on their baked device paths — no rook#17224 drift), all PGs active, `noout` unset ~20:15, **HEALTH_OK**; backfill of the 4.3% degraded remainder completed normally.
- **Aftermath** — external-secrets 2.7.0 + cert-manager v1.20.3 upgrades (frozen mid-outage) completed after the unfreeze; Flux fully reconciled: **122/122 Kustomizations + 103/103 HelmReleases Ready**.

### Root cause

**The Talos v1.12 OOMController default PSI trigger (`memory_full_avg10 > 12.0 && d_memory_full_avg10 > 0.0 && time_since_trigger > 500ms`) is far too aggressive for Ceph nodes.** Memory-PSI spikes are *normal* here (OSD peering/backfill, BlueStore cache churn, recovery IO); the controller answered them by SIGKILLing the heaviest **Burstable** cgroups. The victim-ranking expression never selects Guaranteed cgroups — so cilium-agent, the OSD pods, and kube-apiserver (all Burstable) were the perpetual victims, and every kill made the pressure worse.

Two amplifiers made the PSI spikes pathological:

1. **Dead-container memory debris** — after the 06-30 kills, ~24 GiB (talos-1) and ~70 GiB (talos-2) of reclaimable slab + reparented page cache from dead OSD containers stayed pinned in `kubepods/burstable`. MemFree sat at the watermark (~0.4–2 GiB) while MemAvailable showed 26–72 GiB; every allocation burst forced slow direct reclaim through the debris → PSI `full avg10` 18–46% → trigger fired ~every second (the 07-03 storms).
2. **talos-1's reduced-RAM window** (48 GB single stick until the RAM RMA; **completed 2026-08-21**, all 3 nodes now 96 GB): least headroom at the time, first to trip; its kube-apiserver (646 restarts) and cilium-agent (503 restarts) were chronic victims.

**RULED OUT:** MGLRU regression (`/sys/kernel/mm/lru_gen/enabled = 0x0000` verified on all 3 nodes — PR #1094 held); rook#17224 device-path drift (never materialized — every OSD re-activated on its baked `ROOK_BLOCK_PATH`, including after the full reboots); store corruption / bad hardware (zero corruption signatures across all 6 OSDs through ~6 kill cycles + reboots; SMART/dmesg clean).

### Evidence

```
Default trigger:  memory_full_avg10 > 12.0 && d_memory_full_avg10 > 0.0 && time_since_trigger > 500ms
Victim ranking:   memory_max.hasValue() ? 0.0
                  : {Besteffort:1.0, Burstable:0.5, Guaranteed:0.0, Podruntime:0.0, System:0.0}[class]
                    * memory_current
                  -> Guaranteed cgroups are NEVER selected; big Burstable ones always are

Pre-recovery snapshot (2026-07-03 13:32 UTC):
  osd: 6 osds: 2 up (since 37h), 4 in — hosts talos-1 + talos-2 down
  pgs: 100.000% pgs not active; 393 inactive (347 undersized+degraded+peered)
  2060444/3090666 objects degraded (66.667%)
  MDS: slow metadata IOs blocked >30s, oldest blocked for 134362 secs (~37h)

talos-1 memory anatomy (07-03 13:35 UTC): TOTAL 47.9Gi, FREE ~500Mi, AVAILABLE 26Gi
  Inactive(file) 24.9Gi vs Cached 1.06Gi -> ~24Gi reparented dead-container cache pinned in
  kubepods/burstable (cgroup MemCurrent 30Gi vs live children ~6Gi); containerd RSS 9.5Gi
07-03 kill storms 14:46–15:03 (all 3 nodes): PSI full avg10 18–46% -> kills ~every 1s
  victims: cilium-agent (talos-1/2), radarr (talos-3), kube-apiserver-talos-2;
  collateral: all 6 OSD pods crashed (EPERM device-cgroup + runc sandbox-race damage)
Chronic talos-1 victims: kube-apiserver 646 restarts, cilium-agent 503 restarts
After OOMConfig patch (15:04, trigger 50%/30s): ZERO OOMController events through
  full recovery + backfill on all 3 nodes
```

### Impact

- **Total storage outage ~3 days** — 100% PGs inactive, every RBD/CephFS consumer frozen (dozens of pods in Init/Error/CrashLoop cluster-wide); MDS blocked >37 h.
- **No data loss, no corruption** — replica-3 held; backfill (4.3% degraded at reboot) completed normally to HEALTH_OK.
- **D-state avalanche**: kernel Ceph clients + dead cluster parked processes in uninterruptible D-state (ceph-volume scans, kubelet, sync); SIGKILL cannot reap D-state — talos-2's kubelet wedged in `Stopping` >1 h and even rbd force-unmap blocked in-kernel.
- Secondary damage from the kill storms: containerd state damage (EPERM device-cgroup, runc sandbox races), a ~40 s control-plane VIP outage (cilium kill), restart-counter churn in the hundreds.
- **Detection failure**: with node-exporter disabled and VictoriaMetrics not deployed, a total-storage outage produced zero alerts for ~3 days.

### Resolution

**Recovery (2026-07-03, executed):** talos-1 load shed → RBD/activate circular deadlock broken with a **brief blockpool `min_size=1`** window (used twice, blockpool only, **restored to `min_size=2`** both times) → live `OOMConfig` patch (trigger `memory_full_avg10 > 50.0 && d_memory_full_avg10 > 0.0 && time_since_trigger > duration("30s")`) applied 15:04 UTC to all 3 nodes → storms stopped → remaining D-state/kubelet wedge cleared by **user-initiated full reboot of all 3 nodes (~19:50 UTC)** → all 6 OSDs up in ~60 s, all PGs active, HEALTH_OK, backfill completed. `noout` set 13:30 → unset ~20:15 UTC (approx).

**Durable fixes (in GitOps):**

- **OOMConfig codified in `talos/machineconfig.yaml.j2`** (the live `talosctl patch mc` survives reboot but NOT a config re-render — codifying it was mandatory).
- **cilium-agent → Guaranteed QoS** (requests == limits) — ranking weight 0.0, network plane can no longer be the OOM victim.
- **`NotIn [talos-1]` node affinities on 5 heavy burstables** (coder, changedetection, rsshub, readarr, seerr) - **RAM RMA complete 2026-08-21 (96 GB dual-channel restored on all 3 nodes).** Revert complete: cleared in 93fa733d (#1393). The same commit also cleared the identical affinity from three apps outside this list: agentmemory, radarr (distinct from readarr above), and - partially, it kept a talos-3-only exclusion - sabnzbd. flaresolverr and emqx-exporter never carried this affinity.
- **node-exporter re-enabled + PrometheusRule alerts**: originally `NodeMemoryPressure` (renamed 2026-08-23 from `Talos1MemoryPressure` once the RAM RMA made all 3 nodes identical), `NodeMemoryPSIHigh`, `CephOsdPodTerminalError`, `CephPodCrashLooping`. The first two still could not detect the controller itself (MemAvailable stayed high while PSI killed; the PSI pair's `for:` windows were longer than real bursts), so on 2026-09-06 they were re-based onto measured PSI thresholds and joined by `NodeOOMControllerKill` — current expressions and the traps that defeated the originals live only in `kubernetes/apps/base/monitoring/kube-prometheus-stack/app/alerts/node-memory-pressure.yaml`.
- **ceph-mon → Guaranteed QoS** (mon + logcollector requests == limits, 2Gi/500m) - mon quorum can no longer be the OOM victim. OSDs stay Burstable for now: Guaranteed would have reserved 14Gi×2 on talos-1's 48 GB single-stick window (RMA complete 2026-08-21; all 3 nodes now 96 GB). **TODO (post-RMA): promote `resources.osd` to 14Gi==14Gi Guaranteed; evaluate MDS (blocked on `mds_cache_memory_limit` 8Gi - Guaranteed at 10Gi×4 pods is unaffordable, and a tighter limit risks cgroup OOM against the cache; standby-replay makes MDS kills tolerable meanwhile).** *(This TODO was acted on 2026-08-22 but never actually reached Guaranteed - the `osd` container has no cpu limit, so the pod stayed Burstable regardless of the memory numbers - and the direction has since reversed: the request was cut to 12Gi on 2026-09-20. OSDs remain, and are expected to remain, Burstable. Current state and reasoning: [`ceph-cluster-changelog.md`](./ceph-cluster-changelog.md).)*

Cross-reference: [`ceph-cluster-changelog.md` [2026-07-03]](./ceph-cluster-changelog.md) for the OOMConfig change record, the min_size=1 windows, and the noout window.

### Lessons

- **PSI-based userspace OOM killers must be tuned for storage nodes** — Ceph's normal peering/backfill/cache churn looks like "memory stall" to a 12% PSI trigger; defaults tuned for desktops/generic workloads will kill the storage daemons that are the *cause and cure* of the pressure.
- **Dead-container debris after mass kills poisons reclaim** — reparented page cache + slab pinned in `kubepods/burstable` keeps MemFree at the watermark, making every subsequent allocation a slow direct-reclaim → PSI spiral → more kills. Kill storms are self-amplifying.
- **Kernel Ceph clients + a dead cluster = D-state avalanche** — even force-unmap blocks in-kernel waiting on dead OSDs; SIGKILL is useless. Blocklisting or a node reboot are the only fences.
- **A monitoring gap let a total-storage outage run undetected for ~2+ days** — node metrics and OSD-pod-state alerts are not optional on a storage cluster.
- **Reboot-phobia proved unfounded** — the rook#17224 device-path fear had frozen node reboots for weeks, but fresh boots re-activated every OSD cleanly on the first try. A timely reboot would have shortened this incident dramatically.

---

## [2026-06-19] talos-1 — faulty DDR5 SODIMM (stuck data bit) → silent Ceph store corruption

| Field | Value |
|-------|-------|
| **Node** | talos-1 (10.10.10.11) — Meigao Venus, Raptor Lake i9-13900H, 96 GB (2× Crucial **CT48G56C46S5.M16B** 48 GB DDR5 SODIMM @ 5186 MT/s, x2 channel) |
| **Component** | **System memory** — one faulty 48 GB DDR5 SODIMM ("Stick B"; defective module, stuck bits 2 and 17 under different test patterns). **✅ ISOLATED 2026-06-20** by same-slot A/B swap — IMC + slot + the other stick ("Stick A") all cleared |
| **Affected service** | osd.0 (BlueStore store corruption, repeated) + mon.l (RocksDB store corruption) — any talos-1 Ceph daemon buffering through the bad region |
| **Severity** | **high** — confirmed hardware fault driving repeated silent corruption on talos-1; data contained by replica-3 + csum (no PG damage), but it triggered a mon-quorum scare and forces osd.0 held out |

### Root cause

**MemTest86 confirms a genuinely faulty DDR5 SODIMM on talos-1**, not a failing disk. Test 7 (Moving inversions, 32-bit pattern) failed with a **stuck data bit (bit 2 / `0x4`)** localized to a narrow physical band (~71 GB, `0x11C0`–`0x11DD`). A tight address range + single stuck bit = a defective cell/data line on **one specific stick**, not a scattered memory-controller fault. DDR5-5186 is *below* the 13900H's DDR5-5600 spec, so this is **not** an EXPO/overclock instability — it's a real defect.

This **resolves the osd.0 "bad RAM OR lying 980 PRO" dichotomy in favour of bad RAM**, and explains why the rebuilt osd.0 BlueStore re-corrupted under backfill writes (`Compaction sees out-of-order keys` → `BlueStore.cc:14648 r==0`) with **clean SMART and clean dmesg**: bad RAM flips bits in BlueStore/RocksDB buffers *before* they're written, so the SSD faithfully persists already-corrupt data — clean SMART, corrupt store. The same mechanism corrupted the talos-1 mon.l RocksDB store (rebuilt → mon.m on 2026-06-17). The 980 PRO (FW 5B2QGXA7) and in-band IBECC (ce/ue_count=0) reported clean precisely because the fault is in a region/path neither covers — IBECC counted 0 because the flipping bit was in a stick/region it doesn't scrub under that load.

### Evidence

```
PassMark MemTest86 V11.6 — 13th Gen Intel Core i9-13900H
Memory: 95.7 GB DDR5 5186 MT/s x2 Channel — Crucial CT48G56C46S5.M16B   RAM Temp 70°C
Test 7 [Moving inversions, 32-bit pattern] — FAIL, aborted at error cap, Errors: 10000

Test 7 Addr: 11DD0D80C0  Expected: 00040000  Actual: 00040004  CPU: 0    (bit 2: 0→1)
Test 7 Addr: 11C06D80C0  Expected: FFFBFFFF  Actual: FFFBFFFB  CPU: 13   (bit 2: 1→0)
Test 7 Addr: 11C05D07C0  Expected: FFFBFFFF  Actual: FFFBFFFB  CPU: 13
Test 7 Addr: 11C04D08C0  Expected: FFFBFFFF  Actual: FFFBFFFB  CPU: 13
```

Prior corruption signatures now attributed to this RAM (all SMART/dmesg-clean at the time):
```
osd.0 rebuild:  rocksdb Background IO error Corruption: Compaction sees out-of-order keys
                -> BlueStore.cc:14648 FAILED ceph_assert(r == 0)  (_txc_apply_kv)
mon.l:          rocksdb block checksum mismatch -> "failed to write to db" (rebuilt -> mon.m 2026-06-17)
```

### Impact

- **No PG/data damage** — every osd.0 corruption crash self-recovered with 0 inconsistent PGs (replica-3 + per-object csum contained it; the fault crashes osd.0's local KV, it never commits bad replicas).
- Triggered the 2026-06-17 cascade tail: talos-1 mon.l RocksDB corruption → fragile 2/3 quorum (recovered by rebuilding mon.l → mon.m).
- osd.0 must stay out / runs in a crash→recover stopgap; with osd.0 absent, talos-1's lone Lexar (osd.3) wedges the CephFS metadata pool (relief = `ceph osd down osd.3`).
- Repeated wasted rebuild/backfill churn on already-fragile consumer NVMe.

### Resolution

**✅ ISOLATED 2026-06-20 — confirmed single bad module ("Stick B"), board/slot/IMC clear.** Controlled same-slot A/B swap: **Stick A** ran 4 full passes, 0 errors in the reference slot (proven good); **Stick B** in that *same* slot failed Test 6 (Block move) in 7 min with stuck **bit 17** (`0x00020000`) across 6 threads at ~36 GB. Only the stick changed → Stick B is defective; the IMC and socket are exonerated (Stick A passed in that exact slot). The earlier differing signature (bit 2 @ ~71 GB dual-stick) was the same Stick B surfacing via channel interleave.

**Real fix = RMA Stick B** (Crucial CT48G56C46S5.M16B, lifetime warranty; advance/cross-ship if offered) and **run talos-1 on Stick A** (the 4-pass-clean module) in the meantime - 48 GB single-channel is ample for talos-1's storage/mon role (the big-RAM AI workloads are pinned to talos-3). Reinstall for dual-channel 96 GB when the replacement arrives. **Executed 2026-08-21** (see close-out below). Node must be drained for the swap: `cordon` + `ceph osd set noout` → power off → reseat/replace → power on → uncordon → `unset noout` (one node, mind the [#17224](https://github.com/rook/rook/issues/17224) OSD device-path caveat on restart).

After the swap: **re-run MemTest86 to confirm clean**, then rebuild osd.0 on clean RAM (zap + uncomment disk in the CephCluster HR + provision) and run `ceph osd deep-scrub` across talos-1 OSDs to confirm no latent inconsistency. Until then: **keep osd.0 in the contained stopgap** (or fully out) and treat talos-1 as do-not-trust for write-heavy Ceph work.

**Interim (already in place):** mon.l rebuilt → mon.m; throttled-recovery mClock drift set live; MGLRU disabled cluster-wide (PR #997). These are mitigations for the *cascade*, not for the RAM — the RAM swap is the only durable fix for talos-1's corruption root.

**✅ CLOSED 2026-07-04 — osd.0 rebuilt and returned to service.** With talos-1 running on the
clean Stick A, osd.0 was rebuilt with a fresh BlueStore and came up `up`/out with an empty
store. Marked `in` on 2026-07-04 (cluster HEALTH_OK, 9.7 TiB raw free after the same-day
shared-downloads cleanup); backfill of its ~1.2 TiB share started cleanly at ~44 MiB/s with no
slow ops.

**✅ CLOSED 2026-08-21 - Stick B repaired/exchanged; all 3 nodes at 96 GB.** The faulty
Crucial CT48G56C46S5.M16B ("Stick B") was replaced. talos-1 is back to dual-channel 96 GB
(2× 48 GB DDR5 SODIMM); talos-2 and talos-3 already were. This ends the reduced-RAM window
(48 GB single stick since 2026-06-20) and is the durable hardware fix for this incident.

---

## [2026-06-14] ALL 3 nodes — WD SN770M firmware (HMB) bug → silent Ceph mon RocksDB corruption

| Field | Value |
|-------|-------|
| **Node** | **ALL 3** (talos-1/2/3) — each has a WD_BLACK SN770M 1TB FW **731100WD** as its system/mon disk (talos-1 `nvme2n1`, talos-2 `nvme0n1`, talos-3 `nvme0n1`). talos-1 corrupted first. |
| **Component** | **WD_BLACK SN770M 1TB, FW 731100WD** — DRAM-less (uses HMB); holds `/var` + the openebs-hostpath Ceph mon stores on every node. (OSD disks are separate: Samsung 980/990 PRO, Lexar NM790 — not affected.) |
| **Affected service** | mon.k, mon.l on talos-1 (corrupted); mon.h (talos-3) + mon.i (talos-2) at equal risk |
| **Severity** | **high** → effectively critical: all 3 mon stores share the buggy firmware; a 2nd mon corruption = quorum loss = cluster down |

### Root cause

**Known WD_BLACK SN770/SN770M firmware bug (FW 731100WD), NOT a failing disk.** The SN770M is
DRAM-less and uses **HMB (Host Memory Buffer)** — host RAM for its flash-translation/mapping
tables. FW 731100WD has a documented HMB data-corruption bug (same family that caused Win11
24H2 BSODs; DRAM-less HMB drives are well-known to be unsuitable for DB/FS workloads like
RocksDB/ZFS). It returns corrupted data **silently under load** — which manifests as RocksDB
block checksum mismatches with **completely clean SMART** (NAND is fine; corruption is in the
HMB/FTL layer). Third occurrence on this node (see [2026-03-21] mon.j below, mis-logged as a
one-off "bit-flip"): mon.j → mon.k → mon.l, the last a fresh replacement on a fresh PVC that
corrupted within minutes. **Heavy IO triggers it**: the full-throttle CephFS migration rsync
wedged osd.0, and the kubelet imageGC change (PR #979) deleting ~370 GB off `/var` corrupted
mon.l. (osd.0 also had a BlueStore assert `_txc_apply_kv r==0` on 2026-06-02.)

**SMART (2026-06-14): CLEAN** — overall PASSED, Critical Warning 0x00, Media & Data Integrity
Errors **0**, Available Spare 100%, Percentage Used **3%**, temp 58°C (sensor1 72°C). No MCE /
EDAC / thermal / NVMe-reset events in dmesg. The clean SMART is what re-pointed the diagnosis
from "failing disk" to "firmware HMB bug." Refs: theregister.com/2024/10/17/western_digital_releases_a_firmware,
support-en.wd.com SN770M, github.com/openzfs/zfs/discussions/14793.

### Evidence

```
mon.l (fresh replacement, fresh PVC) crash-loop:
rocksdb: submit_common error: Corruption: block checksum mismatch:
  stored = 3754013901, computed = 4071487067, type = 4
  in /var/lib/ceph/mon/ceph-l/store.db/000245.sst offset 55621975 size 103686
MonitorDBStore::apply_transaction() -> ceph_abort_msg("failed to write to db")

mon.k earlier: ceph_abort_msg("failed to write to db") (MonitorDBStore.h:356), 49 restarts
osd.0 2026-06-02: BlueStore::_txc_apply_kv FAILED ceph_assert(r == 0) (bstore_kv_sync)
```

### Impact

- Ceph repeatedly degraded to `HEALTH_WARN`, 1/3 mons down, quorum held by mon.h (talos-3) + mon.i (talos-2)
- osd.0/4/5 stuck slow ops (768) wedged client/MDS IO 3× — cleared each time by `ceph osd set noout` + restart osd.0
- **No data loss** — all PGs `active+clean`, 3× replication intact, volumes healthy
- Reduced fault tolerance — only 2 working mons; a third mon cannot survive on talos-1

### Resolution

**✅ RESOLVED 2026-06-14** — all 3 nodes' SN770M flashed `731100WD` → **`731150WD`**, rolling one at a time (cordon → `ceph osd set noout` → reset-to-BIOS + flash → power on → uncordon → OSDs/mon rejoin → `unset noout` → recover). Confirmed working: mon.l ran 9 h stable on the patched firmware under real recovery load (it previously corrupted within minutes). All mon stores now on safe firmware; cluster can take heavy IO again.

Immediate (done): cleared slow ops (restart osd.0); reclaimed ~370 GB on `/var` (imageGC PR #979). Removed corrupt mon.k (`ceph mon remove` + delete PVC + patch `rook-ceph-mon-endpoints`); mon.l left crash-looping (recreating on the same buggy firmware is futile). **Real fix: update the SN770M firmware on ALL 3 NODES** 731100WD → latest (≥731120WD), **rolling — one node at a time** to preserve mon quorum (via WD Dashboard on Windows, or `nvme fw-download`/`fw-commit` from a Linux live USB; each node briefly offline). Interim mitigation: disable HMB for the drive. Until patched: **avoid heavy-IO operations on every node** (not just talos-1) — all 3 mon stores are on the buggy firmware and a 2nd mon corruption breaks quorum. After firmware is patched on a node, restore its mon via the clean recreate runbook. **Disk does NOT need replacing — SMART is healthy on all units.** (osd.0's slow-op wedges are on a different disk — likely a separate Ceph/BlueStore-under-load issue.)

---

## [2026-06-10] talos-3 — Arc Pro B70 install: BIOS bus map broken + bus-number exhaustion

| Field | Value |
|-------|-------|
| **Node** | talos-3 (10.10.10.13) — Meigao Venus (Minisforum MS-01, board `AHWSA`) |
| **Component** | PCIe bus-number allocation — BIOS-provided bus map for the on-card upstream switch (`8086:e2ff`) plus firmware bus-number exhaustion once the card was installed |
| **Affected service** | B70 invisible to Linux (no `xe` bind); talos-3's onboard NICs renamed `enp2s0f*` → `enp3s0f*` → `enp6s0f*` across the fix attempts, breaking the bond each time |
| **Severity** | **high** — new hardware unusable for ~2 days across 2 fix attempts, plus a networking outage on every attempt |

### Root cause

Installing the discrete B70 (behind an on-card PCIe switch, `8086:e2ff` → `e2f0` → `e223`) exposed two separate, sequential firmware bugs — do not treat them as one problem:

1. **BIOS left the switch's bus window at `[bus 00-00]`** (invalid — a single-bus window can't hold a switch's own downstream ports). Linux reconfigured it but could only reach bus 02; bus 03 was already claimed by the onboard i40e NIC. First fix attempt, `pci=realloc`, was **the wrong lever**: it reallocates bridge *MMIO windows*, not bus numbers, and did not help.
2. Once `pci=realloc` proved insufficient, the real problem surfaced: **bus-number exhaustion.** The B70's switch needs 3 bus numbers (`e2ff`→`e2f0`→`e223`) and the firmware's static map had none spare. Fix: `pci=assign-busses`, which makes the kernel ignore BIOS bus numbers and renumber the entire bus tree from scratch. This enumerated the B70 at `03:00.0` with `xe` bound and 32 GB ReBAR — and, as a side effect, reassigned the bus number of an unrelated Lexar NM790 NVMe (a Ceph OSD device).

**Adding a PCIe device on this board renumbers every bus after it, which renumbers the onboard NICs.** Both fix attempts moved `enp2s0f*`/`enp3s0f*` to a new name (finally `enp6s0f*` under `pci=assign-busses`), breaking the network bond each time until the fix landed as `LinkAliasConfig` (`net%d`, matched by `link.driver == "i40e"`) instead of hardcoded interface names.

### Evidence

```
Attempt 1 (pci=realloc — insufficient):
  pci 0000:00:00.0: bridge configuration invalid ([bus 00-00]), reconfiguring
  (switch reaches bus 02 only; bus 03 already owned by i40e)

Attempt 2 (pci=assign-busses — the fix):
  B70 switch enumerates 03:00.0 -> 8086:e2f0 -> 8086:e223, bound to xe, 32GB ReBAR
  Lexar NM790 (Ceph OSD device) reassigned to a new BDF as a side effect
  NICs: enp3s0f0/1 -> enp6s0f0/1 on all 3 nodes (LinkAliasConfig absorbs the rename)
```

### Impact

- B70 unusable for ~2 days across the `pci=realloc` (failed) and `pci=assign-busses` (succeeded) attempts.
- Network bond broke on **every** attempt until `LinkAliasConfig` replaced hardcoded interface names — applied proactively to all 3 nodes, not just talos-3, since any future PCIe topology change can renumber any node's NICs.
- No Ceph data-plane impact; the Lexar NM790 OSD reappeared cleanly under its new BDF (Ceph identifies OSDs by device UUID/serial, not PCI address).

### Resolution

`pci=assign-busses` (the bus-number fix; `pci=realloc` remains alongside it for MMIO window sizing) plus `LinkAliasConfig` (`net%d`, `link.driver == "i40e"`) on all 3 nodes are the durable fix, both live in `talos/schematic.yaml.j2` / per-node overlays today. **Kernel args on this cluster only take effect from `talos/schematic.yaml.j2`** — `machine.install.extraKernelArgs` is ignored by SDBoot/UKI on this platform, a lesson this incident cost a full fix cycle to learn.

When the B70 needs a full power cycle after install/removal or a later disappearance, use the GPU/dock-PSU-before-host order in the [2026-08-24] entry (dock PSU on first, wait 5–10s, then host; never reverse).

### Lessons

- **Firmware's PCIe bus map on this board is not trustworthy, and bus numbers are a scarce, contended resource.** `pci=assign-busses` fixes this only at **boot** — nothing recovers a bus-number shortage at runtime (relevant to the [2026-08-24] incident above: a runtime bus-01 rescan cannot renumber buses 02/03, which are occupied today).
- **Adding or removing a PCIe device on this board renames the onboard NICs.** Never hardcode `enpXsYfZ` interface names on this cluster — use `LinkAliasConfig` matched on driver, as done here.
- **`pci=realloc` and `pci=assign-busses` solve different problems** (MMIO window sizing vs. bus-number renumbering) and are easy to confuse when the symptom is "device invisible." Diagnose which one first: an invalid/too-small bus window (`bridge configuration invalid ([bus X-X])`) is `pci=assign-busses`'s problem, not `pci=realloc`'s.

---

## [2026-04-15] NAS (nas.sklab.dev) — failing DAC cable/PHY on Intel 82599ES port 2

| Field | Value |
|-------|-------|
| **Node** | NAS — `nas.sklab.dev` (10.10.0.40) — TrueNAS SCALE 25.04.2.6 |
| **Component** | Intel 82599ES 10G NIC (Huawei OEM, PCI `8086:10fb` subsys `19e5:d111`), port `enp1s0f1` (SFP+: 4) in LACP bond0 |
| **Affected service** | All NFS clients (3x talos nodes); entire media pipeline (Sonarr/Radarr/Tdarr/Jellyfin) |
| **Severity** | **high** — full NAS hangs; forced reboot required; K8s NFS mounts stall |

### Root cause

The DAC (Direct Attach Copper, twinax) cable on **port 2** of the NAS's dual-port 82599ES NIC is physically failing. The port flaps between up/down constantly, triggering `ixgbe` driver TX queue deadlocks (`Detected Tx Unit Hang`). Under heavy NFS load, the reset loop becomes so aggressive (hundreds of resets per minute on both bond members because the LACP driver shares TX across the aggregator) that the kernel can't make progress, `nfsd` threads pile up in D-state, memory pressure builds, and eventually systemd initiates a shutdown that fails to unmount the ZFS pools cleanly.

Initially suspected as a generic 82599ES + TSO driver bug (well-documented class of issue). Disabling TSO/GSO/GRO on both ports via `ethtool -K ... off` did **not** stop the tx_hangs, which pointed to a physical-layer cause rather than offload bug.

### Evidence

Port statistics showed massively asymmetric link stability:

| Metric | `enp1s0f0` (port 1, good) | `enp1s0f1` (port 2, bad) |
|--------|---------------------------|---------------------------|
| `lsc_int` (link state change interrupts) | 8 | **680** |
| Bond `Link Failure Count` | 3 | **437** |
| `tx_carrier_errors` counter exposed | no | yes |
| Flow control disable persisted | yes | no (port resets too fast) |

Kernel log pattern (characteristic signature):
```
ixgbe 0000:01:00.1 enp1s0f1: Detected Tx Unit Hang
  Tx Queue <N>  TDH, TDT <0>, <1>  next_to_use <1>  next_to_clean <0>
ixgbe 0000:01:00.1 enp1s0f1: tx hang N detected on queue N, resetting adapter
ixgbe 0000:01:00.1 enp1s0f1: initiating reset due to tx timeout
ixgbe 0000:01:00.1 enp1s0f1: primary disable timed out
bond0: (slave enp1s0f1): link status definitely down, disabling slave
```

This ran continuously from 18:20 to 19:16 on Apr 15 before the forced shutdown.

SFP+ module identification: both ports show `Copper pigtail / Passive Cable / Twin Axial Pair` — DAC cables, not optical modules. Same vendor/type in both ports.

### Impact

- Reboot loop: crashed Apr 14, crashed again Apr 15 (~36h apart, both during active Tdarr transcoding load)
- Prior 86 days uptime (Jan 18 → Apr 14), so this is a new failure — likely the cable or port physically degraded
- NFS stalls cascade into the K8s cluster: Tdarr copy-failed errors, Radarr import failures, Jellyfin playback issues

### Resolution (immediate, applied)

**1. Took failing port administratively down:**
```bash
ip link set enp1s0f1 down
```
Bond continues via `enp1s0f0` alone (10 Gbps instead of 20 Gbps aggregate — media-serving doesn't need more).

**2. Disabled TSO/GSO/GRO on both ports** (kept as belt-and-braces even after port issue identified — doesn't hurt):
```bash
ethtool -K enp1s0f0 tso off gso off gro off
ethtool -K enp1s0f1 tso off gso off gro off
```

**3. Disabled flow control on both ports** (known UniFi interaction):
```bash
ethtool -A enp1s0f0 autoneg off rx off tx off
ethtool -A enp1s0f1 autoneg off rx off tx off
```

**4. Made ethtool changes persistent via TrueNAS Init/Shutdown Scripts** (System Settings → Advanced → Init/Shutdown Scripts, `POSTINIT`, id=1 ethtool, id=2 swapon).

**5. Enabled 16 GiB swap on previously-unused `/dev/sda4`** — TrueNAS SCALE doesn't configure swap by default; this is an emergency pressure valve to prevent hard crashes under similar incidents.

### Resolution (follow-up, required)

- **Physically swap DAC cables between port 1 and port 2** to determine whether it's the cable or the NIC port itself that's failing:
  - If after swap, port 2 (same cable) goes clean and port 1 (now with ex-port 2 cable) starts flapping → cable is bad, replace the DAC cable
  - If port 2 keeps flapping with the good cable → NIC PHY/SFP+ cage is bad; permanently run single-link or replace the NIC
- **Replace bad component** (cable ~$10, NIC replacement options: Mellanox ConnectX-3/4 ~$25-50 used on eBay, or genuine Intel X520/X540 not Huawei OEM)
- **Re-enable the bond member** after physical repair: `ip link set enp1s0f1 up` and remove the port-down workaround

### Pattern observation

Unlike the Meigao Venus incidents (recurring firmware/PM races on identical hardware), this is a **discrete physical failure** on a specific port/cable. Not a pattern issue — replacement fixes it permanently. But it does highlight:

1. TrueNAS SCALE has no default swap — should add 16 GB swap on `/dev/sda4` on every install as standard practice (now done)
2. The 82599ES family has a deservedly poor reputation; the Huawei OEM rebrand is the worst variant. Prefer Mellanox for future NAS NICs
3. Heavy NFS load from the K8s cluster (Tdarr + media ingest) is the type of workload that now reliably exposes flaky NICs — consider `nconnect=2` NFS mount option to reduce per-socket pressure

---

## [2026-04-14] Intel iGPU GuC firmware init race — Meigao Venus PM instability (3rd subsystem)

| Field | Value |
|-------|-------|
| **Node** | talos-1 (10.10.10.11) initially, fix applied to all 3 nodes |
| **Component** | Intel UHD/Iris Xe iGPU (Raptor Lake i9-13900H, device id `0xa7a0`) |
| **Affected service** | Tdarr workers (HEVC transcoding), Jellyfin (hardware transcoding if scheduled to talos-1) |
| **Severity** | medium — feature degraded, no data risk |

### Root cause

Same Meigao Venus (AHWSA) board-level power management instability that previously caused the **NVMe APST/PCIe ASPM Ceph OSD crashes** (see project memory `project_talos_nvme_pcie_fix.md`, fixed 2026-03-16). This time the racy subsystem was the **Intel iGPU's display power gating** racing with **GuC (Graphics microController) firmware handshake** during boot.

When the i915 driver loaded, GuC firmware (`i915/adlp_guc_70.bin`, version 70.49.4) loaded but the microkernel never reached the `0xf0` (running) state — got stuck at `0x0`. Any subsequent attempt to use QSV or VAAPI hardware encoding via `iHD_drv_video.so` segfaulted deterministically at offset `0x1c87bc`.

The pattern matches the established Meigao board flakiness: aggressive default PM races with device init. We had already disabled PM for NVMe and external PCIe (`nvme_core.default_ps_max_latency_us=0`, `pcie_aspm=off`); this fix extends the same workaround to the iGPU's display power controller.

### Evidence

GuC status comparison across nodes (BEFORE fix):
```
talos-1: GuC status 0x800300ec   uKernel status = 0x0    ← stuck
talos-2: GuC status 0x8003f0ec   uKernel status = 0xf0   ← healthy
talos-3: GuC status 0x8005f0ec   uKernel status = 0xf0   ← healthy
```

ffmpeg QSV test on talos-1 → exit code 139 (SIGSEGV).

Kernel trap log full of identical-offset segfaults from every attempt to use the GPU:
```
traps: HandBrakeCLI[XXXX] general protection fault ip:XXXXXXXX7bc sp:XXXXXXXX
  error:0 in iHD_drv_video.so[1c87bc,XXXXXXXX+a11000]
```
Same offset (`0x1c87bc`) every time — deterministic software fault, not hardware corruption.

Tdarr encoder probe before/after on talos-1:
```
Before: h264_qsv-true-false,  hevc_qsv-true-false,  hevc_vaapi-true-false
After:  h264_qsv-true-true,   hevc_qsv-true-true,   hevc_vaapi-true-true
```

### Impact

- Tdarr DaemonSet running on talos-1 was useless for GPU work (driver crashed on every encode)
- Files transcoded successfully on talos-2/talos-3 but couldn't use the third GPU
- Jellyfin would have lost hardware transcoding if rescheduled to talos-1
- No data loss
- The earlier Tdarr V8 `VerifyChecksum(blob)` crash on talos-1 was a separate, one-off container layer corruption (resolved by pod delete) — not connected to this issue. Initially looked like generic hardware failure but ruled out by confirming no MCE/ECC events in dmesg, healthy temps (51°C), and identical hardware/firmware across nodes

### Resolution

At the time of the incident, kernel arg `i915.enable_dc=0` was added to the factory schematic (disables iGPU display controller power gating). Factory schematic ID then: `ac2b7006014bfd57ed2ee6bce766bfe1d3a18f02e2a5f3a6fc4f5265c77e99ee`.

Rolled out via Talos node upgrade to all 3 nodes one at a time, waiting for Ceph to rebalance between each (kept it from going degraded). After the upgrade:
- talos-1 GuC reached `0xf0`, all encoders show `true-true`
- The pre-existing slow-OSD warning on talos-3's `osd.4` cleared after that node's reboot - likely the same BlueStore PM stall pattern
- All 3 nodes then had all known PM races disabled

```
NVMe:  nvme_core.default_ps_max_latency_us=0    (fixed 2026-03-16)
PCIe:  pcie_aspm=off                             (fixed 2026-03-16)
iGPU:  i915.enable_dc=0                          (fixed 2026-04-14; superseded)
```

**Superseded 2026-06-09** (`73c9e3da`, `feat(talos): migrate Intel GPU driver from i915 to xe`): i915 kernel args (`i915.enable_guc=3`, `i915.enable_dc=0`) were removed because xe handles GuC/device PM; the driver path is xe, not i915. **Do not re-add `i915.enable_dc=0`.** `siderolabs/i915` later returned for firmware only (xe still loads a7a0 blobs from `i915/`) — current extensions and kernel args live in `talos/schematic.yaml.j2`; deliberate GPU changes are logged in [`ai-gpu-changelog.md`](./ai-gpu-changelog.md). Node upgrades are `just talos upgrade-node talos-N`, not `task talos:upgrade-node`.

### Pattern observation

Meigao Venus boards have aggressive default BIOS power management across **every** PM-managed subsystem. Each subsystem's driver has to win a race against PM kicking in. Linux often loses when probing fast. **Expect more surprises from this hardware over time** (USB controllers, display, audio) all fixable by similar `disable PM for X` kernel args. Consider proactively auditing other subsystems' PM behavior before they cause incidents.

---

## [2026-03-21] Ceph monitor mon.j crash loop — RocksDB store corruption on talos-1

| Field | Value |
|-------|-------|
| **Node** | talos-1 (10.10.10.11) |
| **Component** | Local storage (openebs-hostpath) |
| **Affected service** | mon.j (rook-ceph-mon-j) |
| **Severity** | high |

### Root cause

RocksDB SST file `154904.sst` in mon-j's store developed a block checksum mismatch, indicating silent data corruption on the underlying openebs-hostpath volume (`/var/openebs/local/pvc-df451fee-f19a-4568-9e50-891988442cab`). The corruption was detected during a compaction of L0 into L6. Once RocksDB flagged the background error, all subsequent writes were rejected, causing mon-j to abort on every sync attempt. Suspected cause is either an unclean node shutdown or a silent bit-flip on the local disk.

### Evidence

```
rocksdb: Corruption: block checksum mismatch: stored = 467716038, computed = 938189546, type = 4
  in /var/lib/ceph/mon/ceph-j/store.db/154904.sst offset 13748504 size 103348

rocksdb: submit_common error: Corruption: block checksum mismatch (same as above)
  Rocksdb transaction rejected — MonitorDBStore::apply_transaction() -> ceph_abort_msg("failed to write to db")

Crash backtrace: Monitor::sync_start -> apply_transaction -> failed to write to db -> abort
55+ restarts in CrashLoopBackOff over ~4.5 hours
```

### Impact

- Ceph cluster degraded to `HEALTH_WARN` — 1/3 mons down, quorum maintained by mon.h and mon.i
- Rook operator unable to schedule replacement mon-k due to host port conflicts (3300/6789 held by crashing mon-j pod)
- No data loss — all 6 OSDs healthy, all PGs active+clean
- Reduced fault tolerance — loss of one more mon would break quorum

### Resolution

Delete mon-j deployment and PVC to free host ports and remove corrupted store. The Rook operator will automatically create a replacement monitor that syncs a fresh monstore from the quorum.

```bash
kubectl -n rook-ceph delete deployment rook-ceph-mon-j
kubectl -n rook-ceph delete pvc rook-ceph-mon-j
```

---
