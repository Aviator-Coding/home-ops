# Space Engineers Dedicated Server

## 🎮 Overview

A production-ready Space Engineers dedicated server deployment with live plugin management and enterprise-grade backup strategy.

## ✨ Features

- **Game Server**: Space Engineers dedicated server with custom configuration
- **Plugin Management**: FileBrowser sidecar for drag-and-drop plugin uploads
- **Triple Backup**: NAS MinIO + NAS NFS + Cloudflare R2 via VolSync
- **Web Interface**: HTTPRoute-based admin panel via k8s-gateway
- **Storage**: Ceph-backed persistent storage with snapshots

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                    Space Engineers Pod                          │
├──────────────────────┬──────────────────────────────────────────┤
│ Space Engineers     │ FileBrowser                              │
│ :27016 (Game)       │ :8080 (Admin UI)                        │
│                     │                                          │
│ /appdata/space-     │ /srv -> /appdata/space-                 │
│ engineers/instances │ engineers/plugins                        │
└──────────────────────┴──────────────────────────────────────────┘
           │                              │
           ▼                              ▼
┌─────────────────────┐        ┌─────────────────────┐
│ space-engineers-ds  │        │ space-engineers-ds- │
│ PVC (RWO)          │        │ plugins PVC (RWX)   │
│ ceph-block         │        │ ceph-filesystem     │
│ 50Gi               │        │ 10Gi                │
└─────────────────────┘        └─────────────────────┘
           │                              │
           ▼                              ▼
      VolSync                        VolSync
   (CSI Snapshots)                 (Restic Clone)
```

## 🌐 Access Methods

### Game Access
- **Cloudflare Tunnel**: `se-game.${SECRET_DOMAIN}:27016`
- **Internal**: `space-engineers-ds.gaming.svc.cluster.local:27016`

### Admin Panel
- **HTTPRoute**: `https://se-admin.${SECRET_DOMAIN}`
- **Internal**: `http://space-engineers-ds.gaming.svc.cluster.local:8080`

## 💾 Backup Strategy

### Instances Data (RWO - Game Data)
- **Schedule**: Every 4 hours (`0 */4 * * *`)
- **Method**: CSI Snapshots → Restic
- **Destinations**:
  - MinIO S3 (hourly)
  - Cloudflare R2 (daily)
- **Retention**: 24h + 7d + 5w

### Plugins Data (RWX - Shared Files)
- **Schedule**: Every 6 hours (`30 */6 * * *`)
- **Method**: Clone → Restic
- **Destinations**: Cloudflare R2 only
- **Retention**: 14d + 8w + 6m

## 🚀 Deployment

This application is managed by FluxCD and includes:

```yaml
# kubernetes/apps/gaming/space-engineers-ds/
├── app/
│   ├── pvc.yaml                   # Dedicated PVCs for VolSync
│   ├── helmrelease.yaml           # bjw-s/app-template v3
│   └── kustomization.yaml         # Includes VolSync component
├── volsync/
│   ├── instances/                 # Game data backups (CSI)
│   ├── plugins/                   # Plugin backups (Restic)
│   └── kustomization.yaml
└── ks.yaml                        # Main Kustomization
```

## 🔧 Configuration

### Environment Variables
```yaml
INSTANCE_NAME: "Home-Ops Space Engineers Server"
MAX_PLAYERS: "20"
SERVER_NAME: "Home-Ops SE"
WORLD_NAME: "HomeOpsWorld"
VIEW_DISTANCE: "15000"
SYNC_DISTANCE: "3000"
ENABLE_WORKSHOP: "true"
AUTO_RESTART: "true"
AUTO_UPDATE: "true"
```

### Resource Allocation
- **Game Server**: 2-6 CPU, 4-12Gi RAM
- **FileBrowser**: 50m-200m CPU, 128-512Mi RAM
- **Storage**: 50Gi instances + 10Gi plugins

## 🛡️ Security

- **Pod Security**: `runAsUser: 1000`, `runAsGroup: 1000`, `fsGroup: 1000`
- **HTTPRoute**: Internal gateway only (no external direct access)
- **Cloudflare**: Tunneled access with potential Access policies
- **Backup Encryption**: Restic client-side encryption

## 📋 Management

### Plugin Management
1. Access FileBrowser: `https://se-admin.${SECRET_DOMAIN}`
2. Upload plugins to `/srv` directory
3. Server automatically detects changes

### Backup Recovery
```bash
# Restore instances (game data)
kubectl patch replicationdestination space-engineers-ds-instances-restore \
  --type merge --patch '{"spec":{"trigger":{"manual":"restore-'$(date +%s)'"}}}'

# Restore plugins
kubectl patch replicationdestination space-engineers-ds-plugins-restore \
  --type merge --patch '{"spec":{"trigger":{"manual":"restore-'$(date +%s)'"}}}'
```

### Monitoring
```bash
# Check pod status
kubectl get pods -n gaming -l app.kubernetes.io/name=space-engineers-ds

# Check backup status
kubectl get replicationsource -n gaming

# View logs
kubectl logs -n gaming -l app.kubernetes.io/name=space-engineers-ds -c app
```

## 📊 Dependencies

- **Storage**: Rook Ceph with `ceph-block` and `ceph-filesystem`
- **Networking**: k8s-gateway for HTTPRoute support
- **Backup**: VolSync with MinIO + Cloudflare R2
- **Runtime**: bjw-s/app-template v3 Helm chart

## ⚠️ Notes

- **Game Port**: UDP 27016 for game traffic
- **Admin Port**: TCP 8080 for FileBrowser
- **PVC Names**: Must match `space-engineers-ds` and `space-engineers-ds-plugins` for VolSync
- **Namespace**: `gaming` (controlled by ks.yaml)
