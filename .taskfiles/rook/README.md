# Rook Ceph Management Tasks

This Taskfile provides automated tasks for managing Rook Ceph storage cluster operations, including disk wiping and data cleanup for a 3-node Talos cluster.

## 🏗️ Prerequisites

- Kubernetes cluster with kubectl configured (context: `admin@kubernetes`)
- [Task CLI](https://taskfile.dev/) installed
- Rook Ceph operator deployed
- Required template files and scripts in place

## 📁 Required Files Structure

Ensure these files exist before running tasks:

```
.taskfiles/Rook/
├── Taskfile.yaml          # This file
├── README.md             # This documentation
├── scripts/
│   └── wait-for-job.sh   # Job waiting script
└── templates/
    ├── WipeDiskJob.tmpl.yaml    # Disk wiping job template
    └── WipeDataJob.tmpl.yaml    # Data cleanup job template
```

## 🚀 Available Tasks

### Validation Tasks

#### `validate-cluster`
Validate cluster connectivity and readiness.

```bash
task rook:validate-cluster
```

#### `validate-templates`
Validate all required template files exist.

```bash
task rook:validate-templates
```

#### `validate-all`
Run all validation checks.

```bash
task rook:validate-all
```

#### `validate-wipe`
Validate that disk wipe was successful by checking for filesystem signatures.

```bash
task rook:validate-wipe
```

#### `pre-wipe-validation`
Comprehensive validation before wiping disks (includes cluster validation, template validation, backup, and disk checks).

```bash
task rook:pre-wipe-validation
```

#### `post-wipe-validation`
Comprehensive validation after wiping disks (includes wipe validation, status check, cleanup, and health check).

```bash
task rook:post-wipe-validation
```

### Backup Tasks

#### `backup-rook-config`
Backup current Rook configuration to `/tmp/rook-backups/`.

```bash
task rook:backup-rook-config
```

### Disk Status Tasks

#### `check-disks`
Check disk status on all nodes.

```bash
task rook:check-disks
```

#### `check-node-disks`
Check disk status on specific node using debug pods.

```bash
task rook:check-node-disks node=talos-1
```

#### `check-disks-talos`
Check disk status on all nodes using Talos CLI.

```bash
task rook:check-disks-talos
```

#### `check-node-disks-talos`
Check disk status on specific node using Talos CLI.

```bash
task rook:check-node-disks-talos node_ip=10.0.100.11
```

#### `check-wipe-node`
Check wipe status on specific node by examining disk signatures.

```bash
task rook:check-wipe-node node=talos-1
```

### Core Operations

#### `reset-disk`
Reset a single disk on a specific node using privileged containers.

```bash
task rook:reset-disk node=talos-1 disk=/dev/disk/by-id/nvme-Samsung_SSD_980_PRO_2TB_S6B0NL0W412707M
```

**Parameters:**
- `node`: Target node name (required)
- `disk`: Full disk path including `/dev/disk/by-id/` (required)

**What it does:**
- Creates a Kubernetes job on the target node
- Wipes disk partition table and data
- Runs `sgdisk --zap-all`, `dd`, and `blkdiscard` operations

#### `reset-data`
Reset Rook data directories on a specific node.

```bash
task rook:reset-data node=talos-1
```

**Parameters:**
- `node`: Target node name (required)

**What it does:**
- Removes `/var/lib/rook` and `/var/lib/ceph` directories
- Cleans up any leftover Rook metadata

### 🔥 Destructive Operations

#### `wipe-all`
⚠️ **DESTRUCTIVE**: Wipes all disks and data across all nodes.

```bash
task rook:wipe-all
```

#### `wipe-all-with-progress`
⚠️ **DESTRUCTIVE**: Wipes all disks and data with progress tracking and automatic backup.

```bash
task rook:wipe-all-with-progress
```

**Features:**
- Automatic configuration backup before wiping
- Progress indicators showing completion status
- Enhanced logging and status updates

**Execution order:**
1. Creates backup of current Rook configuration
2. Wipes all disks on talos-1
3. Wipes all disks on talos-2
4. Wipes all disks on talos-3
5. Resets all data directories

### Node-Specific Operations

#### `wipe-talos-1`
Wipes all configured disks on talos-1:
- `nvme-Lexar_SSD_NM790_4TB_NME714W100393P2202`
- `nvme-Samsung_SSD_980_PRO_2TB_S6B0NL0W412707M`

```bash
task rook:wipe-talos-1
```

#### `wipe-talos-2`
Wipes all configured disks on talos-2:
- `nvme-Samsung_SSD_990_PRO_2TB_S7KHNJ0WC55436E`
- `nvme-Lexar_SSD_NM790_4TB_NME714W101694P2202`

```bash
task rook:wipe-talos-2
```

#### `wipe-talos-3`
Wipes all configured disks on talos-3:
- `nvme-Samsung_SSD_980_PRO_2TB_S69ENL0TC06068B`
- `nvme-Lexar_SSD_NM790_4TB_NL1948W100519P2202`

```bash
task rook:wipe-talos-3
```

### Quick Operations

#### `quick-wipe-1`, `quick-wipe-2`, `quick-wipe-3`
Complete node reset (disks + data) for individual nodes:

```bash
task rook:quick-wipe-1  # talos-1: disks + data
task rook:quick-wipe-2  # talos-2: disks + data
task rook:quick-wipe-3  # talos-3: disks + data
```

### Utility Tasks

#### `status`
Check the status of running wipe jobs with enhanced output.

```bash
task rook:status
```

#### `cleanup`
Clean up any leftover wipe jobs with comprehensive cleanup.

```bash
task rook:cleanup
```

#### `emergency-stop`
⚠️ **EMERGENCY**: Force stop all running wipe operations.

```bash
task rook:emergency-stop
```

#### `health-check`
Check cluster health after operations.

```bash
task rook:health-check
```

#### `reset-all-data`
Reset Rook data on all nodes without touching disks:

```bash
task rook:reset-all-data
```

## 🎯 Cluster Configuration

### Default Settings
- **Cluster Context**: `admin@kubernetes`
- **Job Namespace**: `default`
- **Job Timeout**: 1 minute
- **Disk Identification**: Uses `/dev/disk/by-id/` paths for consistency

### Node and Disk Mapping

| Node | Disk 1 | Disk 2 |
|------|--------|--------|
| talos-1 | Lexar NM790 4TB `NME714W100393P2202` | Samsung 980 PRO 2TB `S6B0NL0W412707M` |
| talos-2 | Samsung 990 PRO 2TB `S7KHNJ0WC55436E` | Lexar NM790 4TB `NME714W101694P2202` |
| talos-3 | Samsung 980 PRO 2TB `S69ENL0TC06068B` | Lexar NM790 4TB `NL1948W100519P2202` |

### Node Reference System

The Taskfile uses different node references for different operations:

- **Kubernetes Operations** (e.g., `kubectl debug node/`, disk wipe jobs): Use **hostnames**
  - `TALOS_NODE_NAMES`: `[talos-1, talos-2, talos-3]`
  - Kubernetes identifies nodes by their registered node names

- **Talos CLI Operations** (e.g., `talosctl -n`, direct API calls): Use **IP addresses**
  - `TALOS_NODE_IPS`: `[10.10.3.11, 10.10.3.12, 10.10.3.13]`
  - Talos CLI connects directly to node IP addresses

This dual reference system ensures:
- Consistent node identification across different tools
- Easy maintenance when IPs or hostnames change
- Clear separation between Kubernetes and Talos operations

## 🔧 How It Works

1. **Validation**: Checks cluster connectivity and required files before operations
2. **Backup**: Automatically backs up configurations before destructive operations
3. **Job Creation**: Creates Kubernetes jobs with privileged containers
4. **Node Targeting**: Uses `nodeName` to ensure jobs run on specific nodes
5. **Disk Operations**: Mounts host filesystem and performs disk operations
6. **Progress Tracking**: Shows real-time progress and completion status
7. **Monitoring**: Waits for job completion and displays logs
8. **Cleanup**: Automatically removes jobs after completion

## 🛡️ Safety Features

- **Pre-flight Validation**: Validates cluster and templates before operations
- **Automatic Backups**: Creates configuration backups before destructive operations
- **Confirmation Prompts**: All destructive operations require confirmation
- **Progress Indicators**: Visual feedback during multi-step operations
- **Timeouts**: Jobs have 1-minute timeout to prevent hanging
- **Enhanced Logging**: Detailed logs with emojis for better readability
- **Emergency Stop**: Ability to force-stop all operations
- **Health Checks**: Post-operation cluster health validation
- **Automatic Cleanup**: Jobs are automatically deleted after completion
- **Preconditions**: Verifies required files exist before execution

## 📋 Usage Examples

### Pre-flight Checks
```bash
# Validate cluster and templates before operations
task rook:validate-all

# Check current disk status
task rook:check-disks

# Backup current configuration
task rook:backup-rook-config
```

### Complete Cluster Reset
```bash
# Full cluster reset with progress tracking (recommended)
task rook:wipe-all-with-progress

# Basic full cluster reset
task rook:wipe-all
```

### Single Node Operations
```bash
# Reset entire node (disks + data)
task rook:quick-wipe-1

# Reset only disks on a node
task rook:wipe-talos-1

# Reset only data on a node
task rook:reset-data node=talos-1
```

### Individual Disk Operations
```bash
# Wipe specific disk
task rook:reset-disk node=talos-1 disk=/dev/disk/by-id/nvme-Samsung_SSD_980_PRO_2TB_S6B0NL0W412707M
```

### Monitoring and Maintenance
```bash
# Check operation status
task rook:status

# Check cluster health
task rook:health-check

# Clean up stuck jobs
task rook:cleanup

# Emergency stop all operations
task rook:emergency-stop
```

## 🔍 Troubleshooting

### Failed Jobs
Check job logs for detailed error information:
```bash
kubectl get jobs -A | grep wipe
kubectl logs job/wipe-disk-talos-1-<disk-id>
```

### Stuck Jobs
Force cleanup of stuck jobs:
```bash
task rook:cleanup
kubectl delete jobs -A --field-selector status.successful=0
```

### Permission Issues
Ensure the default service account has sufficient privileges:
```bash
kubectl auth can-i create jobs
kubectl auth can-i get nodes
```

### Template Files Missing
Verify all required files exist:
```bash
ls -la .taskfiles/Rook/scripts/
ls -la .taskfiles/Rook/templates/

# If templates are missing, the validation will show specific errors
task rook:validate-templates
```

### Disk Check Issues
If disk checking fails with missing commands:
```bash
# The task uses busybox image and shows disk IDs instead of lsblk
task rook:check-node-disks node=talos-1

# Alternative: Check disks directly via Talos
talosctl -n 10.10.3.11 list /dev/disk/by-id/
```

## ⚠️ Important Warnings

- **Data Loss**: These operations permanently destroy all data on target disks
- **Irreversible**: No recovery possible after disk wiping
- **Cluster Impact**: Wiping all nodes will destroy the entire Ceph cluster
- **Backup First**: Always backup important data before running any wipe operations

## 🚨 Emergency Procedures

### Stop All Operations
```bash
# Emergency stop all wipe operations
task rook:emergency-stop

# Alternative manual cleanup
kubectl delete jobs -A -l job-type=wipe-disk --force --grace-period=0
kubectl delete jobs -A -l job-type=wipe-data --force --grace-period=0
```

### Check Node Status
```bash
# Check cluster health
task rook:health-check

# Manual node health check
kubectl get nodes -o wide
talosctl health --nodes 10.10.3.11,10.10.3.12,10.10.3.13
```

## 🔄 Recommended Workflow

### Full Cluster Wipe (Recommended)
```bash
# 1. Pre-wipe validation (validates cluster, templates, creates backup)
task rook:pre-wipe-validation

# 2. Wipe all disks and data with progress tracking
task rook:wipe-all-with-progress

# 3. Post-wipe validation (validates wipe, cleanup, health check)
task rook:post-wipe-validation
```

### Manual Step-by-Step
```bash
# 1. Validate everything is ready
task rook:validate-all

# 2. Create backup
task rook:backup-rook-config

# 3. Check disk status before wipe
task rook:check-disks-talos

# 4. Wipe all disks and data
task rook:wipe-all

# 5. Validate wipe was successful
task rook:validate-wipe

# 6. Check cluster health
task rook:health-check

# 7. Cleanup any leftover resources
task rook:cleanup
```

### Emergency Stop
```bash
# If something goes wrong during wipe
task rook:emergency-stop
```

### Troubleshooting

#### Node Reference Issues
If you encounter connectivity issues:

- **Kubernetes operations failing**: Check that hostnames (`talos-1`, `talos-2`, `talos-3`) are correct
  ```bash
  kubectl get nodes
  ```

- **Talos CLI operations failing**: Check that IP addresses are correct and accessible
  ```bash
  talosctl -n 10.10.3.11 version
  ```

- **Updating node references**: Edit the `TALOS_NODE_NAMES` and `TALOS_NODE_IPS` variables in the Taskfile if your cluster configuration changes

#### Check Disk Status
```bash
# Using Talos CLI (recommended)
task rook:check-disks-talos

# Using debug pods
task rook:check-disks
```

#### Check Job Status
```bash
task rook:status
```

#### View Logs
```bash
# View logs for specific job
kubectl logs job/wipe-disk-talos-1-<random-suffix>

# View all wipe-related logs
kubectl get jobs -A | grep wipe
```

### Recovery Procedures
```bash
# Restore from backup (if available)
kubectl apply -f /tmp/rook-backups/rook-backup-<timestamp>.yaml

# Validate cluster after recovery
task rook:validate-cluster
```

---

**Last Updated**: July 14, 2025
**Cluster**: admin@kubernetes
**Environment**: Talos 3-node cluster with NVMe SSDs
