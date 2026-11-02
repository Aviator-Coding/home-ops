#!/usr/bin/env python3
# ci: gate
"""Semantic regression test for the VolSync retired-repository expiry ledger.

Prevents: a retired-repository expiry prefix (`paperless-ngx`, `syncthing`) also
selecting and expiring a sibling repository (`paperless-ngx-media`, `syncthing-data`)
or a live one, and the applier reading credentials from a Secret that no longer exists.

Pins the 2026-09-12 captain decision to put an EXPIRY DATE on the retired
pre-kopiur VolSync restic repositories - not delete them now, not keep them
forever - and, much more importantly, pins the one property that makes that
decision safe to execute.

The hazard this file exists for
------------------------------
The `volsync` buckets hold one restic repository per top-level prefix. Some of
those prefixes are strict string extensions of another: `syncthing` and
`syncthing-data`, `paperless-ngx` and `paperless-ngx-media`. All four are
RETIRED since issue #2202 (PR-2 to PR-4), so each pair needs two intended,
distinct rules, and `protected` is empty: no claim runs VolSync. Any match built
with `startswith`, an unanchored regex, a glob, or a bare S3 `Filter.Prefix` of
`paperless-ngx` selects `paperless-ngx-media` too - and any repository put back
on VolSync later is exactly as exposed as `syncthing-data` was when it was live
(all 107 of its objects in the ceph bucket on 2026-09-12 matched a bare
`syncthing`). That deletion would look completely correct in review.

The ledger avoids it structurally rather than carefully: entries are bare path
segments, and `render_lifecycle.py` appends the slash in exactly one place, so
the emitted match is `syncthing/`, which cannot reach `syncthing-data/...`
because the strings differ at index 9 (`/` vs `-`), and `paperless-ngx/`
cannot reach `paperless-ngx-media/...` the same way. These tests assert that the
structure is still intact, that the renderer still fails closed when it is not,
and that the ledger's idea of which repositories are live still agrees with what
the repo actually declares.

Scope note: this deliberately does NOT assert the expiry dates themselves. They
are a judgement call the captain can overrule by editing one line, and freezing
them here would turn a legitimate future edit into a red check - the trap
`falkordb-lan-browser-access-test.py` was converted away from. What is asserted
is the SHAPE of a date (absolute, midnight UTC, after the measurement) and the
absence of a `Days`-based expiry, which would be a correctness bug rather than a
preference: `Days` counts from each object's own creation time and would delete
most of this the moment it was installed.

The repositories outlive the VolSync operator (removed in issue #2202 PR-6): the
`volsync` bucket stays in the `system/volsync` Flux Kustomization, now rendering
only backend/, and its OBC Secret is the applier's ceph credential.

Owning document: .agents/skills/kopiur-backups/references/retired-repo-expiry.md
"""

from __future__ import annotations

import copy
import datetime as dt
import re
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
TOOL_DIR = REPO / "scripts" / "volsync-retired-expiry"
LEDGER = TOOL_DIR / "ledger.yaml"
SKILL_REFS = REPO / ".agents" / "skills" / "kopiur-backups" / "references"
DOC = SKILL_REFS / "retired-repo-expiry.md"
APPLY_PLAN = SKILL_REFS / "retired-expiry-apply-plan.md"
APPLIER = TOOL_DIR / "apply_lifecycle.py"
APPS_MAIN = REPO / "kubernetes" / "apps" / "main"
BUCKET_OVERLAY = APPS_MAIN / "system" / "volsync.yaml"
BUCKET_DIR = REPO / "kubernetes" / "apps" / "base" / "system" / "volsync" / "backend"

sys.path.insert(0, str(TOOL_DIR))
import render_lifecycle  # noqa: E402

# Real object keys, copied from the 2026-09-12 read-only listing of the ceph
# `volsync` bucket. The point of using measured keys rather than invented ones
# is that the adversarial case here is a real pair of repositories, not a
# hypothetical naming scheme.
MEASURED_KEYS = {
    "syncthing": ["syncthing/config", "syncthing/snapshots/0ed2", "syncthing/data/00/00ab"],
    "syncthing-data": [
        "syncthing-data/config",
        "syncthing-data/snapshots/9dd2",
        "syncthing-data/data/1f/1f0c",
    ],
}

