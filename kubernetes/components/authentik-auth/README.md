# Authentik Auth Component

This Kustomize component adds Authentik forward authentication to an application.

## Usage

Add to your app's kustomization.yaml:

```yaml
components:
  - ../../../../components/authentik-auth

patches:
  - target:
      kind: SecurityPolicy
      name: authentik-auth
    patch: |
      - op: replace
        path: /spec/targetRefs/0/name
        value: YOUR_HTTPROUTE_NAME
```

## Prerequisites

Ensure your namespace is listed in the ReferenceGrant at:
`security/authentik/app/referencegrant.yaml`

## How It Works

This component creates a SecurityPolicy that uses Authentik's forward auth endpoint
to authenticate requests before they reach your application.
