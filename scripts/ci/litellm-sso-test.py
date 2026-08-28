#!/usr/bin/env python3
"""Behavioral checks for LiteLLM UI SSO through Authentik.

This is the seam between the already-applied Authentik half and the still-pending
Flux half of the LiteLLM SSO change:

  1. Live Authentik OIDC discovery for the ``litellm`` application must serve
     the authorize/token/userinfo/end-session endpoints the Kubernetes
     ``LiteLLMProxy`` env will call after merge.
  2. ``PROXY_BASE_URL`` on the LiteLLMProxy CR must couple to the redirect URI
     OpenTofu allows on the provider (``{base}/sso/callback``).
  3. Credentials path: ExternalSecret pulls GENERIC_CLIENT_* from 1Password
     ``litellm-sso``; PushSecret writes that item through the Automation
     ClusterSecretStore (not the multi-vault shared store).
  4. ExtAuth regression: ``echo.sklab.dev`` must still 302 to Authentik with all
     five proxy scopes intact after the first apply's property_mappings reorder.
  5. Pre-merge reality: the running LiteLLM deployment does NOT yet auto-redirect
     to SSO (Flux has not applied the CR env). The test records that gap rather
     than pretending full browser login works from this branch alone.

Live checks use public HTTPS endpoints only - no Authentik admin token, no
cluster kubeconfig, no 1Password credentials.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import yaml

ROOT = Path(__file__).resolve().parents[2]
PROXY_CR = ROOT / "kubernetes/apps/base/ai/litellm/app/litellmproxy.yaml"
EXTERNAL_SECRET = ROOT / "kubernetes/apps/base/ai/litellm/app/externalsecret.yaml"
PUSH_SECRET = ROOT / "kubernetes/apps/base/ai/litellm/app/pushsecret-sso.yaml"
KUSTOMIZATION = ROOT / "kubernetes/apps/base/ai/litellm/app/kustomization.yaml"
LITELLM_TOFU = ROOT / "terraform/authentik/litellm.tofu"
OUTPUTS_TOFU = ROOT / "terraform/authentik/outputs.tofu"
CLUSTER_DOMAIN = os.environ.get("CLUSTER_DOMAIN", "sklab.dev")
AUTH_BASE = f"https://auth.{CLUSTER_DOMAIN}"
LITELLM_BASE = f"https://litellm.{CLUSTER_DOMAIN}"
ECHO_URL = f"https://echo.{CLUSTER_DOMAIN}/"
OIDC_URL = f"{AUTH_BASE}/application/o/litellm/.well-known/openid-configuration"
EXPECTED_PROXY_SCOPES = frozenset(
    {"openid", "email", "profile", "ak_proxy", "entitlements"}
)


class Failure(Exception):
    pass


def _http(
    url: str,
    *,
    method: str = "GET",
    follow: bool = False,
    timeout: float = 15.0,
) -> tuple[int, dict[str, str], bytes]:
    req = urllib.request.Request(url, method=method)
    # Never follow redirects automatically - callers need the Location.
    opener = urllib.request.build_opener(urllib.request.HTTPHandler)
    if not follow:
        class _NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001,N803
                return None

        opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(req, timeout=timeout) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, headers, resp.read()
    except urllib.error.HTTPError as exc:
        headers = {k.lower(): v for k, v in exc.headers.items()}
        body = exc.read() if exc.fp else b""
        return exc.code, headers, body


def _env_from_proxy_cr() -> dict[str, str]:
    docs = list(yaml.safe_load_all(PROXY_CR.read_text()))
    proxy = next(d for d in docs if isinstance(d, dict) and d.get("kind") == "LiteLLMProxy")
    env_list = (proxy.get("spec") or {}).get("env") or []
    out: dict[str, str] = {}
    for item in env_list:
        if isinstance(item, dict) and "name" in item and "value" in item:
            out[item["name"]] = str(item["value"])
    return out


def _subst_domain(value: str) -> str:
    return value.replace("${SECRET_DOMAIN}", CLUSTER_DOMAIN)


def test_live_oidc_discovery() -> dict[str, Any]:
    code, _headers, body = _http(OIDC_URL)
    if code != 200:
        raise Failure(f"OIDC discovery HTTP {code} for {OIDC_URL}")
    doc = json.loads(body.decode())
    required = {
        "authorization_endpoint": f"{AUTH_BASE}/application/o/authorize/",
        "token_endpoint": f"{AUTH_BASE}/application/o/token/",
        "userinfo_endpoint": f"{AUTH_BASE}/application/o/userinfo/",
        "end_session_endpoint": f"{AUTH_BASE}/application/o/litellm/end-session/",
        "issuer": f"{AUTH_BASE}/application/o/litellm/",
    }
    for key, expected in required.items():
        got = doc.get(key)
        if got != expected:
            raise Failure(f"OIDC {key}: got {got!r}, expected {expected!r}")

    # Default scopes always present. litellm_role is pending second apply -
    # record presence rather than requiring it, so the test stays honest about
    # the blocked apply gate documented in docs/authentik/terraform.md.
    scopes = set(doc.get("scopes_supported") or [])
    for base in ("openid", "email", "profile"):
        if base not in scopes:
            raise Failure(f"OIDC scopes_supported missing {base!r}: {sorted(scopes)}")

    jwks_uri = doc.get("jwks_uri")
    jcode, _, jbody = _http(str(jwks_uri))
    if jcode != 200:
        raise Failure(f"JWKS HTTP {jcode} for {jwks_uri}")
    jwks = json.loads(jbody.decode())
    if not jwks.get("keys"):
        raise Failure("JWKS returned no keys")

    return {
        "issuer": doc["issuer"],
        "endpoints": required,
        "scopes_supported": sorted(scopes),
        "litellm_role_in_discovery": "litellm_role" in scopes,
        "jwks_keys": len(jwks["keys"]),
    }


def test_proxy_cr_couples_to_authentik() -> dict[str, Any]:
    env = _env_from_proxy_cr()
    required_keys = [
        "GENERIC_AUTHORIZATION_ENDPOINT",
        "GENERIC_TOKEN_ENDPOINT",
        "GENERIC_USERINFO_ENDPOINT",
        "PROXY_BASE_URL",
        "GENERIC_SCOPE",
        "GENERIC_USER_ROLE_ATTRIBUTE",
        "AUTO_REDIRECT_UI_LOGIN_TO_SSO",
        "PROXY_LOGOUT_URL",
    ]
    missing = [k for k in required_keys if k not in env]
    if missing:
        raise Failure(f"LiteLLMProxy missing SSO env: {missing}")

    if env["AUTO_REDIRECT_UI_LOGIN_TO_SSO"] != "true":
        raise Failure(
            f"AUTO_REDIRECT_UI_LOGIN_TO_SSO must be true, got "
            f"{env['AUTO_REDIRECT_UI_LOGIN_TO_SSO']!r}"
        )
    if env["GENERIC_USER_ROLE_ATTRIBUTE"] != "litellm_role":
        raise Failure(
            f"GENERIC_USER_ROLE_ATTRIBUTE must be litellm_role, got "
            f"{env['GENERIC_USER_ROLE_ATTRIBUTE']!r}"
        )
    scopes = set(env["GENERIC_SCOPE"].split())
    if scopes != {"openid", "email", "profile", "litellm_role"}:
        raise Failure(f"GENERIC_SCOPE mismatch: {sorted(scopes)}")

    # Live OIDC endpoints must match the CR once SECRET_DOMAIN is substituted.
    code, _, body = _http(OIDC_URL)
    if code != 200:
        raise Failure(f"OIDC discovery HTTP {code}")
    live = json.loads(body.decode())
    pairs = [
        ("GENERIC_AUTHORIZATION_ENDPOINT", "authorization_endpoint"),
        ("GENERIC_TOKEN_ENDPOINT", "token_endpoint"),
        ("GENERIC_USERINFO_ENDPOINT", "userinfo_endpoint"),
        ("PROXY_LOGOUT_URL", "end_session_endpoint"),
    ]
    matched = {}
    for env_key, live_key in pairs:
        want = _subst_domain(env[env_key])
        got = live.get(live_key)
        if want != got:
            raise Failure(
                f"{env_key}={want!r} does not match live {live_key}={got!r}"
            )
        matched[env_key] = want

    # Redirect coupling: tofu allowed URL == PROXY_BASE_URL + /sso/callback
    tofu = LITELLM_TOFU.read_text()
    m = re.search(
        r'redirect_uri_type\s*=\s*"authorization"\s*\n\s*url\s*=\s*"([^"]+)"',
        tofu,
    )
    if not m:
        raise Failure("litellm.tofu missing authorization redirect_uri url")
    tofu_redirect = m.group(1).replace("${var.cluster_domain}", CLUSTER_DOMAIN)
    proxy_base = _subst_domain(env["PROXY_BASE_URL"])
    expected_redirect = f"{proxy_base}/sso/callback"
    if tofu_redirect != expected_redirect:
        raise Failure(
            f"redirect coupling broken: tofu={tofu_redirect!r} "
            f"expected from PROXY_BASE_URL={expected_redirect!r}"
        )

    # outputs.tofu endpoint map must agree with the CR (documentation contract).
    outputs = OUTPUTS_TOFU.read_text()
    for fragment in (
        "/application/o/authorize/",
        "/application/o/token/",
        "/application/o/userinfo/",
    ):
        if fragment not in outputs:
            raise Failure(f"outputs.tofu missing endpoint fragment {fragment}")

    return {
        "proxy_base_url": proxy_base,
        "redirect_uri": tofu_redirect,
        "matched_live_endpoints": matched,
        "generic_scope": sorted(scopes),
    }


def test_secret_path() -> dict[str, Any]:
    es = yaml.safe_load(EXTERNAL_SECRET.read_text())
    ps = yaml.safe_load(PUSH_SECRET.read_text())
    kust = yaml.safe_load(KUSTOMIZATION.read_text())

    if es.get("kind") != "ExternalSecret":
        raise Failure("externalsecret.yaml must be ExternalSecret")
    tmpl = ((es.get("spec") or {}).get("target") or {}).get("template") or {}
    data = tmpl.get("data") or {}
    for key in ("GENERIC_CLIENT_ID", "GENERIC_CLIENT_SECRET"):
        if key not in data:
            raise Failure(f"ExternalSecret template missing {key}")
    # Non-secret SSO config must NOT be buried in the secret template.
    for banned in (
        "GENERIC_AUTHORIZATION_ENDPOINT",
        "PROXY_BASE_URL",
        "AUTO_REDIRECT_UI_LOGIN_TO_SSO",
        "GENERIC_SCOPE",
    ):
        if banned in data:
            raise Failure(f"non-secret SSO setting {banned} must stay on CR env")

    data_from = [
        (item.get("extract") or {}).get("key")
        for item in (es.get("spec") or {}).get("dataFrom") or []
    ]
    if "litellm-sso" not in data_from:
        raise Failure(f"ExternalSecret dataFrom must include litellm-sso, got {data_from}")

    if ps.get("kind") != "PushSecret":
        raise Failure("pushsecret-sso.yaml must be PushSecret")
    refs = (ps.get("spec") or {}).get("secretStoreRefs") or []
    if not any(
        r.get("name") == "onepassword-automation"
        and r.get("kind") == "ClusterSecretStore"
        for r in refs
    ):
        raise Failure(
            "litellm SSO PushSecret must use onepassword-automation ClusterSecretStore"
        )
    if any(r.get("name") == "onepassword" for r in refs):
        raise Failure("litellm SSO PushSecret must not use multi-vault onepassword store")

    remote = {
        (d.get("match") or {}).get("remoteRef", {}).get("property"): (
            (d.get("match") or {}).get("remoteRef", {}).get("remoteKey")
        )
        for d in (ps.get("spec") or {}).get("data") or []
    }
    if remote.get("LITELLM_SSO_CLIENT_ID") != "litellm-sso":
        raise Failure(f"PushSecret client id remoteKey mismatch: {remote}")
    if remote.get("LITELLM_SSO_CLIENT_SECRET") != "litellm-sso":
        raise Failure(f"PushSecret client secret remoteKey mismatch: {remote}")

    resources = kust.get("resources") or []
    for req in ("./externalsecret.yaml", "./pushsecret-sso.yaml", "./litellmproxy.yaml"):
        if req not in resources:
            raise Failure(f"litellm kustomization missing {req}")

    return {
        "external_secret_keys": sorted(data),
        "data_from": data_from,
        "push_store": refs,
        "push_remote": remote,
    }


def test_echo_extauth_scopes_intact() -> dict[str, Any]:
    code, headers, _body = _http(ECHO_URL)
    if code not in (301, 302, 303, 307, 308):
        raise Failure(f"echo.sklab.dev expected redirect, got HTTP {code}")
    location = headers.get("location") or ""
    if "auth." not in location or "/application/o/authorize" not in location:
        raise Failure(f"echo redirect is not Authentik authorize: {location!r}")
    qs = parse_qs(urlparse(location).query)
    scope_raw = (qs.get("scope") or [""])[0]
    scopes = set(scope_raw.replace("+", " ").split())
    missing = EXPECTED_PROXY_SCOPES - scopes
    if missing:
        raise Failure(
            f"echo ExtAuth lost proxy scopes {sorted(missing)}; got {sorted(scopes)}"
        )
    return {
        "http": code,
        "scopes": sorted(scopes),
        "client_id_present": bool(qs.get("client_id")),
    }


def test_premerge_litellm_has_no_sso_redirect() -> dict[str, Any]:
    """Document the intended ordering: Authentik first, Flux second.

    Until this PR merges, the live UI must NOT auto-bounce to Authentik. A
    surprise SSO redirect here would mean something else already reconfigured
    the running proxy, which is outside this branch's delivery claim.
    """
    # /ui/ should serve the SPA shell without Authentik hop.
    code, headers, body = _http(f"{LITELLM_BASE}/ui/")
    if code != 200:
        raise Failure(f"litellm /ui/ expected 200, got {code}")
    location = headers.get("location") or ""
    if "auth." in location:
        raise Failure(
            f"live litellm /ui/ unexpectedly redirects to Authentik: {location!r}"
        )
    text = body.decode("utf-8", errors="replace")
    if "authentik" in text.lower() and "sso" in text.lower() and "redirect" in text.lower():
        # Soft signal only - SPA bundles can mention strings without redirecting.
        pass

    # /sso/login is registered only when generic SSO env is configured.
    scode, _, sbody = _http(f"{LITELLM_BASE}/sso/login")
    # Pre-merge: 404 (route absent) is the expected "SSO not wired yet" signal.
    # Post-merge: 302 to Authentik would also be fine; anything else is noise.
    if scode not in (404, 302, 303, 307, 401, 403):
        raise Failure(f"unexpected /sso/login status {scode}: {sbody[:200]!r}")

    end_code, end_headers, _ = _http(f"{AUTH_BASE}/application/o/litellm/end-session/")
    if end_code not in (301, 302, 303, 307, 308):
        raise Failure(f"litellm end-session expected redirect, got {end_code}")
    end_loc = end_headers.get("location") or ""
    if "flow" not in end_loc and "login" not in end_loc and "authorize" not in end_loc:
        # Unauthenticated end-session should bounce into an Authentik flow.
        raise Failure(f"end-session redirect unexpected: {end_loc!r}")

    return {
        "ui_http": code,
        "sso_login_http": scode,
        "sso_login_means": (
            "route_absent_pre_merge"
            if scode == 404
            else "sso_route_present"
        ),
        "end_session_http": end_code,
        "end_session_location": end_loc,
        "flux_sso_env_live": scode != 404,
    }


def test_kustomize_build_includes_sso_surface() -> dict[str, Any]:
    env = os.environ.copy()
    cmd = [
        "kubectl",
        "kustomize",
        str(ROOT / "kubernetes/apps/base/ai/litellm/app"),
        "--load-restrictor",
        "LoadRestrictionsNone",
    ]
    try:
        proc = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
            env=env,
            timeout=60,
        )
    except FileNotFoundError as exc:
        raise Failure("kubectl not available for kustomize build") from exc
    except subprocess.CalledProcessError as exc:
        raise Failure(f"kustomize build failed: {exc.stderr[-500:]}") from exc

    docs = [d for d in yaml.safe_load_all(proc.stdout) if isinstance(d, dict)]
    kinds = {
        f"{d.get('kind')}/{(d.get('metadata') or {}).get('name')}" for d in docs
    }
    for required in (
        "LiteLLMProxy/litellm",
        "ExternalSecret/litellm",
        "PushSecret/litellm-sso-credentials",
    ):
        if required not in kinds:
            raise Failure(f"kustomize output missing {required}; got {sorted(kinds)}")

    proxy = next(d for d in docs if d.get("kind") == "LiteLLMProxy")
    env_names = {
        e.get("name")
        for e in (proxy.get("spec") or {}).get("env") or []
        if isinstance(e, dict)
    }
    for name in (
        "PROXY_BASE_URL",
        "GENERIC_SCOPE",
        "AUTO_REDIRECT_UI_LOGIN_TO_SSO",
        "PROXY_LOGOUT_URL",
    ):
        if name not in env_names:
            raise Failure(f"built LiteLLMProxy missing env {name}")

    return {"kinds": sorted(kinds), "sso_env_names": sorted(env_names)}


def main() -> int:
    tests = [
        ("live_oidc_discovery", test_live_oidc_discovery),
        ("proxy_cr_couples_to_authentik", test_proxy_cr_couples_to_authentik),
        ("secret_path", test_secret_path),
        ("echo_extauth_scopes_intact", test_echo_extauth_scopes_intact),
        ("premerge_litellm_has_no_sso_redirect", test_premerge_litellm_has_no_sso_redirect),
        ("kustomize_build_includes_sso_surface", test_kustomize_build_includes_sso_surface),
    ]
    report: dict[str, Any] = {"results": {}, "ok": True}
    for name, fn in tests:
        try:
            report["results"][name] = {"status": "pass", "detail": fn()}
            print(f"PASS  {name}")
        except Failure as exc:
            report["results"][name] = {"status": "fail", "error": str(exc)}
            report["ok"] = False
            print(f"FAIL  {name}: {exc}", file=sys.stderr)
        except Exception as exc:  # noqa: BLE001
            report["results"][name] = {
                "status": "error",
                "error": f"{type(exc).__name__}: {exc}",
            }
            report["ok"] = False
            print(f"ERROR {name}: {type(exc).__name__}: {exc}", file=sys.stderr)

    print("---")
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