MEASURED_KEYS["paperless-ngx"] = [
    "paperless-ngx/config",
    "paperless-ngx/snapshots/4a1c",
    "paperless-ngx/data/00/00ab",
]
MEASURED_KEYS["paperless-ngx-media"] = [
    "paperless-ngx-media/config",
    "paperless-ngx-media/snapshots/77d0",
    "paperless-ngx-media/data/0a/0a1f",
]

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T00:00:00Z$")


class Failure(Exception):
    pass


def ledger() -> dict[str, Any]:
    return render_lifecycle.load_ledger(LEDGER)


def rules() -> list[dict[str, Any]]:
    return render_lifecycle.build_rules(ledger())


def live_volsync_apps_from_git() -> set[str]:
    """The APP values whose Flux overlay still wires up components/volsync.

    This is the repo's own declaration of which restic repositories are still
    being written to, so the ledger's `protected` list is checked against the
    manifests rather than against a hand-maintained copy that can drift. YAML
    anchors (`APP: *app`) resolve during parsing, so this reads the effective
    value. The operator and component are removed (issue #2202 PR-6), so this is
    empty and `protected` must be too; a revert that wires a claim back in makes
    that claim live again, and this is how the ledger finds out.
    """
    live: set[str] = set()
    for path in sorted(APPS_MAIN.rglob("*.yaml")):
        try:
            docs = list(yaml.safe_load_all(path.read_text()))
        except yaml.YAMLError as exc:  # pragma: no cover - malformed manifest
            raise Failure(f"{path.relative_to(REPO)} is not parseable: {exc}") from exc
        for doc in docs:
            if not isinstance(doc, dict) or doc.get("kind") != "Kustomization":
                continue
            spec = doc.get("spec") or {}
            components = spec.get("components") or []
            uses_volsync = "components/volsync" in (spec.get("path") or "") or any(
                "components/volsync" in str(c) for c in components
            )
            if not uses_volsync:
                continue
            app = ((spec.get("postBuild") or {}).get("substitute") or {}).get("APP")
            if app:
                live.add(str(app))
    return live


def test_ledger_entries_are_bare_segments() -> None:
    led = ledger()
    entries = list(led["protected"])
    for tier in led["tiers"].values():
        entries.extend(tier["prefixes"])
    for name in entries:
        if not isinstance(name, str) or not name or "/" in name or name.strip() != name:
            raise Failure(
                f"ledger entry {name!r} is not a bare single path segment; the "
                "trailing slash must only ever be added by render_lifecycle.py"
            )


def test_protected_matches_what_the_repo_declares_live() -> None:
    declared = live_volsync_apps_from_git()
    protected = set(ledger()["protected"])
    if declared != protected:
        raise Failure(
            "ledger `protected` disagrees with the overlays that still use "
            f"components/volsync. Declared live in Git: {sorted(declared)}; "
            f"ledger protected: {sorted(protected)}. If a claim was just "
            "retired from VolSync, move it into a tier deliberately; if one was "
            "re-onboarded, it must be protected before any rule is applied."
        )


def test_no_retired_entry_still_has_a_live_volsync_source() -> None:
    declared = live_volsync_apps_from_git()
    led = ledger()
    for tier_name, tier in led["tiers"].items():
        clash = sorted(set(tier["prefixes"]) & declared)
        if clash:
            raise Failure(
                f"tier {tier_name!r} lists {clash} as retired, but their "
                "overlays still wire up components/volsync - expiring those "
                "would delete a repository that is still being written to"
            )


def test_every_rule_is_an_exact_segment_match() -> None:
    led = ledger()
    expected = {n for tier in led["tiers"].values() for n in tier["prefixes"]}
    got = set()
    for rule in rules():
        prefix = rule["Filter"]["Prefix"]
        if not prefix.endswith("/") or prefix.count("/") != 1:
            raise Failure(
                f"rule {rule['ID']} has Filter.Prefix {prefix!r}; it must be a "
                "single path segment followed by exactly one slash"
            )
        got.add(prefix[:-1])
    if got != expected:
        raise Failure(f"rendered rules {sorted(got)} != ledger entries {sorted(expected)}")


def _repo_keys(name: str) -> list[str]:
    """Measured keys when there are any, else the restic layout every repository has."""
    return MEASURED_KEYS.get(name) or [f"{name}/config", f"{name}/snapshots/0000", f"{name}/data/00/0000"]


