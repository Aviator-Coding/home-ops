# FalkorDB

Image tag, digest, and args are in
`kubernetes/apps/base/database/falkordb/app/helmrelease.yaml`. Do not copy a
version out of a doc.

## Durability

kopiur snapshots this claim with `copyMethod: Snapshot` and no application
hook. Redis restores only what it has fsynced. On this image, one mid-write
snapshot restored 2174 of 2199 nodes with `appendonly yes` and 200 of 2199
with the image's stock RDB-only settings. Both starts looked healthy.
`--appendonly yes --appendfsync everysec` stays. `--save 900 1`,
`--save 300 10`, and `--save 60 1000` stay as a second dump. A torn AOF tail
is Redis's `aof-load-truncated yes` default.

The server is authenticated because the `database` namespace has no
default-deny NetworkPolicy and its consumers are in other namespaces.
Dragonfly can stay unauthenticated: it is per-namespace and the operator
NetworkPolicy limits port 6379 to that namespace.

## Process command

Bypass `run.sh` and `exec redis-server` so it is PID 1. `run.sh` expands
`REDIS_ARGS` unquoted, so a password with whitespace or glob characters
splits. `--requirepass` is double-quoted. `$${FALKORDB_ARGS}` stays
unquoted because the image ships it as several arguments.

Every `$${VAR}` is escaped on purpose. Flux postBuild runs with
`StrictPostBuildSubstitutions=true` over the whole Kustomization. A single
`$` fails the Kustomization. `flate` substitutes leniently and still exits
0. Skill `flux-substitution`.

`BROWSER=0` is inert because `run.sh` is not the command. The browser is
the sibling container. Leave the env var; it guards a future command change.
Redis rewrites its process title, so `/proc/1/cmdline` does not show the
password.

## Memory

| Knob | Value | Why |
|---|---|---|
| `limits.memory` | 16Gi | cgroup ceiling |
| `requests.memory` | 7Gi | resting set, not the ceiling |
| `--maxmemory` | `12gb` (1024^3) | 0.75 of the limit |
| `maxmemory-policy` | `noeviction` | the graph is one key; eviction deletes it |

Move the limit, `--maxmemory`, and the alert ratios together. The 7Gi
request is the resting set. A request far below the working set lets the
scheduler place the pod where it does not fit. Do not raise
`auto-aof-rewrite-percentage` to avoid the AOF-rewrite fork. The fork is
the trigger; a rarer rewrite grows the AOF and lengthens replay. The ceiling
must stay above `used_memory` of the loaded graph. `used_memory` includes
the GraphBLAS matrices. Below the dataset, `noeviction` refuses every write
from the moment the AOF finishes loading, while the pod looks healthy.
`GRAPH.QUERY` is `write denyoom`. `GRAPH.RO_QUERY` is `readonly` and keeps
serving. Do not add an eviction policy.

Alerts in `app/prometheusrule.yaml` divide working set by
`kube_pod_container_resource_limits` (there is no `container_spec_*` series),
joined `on (namespace, pod, container)`:

- `FalkorDBMemoryApproachingCeiling`: ratio `> 0.6`, `for: 2m`, warning.
  Writes refuse at 0.75. A 10m `for` can miss a fast climb.
- `FalkorDBMemoryAtWriteCeiling`: ratio `> 0.7`, `for: 2m`, critical.
- `FalkorDBOOMKilled`: `last_terminated_reason="OOMKilled"` at `for: 0m`
  AND `increase(restarts_total[15m]) > 0`. The ratio rules reset every
  crash. The increase is the recency guard so a stale reason does not
  latch. General rule shape: skill `observability`.

## Probe and pod

Liveness is TCP 6379. Readiness is `redis-cli -e GRAPH.LIST`. Without `-e`,
redis-cli exits 0 on NOAUTH and on ERR, so a bad credential rotation leaves
the pod Ready. Readiness only; a failure removes endpoints and does not
restart the pod. Startup covers a long AOF replay.

Strategy is `Recreate`. The claim is RWO `ceph-block`. RollingUpdate
deadlocks, and two servers must not open the same AOF.

Pod security is the key `defaultPodOptions`, a sibling of `controllers`.
Any other spelling is discarded by Helm with no `flate` error. `runAsUser`
1000, `fsGroup` 1000, `fsGroupChangePolicy: OnRootMismatch`. The
`fix-permissions` init runs as root to chown the emptyDirs the browser
chmods. kopiur `SecurityContextCompatible` will be absent because of that
init. Absence is not a failure. Skill `kopiur-backups`.

## Temp dir

6.0.0's module aborts startup with `TEMP_FOLDER '/tmp' is not writable` when
the read-only rootfs has no writable `/tmp`. The database container mounts an
`emptyDir` at `/tmp`. 4.x never wrote there; the mount stays harmless on 4.x.

## Major bumps

6.0.0 cannot replay an AOF written by 4.x: it loads the RDB base, then aborts
on `Diverged applying GRAPH.EFFECT ... effects buffer is version 2, this build
reads 3` and crash-loops. Do not bump the major version in place (Renovate
will propose it); the image tag stays on 4.x until the AOF format is handled.

## Claim name

Chart-owned PVC, not `components/kopiur/pvc` (that shape is the
VolSync-retirement takeover). `retain: true` so a failed-install uninstall
does not delete the volume. `forceRename: falkordb` so a second persistence
key cannot rename this claim to `falkordb-data` (app-template appends the key
only when more than one PVC renders) and orphan the SnapshotPolicy. Mount with
`advancedMounts` on the database container only. The browser reaches data over
Redis.

## Browser

`AUTH_SECRET` and `ENCRYPTION_KEY` are sha256 of the database password plus
two distinct salts, not extra 1Password fields. They must stay stable across
restarts. The image ships public placeholders and, when `ENCRYPTION_KEY` is
unset, generates a throwaway key. `sha256sum` is 64 lowercase hex, which the
entrypoint's length check requires. Rotating the password rotates both.

The browser container shares the database image, mounts no data volume and
reaches the database over loopback. Its workingDir is
`/var/lib/falkordb/browser` because `API_TOKEN_STORAGE_PATH` (`.data/...`)
resolves relative to it. Two emptyDirs are needed and no others (`/tmp` and
`.next/cache` were measured unnecessary): `/app/.data` (the entrypoint exits 1
if it is unwritable) and `<workingDir>/.data`. Missing the second leaves the pod
Ready while every login fails with `EROFS: chmod '.data'`. Tokens die on
restart by design. Run the image entrypoint with `exec`, not `node server.js`.
`AUTH_URL` and `ALLOWED_ORIGINS` override the image's localhost `.env.local`;
without `AUTH_URL` a successful login redirects to `http://localhost:3000`.
`HOSTNAME=0.0.0.0` is needed because Next.js binds localhost by default.

`BROWSER=0` stays. It is inert while the command bypasses `run.sh`. Reverting
to the image command without it starts a second browser on port 3000.

The browser HTTPRoute hostname is `falkordb-browser`. The LoadBalancer
`falkordb-lb` already owns `falkordb.${SECRET_DOMAIN}` at `10.50.0.24`.
unifi-dns `policy: sync` writes both, so one name with two targets flaps.
Public external-dns does not read Services. Do not front Redis with a Gateway.

`SecurityPolicy/falkordb-browser-auth` is the page boundary. The browser login
is a connection dialog. `database` must remain on the Authentik ReferenceGrant
or the ext_authz backend resolves to nothing.
