# Rook-Ceph Configuration

## Overview

This directory contains the Rook-Ceph operator and cluster configuration for the home-ops Kubernetes cluster.

## Components

- **operator/**: Rook-Ceph operator deployment
- **cluster/**: CephCluster, CephBlockPool, CephFilesystem, and CephObjectStore configuration
- **backup/**: Backup configurations

## Post-Deployment Manual Configuration

Some RGW (RADOS Gateway) settings cannot be configured via Helm values and must be applied manually after deployment or cluster rebuild.

### 1. Set Default Realm/Zonegroup/Zone

The `rgw_realm` is configured in `helmrelease.yaml`, but the default assignments need to be set manually:

```bash
# Access the toolbox
kubectl exec -it -n rook-ceph deploy/rook-ceph-tools -- bash

# Set ceph-objectstore as the default realm
radosgw-admin realm default --rgw-realm=ceph-objectstore

# Set default zonegroup and zone
radosgw-admin zonegroup default --rgw-zonegroup=ceph-objectstore --rgw-realm=ceph-objectstore
radosgw-admin zone default --rgw-zone=ceph-objectstore --rgw-zonegroup=ceph-objectstore --rgw-realm=ceph-objectstore
```

### 2. Configure Zone System Keys

System keys are required for zone synchronization (clears "Access/Secret keys not found" dashboard warning):

```bash
# Create a system user for the zone
radosgw-admin user create --uid=zone.user --display-name="Zone System User" --system

# Note the access_key and secret_key from the output, then set them on the zone
radosgw-admin zone modify --rgw-zone=ceph-objectstore \
  --access-key=<ACCESS_KEY> \
  --secret=<SECRET_KEY> \
  --rgw-realm=ceph-objectstore

# Restart RGW pods to apply
kubectl rollout restart deployment -n rook-ceph -l app=rook-ceph-rgw
```

### 3. Dashboard Admin User Capabilities

Rook creates `dashboard-admin` user automatically, but additional capabilities may be needed:

```bash
# Add full admin capabilities for dashboard
radosgw-admin caps add --uid=dashboard-admin \
  --caps="buckets=*;users=*;usage=read;metadata=read;zone=read"
```

### 4. Clean Up Orphan Default Zone/Zonegroup (if needed)

If orphan `default` zone/zonegroup entries appear in the dashboard:

```bash
# Remove orphan zone and zonegroup
radosgw-admin zone delete --rgw-zone=default
radosgw-admin zonegroup delete --rgw-zonegroup=default

# Clean up orphan metadata from .rgw.root pool
rados -p .rgw.root rm zone_names.default
rados -p .rgw.root rm zonegroups_names.default
rados -p .rgw.root rm default.zone.
rados -p .rgw.root rm default.zonegroup.

# Remove orphan pools if they exist
ceph osd pool delete default.rgw.log default.rgw.log --yes-i-really-really-mean-it
ceph osd pool delete default.rgw.control default.rgw.control --yes-i-really-really-mean-it
ceph osd pool delete default.rgw.meta default.rgw.meta --yes-i-really-really-mean-it

# Restart RGW pods
kubectl rollout restart deployment -n rook-ceph -l app=rook-ceph-rgw
```

## Helm Values Reference

Key settings configured in `cluster/helmrelease.yaml`:

| Setting | Description |
|---------|-------------|
| `cephConfig.client.rgw.rgw_realm` | Prevents orphan default zone/zonegroup creation |
| `mgr.modules[].name: rgw` | Enables RGW mgr module for dashboard integration |
| `cephObjectStores[].spec.gateway.instances: 2` | HA RGW with 2 instances |
| `cephObjectStores[].spec.allowUsersInNamespaces: ["*"]` | Allow OBC in all namespaces |

## Troubleshooting

### Check RGW Status

```bash
# Sync status
radosgw-admin sync status --rgw-realm=ceph-objectstore

# List zones and zonegroups
radosgw-admin zone list
radosgw-admin zonegroup list

# Check .rgw.root pool contents
rados -p .rgw.root ls
```

### Verify Ceph Health

```bash
ceph health detail
ceph status
```

### Check RGW Logs

```bash
kubectl logs -n rook-ceph -l app=rook-ceph-rgw -c rgw --tail=100
```

## References

- Dashboard Templates: https://github.com/ceph/ceph/tree/main/monitoring/ceph-mixin/dashboards_out
- Dashboard Settings: https://docs.ceph.com/en/squid/mgr/dashboard/#enabling-the-embedding-of-grafana-dashboards
