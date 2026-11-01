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

No live example remains. `syncthing-data`
(`kubernetes/apps/main/selfhosted/syncthing.yaml`) and `paperless-ngx-media`
(`kubernetes/apps/main/selfhosted/paperless-ngx.yaml`) used this shape until
2026-11-01, when their backup-only VolSync Kustomizations were deleted (kopiur
only now).

`syncthing` is the 1Gi config claim. `syncthing-data` is the synced-files
claim. They are not prefixes of each other for expiry purposes either; see
[retired-repo-expiry.md](retired-repo-expiry.md). `syncthing` (config) is
kopiur-only, and so is `syncthing-data` (15Gi on `ceph-block`, from the app's
own `pvc.yaml`, no `dataSourceRef`).

## Schedules

Defaults, America/New_York because VolSync honours the k8tz-injected TZ:

| Destination | Cadence |
|---|---|
| ceph | every 4 hours, even hours |
| minio | every 6 hours |
| r2 | daily |

Per-app overrides are `VOLSYNC_SCHEDULE_CEPH`, `VOLSYNC_SCHEDULE_MINIO`,
`VOLSYNC_SCHEDULE_R2`. kopiur's ceph schedules sit on the odd hours so the two
engines did not share an hour. Do not put a claim back on VolSync, and never on an odd hour, without
checking `EXPECTED_R2_HOUR` and the kopiur ceph slots (skill `kopiur-backups`,
`references/schedules-timezone.md`).

`VOLSYNC_CACHE_CAPACITY` is mover scratch: 20-50% of PVC size, 50-100% for
small PVCs. It is not the kopiur restore cache and it does not follow the
~6.2 GiB plateau rule.

## `VOLSYNC_CLAIM` aliases

`existingClaim: ${VOLSYNC_CLAIM:-*app}` is a literal token, not a kustomize
anchor: an overlay that drops `VOLSYNC_CLAIM` while the line exists fails
`flate`. None is left. `paperless-ngx` was the last; its HelmRelease became
`${KOPIUR_CLAIM:-paperless-ngx}` when VolSync left it (2026-11-01), as the
three before it did.

## Do not add another carve-out

New apps are kopiur-only: `components/kopiur` and a chart-owned PVC. The
takeover component `components/kopiur/pvc` is only for retiring an existing
VolSync claim.
