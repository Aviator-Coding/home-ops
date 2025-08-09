# Space Engineers Dedicated Server Setup

## Initial World Creation

The Space Engineers container requires a pre-configured world to start. You need to create this world using the Space Engineers Dedicated Server Tool.

### Steps:

1. **Download Space Engineers Dedicated Server Tool** on a Windows machine
2. **Create a new world** with your desired settings:
   - World Name: `HomeOps-SE-Server` (must match `INSTANCE_NAME` env var)
   - Configure gameplay settings, mods, etc.
   - Save the world
3. **Locate the instance directory** (usually in `%APPDATA%\SpaceEngineersDedicated\Saves\`)
4. **Copy the world folder** to the Kubernetes PVC

### Alternative: Copy from existing PVC

If you need to access the PVC to upload a world:

```bash
# Create a temporary pod to access the instances PVC
kubectl run -n gaming temp-pod --image=busybox --rm -it --restart=Never \
  --overrides='{"spec":{"containers":[{"name":"temp","image":"busybox","command":["sleep","3600"],"volumeMounts":[{"name":"instances","mountPath":"/instances"}]}],"volumes":[{"name":"instances","persistentVolumeClaim":{"claimName":"space-engineers-ds-instances"}}]}}'

# In the pod, you can create a basic world structure or copy files
# The instances directory should contain a folder named "HomeOps-SE-Server"
```

### Using FileBrowser to Upload World

1. Access FileBrowser at `https://se-admin.${SECRET_DOMAIN}`
2. Navigate to the plugins volume (mounted as `/srv`)
3. Use the file upload feature to transfer world files

## Container Requirements

The container needs:
- World instance in `/appdata/space-engineers/instances/HomeOps-SE-Server/`
- Space Engineers Dedicated Server files in `/appdata/space-engineers/SpaceEngineersDedicated/`
- Plugins (optional) in `/appdata/space-engineers/plugins/`

## Troubleshooting

### "Cannot start new world - Premade world not found"
- Ensure the world folder exists in the instances PVC
- Check that the folder name matches the `INSTANCE_NAME` environment variable
- Verify the world files are valid Space Engineers save files

### "Remote API unable to start"
- The Remote API is configured to use port 8081 to avoid conflicts with FileBrowser (port 8080)
- Ensure both containers have proper security contexts

### Steam/Wine Issues
- The container runs in "Experimental mode" due to system requirements
- Wine debugging is disabled with `WINEDEBUG=-all`
- Steam connection should work automatically

## Accessing the Server

- **Game Port**: 27016 (UDP/TCP)
- **FileBrowser**: `https://se-admin.${SECRET_DOMAIN}` (port 8080)
- **Remote API**: port 8081 (if enabled)

## Backup Strategy

VolSync is configured to backup:
- **Instances** (world saves): Every 4 hours using CSI snapshots
- **Plugins**: Every 6 hours using Restic to multiple destinations (MinIO, NFS, Cloudflare R2)
