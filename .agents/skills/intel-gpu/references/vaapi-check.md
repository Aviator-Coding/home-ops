# Verifying VA-API after a GPU change

Allocatable GPU capacity is not proof that transcoding works. Run this after
any change to `generic-device-plugin`, the Talos GPU kernel args, the GPU
hardware, or the `tdarr_node` image. The mechanism (libdrm reopens the
canonical `DEVNAME`) is in [devices.md](devices.md).

```sh
kubectl -n media exec deploy/tdarr-tdarr-node -c app -- ls -l /dev/dri/
kubectl -n media exec deploy/tdarr-tdarr-node -c app -- \
  vainfo --display drm --device /dev/dri/renderD129 | grep -E 'Driver version|AV1.*Enc'
kubectl -n media exec deploy/tdarr-tdarr-node -c app -- \
  tdarr-ffmpeg -y -f lavfi -i testsrc=size=1920x1080:rate=30 -frames:v 120 \
  -c:v av1_qsv -b:v 5M /tmp/vaapi-check.mp4
```

Healthy: Intel iHD driver version, `VAProfileAV1Profile0 : VAEntrypointEncSlice`,
and ffmpeg ending in a `frame= 120` summary. Failure: `Failed to a DRM display`
from vainfo, or `Device creation failed: -542398533` from ffmpeg.
`card1` / `renderD129` are the kernel names. A renamed `mountPath` leaves
Level Zero green and VA-API dead. Pass `--kubeconfig` explicitly.
