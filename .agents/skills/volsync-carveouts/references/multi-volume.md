# One VolSync include, one volume

Every object is named from `${APP}`. Flux allows one `postBuild.substitute`
map per Kustomization, so the component cannot be included twice.

A second PVC is a second document in the app overlay:

- `path: ./kubernetes/components/volsync/backup` (sources and destination, no
  `pvc.yaml`; the claim already exists).
- `APP` set to the **claim name**, so the restic repository, ExternalSecrets
  and `<claim>-dst` do not collide with the parent app.
- `commonMetadata.labels` `app.kubernetes.io/name` stays the parent app.
- `dependsOn` the parent app (the PVC must exist) and `volsync` in `system`.
- `wait: false`.
- Schedule minutes chosen so they do not collide with the other
  `ReplicationSource` objects in the namespace.

Live: `kubernetes/apps/main/selfhosted/syncthing.yaml` (`syncthing-data`) and
`kubernetes/apps/main/selfhosted/paperless-ngx.yaml` (`paperless-ngx-media`).

`syncthing` is the 1Gi config claim. `syncthing-data` is the synced-files
claim. They are not prefixes of each other for expiry purposes either; see
[retired-repo-expiry.md](retired-repo-expiry.md). `syncthing` (config) is
kopiur-only. `syncthing-data` is still dual-engine.

## Schedules

Defaults, America/New_York because VolSync honours the k8tz-injected TZ:

| Destination | Cadence |
|---|---|
| ceph | every 4 hours, even hours |
| minio | every 6 hours |
| r2 | daily |

Per-app overrides are `VOLSYNC_SCHEDULE_CEPH`, `VOLSYNC_SCHEDULE_MINIO`,
`VOLSYNC_SCHEDULE_R2`. kopiur's ceph schedules sit on the odd hours so the two
engines do not share an hour. Do not move a carve-out onto an odd hour without
checking `EXPECTED_R2_HOUR` and the kopiur ceph slots (skill `kopiur-backups`,
`references/schedules-timezone.md`).

`VOLSYNC_CACHE_CAPACITY` is mover scratch: 20-50% of PVC size, 50-100% for
small PVCs. It is not the kopiur restore cache and it does not follow the
~6.2 GiB plateau rule.

## `paperless-ngx` alias

The HelmRelease still contains `existingClaim: ${VOLSYNC_CLAIM:-*app}`. `*app`
is a literal token, not a kustomize anchor. The overlay must keep defining
`VOLSYNC_CLAIM` for as long as that line exists. The other three apps that
used to carry it were renamed to `KOPIUR_CLAIM` when VolSync left.

## Do not add a fourth carve-out

New apps are kopiur-only: `components/kopiur` and a chart-owned PVC. The
takeover component `components/kopiur/pvc` is only for retiring an existing
VolSync claim.
