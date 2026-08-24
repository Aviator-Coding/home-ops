# Coderd Runbooks

These files are mounted as ConfigMap `coder-runbooks` (label `runbook_docs: "true"`).
There is no `runbooks.${SECRET_DOMAIN}` HTTPRoute. Alert `runbook_url` values
point at this Git path.

## CoderdCPUUsage

The CPU usage of one or more Coder pods has been close to the **limit** defined
for the deployment. This can cause slowness, workspaces becoming unavailable,
and liveness-probe restarts.

This cluster's Coder HelmRelease does **not** set a CPU limit. Resources are
`requests.cpu: 71m` and `limits.memory: 1Gi` only (`app/helmrelease.yaml`).
The alert divides usage by `kube_pod_container_resource_limits{resource="cpu"}`,
which is empty here, so this alert will not fire. Do not add a CPU limit as a
first response: the 2026-06-30 outage comment on the HelmRelease is about
memory/OOM, not CPU throttling.

If CPU is actually high, check request vs usage (`kubectl -n coder top pod`)
and node pressure. Compare against
[Coder's Reference Architectures](https://coder.com/docs/v2/latest/admin/architectures)
only after confirming real saturation.

## CoderdMemoryUsage

The memory usage of one or more Coder pods has been close to the limit defined
for the deployment. When the memory usage exceeds the limit, the pod(s) will be
restarted by Kubernetes. This will interrupt all connections to workspaces being
handled by the affected pod(s).

The memory limit is `1Gi`. To resolve, increase that limit on the Coder
HelmRelease if usage is genuinely at the cap. If usage increases monotonically,
that is likely a memory leak.

## CoderdRestarts

One or more Coder pods have been restarting multiple times in the last 10
minutes. This may be due to a number of issues, including:

- Failure to connect to the configured database: Coder requires a reachable
  PostgreSQL database to function. If it fails to connect, you will see an error
  similar to the following:

  ```console
  [warn]  ping postgres: retrying  error="dial tcp postgres-17-rw.database.svc.cluster.local:5432: connect: connection refused"  try=3
  ```

  The live DSN host is `postgres-17-rw.database.svc.cluster.local:5432`
  (`app/externalsecret.yaml`), not a ClusterIP.

- Out-Of-Memory (OOM) kills due to memory usage (see [above](#coderdmemoryusage)),
- An unexpected bug causing the application to exit with an error.

If Coder is not restarting due to excessive memory usage, check the logs:

1. Check the logs of the deployment for any errors,

```console
kubectl -n coder logs deployment/coder --previous
```

2. Check any Kubernetes events related to the deployment,

```console
kubectl -n coder events --watch
```

## CoderdReplicas

One or more Coderd replicas are down. This may cause availability problems and elevated
response times for user and agent API calls.

This cluster runs a single coderd replica (`replicas` defaults to 1; there is no
separate provisioner Deployment). Review `deployment/coder` in namespace `coder`
for `CrashLoopBackOff`, or re-adjust alarm levels if the replica count changes.

## CoderdWorkspaceBuildFailures

A few workspace build errors have been recently observed.

Review Prometheus metrics to identify failed jobs. Check the workspace build logs
to determine if there is a relationship with a new template version or a buggy
Terraform plugin.
