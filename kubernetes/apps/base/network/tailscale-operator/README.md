# Tailscale operator

Puts selected cluster services on the tailnet. Runs in `network`; chart is the
home-operations mirror (`oci://ghcr.io/home-operations/charts-mirror/tailscale-operator`).

| Piece | What it is |
|---|---|
| `app/` | Operator HelmRelease, OCIRepository, ExternalSecret `operator-oauth` |
| `config/` | ProxyGroup `ts-ingress` (2 shared proxy pods, tag `tag:k8s`), applied after the CRDs exist |
| Overlay | `kubernetes/apps/main/network/tailscale-operator.yaml` (two Flux Kustomizations) |

The API server proxy is off (`apiServerProxyConfig.mode: "false"`): no kubectl over the tailnet.

## One-time Tailscale setup

1. **DNS**: enable MagicDNS and HTTPS certificates (Admin console > DNS).
2. **ACL policy** (Access controls) needs the tags and the Services auto-approver:
   ```json
   "tagOwners": {
     "tag:k8s-operator": [],
     "tag:k8s": ["tag:k8s-operator"]
   },
   "autoApprovers": {
     "services": {"tag:k8s": ["tag:k8s"]}
   }
   ```
   Also make sure a grant lets your users reach `tag:k8s` (the default allow-all
   policy already does).
3. **OAuth client** (Settings > Trust credentials > + Credential > OAuth), scopes
   with **Write**, each tagged `tag:k8s-operator`:
   - Devices > Core
   - Keys > Auth Keys
   - General > Services
4. **1Password**: item `tailscale` in a Connect-visible vault (`Homelab`, not
   `Home-Lab`) with fields `client_id` and `client_secret`. `SecretSynced` does
   not mean the fields are non-empty; check the decoded key length if the
   operator fails to log in.

## Verify

```bash
kubectl get externalsecret -n network operator-oauth
kubectl get deploy,pods -n network | grep -i -E 'operator|ts-ingress'
kubectl get proxygroup ts-ingress          # READY / status
```
`tailscale-operator` (tag `tag:k8s-operator`) must appear under Machines.

## Expose a service

Exposed services become `tag:k8s` devices named `<hostname>.<tailnet>.ts.net`.
Proxy pods are created in `network`, whichever namespace the service is in.
No exposure is configured by this app; add the annotations to the app that owns
the service.

### HTTP/HTTPS (Plex, web UIs): Ingress

```yaml
apiVersion: networking.k8s.io/v1
kind: Ingress
metadata:
  name: plex-tailscale
  annotations:
    tailscale.com/proxy-group: ts-ingress   # share proxies; omit for a dedicated proxy pod
spec:
  ingressClassName: tailscale
  defaultBackend:
    service:
      name: plex
      port:
        number: 32400
  tls:
    - hosts: [plex]      # becomes https://plex.<tailnet>.ts.net
```
The proxy terminates TLS with a real certificate and forwards plain HTTP to the
Service. Add `tailscale.com/funnel: "true"` only to publish to the public internet.

### TCP (database, anything non-HTTP): annotate the Service

Simplest, no Service type change; creates one dedicated proxy pod per Service:
```yaml
metadata:
  annotations:
    tailscale.com/expose: "true"
    tailscale.com/hostname: postgres      # postgres.<tailnet>.ts.net:5432
```
Alternative with shared proxies: `type: LoadBalancer`, `loadBalancerClass: tailscale`
and `tailscale.com/proxy-group: ts-ingress`. Verify this on the first use, because the
ProxyGroup path for plain TCP is newer than the per-Service one.

Restrict who can reach a database with a grant on `tag:k8s` ports in the ACL
policy, not just by obscurity of the hostname.

### Cluster to tailnet (egress)

```yaml
apiVersion: v1
kind: Service
metadata:
  name: nas
  annotations:
    tailscale.com/tailnet-fqdn: nas.<tailnet>.ts.net
spec:
  type: ExternalName
  externalName: placeholder   # the operator rewrites this
```

### Sharing with people outside your tailnet

Admin console > Machines > the exposed device (for example `plex`) > Share.
The recipient sees only that node. Revoke it from the same menu.

## Gotchas

- A `ProxyGroupPolicy` can restrict which namespaces may use a ProxyGroup. If an
  Ingress or Service with the `proxy-group` annotation is rejected, check for one.
- Proxy pods are privileged-ish (`NET_ADMIN`); the namespace Pod Security
  labels are warn/audit only, so expect warnings, not rejections.
- ProxyGroup `tags` cannot change after the devices are created; recreate the group.
- Operator and proxy images are pinned to `ghcr.io/tailscale/*` to avoid Docker Hub
  limits. Chart bumps move the default image tag with them (Renovate bumps the
  OCIRepository).
- Retiring: delete exposed Ingress/Service annotations first, then the app, so the
  operator can clean up devices in the tailnet. Stale devices can be removed by hand.
