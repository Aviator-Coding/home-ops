# Host-policy gate and rollback

Run this after any edit to `hostpolicy-ceph.yaml` or to `hostFirewall` / `policyAuditMode`. If a check fails while the policy is enforcing, roll back. Do not debug a host that is already dropping management traffic.

Pass `kubectl --kubeconfig` explicitly. The mise shim overrides `KUBECONFIG` from `.mise.toml`.

## Agents

```bash
kubectl --kubeconfig="$KUBECONFIG" -n kube-system get pods -l k8s-app=cilium -o wide
kubectl --kubeconfig="$KUBECONFIG" -n kube-system logs -l k8s-app=cilium --tail=50 --prefix \
  | grep -iE 'error|failed|too large|verifier' || echo "no agent errors"
```

All three agents Ready. `BPF program is too large` or a verifier error here is the `bpf_host` size failure. The chart tag is in `ocirepository.yaml` (1.18.6 at last read). The upstream report's affected range was `>= 1.17.2, < 1.18.0`; this cluster's feature mix is still broad (netkit, BBR, DSR, maglev, XDP best-effort, BIG TCP). `maxUnavailable: 1` is why a bad program takes one node at a time. Re-read the tag before citing it.

## Switches

```bash
kubectl --kubeconfig="$KUBECONFIG" -n kube-system get cm cilium-config \
  -o jsonpath='{.data.enable-host-firewall}{"  "}{.data.policy-audit-mode}{"\n"}'
```

Enforcing prints `true  false`.

## Two-way bpf gate

On each cilium pod, resolve the host endpoint (identity labels contain `reserved:host`) and run `cilium-dbg bpf policy get <id>` inside `cilium-agent`.

Both must hold on all three nodes:

1. **Deny rows exist** for the Ceph ports in the policy (`fromEntities: world` expands to `reserved:world`, `world-ipv4`, and `world-ipv6`). No Deny rows means the selector matched nothing. The CCNP status still says Valid. `policy-enabled` on the host endpoint is `none`.
2. **`Allow Ingress ANY` is still there.** Its absence means `enableDefaultDeny` took effect. Roll back.

```bash
kubectl --kubeconfig="$KUBECONFIG" get ccnp ceph-lan-isolation -o jsonpath='{.status}'
```

That status is not the gate.

## Management plane

From a host that is not a node: `kubectl get --raw='/readyz'`, `kubectl -n kube-system get nodes`, and `nc -vz 10.10.10.11 50000` (talos-2 is `.12`, talos-3 is `.13`). Then confirm the denied Ceph ports fail from that same non-node host while RGW (`s3`) and Prometheus still work. The port list is the policy's `toPorts`, not a copy kept here.

## Rollback

1. Set `policyAuditMode: true` on the cilium HelmRelease and `flux reconcile kustomization cluster-apps --with-source`. Drops stop. The policy stays installed. Deleting the CCNP also stops drops.
2. If Flux cannot be reached, `kubectl delete ciliumclusterwidenetworkpolicy ceph-lan-isolation`. Fix Git or the next reconcile puts it back.
3. If the API server is unreachable, `talosctl` on port 50000 from a host that still has it. If that is gone, physical console (skill `talos-nodes`).

Host policy is enforced at the tc hook. With `bpf-lb-acceleration: best-effort`, traffic handled at XDP does not traverse it. Do not assume a deny covers an XDP-accelerated service path.
