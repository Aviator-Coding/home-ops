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

The code-server sidecar is not the probe target. It can answer HTTP while
Home Assistant is wedged.
