# GitHub Actions Workflows

This directory contains GitHub Actions workflows for the home-ops repository, supporting GitOps-based infrastructure management with Flux v2 on a Talos Linux Kubernetes cluster.

## Workflow Overview

| Workflow | Trigger | Purpose | Runner |
|----------|---------|---------|--------|
| [flux-local.yaml](#flux-local) | Pull Request | Validates Flux manifests and generates diffs | ubuntu-latest |
| [image-pull.yaml](#image-pull) | Pull Request | Pre-pulls new container images to cluster nodes | Self-hosted |
| [renovate.yaml](#renovate) | Schedule/Push/Manual | Automated dependency updates | ubuntu-latest |
| [codeql.yml](#codeql) | PR/Push/Schedule | Security analysis for GitHub Actions | ubuntu-latest |
| [labeler.yaml](#labeler) | Pull Request | Auto-labels PRs based on changed files | ubuntu-latest |
| [label-sync.yaml](#label-sync) | Push/Schedule | Syncs repository labels from config | ubuntu-latest |
| [tag.yaml](#tag) | Schedule/Manual | Creates monthly release tags | ubuntu-latest |
| [test-runner.yaml](#test-runner) | Schedule/Manual | Tests self-hosted runner functionality | Self-hosted |

## Workflows

### flux-local

**File:** `flux-local.yaml`

Validates Kubernetes manifests using [flux-local](https://github.com/allenporter/flux-local) when PRs modify files in `kubernetes/**/*`.

**Jobs:**
- `filter` - Detects changed files in kubernetes directory
- `test` - Runs `flux-local test` to validate manifests
- `diff` - Generates diffs for HelmReleases and Kustomizations, posts as PR comments
- `success` - Aggregates job results for branch protection

**Dependencies:** Requires `BOT_APP_ID` and `BOT_APP_PRIVATE_KEY` secrets.

### image-pull

**File:** `image-pull.yaml`

Pre-pulls new container images to Talos nodes before PRs are merged, reducing deployment time for new workloads.

**Jobs:**
- `filter` - Detects kubernetes file changes
- `extract` - Extracts image lists from default and PR branches using flux-local
- `diff` - Computes new images not present in default branch
- `pull` - Pulls new images to cluster nodes via talosctl
- `success` - Aggregates job results

**Features:**
- Caches talosctl binary for faster execution
- Runs on self-hosted runners with cluster access
- Parallel image pulls with max-parallel: 4

### renovate

**File:** `renovate.yaml`

Runs [Renovate](https://github.com/renovatebot/renovate) for automated dependency updates.

**Triggers:**
- Push to `.renovaterc.json5` or `.renovate/**`
- Hourly schedule
- Manual dispatch with options for dry-run and log level

**Configuration:** Uses repository's `.renovaterc.json5` for Renovate settings.

### codeql

**File:** `codeql.yml`

Performs CodeQL security analysis on GitHub Actions workflow files.

**Schedule:** Daily at 6:30 AM UTC, plus on PRs and pushes to main.

**Languages:** GitHub Actions (`actions`)

### labeler

**File:** `labeler.yaml`

Automatically applies labels to PRs based on changed file paths using [actions/labeler](https://github.com/actions/labeler).

**Configuration:** Uses `.github/labeler.yaml` for label mappings.

### label-sync

**File:** `label-sync.yaml`

Synchronizes repository labels from `.github/labels.yaml` configuration.

**Schedule:** Daily at midnight UTC.

**Behavior:** Deletes labels not defined in config file.

### tag

**File:** `tag.yaml`

Creates monthly release tags using CalVer format (`YYYY.M.patch`).

**Schedule:** Monthly on the 1st at midnight UTC.

**Tag Format:** `2024.1.0`, `2024.1.1`, `2024.2.0`, etc.

### test-runner

**File:** `test-runner.yaml`

Tests self-hosted runner functionality and available tools.

**Schedule:** Weekly on Sunday at 6 AM UTC.

**Tests:**
- Basic system information and resources
- Docker functionality (if available)
- Common CLI tools (kubectl, helm, talosctl, etc.)
- File system operations

## Common Patterns

### GitHub App Token Generation

Most workflows use a GitHub App for authentication instead of `GITHUB_TOKEN`:

```yaml
- name: Generate Token
  uses: actions/create-github-app-token@v2.2.1
  id: app-token
  with:
    app-id: ${{ secrets.BOT_APP_ID }}
    private-key: ${{ secrets.BOT_APP_PRIVATE_KEY }}
```

**Benefits:**
- Higher rate limits than GITHUB_TOKEN
- Can trigger other workflows
- Consistent identity for automated commits

### Success Validation Pattern

Workflows with multiple jobs use a success job for branch protection:

```yaml
success:
  if: ${{ !cancelled() }}
  needs: [job1, job2]
  name: Workflow - Success
  runs-on: ubuntu-latest
  steps:
    - name: Any jobs failed?
      if: ${{ contains(needs.*.result, 'failure') }}
      run: exit 1

    - name: All jobs passed or skipped?
      if: ${{ !(contains(needs.*.result, 'failure')) }}
      run: echo "All jobs passed or skipped"
```

**Why:** GitHub branch protection requires a single status check. This pattern aggregates results from all jobs.

### Concurrency Control

All workflows use concurrency groups to prevent parallel runs:

```yaml
concurrency:
  group: ${{ github.workflow }}-${{ github.event.number || github.ref }}
  cancel-in-progress: true
```

**Behavior:** Cancels in-progress runs when new commits are pushed.

### Changed Files Detection

Uses `bjw-s-labs/action-changed-files` for efficient file change detection:

```yaml
- name: Get Changed Files
  uses: bjw-s-labs/action-changed-files@v0.4.1
  with:
    patterns: kubernetes/**/*
```

## Self-Hosted Runners

The `image-pull` and `test-runner` workflows use self-hosted runners:

```yaml
runs-on: gha-runner-scale-set-aviator-coding-home-ops
```

**Requirements:**
- Runner scale set deployed via actions-runner-controller
- Network access to Talos nodes
- kubectl and talosctl available (or cached)

## Troubleshooting

### Flux Local Test Failures

1. Check if HelmRelease values are valid
2. Verify Kustomization patches apply correctly
3. Run locally: `flux-local test --path kubernetes/flux/cluster --all-namespaces --enable-helm`

### Image Pull Failures

1. Verify runner has network access to container registries
2. Check talosctl can reach nodes: `talosctl --nodes $NODE version`
3. Ensure `$NODE` environment variable is set on runners

### Renovate Not Creating PRs

1. Check Renovate logs in workflow run
2. Verify `.renovaterc.json5` configuration
3. Check Dependency Dashboard issue for status

### Branch Protection Issues

1. Ensure success job name matches branch protection rule
2. Verify all required jobs are listed in `needs`
3. Check workflow permissions are correct

## Related Documentation

- [Flux v2 Documentation](https://fluxcd.io/docs/)
- [Talos Linux Documentation](https://www.talos.dev/docs/)
- [GitHub Actions Documentation](https://docs.github.com/en/actions)
- [Renovate Documentation](https://docs.renovatebot.com/)
- [flux-local Documentation](https://github.com/allenporter/flux-local)
