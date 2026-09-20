# Repairing the plex and n8n scrape targets (2026-09-20)

PR #1698 made two long-broken exporters scrapeable for the first time. Neither failure was
caused by that PR; both had been silently broken beforehand, and making them scrapeable is
what finally surfaced them. `TargetDown` (`general.rules`, `for: 10m`, `severity: warning`)
has been firing for both since **2026-09-16T12:25Z**, the moment the ServiceMonitors started
selecting targets.

Measured state at the time of this investigation:

```
job=plex-exporter ns=media       up=0  lastError=server returned HTTP status 500 Internal Server Error
job=n8n           ns=selfhosted  up=0  lastError=server returned HTTP status 503 Service Unavailable
```

Both were investigated end to end against the live cluster. One is fixed in Git; one cannot be
fixed from Git and is recorded here with the exact captain action it needs.

---

## 1. n8n - FIXED in Git (and it was never a metrics bug)

### What was actually wrong

`N8N_METRICS=true`, the port, the path and the ServiceMonitor were **all correct**. The 503 did
not come from the metrics subsystem at all. Every route returned it:

```
/healthz              -> 200  {"status":"ok"}
/healthz/readiness    -> 503  {"status":"error"}
/metrics              -> 503  {"code":503,"message":"Database is not ready!"}
/rest/login           -> 503  {"code":503,"message":"Database is not ready!"}
/nonexistent-path-xyz -> 503  {"code":503,"message":"Database is not ready!"}
```

n8n had been **completely down since 2026-09-16 07:13Z** - four days. Not degraded: the UI, the
REST API, webhooks and metrics were all returning 503.

### Mechanism, from n8n 2.34.6's own source

`packages/cli/src/abstract-server.ts`, `setupHealthCheck()` registers a catch-all gate:

```ts
this.app.use((_req, res, next) => {
  if (connectionState.connected) {
    if (connectionState.migrated) next();
    else res.send('n8n is starting up. Please wait');
  } else sendErrorResponse(res, new ServiceUnavailableError('Database is not ready!'));
});
```

`setupHealthCheck()` runs at line 228 (inside `init()`); `/metrics` is registered by
`configure()` at line 329 (`PrometheusMetricsService.init(this.app)` in `server.ts:164-166`).
Express runs middleware in registration order, so **the DB gate precedes `/metrics`** and blocks
it. That is the whole 503.

`connectionState.connected` was latched false by a Postgres blip that morning and never reset.
It was a latch, not an ongoing outage - measured 2026-09-20:

- TCP from the n8n pod to `postgres-17-rw.database.svc.cluster.local:5432` **succeeded**.
- `postgres-17` reported `Cluster in healthy state`, 3/3 instances, primary `postgres-17-1`.
- n8n's own scheduled jobs kept working throughout: `Pruning old insights data` logged on
  09-16, 09-17, 09-18 and 09-19.

So the database was fine and n8n's cached flag was stuck.

### Why nothing caught it

Both the readiness probe and the Gatus check pointed at `/healthz`, which upstream comments as
*"main health check should not care about DB connections"*:

```ts
// main health check should not care about DB connections
this.app.get(healthPath, ...healthMiddlewares, (_req, res) => {
  res.send({ status: 'ok' });
});
```

It is unconditional. **It cannot fail while the process is alive.** Consequences:

| Signal | Reported | Reality |
|---|---|---|
| Pod readiness (`/healthz`) | `1/1 Running`, Ready | every request 503 |
| Gatus `selfhosted/n8n-app` (`/healthz`) | green, HTTP 200 | every request 503 |
| Gatus `selfhosted/n8n-webhooks` (`/webhook/any`, expects 404) | **red, HTTP 503** | correct |
| Prometheus `up{job="n8n"}` | 0, `TargetDown` firing | correct |

The webhook check was the only health signal that told the truth, and it was drowned out by the
green primary check next to it.

### The fix

`kubernetes/apps/base/selfhosted/n8n/app/helmrelease.yaml`:

