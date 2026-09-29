# Hermes shared storage and Samba

## The agent claim

`/opt/data` is the chart-owned `hermes` PVC: RWO `ceph-block`, mounted by
`app` and `codeserver` in the **same** pod. Sessions, memories, and skills
are not safe with two writers. `replicas: 1` and `strategy: Recreate` stay.

The overlay includes `components/kopiur/pvc` and sets `KOPIUR_PUID: "10000"`.
That is the dropped-privilege uid. The pod has no `runAsUser`; s6 starts as
root and drops to 10000, so the container is not `runAsNonRoot`.
`fsGroup: 10000` is what makes the claim writable after the drop. Do not
"fix" that by pinning `runAsNonRoot` on `app`.

`KOPIUR_CAPACITY` does not resize a claim Flux already created. A standing
kopiur `Restore` is `IfNotPresent`; deleting it is how a raised cache
setting takes effect, and deleting a `Snapshot` CR deletes backup data.
Skill `kopiur-backups` owns that procedure. Do not remove the include.

## Samba

`ai/samba` serves two RWX claims to the captain's Mac and to Hermes:

| Claim | Mount | Share | Backup |
|---|---|---|---|
| `shared-xml` | `/opt/xml` | the XML corpus | unbacked on purpose: the Mac holds the authoritative copy |
| `shared-files` | `/opt/files` | `smb://10.50.0.55/files` | **unbacked on purpose, and it has no second copy** |

`shared-files` is scratch. Anything that must survive belongs on a
backed-up claim. Adding `components/kopiur` to `kubernetes/apps/main/ai/pvc.yaml`
with `KOPIUR_PUID`/`PGID` `"10000"` is the reversal. Do not add it as a
drive-by "fix".

Both claims use `ceph-filesystem-rwx` (group `csi-rwx`), not
`ceph-filesystem`.

Identity is 10000:10000 on both sides. Samba `force user`/`force group`
and the Hermes uid match. The volume root mode **2770** (setgid) is
load-bearing: `fsGroupChangePolicy: OnRootMismatch` walks the tree unless
the root already has the right gid **and** the setgid bit. A plain `0770`
root makes kubelet chown every file on the next Hermes start. The Samba
initContainer sets the bit and is non-recursive on purpose (`/shared` is
hundreds of thousands of files).

`smb.conf` is a reloader payload: any byte, comments included, restarts the
samba pod (it drops the SMB sessions). Land such edits alone.

`replicas: 1` and `Recreate` on Samba stay. smbd's per-file locks are
in-process; a second replica would not share them. Reloader is on, for the
same secret-refresh reason as Hermes.
