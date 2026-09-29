# ToolHive

API group `toolhive.stacklok.dev/v1alpha1`. Kinds in use: `MCPServer`,
`VirtualMCPServer`, `EmbeddingServer`, `MCPGroup`, `MCPTelemetryConfig`.

## Session embed

vmcp's optimizer re-embeds the whole aggregated catalog on every new
session, inside `initialize`: 449 tool texts, chunks of 32, each with a
30s HTTP timeout (toolhive 0.42.1). Hermes' connect budget is 30s.

`Qwen/Qwen3-Embedding-0.6B` on CPU took 487.5s per catalog (worst chunk
65s) and could not finish inside that budget. `/health` stayed 200 the
whole time. The shipped model is `BAAI/bge-small-en-v1.5` (the CRD
default): 6.0s per catalog, worst chunk 1.0s, 0 of 449 texts over its
512-token input. Resources on that server: request 1 CPU / 1Gi, limit
4 CPU / 2Gi. The 1 CPU request is what keeps one session's ~23.5
core-seconds inside 30s when the node is saturated. Size any replacement
by per-session latency, and assert every vector is finite and non-zero
(skill `b70-llm-serving`).

Gatus check `ToolHive MCP Session` POSTs `initialize` to the same URL
Hermes uses, 25s client timeout, and asserts `serverInfo`. Gate:
`scripts/ci/toolhive-session-probe-test.py`.

**Known gap.** A fast embed failure (TEI refusing connections) still
answers `initialize` 200 and then terminates the session. Gatus cannot
replay `Mcp-Session-Id` (it only stores body values). A down TEI pod is
still `KubePodNotReady`. A pod that is up and returns errors is not
covered.

## Gateway auth

`https://mcp.${SECRET_DOMAIN}` requires `Authorization: Bearer <token>`.
Everything except `/health` (open for Gatus) returns 401 without it.
Envoy enforces it (`config/securitypolicy.yaml`, `apiKeyAuth` on HTTPRoute
rule `mcp`), not vmcp. vmcp 0.42.1 accepts `anonymous`, `local` or `oidc`
only, and its OIDC validator needs a signed JWT with `exp` or an RFC 7662
introspection endpoint. A static token cannot pass either. So
`incomingAuth` stays `anonymous`, and `config/networkpolicy.yaml` is what
closes the in-cluster Service `vmcp-mcp-gateway-internal:4483`: it admits
4483 only from the `envoy-internal` proxy pods (label
`gateway.envoyproxy.io/owning-gateway-name`) and from Prometheus, which
scrapes `/metrics` there (nothing listens on 8080).
A new in-cluster consumer must use the LAN route with the token, not the
Service. Removing the policy reopens the token bypass.

- Token: ESO `Password` generator + ExternalSecret `mcp-gateway-token`
  (`refreshPolicy: CreatedOnce`), pushed to 1Password vault `Automation`,
  item `mcp-gateway`, field `MCP_GATEWAY_TOKEN`
  (`config/pushsecret.yaml`, `deletionPolicy: None`).
- Consumer: read that item with an ExternalSecret (`extract: mcp-gateway`)
  and send the header. Hermes, opencode and Gatus do, each with
  `MCP_GATEWAY_URL` beside the token. A LAN client copies the field from
  1Password.
- Rotate: delete ExternalSecret `ai/mcp-gateway-token`. A new token is
  minted and pushed over the field, and Envoy picks it up without a
  restart. Consumers follow on their ExternalSecret refresh (Hermes 1h,
  Gatus 5m) and Reloader.
- Rule names on the HTTPRoute are load-bearing: renaming `mcp` detaches
  the policy.

## Do not point vmcp at `ai/embedding-gpu`

Measured and rejected:

- vmcp's OpenAI client sends the whole catalog in one request
  (`openAIMaxBatchSize` 2048). `batch_size=4` is the measured-safe point
  on that pod; larger batches OOM. The 449-input request was not sent.
