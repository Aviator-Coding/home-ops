# GitHub Actions Runner Troubleshooting Guide

This document provides operational guidance for debugging and maintaining the self-hosted GitHub Actions runners deployed via Actions Runner Controller (ARC) on this Kubernetes cluster.

Image tags and chart versions rotate with Renovate. Treat the HelmReleases as
the source of truth:

- Runner image: `gha-runner-scale-set/app/aviator-coding/{home-ops,ai-k8s-sandbox}/helmrelease.yaml`
- Controller chart: `gha-runner-scale-set-controller/app/helmrelease.yaml`

## Architecture Overview

### Components

1. **Controller** (`gha-runner-scale-set-controller`)
   - Manages the lifecycle of runner scale sets
   - Handles communication with GitHub's API
   - Deployed as a Deployment with 2 replicas
   - Chart `gha-runner-scale-set-controller` (currently `0.14.2`; no image pin, chart default tracks the chart)

2. **Listeners** (one per scale set)
   - Long-polling connection to GitHub for job requests
   - Triggers runner pod creation when jobs are queued
   - Names: `<scale-set-name>-*-listener`

3. **Runner Pods** (ephemeral)
   - Created on-demand when jobs are assigned; `minRunners: 1` keeps one warm standby per scale set
   - Deleted after job completion
   - Use ephemeral storage (20Gi `ceph-block` PVCs)

### Scale sets

Both HelmReleases use chart `gha-runner-scale-set` (currently `0.14.2`),
`minRunners: 1` / `maxRunners: 15`, and image
`ghcr.io/home-operations/actions-runner` (currently `2.336.0`).

| HelmRelease | GitHub repo | Extra |
|-------------|-------------|-------|
| `gha-runner-scale-set-aviator-coding-home-ops` | `aviator-coding/home-ops` | talosctl init container, 20Gi ephemeral `ceph-block` work PVC |
| `gha-rs-ac-ai-k8s-sandbox` | `aviator-coding/ai-k8s-sandbox` | privileged `docker:29-dind` sidecar, unbounded `emptyDir` dind-storage |

Listener pods:

- `gha-runner-scale-set-aviator-coding-home-ops-*-listener`
- `gha-rs-ac-ai-k8s-sandbox-*-listener`

`maxRunners: 15` per set is a real ceiling (jobs sit queued once hit). A
"no runners" diagnosis should check **both** sets.

### Namespace

All runner components are deployed in: `actions-runner-system`

### Tooling coverage gap

The maintenance CronJob (`REPO_NAME=home-ops`) and Taskfile recipes
(`.taskfiles/actions-runner/Taskfile.yaml`, `REPO: aviator-coding/home-ops`)
only cover the **home-ops** scale set. They do not list, clean, or cancel
`ai-k8s-sandbox` runners. Use `gh api repos/aviator-coding/ai-k8s-sandbox/actions/runners`
for that repo. Expanding maintenance to the sandbox set is a tooling change,
not done here.

## Common Failure Scenarios

### 1. No Runners Available / Jobs Stuck in Queue

**Symptoms:**
- GitHub Actions jobs show "Waiting for a runner to pick up this job"
- No runner pods are being created

**Diagnosis:**
```bash
# Check listener pod status (both scale sets)
kubectl get pods -n actions-runner-system -l app.kubernetes.io/component=listener

# Check listener logs
kubectl logs -n actions-runner-system -l app.kubernetes.io/component=listener --tail=100

# Check controller logs
kubectl logs -n actions-runner-system -l app.kubernetes.io/name=gha-runner-scale-set-controller --tail=100

# Verify ExternalSecret is synced (GitHub App credentials)
kubectl get externalsecret -n actions-runner-system
kubectl describe externalsecret aviator-coding-runner-secret -n actions-runner-system
```

**Common Causes:**
- GitHub App token expired or misconfigured
- Listener pod crashed or disconnected
- Network connectivity issues to GitHub API
- maxRunners limit reached (15 per scale set)

