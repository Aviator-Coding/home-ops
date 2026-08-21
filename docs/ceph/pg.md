# Ceph Placement Groups (PG) Configuration Guide

> **This cluster:** 6 OSDs, `size=3`, `bulk: true` on block + cephfs-data0,
> GitOps `mon_target_pg_per_osd` 100, autoscaler on. Healthy fingerprint from
> the changelog is **393 PGs active+clean** (point-in-time, 2026-06-17 /
> 2026-07-03). Backlog P4 (verify live `ceph osd pool autoscale-status`) is
> still open. `docs/ceph-cluster-changelog.md` owns current truth; the tables
> below are generic upstream notes, not this cluster's pin.
>
> Standing Ceph config is GitOps in `cluster/helmrelease.yaml`. Live
> `ceph config set` is incident-only and must be logged in the changelog.

## 📊 Overview

Placement Groups (PGs) are fundamental to Ceph's data distribution and performance. They determine how data is spread across OSDs and directly impact cluster efficiency, recovery speed, and overall performance.

---

## 🎯 Modern Approach: Autoscaling (Recommended)

**Ceph Nautilus+ (2019)** introduced automatic PG management, which is now the recommended approach for most deployments.

### Enable Autoscaling

```bash
# Enable autoscaling for a specific pool
ceph osd pool set <pool-name> pg_autoscale_mode on

# Set default autoscaling for new pools (incident-only; GitOps owns standing config)
ceph config set global osd_pool_default_pg_autoscale_mode on

# View autoscaling status and recommendations
ceph osd pool autoscale-status
```

### Configure Target Sizes

```bash
# Method 1: Absolute size
ceph osd pool set mypool target_size_bytes 100T

# Method 2: Relative ratio (recommended for multiple pools)
ceph osd pool set mypool target_size_ratio 1.0
```

### Adjust PG Target (Optional)

```bash
# This cluster's GitOps target is 100 (not 200). Do not apply 200 live.
# Upstream docs recommend ~200 for all but the smallest clusters.
ceph config set global mon_target_pg_per_osd 100
```

---

## 🔧 Legacy Manual Calculation

For older Ceph versions or when autoscaling is disabled, you'll need to calculate PGs manually.
The sample numbers below (3 OSDs, `size=2`) are generic tutorial values, **not** this
cluster (6 OSDs, `size=3`).

### Prerequisites: Gather Cluster Information

#### 1. **Number of OSDs**
```bash
ceph osd ls
```
**Sample Output:**
```
0
1
2
```
*Total OSDs: 3*

#### 2. **Number of Pools**
```bash
ceph osd pool ls
# or
rados lspools
```
**Sample Output:**
```
rbd
images
vms
volumes
backups
```
*Total pools: 5*

#### 3. **Replication Factor**
```bash
ceph osd dump | grep repli
```
**Sample Output:**
```
pool 0 'rbd' replicated size 2 min_size 2 crush_ruleset 0 object_hash rjenkins pg_num 64 pgp_num 64
pool 1 'images' replicated size 2 min_size 2 crush_ruleset 1 object_hash rjenkins pg_num 30 pgp_num 30
pool 2 'vms' replicated size 2 min_size 2 crush_ruleset 1 object_hash rjenkins pg_num 30 pgp_num 30
```
*Replication factor: 2*

### 📐 Calculation Methods

#### **Total PGs for Cluster**
```
Total PGs = (Total_OSDs × Target_PGs_per_OSD) ÷ Replication_Factor
```

**Example:**
- OSDs: 3
- Target PGs per OSD: 100-200 (recommended: 200)
- Replication: 2

```
Total PGs = (3 × 200) ÷ 2 = 300
Nearest power of 2: 512
```

#### **PGs per Pool**
```
PGs_per_pool = Total_PGs ÷ Number_of_pools
```

**Example:**
```
PGs per pool = 512 ÷ 5 = 102.4
Nearest power of 2: 128
```

### 📋 Power of 2 Reference Table

| Power | Value | Power | Value |
|-------|-------|-------|-------|
| 2⁰    | 1     | 2⁶    | 64    |
| 2¹    | 2     | 2⁷    | 128   |
| 2²    | 4     | 2⁸    | 256   |
| 2³    | 8     | 2⁹    | 512   |
| 2⁴    | 16    | 2¹⁰   | 1024  |
| 2⁵    | 32    | 2¹¹   | 2048  |

---

## 🛠️ Essential Commands

### Pool Management
```bash
# Create a new pool (autoscaling enabled)
ceph osd pool create <pool-name>

# Create pool with specific PG count (legacy)
ceph osd pool create <pool-name> <pg-number> <pgp-number>

# Enable/disable autoscaling
ceph osd pool set <pool-name> pg_autoscale_mode <on|off|warn>
```

### Monitoring & Information
```bash
# Get current PG count
ceph osd pool get <pool-name> pg_num
ceph osd pool get <pool-name> pgp_num

# View autoscaling status
ceph osd pool autoscale-status

# Get pool information
ceph osd pool ls detail
```

### Manual PG Adjustment
```bash
# Increase PG count (can only increase, not decrease)
ceph osd pool set <pool-name> pg_num <number>
ceph osd pool set <pool-name> pgp_num <number>

# Set target sizes for autoscaling
ceph osd pool set <pool-name> target_size_bytes <size>
ceph osd pool set <pool-name> target_size_ratio <ratio>
```

---

## ⚠️ Best Practices

### ✅ **Do:**
- Use autoscaling for new deployments (Nautilus+)
- Start with conservative PG counts and scale up
- Monitor cluster performance during PG changes
- Keep `pg_num` and `pgp_num` equal
- Plan for cluster growth with target sizes

### ❌ **Don't:**
- Set too many PGs initially (causes overhead)
- Decrease PG count (not supported)
- Ignore autoscaling recommendations
- Change PGs during high I/O periods

---

## 📈 Performance Guidelines

Generic upstream ranges (not this cluster's pin):

| Cluster Size | Target PGs/OSD | Notes |
|--------------|----------------|-------|
| Small (< 10 OSDs) | 100-150 | Conservative approach |
| Medium (10-50 OSDs) | 150-200 | Balanced performance |
| Large (50+ OSDs) | 200+ | Maximum parallelism |

> This cluster (6 OSDs): GitOps `mon_target_pg_per_osd` **100**; last recorded
> healthy `ceph status` was **393 active+clean**. See changelog backlog P4.
