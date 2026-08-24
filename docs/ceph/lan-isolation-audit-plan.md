# Ceph LAN isolation - audit observation plan (stage 1 -> stage 2)

Companion to `kubernetes/apps/kube-system/cilium/app/hostpolicy-ceph.yaml` and the
`hostFirewall` / `policyAuditMode` values in
`kubernetes/apps/kube-system/cilium/app/helmrelease.yaml`.

This document is the gate between **stage 1 (audit, nothing drops)** and
**stage 2 (enforce)**. Do not open the stage 2 PR without the evidence in
[§4](#4-pass-criteria-all-must-hold) captured and quoted.

## 0. Why staged at all

Talos has no SSH. If a host firewall policy black-holes the node management
plane, there is no way back in except a physical console or a node reset. Every
step below exists to make that impossible to reach by accident.

The design's primary safety property is *structural*, not procedural:
`enableDefaultDeny: {ingress: false, egress: false}` keeps the policy purely
subtractive, so the host endpoint never enters default-deny and only the
enumerated `(world, port)` pairs can ever be affected. Audit mode is the second
layer, not the only one.

## 1. What stage 1 changed

| Change | File | Effect |
|---|---|---|
| `hostFirewall.enabled: true` | `cilium/app/helmrelease.yaml` | `enable-host-firewall: "true"`; host policies now enforceable at the tc hook in `bpf_host` on `bond0`, `bond0.3`, `bond0.90` |
| `policyAuditMode: true` | `cilium/app/helmrelease.yaml` | `policy-audit-mode: "true"`; **every** policy verdict becomes non-enforcing and is reported instead |
| `updateStrategy.rollingUpdate.maxUnavailable: 1` | `cilium/app/helmrelease.yaml` | agent DaemonSet rolls one node at a time instead of two |
| `CiliumClusterwideNetworkPolicy/ceph-lan-isolation` | `cilium/app/hostpolicy-ceph.yaml` | deny-only host policy for the Ceph port set |

Audit mode is global, not host-scoped. While stage 1 is live the cluster's 14
pod `NetworkPolicy` objects (dragonfly cache isolation, prometheus scrape
allows) are also **not enforced**. They are internal-only and nothing here is
internet-facing, but this is the reason the window is bounded rather than
open-ended.

## 2. Post-merge smoke test (run immediately after Flux reconciles stage 1)

Run all four. **If any fails, revert stage 1 - do not investigate with the
policy live.**

```bash
export KUBECONFIG=${KUBECONFIG:-kubeconfig}

# 2a. All three agents healthy after the rolling restart. This is the one that
#     catches a bpf_host program that failed to load or verify.
kubectl -n kube-system get pods -l k8s-app=cilium -o wide
kubectl -n kube-system logs -l k8s-app=cilium --tail=50 --prefix \
  | grep -iE 'error|failed|too large|verifier' || echo "no agent errors"

# 2b. Both switches actually took effect.
kubectl -n kube-system get cm cilium-config \
  -o jsonpath='{.data.enable-host-firewall}{"  "}{.data.policy-audit-mode}{"\n"}'
# MUST print: true  true
```

```bash
# 2c. THE CRITICAL CHECK - the host endpoint must still be subtractive.
for p in $(kubectl -n kube-system get pod -l k8s-app=cilium -o name); do
  echo "== $p"
  HOSTEP=$(kubectl -n kube-system exec ${p#pod/} -c cilium-agent -- \
    cilium-dbg endpoint list -o json 2>/dev/null \
    | python3 -c "import json,sys; print([e['id'] for e in json.load(sys.stdin) if any('reserved:host' in l for l in e['status']['identity']['labels'])][0])")
  echo "host endpoint = $HOSTEP"
  kubectl -n kube-system exec ${p#pod/} -c cilium-agent -- cilium-dbg bpf policy get "$HOSTEP"
done
```

The output **must** still contain a catch-all row:

```
Allow    Ingress     ANY    ANY    ...
```

alongside the new `Deny` rows for the Ceph ports. That catch-all is proof the
host endpoint did **not** enter default-deny. If `Allow Ingress ANY` has
disappeared, `enableDefaultDeny` did not take effect - **revert immediately**;
the only thing standing between that state and a locked-out node is audit mode,
which does not survive being turned off.

```bash
# 2d. Management plane still answers, from a host that is NOT a cluster node.
kubectl get --raw='/readyz'                      # API 6443
kubectl -n kube-system get nodes                 # kubelet 10250 path
# and from the workstation:
nc -vz 10.10.10.11 50000                         # Talos API
```

## 3. Capturing audit verdicts

Hubble is disabled in this cluster, so verdicts come from the agent monitor.
`cilium-dbg monitor -t policy-verdict` emits one event per verdict; under audit
mode a would-be drop is reported with an **audit** action and the packet is
still delivered. `/usr/bin/timeout` exists inside the agent container, so bound
each capture rather than relying on the kubectl stream staying up.

**Scope every capture to the host endpoint** with `--related-to $HOST_EP_ID`.
`policyAuditMode` is global, so an unfiltered monitor also records audit
verdicts from the cluster's pod NetworkPolicies (dragonfly / emqx / loki, etc.).
Those are expected under stage 1 and must not be mixed into the Ceph host-policy
evidence set - pass criteria below are host-endpoint-only for that reason.

Run one capture per node, in parallel, for the whole window. Prefer a durable
local session (`tmux`/`screen`) - a 25h `kubectl exec` stream can drop on the
client side long before `timeout` fires; if it does, restart the capture on that
node and rely on §2c `bpf policy get` deny counters as the non-stream backup.

```bash
export KUBECONFIG=${KUBECONFIG:-kubeconfig}
mkdir -p ./ceph-audit && cd ./ceph-audit
WINDOW=90000   # seconds; see §3.1

for p in $(kubectl -n kube-system get pod -l k8s-app=cilium -o name); do
  pod=${p#pod/}
  node=$(kubectl -n kube-system get pod "$pod" -o jsonpath='{.spec.nodeName}')
  HOST_EP_ID=$(kubectl -n kube-system exec "$pod" -c cilium-agent -- \
    cilium-dbg endpoint list -o jsonpath='{[?(@.status.identity.id==1)].id}')
  echo "$node host endpoint = $HOST_EP_ID"
  ( kubectl -n kube-system exec "$pod" -c cilium-agent -- \
      timeout "$WINDOW" cilium-dbg monitor -t policy-verdict -j --related-to "$HOST_EP_ID" \
      > "verdicts-${node}.jsonl" 2> "verdicts-${node}.err" ) &
done
wait
```

Keep the human-readable form too if you want to eyeball it - drop `-j`. The
field that matters is the verdict action (`audit`), plus source identity /
source IP, destination port, and direction. Confirm the exact rendering on the
first few events before writing any parser against it.

Complement the stream with the static view, which needs no traffic at all and
shows precisely what the datapath will do once enforcing - re-run the §2c
`bpf policy get` and read the `Deny` rows and their packet counters.

### 3.1 Window length - **not one hour**

The brief assumed hourly backups. The live schedules are not hourly:

| Destination | Cadence | Count |
|---|---|---|
| `*-ceph` | every 4 h (`M */4 * * *`, staggered) | 33 |
| `*-minio` | every 6 h (`M */6 * * *`, staggered) | 33 |
| `*-r2` | daily, inside 01:00-04:00 | 33 |

A one-hour window would miss every MinIO mover and every R2 mover. **Run at
least 25 hours**, and make sure it spans the 01:00-04:00 R2 band, so all three
destination classes and a full Ceph and MinIO cycle each appear at least once.
`WINDOW=90000` above is 25 h.

Also make sure the window contains at least one Prometheus scrape of `9283` and
`9926` (every scrape interval, so this is automatic) and, if possible, a
`s3.sklab.dev` request through the HTTPRoute so the legitimate RGW path is
exercised.

## 4. PASS criteria (all must hold)

Stage 2 may only be proposed if **every** one of these holds across all three
nodes' **host-endpoint** captures (`--related-to $HOST_EP_ID` from §3). Pod
NetworkPolicy audit noise is out of scope and must not be scored here.

1. **Zero host-endpoint audit verdicts for any management or cluster flow.**
   Specifically nothing audited on: Talos API `50000`/`50001`, Kubernetes API
   `6443`, kubelet `10250`, etcd `2379`/`2380`, Cilium health `4240`, BGP `179`
   from `10.0.0.1`, DNS `53`, NodePort `30000-32767`, or KubePrism `7445`.
   *Expected structurally* - the policy names none of these ports and the host
   endpoint is not default-deny - so any hit here means the design is wrong, not
   that a carve-out is missing. **Investigate before proceeding.**
2. **Zero host-endpoint audit verdicts for in-cluster sources.** No audited
   host-endpoint flow may carry a `host`, `remote-node`, or pod identity as its
   source. Ceph's own mon quorum, OSD replication, MDS, CSI, VolSync movers and
   Prometheus scrapes must all be absent from the host-endpoint audit set
   (they are outside `fromEntities: world` by construction).
3. **Zero host-endpoint audit verdicts on port 80 destined to a LoadBalancer IP**
   (`10.50.0.21`, `10.50.0.26`, `10.50.0.27`, `10.50.0.28`, `10.50.0.29`).
   This is the one designed-in uncertainty: RGW shares port 80 with the envoy
   gateways and the `ai/*` services. Those target LB IPs rather than a node IP
   and should never reach the host endpoint's ingress policy, but that is the
   assumption this window exists to falsify. **If gateway traffic is audited on
   port 80, drop `80` from the deny list and close RGW another way** (bind RGW
   off `0.0.0.0`, or front it exclusively through the HTTPRoute) rather than
   enforcing as-is.
4. **All 33 `*-ceph` and 33 `*-minio` ReplicationSources completed at least
   once during the window with no new failures:**
   ```bash
   kubectl get replicationsource -A -o custom-columns=\
   NS:.metadata.namespace,NAME:.metadata.name,LAST:.status.lastSyncTime,\
   FAIL:.status.latestMoverStatus.result | sort
   ```
5. **`ceph status` is `HEALTH_OK`** and mon quorum still lists all three mons.
6. **The expected LAN denials DO appear** - otherwise the policy is inert and
   enforcing it would prove nothing. Generate them deliberately from a non-node
   LAN host (e.g. `10.0.0.150`) and confirm each shows up as an audit verdict:
   ```bash
   for pt in 3300 80 9283 9926; do nc -vz -w2 10.10.10.11 $pt; done
   curl -s -o /dev/null -w '%{http_code}\n' http://10.10.10.11:80/
   ```
   All of these still succeed under audit mode - that is the point. They must
   appear in the capture as audited.

### Unexpected-but-legitimate LAN clients

Item 6 can surface a *third* category: a real LAN device that legitimately uses
Ceph directly (an S3 client, a CephFS mount, a scraper). The captures are the
only place this will show up before it breaks. Enumerate every distinct source
IP in the audit set and account for each one. Any address that is not a known
scanner/admin host is a decision for the captain **before** stage 2, not an
acceptable casualty of it.

## 5. Stage 2 (enforce)

Only after §4 passes:

1. Flip `policyAuditMode: true` -> `false` in
   `kubernetes/apps/kube-system/cilium/app/helmrelease.yaml`. Leave
   `hostFirewall.enabled: true` and the policy in place - they are separate
   stages and stage 2 changes **only** this line.
2. Quote the observed evidence in the PR body: the per-node audited-flow
   summary, the explicit "zero management/cluster flows audited" result, the
   port-80 LB finding, and the ReplicationSource completion table.
3. After merge, re-run the entire §2 smoke test - especially **2c**, which is
   now the difference between a working node and a black-holed one - then
   re-run the §4 item 6 probes and confirm they now **fail** from the LAN while
   `s3.sklab.dev` and Prometheus still work.

## 6. Rollback

Fastest safe rollback at any point, in preference order:

1. **Revert the PR** and `flux reconcile kustomization cluster-apps --with-source`.
   Removing the policy or setting `policyAuditMode: true` both immediately stop
   any drop.
2. If Flux cannot reconcile because management access is already impaired, delete
   the policy directly - this is the single fastest un-break:
   ```bash
   kubectl delete ciliumclusterwidenetworkpolicy ceph-lan-isolation
   ```
   Then revert in Git, or Flux will reapply it.
3. If the API server itself is unreachable from the workstation, use the Talos
   API (`talosctl`, port 50000) from a host that still has it. If that is also
   gone, the remaining path is physical console per
   `talos/AGENTS.md`.

Because stage 1 cannot drop a packet, rollback urgency applies to stage 2 and to
a failed agent rollout (§2a), not to stage 1 itself.

## 7. Known risks carried into stage 1

- **`bpf_host` program size.** Enabling host firewall grows the `bpf_host`
  program. cilium/cilium#38967 reports "BPF program is too large" with host
  firewall on a feature-rich config; its stated affected range is
  `>= v1.17.2, < v1.18.0` and this cluster runs 1.18.6, but the feature mix here
  is unusually broad (netkit, BBR, DSR, maglev, XDP best-effort, BIG TCP, PMTU
  discovery, local redirect, socket LB, BGP). §2a is the check for it, and
  `maxUnavailable: 1` is why it can only take one node at a time.
- **Host policies are enforced at the tc hook, not XDP.** With
  `bpf-lb-acceleration: best-effort`, service traffic handled at the XDP hook
  does not traverse the host policy. This is reassuring for the port-80 gateway
  question but also means the deny must not be assumed to cover an
  XDP-accelerated path.
- **Per-endpoint routes** (`endpointRoutes.enabled: true`) were historically
  incompatible with host policy - cilium/cilium#13121, fixed in 1.10 by
  cilium/cilium#15217. Not a concern at 1.18.6, recorded so it is not
  re-litigated.