**Resolution:**
- Restart listener pod if stuck: `kubectl delete pod -n actions-runner-system -l app.kubernetes.io/component=listener`
- Verify GitHub App credentials in 1Password are correct (see Secret Management)
- Check ExternalSecret sync status

### 2. Runner Pods Failing to Start

**Symptoms:**
- Runner pods created but stuck in Pending or CrashLoopBackOff
- Jobs fail immediately after runner assignment

**Diagnosis:**
```bash
# List runner pods (both scale sets; excludes controller and listeners)
kubectl get pods -n actions-runner-system -l actions.github.com/scale-set-name

# Describe a failing runner pod
kubectl describe pod <runner-pod-name> -n actions-runner-system

# Check events for the namespace
kubectl get events -n actions-runner-system --sort-by='.metadata.creationTimestamp' | tail -20
```

**Common Causes:**
- Insufficient resources (CPU/memory) on nodes
- Storage provisioning failures (Ceph issues)
- Image pull failures
- Talos secret not available (home-ops scale set)

**Resolution:**
- Check node resources: `kubectl top nodes`
- Verify Ceph cluster health: `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status`
- Check image availability: `kubectl get events -n actions-runner-system | grep -i pull`

### 3. Controller Pod Unhealthy

**Symptoms:**
- PrometheusRule alert: `GithubActionsRunnerControllerDown`
- No runner scale sets being managed

The scrape `job` label is `actions-runner-system/action-runner-controller`
(PodMonitor `action-runner-controller`, namespace-prefixed). The rule matches
that job. `up{job="gha-runner-scale-set-controller"}` is empty and must not be
used as the health signal.

**Diagnosis:**
```bash
# Check controller pods
kubectl get pods -n actions-runner-system -l app.kubernetes.io/name=gha-runner-scale-set-controller

# Check controller logs
kubectl logs -n actions-runner-system -l app.kubernetes.io/name=gha-runner-scale-set-controller --tail=200

# Check HelmRelease status
flux get hr -n actions-runner-system gha-runner-scale-set-controller
```

**Resolution:**
- If CrashLoopBackOff, check logs for specific error
- Force reconciliation: `flux reconcile hr -n actions-runner-system gha-runner-scale-set-controller`
- Check for CRD issues: `kubectl get crd | grep actions.github.com`

### 4. Jobs Fail with Talos/Kubernetes Access Errors

**Symptoms:**
- Jobs that need `talosctl` or `kubectl` access fail
- Errors about missing config or permissions

Applies to the **home-ops** scale set (talosctl init container). The sandbox
scale set is DinD, not talosctl.

**Diagnosis:**
```bash
# Verify talos secret exists
kubectl get secret actions-runner -n actions-runner-system

# Check runner ServiceAccount RBAC
kubectl get clusterrolebinding | grep actions-runner
```

**Environment Variables:**
Runner pods are configured with these environment variables:
- `NODE_IP`: Set to the host IP for talosctl commands
- `TALOSCONFIG`: Path to Talos configuration file
- `ACTIONS_RUNNER_PRINT_LOG_TO_STDOUT`: Enabled for debugging

**Resolution:**
- Ensure the `actions-runner` secret contains valid talosconfig
- Verify ServiceAccount has required permissions

## Monitoring and Observability

### Metrics

Metrics are exposed via PodMonitor and available in Prometheus/Grafana:

- `gha_completed_jobs_total` - Total completed jobs (by result: success/failure)
- `gha_started_jobs_total` - Total started jobs
- `gha_idle_runners` - Number of idle runners
- `gha_busy_runners` - Number of busy runners
- `gha_assigned_jobs` - Number of assigned/waiting jobs
- `gha_job_startup_duration_seconds` - Time from job assignment to execution start
- `gha_job_execution_duration_seconds` - Job execution duration