# The string-prefix pairs measured in the bucket. Both halves of each are retired.
SIBLING_PAIRS = (("syncthing", "syncthing-data"), ("paperless-ngx", "paperless-ngx-media"))


def test_no_rule_reaches_another_ledger_repository() -> None:
    """Every rule matches its own repository's keys and no other ledger entry's.

    The ledger is the full list of repositories in the bucket (retired and live),
    so this is the exact-segment property checked against every real neighbour,
    not one worked example. The sibling pairs must still be in the ledger and
    still collide under a bare prefix, or the check has lost its hard cases.
    """
    led = ledger()
    names = list(led["protected"]) + [n for tier in led["tiers"].values() for n in tier["prefixes"]]
    by_id = {r["ID"]: r for r in rules()}
    for rule in by_id.values():
        prefix = rule["Filter"]["Prefix"]
        own = rule["ID"].removeprefix("volsync-retired-")
        mine = _repo_keys(own)
        if [k for k in mine if k.startswith(prefix)] != mine:
            raise Failure(f"{prefix!r} failed to match its own repository's keys: {mine}")
        for other in names:
            if other == own:
                continue
            crossed = [k for k in _repo_keys(other) if k.startswith(prefix)]
            if crossed:
                raise Failure(
                    f"CATASTROPHIC: rule {rule['ID']} prefix {prefix!r} reaches the {other!r} "
                    f"repository: {crossed}"
                )
    for stem, ext in SIBLING_PAIRS:
        for name in (stem, ext):
            rule = by_id.get(f"volsync-retired-{name}")
            if rule is None or rule["Filter"]["Prefix"] != f"{name}/":
                raise Failure(f"expected exactly the exact-segment rule `{name}/` for retired {name!r}")
        # The collision is still real, so the slash is still what keeps the rules apart.
        if not [k for k in _repo_keys(ext) if k.startswith(stem)]:
            raise Failure(
                f"the {stem}/{ext} collision is no longer reproducible; this test's "
                "fixtures have drifted from the real bucket layout"
            )


def test_no_rule_can_reach_a_protected_repository() -> None:
    """No rule reaches a live repository, now and if a stem goes back on VolSync.

    `protected` is empty since issue #2202 PR-4, so the loop over it is vacuous
    today. The second half keeps the guard real: it re-lists `paperless-ngx`
    (the live stem of retired `paperless-ngx-media` until 2026-11-01) as
    protected in a copy of the ledger and requires the render to keep its
    sibling's rule while reaching none of its keys.
    """
    led = ledger()
    for live in led["protected"]:
        keys = MEASURED_KEYS.get(live)
        if not keys:
            raise Failure(f"no measured keys for protected repository {live!r}; add a fixture")
        for rule in rules():
            prefix = rule["Filter"]["Prefix"]
            hit = [k for k in keys if k.startswith(prefix)]
            if hit:
                raise Failure(
                    f"CATASTROPHIC: rule {rule['ID']} prefix {prefix!r} matches LIVE "
                    f"{live} objects {hit}"
                )
    for stem, ext in SIBLING_PAIRS:
        relive = copy.deepcopy(led)
        for tier in relive["tiers"].values():
            tier["prefixes"] = [n for n in tier["prefixes"] if n != stem]
        relive["protected"] = [stem]
        got = render_lifecycle.build_rules(relive)
        prefixes = {r["Filter"]["Prefix"] for r in got}
        if f"{ext}/" not in prefixes or f"{stem}/" in prefixes:
            raise Failure(f"re-protecting {stem!r}: expected `{ext}/` kept and `{stem}/` gone, got {sorted(prefixes)}")
        hit = [k for k in _repo_keys(stem) for p in prefixes if k.startswith(p)]
        if hit:
            raise Failure(f"CATASTROPHIC: with {stem!r} live again, rules reach its objects {hit}")


