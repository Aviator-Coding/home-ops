# konflate

Read-only Flux PR-review UI (`home-operations/konflate`). Captain-approved
pilot on 2026-08-29.

- **Write-back is off.** `config.statusChecks` and `config.prComments` are
  `false`. Do not add `secret.writeToken`, `secret.appPrivateKey`, or
  `config.appClientId`.
- **No GitHub credential in-cluster.** This repo is public, so konflate lists
  PRs anonymously. A read-only token may be added later for rate limits; a
  write credential may not.
- **Internal only.** `HTTPRoute` attaches to `envoy-internal` and publishes
  only the internal DNS record.
- **Flux entry point** is `kubernetes/clusters/main` (`config.clusterPath`),
  matching `FluxInstance` `sync.path` and `flux-local --path`.

The chart is single-replica with a Recreate strategy and a RWO cache PVC.
Do not raise `replicaCount`.
