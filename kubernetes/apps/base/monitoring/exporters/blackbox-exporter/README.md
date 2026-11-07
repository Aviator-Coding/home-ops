# Blackbox exporter

`prometheus-community/prometheus-blackbox-exporter` with one module,
`tcp_connect` (no ICMP, so no `NET_RAW`). It answers one question today:
does the NAS accept NFS connections from inside the cluster?

| What | Path |
|------|------|
| Chart, values | `app/ocirepository.yaml`, `app/helmrelease.yaml` |
| Probe `nas-nfs` (TCP `nas.${SECRET_DOMAIN}:2049`, every 30s) | `app/probe.yaml` |
| `NASNFSExportDown` (critical, 5m), `NASNFSProbeMissing` (warning, 15m) | `app/prometheusrule.yaml` |

The Probe sets `jobName: nas-nfs`, so the series is
`probe_success{job="nas-nfs"}`. Keep that job name stable: alert rules
select on it.

## NASNFSExportDown

The NAS refused or timed out TCP 2049 for 5 minutes. Pods that mount the
export hang in `ContainerCreating` or block on I/O.

1. Is it the NAS or the path? From a workstation on the LAN:
   `nc -z nas.<domain> 2049`. Open from the LAN but failing in-cluster
   points at DNS or routing from the nodes, not the NAS.
2. Check the TrueNAS NFS service and the pool. Nothing in the cluster can
   fix a stopped NFS service.
3. After the NAS is back the alert resolves on its own. Pods stuck on a
   stale mount may need a restart.

## NASNFSProbeMissing

The series is gone: the exporter Deployment is down, or the Probe was
renamed or removed. Check `kubectl -n monitoring get deploy blackbox-exporter`
and `kubectl -n monitoring get probe nas-nfs`.
