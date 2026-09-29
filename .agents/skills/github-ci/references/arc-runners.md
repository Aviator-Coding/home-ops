# ARC runners

Namespace `actions-runner-system`. Image and chart pins live in the HelmReleases
(Renovate bumps them). Do not copy a tag out of prose.

| HelmRelease | Repo | Extra |
|---|---|---|
| `gha-runner-scale-set-aviator-coding-home-ops` | `aviator-coding/home-ops` | talosctl init, 20Gi `ceph-block` work PVC. Runs-on label is this name |
| `gha-rs-ac-ai-k8s-sandbox` | `aviator-coding/ai-k8s-sandbox` | privileged `docker` DinD sidecar |

Both: `minRunners: 1`, `maxRunners: 15` (a real queue ceiling, per set).
Controller chart `gha-runner-scale-set-controller`. Listeners are
`<scale-set>-*-listener`.

Taskfile recipes and the maintenance CronJob cover **home-ops only**. Sandbox
runners: `gh api repos/aviator-coding/ai-k8s-sandbox/actions/runners`.

## Credentials

- No Kubernetes API. Do not add a shared `cluster-admin` binding. A future
  workflow that needs the API gets a namespaced Role for those verbs and
  `serviceAccountName` on that one scale set.
- Talos `ServiceAccount/actions-runner` is `os:operator`, the least role that
  can `talosctl image pull`. `os:reader` cannot. It cannot read machine config,
  reset, upgrade or fetch a kubeconfig. Only the home-ops pod mounts the
  reconciled Secret at `/var/run/secrets/talos.dev/talosconfig`.

## Stale client certificate

`image-pull` "Pull Image" presents that Secret. `remote error: tls: expired
certificate` means the node rejected the client cert. A local
`x509: certificate has expired` would be the node's cert instead.

```bash
kubectl delete secret actions-runner -n actions-runner-system
```

The controller recreates it from the ServiceAccount CR. Then re-run the workflow.

## Playbook

Controller health series is
`up{job="actions-runner-system/action-runner-controller"}`.
`up{job="gha-runner-scale-set-controller"}` is empty.

| Symptom | First action |
|---|---|
| Jobs queued, no pods | Listener logs, ExternalSecret `aviator-coding-runner-secret`, both sets against `maxRunners` |
| Pods Pending / CrashLoop | Node pressure, Ceph health, image pull events |
| `GithubActionsRunnerControllerDown` | Controller logs, `flux get hr -n actions-runner-system` |
| Runners live 2-30s, jobs stuck assigned | Ghost jobs. `task actions-runner:cancel-stuck-runs`, then `cleanup-stale-runners`. CronJob already cancels home-ops runs stuck >30m and deletes offline runners with no Pod |
| Listener `context deadline exceeded` to `broker.actions.githubusercontent.com` | `task actions-runner:restart-listener` (deletes listeners for both sets) |
| `image-pull` expired cert | Delete Secret `actions-runner` (above) |

`task actions-runner:diagnose` is the status entry. Full reset of the home-ops
scale set is `task actions-runner:reset-scale-set`.

## What the home-ops runner cannot do

- No Docker socket and no DinD. `renovate.yaml` and `build-talosctl-busybox.yaml`
  stay on `ubuntu-latest` because they run `docker`.
- `podman` cannot run in any pod here: `/proc/sys/user/max_user_namespaces` is
  `0` on the Talos nodes. Enabling user namespaces is a machine-config change,
  not a runner Dockerfile change.
- Spegel mirrors registries for image pulls. Its Service is `spegel` in
  `kube-system` (port 9090). There is no `spegel-metrics` Service.