Controller `up` series: `job="actions-runner-system/action-runner-controller"`.
Listener `up` series are
`job="actions-runner-system/gha-runner-scale-set-aviator-coding-home-ops"` and
`job="actions-runner-system/gha-rs-ac-ai-k8s-sandbox"`.

### Grafana Dashboard

The ARC dashboard is automatically provisioned to Grafana via the `arc-dashboard`
ConfigMap (`grafana_folder: CI`).

Access: Grafana > Dashboards > search **ARC Monitoring** (not "Actions Runner Controller").

### Alerts

PrometheusRules are configured for:
- `GithubActionsRunnerJobFailureRateHigh` - >20% failure rate over 1 hour
- `GithubActionsRunnerZeroIdleRunners` - No idle runners with jobs waiting
- `GithubActionsRunnerControllerDown` - No healthy controller scrape
- `GithubActionsRunnerJobStartupSlow` - P95 startup time >2 minutes

## Manual Operations

### Force Scale Up Runners

To manually trigger runner creation (for testing):
```bash
# Trigger the test workflow from GitHub (home-ops synthetic self-test)
gh workflow run test-runner.yaml --repo aviator-coding/home-ops
```

### Clear Stuck Jobs

If jobs are stuck and need to be cleared:
```bash
# Delete all runner pods (they will be recreated as needed)
kubectl delete pods -n actions-runner-system -l actions.github.com/scale-set-name

# Restart the listener to re-establish GitHub connection
kubectl delete pods -n actions-runner-system -l app.kubernetes.io/component=listener
```

### View Runner Registration

```bash
# home-ops scale set
gh api repos/aviator-coding/home-ops/actions/runners

# sandbox scale set
gh api repos/aviator-coding/ai-k8s-sandbox/actions/runners
```

## Secret Management

### GitHub App Credentials

Managed via ExternalSecret `aviator-coding-runner-secret` syncing from 1Password
(`gha-runner-scale-set/app/aviator-coding/externalsecret.yaml`):

| Kubernetes Secret key | 1Password item | Field |
|-----------------------|----------------|-------|
| `github_app_id` | `github` | `ACR_AVIATOR_CODING_APP_ID` |
| `github_app_installation_id` | `github` | `ACR_AVIATOR_CODING_INSTALLATION_ID` |
| `github_app_private_key` | `github-acr-all-repositories` | `private key` |

Both scale sets share this Secret.

To verify secret sync:
```bash
kubectl get externalsecret -n actions-runner-system aviator-coding-runner-secret -o yaml
```

### Talos Credentials

The `actions-runner` secret contains talosconfig for cluster management operations:
```bash
kubectl get secret actions-runner -n actions-runner-system -o jsonpath='{.data.talosconfig}' | base64 -d
```

## Recovery Procedures

### Complete Runner System Reset

If the runner system is completely broken, name **both** scale sets. Do not
`kubectl delete hr -n actions-runner-system --all` unless you also resume
`gha-rs-ac-ai-k8s-sandbox` afterwards.

1. Suspend and delete the HelmReleases:
```bash
flux suspend hr -n actions-runner-system gha-runner-scale-set-aviator-coding-home-ops
flux suspend hr -n actions-runner-system gha-rs-ac-ai-k8s-sandbox
flux suspend hr -n actions-runner-system gha-runner-scale-set-controller
kubectl delete hr -n actions-runner-system \
  gha-runner-scale-set-aviator-coding-home-ops \
  gha-rs-ac-ai-k8s-sandbox \
  gha-runner-scale-set-controller
```

2. Wait for cleanup:
```bash
kubectl get pods -n actions-runner-system -w
```

3. Resume reconciliation (controller first, then both scale sets):
```bash
flux resume hr -n actions-runner-system gha-runner-scale-set-controller
flux resume hr -n actions-runner-system gha-runner-scale-set-aviator-coding-home-ops
flux resume hr -n actions-runner-system gha-rs-ac-ai-k8s-sandbox
```

