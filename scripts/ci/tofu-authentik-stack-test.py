#!/usr/bin/env python3
"""Behavioral checks for the Authentik OpenTofu adoption stack.

Exercises the same offline contract CI and operators rely on:

  1. ``scripts/ci/tofu-validate.sh`` (real consumer: tofu fmt + validate,
     ``-backend=false``, no credentials).
  2. The Validate workflow's terraform job is path-filtered, installs only
     opentofu, runs that script, and injects no secret env.
  3. The stack HCL (parsed via python-hcl2 into a typed model) pairs every
     managed resource with an import, never declares client_secret, keeps
     client_id non-sensitive, forces certificate PEMs out of state, declares
     property_mappings + redirect_uri_type, and scopes secrets to Automation
     vault ``ref+op://`` refs only.
  4. The PushSecret targets a single-vault Automation SecretStore (not the
     shared multi-vault ClusterSecretStore).
  5. The runbook documents inventory, import strategy, and non-destructive
     plan evidence required for PR acceptance.

Live ``tofu plan`` against Authentik is operator-only (needs the read-only
token + RGW keys) and is deliberately not attempted here. Plan shape is
asserted from the runbook's recorded measurement, which is the artifact the
PR body must carry.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
STACK = ROOT / "terraform" / "authentik"
VALIDATE_SH = ROOT / "scripts" / "ci" / "tofu-validate.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "validate.yaml"
RUNBOOK = ROOT / "docs" / "authentik" / "terraform.md"
PUSHSECRET = (
    ROOT
    / "kubernetes"
    / "apps"
    / "base"
    / "security"
    / "authentik"
    / "app"
    / "pushsecret.yaml"
)
SECRETSTORE = (
    ROOT
    / "kubernetes"
    / "apps"
    / "base"
    / "security"
    / "authentik"
    / "app"
    / "secretstore-automation.yaml"
)
KUSTOMIZATION = (
    ROOT
    / "kubernetes"
    / "apps"
    / "base"
    / "security"
    / "authentik"
    / "app"
    / "kustomization.yaml"
)

# Expected live adoption surface (from the CNPG inventory).
EXPECTED_IMPORTS: dict[str, str] = {
    'authentik_provider_oauth2.oauth2["coder"]': "4",
    'authentik_provider_oauth2.oauth2["open-webui"]': "36",
    'authentik_provider_oauth2.oauth2["pg-admin"]': "2",
    'authentik_application.oauth2["coder"]': "coder",
    'authentik_application.oauth2["open-webui"]': "open-webui",
    'authentik_application.oauth2["pg-admin"]': "pg-admin",
    "authentik_provider_proxy.forward_auth": "37",
    "authentik_application.echo": "echo",
    "authentik_outpost_provider_attachment.forward_auth": (
        "a827266f-21ed-4a8b-a080-7b59a75a042e:37"
    ),
}

MANAGED_RESOURCE_TYPES = frozenset(
    {
        "authentik_application",
        "authentik_provider_oauth2",
        "authentik_provider_proxy",
        "authentik_outpost_provider_attachment",
    }
)

REF_OP = re.compile(r"^ref\+op://([^/]+)/")


class Failure(Exception):
    pass


def _strip_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _norm_key(key: Any) -> str:
    return _strip_quotes(str(key))


def _load_hcl(path: Path) -> dict[str, Any]:
    import hcl2  # type: ignore

    with path.open() as fh:
        return hcl2.load(fh)


def _merge_stack() -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for path in sorted(STACK.glob("*.tofu")):
        parsed = _load_hcl(path)
        for key, value in parsed.items():
            if str(key).startswith("__"):
                continue
            bucket = merged.setdefault(_norm_key(key), [])
            if isinstance(value, list):
                bucket.extend(value)
            else:
                bucket.append(value)
    return merged


def _iter_typed_blocks(
    merged: dict[str, Any], section: str, type_name: str
) -> list[tuple[str, dict[str, Any]]]:
    """Yield (name, body) for resource/data blocks of a given type.

    python-hcl2 keeps type/name labels quoted (e.g. '"oauth2"') and may nest
    the body as a bare dict rather than a single-element list.
    """
    found: list[tuple[str, dict[str, Any]]] = []
    for entry in merged.get(section, []):
        if not isinstance(entry, dict):
            continue
        for raw_type, bodies in entry.items():
            if _norm_key(raw_type) != type_name:
                continue
            if not isinstance(bodies, dict):
                continue
            for raw_name, block in bodies.items():
                name = _norm_key(raw_name)
                items = block if isinstance(block, list) else [block]
                for item in items:
                    if isinstance(item, dict):
                        found.append((name, item))
    return found


def _resource_addrs(merged: dict[str, Any]) -> set[str]:
    """Return addrs like authentik_provider_oauth2.oauth2[\"coder\"]."""
    addrs: set[str] = set()
    for rtype in MANAGED_RESOURCE_TYPES:
        for name, body in _iter_typed_blocks(merged, "resource", rtype):
            if (
                rtype
                in {
                    "authentik_application",
                    "authentik_provider_oauth2",
                }
                and name == "oauth2"
                and "for_each" in body
            ):
                for key in ("coder", "open-webui", "pg-admin"):
                    addrs.add(f'{rtype}.{name}["{key}"]')
                continue
            addrs.add(f"{rtype}.{name}")
    return addrs


def _import_map(merged: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for entry in merged.get("import", []):
        if not isinstance(entry, dict):
            continue
        to = entry.get("to")
        ident = entry.get("id")
        if to is None or ident is None:
            continue
        # hcl2 wraps references as ${...} and strings as "..."
        to_s = str(to)
        if to_s.startswith("${") and to_s.endswith("}"):
            to_s = to_s[2:-1]
        id_s = _strip_quotes(str(ident))
        out[to_s] = id_s
    return out


def _provider_oauth2_blocks(merged: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        body
        for _name, body in _iter_typed_blocks(
            merged, "resource", "authentik_provider_oauth2"
        )
    ]


def _proxy_blocks(merged: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        body
        for _name, body in _iter_typed_blocks(
            merged, "resource", "authentik_provider_proxy"
        )
    ]


def _cert_data_blocks(merged: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        body
        for _name, body in _iter_typed_blocks(
            merged, "data", "authentik_certificate_key_pair"
        )
    ]


def _variables(merged: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in merged.get("variable", []):
        if not isinstance(entry, dict):
            continue
        for name, bodies in entry.items():
            body = bodies[0] if isinstance(bodies, list) else bodies
            if isinstance(body, dict):
                out[_norm_key(name)] = body
    return out


def test_tofu_validate_script() -> dict[str, Any]:
    env = os.environ.copy()
    # Prefer a resolved tofu binary; CI installs via mise.
    path_prefix = []
    mise_tofu = Path.home() / ".local/share/mise/installs/opentofu/1.12.6"
    if (mise_tofu / "tofu").exists():
        path_prefix.append(str(mise_tofu))
    if path_prefix:
        env["PATH"] = os.pathsep.join(path_prefix + [env.get("PATH", "")])

    # Guard: the script must not see credentials even if the parent shell has them.
    for key in list(env):
        if key.startswith("TF_VAR_") or key in {
            "AWS_ACCESS_KEY_ID",
            "AWS_SECRET_ACCESS_KEY",
            "AUTHENTIK_TOKEN",
            "OP_SERVICE_ACCOUNT_TOKEN",
        }:
            env.pop(key, None)

    proc = subprocess.run(
        [str(VALIDATE_SH)],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise Failure(
            f"tofu-validate.sh failed ({proc.returncode}):\n"
            f"{proc.stdout}\n{proc.stderr}"
        )
    if "OpenTofu stack(s) formatted and valid" not in proc.stdout:
        raise Failure(f"unexpected validate output:\n{proc.stdout}")
    if "backend=false" not in VALIDATE_SH.read_text():
        raise Failure("tofu-validate.sh must init with -backend=false")
    return {
        "ok": True,
        "stdout_tail": "\n".join(proc.stdout.strip().splitlines()[-8:]),
    }


def test_workflow_terraform_job() -> dict[str, Any]:
    docs = list(yaml.safe_load_all(WORKFLOW.read_text()))
    wf = docs[0]
    jobs = wf["jobs"]
    if "terraform" not in jobs:
        raise Failure("validate.yaml missing terraform job")
    filt = jobs["filter"]["steps"]
    tf_step = next(
        (
            s
            for s in filt
            if s.get("id") == "terraform" or s.get("name") == "Terraform Changes"
        ),
        None,
    )
    if tf_step is None:
        raise Failure("filter job missing Terraform Changes step")
    patterns = tf_step["with"]["patterns"]
    for required in ("terraform/**", "scripts/ci/tofu-validate.sh"):
        if required not in patterns:
            raise Failure(f"terraform path filter missing {required!r}")

    job = jobs["terraform"]
    # No credentials on the job or its steps.
    if job.get("env"):
        raise Failure(f"terraform job must not set env credentials: {job['env']}")
    validate_step = None
    for step in job["steps"]:
        if step.get("env"):
            raise Failure(f"terraform step must not set env: {step}")
        run = step.get("run", "")
        if "apply" in run.split():
            raise Failure(f"terraform job must never apply: {run!r}")
        if "tofu-validate.sh" in run:
            validate_step = step
    if validate_step is None:
        raise Failure("terraform job does not run tofu-validate.sh")

    setup = next(s for s in job["steps"] if s.get("name") == "Setup Tools")
    if "opentofu" not in str(setup.get("with", {})):
        raise Failure("terraform job must install opentofu via mise")

    # PyYAML 1.1 parses unquoted `on:` as boolean True.
    on_block = wf.get("on", wf.get(True))
    if not isinstance(on_block, dict):
        raise Failure("workflow missing on: trigger block")
    on_paths = on_block["pull_request"]["paths"]
    if "terraform/**" not in on_paths:
        raise Failure("workflow pull_request paths missing terraform/**")

    return {
        "job": "terraform",
        "runs": validate_step["run"].strip(),
        "path_filter": [p for p in patterns.splitlines() if p.strip()],
    }


def test_stack_hcl_model() -> dict[str, Any]:
    merged = _merge_stack()
    resources = _resource_addrs(merged)
    imports = _import_map(merged)

    if resources != set(EXPECTED_IMPORTS):
        raise Failure(
            "managed resource set mismatch:\n"
            f"  only in hcl: {sorted(resources - set(EXPECTED_IMPORTS))}\n"
            f"  only expected: {sorted(set(EXPECTED_IMPORTS) - resources)}"
        )
    if imports != EXPECTED_IMPORTS:
        raise Failure(
            "import map mismatch:\n"
            f"  got: {json.dumps(imports, indent=2, sort_keys=True)}\n"
            f"  expected: {json.dumps(EXPECTED_IMPORTS, indent=2, sort_keys=True)}"
        )
    missing = resources - set(imports)
    if missing:
        raise Failure(f"resources without import blocks: {sorted(missing)}")

    # client_secret must never appear on any provider resource body.
    for block in _provider_oauth2_blocks(merged) + _proxy_blocks(merged):
        if "client_secret" in block:
            raise Failure(
                "client_secret must not be declared (optional+computed adopt path)"
            )
        if "property_mappings" not in block:
            raise Failure("property_mappings must be declared on every provider")

    for block in _provider_oauth2_blocks(merged):
        # Provider schema field is allowed_redirect_uris; may be a for-expression.
        uris = block.get("allowed_redirect_uris") or block.get("redirect_uris")
        if not uris:
            raise Failure("oauth2 providers must declare allowed_redirect_uris")
        if isinstance(uris, list):
            for uri in uris:
                if not isinstance(uri, dict) or "redirect_uri_type" not in uri:
                    raise Failure(
                        f"redirect_uri_type missing on redirect_uris entry: {uri!r}"
                    )
        elif "redirect_uri_type" not in str(uris):
            raise Failure(
                "redirect_uri_type must be present in redirect_uris expression"
            )

    for block in _cert_data_blocks(merged):
        if block.get("fetch_certificate") is not False:
            raise Failure("certificate data source must set fetch_certificate=false")
        if block.get("fetch_key") is not False:
            raise Failure("certificate data source must set fetch_key=false")

    variables = _variables(merged)
    if "authentik_token" not in variables:
        raise Failure("missing authentik_token variable")
    if variables["authentik_token"].get("sensitive") is not True:
        raise Failure("authentik_token must be sensitive")
    for name in ("coder_client_id", "open_webui_client_id", "pgadmin_client_id"):
        if name not in variables:
            raise Failure(f"missing variable {name}")
        if variables[name].get("sensitive") is True:
            raise Failure(
                f"{name} must NOT be sensitive (public OAuth2 id; avoids phantom diffs)"
            )
        if "description" not in variables[name]:
            raise Failure(f"{name} must carry a description")
        if "type" not in variables[name]:
            raise Failure(f"{name} must declare a type")

    # Backend must be S3-shaped (Ceph RGW) with lockfile, not local.
    backends = merged.get("terraform", [])
    s3 = None
    for tf in backends:
        if not isinstance(tf, dict):
            continue
        backend = tf.get("backend")
        candidates: list[Any] = []
        if isinstance(backend, list):
            candidates.extend(backend)
        elif isinstance(backend, dict):
            candidates.append(backend)
        for cand in candidates:
            if not isinstance(cand, dict):
                continue
            for bkey, bval in cand.items():
                if _norm_key(bkey) == "s3":
                    s3 = bval
                    break
            if s3 is not None:
                break
        if s3 is not None:
            break
    if s3 is None:
        raise Failure("stack must declare an s3 backend (Ceph RGW)")
    s3_body = s3[0] if isinstance(s3, list) else s3
    bucket = _strip_quotes(str(s3_body.get("bucket", "")))
    if bucket != "terraform-state":
        raise Failure(f"unexpected state bucket: {bucket!r}")
    if s3_body.get("use_lockfile") is not True:
        raise Failure("backend must enable use_lockfile")

    # Provider pin is ~> matching Authentik release line.
    main = _load_hcl(STACK / "main.tofu")
    tf_blocks = main.get("terraform", [])
    pin = None
    for block in tf_blocks:
        if not isinstance(block, dict):
            continue
        rp = block.get("required_providers")
        if not rp:
            continue
        items = rp if isinstance(rp, list) else [rp]
        for item in items:
            if not isinstance(item, dict):
                continue
            for pkey, pval in item.items():
                if _norm_key(pkey) != "authentik":
                    continue
                auth = pval[0] if isinstance(pval, list) else pval
                if isinstance(auth, dict):
                    raw_pin = auth.get("version")
                    if raw_pin is not None:
                        pin = _strip_quotes(str(raw_pin))
    if not pin or not str(pin).startswith("~>"):
        raise Failure(f"authentik provider must use ~> pin, got {pin!r}")

    return {
        "resources": sorted(resources),
        "imports": imports,
        "provider_version": pin,
        "backend_bucket": bucket,
    }


def test_secrets_vals_automation_only() -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for name in ("secrets.vals.yaml", "secrets-apply.vals.yaml"):
        data = yaml.safe_load((STACK / name).read_text())
        if not isinstance(data, dict) or not data:
            raise Failure(f"{name} must be a non-empty mapping")
        for key, value in data.items():
            if not isinstance(value, str) or not value.startswith("ref+op://"):
                raise Failure(
                    f"{name}:{key} must be a ref+op:// reference, got {value!r}"
                )
            m = REF_OP.match(value)
            if not m or m.group(1) != "Automation":
                raise Failure(
                    f"{name}:{key} must target Automation vault, got {value!r}"
                )
        summary[name] = {
            "keys": sorted(data),
            "token_field": data.get("TF_VAR_authentik_token"),
        }

    ro = summary["secrets.vals.yaml"]["token_field"]
    rw = summary["secrets-apply.vals.yaml"]["token_field"]
    if not str(ro).endswith("/AUTHENTIK_TOKEN"):
        raise Failure("plan vals must use AUTHENTIK_TOKEN (read-only)")
    if not str(rw).endswith("/AUTHENTIK_APPLY_TOKEN"):
        raise Failure("apply vals must use AUTHENTIK_APPLY_TOKEN (fail-closed)")
    if ro == rw:
        raise Failure("plan and apply vals must not share the same token field")
    return summary


def test_pushsecret_single_vault() -> dict[str, Any]:
    store = yaml.safe_load(SECRETSTORE.read_text())
    push = yaml.safe_load(PUSHSECRET.read_text())
    kust = yaml.safe_load(KUSTOMIZATION.read_text())

    if store.get("kind") != "SecretStore":
        raise Failure("secretstore-automation.yaml must be a SecretStore")
    vaults = (
        store.get("spec", {})
        .get("provider", {})
        .get("onepassword", {})
        .get("vaults", {})
    )
    if list(vaults.keys()) != ["Automation"]:
        raise Failure(f"SecretStore must scope only Automation, got {vaults!r}")

    if push.get("kind") != "PushSecret":
        raise Failure("pushsecret.yaml must be a PushSecret")
    refs = push.get("spec", {}).get("secretStoreRefs", [])
    if not any(r.get("name") == "onepassword-automation" for r in refs):
        raise Failure("PushSecret must reference onepassword-automation SecretStore")
    # Must not point at the shared multi-vault ClusterSecretStore.
    if any(r.get("name") == "onepassword" for r in refs):
        raise Failure(
            "PushSecret must not use shared onepassword ClusterSecretStore"
        )

    resources = kust.get("resources", [])
    for req in ("./secretstore-automation.yaml", "./pushsecret.yaml"):
        if req not in resources:
            raise Failure(f"kustomization missing {req}")

    return {
        "vaults": vaults,
        "push_store": refs,
        "remote_keys": [
            d.get("match", {}).get("remoteRef", {}).get("remoteKey")
            for d in push.get("spec", {}).get("data", [])
        ],
    }


def test_runbook_acceptance_surface() -> dict[str, Any]:
    text = RUNBOOK.read_text()
    required_headings = [
        "## 1. The inventory this code was written from",
        "## 2. Import strategy",
        "## 6. Planning",
        "## 7. Applying",
    ]
    missing = [h for h in required_headings if h not in text]
    if missing:
        raise Failure(f"runbook missing sections: {missing}")

    for app in ("coder", "open-webui", "pg-admin", "echo"):
        if app not in text:
            raise Failure(f"runbook inventory missing app {app!r}")

    if not re.search(r"Flows\s*\|\s*14", text):
        raise Failure("runbook must record 14 blueprint-managed flows")
    if not re.search(r"Stages\s*\|\s*18", text):
        raise Failure("runbook must record 18 blueprint-managed stages")

    if "Plan: 9 to import, 0 to add, 4 to change, 0 to destroy." not in text:
        raise Failure("runbook must record measured non-destructive plan evidence")
    if "0 to add and 0 to destroy is the number that matters" not in text:
        raise Failure("runbook must call out zero creates/destroys")

    if "explicit go-ahead" not in text and "explicit captain" not in text.lower():
        raise Failure("runbook must keep the captain-approval apply gate")
    if "tofu apply" not in text:
        raise Failure("runbook must document apply procedure")

    if "tofu-readonly" not in text:
        raise Failure("runbook must document tofu-readonly service account")
    if "403" not in text:
        raise Failure("runbook must record write-denied API checks")

    return {
        "plan_evidence": "Plan: 9 to import, 0 to add, 4 to change, 0 to destroy.",
        "sections_present": required_headings,
    }


def main() -> int:
    tests = [
        ("tofu_validate_script", test_tofu_validate_script),
        ("workflow_terraform_job", test_workflow_terraform_job),
        ("stack_hcl_model", test_stack_hcl_model),
        ("secrets_vals_automation_only", test_secrets_vals_automation_only),
        ("pushsecret_single_vault", test_pushsecret_single_vault),
        ("runbook_acceptance_surface", test_runbook_acceptance_surface),
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
        except Exception as exc:  # noqa: BLE001 - surface unexpected errors
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
