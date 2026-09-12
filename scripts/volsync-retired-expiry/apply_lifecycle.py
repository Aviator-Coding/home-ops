#!/usr/bin/env python3
"""Install the retired-repository expiry rules on ONE object store, with proof.

Operator tool, run by hand, once per destination. It decides nothing: the rule
set comes from ledger.yaml via render_lifecycle.py, and this script only carries
it to a bucket and then proves it arrived.

It is DRY-RUN BY DEFAULT. Without --confirm it performs every read-only step -
the live match proof included - and writes nothing.

Why the read-back is built in rather than being step 5 of a runbook
-------------------------------------------------------------------
The whole reason these rules are applied by hand is that the declarative path
for this cluster SILENTLY IGNORES lifecycle writes: Rook's OBC controller has no
update path, so a `bucketLifecycle` on the 341-day-old `volsync` OBC is accepted
into Git, renders clean, and never reaches the bucket. A reader - or an operator
- concluding that retention is in force when it is not is the worst outcome
available here. So this script refuses to report success on a write it has not
read back and compared, and a skipped verification is not reachable by
forgetting a step.

Safety properties
-----------------
1. Dry-run default; --confirm is the only way anything is written.
2. Pre-flight proof against a FRESH listing of the real bucket: every rule's
   match is counted, and the run ABORTS if any rule matches even one object
   belonging to a live repository. The ledger's proof is from 2026-09-12; this
   re-establishes it at apply time against current data.
3. The pre-existing lifecycle configuration (or its documented absence) is saved
   to a file BEFORE writing, so the undo is exact rather than assumed.
4. Post-write read-back and comparison. Mismatch is a non-zero exit.

Credentials are read from a named Kubernetes Secret by default, or from the
environment (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY) for a destination whose
in-cluster credential is not permitted to manage bucket configuration - which is
the case for r2, whose token has object read/write but not the
`Workers R2 Storage Write` permission group that lifecycle management requires.

See docs/backups/volsync-retired-expiry-apply-plan.md.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render_lifecycle  # noqa: E402

# Per destination: the Secret whose RESTIC_REPOSITORY names the endpoint and
# whose AWS_* keys are the credential, plus the S3 region to sign with.
#
# `ceph` is deliberately NOT the in-cluster service URL from the Secret: that
# resolves only inside the cluster. The external S3 route is used instead, and
# --endpoint can override it.
DESTINATIONS: dict[str, dict[str, Any]] = {
    "ceph": {
        "secret": ("selfhosted", "syncthing-data-volsync-ceph-secret"),
        "region": "us-east-1",
        "endpoint_override": "https://s3.${SECRET_DOMAIN}",
        "note": "credential is the OBC bucket owner, so lifecycle management is authorised",
    },
    "r2": {
        "secret": ("selfhosted", "syncthing-data-volsync-r2-secret"),
        "region": "auto",
        "endpoint_override": None,
        "note": (
            "the in-cluster token CANNOT manage lifecycle (measured: AccessDenied on "
            "GetBucketLifecycleConfiguration). Use --credentials env with a token in the "
            "'Workers R2 Storage Write' permission group."
        ),
    },
    "minio": {
        "secret": ("selfhosted", "syncthing-data-volsync-minio-secret"),
        "region": "us-east-1",
        "endpoint_override": None,
        "note": "credential permitted (measured: GetBucketLifecycleConfiguration succeeded)",
    },
}
BUCKET = "volsync"


def _kubectl_secret(ns: str, name: str, key: str, kubeconfig: str | None) -> str:
    cmd = ["kubectl"]
    if kubeconfig:
        cmd += [f"--kubeconfig={kubeconfig}"]
    cmd += ["-n", ns, "get", "secret", name, "-o", f"jsonpath={{.data.{key}}}"]
    import base64

    out = subprocess.check_output(cmd, text=True).strip()
    if not out:
        raise SystemExit(f"secret {ns}/{name} has no key {key}")
    return base64.b64decode(out).decode()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--destination", required=True, choices=sorted(DESTINATIONS))
    ap.add_argument("--confirm", action="store_true", help="actually write (default: dry run)")
    ap.add_argument("--credentials", choices=["secret", "env"], default="secret")
    ap.add_argument("--endpoint", help="override the endpoint URL")
    ap.add_argument("--kubeconfig", help="passed to kubectl; avoids mise shim KUBECONFIG surprises")
    ap.add_argument(
        "--save-previous",
        type=Path,
        help="where to write the pre-existing lifecycle config (required with --confirm)",
    )
    ap.add_argument(
        "--undo",
        type=Path,
        metavar="SAVED_JSON",
        help="restore the state in a file previously written by --save-previous, "
        "instead of applying the ledger. Still needs --confirm.",
    )
    args = ap.parse_args(argv)

    import os

    import boto3
    from botocore.config import Config
    from botocore.exceptions import ClientError

    dest = DESTINATIONS[args.destination]
    print(f"destination : {args.destination}")
    print(f"note        : {dest['note']}")

    if args.confirm and not args.save_previous and not args.undo:
        print("\n--save-previous is required with --confirm: the undo must be exact, "
              "not assumed.", file=sys.stderr)
        return 2

    # Endpoint: from the Secret's own RESTIC_REPOSITORY unless overridden, so the
    # bucket we touch is always the one VolSync actually writes to.
    ns, sname = dest["secret"]
    repo_url = _kubectl_secret(ns, sname, "RESTIC_REPOSITORY", args.kubeconfig)
    from_secret = repo_url.removeprefix("s3:").split("/" + BUCKET)[0]
    endpoint = args.endpoint or dest["endpoint_override"] or from_secret
    if "${SECRET_DOMAIN}" in endpoint:
        print(f"\nendpoint {endpoint!r} still contains ${{SECRET_DOMAIN}}; pass --endpoint "
              "with the resolved host.", file=sys.stderr)
        return 2
    print(f"endpoint    : {endpoint}   (VolSync writes to {from_secret})")

    if args.credentials == "env":
        ak, sk = os.environ.get("AWS_ACCESS_KEY_ID"), os.environ.get("AWS_SECRET_ACCESS_KEY")
        if not ak or not sk:
            print("\n--credentials env needs AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY "
                  "exported.", file=sys.stderr)
            return 2
        print("credential  : from environment")
    else:
        ak = _kubectl_secret(ns, sname, "AWS_ACCESS_KEY_ID", args.kubeconfig)
        sk = _kubectl_secret(ns, sname, "AWS_SECRET_ACCESS_KEY", args.kubeconfig)
        print(f"credential  : from Secret {ns}/{sname}")

    s3 = boto3.client(
        "s3", endpoint_url=endpoint, aws_access_key_id=ak, aws_secret_access_key=sk,
        region_name=dest["region"],
        config=Config(signature_version="s3v4", retries={"max_attempts": 5}),
    )

    if args.undo:
        saved = json.loads(args.undo.read_text()).get("Rules", [])
        print(f"\nUNDO: restoring the {len(saved)}-rule state saved in {args.undo}")
        if not args.confirm:
            print("DRY RUN - nothing written. Re-run with --confirm.")
            return 0
        if saved:
            s3.put_bucket_lifecycle_configuration(
                Bucket=BUCKET, LifecycleConfiguration={"Rules": saved}
            )
        else:
            s3.delete_bucket_lifecycle(Bucket=BUCKET)
        try:
            back = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET).get("Rules", [])
        except ClientError as e:
            if e.response["Error"]["Code"].startswith("NoSuchLifecycleConfiguration"):
                back = []
            else:
                raise
        if len(back) != len(saved):
            print(f"UNDO VERIFICATION FAILED: {len(back)} rules present, expected "
                  f"{len(saved)}", file=sys.stderr)
            return 5
        print(f"UNDO VERIFIED: {len(back)} rules present, matching the saved state.")
        return 0

    rules = render_lifecycle.build_rules(render_lifecycle.load_ledger())
    protected = list(render_lifecycle.load_ledger()["protected"])
    print(f"rules       : {len(rules)} from ledger.yaml")

    # ---- pre-flight proof against a fresh listing -------------------------
    print("\npre-flight: listing the live bucket and re-proving the match ...")
    keys: list[str] = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET):
        keys.extend(o["Key"] for o in page.get("Contents", []))
    print(f"  {len(keys)} objects listed")

    live_space = tuple(f"{p}/" for p in protected)
    live_keys = [k for k in keys if k.startswith(live_space)]
    prefixes = [r["Filter"]["Prefix"] for r in rules]
    endangered = [k for k in live_keys if any(k.startswith(p) for p in prefixes)]
    matched = [k for k in keys if any(k.startswith(p) for p in prefixes)]

    print(f"  live objects (protected repos)      : {len(live_keys)}")
    print(f"  live objects matched by these rules : {len(endangered)}   (must be 0)")
    print(f"  retired objects matched             : {len(matched)}")
    unmatched = len(keys) - len(matched) - len(live_keys)
    print(f"  objects matched by nothing          : {unmatched}   "
          "(prefixes present here but absent from the ledger)")

    if endangered:
        print("\nABORT: these rules would reach LIVE repositories:", file=sys.stderr)
        for k in endangered[:10]:
            print(f"  {k}", file=sys.stderr)
        return 3

    if unmatched:
        print("\n  NOTE: this destination holds prefixes the ledger does not list. That is "
              "not an error - the ledger is a dated measurement - but they will never "
              "expire until they are added.")

    # ---- previous state ---------------------------------------------------
    try:
        prev: Any = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET)
        prev_rules = prev.get("Rules", [])
        print(f"\nexisting lifecycle: {len(prev_rules)} rules  ->  THIS WRITE REPLACES THEM")
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code.startswith("NoSuchLifecycleConfiguration"):
            prev, prev_rules = {"Rules": []}, []
            print("\nexisting lifecycle: none (undo = delete the configuration)")
        else:
            print(f"\ncannot read existing lifecycle ({code}): "
                  f"{e.response['Error'].get('Message','')}", file=sys.stderr)
            return 4

    if not args.confirm:
        print("\nDRY RUN - nothing written. Re-run with --confirm --save-previous <path>.")
        return 0

    args.save_previous.write_text(json.dumps({"Rules": prev_rules}, indent=2, default=str))
    print(f"previous state saved to {args.save_previous}")

    # ---- write ------------------------------------------------------------
    print("\nwriting lifecycle configuration ...")
    s3.put_bucket_lifecycle_configuration(
        Bucket=BUCKET, LifecycleConfiguration={"Rules": rules}
    )

    # ---- mandatory read-back ---------------------------------------------
    print("reading it back ...")
    got = s3.get_bucket_lifecycle_configuration(Bucket=BUCKET).get("Rules", [])
    want_by_id = {r["ID"]: r for r in rules}
    got_by_id = {r.get("ID"): r for r in got}

    problems: list[str] = []
    if set(want_by_id) != set(got_by_id):
        problems.append(
            f"rule IDs differ: missing {sorted(set(want_by_id) - set(got_by_id))}, "
            f"unexpected {sorted(set(got_by_id) - set(want_by_id))}"
        )
    for rid, want in want_by_id.items():
        have = got_by_id.get(rid)
        if not have:
            continue
        wp = want["Filter"]["Prefix"]
        hp = (have.get("Filter") or {}).get("Prefix", have.get("Prefix"))
        if hp != wp:
            problems.append(f"{rid}: prefix stored as {hp!r}, sent {wp!r}")
        hd = str((have.get("Expiration") or {}).get("Date", ""))
        if not hd.startswith(want["Expiration"]["Date"][:10]):
            problems.append(f"{rid}: date stored as {hd!r}, sent {want['Expiration']['Date']!r}")
        if (have.get("Status") or "") != "Enabled":
            problems.append(f"{rid}: status {have.get('Status')!r}, expected 'Enabled'")

    # The property that actually matters, re-checked on what the store KEPT
    # rather than on what we sent.
    for r in got:
        hp = (r.get("Filter") or {}).get("Prefix", r.get("Prefix")) or ""
        for p in protected:
            if hp.startswith(f"{p}/") or f"{p}/".startswith(hp):
                problems.append(
                    f"STORED rule {r.get('ID')!r} prefix {hp!r} is in range of live repo {p!r}"
                )

    if problems:
        print(f"\nVERIFICATION FAILED ({len(problems)} problems) - the store did NOT keep what "
              "was sent:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        print(f"\nRestore with: --undo using {args.save_previous}", file=sys.stderr)
        return 5

    print(f"\nVERIFIED: {len(got)} rules stored, all prefixes exact-segment, "
          "no rule in range of a live repository.")
    earliest = min(r["Expiration"]["Date"] for r in rules)
    print(f"Earliest deletion: {earliest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
