---
name: networking
description: "Read before adding or editing an HTTPRoute, Gateway, HTTPRouteFilter, SecurityPolicy, external-dns annotation, LoadBalancer IP/BGP setting, NetworkPolicy or CiliumNetworkPolicy, or when a hostname is unreachable or DNS resolves wrong. Covers the DNS target coming from the parent Gateway, hand-made UniFi records, DNS-reply port:53 rules that never match, and supplementing chart-owned policies."
---

# Networking: Gateway API, DNS, BGP and NetworkPolicy

Stub. The condensed tripwires and `references/` for this skill land in a later
docs-restructure PR; until then the knowledge lives in the sources below. Read the
ones that match your task, and verify anything you rely on against the repo.

## Sources today

- [`kubernetes/apps/base/kube-system/cilium/Readme.md`](../../../kubernetes/apps/base/kube-system/cilium/Readme.md) - Cilium, BGP and LB IPAM
- [`docs/networking/bgp.md`](../../../docs/networking/bgp.md) - BGP and UniFi FRR
- [`docs/network/cmd.md`](../../../docs/network/cmd.md) - network diagnostics
- [`docs/network/envoy-gateway-internal-domains-analysis-2026-07.md`](../../../docs/network/envoy-gateway-internal-domains-analysis-2026-07.md) - Envoy Gateway analysis
- [`.taskfiles/network/README.md`](../../../.taskfiles/network/README.md) - task network:* recipes
- [`kubernetes/apps/base/ai/agentgateway/app/gateways/README.md`](../../../kubernetes/apps/base/ai/agentgateway/app/gateways/README.md) - DNS ownership worked case
- [`kubernetes/apps/base/monitoring/loki/app/networkpolicy.yaml`](../../../kubernetes/apps/base/monitoring/loki/app/networkpolicy.yaml) - DNS-reply rule example

`AGENTS.md` entries (search for the opening words):

- A plain `networking.k8s.io/v1` NetworkPolicy `ports:` entry
- Two DNS facts that make confident conclusions wrong
- `envoy-internal`/`envoy-external` are Envoy Gateway

## Related skills

- `cilium-host-policy`
- `authentik-terraform`
