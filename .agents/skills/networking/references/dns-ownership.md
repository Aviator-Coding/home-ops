# DNS ownership

Split DNS: `network/cloudflare-dns` is public, `network/unifi-dns` is the LAN
webhook. Annotations:

- `external-dns.kubernetes.io/target: "internal.${SECRET_DOMAIN}"` or
  `"external.${SECRET_DOMAIN}"` on the **Gateway** is what the webhook uses
  for `gateway-httproute` sources.
- The same annotation on an HTTPRoute is not the control. A dual-parent route
  publishes both targets. The webhook keeps the first and warns about once a
  minute.

## Hand-made records

Registry TXT twin: `k8s.main.<type>-<name>.${SECRET_DOMAIN}`. No twin means
the record was created in the UniFi controller. external-dns `--policy=sync`
will not converge it.

The wildcard `*.${SECRET_DOMAIN} -> 10.50.0.27` has no twin. Unclaimed names
resolve to the AI gateway. No manifest edit removes that wildcard.

Read the webhook before blaming a manifest:

```bash
kubectl -n network port-forward deploy/unifi-dns 18888:8888
curl -H 'Accept: application/external.dns.webhook+json;version=1' localhost:18888/records
```

## DNS replies and NetworkPolicy

An ingress `ports:` match is the destination port on the selected pod. CoreDNS
replies to the querier's ephemeral port. `port: 53` never matches that packet.
Cilium `kubeProxyReplacement` with DSR and `socketLB` `hostNamespaceOnly` also
fails to correlate the UDP reply when the querier's node hosts a CoreDNS
replica, so the reply is policy-denied unless a rule admits it without a port.

The from-kube-dns rule therefore has no `ports:` key. That shape is on:

- `kubernetes/components/dragonfly/networkpolicy.yaml` (all Dragonfly instances)
- chart policies that cannot be edited in git, closed with a second
  NetworkPolicy using the chart's own `podSelector` (`flux-operator`,
  grafana image-renderer)

Other namespaces that copied the same rule are owned with those apps. When
you add a restrictive ingress policy that can share a node with CoreDNS, use
the no-port form.

`cilium monitor --type drop` shows the ephemeral destination port on a dropped
reply.

## Same-node Service replies (cilium/cilium#47913)

The CoreDNS correlation failure above is one case of an open upstream bug,
[cilium/cilium#47913](https://github.com/cilium/cilium/issues/47913). It
needs netkit (`datapath-mode: netkit`), endpoint routes and
`bpf-lb-sock-hostns-only: true`, all live here. When a Service backend runs
on the client's own node, local delivery enforces the client's ingress policy
twice. The second pass runs after reverse NAT, so the reply's source is the
ClusterIP, not the backend. That misses the backend-keyed conntrack entry and
the reply is a new flow: `Policy denied`. Pod-to-pod traffic without a
Service, and backends on another node, are not affected.

Rule while it is open: a CiliumNetworkPolicy (or NetworkPolicy) with
ingress rules must admit every Service peer the pod connects to with a
`fromEndpoints` rule and no `toPorts`, commented
`# reply traffic: cilium/cilium#47913, remove toPorts-free rule when fixed`.
That covers CoreDNS, databases, dragonfly, LiteLLM, LoadBalancer IPs whose
backends are pods (envoy-internal), and `fromEntities: kube-apiserver` for
the `kubernetes` Service. An egress allow toward the peer does not help; the
drop is on ingress.

Signature on the client's node:

```bash
# Hubble: the backend's Service port as source, a SYN,ACK, no verdict match
selfhosted/paperless-ngx-dragonfly:6379 <> selfhosted/paperless-ngx-...:45876 policy-verdict:none INGRESS DENIED (TCP Flags: SYN, ACK)
# cilium-dbg bpf ct list global: a spurious IN entry from the ClusterIP, RevNAT=0,
# next to the legitimate OUT entry to the backend pod IP with a RevNAT id
TCP IN 10.43.245.253:6379 -> 10.42.2.45:<eph> ... RevNAT=0 SourceSecurityID=72166
TCP OUT 10.42.2.45:<eph> -> 10.42.2.120:6379 ... RevNAT=182
```

The fix, cilium/cilium#47914, makes the agent reject netkit + endpoint routes
+ L7 proxy. Review a Cilium bump for it: skill `renovate-review`.

Incident, 2026-11-07: six CiliumNetworkPolicies (#2425-#2430) merged without
peer reply rules; paperless-ngx login hung on dragonfly replies on talos-1, and
all six were reverted in #2431.

## Staleness alert

`ExternalDNSStale` in `network/cloudflare-dns/app/prometheusrule.yaml` is
unselectored on purpose: one rule covers cloudflare-dns and unifi-dns. The
threshold is 180s, three times `--interval=1m`. A 60s threshold sits inside
the healthy staleness sawtooth and stays true on a healthy controller.

## nslookup

Short names and `ndots:5` make search-domain behavior matter. Busybox
`nslookup` returns NXDOMAIN for a short name it should search. `getent hosts`
follows `resolv.conf`. A trailing dot (`name.`) suppresses search.

external-dns v0.22+ reads only the GA prefix `external-dns.kubernetes.io/`; an
`external-dns.alpha.kubernetes.io/*` annotation is silently ignored. On chart
1.23.0 (2026-11-04) the old prefix made `cloudflare-dns` drop the Gateway
target, delete the public CNAMEs and fail on proxied A records for the
gateway IP (Cloudflare 9003). Never reintroduce the `alpha.` prefix.
