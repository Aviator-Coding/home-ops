---
name: app-workloads
description: "Read before writing or changing an app's pod spec: securityContext, fsGroup, app-template pod-options keys, liveness/readiness probes, PVC mounts, linuxserver images, Recreate on RWO claims, or when an app is Ready but cannot write to its volume. Covers the silently discarded pod-options key, fsGroup re-owning content, and probes that can never fail."
---

# App pod specs: securityContext, fsGroup, probes and PVC mounts

An app can be Ready, probed, and backed up while it cannot write its own
volume. Nothing in `flate` catches that.

## Tripwires

1. **A pod-options key the chart does not read is discarded.** app-template's
   schema is too loose to warn. `selfhostedPodOptions` (and any other name
   the chart does not know) ships `securityContext: {}`. `flate` stays green.
   The key the chart reads is the one sibling apps use. Render the pod and
   read `securityContext` before trusting a values edit.
   [pod-security.md](references/pod-security.md)
2. **`fsGroup` re-owns existing content, including with `OnRootMismatch`.**
   Both Ceph CSI drivers are `fsGroupPolicy: File`. `OnRootMismatch` skips
   the walk only when the volume root already matches. Adding or changing
   `fsGroup` is the mismatch that chowns the tree on the next mount. The
   chown persists after the manifest is reverted.
3. **linuxserver images need both opt-in env vars** under this repo's
   non-root, read-only-rootfs convention: `LSIO_NON_ROOT_USER: "true"` and
   `LSIO_READ_ONLY_FS: "true"`. Still required: `emptyDir` at `/run` and
   `/tmp`, and a root initContainer that chowns them first. Worked example:
   `kubernetes/apps/base/downloads/bazarr/app/helmrelease.yaml`.
4. **A probe must be able to return non-200.** n8n's `/healthz` is an
   unconditional ok, so the pod stayed Ready through a multi-day database
   outage. The DB-aware path is `/healthz/readiness`. Curl a knowingly
   broken instance before trusting a path. [probes.md](references/probes.md)
5. **An RWO claim needs `strategy: Recreate`.** A second pod cannot mount
   it, so a RollingUpdate sits on the old pod until the deploy times out.
6. **Intentional read-only is `volumeMounts[].readOnly: true`.** The
   pod-wide skip annotation on the writable check is an unused escape
   hatch. Skill `pvc-integrity-checks`.
7. **Never seed a Secret with client-side `kubectl apply`.** The value
   lands in `last-applied-configuration`. Skill `secrets-1password`.

## Where things live

| What | Path |
|---|---|
| App HelmRelease | `kubernetes/apps/base/<ns>/<app>/app/helmrelease.yaml` |
| Writable check | `kubernetes/apps/base/system/pvc-writable-check/` |
| linuxserver example | `kubernetes/apps/base/downloads/bazarr/app/helmrelease.yaml` |
| Probe that can fail | `kubernetes/apps/base/selfhosted/n8n/app/helmrelease.yaml` |
| Probes instead of a bespoke down-alert | `kubernetes/apps/base/home-automation/home-assistant/app/helmrelease.yaml` |

## Procedures

- Security context, fsGroup, linuxserver, RWO: [pod-security.md](references/pod-security.md).
- Probes and Gatus paths: [probes.md](references/probes.md).

## Verify

- Render the workload and read `securityContext`, `volumeMounts`, and
  `strategy` from the pod template, not from a values key you hope the
  chart accepted.
- For a PVC, the writable check is `test -w <mountPath>` inside the
  container. Skill `pvc-integrity-checks`.
- A new probe: confirm the path returns non-200 when the dependency it
  claims to represent is down.
