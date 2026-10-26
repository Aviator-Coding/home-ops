# Home Assistant

## Address

Multus network `multus-iot` in `kube-system`, static IP `10.40.0.100/16`.
No MAC address in the annotation. A pinned MAC on `bond0.3` makes the kernel
reject the new pod's `net1` while the dying pod's veth still holds that MAC
(`address already in use`). A fresh MAC each cycle avoids the race. Devices
on the IoT VLAN key on the IP, so the MAC must stay unpinned.

Trusted proxies are the LAN, the cluster service range, and the pod range
already set in the HelmRelease (`HASS_HTTP_TRUSTED_PROXY_*`,
`HASS_HTTP_USE_X_FORWARDED_FOR`). Do not drop them when editing env.

## Probes

Liveness and readiness are `GET /manifest.json` on port 8123. That path is
served without a session, so the probe measures Home Assistant rather than
the ext_authz policy on the public HTTPRoute. A check that hits the public
hostname can stay green while the app is down. Skill `observability`.

Startup delays liveness and readiness (the probe block's startup threshold)
so a slow integration load is not marked dead. Once the pod can go unready,
the stock `KubePodNotReady` rule covers it. Do not add a second "HA is down"
PrometheusRule.

## Authentik sign-in (auth_oidc)

The public route `hass.${SECRET_DOMAIN}` is **not** behind Authentik ExtAuth. A
forward-auth gate redirects `/auth/authorize`, `/auth/token` and the websocket
to a browser login, which breaks the companion app, Google linking and every
client that uses Home Assistant's own token flow. Authentik signs in inside
Home Assistant instead, through the `hass-oidc-auth` custom component. The
`hass-code` route stays ExtAuth-gated because code-server runs `--auth none`.

- Provider: `terraform/authentik/home-assistant.tofu`. Public client + PKCE, no
  secret. `client_id` there and in `app/resources/auth_oidc.yaml` must match.
- Settings: `app/resources/auth_oidc.yaml` (git), mounted at
  `/config/auth_oidc.yaml`. `/config/configuration.yaml` on the PVC needs the
  one line `auth_oidc: !include auth_oidc.yaml`; that file is not in git.
- Component: the `install-oidc` init container pins `OIDC_VERSION` (Renovate
  annotated) and unpacks it into `/config/custom_components/auth_oidc`.
- Local username/password login stays enabled as the fallback. Do not remove it.
- The mobile app signs in with the component's device-code flow.
- A new Authentik login creates a new HA user unless
  `features.automatic_user_linking` is on, which also skips MFA. Use it once,
  then turn it off.
- Never re-add a SecurityPolicy to `home-assistant-app`.

The code-server sidecar is not the probe target. It can answer HTTP while
Home Assistant is wedged.
