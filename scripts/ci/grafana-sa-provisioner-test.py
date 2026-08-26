#!/usr/bin/env python3
"""Behavioral + semantic tests for grafana-sa-provisioner (captain option c).

Proves the self-healing Grafana Viewer SA/token loop offline, without a live
cluster:

  1. Drive resources/reconcile.py against an in-process mock Grafana HTTP API
     and a mock in-cluster Kubernetes Secrets API. Assert observable outcomes:
       - valid stored token -> no-op (zero Grafana writes, zero Secret writes)
       - missing/invalid token -> session login, Viewer SA find-or-create,
         token mint, Secret write
       - drifted SA role is re-asserted to Viewer (never left Editor/Admin)
       - admin login failure exits non-zero and writes nothing
  2. kubectl kustomize the provisioner base and the grafana-mcp consumer path;
     assert the rendered object model (CronJob wiring via Helm values is
     checked on the source HelmRelease as a structured YAML document; PushSecret,
     PrometheusRule, ExternalSecret refresh, Reloader annotation).

Live post-merge proof (one CronJob cycle + ExternalSecret sync + grafana-mcp
query) remains deferred until Flux applies this on the cluster.
"""

from __future__ import annotations

import base64
import importlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
RECONCILE_PATH = (
    ROOT
    / "kubernetes/apps/base/monitoring/grafana-sa-provisioner/app/resources/reconcile.py"
)
PROVISIONER_APP = ROOT / "kubernetes/apps/base/monitoring/grafana-sa-provisioner/app"
PROVISIONER_OVERLAY = ROOT / "kubernetes/apps/main/monitoring/grafana-sa-provisioner.yaml"
MONITORING_MAIN = ROOT / "kubernetes/apps/main/monitoring"
MCP_SERVERS_PATH = ROOT / "kubernetes/apps/base/ai/toolhive/mcp-servers"

SA_NAME = "grafana-mcp-viewer"
SECRET_NAME = "grafana-sa-provisioner-token"
SECRET_KEY = "GRAFANA_SERVICE_ACCOUNT_TOKEN"
ADMIN_USER = "admin"
ADMIN_PASSWORD = "s3cret-admin"
NAMESPACE = "monitoring"


class Failure(Exception):
    pass


def require(cond: bool, msg: str) -> None:
    if not cond:
        raise Failure(msg)


# ---------------------------------------------------------------------------
# Mock Grafana
# ---------------------------------------------------------------------------


class GrafanaState:
    def __init__(self) -> None:
        self.admin_user = ADMIN_USER
        self.admin_password = ADMIN_PASSWORD
        self.sessions: set[str] = set()
        self.service_accounts: dict[int, dict[str, Any]] = {}
        self.tokens: dict[int, list[dict[str, Any]]] = {}  # sa_id -> tokens
        self.next_sa_id = 1
        self.next_token_id = 1
        self.calls: list[tuple[str, str, Any]] = []
        self.accept_basic = False  # cluster has GF_AUTH_BASIC_ENABLED=false
        self.fail_admin_login = False
        self.valid_bearer_tokens: set[str] = set()
        self.lock = threading.Lock()

    def reset(self) -> None:
        with self.lock:
            self.sessions.clear()
            self.service_accounts.clear()
            self.tokens.clear()
            self.next_sa_id = 1
            self.next_token_id = 1
            self.calls.clear()
            self.fail_admin_login = False
            self.valid_bearer_tokens.clear()


GRAFANA = GrafanaState()


class GrafanaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet
        return

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return None
        raw = self.rfile.read(length)
        return json.loads(raw.decode() or "null")

    def _send(self, code: int, body: Any = None) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        # Echo Set-Cookie on login only (handled by caller)
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def _session_ok(self) -> bool:
        cookie = self.headers.get("Cookie") or ""
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("grafana_session="):
                return part.split("=", 1)[1] in GRAFANA.sessions
        return False

    def _bearer_ok(self) -> bool:
        auth = self.headers.get("Authorization") or ""
        if not auth.startswith("Bearer "):
            return False
        return auth[len("Bearer ") :] in GRAFANA.valid_bearer_tokens

    def _authed(self) -> bool:
        return self._session_ok() or self._bearer_ok()

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()
        with GRAFANA.lock:
            GRAFANA.calls.append(("POST", path, body))

        if path == "/login":
            if GRAFANA.fail_admin_login or not (
                isinstance(body, dict)
                and body.get("user") == GRAFANA.admin_user
                and body.get("password") == GRAFANA.admin_password
            ):
                self._send(401, {"message": "Invalid username or password"})
                return
            sid = f"sess-{len(GRAFANA.sessions) + 1}"
            GRAFANA.sessions.add(sid)
            payload = json.dumps({"message": "Logged in"}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.send_header(
                "Set-Cookie",
                f"grafana_session={sid}; Path=/; HttpOnly",
            )
            self.end_headers()
            self.wfile.write(payload)
            return

        if path == "/api/serviceaccounts":
            if not self._session_ok():
                self._send(401, {"message": "Unauthorized"})
                return
            require(isinstance(body, dict), "create SA body must be object")
            # Enforce A6 at the API surface the reconciler hits.
            require(body.get("role") == "Viewer", f"create SA role must be Viewer, got {body.get('role')!r}")
            sa_id = GRAFANA.next_sa_id
            GRAFANA.next_sa_id += 1
            sa = {
                "id": sa_id,
                "name": body["name"],
                "role": body["role"],
                "isDisabled": body.get("isDisabled", False),
            }
            GRAFANA.service_accounts[sa_id] = sa
            GRAFANA.tokens[sa_id] = []
            self._send(201, sa)
            return

        if path.startswith("/api/serviceaccounts/") and path.endswith("/tokens"):
            if not self._session_ok():
                self._send(401, {"message": "Unauthorized"})
                return
            sa_id = int(path.split("/")[3])
            tok_id = GRAFANA.next_token_id
            GRAFANA.next_token_id += 1
            key = f"glsa_token_{tok_id}_{sa_id}"
            entry = {"id": tok_id, "name": (body or {}).get("name", "tok"), "key": key}
            GRAFANA.tokens.setdefault(sa_id, []).append(
                {"id": tok_id, "name": entry["name"]}
            )
            GRAFANA.valid_bearer_tokens.add(key)
            self._send(200, entry)
            return

        self._send(404, {"message": f"no mock for POST {path}"})

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        with GRAFANA.lock:
            GRAFANA.calls.append(("GET", path, None))

        if path == "/api/org":
            if not self._authed():
                self._send(401, {"message": "Unauthorized"})
                return
            self._send(200, {"id": 1, "name": "Main Org."})
            return

        if path == "/api/serviceaccounts/search":
            if not self._session_ok():
                self._send(401, {"message": "Unauthorized"})
                return
            qs = parse_qs(parsed.query)
            query = (qs.get("query") or [""])[0]
            found = [
                sa
                for sa in GRAFANA.service_accounts.values()
                if query in sa["name"]
            ]
            self._send(200, {"serviceAccounts": found, "totalCount": len(found)})
            return

        if path.startswith("/api/serviceaccounts/") and path.endswith("/tokens"):
            if not self._session_ok():
                self._send(401, {"message": "Unauthorized"})
                return
            sa_id = int(path.split("/")[3])
            self._send(200, list(GRAFANA.tokens.get(sa_id, [])))
            return

        self._send(404, {"message": f"no mock for GET {path}"})

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        body = self._read_json()
        with GRAFANA.lock:
            GRAFANA.calls.append(("PATCH", path, body))

        if path.startswith("/api/serviceaccounts/"):
            if not self._session_ok():
                self._send(401, {"message": "Unauthorized"})
                return
            sa_id = int(path.rsplit("/", 1)[-1])
            sa = GRAFANA.service_accounts.get(sa_id)
            if sa is None:
                self._send(404, {"message": "not found"})
                return
            if isinstance(body, dict) and "role" in body:
                require(
                    body["role"] == "Viewer",
                    f"PATCH role must re-assert Viewer, got {body['role']!r}",
                )
                sa["role"] = body["role"]
            self._send(200, sa)
            return

        self._send(404, {"message": f"no mock for PATCH {path}"})

    def do_DELETE(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        with GRAFANA.lock:
            GRAFANA.calls.append(("DELETE", path, None))

        if "/tokens/" in path:
            if not self._session_ok():
                self._send(401, {"message": "Unauthorized"})
                return
            parts = path.strip("/").split("/")
            # api serviceaccounts {id} tokens {tid}
            sa_id = int(parts[2])
            tok_id = int(parts[4])
            GRAFANA.tokens[sa_id] = [
                t for t in GRAFANA.tokens.get(sa_id, []) if t["id"] != tok_id
            ]
            self._send(200, {"message": "deleted"})
            return

        self._send(404, {"message": f"no mock for DELETE {path}"})


def start_grafana() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), GrafanaHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Mock Kubernetes Secrets API
# ---------------------------------------------------------------------------


class K8sState:
    def __init__(self) -> None:
        self.secrets: dict[str, dict[str, str]] = {}  # name -> {key: plaintext}
        self.writes: list[tuple[str, str, dict[str, Any]]] = []
        self.reads: list[str] = []

    def reset(self) -> None:
        self.secrets.clear()
        self.writes.clear()
        self.reads.clear()


K8S = K8sState()


class K8sHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _read_json(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        if length == 0:
            return None
        return json.loads(self.rfile.read(length).decode() or "null")

    def _send(self, code: int, body: Any = None) -> None:
        payload = b"" if body is None else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        prefix = f"/api/v1/namespaces/{NAMESPACE}/secrets/"
        require(path.startswith(prefix), f"unexpected k8s GET {path}")
        name = path[len(prefix) :]
        K8S.reads.append(name)
        if name not in K8S.secrets:
            self._send(404, {"kind": "Status", "reason": "NotFound"})
            return
        data = {
            k: base64.b64encode(v.encode()).decode()
            for k, v in K8S.secrets[name].items()
        }
        self._send(200, {"kind": "Secret", "metadata": {"name": name}, "data": data})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        require(
            path == f"/api/v1/namespaces/{NAMESPACE}/secrets",
            f"unexpected k8s POST {path}",
        )
        body = self._read_json()
        K8S.writes.append(("POST", path, body))
        name = body["metadata"]["name"]
        # stringData is what the reconciler sends
        string_data = body.get("stringData") or {}
        K8S.secrets[name] = dict(string_data)
        self._send(201, {"kind": "Secret", "metadata": {"name": name}})

    def do_PATCH(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        prefix = f"/api/v1/namespaces/{NAMESPACE}/secrets/"
        require(path.startswith(prefix), f"unexpected k8s PATCH {path}")
        body = self._read_json()
        K8S.writes.append(("PATCH", path, body))
        name = path[len(prefix) :]
        string_data = body.get("stringData") or {}
        K8S.secrets.setdefault(name, {}).update(string_data)
        self._send(200, {"kind": "Secret", "metadata": {"name": name}})


def start_k8s() -> tuple[ThreadingHTTPServer, str]:
    # TLS so ssl.create_default_context(cafile=...) in reconcile works with our CA.
    # Simpler: monkeypatch k8s path to plain HTTP by patching reconcile after load.
    server = ThreadingHTTPServer(("127.0.0.1", 0), K8sHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}"


# ---------------------------------------------------------------------------
# Load reconciler under controlled env
# ---------------------------------------------------------------------------


def load_reconcile(grafana_url: str, k8s_url: str, sa_dir: Path):
    os.environ["GRAFANA_ADMIN_USER"] = ADMIN_USER
    os.environ["GRAFANA_ADMIN_PASSWORD"] = ADMIN_PASSWORD
    os.environ["GRAFANA_URL"] = grafana_url
    os.environ["GRAFANA_SA_NAME"] = SA_NAME
    os.environ["SECRET_NAME"] = SECRET_NAME
    os.environ["SECRET_KEY"] = SECRET_KEY

    # Ensure a fresh module each scenario (module-level env capture).
    sys.modules.pop("reconcile_under_test", None)
    importlib.invalidate_caches()

    # Load from file path as a unique module name.
    spec = importlib.util.spec_from_file_location("reconcile_under_test", RECONCILE_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reconcile_under_test"] = mod
    spec.loader.exec_module(mod)

    mod.GRAFANA_URL = grafana_url
    mod.K8S_API = k8s_url
    mod.SA_DIR = str(sa_dir)
    mod.ADMIN_USER = ADMIN_USER
    mod.ADMIN_PASSWORD = ADMIN_PASSWORD
    mod.SA_NAME = SA_NAME
    mod.SECRET_NAME = SECRET_NAME
    mod.SECRET_KEY = SECRET_KEY

    # reconcile.k8s_request uses HTTPS + SA ca.crt. Point it at plain HTTP mock
    # by replacing k8s_request with a thin wrapper that skips TLS.
    original_urlopen = __import__("urllib.request").request.urlopen

    def k8s_request(method, path, body=None, ok_statuses=(200,)):
        token = mod._read(f"{mod.SA_DIR}/token")
        data = json.dumps(body).encode() if body is not None else None
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": (
                "application/merge-patch+json" if method == "PATCH" else "application/json"
            ),
        }
        req = __import__("urllib.request").request.Request(
            f"{mod.K8S_API}{path}",
            data=data,
            method=method,
            headers=headers,
        )
        try:
            with original_urlopen(req, timeout=10) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw else {})
        except __import__("urllib.error").error.HTTPError as e:
            if e.code in ok_statuses:
                return e.code, {}
            raise

    mod.k8s_request = k8s_request
    return mod


def make_sa_dir(tmp: Path) -> Path:
    sa = tmp / "sa"
    sa.mkdir()
    (sa / "token").write_text("k8s-sa-token")
    (sa / "namespace").write_text(NAMESPACE)
    # ca.crt unused after k8s_request patch, but create for realism
    (sa / "ca.crt").write_text("not-a-real-ca\n")
    return sa


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def run_main(mod) -> tuple[int, str, str]:
    import io
    from contextlib import redirect_stderr, redirect_stdout

    out, err = io.StringIO(), io.StringIO()
    code = 0
    try:
        with redirect_stdout(out), redirect_stderr(err):
            mod.main()
    except SystemExit as exc:
        code = int(exc.code or 0)
    except Exception:
        err.write(traceback.format_exc())
        code = 1
    return code, out.getvalue(), err.getvalue()


def scenario_valid_token_noop(mod) -> None:
    GRAFANA.reset()
    K8S.reset()
    # Pre-seed a valid token in the Secret and in Grafana's accepted set.
    token = "glsa_already_valid"
    GRAFANA.valid_bearer_tokens.add(token)
    K8S.secrets[SECRET_NAME] = {SECRET_KEY: token}

    code, out, err = run_main(mod)
    require(code == 0, f"valid-token noop should exit 0: code={code} err={err}")
    require("still valid" in out, f"expected noop message, got out={out!r} err={err!r}")
    # No Secret writes
    require(K8S.writes == [], f"noop must not write secrets: {K8S.writes}")
    # No mutating Grafana calls
    mutating = [c for c in GRAFANA.calls if c[0] in {"POST", "PATCH", "DELETE"}]
    require(mutating == [], f"noop must not mutate Grafana: {mutating}")
    # Only the probe GET /api/org
    gets = [c for c in GRAFANA.calls if c[0] == "GET"]
    require(len(gets) == 1 and gets[0][1] == "/api/org", f"expected single probe: {gets}")


def scenario_missing_token_creates_viewer(mod) -> None:
    GRAFANA.reset()
    K8S.reset()

    code, out, err = run_main(mod)
    require(code == 0, f"create path should exit 0: code={code} err={err}")
    require("recreated" in out, f"expected recreate message: out={out!r} err={err!r}")

    # Secret created with a token
    require(SECRET_NAME in K8S.secrets, "Secret was not created")
    token = K8S.secrets[SECRET_NAME].get(SECRET_KEY)
    require(bool(token), "Secret missing token key")
    require(token in GRAFANA.valid_bearer_tokens, "minted token not accepted by Grafana mock")

    # Exactly one Viewer SA
    sas = list(GRAFANA.service_accounts.values())
    require(len(sas) == 1, f"expected one SA, got {sas}")
    require(sas[0]["name"] == SA_NAME, f"SA name: {sas[0]}")
    require(sas[0]["role"] == "Viewer", f"A6 Viewer only, got role={sas[0]['role']!r}")

    # Login used session form, not Basic: POST /login must appear
    posts = [c for c in GRAFANA.calls if c[0] == "POST"]
    require(any(c[1] == "/login" for c in posts), f"expected POST /login: {posts}")
    # K8s write was create (POST), not patch
    require(
        any(w[0] == "POST" for w in K8S.writes),
        f"expected Secret create: {K8S.writes}",
    )


def scenario_invalid_token_rotates_and_patches(mod) -> None:
    GRAFANA.reset()
    K8S.reset()
    # Existing Secret with dead token; existing SA with an old token entry
    K8S.secrets[SECRET_NAME] = {SECRET_KEY: "glsa_dead"}
    sa_id = GRAFANA.next_sa_id
    GRAFANA.next_sa_id += 1
    GRAFANA.service_accounts[sa_id] = {
        "id": sa_id,
        "name": SA_NAME,
        "role": "Viewer",
        "isDisabled": False,
    }
    GRAFANA.tokens[sa_id] = [{"id": 99, "name": "old"}]

    code, out, err = run_main(mod)
    require(code == 0, f"rotate path should exit 0: code={code} err={err}")
    require("recreated" in out, f"expected recreate: out={out!r}")

    new_token = K8S.secrets[SECRET_NAME][SECRET_KEY]
    require(new_token != "glsa_dead", "token was not rotated")
    require(new_token in GRAFANA.valid_bearer_tokens, "new token not valid")
    # Old token entry deleted
    require(
        all(t["id"] != 99 for t in GRAFANA.tokens[sa_id]),
        f"old token not revoked: {GRAFANA.tokens[sa_id]}",
    )
    # Patch, not create
    require(
        any(w[0] == "PATCH" for w in K8S.writes),
        f"expected Secret patch: {K8S.writes}",
    )
    # No second SA created
    require(len(GRAFANA.service_accounts) == 1, "must find-or-create, not duplicate")


def scenario_role_drift_reassert_viewer(mod) -> None:
    GRAFANA.reset()
    K8S.reset()
    sa_id = GRAFANA.next_sa_id
    GRAFANA.next_sa_id += 1
    GRAFANA.service_accounts[sa_id] = {
        "id": sa_id,
        "name": SA_NAME,
        "role": "Admin",  # drifted
        "isDisabled": False,
    }
    GRAFANA.tokens[sa_id] = []

    code, out, err = run_main(mod)
    require(code == 0, f"drift path should exit 0: code={code} err={err}")
    sa = GRAFANA.service_accounts[sa_id]
    require(sa["role"] == "Viewer", f"role not re-asserted to Viewer: {sa}")
    patches = [c for c in GRAFANA.calls if c[0] == "PATCH"]
    require(patches, "expected PATCH to re-assert Viewer role")
    require(
        all((c[2] or {}).get("role") == "Viewer" for c in patches),
        f"PATCH bodies must set Viewer: {patches}",
    )


def scenario_admin_auth_failure(mod) -> None:
    GRAFANA.reset()
    K8S.reset()
    GRAFANA.fail_admin_login = True

    code, out, err = run_main(mod)
    require(code != 0, f"admin 401 must fail the job, got code={code} out={out!r}")
    require(
        "admin authentication failed" in err or "401" in err or "HTTPError" in err,
        f"expected loud admin failure in stderr: {err!r}",
    )
    require(K8S.writes == [], f"must not write Secret on admin failure: {K8S.writes}")
    require(
        GRAFANA.service_accounts == {},
        f"must not create SA on admin failure: {GRAFANA.service_accounts}",
    )


# ---------------------------------------------------------------------------
# Manifest semantic checks
# ---------------------------------------------------------------------------


def kustomize_build(path: Path) -> list[dict[str, Any]]:
    proc = subprocess.run(
        ["kubectl", "kustomize", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise Failure(
            f"kubectl kustomize {path} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    docs = [d for d in yaml.safe_load_all(proc.stdout) if d]
    if not docs:
        raise Failure(f"kubectl kustomize {path} produced no documents")
    return docs


def load_yaml(path: Path) -> Any:
    with path.open() as f:
        return yaml.safe_load(f)


def assert_provisioner_manifests() -> dict[str, Any]:
    docs = kustomize_build(PROVISIONER_APP)
    kinds = {(d.get("kind"), d.get("metadata", {}).get("name")) for d in docs}

    require(
        ("HelmRelease", "grafana-sa-provisioner") in kinds
        or any(d.get("kind") == "HelmRelease" for d in docs),
        f"HelmRelease missing from build: {sorted(kinds)}",
    )
    require(
        ("PushSecret", "grafana-sa-provisioner-token") in kinds,
        f"PushSecret missing: {sorted(kinds)}",
    )
    require(
        ("PrometheusRule", "grafana-sa-provisioner-rules") in kinds,
        f"PrometheusRule missing: {sorted(kinds)}",
    )
    require(
        ("ConfigMap", "grafana-sa-provisioner-configmap") in kinds,
        f"reconcile ConfigMap missing: {sorted(kinds)}",
    )

    # ConfigMap must carry the real script (executable contract surface).
    cm = next(
        d
        for d in docs
        if d.get("kind") == "ConfigMap"
        and d["metadata"]["name"] == "grafana-sa-provisioner-configmap"
    )
    script = (cm.get("data") or {}).get("reconcile.py", "")
    require("def main" in script, "ConfigMap reconcile.py missing main()")
    require("Viewer" in script, "ConfigMap script must mention Viewer role")

    # PushSecret -> existing 1Password item/field
    ps = next(d for d in docs if d.get("kind") == "PushSecret")
    ps_spec = ps["spec"]
    require(ps_spec.get("refreshInterval") == "5m", f"PushSecret refresh: {ps_spec.get('refreshInterval')}")
    require(
        (ps_spec.get("selector") or {}).get("secret", {}).get("name")
        == "grafana-sa-provisioner-token",
        f"PushSecret selector: {ps_spec.get('selector')}",
    )
    store_refs = ps_spec.get("secretStoreRefs") or []
    require(
        any(
            r.get("name") == "onepassword" and r.get("kind") == "ClusterSecretStore"
            for r in store_refs
        ),
        f"PushSecret store refs: {store_refs}",
    )
    data = ps_spec.get("data") or []
    require(len(data) == 1, f"PushSecret data entries: {data}")
    match = data[0].get("match") or {}
    require(match.get("secretKey") == SECRET_KEY, f"PushSecret secretKey: {match}")
    remote = match.get("remoteRef") or {}
    require(remote.get("remoteKey") == "grafana-mcp", f"PushSecret remoteKey: {remote}")
    require(
        remote.get("property") == SECRET_KEY,
        f"PushSecret property: {remote}",
    )

    # PrometheusRule: sustained failure + never-succeeded (vector(0)) semantics
    pr = next(d for d in docs if d.get("kind") == "PrometheusRule")
    rules = []
    for g in pr.get("spec", {}).get("groups") or []:
        rules.extend(g.get("rules") or [])
    alert = next((r for r in rules if r.get("alert") == "GrafanaSAProvisionerFailing"), None)
    require(alert is not None, f"GrafanaSAProvisionerFailing missing: {rules}")
    expr = " ".join((alert.get("expr") or "").split())
    require(
        'cronjob="grafana-sa-provisioner"' in expr
        or "cronjob='grafana-sa-provisioner'" in expr
        or 'cronjob="grafana-sa-provisioner"' in (alert.get("expr") or ""),
        f"alert expr must select the CronJob: {expr}",
    )
    require('namespace="monitoring"' in expr, f"alert must scope monitoring: {expr}")
    require(
        "kube_cronjob_status_last_successful_time" in expr,
        f"alert must use last_successful_time: {expr}",
    )
    require("vector(0)" in expr, f"alert must cover never-succeeded via vector(0): {expr}")
    require("> 3600" in expr, f"alert must fire after 1h: {expr}")
    require(alert.get("for") == "5m", f"alert for=: {alert.get('for')}")
    require(
        (alert.get("labels") or {}).get("severity") == "warning",
        f"alert severity: {alert.get('labels')}",
    )

    # HelmRelease values: CronJob */5, admin secretKeyRef, no new secret
    hr = next(d for d in docs if d.get("kind") == "HelmRelease")
    values = hr.get("spec", {}).get("values") or {}
    controllers = values.get("controllers") or {}
    ctrl = controllers.get("grafana-sa-provisioner") or {}
    require(ctrl.get("type") == "cronjob", f"controller type: {ctrl.get('type')}")
    schedule = ((ctrl.get("cronjob") or {}).get("schedule"))
    require(schedule == "*/5 * * * *", f"CronJob schedule: {schedule}")
    require(
        (ctrl.get("cronjob") or {}).get("concurrencyPolicy") == "Forbid",
        "concurrencyPolicy must be Forbid",
    )
    container = ((ctrl.get("containers") or {}).get("app") or {})
    env = container.get("env") or {}
    # bjw-s app-template accepts map or list env; handle map form used here
    if isinstance(env, dict):
        admin_user = env.get("GRAFANA_ADMIN_USER") or {}
        admin_pass = env.get("GRAFANA_ADMIN_PASSWORD") or {}
        user_ref = (admin_user.get("valueFrom") or {}).get("secretKeyRef") or {}
        pass_ref = (admin_pass.get("valueFrom") or {}).get("secretKeyRef") or {}
    else:
        raise Failure(f"unexpected env form: {env!r}")
    require(user_ref.get("name") == "grafana-admin-secret", f"admin user secret: {user_ref}")
    require(user_ref.get("key") == "admin-user", f"admin user key: {user_ref}")
    require(pass_ref.get("name") == "grafana-admin-secret", f"admin pass secret: {pass_ref}")
    require(pass_ref.get("key") == "admin-password", f"admin pass key: {pass_ref}")
    require(
        (env.get("GRAFANA_URL") == "http://grafana.monitoring.svc.cluster.local")
        or (
            isinstance(env.get("GRAFANA_URL"), dict)
            and env["GRAFANA_URL"].get("value")
            == "http://grafana.monitoring.svc.cluster.local"
        ),
        f"GRAFANA_URL: {env.get('GRAFANA_URL')}",
    )

    # RBAC: get/patch pinned to owned secret only
    rbac_roles = (values.get("rbac") or {}).get("roles") or {}
    role = rbac_roles.get("grafana-sa-provisioner-secrets") or {}
    rules = role.get("rules") or []
    get_patch = [
        r
        for r in rules
        if set(r.get("verbs") or []) >= {"get", "patch"}
        or set(r.get("verbs") or []) == {"get", "patch"}
    ]
    require(get_patch, f"missing get/patch rule: {rules}")
    require(
        any(
            (r.get("resourceNames") == [SECRET_NAME])
            for r in get_patch
        ),
        f"get/patch must pin resourceNames to {SECRET_NAME}: {get_patch}",
    )
    create_rules = [r for r in rules if "create" in (r.get("verbs") or [])]
    require(create_rules, f"missing create rule: {rules}")
    require(
        all(not r.get("resourceNames") for r in create_rules),
        "create must not set resourceNames (K8s cannot scope create by name)",
    )

    # Flux overlay Kustomization
    overlay = load_yaml(PROVISIONER_OVERLAY)
    require(overlay.get("kind") == "Kustomization", "overlay must be Flux Kustomization")
    require(
        overlay.get("metadata", {}).get("namespace") == "monitoring",
        f"overlay ns: {overlay.get('metadata')}",
    )
    require(
        overlay.get("spec", {}).get("targetNamespace") == "monitoring",
        f"targetNamespace: {overlay.get('spec')}",
    )
    deps = {
        (d.get("name"), d.get("namespace"))
        for d in (overlay.get("spec", {}).get("dependsOn") or [])
    }
    require(("grafana", "monitoring") in deps, f"must depend on grafana: {deps}")
    require(
        ("onepassword-store", "security") in deps,
        f"must depend on onepassword-store: {deps}",
    )
    require(
        overlay["spec"].get("path")
        == "./kubernetes/apps/base/monitoring/grafana-sa-provisioner/app",
        f"overlay path: {overlay['spec'].get('path')}",
    )

    # monitoring main kustomization includes the overlay
    main_k = load_yaml(MONITORING_MAIN / "kustomization.yaml")
    resources = main_k.get("resources") or []
    require(
        any("grafana-sa-provisioner" in str(r) for r in resources),
        f"monitoring main missing provisioner overlay: {resources}",
    )

    return {
        "kustomize_docs": len(docs),
        "alert_expr": expr,
        "schedule": schedule,
        "pushsecret_remote": f"{remote.get('remoteKey')}/{remote.get('property')}",
    }


def assert_consumer_manifests() -> dict[str, Any]:
    docs = kustomize_build(MCP_SERVERS_PATH)

    es = next(
        d
        for d in docs
        if d.get("kind") == "ExternalSecret"
        and d.get("metadata", {}).get("name") == "toolhive-grafana"
    )
    require(
        es["spec"].get("refreshInterval") == "5m",
        f"grafana-mcp ExternalSecret refreshInterval must be 5m, got {es['spec'].get('refreshInterval')!r}",
    )
    data_from = es["spec"].get("dataFrom") or []
    keys = [((i.get("extract") or {}).get("key")) for i in data_from]
    require("grafana-mcp" in keys, f"ExternalSecret must read grafana-mcp item: {keys}")

    server = next(
        d
        for d in docs
        if d.get("kind") == "MCPServer"
        and d.get("metadata", {}).get("name") == "grafana-mcp"
    )
    ann = (
        ((server.get("spec") or {}).get("podTemplateSpec") or {})
        .get("metadata")
        or {}
    ).get("annotations") or {}
    require(
        ann.get("secret.reloader.stakater.com/reload") == "toolhive-grafana",
        f"Reloader annotation missing/wrong on grafana-mcp pod template: {ann}",
    )
    # secrets still point at toolhive-grafana / GRAFANA_SERVICE_ACCOUNT_TOKEN
    secrets = server["spec"].get("secrets") or []
    require(
        any(
            s.get("name") == "toolhive-grafana"
            and s.get("key") == SECRET_KEY
            for s in secrets
        ),
        f"MCPServer secrets wiring: {secrets}",
    )
    return {
        "refreshInterval": es["spec"].get("refreshInterval"),
        "reloader": ann.get("secret.reloader.stakater.com/reload"),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    require(RECONCILE_PATH.is_file(), f"missing {RECONCILE_PATH}")

    print("==> start mock Grafana + Kubernetes APIs")
    g_srv, g_url = start_grafana()
    k_srv, k_url = start_k8s()
    print(f"    grafana={g_url} k8s={k_url}")

    results: list[str] = []
    try:
        with tempfile.TemporaryDirectory() as tmp:
            sa_dir = make_sa_dir(Path(tmp))
            mod = load_reconcile(g_url, k_url, sa_dir)

            scenarios: list[tuple[str, Callable]] = [
                ("valid stored token is a no-op", scenario_valid_token_noop),
                ("missing token creates Viewer SA + Secret", scenario_missing_token_creates_viewer),
                ("invalid token rotates + patches Secret", scenario_invalid_token_rotates_and_patches),
                ("drifted SA role re-asserted to Viewer (A6)", scenario_role_drift_reassert_viewer),
                ("admin auth failure fails job, writes nothing", scenario_admin_auth_failure),
            ]
            for name, fn in scenarios:
                print(f"==> scenario: {name}")
                fn(mod)
                print("    OK")
                results.append(name)

        print("==> kustomize/semantic: grafana-sa-provisioner")
        prov = assert_provisioner_manifests()
        print(f"    OK docs={prov['kustomize_docs']} schedule={prov['schedule']} push={prov['pushsecret_remote']}")
        results.append("provisioner manifests")

        print("==> kustomize/semantic: grafana-mcp consumer")
        cons = assert_consumer_manifests()
        print(f"    OK refresh={cons['refreshInterval']} reloader={cons['reloader']}")
        results.append("consumer manifests")

    finally:
        g_srv.shutdown()
        k_srv.shutdown()

    print("PASS: grafana-sa-provisioner self-heal contracts hold")
    print("covered:")
    for r in results:
        print(f"  - {r}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Failure as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)
