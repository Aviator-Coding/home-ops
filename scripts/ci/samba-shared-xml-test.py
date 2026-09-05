#!/usr/bin/env python3
"""Semantic regression test for the LAN SMB shared-xml share.

Pins the captain decision (2026-09-04) that stood up a LAN-only Samba share on
a new shared CephFS RWX claim so BOTH the captain's Mac and the hermes agent
can read/write the ~300k-file XML corpus. The dataset itself is loaded later;
this test pins the empty-share GitOps contract:

  1. Standalone `shared-xml` PVC: 100Gi, ReadWriteMany, ceph-filesystem-rwx
     (csi-rwx), NOT ceph-filesystem / default `csi` (Tentacle corruption path).
  2. No backup engine on the claim (no kopiur/volsync Component on ai-pvc or
     samba overlays) - Mac is authoritative.
  3. Samba app-template Deployment mounts that claim at /shared, runs
     ghcr.io/dockur/samba with a verbatim smb.conf ConfigMap, ExternalSecret
     password at /run/secrets/pass, and a LAN-only LoadBalancer on 10.50.0.55
     port 445 (no HTTPRoute / envoy).
  4. Ownership contract in the rendered smb.conf: force user/group hermes/smb
     (UID/GID 10000 via env), create/directory masks, fruit:nfs_aces = no,
     fruit:resource = file (deliberate), SMB3-only.
  5. SETGID invariant: initContainer chowns 10000:10000 + chmod 2770; app
     postStart waits for `pgrep -x smbd` then re-asserts 2770. A shell model of
     dockur/samba's empty-share `chmod 0770` proves the postStart is what holds
     setgid after the entrypoint, and a live filesystem probe proves 2770
     carries S_ISGID while 0770 does not (OnRootMismatch skip condition).
  6. hermes HelmRelease mounts the same claim READ-WRITE at /opt/xml on the
     `app` container only; `/opt/data` stays on the hermes claim; hermes Flux
     Kustomization dependsOn `ai-pvc` first.

Live Mac mount_smbfs ownership drills and cluster apply are outside this
GitOps pin (no kubeconfig in CI). This test renders the real consumers
(kustomize + helm template of app-template 5.1.0) and asserts the parsed
objects, plus a filesystem model of the setgid race the postStart closes.
"""

from __future__ import annotations

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from configparser import ConfigParser
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[2]
PVC_DIR = REPO / "kubernetes/apps/base/ai/pvc/app"
SAMBA_DIR = REPO / "kubernetes/apps/base/ai/samba/app"
HERMES_HR = REPO / "kubernetes/apps/base/ai/hermes/app/helmrelease.yaml"
AI_MAIN = REPO / "kubernetes/apps/main/ai"
PVC_OVERLAY = AI_MAIN / "pvc.yaml"
SAMBA_OVERLAY = AI_MAIN / "samba.yaml"
HERMES_OVERLAY = AI_MAIN / "hermes.yaml"
AI_KUSTOMIZATION = AI_MAIN / "kustomization.yaml"

APP_TEMPLATE_VERSION = "5.1.0"
APP_TEMPLATE_OCI = "oci://ghcr.io/bjw-s-labs/helm/app-template"
EXPECTED_LB_IP = "10.50.0.55"
EXPECTED_UID = "10000"
EXPECTED_GID = "10000"
CLAIM = "shared-xml"
STORAGE_CLASS = "ceph-filesystem-rwx"
CAPACITY = "100Gi"
SAMBA_IMAGE = "ghcr.io/dockur/samba"
SAMBA_TAG = "4.23.10"

# IPs the captain named as taken (must not be the samba pick).
TAKEN_IPS = {
    "10.50.0.21",
    "10.50.0.22",
    "10.50.0.23",
    "10.50.0.26",
    "10.50.0.27",
    "10.50.0.28",
    "10.50.0.29",
    "10.50.0.30",
    "10.50.0.51",
    "10.50.0.52",
    "10.50.0.54",
    "10.50.0.121",
}


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