- At batch 4, one of three full-catalog passes returned NaN. Go's JSON
  decoder turns `null` into `0` for float32 with no error, so tool search
  would degrade silently.
- The pod is `priorityClassName: embedding-gpu-low` and shares the B70
  with chat. Every session would be a 449-text burst. Skill
  `b70-llm-serving` for the rate curve.
- The operator rejects `openai` together with `embeddingServerRef`.
- Wall time at batch 4 was 5.4s versus 6.0s for bge-small. Quality on
  the five checked queries was not better once vmcp's BM25 blend is in
  the path. Queries are sent without Qwen3's instruct prefix.

## Deactivated servers

Commented out of `mcp-servers/kustomization.yaml`. Files stay.

| Server | Blocker |
|---|---|
| `garmin-connect-mcp` | 1Password item `garmin`, fields `username` and `password` |
| `ha-mcp` | item `home-assistant`, field `HOMEASSISTANT_TOKEN` |
| `seerr-mcp` | item `seerr`, field `SEERR_API_KEY` |
| `talos-mcp` | Talos API allowlist. See below |

Uncommenting a row before the secret exists leaves the server unable to
start. Skill `secrets-1password`: `SecretSynced` does not mean the value
is non-empty.

### talos-mcp

`ServiceAccount` `talos-mcp-talosconfig` at role `os:reader`, secret of
the same name mounted at `/var/run/secrets/talos.dev`. The controller
rejects namespace `ai` with `ErrNamespaceNotAllowed`. Live objects are
absent on purpose.

Re-enable, in order:

1. In `talos/machineconfig.yaml.j2`
   `features.kubernetesTalosAPIAccess`: add `ai` to
   `allowedKubernetesNamespaces` (today `actions-runner-system` and
   `system-upgrade`) and `os:reader` to `allowedRoles` (today `os:admin`
   and `os:operator`).
2. `just talos apply-node` on talos-1, talos-2, and talos-3. Flux will
   not do this. Skill `talos-nodes`. Confirm the template pins match the
   live nodes before applying.
3. Uncomment `./talos-mcp` in the kustomization.

## Resources and reloads

`MCPServer.spec.resources` is applied by the operator to the **proxy**
Deployment. It does not bound the MCP container. Read the rendered
Deployment before trusting a limit written on the CR.

grafana-mcp: `GRAFANA_SERVICE_ACCOUNT_TOKEN` is injected into the MCP
StatefulSet. The CRD has no StatefulSet override hook. Reloader's
annotation has to be on `spec.podTemplateSpec.metadata.annotations`
(`secret.reloader.stakater.com/reload: toolhive-grafana`). Putting it
on `resourceOverrides.proxyDeployment` does not roll the consumer.
Removing it leaves a rotated token stale until some other rollout.

## Flux MCP is read-only

`flux-operator-mcp` runs with `--read-only` and its ServiceAccount has
only `kubectl-mcp-readonly`; the Flux write ClusterRole is gone. Flux
controllers apply as cluster-admin, so a gateway token must not reach
apply or delete. Flux changes ship by pull request. Do not add the write
grant back.

## vmcp memory

The vmcp container limit is 2Gi (request 256Mi), set on
`VirtualMCPServer.spec.podTemplateSpec` in `config/virtualmcpserver.yaml`.
At 1Gi it was OOMKilled: the working set climbs about 680MiB/day.
`MCPGatewayMemoryGrowth` (48h linear projection above the limit) and
`MCPGatewayMemoryNearLimit` (over 80%) live in `config/prometheusrule.yaml`.

## kubectl-mcp RBAC

`ClusterRole/kubectl-mcp-readonly` carries an `aggregationRule` and no
rules of its own. The controller overwrites `rules` with the union of
selected roles. Hand-written grants go on `kubectl-mcp-readonly-base`,
selected by
`rbac.home-operations.com/aggregate-to-kubectl-mcp-readonly: "true"`.
The binding targets `kubectl-mcp-readonly`, not the base. Core `secrets`
and exec/proxy subresources stay off the list.