def test_renderer_refuses_a_ledger_that_endangers_a_live_repository() -> None:
    """Prove the fail-closed guard actually refuses, rather than trusting it."""
    base = ledger()

    # `protected` is empty since issue #2202 PR-4, so mark a retired stem live in a
    # copy: listing it in a tier as well must be refused, not rendered.
    moved = copy.deepcopy(base)
    moved["protected"] = list(base["protected"]) + ["paperless-ngx"]
    try:
        render_lifecycle.build_rules(moved)
    except render_lifecycle.UnsafeLedger:
        pass
    else:
        raise Failure("renderer accepted a ledger listing a LIVE repository (paperless-ngx) as retired")

    preslashed = copy.deepcopy(base)
    preslashed["tiers"]["redundant"]["prefixes"].append("syncthing-data/")
    try:
        render_lifecycle.build_rules(preslashed)
    except render_lifecycle.UnsafeLedger:
        pass
    else:
        raise Failure("renderer accepted a pre-slashed ledger entry")

    dupe = copy.deepcopy(base)
    dupe["tiers"]["sole_copy"]["prefixes"].append(dupe["tiers"]["redundant"]["prefixes"][0])
    try:
        render_lifecycle.build_rules(dupe)
    except render_lifecycle.UnsafeLedger:
        pass
    else:
        raise Failure("renderer accepted the same prefix in two tiers with different dates")


def test_expiry_is_absolute_midnight_utc_and_never_day_based() -> None:
    led = ledger()
    measured = dt.date.fromisoformat(str(led["measured_at"]))
    for rule in rules():
        exp = rule["Expiration"]
        if "Days" in exp:
            raise Failure(
                f"rule {rule['ID']} uses Expiration.Days. Days counts from each "
                "object's own creation time, and these repositories stopped "
                "being written between 2025-05 and 2026-09, so it would delete "
                "them immediately instead of on an expiry date."
            )
        date = exp.get("Date", "")
        if not DATE_RE.match(date):
            raise Failure(
                f"rule {rule['ID']} has Expiration.Date {date!r}; S3 requires an "
                "absolute ISO 8601 date at midnight UTC"
            )
        if dt.date.fromisoformat(date[:10]) <= measured:
            raise Failure(
                f"rule {rule['ID']} expires {date} which is not after the "
                f"ledger's measured_at {measured} - landing it would delete "
                "immediately rather than set a recovery window"
            )


def test_owning_document_exists() -> None:
    if not DOC.is_file():
        raise Failure(f"owning document missing: {DOC.relative_to(REPO)}")
    text = DOC.read_text()
    for needle in ("syncthing-data", "render_lifecycle.py", "ledger.yaml"):
        if needle not in text:
            raise Failure(f"owning document does not mention {needle!r}")


def test_decision_record_says_up_front_that_nothing_is_applied() -> None:
    """The captain's explicit requirement, and the highest-stakes line in the docs.

    Nothing in this repo applies these rules, and one of the three stores silently
    ignores the declarative write that would look like it had. A reader who
    concludes the retention is in force is wrong in the direction that loses data
    later - so the disclaimer must stay at the TOP, not drift into a footnote.
    """
    head = DOC.read_text().split("\n")[:24]
    banner = "\n".join(head)
    if "NOT IN FORCE" not in banner:
        raise Failure(
            "the decision record no longer states in its first 24 lines that the "
            "retention is NOT in force - that disclaimer is load-bearing"
        )
    for needle in ("merging does not apply them", "apply-plan"):
        if needle not in banner:
            raise Failure(f"decision-record banner no longer contains {needle!r}")


