#!/usr/bin/env python3
"""Semantic regression test for kopiur Stage 0 (operator + repositories only).

Stage 0 is additive by design: install the kopiur operator and declare WHERE
backups could go, without changing a single existing VolSync backup and without
creating any SnapshotPolicy/SnapshotSchedule (Stage 1).

This test does not grep source text as its evidence. It:
  1. Renders the real kustomize builds Flux would apply for app + repository.
  2. Parses the overlay Flux Kustomizations and Renovate autoMerge config into
     structured objects.
  3. Asserts the Stage 0 safety contract on those objects:
       - operator pin is an exact chart tag; install+upgrade CRDs CreateReplace
       - monitoring (ServiceMonitor, PrometheusRule, dashboards) enabled, plus
         our own absent(up{...}) operator/webhook alerts
       - overlay healthChecks target the controller/webhook Deployments with
         wait: false
       - exactly two ClusterRepositories (ceph, r2) - no MinIO - each on a NEW
         `kopiur` bucket with allowedNamespaces.all and explicit deletion
         protection (threshold + onNamespaceDelete: Orphan)
       - no SnapshotPolicy / SnapshotSchedule / ReplicationSource objects
       - credentials use explicit remoteRef.property (no dataFrom.extract) and
         never embed secret values
       - Renovate still matches kopiur packages but the LAST matching rule sets
         automerge: false (packageRules are last-match-wins)

Live Ready / `kubectl kopiur doctor` remain post-merge gates; this pins the
GitOps contract that must hold before merge.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
KOPIUR_BASE = ROOT / "kubernetes/apps/base/system/kopiur"
KOPIUR_APP = KOPIUR_BASE / "app"
KOPIUR_REPO = KOPIUR_BASE / "repository"
KOPIUR_OVERLAY = ROOT / "kubernetes/apps/main/system/kopiur.yaml"
SYSTEM_OVERLAY_KUST = ROOT / "kubernetes/apps/main/system/kustomization.yaml"
AUTOMERGE = ROOT / ".renovate/autoMerge.json5"
VOLSYNC_TREE = [
    ROOT / "kubernetes/apps/base/system/volsync",
    ROOT / "kubernetes/components/volsync",
    ROOT / "kubernetes/apps/main/system/volsync.yaml",
]

FORBIDDEN_KINDS = frozenset(
    {
        "SnapshotPolicy",
        "SnapshotSchedule",
        "ReplicationSource",
        "ReplicationDestination",
        "ReplicationSource",  # volsync
        "Destination",  # defensive
    }
)

# Kinds Stage 0 must never emit into either kustomize build.
STAGE0_FORBIDDEN_KINDS = frozenset(
    {
        "SnapshotPolicy",
        "SnapshotSchedule",
        "ReplicationSource",
        "ReplicationDestination",
    }
)

EXPECTED_REPOS = frozenset({"ceph", "r2"})
FORBIDDEN_REPO_NAMES = frozenset({"minio", "MinIO"})
EXPECTED_BUCKET = "kopiur"

KOPIUR_PACKAGE_NAMES = frozenset(
    {
        "ghcr.io/home-operations/charts/kopiur",
        "ghcr.io/home-operations/kopiur-controller",
        "ghcr.io/home-operations/kopiur-webhook",
        "ghcr.io/home-operations/kopiur-mover",
        "home-operations/kopiur",
    }
)


class Failure(Exception):
    pass


def _which(name: str) -> str | None:
    from shutil import which

    return which(name)


def kustomize_build(path: Path) -> list[dict[str, Any]]:
    """Render a kustomization the way Flux would (kustomize build)."""
    kustomize = _which("kustomize")
    cmd: list[str]
    if kustomize:
        cmd = [kustomize, "build", str(path)]
    else:
        kubectl = _which("kubectl")
        if not kubectl:
            raise Failure("neither kustomize nor kubectl is on PATH")
        cmd = [kubectl, "kustomize", str(path)]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise Failure(
            f"{' '.join(cmd)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    if not docs:
        raise Failure(f"kustomize build of {path} produced no documents")
    return docs


def load_multi(path: Path) -> list[dict[str, Any]]:
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    if not docs:
        raise Failure(f"{path} produced no documents")
    return docs


def by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == kind]


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def parse_json5_comments(text: str) -> Any:
    """Minimal JSON5 subset parser for our renovate autoMerge file.

    Handles the subset we actually ship: // line comments (never inside
    strings - bare `https://` must survive), unquoted keys (including
    $schema), single-line strings, and trailing commas. Not a general
    JSON5 engine.
    """

    def strip_line_comments_outside_strings(src: str) -> str:
        out: list[str] = []
        i = 0
        n = len(src)
        in_str = False
        while i < n:
            ch = src[i]
            if in_str:
                out.append(ch)
                if ch == "\\" and i + 1 < n:
                    out.append(src[i + 1])
                    i += 2
                    continue
                if ch == '"':
                    in_str = False
                i += 1
                continue
            if ch == '"':
                in_str = True
                out.append(ch)
                i += 1
                continue
            if ch == "/" and i + 1 < n and src[i + 1] == "/":
                # Skip through end of line; keep the newline itself.
                while i < n and src[i] not in "\n\r":
                    i += 1
                continue
            out.append(ch)
            i += 1
        return "".join(out)

    no_line = strip_line_comments_outside_strings(text)
    # Quote bare identifier keys: { packageRules: ... } / $schema:
    quoted = re.sub(
        r"([{\[,]\s*)([A-Za-z_$][\w$]*)\s*:",
        r'\1"\2":',
        no_line,
    )
    no_trail = re.sub(r",(\s*[}\]])", r"\1", quoted)
    try:
        return json.loads(no_trail)
    except json.JSONDecodeError as e:
        raise Failure(f"failed to parse autoMerge.json5 subset as JSON: {e}") from e


def test_kustomize_app_operator_contract(app_docs: list[dict[str, Any]]) -> None:
    oci = by_kind(app_docs, "OCIRepository")
    require(len(oci) == 1, f"expected 1 OCIRepository, got {len(oci)}")
    ref = oci[0].get("spec", {}).get("ref", {})
    require(
        "tag" in ref and "semver" not in ref and "digest" not in ref,
        f"OCIRepository must pin an exact tag only, got ref={ref!r}",
    )
    tag = ref["tag"]
    require(
        isinstance(tag, str) and bool(re.fullmatch(r"\d+\.\d+\.\d+", tag)),
        f"OCIRepository tag must be exact x.y.z, got {tag!r}",
    )
    url = oci[0].get("spec", {}).get("url", "")
    require(
        url == "oci://ghcr.io/home-operations/charts/kopiur",
        f"unexpected chart url {url!r}",
    )

    hrs = by_kind(app_docs, "HelmRelease")
    require(len(hrs) == 1, f"expected 1 HelmRelease, got {len(hrs)}")
    hr = hrs[0]
    install_crds = hr.get("spec", {}).get("install", {}).get("crds")
    upgrade_crds = hr.get("spec", {}).get("upgrade", {}).get("crds")
    require(
        install_crds == "CreateReplace",
        f"install.crds must be CreateReplace, got {install_crds!r}",
    )
    require(
        upgrade_crds == "CreateReplace",
        f"upgrade.crds must be CreateReplace (Helm crds/ is install-only), got {upgrade_crds!r}",
    )
    values = hr.get("spec", {}).get("values", {})
    require(
        values.get("installScope") == "cluster",
        "installScope must be cluster so ClusterRepository is reconciled",
    )
    mon = values.get("monitoring", {})
    require(mon.get("serviceMonitor", {}).get("enabled") is True, "serviceMonitor off")
    require(mon.get("prometheusRule", {}).get("enabled") is True, "prometheusRule off")
    require(mon.get("dashboards", {}).get("enabled") is True, "dashboards off")
    require(
        values.get("webhook", {}).get("serviceMonitor", {}).get("enabled") is True,
        "webhook.serviceMonitor must be enabled",
    )

    # Our own absent() alerts (chart's 12 have no operator-gone equivalent).
    rules = by_kind(app_docs, "PrometheusRule")
    require(len(rules) >= 1, "missing kopiur-absent PrometheusRule")
    alert_names: set[str] = set()
    exprs: list[str] = []
    for pr in rules:
        for group in pr.get("spec", {}).get("groups", []) or []:
            for rule in group.get("rules", []) or []:
                if "alert" in rule:
                    alert_names.add(rule["alert"])
                    exprs.append(rule.get("expr", ""))
    require(
        "KopiurComponentAbsent" in alert_names,
        f"missing KopiurComponentAbsent, have {sorted(alert_names)}",
    )
    require(
        "KopiurWebhookAbsent" in alert_names,
        f"missing KopiurWebhookAbsent, have {sorted(alert_names)}",
    )
    joined = "\n".join(exprs)
    require(
        'absent(up{job="kopiur-controller-metrics"})' in joined,
        "controller absent() expr missing",
    )
    require(
        'absent(up{job="kopiur-webhook"})' in joined,
        "webhook absent() expr missing",
    )

    # Ceph bucket is an OBC named kopiur (new bucket), not a hand-made path and
    # not the live volsync bucket.
    obcs = by_kind(app_docs, "ObjectBucketClaim")
    require(len(obcs) == 1, f"expected 1 ObjectBucketClaim, got {len(obcs)}")
    obc = obcs[0]
    require(obc.get("metadata", {}).get("name") == "kopiur", "OBC name must be kopiur")
    require(
        obc.get("spec", {}).get("bucketName") == EXPECTED_BUCKET,
        f"OBC bucketName must be {EXPECTED_BUCKET!r}",
    )
    require(
        obc.get("spec", {}).get("storageClassName") == "ceph-bucket",
        "OBC must use ceph-bucket StorageClass",
    )

    pushes = by_kind(app_docs, "PushSecret")
    require(len(pushes) == 1, f"expected 1 PushSecret, got {len(pushes)}")
    require(
        pushes[0].get("spec", {}).get("deletionPolicy") == "None",
        "PushSecret deletionPolicy must be None so prune cannot delete the 1P record",
    )
    remote_keys = {
        m.get("match", {}).get("remoteRef", {}).get("remoteKey")
        for m in pushes[0].get("spec", {}).get("data", []) or []
    }
    require(
        remote_keys == {"kopiur-ceph-bucket"},
        f"PushSecret must write kopiur-ceph-bucket only, got {remote_keys}",
    )

    # Substitution ExternalSecret: explicit property, not dataFrom.extract.
    esubs = [
        d
        for d in by_kind(app_docs, "ExternalSecret")
        if d.get("metadata", {}).get("name") == "kopiur-substitutions"
    ]
    require(len(esubs) == 1, "missing kopiur-substitutions ExternalSecret")
    es = esubs[0]["spec"]
    require(
        "dataFrom" not in es,
        "kopiur-substitutions must not use dataFrom.extract (missing keys go silent)",
    )
    data = es.get("data") or []
    require(len(data) >= 1, "kopiur-substitutions has no data entries")
    for entry in data:
        prop = (entry.get("remoteRef") or {}).get("property")
        require(
            isinstance(prop, str) and prop,
            f"substitution entry missing explicit remoteRef.property: {entry!r}",
        )
        require(
            (entry.get("remoteRef") or {}).get("key") == "kopiur-r2",
            "substitutions must read kopiur-r2, never volsync-template",
        )


def test_kustomize_repository_contract(repo_docs: list[dict[str, Any]]) -> None:
    kinds = {d.get("kind") for d in repo_docs}
    overlap = kinds & STAGE0_FORBIDDEN_KINDS
    require(not overlap, f"Stage 0 repository build must not emit {sorted(overlap)}")

    repos = by_kind(repo_docs, "ClusterRepository")
    names = {r.get("metadata", {}).get("name") for r in repos}
    require(
        names == EXPECTED_REPOS,
        f"ClusterRepository set must be exactly {sorted(EXPECTED_REPOS)}, got {sorted(names)}",
    )
    require(
        not (names & FORBIDDEN_REPO_NAMES),
        f"MinIO must be absent from Stage 0, found {names & FORBIDDEN_REPO_NAMES}",
    )

    for repo in repos:
        name = repo["metadata"]["name"]
        spec = repo.get("spec") or {}
        require(
            (spec.get("allowedNamespaces") or {}).get("all") is True,
            f"{name}: allowedNamespaces.all must be true",
        )
        s3 = (spec.get("backend") or {}).get("s3") or {}
        require(
            s3.get("bucket") == EXPECTED_BUCKET,
            f"{name}: bucket must be new dedicated {EXPECTED_BUCKET!r}, got {s3.get('bucket')!r}",
        )
        # Never point at a volsync/<app> restic path.
        for field in ("prefix", "path"):
            require(
                not s3.get(field),
                f"{name}: must not set s3.{field} (would risk a VolSync restic path)",
            )
        dp = spec.get("deletionProtection") or {}
        require(
            "threshold" in dp and isinstance(dp["threshold"], int) and dp["threshold"] > 0,
            f"{name}: deletionProtection.threshold must be pinned explicitly > 0",
        )
        require(
            spec.get("onNamespaceDelete") == "Orphan",
            f"{name}: onNamespaceDelete must be Orphan (ADR-0005), got {spec.get('onNamespaceDelete')!r}",
        )
        require(
            (spec.get("create") or {}).get("enabled") is True,
            f"{name}: create.enabled must be true (bootstrap empty repo)",
        )
        require(spec.get("mode") == "ReadWrite", f"{name}: mode must be ReadWrite")
        enc = (spec.get("encryption") or {}).get("passwordSecretRef") or {}
        require(
            enc.get("key") == "KOPIA_PASSWORD",
            f"{name}: encryption password key must be KOPIA_PASSWORD",
        )
        require(
            enc.get("namespace") == "system",
            f"{name}: password secret must live in system",
        )

    # Per-destination ExternalSecrets, explicit properties, no volsync-template.
    secrets = by_kind(repo_docs, "ExternalSecret")
    secret_names = {s.get("metadata", {}).get("name") for s in secrets}
    require(
        secret_names == {"kopiur-ceph", "kopiur-r2"},
        f"expected ExternalSecrets kopiur-ceph + kopiur-r2, got {sorted(secret_names)}",
    )
    for es in secrets:
        name = es["metadata"]["name"]
        spec = es.get("spec") or {}
        require(
            "dataFrom" not in spec,
            f"{name}: must not use dataFrom.extract (empty-string credential trap)",
        )
        data = spec.get("data") or []
        require(data, f"{name}: no data entries")
        keys_read = set()
        for entry in data:
            rr = entry.get("remoteRef") or {}
            require(
                isinstance(rr.get("property"), str) and rr["property"],
                f"{name}: every entry needs explicit remoteRef.property, got {entry!r}",
            )
            keys_read.add(rr.get("key"))
            # No inline credential values anywhere in the object.
            for v in entry.values():
                if isinstance(v, str):
                    require(
                        not re.search(r"(?i)(sk-|AKIA|password\s*=)", v),
                        f"{name}: looks like an embedded credential value",
                    )
        require(
            keys_read == {name},
            f"{name}: must read only its own 1Password item {name!r}, read {keys_read}",
        )
        # Never volsync-template.
        require(
            "volsync-template" not in keys_read,
            f"{name}: must not read volsync-template",
        )

    # Ceph password-only; R2 carries password + access keys (S3 keys not on ceph ES).
    ceph_es = next(s for s in secrets if s["metadata"]["name"] == "kopiur-ceph")
    ceph_secret_keys = {e["secretKey"] for e in ceph_es["spec"]["data"]}
    require(
        ceph_secret_keys == {"KOPIA_PASSWORD"},
        f"kopiur-ceph must carry KOPIA_PASSWORD only (OBC owns S3 keys), got {ceph_secret_keys}",
    )
    r2_es = next(s for s in secrets if s["metadata"]["name"] == "kopiur-r2")
    r2_secret_keys = {e["secretKey"] for e in r2_es["spec"]["data"]}
    require(
        r2_secret_keys
        == {"KOPIA_PASSWORD", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"},
        f"kopiur-r2 secret keys unexpected: {r2_secret_keys}",
    )


def test_no_stage1_anywhere_under_kopiur() -> None:
    """Walk the kopiur tree and ensure no Stage 1 kinds sneak in as raw YAML."""
    found: list[str] = []
    for path in KOPIUR_BASE.rglob("*.yaml"):
        for doc in yaml.safe_load_all(path.read_text()):
            if not doc:
                continue
            kind = doc.get("kind")
            if kind in STAGE0_FORBIDDEN_KINDS:
                found.append(f"{path.relative_to(ROOT)}:{kind}")
    require(not found, f"Stage 0 tree contains forbidden kinds: {found}")


def test_overlay_healthchecks_and_wiring() -> None:
    docs = load_multi(KOPIUR_OVERLAY)
    require(len(docs) == 2, f"overlay must define 2 Kustomizations, got {len(docs)}")
    by_name = {d["metadata"]["name"]: d for d in docs}
    require(
        set(by_name) == {"kopiur", "kopiur-repository"},
        f"overlay names must be kopiur + kopiur-repository, got {sorted(by_name)}",
    )

    op = by_name["kopiur"]["spec"]
    require(op.get("wait") is False, "kopiur overlay wait must be false")
    require(op.get("prune") is True, "kopiur prune stays true (deletionProtection is the backstop)")
    require(
        op.get("path") == "./kubernetes/apps/base/system/kopiur/app",
        f"unexpected kopiur path {op.get('path')!r}",
    )
    require(op.get("targetNamespace") == "system", "kopiur targetNamespace must be system")
    hc = op.get("healthChecks") or []
    hc_set = {(h.get("kind"), h.get("name"), h.get("namespace")) for h in hc}
    require(
        ("Deployment", "kopiur-controller", "system") in hc_set,
        f"healthChecks must include Deployment/kopiur-controller, got {hc_set}",
    )
    require(
        ("Deployment", "kopiur-webhook", "system") in hc_set,
        f"healthChecks must include Deployment/kopiur-webhook, got {hc_set}",
    )
    # Never the HelmRelease (Ready stays True through crashloop).
    require(
        all(h.get("kind") != "HelmRelease" for h in hc),
        "healthChecks must not target HelmRelease",
    )

    repo = by_name["kopiur-repository"]["spec"]
    require(repo.get("wait") is False, "kopiur-repository wait must be false")
    require(
        repo.get("path") == "./kubernetes/apps/base/system/kopiur/repository",
        f"unexpected repository path {repo.get('path')!r}",
    )
    deps = {(d.get("name"), d.get("namespace")) for d in (repo.get("dependsOn") or [])}
    require(
        ("kopiur", "system") in deps,
        f"kopiur-repository must dependOn kopiur (webhook failurePolicy:Fail), got {deps}",
    )
    # substituteFrom must pull kopiur-substitutions (R2 endpoint) + cluster-secrets.
    sub_from = {
        (s.get("kind"), s.get("name"))
        for s in (repo.get("postBuild") or {}).get("substituteFrom") or []
    }
    require(
        ("Secret", "kopiur-substitutions") in sub_from,
        f"repository substituteFrom must include kopiur-substitutions, got {sub_from}",
    )

    # Wired into the system namespace overlay.
    sys_kust = yaml.safe_load(SYSTEM_OVERLAY_KUST.read_text())
    resources = sys_kust.get("resources") or []
    require(
        "./kopiur.yaml" in resources,
        f"system overlay kustomization must list ./kopiur.yaml, got {resources}",
    )


def test_renovate_automerge_exclusion() -> None:
    """Simulate Renovate last-match-wins against kopiur package names."""
    cfg = parse_json5_comments(AUTOMERGE.read_text())
    rules = cfg.get("packageRules") or []
    require(rules, "autoMerge.json5 has no packageRules")

    def rule_matches(rule: dict[str, Any], pkg: str) -> bool:
        names = rule.get("matchPackageNames")
        if names is not None:
            return pkg in names
        # Blanket rules (no matchPackageNames) match every package subject to
        # their other matchers; for automerge resolution we only care that a
        # later kopiur-specific rule can override them.
        return "matchPackageNames" not in rule

    for pkg in sorted(KOPIUR_PACKAGE_NAMES):
        winning_automerge: bool | None = None
        winning_index = -1
        for idx, rule in enumerate(rules):
            if rule_matches(rule, pkg) and "automerge" in rule:
                winning_automerge = bool(rule["automerge"])
                winning_index = idx
        require(
            winning_automerge is False,
            f"{pkg}: final automerge winner must be false (got {winning_automerge} from rule {winning_index})",
        )
        require(
            winning_index == len(rules) - 1,
            f"{pkg}: kopiur exclusion must be the LAST packageRule so it wins "
            f"(won at index {winning_index}, last is {len(rules) - 1})",
        )

    last = rules[-1]
    require(
        set(last.get("matchPackageNames") or []) >= KOPIUR_PACKAGE_NAMES,
        f"last rule must cover all kopiur packages, got {last.get('matchPackageNames')}",
    )


def test_volsync_tree_not_in_kopiur_builds(
    app_docs: list[dict[str, Any]], repo_docs: list[dict[str, Any]]
) -> None:
    """Stage 0 builds must not emit or rename any VolSync workload objects."""
    for docs, label in ((app_docs, "app"), (repo_docs, "repo")):
        for d in docs:
            kind = d.get("kind")
            require(
                kind not in STAGE0_FORBIDDEN_KINDS,
                f"{label} build emitted forbidden kind {kind}",
            )
            name = (d.get("metadata") or {}).get("name", "")
            require(
                not str(name).startswith("volsync"),
                f"{label} build must not emit volsync-named object {kind}/{name}",
            )


def test_no_embedded_credentials(app_docs: list[dict[str, Any]], repo_docs: list[dict[str, Any]]) -> None:
    """Walk every string leaf in rendered docs; nothing looks like a live secret."""
    suspicious = re.compile(
        r"(?i)\b(sk-ant-|sk-live-|AKIA[0-9A-Z]{16}|BEGIN (RSA |OPENSSH )?PRIVATE KEY)\b"
    )

    def walk(obj: Any, path: str, hits: list[str]) -> None:
        if isinstance(obj, dict):
            for k, v in obj.items():
                walk(v, f"{path}.{k}", hits)
        elif isinstance(obj, list):
            for i, v in enumerate(obj):
                walk(v, f"{path}[{i}]", hits)
        elif isinstance(obj, str):
            if suspicious.search(obj):
                hits.append(f"{path}={obj[:32]}...")

    hits: list[str] = []
    for docs in (app_docs, repo_docs):
        for d in docs:
            walk(d, d.get("kind", "?"), hits)
    require(not hits, f"rendered manifests appear to embed credentials: {hits}")


def main() -> int:
    tests = []
    failures: list[str] = []

    def run(name: str, fn) -> None:
        tests.append(name)
        try:
            fn()
            print(f"[PASS] {name}")
        except Failure as e:
            print(f"[FAIL] {name}: {e}")
            failures.append(f"{name}: {e}")
        except Exception as e:  # noqa: BLE001 - surface unexpected errors as failures
            print(f"[FAIL] {name}: unexpected {type(e).__name__}: {e}")
            failures.append(f"{name}: {e}")

    try:
        app_docs = kustomize_build(KOPIUR_APP)
        repo_docs = kustomize_build(KOPIUR_REPO)
    except Failure as e:
        print(f"[FAIL] kustomize build: {e}")
        print("Summary: 0 passed, 1 failed")
        return 1

    run("operator_contract", lambda: test_kustomize_app_operator_contract(app_docs))
    run("repository_contract", lambda: test_kustomize_repository_contract(repo_docs))
    run("no_stage1_in_tree", test_no_stage1_anywhere_under_kopiur)
    run("overlay_healthchecks", test_overlay_healthchecks_and_wiring)
    run("renovate_automerge_exclusion", test_renovate_automerge_exclusion)
    run(
        "no_volsync_objects_in_builds",
        lambda: test_volsync_tree_not_in_kopiur_builds(app_docs, repo_docs),
    )
    run(
        "no_embedded_credentials",
        lambda: test_no_embedded_credentials(app_docs, repo_docs),
    )

    # VolSync paths must still exist and be loadable (Stage 0 did not delete them).
    def volsync_still_present() -> None:
        missing = [str(p.relative_to(ROOT)) for p in VOLSYNC_TREE if not p.exists()]
        require(not missing, f"VolSync tree paths missing: {missing}")
        # Spot-check the operator HelmRelease still has its own CreateReplace CRDs
        # and was not rewritten into a kopiur reference.
        vs_hr = ROOT / "kubernetes/apps/base/system/volsync/app/helmrelease.yaml"
        docs = load_multi(vs_hr)
        hr = next(d for d in docs if d.get("kind") == "HelmRelease")
        require(hr["metadata"]["name"] == "volsync", "volsync HelmRelease renamed")
        require(
            hr["spec"]["chartRef"]["name"] == "volsync",
            "volsync HelmRelease chartRef drifted",
        )

    run("volsync_tree_intact", volsync_still_present)

    passed = len(tests) - len(failures)
    print(f"Summary: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
