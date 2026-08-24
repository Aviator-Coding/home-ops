# Envoy Gateway Internal-Domain Reachability — 2026-07-03/04 Analysis

> **Snapshot 2026-07-03/04.** Do not use this file as the source of current
> chart versions or hostname counts. Git as of 2026-08: gateway-helm is
> **v1.8.3** (`kubernetes/apps/base/network/envoy-gateway/app/ocirepository.yaml`);
> data plane `envoyproxy/envoy:v1.39.0`. Upstream **Envoy Gateway v1.9.0**
> exists ([announcement](https://gateway.envoyproxy.io/news/releases/v1.9/),
> 2026-07-29). Re-evaluate Option B against v1.9 notes before acting.
>
> Option A (drop `GatewayNamespaceMode`) is still open - HelmRelease still has
> `deploy.type: GatewayNamespace`.
>
> Split DNS in-cluster is UniFi + external-dns (`network/unifi-dns`), not
> `k8s_gateway` (that app was deleted 2025-10-19). Hypothesis 4 below is
> historical wording; the evidence used the UniFi resolver, which is still
> the path.
>
> The 58/60 hostname matrix is a 2026-07-04 01:10–01:25 UTC probe window, not
> a current inventory. The residual gatus table is 2026-07-04; `ai/comfyui`
> parked-503 was still true in git at the 2026-08 audit.

| Field | Value |
|-------|-------|
| **Reported symptom** | "Internal domains unreachable" — apps behind the `envoy-internal` Gateway (VIP 10.50.0.26) during/after the 2026-06-30 → 07-03 Ceph/OOM outage and its recovery |
| **Component** | Envoy Gateway v1.8.x (controller `envoy-gateway` + proxy fleets `envoy-internal`/`envoy-external`, ns `network`), with cilium BGP VIP announcement, split DNS, and the wildcard TLS cert as adjacent suspects |
| **Verdict** | **Transient, self-resolved.** The user-initiated full 3-node reboot (~19:50–20:00 UTC 07-03) restarted the EG controller AND both proxy fleets together — accidentally executing the exact known-good fix sequence from the 2026-06-23 incident (controller first, then proxies). Gateways re-`Programmed` at 20:37:14Z (internal) / 20:37:25Z (external). At probe time (01:10–01:25 UTC 07-04) 58/60 internal hostnames were healthy via the VIP **and** via real DNS; zero DNS/TCP/TLS-class failures. The 2 residual 503s are pre-existing app faults. |
| **Severity** | low (after the fact) — but the incident re-confirms the standing GatewayNamespaceMode freeze risk documented 2026-06-23, and it ran through a total monitoring blind spot |
| **Related** | [`hardware-incidents.md` [2026-06-30]](../hardware-incidents.md) (the trigger outage), `kubernetes/apps/base/network/envoy-gateway/app/helmrelease.yaml` + `prometheusrule.yaml` (the 2026-06-23 GatewayNamespaceMode incident record) |

## Summary

During the 2026-06-30 → 07-03 storage outage ("OOMController kill storm → 100% PGs inactive", see hardware-incidents.md) internal domains were reported unreachable. This was expected — most backends were frozen on dead RBD/CephFS mounts, cilium-agent was being OOM-killed (one documented ~40 s VIP outage at 13:36Z 07-03), and gatus/Prometheus were themselves down. The question this analysis answers is whether the *gateway layer* was independently broken — specifically whether the documented EG v1.8.1 stale-xDS-endpoint freeze (2026-06-23) recurred, which the recovery-day mass reboot (every pod IP in the cluster changed at once) would have maximally triggered.

**It did not recur — or if it began, it never survived the reboot.** The reboot restarted controller and proxies together, which is precisely the June runbook fix (fresh controller → fresh xDS state → proxies resync on fresh SA tokens). Every layer was verified healthy post-recovery with the evidence below; each alternative hypothesis is explicitly ruled out. Two red herrings encountered during diagnosis are documented so future debuggers don't chase them.

## Timeline (UTC)

| Time | Event | Evidence |
|------|-------|----------|
| 06-30 ~17:07 | OOMController kill storm begins on talos-1/talos-2 → over the following hours 4/6 OSDs down, 100% PGs inactive; all RBD/CephFS IO frozen ~3 days; monitoring blind (node-exporter disabled, VictoriaMetrics not deployed) | hardware-incidents.md [2026-06-30] |
| 07-03 13:30–15:04 | Recovery ops: load shed, `min_size=1` windows, 14:46–15:03 kill storms on ALL 3 nodes (cilium-agent among victims), 15:04 live OOMConfig patch → storms stop | hardware-incidents.md |
| 07-03 17:16:33 | EG controller restart #16 (recovery churn) — the "previous" container instance starts | pod `lastState.terminated.startedAt=2026-07-03T17:16:33Z`; previous log first line 17:16:33Z |
| 07-03 ~17:20 | Last Prometheus sample before the metrics gap (Prometheus rode the storage freeze) | no samples for `envoy_control_plane_connected_state` 17:20 → 20:38Z |
| 07-03 ~19:50–20:00 | **User-initiated full reboot of all 3 nodes** — clears the D-state wedge; all 6 OSDs up in ~60 s; every pod IP in the cluster changes | hardware-incidents.md |
| 07-03 20:33–20:34 | Previous controller container's last log line 20:33:13Z; terminated **exit 255, reason `Unknown`, finishedAt 20:34:20Z** — node-reboot kill, NOT the June exit-137 liveness kill | `kubectl describe` lastState; `eg-controller-previous.log` tail |
| 07-03 20:36 | Controller restart #17; both proxy fleets' `envoy` containers restart (~20:36–20:44) | proxy `BootstrapConfigDump last_updated=2026-07-03T20:36:06.553Z`; pod RESTARTS "2 (4h46m ago)" at 01:22Z |
| 07-03 20:37:14 / 20:37:25 | Gateways `envoy-internal` / `envoy-external` **Accepted + Programmed = True** (lastTransitionTime), stable since | `kubectl get gateway -o yaml` |
| 07-03 20:38 | First post-gap Prometheus sample: `envoy_control_plane_connected_state = 1` on both proxies — connected from the first scrape onward | Prometheus query during diagnosis |
| 07-04 00:40 | node-exporter re-enabled (monitoring restored per the hardware-incident durable fixes) | hardware-incidents.md |
| 07-04 ~00:50 | cilium DaemonSet re-rolled to Guaranteed QoS; one-sample gatus blips ~00:33Z on readarr/rsshub/seerr/changedetection during the churn window | gatus history |
| 07-04 00:56:28 | Last xDS config push observed in the proxy config dump — **after** the cilium re-roll, proving live xDS delivery through the churn | `ClustersConfigDump ... last_updated .. 2026-07-04T00:56:28.435Z` |
| 07-04 01:10–01:25 | Probe window: **58/60 internal hostnames healthy** via `--resolve host:443:10.50.0.26` AND via real DNS; zero DNS/TCP/TLS failures; external gateway contrast (3/3) also green | symptom matrix |

## Symptom matrix (probe window 01:10–01:25 UTC 07-04)

58 HTTPRoutes / 60 hostnames parented to `envoy-internal`. Full per-hostname probes (`curl -m 8 --resolve <host>:443:10.50.0.26`) from the LAN host:

- **58/60 OK** — expected 200/30x (or documented expected 4xx: `llm-api` 401 key-auth, `tts`/`playwright` 404 no route at `/`, `mcp` 406 vmcp rejects plain GET). All with `ssl_verify_result=0`, all < 120 ms.
- **2/60 HTTP-503** — `comfyui` (backend Deployment deliberately 0/0, dedicated-VRAM default-off runbook) and `hermes-webui` (hermes pod 2/3 CrashLoopBackOff, 56+ restarts; `:12321` times out even in-cluster). Both proven app-side; the gateway is truthfully reporting dead backends.
- **Zero** DNS-fail, TCP-timeout, TCP-refused, TLS-fail, or hang classes observed.
- **Real-DNS path** (no `--resolve`, LAN resolver 10.0.0.1): grafana/gatus/homepage/sonarr/open-webui all resolve to 10.50.0.26 and return the same codes — the full browser-equivalent path works.
- **External contrast** (via 10.50.0.21): echo 302, plex 401, hass 302 — external gateway equally healthy.

## Hypothesis adjudication

Every hypothesis from the diagnosis plan, with the evidence line that decides it:

| # | Hypothesis | Verdict | Deciding evidence |
|---|-----------|---------|-------------------|
| 1 | **Stale xDS endpoints** (the 2026-06-23 EG bug: proxies frozen on pre-reboot pod IPs) | **RULED OUT** (post-reboot) | 8/8 sampled `/clusters` endpoint IPs match live EndpointSlices (4 re-verified at write time: grafana 10.42.1.116, sonarr 10.42.0.35, homepage 10.42.0.143, jellyfin 10.42.1.32 — exact match). `cx_connect_fail = 0` on all healthy backends. `control_plane.connected_state: 1` on both proxies since the first post-reboot scrape (20:38Z). Config pushes observed as late as 00:56:28Z — *after* the 00:50Z cilium re-roll. Snapshot version advanced live (clusters dump `version_info=400` at capture; controller delta watches at version `409` by 01:17Z) — the config is moving, not frozen. |
| 2 | **Controller dead / xDS auth storm** (June signature: exit-137 liveness kill from kubejwt token-expiry flood) | **RULED OUT** | Last termination: **exit 255, reason `Unknown`, finishedAt 20:34:20Z** — the node reboot, not a liveness kill. `grep -icE 'error\|unauthenticated\|token\|failed'` over both controller logs (~6 MB, previous 17:16→20:33Z + current captured window 00:39→01:17Z): **0 matches**. `xds_cluster.update_success: 3389`, `update_failure: 4` (transient, during churn). |
| 3 | **VIP not announced** (cilium restarted 500+ times during the incident, then re-rolled) | **RULED OUT** | Announcement is **BGP**, not L2: `ciliumbgpclusterconfig/l3-bgp-cluster-config` + peer config, 295d old; `kubectl get ciliuml2announcementpolicy -A` → *No resources found*. `Test-NetConnection 10.50.0.26 -Port 443` from the LAN → `TcpTestSucceeded: True`; every curl in the matrix connected. (Cilium kills DID cause a ~40 s VIP outage at 13:36Z 07-03 mid-incident — transient, fixed by the Guaranteed-QoS change.) |
| 4 | **DNS broken** (k8s_gateway/split DNS rebooted) | **RULED OUT** | LAN resolver (unifi): `grafana.sklab.dev.` → CNAME `internal.sklab.dev` → **10.50.0.26**; `sonarr` same; `echo.sklab.dev.` → CNAME `external.sklab.dev` → 10.50.0.21 + Cloudflare AAAAs. Real-DNS curls hit the correct VIP and succeed. |
| 5 | **TLS/cert swap** (cert-manager v1.20.3 upgrade completed the same evening) | **RULED OUT** | Live handshakes on both VIPs serve **CN=sklab.dev, issuer Let's Encrypt, notBefore Jun 15 2026, notAfter Sep 13 2026** — the same pre-upgrade wildcard; `ssl_verify_result=0` on all 60 probes; `sds.network/sklab-dev-production-tls.update_success: 1`, `update_failure: 0`. |
| 6 | **Backends dead** | **RULED OUT** (as a gateway-layer cause) | In-cluster direct: `grafana.monitoring.svc:80/api/health` → 200, `sonarr.downloads.svc:8989` → 200. The only dead backend (`hermes.ai.svc:12321` → timeout) is reported truthfully as 503 by the gateway. |

**Root cause of the report:** during the outage window itself, unreachability was real but *not gateway-specific* — storage-frozen backends, OOM-killed cilium-agent, and dead monitoring. Whatever state the gateway layer was in during that window (unknowable — see detection gaps), the 19:50 full reboot restarted controller + proxies in the known-good order and the data plane was verifiably converged and live-updating from 20:37Z onward. No fix action was required or taken; the diagnosis was evidence-capture only.

## Red herrings (do not chase these next time)

1. **Stale `cilium-l2announce-network-envoy-*` leases.** `kube-system` contains leases `cilium-l2announce-network-envoy-internal` / `-external` with an **EMPTY holder and renewTime 2025-10-19T06:31Z**. These are dead artifacts of a long-removed CiliumL2AnnouncementPolicy (none exists today) — they look exactly like "the VIP lease was lost 8 months ago" but mean nothing. VIP announcement here is **BGP** (`l3-bgp-cluster-config`). Verify reachability with a TCP test, not the lease.
2. **Windows `nslookup` without a trailing dot.** The host's search suffix `home.sklab.dev` gets appended first, and a wildcard record answers `*.home.sklab.dev → 10.50.0.27` — which is the **agentgateway `internal` LB in ns `ai`**, not envoy-internal. `nslookup grafana.sklab.dev` therefore "resolves to the wrong IP" while every real client (browser, curl) using the OS resolver path works fine. Always query with a trailing dot (`grafana.sklab.dev.`) when debugging.

## Residual failures (pre-existing, app-level — make the status page LOOK broken)

Five gatus checks sat at **0.000000% 24 h uptime**, i.e. failing since *before* this incident. None are gateway faults. **Table as of 2026-07-04** (do not treat hermes-webui / agentmemory / authentik-outpost / mcp-gateway rows as current without a live probe):

| Check | Status | Actual cause | Follow-up |
|-------|--------|--------------|-----------|
| `ai/comfyui` | 503 | Deployment deliberately scaled 0/0 (dedicated-VRAM default-off runbook) | Make the gatus check conditional or drop it; 503-when-parked is by design |
| `internal/hermes-webui` | 503 | hermes pod 2/3 CrashLoopBackOff (56+ restarts); `:12321` times out in-cluster (`cx_connect_fail: 142` on the proxy — the gateway keeps trying) | Fix the hermes webui container (known pre-existing image bug) |
| `internal/agentmemory` | 404 | Stale check path expectation | Update the gatus check definition |
| `internal/authentik-outpost` | 404 | Stale check path expectation | Update the gatus check definition |
| `internal/mcp-gateway` | 406 | vmcp rejects plain GET (correct behavior) | Check should expect 406 or POST a real MCP request |

Minor noise, for completeness: single `cx_connect_fail: 1` counters on emqx/n8n/linkwarden/paperless/obsidian-livesync/open-webui (one-shot fails during post-reboot churn, all healthy since), and `zigbee2mqtt` rule/1 (`:8080` websocket backend) at 52 — z2m itself serves 200 via the gateway.

## Detection gaps

- **Prometheus has no samples 17:20 → 20:38 UTC 07-03** — it rode the storage freeze; node-exporter was only re-enabled 00:40Z 07-04. The `EnvoyProxyControlPlaneDisconnected` alert (shipped after June) **legitimately did not fire**: `connected_state` was `1` at every instant it was actually scraped. "No alert" during the blind window is **absence of data, not absence of failure** — whether the proxies were frozen 17:07 06-30 → 19:50 07-03 is unknowable from metrics.
- **Gatus was itself down** during the outage/reboot window (in-cluster, storage-dependent) and retains only ~50 samples per endpoint (~50 min at 1-min interval) — its "99.65% 24 h uptime" for grafana/gatus post-recovery cannot see the window either.
- Consequence: the monitoring-stack durable fixes from the hardware incident (node-exporter + PSI/OSD alerts) are also what closes *this* gap. An external (out-of-cluster) probe of `https://gatus.sklab.dev` via the VIP would additionally catch "gateway down + monitoring blind" as a class; worth considering (e.g. a healthchecks.io-style dead-man switch on gatus itself).

## Standing risk & durable-fix proposal (PROPOSAL ONLY — no changes made)

### The risk that remains armed

`GatewayNamespaceMode` is still enabled (`config.envoyGateway.provider.kubernetes.deploy.type: GatewayNamespace` in `kubernetes/apps/base/network/envoy-gateway/app/helmrelease.yaml`). In this mode the proxies authenticate to the controller's xDS server with **projected ServiceAccount JWT tokens** validated by a kubejwt interceptor (hardening added upstream in v1.8.1). The 2026-06-23 incident chain — stale proxies presenting expired SA tokens → auth-storm floods the controller → liveness kill crash-loop → xDS frozen → proxies pinned to dead pod IPs — re-arms on any future event that (a) breaks token refresh/validation and (b) coincides with pod-IP churn. This incident's mass reboot was the maximal churn trigger; we got lucky that the reboot itself also executed the fix order.

Notably, this cluster gains **nothing** from GatewayNamespaceMode: both Gateways (`envoy-internal`/`envoy-external`) live in ns `network`, which is *also* the controller's namespace. The mode's entire purpose is multi-tenant isolation of proxies into per-Gateway namespaces — here it changes nothing topologically and only adds the JWT/xDS-auth failure surface.

### Option A (recommended): drop GatewayNamespaceMode

**Change** — in `kubernetes/apps/base/network/envoy-gateway/app/helmrelease.yaml`, remove the `deploy` block (default is ControllerNamespace mode):

```yaml
    config:
      envoyGateway:
        provider:
          type: Kubernetes
          kubernetes: {}   # remove `deploy: { type: GatewayNamespace }`
```

**What it eliminates**: in ControllerNamespace mode the proxies mount the controller-namespace certgen mTLS certs directly for xDS — there is **no projected-SA-token / kubejwt path at all**. The June failure class ceases to exist structurally.

**Blast radius / cutover facts:**

- The controller deletes the GatewayNamespace-mode proxy Deployments/Services and creates ControllerNamespace-mode ones (generated names change to `envoy-<gw-ns>-<gw-name>-<hash>` style) — a **brief data-plane interruption** while the new pods start and the LB Services are recreated.
- Both LB Services are recreated in ns `network` (same namespace as before, since Gateways live there): the **`lbipam.cilium.io` VIP annotations propagate from the Gateway infrastructure**, so 10.50.0.26/.21 should re-attach, and BGP re-announces them — but expect seconds-to-a-minute of VIP unreachability; external-dns records point at the VIPs (CNAME targets) and need no change.
- Anything keyed to the old resource names needs review after cutover: `pdb.yaml`, `observability.yaml` (PodMonitor/selectors), the Grafana dashboard, and the `EnvoyProxy` CR's `envoyDeployment.patch` (label selectors are name-independent, but verify).
- The `EnvoyProxyControlPlaneDisconnected` / `ControllerCrashLooping` PrometheusRules stay — they remain valid tripwires in either mode.

**Suggested window**: single evening maintenance window, cluster otherwise healthy (Ceph HEALTH_OK, no pending Flux changes). Sequence: merge the HR change → watch controller reconcile → confirm new proxy pods Ready + Gateways Programmed → `Test-NetConnection` both VIPs → spot-curl 5 hostnames → re-run the 60-hostname matrix. Rollback = revert the commit (the controller will recreate the GatewayNamespace-mode resources the same way).

### Option B: upgrade Envoy Gateway and keep the mode

Checked upstream (2026-07-04):

- **v1.6.0** already "fixed service account token handling in GatewayNamespaceMode to use SDS for properly refreshing expired tokens" — yet the June incident happened on **v1.8.1**, i.e. the refresh fix does not close the whole class (the June freeze involved the *controller* crash-looping under an auth storm, after which proxies could not resync).
- **v1.8.1** (2026-06-04) added the xDS auth hardening itself (unary interceptor + SotW validation — the very kubejwt layer that stormed) and fixed the xDS server serving a stale cert after cert-manager rotation.
- **v1.8.2** (2026-06-30, latest as of writing) contains **no fix** for the SA-token auth-storm / controller-liveness class; release notes cover unrelated xDS/rate-limit/status fixes. No v1.9 release exists yet.

Verdict: an upgrade to v1.8.2 is fine hygiene but does **not** remove the standing risk. There is no released fix to wait for.

### Recommendation

**Adopt Option A.** GatewayNamespaceMode provides zero benefit in a single-tenant cluster whose Gateways already share the controller namespace, and it is the sole enabler of the June freeze class. Do it in a planned window with the verification sequence above, keep the PrometheusRules, and optionally bump to v1.8.2 in the same PR. Until then, the operational runbook stands: on any suspicion of frozen routing, `kubectl -n network rollout restart deploy envoy-gateway`, wait Ready, then restart `envoy-internal` + `envoy-external` — controller first, proxies second.

## Cross-references

- [`docs/hardware-incidents.md` [2026-06-30]](../hardware-incidents.md) — the OOMController/Ceph outage whose recovery reboot bookends this incident
- `kubernetes/apps/base/network/envoy-gateway/app/helmrelease.yaml` — GatewayNamespaceMode config + 2026-06-23 incident comment block
- `kubernetes/apps/base/network/envoy-gateway/app/prometheusrule.yaml` — the June tripwires (`EnvoyProxyControlPlaneDisconnected`, `EnvoyGatewayControllerCrashLooping`) and the restart runbook
- Upstream: [Gateway Namespace Mode docs](https://gateway.envoyproxy.io/latest/tasks/operations/gateway-namespace-mode/), [v1.6.0 notes](https://gateway.envoyproxy.io/news/releases/notes/v1.6.0/) (SA-token SDS refresh), [v1.8.1 notes](https://gateway.envoyproxy.io/news/releases/notes/v1.8.1/) (xDS auth hardening, stale xDS cert fix), [v1.8.2 notes](https://gateway.envoyproxy.io/news/releases/notes/v1.8.2/) (no auth-storm fix)
