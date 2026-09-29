# Device groups

Intel's GPU plugin cannot split two `xe` devices into two extended
resources. The B70 is advertised by generic-device-plugin. The iGPU
stays `gpu.intel.com/xe` with `allowIDs: "0xa7a0"`.

| Resource | Who | Node path | In-container path |
|---|---|---|---|
| `devic.es/b70` | Level Zero: `ai/vllm`, `ai/embedding-gpu`. `vllm-embed` and comfyui are not live consumers | `by-path/pci-0000:03:00.0-{card,render}` | `/dev/dri/card0`, `/dev/dri/renderD128` |
| `devic.es/b70-vaapi` | VA-API: `tdarr-node` | same host nodes | `/dev/dri/card1`, `/dev/dri/renderD129` |
| `gpu.intel.com/xe` | iGPU: `plex`, `playwright`. jellyfin is retired | on-die `0xa7a0` | plugin default |

`count: 99` on both B70 groups is a share token. It is not a fence and
not 99 devices. Skill `b70-llm-serving` for what sharing the card does
to chat.

## Why the rename kills VA-API

libdrm does not use the path you pass. It `fstat`s the fd, reads
`/sys/dev/char/<major>:<minor>/uevent`, and reopens the canonical
`DEVNAME`. For this card that is `dri/renderD129`, because the iGPU
probes first and takes `card0` / `renderD128`. A container that only
has the renamed node has nothing to reopen. `vaGetDisplayDRM()` fails
before a driver loads. A symlink at `renderD129` inside the container
made the unchanged `renderD128` path work, and removing the symlink
broke it again.

Level Zero opens whatever `/dev/dri/render*` exists, so the AI stack
stays up. That split hid a multi-day Tdarr outage (PR #1443). Ship no
GPU change without the VA-API check in `docs/media-stack.md`.

If kernel enumeration order changes, `b70-vaapi`'s `mountPath` values
have to follow the new kernel names. Tdarr then falls back to its CPU
worker instead of failing every job, which looks like a slow library
rather than a device bug.

## IDs and the hash

Device IDs are `sha1(count + every host path in the group)`. Editing
`b70`'s paths changes all 99 IDs and invalidates allocations held by
running pods. A new consumer that needs different node names gets a new
group, which is what `b70-vaapi` is.

The config is mounted `subPath`. Kubelet will not pick up a changed
file in place. `configMapGenerator` has the name-suffix hash enabled so
a content change renames the ConfigMap, the HelmRelease reference
updates, and the DaemonSet rolls. A comment-only edit is a content
change. A device-plugin restart does not drop pods that already hold a
device (verified when this hash was turned on: vllm kept its slot).
Still do not roll it to tidy a sentence.

## Sysfs is not renamed

Inside a `devic.es/b70` container `/dev/dri/card0` is the B70 and
`/sys/class/drm/card0` is the host iGPU. Read
`/sys/bus/pci/devices/0000:03:00.0` (`device` file reads `0xe223`).
