# Why the Ceph host policy is shaped this way

## Default-deny is per direction and OR-ed

On the Cilium 1.18 CCNP CRD, a direction that has rules defaults `enableDefaultDeny` to true. `ingressDeny` counts as rules. The host endpoint, which is where a `nodeSelector` policy attaches, then denies every ingress not listed. That includes the Talos API, the Kubernetes API, kubelet, etcd, Cilium health, and BGP to the router.

Policies OR their default-deny bit. A new CCNP that forgets `enableDefaultDeny: {ingress: false}` turns deny on for the host even when `hostpolicy-ceph.yaml` sets it false. Set both directions false unless you have listed every flow that must survive.

The live policy's safety is that flag, not audit mode. Audit mode is off.

## Host-endpoint labels are a subset

Cilium drops well-known node labels before they reach the host endpoint, including `kubernetes.io/os`, `kubernetes.io/hostname`, `kubernetes.io/arch`, and the `beta.kubernetes.io/*` pair. Labels that survive include `node-role.kubernetes.io/control-plane`, `topology.kubernetes.io/region`, `topology.kubernetes.io/zone`, `extensions.talos.dev/*`, and `intel.feature.node.kubernetes.io/gpu`.

The first policy selected `kubernetes.io/os: linux`. Status Valid, zero Deny rows, Ceph still reachable from the LAN. The live selector is `node-role.kubernetes.io/control-plane` Exists. All three nodes are control-plane, so that covers the cluster. A worker node added later is not covered and its Ceph daemons stay LAN-exposed. Re-check the selector as part of adding one.

The upstream host-policy example that selects `kubernetes.io/hostname` hits this same filter. Confirm with `cilium-dbg bpf policy get`, not with the CCNP status. The measured sets are commented on `hostpolicy-ceph.yaml`. `scripts/ci/hostpolicy-ceph-selector-test.py` pins the live key.

## Why a rook-ceph NetworkPolicy cannot do this

Ceph daemons run with `spec.network.provider: host`. Pod NetworkPolicy selects pod endpoints. The mon, OSD, and RGW sockets are on the host. The CCNP is the object that can name those ports.

Denied ports live only in `hostpolicy-ceph.yaml` (mon msgr2 3300, msgr1 6789 so it cannot be rebound silently, RGW, and the rest of that list). Edit them there. Cluster-internal mon traffic is host and remote-node identity and is not the `world` entity this policy denies.

## bpf program size

Enabling the host firewall grows `bpf_host`. A program that fails to load shows up in the agent log, which is check 2a in [gates.md](gates.md). Recorded so it is not re-litigated: per-endpoint routes were incompatible with host policy before Cilium 1.10 and are not a concern on the 1.18 line this cluster runs. Re-read `ocirepository.yaml` before quoting the tag.
