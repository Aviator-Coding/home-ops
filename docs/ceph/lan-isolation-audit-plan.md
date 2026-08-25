# Ceph LAN isolation - audit plan, gates, and rollback

Companion to `kubernetes/apps/base/kube-system/cilium/app/hostpolicy-ceph.yaml` and the
`hostFirewall` / `policyAuditMode` values in
`kubernetes/apps/base/kube-system/cilium/app/helmrelease.yaml`.

**Status: stage 2 complete (enforcing since 2026-08-25).**
`policyAuditMode: false` and `hostFirewall.enabled: true` are live; the Ceph
host policy drops the enumerated `(world, port)` pairs. The stage-1 audit
evidence that justified the flip is recorded in
[`lan-isolation-audit-results.md`](./lan-isolation-audit-results.md).

This document remains the owner for **post-merge smoke / two-way BPF gate
(§2)**, the historical audit capture procedure (§3–§4), and **rollback (§6)**.
Do not treat the stage-1 capture sections as the current cluster state.

## 0. Why it was staged

Talos has no SSH. If a host firewall policy black-holes the node management
plane, there is no way back in except a physical console or a node reset. The
staged rollout (audit window, then enforce) existed to make that impossible to
reach by accident.

The design's primary safety property is *structural*, not procedural, and it
is still what protects the nodes under enforce:
`enableDefaultDeny: {ingress: false, egress: false}` keeps the policy purely
subtractive, so the host endpoint never enters default-deny and only the
enumerated `(world, port)` pairs can ever be dropped. Audit mode was a temporary
second layer during stage 1; it is **not** what makes enforce safe.

## 1. What stage 1 changed (historical)

| Change | File | Effect |
|---|---|---|
| `hostFirewall.enabled: true` | `cilium/app/helmrelease.yaml` | `enable-host-firewall: "true"`; host policies enforceable at the tc hook in `bpf_host` on `bond0`, `bond0.3`, `bond0.90` |
| `policyAuditMode: true` (stage 1 only) | `cilium/app/helmrelease.yaml` | `policy-audit-mode: "true"`; every policy verdict non-enforcing and reported instead. **Stage 2 flipped this to `false`.** |
| `updateStrategy.rollingUpdate.maxUnavailable: 1` | `cilium/app/helmrelease.yaml` | agent DaemonSet rolls one node at a time instead of two |
| `CiliumClusterwideNetworkPolicy/ceph-lan-isolation` | `cilium/app/hostpolicy-ceph.yaml` | deny-only host policy for the Ceph port set |

Audit mode is global, not host-scoped. While stage 1 was live the cluster's 14
pod `NetworkPolicy` objects (dragonfly cache isolation, prometheus scrape
allows) were also **not enforced**. Stage 2 restored their pre-stage-1
enforcement along with the host policy.

## 2. Post-merge smoke test (stage 1 historical; re-run after stage 2 / any host-policy edit)

