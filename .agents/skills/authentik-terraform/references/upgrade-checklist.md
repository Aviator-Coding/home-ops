# Authentik server upgrade

Chart and image move together. Tag is
`kubernetes/apps/base/security/authentik/app/helmrelease.yaml`
(`OCIRepository` `ref.tag`). Current line when this skill was written:
`2026.8.3`. Release lines are quarterly: 2026.2, 2026.5, 2026.8, 2026.11.
There is no 2026.6 or 2026.7.

One line forward is one Django migration set. Migrations are one-way. Confirm
CNPG `database/postgres-17` `Ready`, `ContinuousArchiving` and a recent
successful `Backup` before the bump. The bundled Bitnami postgres subchart is
disabled here. Its image bump is irrelevant.

## Before 2026.11

The Base URL system setting is optional on 2026.8 and mandatory in 2026.11.
Set it on the live instance before that upgrade. It is not a Helm value in
this repo. Confirm it is set (Authentik system settings) rather than assuming
the chart pin implies it.

## Trusted proxies

`AUTHENTIK_LISTEN__TRUSTED_PROXY_CIDRS` is unset, so the default applies
(`10.0.0.0/8` among others). The pod network `10.42.0.0/16` sits inside that
range, which is why Envoy's `X-Forwarded-*` headers stay trusted. A miss
shows up as HTTPS treated as HTTP (redirect loops, mixed content). Re-check
the default if the pod CIDR ever leaves `10.0.0.0/8`.

## After the pods are Ready

Read-only. Expect the same shape as before the bump.

| Surface | Check | Healthy |
|---|---|---|
| hass, kromgo, echo, opencode | `curl -sI` the hostname | 302 to `auth.${SECRET_DOMAIN}` authorize |
| coder | `GET /api/v2/users/authmethods` | `oidc.enabled=true` |
| coder discovery | `https://auth.${SECRET_DOMAIN}/application/o/coder/.well-known/openid-configuration` | issuer and endpoints present |
| authentik | `curl -sI https://auth.${SECRET_DOMAIN}/` | 302 to the default authentication flow |

A 401 or a non-redirect on the outpost apps, or `oidc.enabled=false`, is a
regression. The 302 alone does not prove a brand-new hostname is authorized.
See [inventory.md](inventory.md).

The embedded outpost Service selects the server pod. There is no separate
outpost Deployment to upgrade.

A provider-plugin bump (`terraform-provider-authentik`, often a Renovate PR
on `main.tofu` and `.terraform.lock.hcl`) is not the server upgrade. Re-run
its `tofu plan` after the server is on the line the provider requires. Apply
still needs the go-ahead in [apply-runbook.md](apply-runbook.md).