def test_apply_plan_exists_and_covers_every_destination() -> None:
    if not APPLY_PLAN.is_file():
        raise Failure(f"apply plan missing: {APPLY_PLAN.relative_to(REPO)}")
    text = APPLY_PLAN.read_text()
    if "has been executed" not in text and "not been executed" not in text:
        raise Failure("apply plan does not state that it has not been executed")
    for dest in ("ceph", "r2", "minio"):
        if f"--destination {dest}" not in text:
            raise Failure(f"apply plan gives no command for destination {dest!r}")
    for needle in ("--undo", "--save-previous", "Workers R2 Storage Write", "--credentials env",
                   "system/volsync", "volsync-template", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        if needle not in text:
            raise Failure(
                f"apply plan no longer covers {needle!r} - undo, exact-undo material "
                "and the r2 permission prerequisite are all required"
            )


def test_applier_is_dry_run_by_default_and_covers_all_destinations() -> None:
    """Execute apply_lifecycle.py's real main() and prove the safety gate behaviourally.

    boto3 is imported unconditionally by apply_lifecycle.py's main(); it is not a
    hypothetical here, it is a transitive dependency of `litellm[proxy]`, which
    `validate.yaml`'s python-tests job already installs for every other test in
    this file's own suite. No network call is made - the S3 client is faked.
    """
    if not APPLIER.is_file():
        raise Failure("apply_lifecycle.py missing")

    sys.path.insert(0, str(TOOL_DIR))
    import importlib.util

    spec = importlib.util.spec_from_file_location("apply_lifecycle", APPLIER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    if set(mod.DESTINATIONS) != {"ceph", "r2", "minio"}:
        raise Failure(
            f"applier destinations {sorted(mod.DESTINATIONS)} != the three volsync "
            "buckets; the captain's scope is all three"
        )
    if mod.BUCKET != "volsync":
        raise Failure(f"applier targets bucket {mod.BUCKET!r}, expected 'volsync'")

    try:
        import boto3
    except ModuleNotFoundError as exc:
        raise Failure(
            "boto3 is not importable in this environment; it is required to "
            "behaviourally exercise apply_lifecycle.py's main()"
        ) from exc

    def refuse_kubectl(*_a: Any, **_k: Any) -> str:
        raise Failure(
            "apply_lifecycle.py reached kubectl/credentials before refusing "
            "--confirm without --save-previous - the refusal must come first"
        )

    mod._kubectl_secret = refuse_kubectl
    rc = mod.main(["--destination", "ceph", "--confirm"])
    if rc != 2:
        raise Failure(
            f"--confirm without --save-previous returned {rc}, expected 2 - "
            "writing must stay unreachable without explicit undo material"
        )

    write_calls: list[str] = []

    class _FakePaginator:
        def paginate(self, **_kw: Any) -> Any:
            yield {"Contents": []}

    class _FakeS3:
        def get_paginator(self, _name: str) -> Any:
            return _FakePaginator()

        def get_bucket_lifecycle_configuration(self, **_kw: Any) -> Any:
            from botocore.exceptions import ClientError

            raise ClientError(
                {"Error": {"Code": "NoSuchLifecycleConfiguration", "Message": "none"}},
                "GetBucketLifecycleConfiguration",
            )

        def put_bucket_lifecycle_configuration(self, **_kw: Any) -> None:
            write_calls.append("put_bucket_lifecycle_configuration")

        def delete_bucket_lifecycle(self, **_kw: Any) -> None:
            write_calls.append("delete_bucket_lifecycle")

    def fake_kubectl_secret(_ns: str, _name: str, key: str, _kubeconfig: str | None) -> str:
        if key == "RESTIC_REPOSITORY":
            return "s3:https://minio.example.invalid/volsync"
        return "fake"

    mod._kubectl_secret = fake_kubectl_secret
    real_client = boto3.client
    boto3.client = lambda *_a, **_k: _FakeS3()
    try:
        # No --confirm anywhere on this call - proves confirm defaults to False
        # via the module's own argparse parser, not by reading its text: if it
        # defaulted to True, main() would take the --save-previous-required
        # branch above and return 2, not run the full dry-run path to rc 0.
        # ceph reads the OBC Secret by default (faked above).
        rc = mod.main(["--destination", "ceph", "--endpoint", "https://s3.example.invalid"])
        if rc != 0:
            raise Failure(f"ceph dry run (no --confirm) exited {rc}, expected 0")
        # minio has no in-cluster Secret any more: env credentials are its default.
        with _env(AWS_ACCESS_KEY_ID="fake", AWS_SECRET_ACCESS_KEY="fake"):
            mod._kubectl_secret = refuse_kubectl
            rc = mod.main(["--destination", "minio", "--endpoint", "https://minio.example.invalid"])
    finally:
        boto3.client = real_client

    if rc != 0:
        raise Failure(f"dry run (no --confirm) exited {rc}, expected 0")
    if write_calls:
        raise Failure(f"dry run (no --confirm) called write method(s): {write_calls}")


class _env:
    """Set (or, with None, unset) environment variables for a block."""

    def __init__(self, **kw: str | None) -> None:
        self.kw = kw
        self.old: dict[str, str | None] = {}

    def __enter__(self) -> None:
        import os

        for k, v in self.kw.items():
            self.old[k] = os.environ.get(k)
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def __exit__(self, *_a: Any) -> None:
        import os

        for k, v in self.old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _load_applier() -> Any:
    import importlib.util

    spec = importlib.util.spec_from_file_location("apply_lifecycle_cred", APPLIER)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_applier_credential_sources_survive_the_volsync_claims_leaving() -> None:
    """Issue #2202 decision 5.

    The per-claim `<claim>-volsync-<dest>-secret` Secrets disappear when the last
    claim leaves VolSync, so the applier must not read any of them. ceph reads the
    OBC Secret `system/volsync` (which outlives the operator: the OBC is
    `prune: disabled`); minio and r2 take credentials from the environment, filled
    from the 1Password item `volsync-template`.
    """
    import boto3

    text = APPLIER.read_text()
    for needle in ("syncthing-data", "paperless-ngx", "-volsync-", "selfhosted"):
        if needle in text:
            raise Failure(f"apply_lifecycle.py still references {needle!r}, a pruned credential source")

    mod = _load_applier()
    if mod.DESTINATIONS["ceph"]["secret"] != ("system", "volsync"):
        raise Failure(f"ceph credentials come from {mod.DESTINATIONS['ceph']['secret']}, expected system/volsync")
    for dest in ("minio", "r2"):
        if mod.DESTINATIONS[dest].get("secret") is not None:
            raise Failure(f"{dest} still names an in-cluster Secret; it must be env-only")
        if mod.DESTINATIONS[dest]["credentials"] != "env":
            raise Failure(f"{dest} must default to --credentials env")

    seen: list[tuple[str, str, str]] = []
    clients: list[dict[str, Any]] = []

    class _S3:
        def get_paginator(self, _n: str) -> Any:
            class _P:
                def paginate(self, **_k: Any) -> Any:
                    yield {"Contents": []}

            return _P()

        def get_bucket_lifecycle_configuration(self, **_k: Any) -> Any:
            from botocore.exceptions import ClientError

            raise ClientError({"Error": {"Code": "NoSuchLifecycleConfiguration"}}, "Get")

        def put_bucket_lifecycle_configuration(self, **_k: Any) -> None:
            raise Failure("a dry run reached put_bucket_lifecycle_configuration")

        def delete_bucket_lifecycle(self, **_k: Any) -> None:
            raise Failure("a dry run reached delete_bucket_lifecycle")

    def fake_secret(ns: str, name: str, key: str, _kc: str | None) -> str:
        seen.append((ns, name, key))
        return f"fake-{key}"

    def fake_client(*_a: Any, **kw: Any) -> Any:
        clients.append(kw)
        return _S3()

    real = boto3.client
    boto3.client = fake_client
    mod._kubectl_secret = fake_secret
    try:
        # ceph: Secret system/volsync, AWS_* keys, nothing from selfhosted.
        rc = mod.main(["--destination", "ceph", "--endpoint", "https://s3.example.invalid"])
        if rc != 0:
            raise Failure(f"ceph dry run exited {rc}")
        want = {("system", "volsync", "AWS_ACCESS_KEY_ID"), ("system", "volsync", "AWS_SECRET_ACCESS_KEY")}
        if set(seen) != want:
            raise Failure(f"ceph read {sorted(set(seen))}, expected exactly {sorted(want)}")
        if clients[-1]["aws_access_key_id"] != "fake-AWS_ACCESS_KEY_ID":
            raise Failure("ceph client was not built from the OBC Secret key")

        # minio / r2: env only, never kubectl.
        def no_kubectl(*_a: Any, **_k: Any) -> str:
            raise Failure("minio/r2 must not read a Kubernetes Secret")

        mod._kubectl_secret = no_kubectl
        for dest, ep in (("minio", "https://minio.example.invalid"), ("r2", None)):
            argv = ["--destination", dest] + (["--endpoint", ep] if ep else [])
            with _env(AWS_ACCESS_KEY_ID="env-ak", AWS_SECRET_ACCESS_KEY="env-sk"):
                rc = mod.main(argv)
            if rc != 0:
                raise Failure(f"{dest} dry run with env credentials exited {rc}")
            if clients[-1]["aws_access_key_id"] != "env-ak" or clients[-1]["aws_secret_access_key"] != "env-sk":
                raise Failure(f"{dest} client was not built from the environment credentials")
            with _env(AWS_ACCESS_KEY_ID=None, AWS_SECRET_ACCESS_KEY=None):
                rc = mod.main(argv)
            if rc != 2:
                raise Failure(f"{dest} without env credentials exited {rc}, expected 2")
            rc = mod.main(argv + ["--credentials", "secret"])
            if rc != 2:
                raise Failure(f"{dest} --credentials secret exited {rc}, expected 2 (no in-cluster Secret)")
        r2_ep = [c for c in clients if "r2.cloudflarestorage.com" in c.get("endpoint_url", "")]
        if not r2_ep:
            raise Failure("r2 did not default to the endpoint in ledger.yaml")
    finally:
        boto3.client = real


def test_ceph_credential_source_is_still_declared() -> None:
    """The applier's ceph credential (Secret system/volsync) comes from the OBC.

    The OBC stays in Flux Kustomization system/volsync after the operator left
    (issue #2202 PR-6). If it left Git or lost `prune: disabled`, Flux would delete
    the bucket with every retired repository in it, and the applier would lose its
    only ceph credential.
    """
    ks = [d for d in yaml.safe_load_all(BUCKET_OVERLAY.read_text()) if isinstance(d, dict)]
    paths = [str((d.get("spec") or {}).get("path") or "") for d in ks if d.get("kind") == "Kustomization"]
    if paths != ["./kubernetes/apps/base/system/volsync/backend"]:
        raise Failure(f"{BUCKET_OVERLAY.relative_to(REPO)} must render only the bucket backend, got {paths}")
    resources = yaml.safe_load((BUCKET_DIR / "kustomization.yaml").read_text()).get("resources") or []
    obcs = []
    for res in resources:
        f = BUCKET_DIR / str(res)
        if f.is_file():
            obcs += [d for d in yaml.safe_load_all(f.read_text()) if isinstance(d, dict) and d.get("kind") == "ObjectBucketClaim"]
    names = [((d.get("metadata") or {}).get("name"), (d.get("spec") or {}).get("bucketName")) for d in obcs]
    if names != [("volsync", "volsync")]:
        raise Failure(f"expected one OBC volsync/volsync in {BUCKET_DIR.relative_to(REPO)}, got {names}")
    ann = (obcs[0].get("metadata") or {}).get("annotations") or {}
    if ann.get("kustomize.toolkit.fluxcd.io/prune") != "disabled":
        raise Failure("OBC volsync lost kustomize.toolkit.fluxcd.io/prune: disabled")


def main() -> int:
    tests: list[str] = []
    failures: list[str] = []

    def run(name: str, fn: Any) -> None:
        tests.append(name)
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as e:
            failures.append(name)
            print(f"[FAIL] {name}: {e}")
        except render_lifecycle.UnsafeLedger as e:
            # The renderer's own fail-closed guard fired. That is the correct
            # behaviour for an unsafe ledger, but it still means this branch
            # cannot be rendered, so it is a failure - reported as a refusal
            # rather than as a test bug so the reason is obvious.
            failures.append(name)
            print(f"[FAIL] {name}: renderer refused to render: {e}")
        except Exception as e:  # noqa: BLE001
            failures.append(name)
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}")

    run("ledger_entries_are_bare_segments", test_ledger_entries_are_bare_segments)
    run("protected_matches_what_the_repo_declares_live", test_protected_matches_what_the_repo_declares_live)
    run("no_retired_entry_still_has_a_live_volsync_source", test_no_retired_entry_still_has_a_live_volsync_source)
    run("every_rule_is_an_exact_segment_match", test_every_rule_is_an_exact_segment_match)
    run("no_rule_reaches_another_ledger_repository", test_no_rule_reaches_another_ledger_repository)
    run("no_rule_can_reach_a_protected_repository", test_no_rule_can_reach_a_protected_repository)
    run("renderer_refuses_a_ledger_that_endangers_a_live_repository", test_renderer_refuses_a_ledger_that_endangers_a_live_repository)
    run("expiry_is_absolute_midnight_utc_and_never_day_based", test_expiry_is_absolute_midnight_utc_and_never_day_based)
    run("owning_document_exists", test_owning_document_exists)
    run("decision_record_says_up_front_that_nothing_is_applied", test_decision_record_says_up_front_that_nothing_is_applied)
    run("apply_plan_exists_and_covers_every_destination", test_apply_plan_exists_and_covers_every_destination)
    run("applier_is_dry_run_by_default_and_covers_all_destinations", test_applier_is_dry_run_by_default_and_covers_all_destinations)
    run("applier_credential_sources_survive_the_volsync_claims_leaving", test_applier_credential_sources_survive_the_volsync_claims_leaving)
    run("ceph_credential_source_is_still_declared", test_ceph_credential_source_is_still_declared)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
