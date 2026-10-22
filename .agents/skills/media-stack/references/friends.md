# Friends: Wizarr invites, Seerr requests, Plex limits

Git holds the Wizarr app, the Seerr and Wizarr routes and the kopiur backups. Everything
below is application state: set it in the app, verify it through the app's API, and never
copy a token or key into Git.

## Exposure
- `seerr.${SECRET_DOMAIN}`: `envoy-external` (Plex login for friends).
- `join.${SECRET_DOMAIN}`: `envoy-external`, invite flow paths only (`/j`, `/join`,
  `/invitation`, `/wizard`, `/static`, `/image-proxy`, `/cinema-posters`, `/reset`,
  `/favicon.ico`, `/plex/callback`). The allow-list is pinned by
  `scripts/ci/wizarr-contract-test.py`; a new invite-flow path needs a route entry.
- `wizarr.${SECRET_DOMAIN}`: `envoy-internal`, the admin UI, LAN or VPN only.

## Current policy (owner, 2026-10-19)
Seerr request quotas and Plex remote limits are unlimited for now: leave quotas unset and do
not set a remote bandwidth cap or stream limit. Revisit when friends are consuming noticeable
bandwidth or storage.

## One-time setup (owner, in the apps)
1. **Wizarr** is seeded by the `seed` init container (`seed.py`, ConfigMap `wizarr-seed`): admin
   `admin`, the Plex server (token from the `plex` 1Password item; decoded length must be
   non-zero), the Seerr connection (key from Secret `seerr-api-key`, Seerr accounts are created
   on invite) and an API key named `hermes`. The generated admin password and API key are
   pushed to the 1Password item `wizarr` (vault Automation). Re-runs are idempotent, a rotated
   Plex token or Seerr key follows on the next pod start, and a password changed in the UI is
   kept. Wizarr has no env provisioning, so the seed writes its tables through its own models:
   re-check `seed.py` against the schema when bumping the image (`wizarr-seed-test.py`). Still
   in the UI: choose the libraries an invite grants, set invite expiry and use limit.
2. **Seerr** Settings > Users: default permissions, request approval mode, movie and series
   quotas (limit and days). Admins bypass quotas.
3. A friend gets one Seerr account, created by Wizarr (the seeded connection). Do not also
   import Plex users in Seerr.
4. **Plex** Settings > Remote Access and Transcoder: remote bandwidth cap and remote
   transcoding. Values are the owner's call; no safe default exists.

## Verify
- `GET https://wizarr.${SECRET_DOMAIN}/health` and `https://join.${SECRET_DOMAIN}/j/invalid`
  respond; `https://join.${SECRET_DOMAIN}/admin` returns 404 from outside.
- Seerr: `GET /api/v1/settings/main` and `GET /api/v1/user` (quota fields) with the admin API key.
- Walkthrough: invite, Plex login, request, approval, title available. Revoke the test invite.

## Stale Error pods
Pods left in `Error` by a node shutdown (`kubectl get pods -n media`) are evictions, not
outages. Delete only those, never the Ready replica.