Run all four. **If any fails under enforce, roll back immediately (§6) - do not
investigate with a broken host policy live.**

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
# Current (stage 2 enforcing): MUST print: true  false
# Stage 1 audit window was:                 true  true
```

```bash
# 2c. THE CRITICAL CHECK - two-way gate on every host endpoint:
#     (i) Deny rows present (policy is not inert) AND
#     (ii) Allow Ingress ANY still present (not default-deny).
for p in $(kubectl -n kube-system get pod -l k8s-app=cilium -o name); do
  echo "== $p"
  HOSTEP=$(kubectl -n kube-system exec ${p#pod/} -c cilium-agent -- \
    cilium-dbg endpoint list -o json 2>/dev/null \
    | python3 -c "import json,sys; print([e['id'] for e in json.load(sys.stdin) if any('reserved:host' in l for l in e['status']['identity']['labels'])][0])")
  echo "host endpoint = $HOSTEP"
  kubectl -n kube-system exec ${p#pod/} -c cilium-agent -- cilium-dbg bpf policy get "$HOSTEP"
done
```

This output is a **two-way** gate. Both halves must hold on all three nodes.

**(i) `Deny` rows MUST be present** - one per Ceph port, e.g.

```
Deny     Ingress     reserved:world        3300/TCP   ...
Deny     Ingress     reserved:world-ipv4   3300/TCP   ...
Deny     Ingress     reserved:world-ipv6   3300/TCP   ...
```

(`fromEntities: world` expands to those three reserved identities.) If there
are **no** `Deny` rows, the policy is **inert**: its `nodeSelector` matched
nothing, the daemons are still LAN-exposed, and any audit window would record
nothing and prove nothing. This fails silently - the CCNP still reports
`VALID: True`. Confirm with:

```bash
# inert policy looks exactly like a healthy one from the API's point of view
kubectl get ccnp ceph-lan-isolation -o jsonpath='{.status}'   # says Valid: True either way
# the real signal is on the endpoint:
cilium-dbg endpoint list -o json | jq '.[]|select(.status.identity.id==1)|.status.policy.realized."policy-enabled"'
# "none" => nothing selected the host endpoint
```

This is not hypothetical: the first stage-1 policy shipped with
`nodeSelector: kubernetes.io/os=linux`, which Cilium's default label filter
strips from the host endpoint, so it was inert on all three nodes until the
selector was changed to `node-role.kubernetes.io/control-plane`. See the
selector comment in `hostpolicy-ceph.yaml` for the measured label sets.

**(ii) The catch-all `Allow Ingress ANY` row MUST still be present** alongside
those `Deny` rows. It is proof the host endpoint did **not** enter default-deny.
If `Allow Ingress ANY` has disappeared, `enableDefaultDeny` did not take effect -
**roll back immediately (§6)**. Under enforce there is no audit-mode cushion:
missing that row means the host endpoint is in ingress default-deny and can
black-hole management traffic.

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
While `policyAuditMode` is true it is global, so an unfiltered monitor also
records audit verdicts from the cluster's pod NetworkPolicies (dragonfly /
emqx / loki, etc.). Those were expected under stage 1 and must not be mixed into
the Ceph host-policy evidence set - pass criteria below are host-endpoint-only
for that reason. Under current enforce mode, use the same `--related-to` scope
when debugging drops.

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

## 4. PASS criteria (historical gate - all held; see results doc)

Stage 2 was only allowed once **every** one of these held across all three
nodes' **host-endpoint** captures (`--related-to $HOST_EP_ID` from §3). The
recorded pass is in [`lan-isolation-audit-results.md`](./lan-isolation-audit-results.md).
Pod NetworkPolicy audit noise was out of scope and must not be scored here.

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
scanner/admin host was a decision for the captain **before** stage 2, not an
acceptable casualty of it. The audit window found none (only the operator probe).

## 5. Stage 2 (enforce) - DONE

Completed 2026-08-25 after §4 passed. Evidence:
[`lan-isolation-audit-results.md`](./lan-isolation-audit-results.md).

What stage 2 changed (and only this):

1. Flipped `policyAuditMode: true` -> `false` in
   `kubernetes/apps/base/kube-system/cilium/app/helmrelease.yaml`. Left
   `hostFirewall.enabled: true` and the policy rules unchanged.
2. Post-merge verification (still required after any future host-policy edit):
   re-run the entire §2 smoke test - especially **2c**'s two-way gate (Deny
   rows present *and* `Allow Ingress ANY` surviving; either half failing is a
   stop) - then probe the denied ports from a non-node LAN host and confirm they
   **fail** while `s3.sklab.dev` and Prometheus still work. Concrete commands are
   also at the bottom of the results doc.

## 6. Rollback

Fastest safe rollback at any point, in preference order:

1. **Set `policyAuditMode: true`** in
   `kubernetes/apps/base/kube-system/cilium/app/helmrelease.yaml` and let Flux
   reconcile (or revert the enforce commit) -
   `flux reconcile kustomization cluster-apps --with-source`.
   That immediately un-drops everything while leaving the policy and host
   firewall installed. Deleting the CCNP also stops drops.
2. If Flux cannot reconcile because management access is already impaired, delete
   the policy directly - this is the single fastest un-break:
   ```bash
   kubectl delete ciliumclusterwidenetworkpolicy ceph-lan-isolation
   ```
   Then fix Git, or Flux will reapply it.
3. If the API server itself is unreachable from the workstation, use the Talos
   API (`talosctl`, port 50000) from a host that still has it. If that is also
   gone, the remaining path is physical console per
   `talos/AGENTS.md`.

The live policy is enforcing, so rollback urgency applies whenever §2 fails or
management/cluster traffic is impaired - not only during an agent rollout
(§2a).

## 7. Known risks (still apply under enforce)

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
