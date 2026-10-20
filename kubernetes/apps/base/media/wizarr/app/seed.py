"""Idempotent first-boot seed for Wizarr, run by the `seed` init container after the DB migration.

Wizarr has no env-based provisioning: the admin account, media server, integrations and API keys are database rows
that its setup wizard creates. This script writes the same rows through Wizarr's own models, from GitOps secrets.
Skill media-stack references/friends.md.

Re-runs are safe. The Plex token and Seerr key follow rotation; an admin password changed in the UI is left alone.
Libraries and invite design stay in the UI. A missing env var aborts the seed (KeyError) before any row is written.
"""

import hashlib
import os

SERVER_NAME = "Plex"
CONNECTION_NAME = "Seerr"
API_KEY_NAME = "hermes"


def _upsert(model, lookup, **fields):
    row = model.query.filter_by(**lookup).first()
    created = row is None
    if created:
        row = model(**lookup)
    for key, value in fields.items():
        setattr(row, key, value)
    return row, created


def seed(models, session, env) -> list[str]:
    cfg = {k: env[k] for k in ("WIZARR_ADMIN_USERNAME", "WIZARR_ADMIN_PASSWORD", "WIZARR_API_KEY",
                               "PLEX_URL", "PLEX_TOKEN", "SEERR_URL", "SEERR_API_KEY")}
    done = []

    admin = models.AdminAccount.query.filter_by(username=cfg["WIZARR_ADMIN_USERNAME"]).first()
    if admin is None:
        admin = models.AdminAccount(username=cfg["WIZARR_ADMIN_USERNAME"])
        admin.set_password(cfg["WIZARR_ADMIN_PASSWORD"])
        session.add(admin)
        done.append("admin")
    # Wizarr redirects every page to /setup until this legacy row holds the admin name.
    legacy, _ = _upsert(models.Settings, {"key": "admin_username"}, value=cfg["WIZARR_ADMIN_USERNAME"])
    session.add(legacy)

    server, created = _upsert(models.MediaServer, {"name": SERVER_NAME}, server_type="plex",
                              url=cfg["PLEX_URL"], api_key=cfg["PLEX_TOKEN"], verified=True)
    session.add(server)
    done.append("plex-created" if created else "plex-synced")
    session.flush()

    conn, created = _upsert(models.Connection, {"connection_type": "overseerr", "media_server_id": server.id},
                            name=CONNECTION_NAME, url=cfg["SEERR_URL"], api_key=cfg["SEERR_API_KEY"],
                            provision_plex_users=True)
    session.add(conn)
    done.append("seerr-created" if created else "seerr-synced")

    key_hash = hashlib.sha256(cfg["WIZARR_API_KEY"].encode()).hexdigest()
    if models.ApiKey.query.filter_by(key_hash=key_hash).first() is None:
        session.flush()
        session.add(models.ApiKey(name=API_KEY_NAME, key_hash=key_hash, created_by_id=admin.id, is_active=True))
        done.append("api-key")
    session.flush()
    return done


def main() -> None:
    from app import create_app, models
    from app.config import ProductionConfig
    from app.extensions import db

    with create_app(ProductionConfig).app_context():
        done = seed(models, db.session, os.environ)
        db.session.commit()
    print("[wizarr-seed] " + ", ".join(done))


if __name__ == "__main__":
    main()