- **readiness** -> `/healthz/readiness`, which returns 200 only when
  `connected && migrated && fullyReady`. It is a cached in-memory flag read, not a DB query, so
  `timeoutSeconds: 1` remains correct.
- **liveness** stays on `/healthz` **deliberately**. A DB-aware liveness probe would restart
  every DB-dependent pod on each CNPG switchover.
- **startup** stays disabled **deliberately**. Readiness failures never restart a pod, so a slow
  schema migration just holds the pod NotReady until it completes; a startup probe on the
  readiness path would CrashLoop n8n through a long migration instead.
- The Gatus `n8n-app` endpoint moves to `/healthz/readiness` for the same reason.

A latched pod now drops out of the Service endpoints and trips kube-prometheus-stack's
namespace-agnostic `KubePodNotReady` (`for: 15m`, `severity: warning`).

**Proof the new check can fail** (the requirement that a check be shown to go red before its
green is trusted): measured against the live broken pod, `/healthz` returned **200** while
`/healthz/readiness` returned **503**. The new check fails on exactly the state the old one
passed. The rendered Deployment was confirmed with `helm template` against app-template 5.1.0:

```
livenessProbe  -> {'path': '/healthz',           'port': 80}
readinessProbe -> {'path': '/healthz/readiness', 'port': 80}
startupProbe   -> None
```

**The ServiceMonitor was not touched.** It was already correct.

### What merging this does

The manifest change rolls the Deployment, and the replacement pod connects to the (healthy)
database, clearing the latch. `/metrics` is then served normally. Confirm after Flux reconciles:

```sh
kubectl --kubeconfig=<abs path> -n selfhosted port-forward svc/n8n 18000:80
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18000/healthz/readiness   # expect 200
curl -s http://127.0.0.1:18000/metrics | head                                        # expect n8n_* series
```

---

## 2. plex-exporter - NOT fixable from Git

### What is wrong

The exporter returns 500. Its own body names the cause:

```
Puma caught this error: unexpected token at
'<html><head><title>Unauthorized</title></head><body><h1>401 Unauthorized</h1></body></html>'
(JSON::ParserError)
  /srv/lib/middleware/collector.rb:252:in `block in send_plex_api_request'
  /srv/lib/middleware/collector.rb:145:in `collect_session_metrics'
```

It calls Plex, gets a 401 as HTML, and dies parsing that HTML as JSON. The 500 is two layers
downstream of the real fault.

### The token is not dead or wrong - it is ABSENT

This is the distinction that matters, because "dead token" and "no token" have different fixes.

`media/plex-exporter-secret` carries `PLEX_TOKEN` as a **zero-length string**:

```
key=PLEX_TOKEN len=0 stripped_len=0
```

while the ExternalSecret reports `SecretSynced / Ready=True`.

Reading the source item through 1Password Connect (read-only) shows why - the field exists but
holds no value at all:

```
Homelab/plex  (SECURE_NOTE)
  label='notesPlain'  type=STRING     present=False len=0
  label='PLEX_TOKEN'  type=CONCEALED  present=False len=0
```

ESO resolved the property, found nothing, wrote an empty string, and reported success.

Confirmed against Plex directly that an empty token reproduces the exporter's exact failure,
and that Plex itself is healthy:

```
GET /status/sessions?X-Plex-Token=                      -> 401  <html>...Unauthorized...</html>
GET /status/sessions?X-Plex-Token=deadbeefdeadbeefdead  -> 401  <html>...Unauthorized...</html>
GET /identity                    (no auth needed)       -> 200  {"claimed":true,"version":"1.43.1.10611-1e34174b1"}
```

So Plex is up and claimed; nothing is wrong with the server or the exporter image.

### A valid token already exists on the server

The Plex Media Server stores its own working token in `Preferences.xml`, which
`docs/media-stack.md#library-scan-triggers-application-settings-not-gitops` already documents
reading. Verified 2026-09-20 - a 20-character `PlexOnlineToken` is present and works against
the exact endpoint the exporter calls:

