# Ceph Toolbox Documentation

This guide covers how to use the Rook Ceph toolbox for managing your Ceph cluster operations.

## Table of Contents
- [Enabling the Toolbox](#enabling-the-toolbox)
- [Accessing the Toolbox](#accessing-the-toolbox)
- [Basic Commands](#basic-commands)
- [Cluster Health & Status](#cluster-health--status)
- [Storage Management](#storage-management)
- [Pool Operations](#pool-operations)
- [CephFS Operations](#cephfs-operations)
- [Object Storage Operations](#object-storage-operations)
- [Monitoring & Troubleshooting](#monitoring--troubleshooting)
- [Performance Tuning](#performance-tuning)
- [Maintenance Operations](#maintenance-operations)

## Enabling the Toolbox

### Permanent Enable
Add to your HelmRelease values:
```yaml
toolbox:
  enabled: true
```

Then reconcile:
```bash
flux reconcile hr rook-ceph-cluster -n rook-ceph
```

### Temporary Pod
For one-time operations:
```bash
kubectl run -i --tty --rm debug-ceph \
  --image=rook/ceph:v1.17.1 \
  --restart=Never \
  -n rook-ceph -- bash
```

## Accessing the Toolbox

### Interactive Shell
```bash
kubectl exec -it -n rook-ceph deployment/rook-ceph-tools -- bash
```

### Single Command
```bash
kubectl exec -n rook-ceph deployment/rook-ceph-tools -- ceph status
```

## Basic Commands

### Cluster Overview
```bash
# Overall cluster status
ceph status

# Detailed health information
ceph health detail

# Cluster configuration
ceph config dump

# Version information
ceph version
```

## Cluster Health & Status

### Health Checks
```bash
# Quick health check
ceph health

# Detailed health with explanations
ceph health detail

# Monitor cluster events
ceph -w

# Check cluster capacity
ceph df

# Detailed capacity per pool
ceph df detail
```

### Node & OSD Status
```bash
# List all OSDs
ceph osd ls

# OSD status and utilization
ceph osd status

# OSD tree (shows physical layout)
ceph osd tree

# Detailed OSD information
ceph osd dump

# Check specific OSD
ceph osd metadata <osd-id>
```

## Storage Management

### OSD Operations
```bash
# Mark OSD out (for maintenance)
ceph osd out <osd-id>

# Mark OSD in (return to service)
ceph osd in <osd-id>

# Stop OSD
ceph osd down <osd-id>

# Remove OSD from cluster
ceph osd crush remove osd.<osd-id>
ceph auth del osd.<osd-id>
ceph osd rm <osd-id>

# Check OSD usage
ceph osd df

# Reweight OSD
ceph osd reweight <osd-id> <weight>
```

### CRUSH Map Operations
```bash
# Show CRUSH map
ceph osd crush tree

# Show CRUSH rules
ceph osd crush rule list
ceph osd crush rule dump <rule-name>

# Create new CRUSH rule
ceph osd crush rule create-simple <rule-name> <root> <type>
```

## Pool Operations

### Pool Management
```bash
# List all pools
ceph osd lspools

# Create replicated pool
ceph osd pool create <pool-name> <pg-num> <pgp-num> replicated

# Create erasure-coded pool
ceph osd pool create <pool-name> <pg-num> <pgp-num> erasure

# Delete pool (requires confirmation)
ceph tell mon.* injectargs --mon-allow-pool-delete=true
ceph osd pool delete <pool-name> <pool-name> --yes-i-really-really-mean-it

# Pool statistics
ceph osd pool stats
ceph osd pool stats <pool-name>

# Pool configuration
ceph osd pool get <pool-name> all
ceph osd pool set <pool-name> <property> <value>
```

### Pool Properties
```bash
# Set pool size (replication factor)
ceph osd pool set <pool-name> size 3
ceph osd pool set <pool-name> min_size 2

# Enable compression
ceph osd pool set <pool-name> compression_algorithm zstd
ceph osd pool set <pool-name> compression_mode aggressive

# Set PG autoscaler
ceph osd pool set <pool-name> pg_autoscale_mode on

# Application tags
ceph osd pool application enable <pool-name> <app-name>
```

## CephFS Operations

### Filesystem Management
```bash
# List filesystems
ceph fs ls

# Show filesystem details
ceph fs get <fs-name>

# Create filesystem
ceph fs new <fs-name> <metadata-pool> <data-pool>

# Remove filesystem
ceph fs rm <fs-name> --yes-i-really-mean-it

# Filesystem status
ceph fs status <fs-name>

# Show active MDS daemons
ceph fs dump
ceph mds stat
```

### CephFS Pool Operations
```bash
# Add data pool to filesystem
ceph fs add_data_pool <fs-name> <pool-name>

# Remove data pool from filesystem
ceph fs rm_data_pool <fs-name> <pool-name>

# Set default data pool
ceph fs set <fs-name> default_data_pool <pool-name>
```

### Subvolume Management
```bash
# List subvolume groups
ceph fs subvolumegroup ls <fs-name>

# Create subvolume group
ceph fs subvolumegroup create <fs-name> <group-name>

# List subvolumes
ceph fs subvolume ls <fs-name> --group-name <group-name>

# Create subvolume
ceph fs subvolume create <fs-name> <subvol-name> --group-name <group-name>

# Get subvolume info
ceph fs subvolume info <fs-name> <subvol-name> --group-name <group-name>
```

## Object Storage Operations

### RGW Status
```bash
# List RGW instances
ceph orch ls rgw

# RGW statistics
radosgw-admin user list
radosgw-admin bucket list

# Zone and zonegroup info
radosgw-admin zone list
radosgw-admin zonegroup list
```

### User Management
```bash
# Create user
radosgw-admin user create --uid=<user-id> --display-name="<display-name>"

# List users
radosgw-admin user list

# User information
radosgw-admin user info --uid=<user-id>

# Generate access keys
radosgw-admin key create --uid=<user-id> --key-type=s3
```

## Monitoring & Troubleshooting

### Performance Monitoring
```bash
# Monitor cluster performance
ceph tell osd.* perf dump
ceph tell osd.* config show

# Monitor placement groups
ceph pg stat
ceph pg dump
ceph pg <pg-id> query

# I/O statistics
ceph osd perf
```

### Log Analysis
```bash
# Check cluster logs
ceph log last 100

# OSD logs
ceph tell osd.<id> log flush
```

### Debugging
```bash
# Debug specific components
ceph daemon osd.<id> config show
ceph daemon mon.<id> config show
ceph daemon mds.<id> config show

# Performance counters
ceph daemon osd.<id> perf dump
ceph daemon mon.<id> perf dump
```

## Performance Tuning

### OSD Performance
```bash
# Set OSD flags
ceph osd set noup          # Prevent OSDs from coming up
ceph osd set nodown        # Prevent OSDs from being marked down
ceph osd set norecover     # Disable recovery
ceph osd set norebalance   # Disable rebalancing
ceph osd set nobackfill    # Disable backfill

# Unset flags
ceph osd unset noup
ceph osd unset nodown
ceph osd unset norecover
ceph osd unset norebalance
ceph osd unset nobackfill
```

### Recovery Tuning
```bash
# Limit recovery operations
ceph tell 'osd.*' injectargs --osd-max-backfills=1
ceph tell 'osd.*' injectargs --osd-recovery-max-active=1

# Adjust recovery sleep
ceph tell 'osd.*' injectargs --osd-recovery-sleep=0.1
```

## Maintenance Operations

### Cluster Maintenance
```bash
# Set maintenance mode
ceph osd set noout         # Prevent OSDs from being marked out
ceph osd set norebalance   # Stop rebalancing

# Update crush map
ceph osd getcrushmap -o crushmap
crushtool -d crushmap -o crushmap.txt
# Edit crushmap.txt
crushtool -c crushmap.txt -o crushmap.new
ceph osd setcrushmap -i crushmap.new
```

### Backup Operations
```bash
# Export cluster configuration
ceph config-key dump
ceph auth export

# Export CRUSH map
ceph osd getcrushmap -o crushmap.backup

# Export monitor map
ceph mon getmap -o monmap.backup
```

### Recovery Operations
```bash
# Force create placement groups
ceph osd force-create-pg <pg-id>

# Repair inconsistent PGs
ceph pg repair <pg-id>

# Scrub operations
ceph osd deep-scrub <osd-id>
ceph pg scrub <pg-id>
ceph pg deep-scrub <pg-id>
```

## Common Use Cases

### Removing a Failed Disk
```bash
# 1. Mark OSD out
ceph osd out <osd-id>

# 2. Wait for rebalancing to complete
ceph status

# 3. Stop the OSD
ceph osd down <osd-id>

# 4. Remove from CRUSH map
ceph osd crush remove osd.<osd-id>

# 5. Remove authentication
ceph auth del osd.<osd-id>

# 6. Remove OSD
ceph osd rm <osd-id>
```

### Emergency Pool Deletion
```bash
# Enable pool deletion
ceph tell mon.* injectargs --mon-allow-pool-delete=true

# Delete pool (type name twice for confirmation)
ceph osd pool delete <pool-name> <pool-name> --yes-i-really-really-mean-it

# Disable pool deletion
ceph tell mon.* injectargs --mon-allow-pool-delete=false
```

### Cluster Recovery
```bash
# Check for stuck PGs
ceph pg dump_stuck

# Restart stuck processes
ceph tell osd.<id> restart
ceph tell mon.<id> restart
ceph tell mds.<id> restart

# Force PG creation
ceph osd force-create-pg <pg-id>
```

## Safety Notes

⚠️ **WARNING**: Many of these commands can cause data loss or cluster instability. Always:

- Take backups before major operations
- Test in non-production environments first
- Monitor cluster health during operations
- Have a rollback plan
- Understand the impact of each command

## Exit the Toolbox

```bash
exit
```

The toolbox pod will remain running for future use unless you disable it in the HelmRelease configuration.
