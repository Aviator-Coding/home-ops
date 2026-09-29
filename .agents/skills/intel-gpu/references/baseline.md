# B70 baseline the rest of the stack assumes

Current chat serving shape. Flag pins and how to re-measure are skill
`b70-llm-serving`. This page is the device-side picture those pins were
fitted to.

| Fact | Value |
|---|---|
| PCI | `0000:03:00.0`, `8086:e223`, 32 GB ReBAR |
| VRAM | 32656 MiB. Chat weights + KV hold about 26909 MiB. Free at the embedding server's 512 ubatch was about 5747 MiB before that server's own ~1493 MiB |
| Context | 262144, flash-attn on, KV `q8_0`/`q8_0` |
| KV at that window | 2720 MiB (hybrid: 10 of 40 layers are full attention). Older notes that say ~5.2 GiB are stale |
| Physical batch | `-b` and `-ub` 2048 |
| Image | `ghcr.io/ggml-org/llama.cpp:server-intel-b10820` |
| SYCL, not Vulkan | Kept. Do not flip the backend as a drive-by |

`pcie_port_pm=off` is in the Talos schematic so the dock's card is not
lost to runtime power management. Applying it is `just talos upgrade-node`,
not a Flux reconcile. Skill `talos-nodes`.

iGPU consumers today are `plex` and `playwright`. jellyfin is retired.
A new iGPU consumer updates `scripts/ci/igpu-xe-allowids-test.py`'s
consumer map in the same change.