```
token_present=yes length=20
GET /status/sessions   -> HTTP 200  {"MediaContainer":{"size":0}}
GET /library/sections  -> HTTP 200  {"MediaContainer":{"size":3,...}}
```

**So no token needs to be minted.** The remaining step is only to copy that value into
1Password, which is a captain action - this repo's rule is that app secrets live in
1Password and reach the cluster through ExternalSecrets, never in Git and never written by
hand into a live Secret.

### Captain action

Populate the **`PLEX_TOKEN` field of the `plex` item in the `Homelab` vault**.

`Homelab` is correct and deliberate: in-cluster ESO reaches the cluster through 1Password
Connect, whose credential can only see `Homelab`, `Automation` and `Services`. The hyphenated
`Home-Lab` vault (used by `vals` for bootstrap/Talos rendering) is **invisible to Connect** -
an ExternalSecret can never read from it.

Read the value the server is already using:

```sh
export KUBECONFIG=<absolute path>   # never the mise-shim-overridden one, see AGENTS.md NOTES
kubectl --kubeconfig=$KUBECONFIG exec -n media deploy/plex -c app -- sh -c \
  'grep -oE "PlexOnlineToken=\"[^\"]+\"" "/config/Library/Application Support/Plex Media Server/Preferences.xml" | cut -d\" -f2'
```

Paste it into `Homelab/plex` -> `PLEX_TOKEN`. No manifest change is needed; the ExternalSecret
already refers to exactly that item and property, and refreshes every 5 minutes.

Verify afterwards:

```sh
kubectl --kubeconfig=$KUBECONFIG -n media get externalsecret plex-exporter     # SecretSynced
kubectl --kubeconfig=$KUBECONFIG -n media get secret plex-exporter-secret \
  -o jsonpath='{.data.PLEX_TOKEN}' | base64 -d | wc -c                         # expect ~20, not 0
kubectl --kubeconfig=$KUBECONFIG -n media rollout restart deploy/plex-exporter # pick up new env
kubectl --kubeconfig=$KUBECONFIG -n media port-forward svc/plex-exporter 19594:9594
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:19594/metrics        # expect 200
```

`TargetDown` for `job=plex-exporter` clears within ~10 minutes of the target returning 200.

### Why the exporter's probes were deliberately NOT changed

The exporter's liveness/readiness/startup probes all target `/`, which returns **200** even
while `/metrics` returns 500. That looks like an obvious bug to fix, and fixing it would make
things worse.

Today the pod is Ready, so it stays in the Service endpoints, Prometheus scrapes it, gets 500,
and records `up=0` - the target is **honestly down** and `TargetDown` fires. Pointing the probes
at `/metrics` would make the pod NotReady, remove it from the endpoints, and delete the target
from Prometheus entirely. `up` would not go to 0; it would **cease to exist**, and `TargetDown`
could no longer fire on it.

That is the opposite of what is wanted here. `up=0` on a live target is the strongest honest
signal available, so the probes stay as they are. (This differs from the n8n case above, where
the app is a user-facing service whose readiness genuinely should gate traffic, and where
`KubePodNotReady` provides the paging signal instead.)

---

## 3. Fleet finding: ESO reports `SecretSynced` for empty values

The plex root cause is not unique. Auditing all **129** ExternalSecrets in the cluster for
zero-length values found **7 secrets** carrying at least one empty key, every one of them
reporting `SecretSynced`:

| Namespace | Secret | Empty keys |
|---|---|---|
| `media` | `plex-exporter-secret` | `PLEX_TOKEN` |
| `downloads` | `bazarr-secret` | `PLEX_TOKEN` |
| `home-automation` | `home-assistant` | `HASS_DARKSKY_API_KEY`, `HASS_ECOBEE_API_KEY`, `HASS_GOOGLE_PROJECT_ID`, `HASS_GOOGLE_SECURE_DEVICES_PIN` |
| `home-automation` | `zigbee2mqtt-secret` | `zigbee_ext_pan_id`, `zigbee_network_key`, `zigbee_pan_id` |
| `coder` | `coder` | `CODER_OIDC_ALLOWED_GROUPS` |
| `ai` | `sklab-dev-production-tls` | `notesPlain` |
| `network` | `sklab-dev-tls` | `notesPlain` |