def load_docs(path: Path) -> list[dict[str, Any]]:
    docs = [d for d in yaml.safe_load_all(path.read_text()) if d]
    require(docs, f"{path} produced no YAML documents")
    return docs


def _which(*names: str) -> str | None:
    for name in names:
        found = shutil.which(name)
        if found:
            return found
    # mise install layout fallbacks (shim may exist without a default version)
    home = Path.home() / ".local/share/mise/installs"
    candidates = [
        home / "aqua-kubernetes-sigs-kustomize",
        home / "kustomize",
        home / "helm",
        home / "aqua-helm-helm",
    ]
    for root in candidates:
        if not root.is_dir():
            continue
        for path in sorted(root.rglob(names[0])):
            if path.is_file() and os.access(path, os.X_OK):
                return str(path)
    return None


def kustomize_build(path: Path) -> list[dict[str, Any]]:
    binary = _which("kustomize")
    if binary:
        cmd = [binary, "build", str(path)]
    else:
        kubectl = _which("kubectl")
        require(kubectl is not None, "neither kustomize nor kubectl is available")
        cmd = [kubectl, "kustomize", str(path), "--load-restrictor", "LoadRestrictionsNone"]
    try:
        out = subprocess.run(cmd, check=True, capture_output=True, text=True).stdout
    except subprocess.CalledProcessError as exc:
        raise Failure(f"kustomize build failed for {path}: {exc.stderr or exc}") from exc
    docs = [d for d in yaml.safe_load_all(out) if d]
    require(docs, f"kustomize build of {path} produced no documents")
    return docs


_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


def envsubst(text: str, env: dict[str, str]) -> str:
    def repl(match: re.Match[str]) -> str:
        key = match.group(1)
        default = match.group(2)
        if key in env:
            return env[key]
        if default is not None:
            return default
        return match.group(0)

    return _ENV_PATTERN.sub(repl, text)


def helm_template(release: str, values: dict[str, Any], namespace: str = "ai") -> list[dict[str, Any]]:
    helm = _which("helm")
    require(helm is not None, "helm is not available")
    with tempfile.TemporaryDirectory(prefix="samba-shared-xml-") as tmp:
        values_path = Path(tmp) / "values.yaml"
        values_path.write_text(yaml.safe_dump(values))
        chart_tg = Path(tmp) / f"app-template-{APP_TEMPLATE_VERSION}.tgz"
        pull = subprocess.run(
            [
                helm,
                "pull",
                APP_TEMPLATE_OCI,
                "--version",
                APP_TEMPLATE_VERSION,
                "--destination",
                tmp,
            ],
            capture_output=True,
            text=True,
        )
        require(pull.returncode == 0, f"helm pull app-template failed: {pull.stderr.strip()}")
        require(chart_tg.is_file(), f"helm pull did not produce {chart_tg.name}")
        templated = subprocess.run(
            [
                helm,
                "template",
                release,
                str(chart_tg),
                "-f",
                str(values_path),
                "-n",
                namespace,
            ],
            capture_output=True,
            text=True,
        )
        require(
            templated.returncode == 0,
            f"helm template {release} failed: {templated.stderr.strip()}",
        )
        docs = [d for d in yaml.safe_load_all(templated.stdout) if d]
        require(docs, f"helm template {release} produced no documents")
        return docs


def hr_values(path: Path, env: dict[str, str] | None = None) -> dict[str, Any]:
    text = path.read_text()
    if env:
        text = envsubst(text, env)
    docs = [d for d in yaml.safe_load_all(text) if d]
    hrs = [d for d in docs if d.get("kind") == "HelmRelease"]
    require(len(hrs) == 1, f"{path} must contain exactly one HelmRelease")
    values = (hrs[0].get("spec") or {}).get("values") or {}
    require(isinstance(values, dict) and values, f"{path} HelmRelease has empty values")
    return values


