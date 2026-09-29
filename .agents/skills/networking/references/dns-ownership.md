# DNS ownership

Split DNS: `network/cloudflare-dns` is public, `network/unifi-dns` is the LAN
webhook. Annotations:

- `external-dns.alpha.kubernetes.io/target: "internal.${SECRET_DOMAIN}"` or
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

## nslookup

Short names and `ndots:5` make search-domain behavior matter. Busybox
`nslookup` returns NXDOMAIN for a short name it should search. `getent hosts`
follows `resolv.conf`. A trailing dot (`name.`) suppresses search.