`downloads/bazarr` reads the **same** `Homelab/plex` -> `PLEX_TOKEN` field, so populating it
repairs Bazarr's Plex integration at the same time as the exporter.

### Counting this precisely (the units differ)

Measured two independent ways that agree: once by walking all 129 ExternalSecrets and resolving
their targets, and once by sweeping all **674** Secrets in the cluster for zero-length values
(the second method avoids any target-name-resolution hole in the first, and it also found the
only non-ESO case, `monitoring/prometheus-kube-prometheus-stack-web-config`, which is
Prometheus-operator-owned and normal).

State the unit when quoting this, because three different numbers are all correct:

- **7 secrets** carry at least one empty key.
- **7 ExternalSecrets** own them (the two `*-tls` ones are ESO-managed but carry no
  `ownerReferences`, so an owner-based count alone would say 5).
- **12 empty keys** in total - of which **9 are credential-shaped**, once the two `notesPlain`
  secure-note artifacts and `coder`'s plausibly-deliberate `CODER_OIDC_ALLOWED_GROUPS` are set
  aside.

### The two `home-automation` entries are NOT the same kind of problem

They were investigated on 2026-09-20 and they differ from each other and from plex:

**`home-assistant` (4 keys) - dead declarations.** Its live `/config` references none of
`HASS_DARKSKY_API_KEY`, `HASS_ECOBEE_API_KEY`, `HASS_GOOGLE_PROJECT_ID` or
`HASS_GOOGLE_SECURE_DEVICES_PIN`; they appear only in the ExternalSecret's own template. Nothing
consumes them, and Dark Sky's API shut down in 2023. This is the same shape as the dead
`APP_UID`/`APP_GID` `postBuild.substitute` pairs AGENTS.md already records: a declared value no
manifest reads is a liability, not documentation. Home Assistant is unaffected today.

**`zigbee2mqtt` (3 keys) - a live latent hazard, not a current fault.** Unlike the above these
ARE wired into live env vars (`ZIGBEE2MQTT_CONFIG_ADVANCED_NETWORK_KEY`, `_PAN_ID`,
`_EXT_PAN_ID`) via `secretKeyRef`, and all three are empty in the running pod. The app is
nonetheless healthy (2/2, 14d) because its **persisted** `/data/configuration.yaml` holds a real
`network_key` and `pan_id: 53625`, and an empty env var does not override it.

The hazard is that the declarative intent - pin the Zigbee network identity from 1Password - is
silently not in force. The values live only on the PVC. If that volume were ever restored empty
or recreated (see the `latestImage` / populator traps in AGENTS.md), zigbee2mqtt would generate
a **new random network key**, and every paired Zigbee device would drop off the network and need
re-pairing. Nothing in the cluster would report an error first.

Neither is changed here - both are outside this task's scope (the two scrape targets) and the
zigbee2mqtt one in particular is a decision about whether to populate the 1Password fields from
the live persisted config or to delete the env wiring, not a mechanical fix.

The generalisable point is the one the plex case proves: **an ExternalSecret reporting
`SecretSynced / Ready=True` does not mean the value is usable.** ESO does not validate that a
resolved property is non-empty, and a missing 1Password field value is indistinguishable from a
deliberately empty one at the API. When an app authenticates with a secret and fails on auth,
check the secret's **length**, not its ExternalSecret status:

```sh
kubectl --kubeconfig=$KUBECONFIG -n <ns> get secret <name> \
  -o jsonpath='{.data.<KEY>}' | base64 -d | wc -c
```

A standing detector for this (a periodic sweep alerting on zero-length keys in ESO-owned
Secrets, in the shape of `kubernetes/apps/base/system/pvc-writable-check`) would be the natural
follow-up. It is deliberately **not** built here: that is a new always-on cluster component,
and the direct path - fixing the one field that is actually broken - resolves both live
consumers today. It is recorded as an option, not a pending task.