def parse_smb_conf(text: str) -> ConfigParser:
    # Samba conf is INI-like. Use '=' as the ONLY delimiter: fruit:metadata and
    # friends contain colons, and ConfigParser's default ':' delimiter would
    # split them into duplicate bare 'fruit' keys.
    parser = ConfigParser(interpolation=None, delimiters=("=",))
    parser.optionxform = str  # type: ignore[method-assign]  # preserve case
    cleaned_lines: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith(";"):
            continue
        cleaned_lines.append(stripped)
    parser.read_string("\n".join(cleaned_lines) + "\n")
    return parser


def by_kind(docs: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    return [d for d in docs if d.get("kind") == kind]


def deployment_pod(docs: list[dict[str, Any]]) -> dict[str, Any]:
    deps = by_kind(docs, "Deployment")
    require(len(deps) == 1, f"expected one Deployment, got {len(deps)}")
    return deps[0]["spec"]["template"]["spec"]


def container_by_name(pod: dict[str, Any], name: str) -> dict[str, Any]:
    for c in list(pod.get("initContainers") or []) + list(pod.get("containers") or []):
        if c.get("name") == name:
            return c
    raise Failure(f"container {name!r} not found in pod")


def mounts_for(container: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for m in container.get("volumeMounts") or []:
        out[m["mountPath"]] = m
    return out


def pvc_volume_claims(pod: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for v in pod.get("volumes") or []:
        claim = (v.get("persistentVolumeClaim") or {}).get("claimName")
        if claim:
            out[v["name"]] = claim
    return out


def overlay_doc(path: Path) -> dict[str, Any]:
    docs = load_docs(path)
    require(len(docs) == 1 and docs[0].get("kind") == "Kustomization", f"{path} must be one Flux Kustomization")
    return docs[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_shared_xml_pvc_contract() -> None:
    docs = kustomize_build(PVC_DIR)
    pvcs = by_kind(docs, "PersistentVolumeClaim")
    require(len(pvcs) == 1, f"ai/pvc must render exactly one PVC, got {len(pvcs)}")
    pvc = pvcs[0]
    require(pvc["metadata"]["name"] == CLAIM, f"PVC name must be {CLAIM}")
    spec = pvc["spec"]
    require(spec.get("storageClassName") == STORAGE_CLASS, f"storageClassName must be {STORAGE_CLASS}")
    require(spec.get("accessModes") == ["ReadWriteMany"], "accessModes must be [ReadWriteMany]")
    storage = ((spec.get("resources") or {}).get("requests") or {}).get("storage")
    require(storage == CAPACITY, f"storage request must be {CAPACITY}, got {storage!r}")
    # Standalone directory shape (not embedded in an app HelmRelease).
    require((PVC_DIR / "shared-xml.yaml").is_file(), "shared-xml.yaml must live under ai/pvc/app/")
    require(not (SAMBA_DIR / "shared-xml.yaml").exists(), "PVC must not be embedded under samba/")
    require(not (REPO / "kubernetes/apps/base/ai/hermes/app/shared-xml.yaml").exists(), "PVC must not be under hermes/")


def test_no_backup_on_shared_xml() -> None:
    for path, label in ((PVC_OVERLAY, "ai-pvc"), (SAMBA_OVERLAY, "samba")):
        doc = overlay_doc(path)
        components = doc.get("spec", {}).get("components") or []
        joined = " ".join(str(c) for c in components)
        require(
            "kopiur" not in joined and "volsync" not in joined,
            f"{label} overlay must not attach kopiur/volsync (no backup on shared-xml); got {components!r}",
        )
        # Comment on the PVC file records the deliberate omission.
    pvc_text = (PVC_DIR / "shared-xml.yaml").read_text()
    require(
        "DELIBERATELY NOT BACKED UP" in pvc_text or "not backed up" in pvc_text.lower(),
        "shared-xml PVC must record the no-backup decision in a comment",
    )


def test_samba_overlay_wiring() -> None:
    main = load_docs(AI_KUSTOMIZATION)[0]
    resources = main.get("resources") or []
    require("./samba.yaml" in resources, "ai/kustomization.yaml must list ./samba.yaml")
    require("./pvc.yaml" in resources, "ai/kustomization.yaml must list ./pvc.yaml")

    pvc = overlay_doc(PVC_OVERLAY)
    require(pvc["metadata"]["name"] == "ai-pvc", "PVC overlay name must be ai-pvc")
    require(pvc["metadata"]["namespace"] == "ai", "PVC overlay namespace must be ai")
    deps = pvc["spec"].get("dependsOn") or []
    require(
        any(d.get("name") == "rook-ceph-cluster" and d.get("namespace") == "rook-ceph" for d in deps),
        "ai-pvc must dependOn rook-ceph-cluster",
    )

    samba = overlay_doc(SAMBA_OVERLAY)
    require(samba["metadata"]["name"] == "samba", "samba overlay name")
    require(samba["metadata"]["namespace"] == "ai", "samba overlay namespace")
    sdeps = samba["spec"].get("dependsOn") or []
    require(
        any(d.get("name") == "ai-pvc" and d.get("namespace") == "ai" for d in sdeps),
        "samba must dependOn ai-pvc",
    )
    require(
        any(d.get("name") == "onepassword-store" for d in sdeps),
        "samba must dependOn onepassword-store for the ExternalSecret",
    )
    health = samba["spec"].get("healthChecks") or []
    require(
        any(h.get("kind") == "Deployment" and h.get("name") == "samba" for h in health),
        "samba healthChecks must target the Deployment workload, not the HelmRelease",
    )


def test_hermes_depends_on_ai_pvc_first() -> None:
    hermes = overlay_doc(HERMES_OVERLAY)
    deps = hermes["spec"].get("dependsOn") or []
    require(deps, "hermes dependsOn must be non-empty")
    require(
        deps[0].get("name") == "ai-pvc" and deps[0].get("namespace") == "ai",
        f"ai-pvc must lead hermes dependsOn (got {deps[0]!r})",
    )


def test_samba_rendered_workload_and_service() -> None:
    built = kustomize_build(SAMBA_DIR)
    # ExternalSecret: password file contract, no plaintext.
    secrets = by_kind(built, "ExternalSecret")
    require(len(secrets) == 1, "samba must render one ExternalSecret")
    es = secrets[0]
    require(es["metadata"]["name"] == "samba", "ExternalSecret name")
    tmpl = ((es.get("spec") or {}).get("target") or {}).get("template") or {}
    data = tmpl.get("data") or {}
    require("pass" in data, "ExternalSecret must template a `pass` key for /run/secrets/pass")
    require(
        "SMB_PASSWORD" in str(data.get("pass")),
        "pass template must pull SMB_PASSWORD from 1Password",
    )
    full = yaml.safe_dump(built)
    require("ref+op://" not in full, "no vals/op refs in in-cluster manifests")
    # No credential literals: ExternalSecret must only carry the Go template
    # marker, never a concrete password string in any rendered object.
    require(
        re.search(r"\bSMB_PASSWORD\b", full) is not None,
        "ExternalSecret must reference SMB_PASSWORD",
    )
    require(
        re.search(r"pass:\s*['\"]?[A-Za-z0-9+/=_-]{12,}['\"]?\s*$", full, re.M) is None,
        "no concrete pass: value may appear in rendered manifests",
    )
    # dockur reads the secret file; PASS must not be an env value on the HR.
    hr_docs = [d for d in built if d.get("kind") == "HelmRelease"]
    require(len(hr_docs) == 1, "one HelmRelease in samba kustomize build")
    hr_dump = yaml.safe_dump(hr_docs[0])
    require(
        re.search(r"-\s*name:\s*PASS\b", hr_dump) is None
        and "PASS:" not in hr_dump.split("env:", 1)[-1][:500] if "env:" in hr_dump else True,
        "PASS must not be injected as container env (file mount only)",
    )

    cms = by_kind(built, "ConfigMap")
    require(any(c["metadata"]["name"] == "samba-configmap" for c in cms), "samba-configmap must render")
    cm = next(c for c in cms if c["metadata"]["name"] == "samba-configmap")
    smb_text = (cm.get("data") or {}).get("smb.conf")
    require(isinstance(smb_text, str) and smb_text.strip(), "ConfigMap must carry smb.conf")
    conf = parse_smb_conf(smb_text)

    require(conf.has_section("global"), "smb.conf must have [global]")
    require(conf.has_section("xml"), "smb.conf must have [xml] share")
    g, x = conf["global"], conf["xml"]
    require(g.get("disable netbios") == "yes", "NetBIOS must be disabled (445 only)")
    require(g.get("smb ports") == "445", "smb ports must be 445")
    require(g.get("server min protocol") == "SMB3", "SMB3 minimum")
    require(g.get("netbios name") == "HOMELAB-XML", "explicit short netbios name")
    require("fruit" in g.get("vfs objects", ""), "vfs_fruit for macOS interop")
    require(g.get("fruit:metadata") == "stream", "AppleDouble metadata in xattr stream")
    require(g.get("fruit:resource") == "file", "fruit:resource=file is the measured deliberate choice")
    require(g.get("fruit:nfs_aces") == "no", "fruit:nfs_aces=no is load-bearing for ownership")
    require(x.get("path") == "/shared", "[xml] path must be /shared")
    require(x.get("read only") == "no", "[xml] must be writable")
    require(x.get("force user") == "hermes", "force user = hermes")
    require(x.get("force group") == "smb", "force group = smb")
    require(x.get("create mask") == "0664" and x.get("force create mode") == "0664", "file mode contract")
    require(x.get("directory mask") == "0775" and x.get("force directory mode") == "0775", "dir mode contract")
    require(x.get("inherit permissions") == "no", "inherit permissions must be off")
    require(x.get("valid users") == "hermes", "valid users = hermes")
    require(x.get("guest ok") == "no", "no guest access")

    # HelmRelease values → real Deployment/Service via app-template.
    values = hr_values(
        SAMBA_DIR / "helmrelease.yaml",
        env={"CONFIG_TIMEZONE": "America/New_York"},
    )
    rendered = helm_template("samba", values, namespace="ai")
    require(not by_kind(rendered, "HTTPRoute"), "samba must not render an HTTPRoute")
    require(not by_kind(rendered, "Gateway"), "samba must not render a Gateway")

    svcs = by_kind(rendered, "Service")
    require(len(svcs) == 1, "exactly one Service")
    svc = svcs[0]
    require(svc["spec"].get("type") == "LoadBalancer", "Service must be LoadBalancer")
    ann = (svc.get("metadata") or {}).get("annotations") or {}
    require(ann.get("lbipam.cilium.io/ips") == EXPECTED_LB_IP, f"LB IP must be {EXPECTED_LB_IP}")
    ports = svc["spec"].get("ports") or []
    require(len(ports) == 1 and ports[0].get("port") == 445, "Service must expose only TCP 445")
    require(ports[0].get("protocol", "TCP") == "TCP", "SMB port protocol TCP")

    pod = deployment_pod(rendered)
    sc = pod.get("securityContext") or {}
    require(sc.get("fsGroup") == 10000, "pod fsGroup 10000")
    require(sc.get("fsGroupChangePolicy") == "OnRootMismatch", "OnRootMismatch required for setgid skip")

    claims = pvc_volume_claims(pod)
    require(claims.get("shared-xml") == CLAIM, "Deployment must PVC-mount shared-xml")

    init = container_by_name(pod, "chown-share")
    init_cmd = "\n".join(str(x) for x in (init.get("command") or []))
    require("chown 10000:10000 /shared" in init_cmd, "initContainer must chown 10000:10000")
    require("chmod 2770 /shared" in init_cmd, "initContainer must chmod 2770 (setgid)")
    require("-R" not in init_cmd.replace("/shared", ""), "chown/chmod must be non-recursive")
    require((init.get("securityContext") or {}).get("runAsUser") == 0, "chown-share runs as root")

    app = container_by_name(pod, "app")
    image = app.get("image") or ""
    require(image == f"{SAMBA_IMAGE}:{SAMBA_TAG}", f"image must be {SAMBA_IMAGE}:{SAMBA_TAG}, got {image}")
    env = {e["name"]: e.get("value") for e in app.get("env") or [] if "name" in e}
    require(env.get("USER") == "hermes", "USER=hermes")
    require(env.get("UID") == EXPECTED_UID and env.get("GID") == EXPECTED_GID, "UID/GID 10000")
    require((app.get("securityContext") or {}).get("runAsUser") == 0, "smbd starts as root (setuid force user)")

    life = ((app.get("lifecycle") or {}).get("postStart") or {}).get("exec") or {}
    post_cmd = "\n".join(str(x) for x in (life.get("command") or []))
    require("pgrep -x smbd" in post_cmd, "postStart must wait on pgrep -x smbd (happens-after edge)")
    require("chmod 2770 /shared" in post_cmd, "postStart must re-assert 2770")
    require("exit 0" in post_cmd, "postStart must always exit 0")

    app_mounts = mounts_for(app)
    require("/shared" in app_mounts, "app mounts /shared")
    require(app_mounts.get("/etc/samba/smb.conf", {}).get("readOnly") is True, "smb.conf readOnly")
    require(app_mounts.get("/run/secrets/pass", {}).get("subPath") == "pass", "password file mount")
    require(app_mounts.get("/run/secrets/pass", {}).get("readOnly") is True, "password mount readOnly")

    # LB IP must not collide with any other in-repo annotation, and must not be a taken address.
    require(EXPECTED_LB_IP not in TAKEN_IPS, "chosen IP must not be in the taken list")
    other_ips: set[str] = set()
    for path in (REPO / "kubernetes").rglob("*.yaml"):
        if path == SAMBA_DIR / "helmrelease.yaml":
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        if "lbipam.cilium.io/ips" not in text:
            continue
        for doc in yaml.safe_load_all(text):
            if not doc:
                continue
            ann = (doc.get("metadata") or {}).get("annotations") or {}
            # also nested helm values
            raw = yaml.safe_dump(doc)
            for match in re.finditer(r"lbipam\.cilium\.io/ips:\s*[\"']?([0-9.]+)", raw):
                ip = match.group(1)
                if "samba" in str(path) and ip == EXPECTED_LB_IP:
                    continue
                other_ips.add(ip)
    require(
        EXPECTED_LB_IP not in other_ips,
        f"LB IP {EXPECTED_LB_IP} collides with another manifest: already used in repo",
    )


def test_setgid_race_model_and_filesystem_bit() -> None:
    """Model dockur/samba empty-share chmod and prove 2770 carries S_ISGID."""

    def entrypoint_empty_chmod(current: int, empty: bool) -> int:
        # From upstream samba.sh:
        #   if [ -z "$(ls -A "$share")" ]; then chmod 0770 "$share"; fi
        # runs AFTER the initContainer and BEFORE `exec smbd`.
        if empty:
            return 0o0770
        return current

    def poststart_reassert(_current: int, smbd_running: bool) -> int:
        # postStart waits for pgrep -x smbd, then chmod 2770.
        require(smbd_running, "postStart only runs its chmod after smbd is up")
        return 0o2770

    # Empty share without postStart loses setgid - the bug the hook closes.
    mode = 0o2770  # initContainer
    mode = entrypoint_empty_chmod(mode, empty=True)
    require(mode == 0o0770, "entrypoint strips setgid on empty share")
    require(mode & stat.S_ISGID == 0, "0770 must not carry S_ISGID")

    # With postStart after exec smbd, setgid is restored before clients write.
    mode = 0o2770
    mode = entrypoint_empty_chmod(mode, empty=True)
    mode = poststart_reassert(mode, smbd_running=True)
    require(mode == 0o2770, "postStart restores 2770 after smbd")
    require(mode & stat.S_ISGID == stat.S_ISGID, "2770 must carry S_ISGID")

    # Non-empty share: entrypoint leaves mode alone; postStart is idempotent.
    mode = 0o2770
    mode = entrypoint_empty_chmod(mode, empty=False)
    require(mode == 0o2770, "non-empty share keeps initContainer mode")
    mode = poststart_reassert(mode, smbd_running=True)
    require(mode == 0o2770, "postStart idempotent on already-correct mode")

    # Live filesystem probe: prove the bit semantics OnRootMismatch depends on.
    with tempfile.TemporaryDirectory(prefix="setgid-probe-") as tmp:
        share = Path(tmp) / "shared"
        share.mkdir()
        os.chmod(share, 0o2770)
        st = share.stat()
        require(st.st_mode & stat.S_ISGID, "chmod 2770 must set S_ISGID on a real directory")
        os.chmod(share, 0o0770)
        st = share.stat()
        require(not (st.st_mode & stat.S_ISGID), "chmod 0770 must clear S_ISGID on a real directory")
        # Re-assert path used by postStart
        os.chmod(share, 0o2770)
        require(share.stat().st_mode & stat.S_ISGID, "re-assert 2770 restores S_ISGID")


def test_hermes_xml_mount_rw_app_only() -> None:
    values = hr_values(
        HERMES_HR,
        env={
            "CONFIG_TIMEZONE": "America/New_York",
            "SECRET_DOMAIN": "example.com",
            "APP": "hermes",
        },
    )
    persistence = values.get("persistence") or {}
    require("data" in persistence, "hermes data persistence must remain")
    require(persistence["data"].get("existingClaim") in ("hermes", "${APP}"), "data claim untouched")
    require("xml" in persistence, "xml persistence entry required")
    xml = persistence["xml"]
    require(xml.get("existingClaim") == CLAIM, "xml existingClaim must be shared-xml")
    adv = ((xml.get("advancedMounts") or {}).get("hermes") or {})
    require(list(adv.keys()) == ["app"], f"xml must mount on app only, got {list(adv.keys())}")
    app_mounts = adv["app"]
    require(
        any(m.get("path") == "/opt/xml" for m in app_mounts),
        "app must mount shared-xml at /opt/xml",
    )
    # No readOnly: true on the xml mount (two-way share).
    for m in app_mounts:
        if m.get("path") == "/opt/xml":
            require(not m.get("readOnly"), "/opt/xml must be read-write")

    rendered = helm_template("hermes", values, namespace="ai")
    pod = deployment_pod(rendered)
    claims = pvc_volume_claims(pod)
    require(claims.get("data") == "hermes", "data volume still claims hermes")
    require(claims.get("xml") == CLAIM, "xml volume claims shared-xml")

    app = container_by_name(pod, "app")
    app_m = mounts_for(app)
    require("/opt/data" in app_m and app_m["/opt/data"]["name"] == "data", "app keeps /opt/data")
    require("/opt/xml" in app_m and app_m["/opt/xml"]["name"] == "xml", "app mounts /opt/xml")
    require(not app_m["/opt/xml"].get("readOnly"), "/opt/xml rendered read-write")

    for name in ("copy-config", "seed-skills", "codeserver"):
        try:
            other = container_by_name(pod, name)
        except Failure:
            continue
        other_m = mounts_for(other)
        require(
            "/opt/xml" not in other_m,
            f"{name} must NOT mount /opt/xml (app-only contract)",
        )
        if name != "codeserver":
            # init/seed still see /opt/data
            pass
        if "/opt/data" in other_m:
            require(other_m["/opt/data"]["name"] == "data", f"{name} /opt/data stays on data claim")


def main() -> int:
    tests = [
        test_shared_xml_pvc_contract,
        test_no_backup_on_shared_xml,
        test_samba_overlay_wiring,
        test_hermes_depends_on_ai_pvc_first,
        test_samba_rendered_workload_and_service,
        test_setgid_race_model_and_filesystem_bit,
        test_hermes_xml_mount_rw_app_only,
    ]
    failed = 0
    for test in tests:
        name = test.__name__
        try:
            test()
        except Failure as exc:
            print(f"[FAIL] {name}: {exc}")
            failed += 1
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors per-test
            print(f"[FAIL] {name}: unexpected {type(exc).__name__}: {exc}")
            failed += 1
        else:
            print(f"[PASS] {name}")
    print(f"{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
