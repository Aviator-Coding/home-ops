#!/usr/bin/env python3
"""Contract test for the DuckLake deployment (database/ducklake).

Pins the parts that fail silently or destroy data:

  1. The kustomize build of the app emits every object the Jobs and the
     CronJob reference (secrets, generated ConfigMap), and the overlay depends
     on postgres-17, the Ceph cluster and the 1Password store.
  2. The bucket claim is on ceph-bucket (reclaimPolicy Delete), carries
     `kustomize.toolkit.fluxcd.io/prune: disabled` and a quota.
  3. Both PushSecrets write through the single-vault `onepassword-automation`
     store with `deletionPolicy: None`, to the documented 1Password items.
  4. The role password is generated once (`CreatedOnce`), never refreshed.
  5. The maintenance CronJob ships suspended, Forbid, on the official
     duckdb/duckdb image with a pinned tag, and its SQL sets the 30d/3d
     retention before CHECKPOINT. Un-suspending or moving to a tag-less image
     is a deliberate change that must edit this test.
  6. cluster-17 adds postgres-17.${SECRET_DOMAIN} to serverAltDNSNames and
     does not take a user-supplied server certificate.

Live state (role exists, 1Password items written, extension egress) is
outside this GitOps pin.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
APP_DIR = REPO / "kubernetes/apps/base/database/ducklake/app"
OVERLAY = REPO / "kubernetes/apps/main/database/ducklake.yaml"
OVERLAY_INDEX = REPO / "kubernetes/apps/main/database/kustomization.yaml"
CLUSTER = REPO / "kubernetes/apps/base/database/cloudnative-pg/cluster-17/cluster-17.yaml"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def build() -> list[dict[str, Any]]:
    binary = shutil.which("kustomize")
    cmd = [binary, "build", str(APP_DIR)] if binary else ["kubectl", "kustomize", str(APP_DIR)]
    out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    return [d for d in yaml.safe_load_all(out) if d]


def find(docs: list[dict[str, Any]], kind: str, name: str) -> dict[str, Any]:
    for d in docs:
        if d.get("kind") == kind and (d.get("metadata") or {}).get("name") == name:
            return d
    raise Failure(f"{kind}/{name} missing from the kustomize build")


def test_build_and_overlay() -> None:
    docs = build()
    for kind, name in [
        ("Password", "ducklake-db-password"),
        ("ExternalSecret", "ducklake-db-password"),
        ("ExternalSecret", "ducklake"),
        ("PushSecret", "ducklake-db-password"),
        ("PushSecret", "ducklake-ceph-bucket"),
        ("Job", "ducklake-db-init"),
        ("ObjectBucketClaim", "ducklake"),
        ("CronJob", "ducklake-maintenance"),
        ("ConfigMap", "ducklake-maintenance"),
    ]:
        find(docs, kind, name)
    ov = yaml.safe_load(OVERLAY.read_text())
    require(ov["spec"]["path"] == "./kubernetes/apps/base/database/ducklake/app", "overlay path")
    deps = {d["name"] for d in ov["spec"]["dependsOn"]}
    require(
        {"postgres-cluster-17", "rook-ceph-cluster", "onepassword-store"} <= deps,
        f"overlay dependsOn must cover postgres-cluster-17, rook-ceph-cluster, onepassword-store; got {sorted(deps)}",
    )
    require(ov["spec"].get("wait") is True, "overlay must wait on the db-init Job")
    require("./ducklake.yaml" in OVERLAY_INDEX.read_text(), "ducklake.yaml missing from the database overlay index")
    job = find(docs, "Job", "ducklake-db-init")
    require(
        job["metadata"]["annotations"].get("kustomize.toolkit.fluxcd.io/force") == "enabled",
        "db-init Job needs force: enabled",
    )
    refs = [e["secretRef"]["name"] for e in job["spec"]["template"]["spec"]["containers"][0]["envFrom"]]
    require(refs == ["ducklake-secret"], f"db-init must read ducklake-secret, got {refs}")


def test_secret_keys() -> None:
    docs = build()
    es = find(docs, "ExternalSecret", "ducklake")
    require(es["spec"]["target"]["name"] == "ducklake-secret", "ExternalSecret target name")
    keys = set(es["spec"]["target"]["template"]["data"])
    need = {
        "INIT_POSTGRES_DBNAME", "INIT_POSTGRES_HOST", "INIT_POSTGRES_USER", "INIT_POSTGRES_PASS",
        "INIT_POSTGRES_SUPER_PASS", "PGHOST", "PGDATABASE", "PGUSER", "PGPASSWORD",
    }
    require(need <= keys, f"ducklake-secret missing keys {sorted(need - keys)}")
    extracted = {d["extract"]["key"] for d in es["spec"]["dataFrom"]}
    require(extracted == {"ducklake", "cloudnative-pg"}, f"unexpected 1Password items {extracted}")
    gen = find(docs, "ExternalSecret", "ducklake-db-password")
    require(gen["spec"].get("refreshPolicy") == "CreatedOnce", "role password must be generated once")
    pw = find(docs, "Password", "ducklake-db-password")
    require(pw["spec"]["symbols"] == 0, "password must stay alphanumeric (libpq env, ATTACH strings)")


def test_bucket_protection_and_pushes() -> None:
    docs = build()
    obc = find(docs, "ObjectBucketClaim", "ducklake")
    require(obc["spec"]["storageClassName"] == "ceph-bucket", "OBC storage class")
    require(
        obc["metadata"]["annotations"].get("kustomize.toolkit.fluxcd.io/prune") == "disabled",
        "OBC must carry prune: disabled (the class reclaims with Delete)",
    )
    cfg = obc["spec"].get("additionalConfig") or {}
    require("maxSize" in cfg and "maxObjects" in cfg, "OBC needs a quota")
    expect = {
        "ducklake-ceph-bucket": {"ducklake-ceph-bucket"},
        "ducklake-db-password": {"ducklake"},
    }
    for name, items in expect.items():
        ps = find(docs, "PushSecret", name)
        require(ps["spec"].get("deletionPolicy") == "None", f"{name}: deletionPolicy must be None")
        stores = [(s["name"], s["kind"]) for s in ps["spec"]["secretStoreRefs"]]
        require(stores == [("onepassword-automation", "ClusterSecretStore")], f"{name}: store {stores}")
        remote = {d["match"]["remoteRef"]["remoteKey"] for d in ps["spec"]["data"]}
        require(remote == items, f"{name}: remoteKey {remote}")
    bucket_push = find(docs, "PushSecret", "ducklake-ceph-bucket")
    require(bucket_push["spec"]["selector"]["secret"]["name"] == "ducklake", "bucket push must select the OBC secret")


def test_maintenance_cronjob() -> None:
    docs = build()
    cj = find(docs, "CronJob", "ducklake-maintenance")
    spec = cj["spec"]
    require(spec.get("suspend") is True, "CronJob must ship suspended; CHECKPOINT deletes data files for good")
    require(spec.get("concurrencyPolicy") == "Forbid", "concurrencyPolicy must be Forbid")
    pod = spec["jobTemplate"]["spec"]["template"]["spec"]
    c = pod["containers"][0]
    require(
        re.fullmatch(r"docker\.io/duckdb/duckdb:\d+\.\d+\.\d+", c["image"]) is not None,
        f"image must be the official duckdb/duckdb with an exact tag, got {c['image']}",
    )
    secrets = {e["valueFrom"]["secretKeyRef"]["name"] for e in c["env"] if "valueFrom" in e}
    secrets |= {e["secretRef"]["name"] for e in c.get("envFrom", [])}
    require(secrets == {"ducklake", "ducklake-secret"}, f"CronJob credentials must come from the two secrets, got {secrets}")
    cm = find(docs, "ConfigMap", "ducklake-maintenance")
    sql = cm["data"]["maintenance.sql"]
    order = [sql.find(s) for s in ("ATTACH", "'expire_older_than', '30 days'", "'delete_older_than', '3 days'", "CHECKPOINT")]
    require(all(i >= 0 for i in order) and order == sorted(order), f"maintenance.sql order/options wrong: {order}")
    require("URL_STYLE 'path'" in sql, "S3 secret must be path style")
    require(not re.search(r"(KEY_ID|SECRET|PASSWORD)\s+'[^']", sql), "maintenance.sql must read credentials from the environment")


def test_cluster_certificate() -> None:
    text = CLUSTER.read_text()
    cluster = next(d for d in yaml.safe_load_all(text) if d and d.get("kind") == "Cluster")
    certs = cluster["spec"].get("certificates") or {}
    require(
        "postgres-17.${SECRET_DOMAIN}" in (certs.get("serverAltDNSNames") or []),
        "cluster-17 must add postgres-17.${SECRET_DOMAIN} to serverAltDNSNames",
    )
    require(
        "serverTLSSecret" not in certs and "serverCASecret" not in certs,
        "a user-supplied server certificate ties intra-cluster trust to a public CA chain; see skill databases ducklake.md",
    )


def main() -> int:
    tests = [
        test_build_and_overlay,
        test_secret_keys,
        test_bucket_protection_and_pushes,
        test_maintenance_cronjob,
        test_cluster_certificate,
    ]
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            test()
        except Failure as exc:
            failed += 1
            print(f"[FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors as failures
            failed += 1
            print(f"[FAIL] {name}: unexpected {type(exc).__name__}: {exc}")
        else:
            print(f"[PASS] {name}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
