# 1Password Setup Guide for Organisation Services

This guide covers creating 1Password items for each new organisation service that will be deployed to the Home-Ops Kubernetes cluster. Each item stores secrets that are pulled into the cluster via `ExternalSecret` resources backed by the `onepassword` ClusterSecretStore.

Vaults (case-sensitive):

- ESO ClusterSecretStore `onepassword` uses `Homelab`, `Automation`, and `Services`.
- Bootstrap/Talos `vals` uses a different vault, `Home-Lab` (`ref+op://Home-Lab/...`). Do not mix them.

PostgreSQL hostname is **not** a 1Password field. App ExternalSecrets pin
`postgres-17-rw.database.svc.cluster.local` in the template (GitOps hardcode after
the 2026-05-31 1Password/ESO cutover and later postgres-host pinning). Do not add
`POSTGRES_DB_HOST` to new items.

---

## Prerequisites

1. **1Password vault access** -- You must have write access to the 1Password vault connected to the cluster's `onepassword` ClusterSecretStore (`Homelab` for these org services).
2. **CloudNative-PG superuser password** -- The `cloudnative-pg` 1Password item must already exist and contain the `POSTGRES_SUPER_PASS` field. This is the superuser password for the `postgres-17` cluster in the `database` namespace. You do not need to duplicate it into each service's item; the ExternalSecret pulls from both the app item and the `cloudnative-pg` item automatically.
3. **`op` CLI (optional)** -- If you prefer command-line creation, install the [1Password CLI](https://developer.1password.com/docs/cli/get-started/) and sign in.

---

## How ExternalSecrets and postgres-init Work Together

Each service that uses PostgreSQL follows this pattern:

1. The app's **ExternalSecret** extracts fields from _two_ 1Password items:
    - The app's own item (e.g., `linkwarden`) -- contains DB name, user, password, and any app-specific secrets.
    - The shared `cloudnative-pg` item -- contains `POSTGRES_SUPER_PASS`.

2. The ExternalSecret templates these into a Kubernetes Secret with both the app's env vars and the **`INIT_POSTGRES_*`** env vars consumed by the `postgres-init` init container. `INIT_POSTGRES_HOST` is hardcoded to `postgres-17-rw.database.svc.cluster.local` in the template, not mapped from a 1Password field.

3. The `postgres-init` container (`ghcr.io/home-operations/postgres-init`) uses the superuser password to connect to the PostgreSQL cluster, create the database and role if they don't exist, and grant permissions -- all before the main app container starts.

### Common postgres-init Fields Explained

| Field                      | Purpose                                                                  |
| -------------------------- | ------------------------------------------------------------------------ |
| `INIT_POSTGRES_DBNAME`     | Database name to create (mapped from `POSTGRES_DB_NAME`)                 |
| `INIT_POSTGRES_HOST`       | PostgreSQL host (hardcoded `postgres-17-rw.database.svc.cluster.local`)  |
| `INIT_POSTGRES_USER`       | Database role/user to create (mapped from `POSTGRES_DB_USER_NAME`)       |
| `INIT_POSTGRES_PASS`       | Password for the database role (mapped from `POSTGRES_DB_USER_PASSWORD`) |
| `INIT_POSTGRES_SUPER_PASS` | Superuser password for admin operations (from `cloudnative-pg` item)     |

---

## Service Setup Steps

### 1. linkwarden

- **1Password item name**: `linkwarden`
- **Namespace**: `selfhosted`

**Generate secrets first:**

```bash
# NEXTAUTH_SECRET -- 32-byte hex string
openssl rand -hex 32

# POSTGRES_DB_USER_PASSWORD -- strong random password
openssl rand -base64 24
```

**Create the 1Password item with these fields:**

| Field Name                  | Value                                   |
| --------------------------- | --------------------------------------- |
| `NEXTAUTH_SECRET`           | _(output of `openssl rand -hex 32`)_    |
| `POSTGRES_DB_NAME`          | `linkwarden`                            |
| `POSTGRES_DB_USER_NAME`     | `linkwarden`                            |
| `POSTGRES_DB_USER_PASSWORD` | _(output of `openssl rand -base64 24`)_ |

`NEXTAUTH_URL` is not a secret. The HelmRelease sets `NEXTAUTH_URL: "https://links.${SECRET_DOMAIN}"`.

> **Note**: `POSTGRES_SUPER_PASS` is NOT stored in this item. It is pulled from the existing `cloudnative-pg` item via a second `dataFrom` extract in the ExternalSecret.

**CLI creation (optional):**

```bash
NEXTAUTH_SECRET=$(openssl rand -hex 32)
DB_PASS=$(openssl rand -base64 24)

op item create \
  --category=login \
  --title="linkwarden" \
  --vault="Homelab" \
  "NEXTAUTH_SECRET=$NEXTAUTH_SECRET" \
  "POSTGRES_DB_NAME=linkwarden" \
  "POSTGRES_DB_USER_NAME=linkwarden" \
  "POSTGRES_DB_USER_PASSWORD=$DB_PASS"
```

---

### 2. immich

- **1Password item name**: `immich`
- **Namespace**: `media`

**Generate secrets first:**

```bash
# IMMICH_SECRET_KEY -- 32-byte hex string
openssl rand -hex 32

# POSTGRES_DB_USER_PASSWORD -- strong random password
openssl rand -base64 24
```

**Create the 1Password item with these fields:**

| Field Name                  | Value                                   |
| --------------------------- | --------------------------------------- |
| `POSTGRES_DB_NAME`          | `immich`                                |
| `POSTGRES_DB_USER_NAME`     | `immich`                                |
| `POSTGRES_DB_USER_PASSWORD` | _(output of `openssl rand -base64 24`)_ |
| `IMMICH_SECRET_KEY`         | _(output of `openssl rand -hex 32`)_    |

The ExternalSecret maps Immich `DB_PASSWORD` from `POSTGRES_DB_USER_PASSWORD`. A separate `DB_PASSWORD` field in 1Password is unused.

> **Note**: `POSTGRES_SUPER_PASS` comes from the `cloudnative-pg` item.

**CLI creation (optional):**

```bash
IMMICH_SECRET=$(openssl rand -hex 32)
DB_PASS=$(openssl rand -base64 24)

op item create \
  --category=login \
  --title="immich" \
  --vault="Homelab" \
  "POSTGRES_DB_NAME=immich" \
  "POSTGRES_DB_USER_NAME=immich" \
  "POSTGRES_DB_USER_PASSWORD=$DB_PASS" \
  "IMMICH_SECRET_KEY=$IMMICH_SECRET"
```

---

### 3. paperless-ngx

- **1Password item name**: `paperless-ngx`
- **Namespace**: `selfhosted`

**Generate secrets first:**

```bash
# PAPERLESS_SECRET_KEY -- 32-byte hex string
openssl rand -hex 32

# PAPERLESS_ADMIN_PASSWORD -- strong admin password
openssl rand -base64 18

# POSTGRES_DB_USER_PASSWORD -- strong random password
openssl rand -base64 24
```

**Create the 1Password item with these fields:**

| Field Name                  | Value                                        |
| --------------------------- | -------------------------------------------- |
| `PAPERLESS_SECRET_KEY`      | _(output of `openssl rand -hex 32`)_         |
| `PAPERLESS_ADMIN_USER`      | `admin` _(or your preferred admin username)_ |
| `PAPERLESS_ADMIN_PASSWORD`  | _(output of `openssl rand -base64 18`)_      |
| `POSTGRES_DB_NAME`          | `paperless`                                  |
| `POSTGRES_DB_USER_NAME`     | `paperless`                                  |
| `POSTGRES_DB_USER_PASSWORD` | _(output of `openssl rand -base64 24`)_      |

> **Note**: `POSTGRES_SUPER_PASS` comes from the `cloudnative-pg` item.

**CLI creation (optional):**

```bash
SECRET_KEY=$(openssl rand -hex 32)
ADMIN_PASS=$(openssl rand -base64 18)
DB_PASS=$(openssl rand -base64 24)

op item create \
  --category=login \
  --title="paperless-ngx" \
  --vault="Homelab" \
  "PAPERLESS_SECRET_KEY=$SECRET_KEY" \
  "PAPERLESS_ADMIN_USER=admin" \
  "PAPERLESS_ADMIN_PASSWORD=$ADMIN_PASS" \
  "POSTGRES_DB_NAME=paperless" \
  "POSTGRES_DB_USER_NAME=paperless" \
  "POSTGRES_DB_USER_PASSWORD=$DB_PASS"
```

---

### 4. obsidian-livesync

- **1Password item name**: `obsidian-livesync`
- **Namespace**: `selfhosted`

**Generate secrets first:**

```bash
# COUCHDB_PASSWORD -- strong password
openssl rand -base64 24

# COUCHDB_SECRET -- random UUID for cookie auth
uuidgen
```

**Create the 1Password item with these fields:**

| Field Name         | Value                                   |
| ------------------ | --------------------------------------- |
| `COUCHDB_USER`     | `admin`                                 |
| `COUCHDB_PASSWORD` | _(output of `openssl rand -base64 24`)_ |
| `COUCHDB_SECRET`   | _(output of `uuidgen`)_                 |

> **Note**: This service does NOT use PostgreSQL, so there are no `POSTGRES_*` or `INIT_POSTGRES_*` fields.

**CLI creation (optional):**

```bash
COUCH_PASS=$(openssl rand -base64 24)
COUCH_SECRET=$(uuidgen)

op item create \
  --category=login \
  --title="obsidian-livesync" \
  --vault="Homelab" \
  "COUCHDB_USER=admin" \
  "COUCHDB_PASSWORD=$COUCH_PASS" \
  "COUCHDB_SECRET=$COUCH_SECRET"
```

---

### 5. ntfy (ConfigMap-only; no 1Password item)

ntfy auth is a ConfigMap (`kubernetes/apps/base/selfhosted/ntfy/app/configmap.yaml`):
`auth-default-access: read-write`. There is no ntfy ExternalSecret. Creating an
`NTFY_AUTH_DEFAULT_ACCESS` 1Password item would do nothing unless/until auth is
wired through ESO.

---

## Verification

After creating all 1Password items and deploying the ExternalSecret manifests, verify that secrets are syncing correctly.

### Check ExternalSecret sync status

```bash
# List all ExternalSecrets across all namespaces
kubectl get externalsecret -A

# Expected output for healthy secrets (namespaces match GitOps):
# NAMESPACE    NAME                   STORE        REFRESH   STATUS         READY
# selfhosted   linkwarden-secret      onepassword  5m        SecretSynced   True
# media        immich-secret          onepassword  5m        SecretSynced   True
# selfhosted   paperless-ngx-secret   onepassword  5m        SecretSynced   True
# selfhosted   obsidian-livesync-...  onepassword  5m        SecretSynced   True
```

### Check individual ExternalSecret details

```bash
# Describe a specific ExternalSecret for detailed sync info
kubectl describe externalsecret <app-name>-secret -n <namespace>
```

### Common issues

| Symptom                      | Cause                                           | Fix                                                                                                                                               |
| ---------------------------- | ----------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| `STATUS: SecretSyncedError`  | 1Password item not found or field name mismatch | Verify the item title matches the `key:` in `dataFrom.extract`, and all field names match exactly                                                 |
| `READY: False`               | ClusterSecretStore unreachable                  | Check the `onepassword` ClusterSecretStore and the 1Password Connect server pods                                                                  |
| Pod stuck in Init            | `postgres-init` failing                         | Check init container logs: `kubectl logs <pod> -c init-db -n <ns>`. Usually means `POSTGRES_SUPER_PASS` is wrong or the PG cluster is unreachable |
| Secret exists but app errors | Field name mismatch in ExternalSecret template  | Compare the app's expected env var names with what the ExternalSecret template produces                                                           |

### Verify the Kubernetes Secret was created

```bash
# Check that the secret exists and has the expected keys
kubectl get secret <app-name>-secret -n <namespace> -o jsonpath='{.data}' | jq 'keys'
```

---

## Reference: Existing `cloudnative-pg` Item

The `cloudnative-pg` 1Password item is shared across all PostgreSQL-backed services. It must contain:

| Field Name            | Purpose                                                         |
| --------------------- | --------------------------------------------------------------- |
| `POSTGRES_SUPER_PASS` | Superuser password for the `postgres-17` CloudNative-PG cluster |

This item already exists and should not be modified. Every ExternalSecret for a PostgreSQL-backed service includes:

```yaml
dataFrom:
    - extract:
          key: <app-name> # App's own secrets
    - extract:
          key: cloudnative-pg # Shared superuser password
```

This pattern ensures the `postgres-init` container receives `INIT_POSTGRES_SUPER_PASS` without duplicating the superuser password across individual service items.

Connect server: `onepassword-connect.security.svc.cluster.local:8080`.
ClusterSecretStore name: `onepassword`.