`task actions-runner:reset-scale-set` only recreates the home-ops HelmRelease.

### Rotate GitHub App Credentials

1. Generate a new private key in GitHub App settings
2. Update 1Password item `github-acr-all-repositories` field `private key`
   (and `github` / `ACR_AVIATOR_CODING_APP_ID` + `ACR_AVIATOR_CODING_INSTALLATION_ID` if those changed)
3. Force ExternalSecret refresh:
```bash
kubectl annotate externalsecret -n actions-runner-system aviator-coding-runner-secret force-sync=$(date +%s) --overwrite
```

## Automated Self-Healing

The runner system includes automated maintenance mechanisms to prevent and recover from common failure scenarios.

### Maintenance CronJob

A CronJob (`runner-maintenance`) runs every 15 minutes against **home-ops only**
(`REPO_NAME=home-ops`) to automatically:

1. **Clean stale runners**: Removes offline runners from GitHub's API that are no longer active
2. **Cancel stuck runs**: Cancels workflow runs stuck in "queued" state for more than 30 minutes
3. **Log anomalies**: Reports long-running jobs that may need manual attention

It does not cover `ai-k8s-sandbox`. It has no disk, PVC, or image-cache cleanup.

**View maintenance logs:**
```bash
task actions-runner:logs-maintenance
```

**Manually trigger maintenance:**
```bash
task actions-runner:run-maintenance
```

### Prometheus Alerts

The following alerts monitor runner health:

| Alert | Severity | Description |
|-------|----------|-------------|
| `GithubActionsRunnerJobFailureRateHigh` | warning | >20% job failure rate over 1 hour |
| `GithubActionsRunnerZeroIdleRunners` | warning | No idle runners with jobs waiting |
| `GithubActionsRunnerControllerDown` | critical | No healthy controller `up` scrape (`job="actions-runner-system/action-runner-controller"`) |
| `GithubActionsRunnerJobStartupSlow` | warning | P95 job startup time exceeds 2 minutes |
| `GithubActionsRunnerShortLifetimeDetected` | warning | Multiple runners exiting in <30 seconds (ghost jobs) |
| `GithubActionsRunnerListenerDisconnected` | critical | Listener pod restarting frequently |
| `GithubActionsRunnerAssignedJobsStuck` | warning | Jobs assigned but not running |

## Ghost Job Recovery

Ghost jobs occur when GitHub's Actions service has stale job assignments that no longer exist. This causes runners to start, receive outdated job IDs, find no work, and exit immediately (typically 2-second lifetime).

### Symptoms

- Runner pods with very short lifetime (2-30 seconds)
- Jobs stuck in "assigned" state on GitHub
- Alert: `GithubActionsRunnerShortLifetimeDetected`
- Listener logs showing job assignments but runners exiting quickly

### Automated Recovery

The maintenance CronJob automatically (home-ops only):
- Cleans offline runners from GitHub every 15 minutes
- Cancels workflow runs stuck for more than 30 minutes

### Manual Recovery

```bash
# Cancel stuck workflow runs (home-ops)
task actions-runner:cancel-stuck-runs

# Clean up stale runners from GitHub API (home-ops)
task actions-runner:cleanup-stale-runners

# Full reset if above doesn't work (home-ops HelmRelease only)
task actions-runner:reset-scale-set
```

For the sandbox repo, use `gh` against `aviator-coding/ai-k8s-sandbox` instead
of the Taskfile.

## Broker Connection Issues

The listener pod maintains a long-polling connection to `broker.actions.githubusercontent.com`. Connection issues cause 100-second timeouts and missed job notifications.

### Symptoms

- Listener logs showing: `context deadline exceeded (Client.Timeout exceeded)`
- Jobs waiting but no runners being created
- Alert: `GithubActionsRunnerListenerDisconnected`

### Automated Recovery

The listener pod will automatically restart on connection failures. Frequent restarts trigger the `GithubActionsRunnerListenerDisconnected` alert.

