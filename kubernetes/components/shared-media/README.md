# Shared Media Component

This Kustomize component provides easy access to shared CephFS media storage across multiple namespaces.

## What it provides

- **Shared Storage**: All namespaces accessing this component share the same underlying CephFS subvolume
- **Namespace Isolation**: Each namespace gets its own PV/PV pair for security
- **Easy Reuse**: Simple component inclusion with postBuild variable substitution

## Storage Details

- **Subvolume Path**: `/volumes/media/shared-media/6d1c35aa-5b29-43a0-9f8a-d2c11363423b`
- **Access Mode**: ReadWriteMany (can be mounted in multiple pods simultaneously)
- **Size**: 5Ti (configurable via `SHARED_MEDIA_CAPACITY`)
- **Storage Class**: Static binding (no dynamic provisioning)

## Usage

### 1. Include in your kustomization.yaml

```yaml
# In your app's kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

components:
  - ../../components/shared-media  # Adjust path as needed
```

### 2. Set the namespace in your Flux Kustomization

```yaml
# In your Flux Kustomization resource
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: my-app
spec:
  postBuild:
    substitute:
      NAMESPACE: "downloads"  # Your target namespace
      # Optional overrides:
      # SHARED_MEDIA_CLAIM: "my-custom-pvc-name"
      # SHARED_MEDIA_CAPACITY: "10Ti"
```

### 3. Use in your Helm charts

```yaml
# In your HelmRelease values
persistence:
  shared-media:
    existingClaim: shared-media-pvc  # or ${SHARED_MEDIA_CLAIM} if customized
    advancedMounts:
      app:
        app:
          - path: /shared-media
```

## Available Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `NAMESPACE` | **Required** | Target namespace for the PVC |
| `SHARED_MEDIA_CLAIM` | `shared-media-pvc` | Name of the PVC |
| `SHARED_MEDIA_CAPACITY` | `5Ti` | Storage capacity |
| `SHARED_MEDIA_VOLUME_HANDLE` | `0001-0009-...` | CephFS volume handle (usually no need to change) |

## Example: Downloads Namespace

```yaml
# flux/clusters/prod/downloads/kustomization.yaml
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: downloads
spec:
  path: ./kubernetes/apps/downloads
  postBuild:
    substitute:
      NAMESPACE: "downloads"
```

```yaml
# kubernetes/apps/downloads/kustomization.yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

components:
  - ../../components/shared-media

resources:
  - ./sabnzbd
  - ./qbittorrent
  # ... other apps
```

## Directory Structure

Once mounted, you can organize your shared media like:

```
/shared-media/
├── downloads/          # Downloads from various sources
├── movies/            # Movie collection
├── tv-shows/          # TV series
├── music/             # Music collection
└── books/             # Ebook collection
```

## Benefits

✅ **Clean Syntax**: Uses postBuild variables like VolSync
✅ **No Complex Patches**: Simple variable substitution
✅ **True Shared Storage**: Files written in one namespace appear in all others
✅ **Easy File Movement**: Move downloads to final destinations without copying
✅ **Single Backup Point**: Only one subvolume to backup/snapshot
✅ **Consistent Naming**: Same PVC name across all namespaces by default
✅ **Reusable**: Add to any namespace with just `NAMESPACE` variable

## Troubleshooting

- **PVC Pending**: Check that `NAMESPACE` is set correctly in your Flux Kustomization
- **Mount Failed**: Verify the component path is correct in your kustomization.yaml
- **Permission Issues**: Check that your pod's securityContext allows access to the mounted filesystem
