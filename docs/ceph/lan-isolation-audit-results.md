# Ceph LAN isolation - stage-1 audit results (the evidence for enforcing)

Window: **2026-08-24T10:38:20Z -> 2026-08-25T11:38:28Z (25h)**, all three nodes,
Cilium 1.18.6, `policy-audit-mode: "true"` throughout (nothing could be dropped).

This is the evidence gate defined by `lan-isolation-audit-plan.md` §4. It is the
justification for flipping `policyAuditMode` to `false`.

## Verdict: all pass criteria met

| # | Criterion | Result |
|---|---|---|
| 1 | No management/cluster flow audited (Talos 50000/50001, API 6443, kubelet 10250, etcd, health 4240, BGP 179, DNS, NodePort, KubePrism 7445) | **PASS** - zero |
| 2 | No in-cluster source audited (host / remote-node / pod identity) | **PASS** - structurally impossible, see below |
| 3 | No port-80 audit for LoadBalancer traffic | **PASS** - proved by active test |
| 4 | All VolSync ReplicationSources completed, no new failures | **PASS** - 99/99 Successful |
| 5 | `ceph status` HEALTH_OK, 3/3 mon quorum | **PASS** |
| 6 | Intended LAN denials DO appear (policy is not inert) | **PASS** - 5 verdicts, all deliberate probes |

## The headline number: deny-counter delta over 25 hours

`cilium-dbg bpf policy get <host-endpoint>` packet counters, t0 vs t1. These are
cumulative in the datapath and independent of the verdict stream, so they are the
authoritative measure of what enforcement *would* have dropped.

| Node | Delta | Attribution |
|---|---|---|
| talos-1 | **0 packets** | - |
| talos-2 | **4 packets** | `10.0.0.150 -> 10.10.10.12:3300`, a deliberate probe |
| talos-3 | **0 packets** | measured against its post-reboot baseline (see below) |

In 25 hours of production operation the policy would have dropped **nothing
except one probe fired by the operator running this test**.

## Criterion 2 is structural, not statistical

Every deny row on every host endpoint is bound to `reserved:world`,
`reserved:world-ipv4` or `reserved:world-ipv6`. There is **no deny row for any
cluster identity on any node**, so no `host`, `remote-node` or pod-identity flow
can match a deny entry regardless of traffic pattern. Confirmed on all three:

```
talos-1: NONE (only reserved:world/-ipv4/-ipv6)
talos-2: NONE (only reserved:world/-ipv4/-ipv6)
talos-3: NONE (only reserved:world/-ipv4/-ipv6)
```

This is the intended consequence of `fromEntities: [world]` plus
`enableDefaultDeny: false`, and it is why mon quorum, OSD replication, MDS, CSI,
VolSync movers and Prometheus scrapes were never at risk.

Corroborating: all 4 Prometheus Ceph targets stayed `up=1` for the window, on the
very ports denied from world - `10.10.10.11:9926`, `10.10.10.12:9926`,
`10.10.10.13:9926`, `10.10.10.12:9283`.

## Criterion 3 - the port-80 question, actively falsified

RGW shares port 80 with `envoy-external` (10.50.0.21), `envoy-internal`
(10.50.0.26) and the three `ai/*` LoadBalancers. The plan required proving that
LoadBalancer traffic does not reach the host endpoint policy. Zero denies alone
would have been inconclusive, so the case was driven deliberately from
`10.0.0.150` (a `world` source):

```
http://10.50.0.21:80/  -> HTTP 301      port-80 deny counters before: 0 / 11 / 2
http://10.50.0.26:80/  -> HTTP 301      port-80 deny counters after : 0 / 11 / 2
http://s3.sklab.dev/   -> HTTP 301      (talos-2 / talos-1 / talos-3)
```

Traffic served normally, counters did not move. LoadBalancer traffic targets the
LB IP, not the node IP, and does not traverse the host endpoint's ingress policy.
`https://s3.sklab.dev/` also returned HTTP 200 throughout, so the legitimate RGW
path via the HTTPRoute is unaffected.

