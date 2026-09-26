# TrueNAS Graphite exporter mapping

Vendored mapping for `prom/graphite-exporter` (HelmRelease tag currently
`v0.17.0`). Upstream project:
<https://github.com/Supporterino/truenas-graphite-to-prometheus>

## This repo

| What | Path |
|------|------|
| Mapping ConfigMap source | `graphite_mapping.conf` (this directory); mounted at `/tmp/graphite_mapping.conf` |
| Dashboards | Retired. The three TrueNAS boards queried `cpu_temperature`, `disk_await`, `zfs_*` and the rest of the graphite-mapped names, and this Prometheus has none of them: `graphite_last_processed_timestamp_seconds` stays 0 because the NAS has never sent a sample. |
| HelmRelease | `../helmrelease.yaml` - LoadBalancer TCP/UDP 2003, metrics 9108 |

Mappings expect Graphite prefix `truenas` (see `match: 'truenas\.(.*)\....'`).
Point TrueNAS Graphite destination at this exporter's LoadBalancer, port 2003.
The Cilium IP pin `io.cilium/lb-ipam-ips: 10.10.0.190` is commented; look up the
live EXTERNAL-IP with `kubectl -n monitoring get svc graphite-exporter`.

## TrueNAS 25.04 / exporter config v2.1

Upstream warns that from exporter config **v2.1** you must also install their
`netdata.conf` on the TrueNAS host because TrueNAS 25.04 dropped default
metrics. This cluster vendors the mapping file only. It does **not** vendor
`netdata.conf`. Do not bump the mapping to v2.1 without adding that host
file, and do not put the TrueNAS boards back until a sample has actually
arrived (`graphite_last_processed_timestamp_seconds` > 0).

Bumping the mapping is a separate change; this Readme only records the gap.
