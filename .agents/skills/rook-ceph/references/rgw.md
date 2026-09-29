# RGW

`ceph health` does not read the RGW access log. Check writes with the verb/status one-liner in the skill before calling S3 healthy.

## What Git owns

`cephConfig.client.rgw.rgw_realm` is set so RGW does not create a fresh orphan `default` zone on every start. The object store is `ceph-objectstore`, two gateway instances, `allowUsersInNamespaces: ["*"]`. The realm, zonegroup and zone *defaults*, and the zone system user, are not Helm values. They are applied once with `radosgw-admin` and must be repeated after a cluster rebuild. Full command sequence: `kubernetes/apps/base/rook-ceph/rook-ceph/backup/RECOVERY-PROCEDURES.md` (section "RGW realm, zone and system user").

Short form, from the toolbox Deployment:

```bash
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- \
  radosgw-admin realm default --rgw-realm=ceph-objectstore
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- \
  radosgw-admin zonegroup default --rgw-zonegroup=ceph-objectstore --rgw-realm=ceph-objectstore
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- \
  radosgw-admin zone default --rgw-zone=ceph-objectstore \
  --rgw-zonegroup=ceph-objectstore --rgw-realm=ceph-objectstore
```

Zone sync needs a system user. Without it, sync reports `Access/Secret keys not found`:

```bash
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- \
  radosgw-admin user create --uid=zone.user --display-name="Zone System User" --system
# then, with the keys from that output:
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- \
  radosgw-admin zone modify --rgw-zone=ceph-objectstore \
  --access-key=<ACCESS_KEY> --secret=<SECRET_KEY> --rgw-realm=ceph-objectstore
kubectl rollout restart deployment -n rook-ceph -l app=rook-ceph-rgw
```

Do not put the printed keys in Git.

## Orphan default zone

If `radosgw-admin zone list` shows a `default` zone left beside `ceph-objectstore`:

```bash
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- radosgw-admin zone delete --rgw-zone=default
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- radosgw-admin zonegroup delete --rgw-zonegroup=default
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- rados -p .rgw.root rm zone_names.default
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- rados -p .rgw.root rm zonegroups_names.default
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- rados -p .rgw.root rm default.zone.
kubectl exec -n rook-ceph deploy/rook-ceph-tools -- rados -p .rgw.root rm default.zonegroup.
```

Delete the `default.rgw.log`, `default.rgw.control` and `default.rgw.meta` pools only when `ceph osd pool ls` shows them and they are empty of anything you still need. Then restart the RGW deployments.

## Reading a 403

```bash
kubectl -n rook-ceph logs -l app=rook-ceph-rgw -c rgw --since=20m \
  | grep -oE '"(GET|PUT|HEAD) [^"]*" [0-9]{3}'
```

`op=put_obj` with `http_status=403` and user `-` is the SigV4 failure from [decisions.md](decisions.md), not an ObjectBucket policy. GET and HEAD staying 200 is the signature of that bug.
