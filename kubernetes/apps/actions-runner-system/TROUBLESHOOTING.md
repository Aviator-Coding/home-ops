# GitHub Actions Runner Troubleshooting Guide

This document provides operational guidance for debugging and maintaining the self-hosted GitHub Actions runners deployed via Actions Runner Controller (ARC) on this Kubernetes cluster.

## Architecture Overview

### Components

1. **Controller** (`gha-runner-scale-set-controller`)
   - Manages the lifecycle of runner scale sets
   - Handles communication with GitHub's API
   - Deployed as a Deployment with 2 replicas

2. **Listener** (`gha-runner-scale-set-aviator-coding-home-ops-*-listener`)
   - Long-polling connection to GitHub for job requests
   - One listener per runner scale set
   - Triggers runner pod creation when jobs are queued

3. **Runner Pods** (ephemeral)
   - Created on-demand when jobs are assigned
   - Deleted after job completion
   - Use ephemeral storage (Ceph block volumes)

### Namespace

All runner components are deployed in: `actions-runner-system`

## Common Failure Scenarios

### 1. No Runners Available / Jobs Stuck in Queue

**Symptoms:**
- GitHub Actions jobs show "Waiting for a runner to pick up this job"
- No runner pods are being created

**Diagnosis:**
```bash
# Check listener pod status
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
- maxRunners limit reached

**Resolution:**
- Restart listener pod if stuck: `kubectl delete pod -n actions-runner-system -l app.kubernetes.io/component=listener`
- Verify GitHub App credentials in 1Password are correct
- Check ExternalSecret sync status

### 2. Runner Pods Failing to Start

**Symptoms:**
- Runner pods created but stuck in Pending or CrashLoopBackOff
- Jobs fail immediately after runner assignment

**Diagnosis:**
```bash
# List all runner pods
kubectl get pods -n actions-runner-system | grep -v controller -| grep -v listener

# Describe a failing runner pod
kubectl describe pod <runner-pod-name> -n actions-runner-system

# Check events for the namespace
kubectl get events -n actions-runner-system --sort-by='.metadata.creationTimestamp' | tail -20
```

**Common Causes:**
- Insufficient resources (CPU/memory) on nodes
- Storage provisioning failures (Ceph issues)
- Image pull failures
- Talos secret not available

**Resolution:**
- Check node resources: `kubectl top nodes`
- Verify Ceph cluster health: `kubectl -n rook-ceph exec deploy/rook-ceph-tools -- ceph status`
- Check image availability: `kubectl get events -n actions-runner-system | grep -i pull`

### 3. Controller Pod Unhealthy

**Symptoms:**
- PrometheusRule alert: `GithubActionsRunnerControllerDown`
- No runner scale sets being managed

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

### Grafana Dashboard

The ARC dashboard is automatically provisioned to Grafana via the `arc-dashboard` ConfigMap.

Access: Grafana > Dashboards > Search for "Actions Runner Controller"

### Alerts

PrometheusRules are configured for:
- `GithubActionsRunnerJobFailureRateHigh` - >20% failure rate over 1 hour
- `GithubActionsRunnerZeroIdleRunners` - No idle runners with jobs waiting
- `GithubActionsRunnerControllerDown` - Controller pod not running
- `GithubActionsRunnerJobStartupSlow` - P95 startup time >2 minutes

## Manual Operations

### Force Scale Up Runners

To manually trigger runner creation (for testing):
```bash
# Trigger the test workflow from GitHub
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
# Check registered runners via GitHub CLI
gh api repos/aviator-coding/home-ops/actions/runners
```

## Secret Management

### GitHub App Credentials

Managed via ExternalSecret syncing from 1Password:

```yaml
# ExternalSecret: aviator-coding-runner-secret
# Syncs to: Secret/aviator-coding-runner-secret
# Contains:
#   - github_app_id
#   - github_app_installation_id
#   - github_app_private_key
```

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

If the runner system is completely broken:

1. Delete the HelmReleases:
```bash
flux suspend hr -n actions-runner-system gha-runner-scale-set-aviator-coding-home-ops
flux suspend hr -n actions-runner-system gha-runner-scale-set-controller
kubectl delete hr -n actions-runner-system --all
```

2. Wait for cleanup:
```bash
kubectl get pods -n actions-runner-system -w
```

3. Resume reconciliation:
```bash
flux resume hr -n actions-runner-system gha-runner-scale-set-controller
flux resume hr -n actions-runner-system gha-runner-scale-set-aviator-coding-home-ops
```

### Rotate GitHub App Credentials

1. Generate new private key in GitHub App settings
2. Update the secret in 1Password
3. Force ExternalSecret refresh:
```bash
kubectl annotate externalsecret -n actions-runner-system aviator-coding-runner-secret force-sync=$(date +%s) --overwrite
```

## Related Documentation

- [Actions Runner Controller GitHub](https://github.com/actions/actions-runner-controller)
- [ARC Troubleshooting Guide](https://github.com/actions/actions-runner-controller/blob/master/TROUBLESHOOTING.md)
- [GitHub Docs - ARC](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners-with-actions-runner-controller/about-actions-runner-controller)
- [Securing Self-Hosted Runners](https://some-natalie.dev/blog/securing-ghactions-with-arc/)
