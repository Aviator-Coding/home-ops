#!/usr/bin/env python3
"""Self-heal the Grafana Viewer service account + token (captain option c, 2026-08-26).

Runs as a CronJob (see helmrelease.yaml). Grafana has persistence.enabled:false
(emptyDir SQLite), so every pod restart wipes admin-created service accounts
and tokens along with everything else - this script exists to notice that and
recreate them, on a schedule, with no human action.

Pure stdlib: no requests, no PyYAML, no kubectl image - just python:alpine
talking to the Grafana HTTP API and the in-cluster Kubernetes API directly
(same approach as ai/litellm's provision_keys.py).

Flow, cheapest branch first:
  1. Read the last-known token from our own Secret via the K8s API. If none is
     stored, skip straight to step 3.
  2. Probe it with one GET /api/org. 200 -> valid, exit 0, no writes at all
     (the common case: silent and cheap, matching every other successful run).
  3. Invalid or absent -> authenticate with the env-provisioned admin
     credentials (GF_SECURITY_ADMIN_USER/PASSWORD via grafana-admin-secret,
     mounted as GRAFANA_ADMIN_USER/PASSWORD). If admin auth itself 401s, that
     means live Grafana state has drifted from grafana-admin-secret (Grafana
     only re-applies GF_SECURITY_ADMIN_* to a *fresh* SQLite db at boot; a
     live password change or 1Password rotation after boot does not affect
     the running process). Fail loudly - only a Grafana pod restart clears
     this, see README.md - so the sustained-failure alert can page a human.
  4. Find-or-create the Viewer-scoped service account (captain decision A6:
     Viewer only, never Editor/Admin), revoke any tokens it already holds,
     mint one fresh token.
  5. Write the new token into our own Secret (create or patch). A PushSecret
     (pushsecret.yaml) watches that Secret and syncs it to the existing
     1Password item `grafana-mcp` / field `GRAFANA_SERVICE_ACCOUNT_TOKEN`,
     which the grafana-mcp ExternalSecret already reads from - see
     kubernetes/apps/base/ai/toolhive/mcp-servers/grafana-mcp/externalsecret.yaml.
"""
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

GRAFANA_URL = os.environ.get("GRAFANA_URL", "http://grafana.monitoring.svc.cluster.local")
ADMIN_USER = os.environ["GRAFANA_ADMIN_USER"]
ADMIN_PASSWORD = os.environ["GRAFANA_ADMIN_PASSWORD"]
SA_NAME = os.environ.get("GRAFANA_SA_NAME", "grafana-mcp-viewer")
SECRET_NAME = os.environ.get("SECRET_NAME", "grafana-sa-provisioner-token")
SECRET_KEY = os.environ.get("SECRET_KEY", "GRAFANA_SERVICE_ACCOUNT_TOKEN")

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
K8S_API = "https://kubernetes.default.svc"


def _read(path):
    with open(path) as f:
        return f.read().strip()


def _basic_auth_header(user, password):
    return "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()


def grafana_request(method, path, body=None, auth_header=None, timeout=10):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{GRAFANA_URL}{path}",
        data=data,
        method=method,
        headers={"Authorization": auth_header, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else {})


def k8s_request(method, path, body=None, ok_statuses=(200,)):
    token = _read(f"{SA_DIR}/token")
    ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{K8S_API}{path}",
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/merge-patch+json" if method == "PATCH" else "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        if e.code in ok_statuses:
            return e.code, {}
        raise


def get_stored_token(namespace):
    status, body = k8s_request("GET", f"/api/v1/namespaces/{namespace}/secrets/{SECRET_NAME}", ok_statuses=(200, 404))
    if status == 404:
        return None, False
    encoded = body.get("data", {}).get(SECRET_KEY)
    return (base64.b64decode(encoded).decode() if encoded else None), True


def write_token(namespace, secret_exists, token):
    body = {"stringData": {SECRET_KEY: token}}
    if secret_exists:
        k8s_request("PATCH", f"/api/v1/namespaces/{namespace}/secrets/{SECRET_NAME}", body)
    else:
        body.update({"apiVersion": "v1", "kind": "Secret", "metadata": {"name": SECRET_NAME}})
        k8s_request("POST", f"/api/v1/namespaces/{namespace}/secrets", body)


def probe_token(token):
    try:
        grafana_request("GET", "/api/org", auth_header=f"Bearer {token}")
        return True
    except Exception as exc:  # noqa: BLE001 - any failure means "treat as invalid"
        print(f"stored token failed probe: {exc}", file=sys.stderr)
        return False


def find_or_create_service_account(admin_auth):
    status, body = grafana_request("GET", f"/api/serviceaccounts/search?query={SA_NAME}&perpage=100", auth_header=admin_auth)
    for sa in body.get("serviceAccounts", []):
        if sa["name"] == SA_NAME:
            if sa.get("role") != "Viewer":
                # Defensive: this reconciler only ever creates Viewer accounts
                # (A6), but re-assert on every re-provision in case of manual
                # drift.
                grafana_request("PATCH", f"/api/serviceaccounts/{sa['id']}", {"role": "Viewer"}, auth_header=admin_auth)
            return sa["id"]
    _, created = grafana_request(
        "POST", "/api/serviceaccounts", {"name": SA_NAME, "role": "Viewer", "isDisabled": False}, auth_header=admin_auth
    )
    return created["id"]


def rotate_token(sa_id, admin_auth):
    _, tokens = grafana_request("GET", f"/api/serviceaccounts/{sa_id}/tokens", auth_header=admin_auth)
    for tok in tokens:
        grafana_request("DELETE", f"/api/serviceaccounts/{sa_id}/tokens/{tok['id']}", auth_header=admin_auth)
    _, minted = grafana_request(
        "POST",
        f"/api/serviceaccounts/{sa_id}/tokens",
        {"name": f"reconciler-{int(time.time())}"},
        auth_header=admin_auth,
    )
    return minted["key"]


def main():
    namespace = _read(f"{SA_DIR}/namespace")

    stored_token, secret_exists = get_stored_token(namespace)
    if stored_token and probe_token(stored_token):
        print(f"{SA_NAME}: existing token still valid, nothing to do")
        return

    admin_auth = _basic_auth_header(ADMIN_USER, ADMIN_PASSWORD)
    try:
        grafana_request("GET", "/api/org", auth_header=admin_auth)
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            print(
                "admin authentication failed (401) - grafana-admin-secret no longer "
                "matches live Grafana state. Grafana bootstraps GF_SECURITY_ADMIN_USER/"
                "PASSWORD into a fresh SQLite db only at pod startup (persistence is "
                "disabled), so this needs a Grafana pod restart to clear, not a config "
                "change here. See README.md.",
                file=sys.stderr,
            )
        raise

    sa_id = find_or_create_service_account(admin_auth)
    new_token = rotate_token(sa_id, admin_auth)
    write_token(namespace, secret_exists, new_token)
    print(f"{SA_NAME}: recreated service account + token (sa_id={sa_id})")


if __name__ == "__main__":
    main()