### Manual Recovery

```bash
# Restart the listener pod
task actions-runner:restart-listener

# Check listener logs for errors
task actions-runner:logs-listener
```

`restart-listener` deletes every listener in the namespace (both scale sets).

### Investigation Steps

If broker timeouts persist:

```bash
# Check DNS resolution
kubectl exec -n actions-runner-system -l app.kubernetes.io/component=listener -- nslookup broker.actions.githubusercontent.com

# Check network policies
kubectl get ciliumnetworkpolicy -n actions-runner-system

# Check egress gateway configuration
kubectl get ciliumnodes -o yaml | grep -A5 egressGateway
```

## Task Commands Reference

All runner maintenance tasks are available via the Taskfile. Unless noted, they
target `aviator-coding/home-ops` only.

```bash
# Show current system status
task actions-runner:diagnose

# View logs
task actions-runner:logs-controller
task actions-runner:logs-listener
task actions-runner:logs-maintenance

# Recovery actions
task actions-runner:restart-listener
task actions-runner:cleanup-stale-runners
task actions-runner:cancel-stuck-runs
task actions-runner:reset-scale-set

# Maintenance
task actions-runner:run-maintenance
```

## Image Pull Optimization

### Spegel P2P Registry Cache

This cluster uses Spegel as a peer-to-peer container image cache. When one node pulls an image,
other nodes can fetch it from the peer instead of the upstream registry.

**Spegel Configuration:**
- Deployed in `kube-system` namespace
- Mirrors ALL registries by default (including ghcr.io, docker.io); the HelmRelease does not set an allow-list
- Exposes local registry on port 29999

**Container Images Used by ARC:**

Current tags live in the HelmReleases (Renovate bumps them). Typical set:

| Image | Registry | Cacheable by Spegel |
|-------|----------|---------------------|
| `ghcr.io/home-operations/actions-runner` (scale-set image tag) | GHCR | Yes |
| `gha-runner-scale-set-controller` chart default image | GHCR | Yes |
| `docker:29-dind` (sandbox sidecar) | Docker Hub | Yes |

**Verifying Spegel is working:**
```bash
# Check Spegel pods
kubectl -n kube-system get pods -l app.kubernetes.io/name=spegel

# Check containerd registry config
talosctl -n <node-ip> cat /etc/cri/conf.d/hosts/ghcr.io/hosts.toml

# View Spegel metrics (Service is `spegel`, port 9090; there is no `spegel-metrics` Service)
kubectl -n kube-system port-forward svc/spegel 9090:9090
curl localhost:9090/metrics | grep spegel
```

**If image pulls are slow:**
1. Verify Spegel pods are running on all nodes
2. Check Spegel logs for errors: `kubectl -n kube-system logs -l app.kubernetes.io/name=spegel --tail=50`
3. Verify the first node has completed the pull (others wait for P2P)
4. Monitor bandwidth with `talosctl dashboard`
5. Check Spegel Grafana dashboard for P2P transfer metrics

### Resource Right-Sizing

Runner resource requests have been optimized based on actual usage:

| Resource | Old Request | New Request | Actual Usage |
|----------|-------------|-------------|--------------|
| CPU | 500m | 200m | 8-14m |
| Memory | 2Gi | 512Mi | 125-142Mi |

This allows more concurrent runners on the same hardware while still providing headroom for burst usage. Limits remain 2 CPU / 8Gi (plus 2 CPU / 4Gi for the sandbox DinD sidecar).

## Related Documentation

- [Actions Runner Controller GitHub](https://github.com/actions/actions-runner-controller)
- [ARC Troubleshooting Guide](https://github.com/actions/actions-runner-controller/blob/master/TROUBLESHOOTING.md)
- [GitHub Docs - ARC](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners-with-actions-runner-controller/about-actions-runner-controller)
- [Securing Self-Hosted Runners](https://some-natalie.dev/blog/securing-ghactions-with-arc/)
