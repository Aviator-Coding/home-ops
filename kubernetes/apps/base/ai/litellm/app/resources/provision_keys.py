#!/usr/bin/env python3
"""Mint/sync LiteLLM virtual keys from consumers.json (D4 per-consumer governance).

Runs from helmrelease.yaml as the post-install/post-upgrade hook Job and the
15m provision-keys-sync CronJob. Pure stdlib: no PyYAML, no curl, no kubectl
image - just python:alpine talking to the LiteLLM proxy's REST API and the
in-cluster Kubernetes API directly.

Idempotent: an existing consumer key is looked up in the litellm-consumer-keys
Secret and re-synced via /key/update (budgets/allow-list only, key unchanged).
A consumer with no existing key is minted via /key/generate and the new raw
key is written into the Secret. LiteLLM has no config-file-only way to declare
a budgeted key - see docs/ai-system/litellm/README.md#why-postgres.
"""
import base64
import json
import os
import ssl
import sys
import time
import urllib.error
import urllib.request

LITELLM_BASE_URL = os.environ.get("LITELLM_BASE_URL", "http://litellm.ai.svc.cluster.local:4000")
MASTER_KEY = os.environ["LITELLM_MASTER_KEY"]
CONSUMERS_FILE = os.environ.get("CONSUMERS_FILE", "/config/consumers.json")
SECRET_NAME = os.environ.get("SECRET_NAME", "litellm-consumer-keys")

SA_DIR = "/var/run/secrets/kubernetes.io/serviceaccount"
K8S_API = "https://kubernetes.default.svc"


def _read(path):
    with open(path) as f:
        return f.read().strip()


def litellm_request(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{LITELLM_BASE_URL}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {MASTER_KEY}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        return resp.status, (json.loads(raw) if raw else {})


def wait_for_litellm(attempts=30, delay=5):
    for i in range(attempts):
        try:
            # /health/readiness (checks the DB connection), not /health/liveness
            # (checks only that the process is up) - /key/generate needs the DB
            # already migrated and reachable, not just the process running.
            req = urllib.request.Request(f"{LITELLM_BASE_URL}/health/readiness")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return
        except Exception as exc:  # noqa: BLE001 - genuinely any failure means "not ready yet"
            print(f"waiting for litellm ({i + 1}/{attempts}): {exc}", file=sys.stderr)
        time.sleep(delay)
    print("litellm did not become healthy in time", file=sys.stderr)
    sys.exit(1)


def k8s_request(method, path, body=None, ok_statuses=(200,)):
    token = _read(f"{SA_DIR}/token")
    ctx = ssl.create_default_context(cafile=f"{SA_DIR}/ca.crt")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{K8S_API}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/merge-patch+json" if method == "PATCH" else "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10, context=ctx) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else {})
    except urllib.error.HTTPError as e:
        if e.code in ok_statuses:
            return e.code, {}
        raise


def get_existing_keys(namespace):
    status, body = k8s_request("GET", f"/api/v1/namespaces/{namespace}/secrets/{SECRET_NAME}", ok_statuses=(200, 404))
    if status == 404:
        return {}, False
    return {k: base64.b64decode(v).decode() for k, v in body.get("data", {}).items()}, True


def write_new_keys(namespace, secret_exists, new_keys):
    if secret_exists:
        k8s_request(
            "PATCH",
            f"/api/v1/namespaces/{namespace}/secrets/{SECRET_NAME}",
            {"stringData": new_keys},
        )
    else:
        k8s_request(
            "POST",
            f"/api/v1/namespaces/{namespace}/secrets",
            {"apiVersion": "v1", "kind": "Secret", "metadata": {"name": SECRET_NAME}, "stringData": new_keys},
        )


def main():
    namespace = _read(f"{SA_DIR}/namespace")
    with open(CONSUMERS_FILE) as f:
        consumers = json.load(f)["consumers"]

    wait_for_litellm()
    existing_keys, secret_exists = get_existing_keys(namespace)

    new_keys = {}
    for consumer in consumers:
        name = consumer["name"]
        common = {
            "models": consumer["models"],
            "max_budget": consumer["maxBudget"],
            "budget_duration": consumer["budgetDuration"],
            "rpm_limit": consumer["rpmLimit"],
            "tpm_limit": consumer["tpmLimit"],
        }
        if name in existing_keys:
            litellm_request("POST", "/key/update", {"key": existing_keys[name], **common})
            print(f"{name}: synced budget/allow-list on existing key")
        else:
            status, resp = litellm_request("POST", "/key/generate", {"key_alias": name, **common})
            new_keys[name] = resp["key"]
            print(f"{name}: minted new key ({resp['key'][:10]}...)")

    if new_keys:
        write_new_keys(namespace, secret_exists, new_keys)
        print(f"wrote {len(new_keys)} new key(s) to secret/{SECRET_NAME}")


if __name__ == "__main__":
    main()
