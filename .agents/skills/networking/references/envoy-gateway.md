# Envoy Gateway

Chart pin is the `OCIRepository` tag in
`kubernetes/apps/base/network/envoy-gateway/app/ocirepository.yaml` (v1.9.2
when this skill was written). Class `envoy`. Gateways `envoy-internal`
(`10.50.0.26`) and `envoy-external` (`10.50.0.21`) are in `app/envoy.yaml`.

## Resources path

Controller requests and limits belong under:

```yaml
deployment:
  envoyGateway:
    resources: ...
```

`config.envoyGateway` is the EnvoyGateway config document. A `resources` key
there is ignored and the controller stays on chart defaults.

## GatewayNamespaceMode

`config.envoyGateway.provider.kubernetes.deploy.type` is `GatewayNamespace`.
Proxies run in the namespace of the Gateway and authenticate to xDS with their
own ServiceAccount tokens.

When the controller is down long enough for those tokens to expire, the
proxies retry auth in a loop. That storm is what liveness-killed the
controller (exit 137) while memory sat far under the limit. Recovery order:
bring the controller up, then restart the stale proxy Deployments so they
mint fresh tokens. Memory headroom does not clear the storm.
`app/prometheusrule.yaml` is the detector.

## HTTPRouteFilter

`gateway.envoyproxy.io/v1alpha1` `HTTPRouteFilter` with
`spec.directResponse.statusCode` answers before any backend. Reference it
from a rule:

```yaml
filters:
  - type: ExtensionRef
    extensionRef:
      group: gateway.envoyproxy.io
      kind: HTTPRouteFilter
      name: <filter>
```

That rule has no `backendRefs`. Gateway API path matching is most-specific
wins, independent of rule order, so a narrow `PathPrefix` coexists with a
catch-all `/` on the same route.

This is the outer layer only. It never sees in-cluster Service DNS. Closing
a Service path is the backend's own config (LiteLLM
`pass_through_endpoints`, skill `litellm-proxy`).

A static bearer token uses `SecurityPolicy.spec.apiKeyAuth` with
`extractFrom.headers: [Authorization]`. Envoy strips the `Bearer ` prefix
before comparing. `targetRefs[].sectionName` scopes it to one named route
rule, so a health path can stay open. The same outer-layer limit applies:
callers of the Service bypass it. Example: `ai/toolhive/config`
(skill `ai-stack`).

agentgateway uses `AgentgatewayPolicy.spec.traffic.directResponse` on
`agentgateway.dev` routes. An Envoy `HTTPRouteFilter` does not apply there.

## Envoy preStop patch

The gateway-helm CRD does not expose `envoyDeployment.container.lifecycle`
or `pod.terminationGracePeriodSeconds`, so `app/envoy.yaml` carries a
strategic-merge `patch` that replaces the chart-injected envoy `preStop`
(`httpGet :19002/shutdown/ready`). That probe polls the shutdown-manager
sidecar; once the sidecar finishes its own drain (`--drain-timeout=180s`)
and exits, port 19002 disappears and kubelet records `FailedPreStopHook`.
A passive sleep lets the shutdown-manager drain run uninterrupted, and envoy
still drains via `spec.shutdown.drainTimeout`.
