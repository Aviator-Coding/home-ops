#!/usr/bin/env python3
"""Wizarr's own setup is seeded from GitOps secrets by an idempotent init container.

  1. ``seed.py`` creates the admin, the Plex server, the Seerr connection and a Hermes API key, and a second run
     changes nothing (behaviour test against in-memory stand-ins for Wizarr's models).
  2. A token or key that rotated in 1Password reaches Wizarr; a manual admin password change is not clobbered.
  3. Manifest wiring: ConfigMap, init container on the app image, env from the right Secrets, generated
     credentials pushed back to 1Password so the owner can log in.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import types
from pathlib import Path

_spec = importlib.util.spec_from_file_location("bc", Path(__file__).with_name("_books_common.py"))
bc = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bc
_spec.loader.exec_module(bc)  # type: ignore[union-attr]

APP = bc.ROOT / "kubernetes/apps/base/media/wizarr/app"
ENV = {"WIZARR_ADMIN_USERNAME": "admin", "WIZARR_ADMIN_PASSWORD": "pw-one", "WIZARR_API_KEY": "key-one",
       "PLEX_URL": "http://plex.test:32400", "PLEX_TOKEN": "tok-one",
       "SEERR_URL": "http://seerr.test:5055", "SEERR_API_KEY": "seerr-one"}


class _Query:
    def __init__(self, cls):
        self.cls = cls

    def filter_by(self, **kw):
        self.kw = kw
        return self

    def first(self):
        for row in self.cls.rows:
            if all(getattr(row, k, None) == v for k, v in self.kw.items()):
                return row
        return None


def _model(name: str):
    class Model:
        rows: list = []

        def __init__(self, **kw):
            for k, v in kw.items():
                setattr(self, k, v)
            self.id = None

        def set_password(self, raw):
            self.password_hash = "hash:" + raw

    Model.__name__ = name
    Model.rows = []
    Model.query = _Query(Model)
    return Model


class _Session:
    def __init__(self, models):
        self.models, self.n = models, 0

    def add(self, row):
        cls = type(row)
        if row not in cls.rows:
            cls.rows.append(row)

    def flush(self):
        for m in self.models:
            for row in m.rows:
                if row.id is None:
                    self.n += 1
                    row.id = self.n


def _fixture():
    ns = types.SimpleNamespace(
        AdminAccount=_model("AdminAccount"), MediaServer=_model("MediaServer"),
        Connection=_model("Connection"), ApiKey=_model("ApiKey"), Settings=_model("Settings"))
    return ns, _Session([ns.AdminAccount, ns.MediaServer, ns.Connection, ns.ApiKey, ns.Settings])


def _load_seed():
    path = APP / "seed.py"
    bc.assert_true(path.exists(), "seed.py missing")
    spec = importlib.util.spec_from_file_location("wizarr_seed", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


def check_seed_creates_everything() -> None:
    models, session = _fixture()
    _load_seed().seed(models, session, ENV)
    admin = models.AdminAccount.rows
    bc.assert_true(len(admin) == 1 and admin[0].username == "admin" and admin[0].password_hash == "hash:pw-one", "admin")
    settings = {r.key: r.value for r in models.Settings.rows}
    bc.assert_true(settings.get("admin_username") == "admin", "Settings admin_username gates Wizarr's onboarding redirect")
    plex = models.MediaServer.rows
    bc.assert_true(len(plex) == 1 and plex[0].server_type == "plex" and plex[0].url == ENV["PLEX_URL"]
                   and plex[0].api_key == "tok-one", "plex server")
    conn = models.Connection.rows
    bc.assert_true(len(conn) == 1 and conn[0].connection_type == "overseerr" and conn[0].url == ENV["SEERR_URL"]
                   and conn[0].api_key == "seerr-one" and conn[0].media_server_id == plex[0].id, "seerr connection")
    bc.assert_true(conn[0].provision_plex_users is True, "Seerr accounts are provisioned on invite")
    key = models.ApiKey.rows
    bc.assert_true(len(key) == 1 and key[0].key_hash == hashlib.sha256(b"key-one").hexdigest()
                   and key[0].created_by_id == admin[0].id and key[0].is_active is True, "api key stored hashed")


def check_seed_is_idempotent_and_rotates() -> None:
    models, session = _fixture()
    mod = _load_seed()
    mod.seed(models, session, ENV)
    models.AdminAccount.rows[0].password_hash = "hash:changed-by-hand"
    mod.seed(models, session, {**ENV, "PLEX_TOKEN": "tok-two", "SEERR_API_KEY": "seerr-two"})
    counts = [len(m.rows) for m in (models.AdminAccount, models.MediaServer, models.Connection, models.ApiKey)]
    bc.assert_true(counts == [1, 1, 1, 1], f"second run must not duplicate rows, got {counts}")
    bc.assert_true(models.AdminAccount.rows[0].password_hash == "hash:changed-by-hand", "admin password must not be reset")
    bc.assert_true(models.MediaServer.rows[0].api_key == "tok-two", "rotated Plex token must reach Wizarr")
    bc.assert_true(models.Connection.rows[0].api_key == "seerr-two", "rotated Seerr key must reach Wizarr")


def check_seed_fails_fast_on_missing_env() -> None:
    models, session = _fixture()
    env = {k: v for k, v in ENV.items() if k != "PLEX_TOKEN"}
    try:
        _load_seed().seed(models, session, env)
    except KeyError:
        return
    raise bc.Failure("a missing PLEX_TOKEN must abort the seed, not create a half-configured server")


def check_manifests() -> None:
    kz = bc.load_kind(APP / "kustomization.yaml", "Kustomization")
    for res in ("./password.yaml", "./externalsecret.yaml", "./externalsecret-generated.yaml", "./pushsecret.yaml"):
        bc.assert_true(res in kz["resources"], f"kustomization must include {res}")
    gens = {g["name"]: g for g in kz.get("configMapGenerator", [])}
    bc.assert_true("wizarr-seed" in gens and "seed.py" in gens["wizarr-seed"]["files"], "wizarr-seed ConfigMap from seed.py")
    ctrl = bc.load_kind(APP / "helmrelease.yaml", "HelmRelease")["spec"]["values"]["controllers"]["wizarr"]
    init = ctrl["initContainers"]["seed"]
    bc.assert_true(init["image"] == ctrl["containers"]["app"]["image"], "seed runs on the exact app image")
    env = init["env"]
    refs = {k: v["valueFrom"]["secretKeyRef"] for k, v in env.items() if isinstance(v, dict)}
    want = {"PLEX_TOKEN": "wizarr-seed-secret", "SEERR_API_KEY": "seerr-api-key",
            "WIZARR_ADMIN_PASSWORD": "wizarr-generated", "WIZARR_API_KEY": "wizarr-generated"}
    for key, secret in want.items():
        bc.assert_true(refs.get(key, {}).get("name") == secret, f"{key} must come from secret {secret}, got {refs.get(key)}")
    bc.assert_true(env.get("PLEX_URL") == "http://plex.media.svc.cluster.local:32400", "PLEX_URL")
    bc.assert_true(env.get("SEERR_URL") == "http://seerr.media.svc.cluster.local:5055", "SEERR_URL")
    sec = init.get("securityContext", {})
    bc.assert_true(sec.get("readOnlyRootFilesystem") is True and sec.get("allowPrivilegeEscalation") is False, "init hardening")
    push = bc.load_kind(APP / "pushsecret.yaml", "PushSecret")["spec"]
    props = {d["match"]["remoteRef"]["property"] for d in push["data"]}
    bc.assert_true(props == {"WIZARR_ADMIN_PASSWORD", "WIZARR_API_KEY"}, f"pushed properties {props}")
    bc.assert_true(push["secretStoreRefs"][0]["name"] == "onepassword-automation" and push["deletionPolicy"] == "None", "push store")
    es = bc.load_kind(APP / "externalsecret-generated.yaml", "ExternalSecret")
    bc.assert_true(es["spec"].get("refreshPolicy") == "CreatedOnce", "generated credentials are minted once")


if __name__ == "__main__":
    sys.exit(bc.run([
        ("seed creates admin, Plex, Seerr link, API key", check_seed_creates_everything),
        ("seed is idempotent and follows rotation", check_seed_is_idempotent_and_rotates),
        ("seed aborts on missing env", check_seed_fails_fast_on_missing_env),
        ("manifests wire secrets and push-back", check_manifests),
    ]))