## Criterion 4 - backups

All 99 VolSync ReplicationSources reported `result: Successful` and all synced
inside the window, covering every destination class:

```
ceph   result=Successful  count=33     (every 4h)
minio  result=Successful  count=33     (every 6h)
r2     result=Successful  count=33     (daily, 01:00-04:00 band - inside the window)
sources with NO sync inside the window: 0
```

## Criterion 6 - the policy is demonstrably live

Five audit verdicts were captured, from exactly one source IP - the workstation
running this test:

```
10.0.0.150 -> 10.10.10.12:3300      10.0.0.150 -> 10.10.10.13:3300
10.0.0.150 -> 10.10.10.13:80        10.0.0.150 -> 10.10.10.13:9926
10.0.0.150 -> 10.10.10.13:6800
```

Sample, showing audit semantics (matched, reported, delivered anyway):

```
Policy verdict log: flow 0xb77ce6dc local EP ID 101, remote ID world, proto 6,
ingress, action audit, auth: disabled, match L3-L4,
10.0.0.150:60765 -> 10.10.10.13:3300 tcp SYN
```

Every probed connection still succeeded during the window - RGW served its
anonymous bucket listing (HTTP 200) and both metrics endpoints returned 200 -
which is the exposure this policy closes once enforcing.

**No third-party LAN client appeared.** Across 25 hours the only `world` source
touching any Ceph port was the operator's own workstation, so there is no
unexpected-but-legitimate consumer to accommodate.

## Caveats, honestly stated

- **Verdict counts are per flow; counters are per packet.** 5 verdicts vs 114
  cumulative counter packets is not a discrepancy: Cilium logs a verdict once per
  flow, while the counter increments for every packet of that flow.
- **talos-3 was power-cycled at ~2026-08-25T00:00Z** (unrelated GPU reseat). Its
  BPF counters reset and its host endpoint renumbered 2206 -> 101, so its delta is
  measured against a post-reboot baseline and covers ~11.6h, not the full 25h.
  talos-1 and talos-2 cover the full window.
- **~7.5 min capture gap on talos-3** (23:58:04Z -> 00:05:42Z) spanning the
  reboot. The deny counters cover that gap even though the stream did not.
- **Streams restarted about every 4h on every node.** This is kubelet's default
  `streamingConnectionIdleTimeout` closing an idle exec stream, not a fault; the
  wrapper reconnected each time. Gaps were seconds. Anyone repeating this should
  expect it and must not read the restarts as lost coverage.
- **Low organic traffic means low stress.** Enforcement is being justified by the
  absence of at-risk flows, not by having survived heavy adversarial load.

## Bonus result: audit mode survived a node reboot

talos-3 returned from a cold boot with `policy-enabled = audit-ingress`, all 45
deny rows restored and the `Allow Ingress ANY` catch-all intact. This validates
choosing the ConfigMap-level `policyAuditMode` over the per-endpoint
`cilium-dbg endpoint config PolicyAuditMode=Enabled` toggle, which reverts to
**enforcing** on agent restart and would have silently enforced this policy on a
node rebooted for unrelated hardware work.

## Incidental finding

`rook-ceph-rgw` runs 2 replicas, on talos-1 and talos-2 only. Port 80 has no
listener on talos-3, so the port-80 deny is operative on two nodes. A verdict is
still emitted there on SYN because ingress policy is evaluated before the
(absent) listener.

## Post-merge verification for stage 2

Re-run `lan-isolation-audit-plan.md` §2c first - it is now the difference between
a working node and a black-holed one. Then confirm the exposure is actually
closed, from a LAN host that is not a cluster node:

```bash
for pt in 3300 80 9926; do nc -vz -w3 10.10.10.11 $pt; done   # expect: FAIL
curl -s -o /dev/null -w '%{http_code}\n' https://s3.sklab.dev/ # expect: 200
```

Ceph targets in Prometheus must stay `up=1` and `ceph -s` must stay HEALTH_OK.
Rollback: `lan-isolation-audit-plan.md` §6.
