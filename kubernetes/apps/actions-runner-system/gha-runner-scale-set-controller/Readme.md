# gha-runner-scale-set-controller

Actions Runner Controller (ARC) scale-set controller for this cluster.

- Chart: `gha-runner-scale-set-controller` in `app/helmrelease.yaml` (currently `0.14.2`)
- Replicas: 2
- Namespace: `actions-runner-system`

Scale sets the controller manages (same chart family, currently `0.14.2`):

| HelmRelease | Repo |
|-------------|------|
| `gha-runner-scale-set-aviator-coding-home-ops` | `aviator-coding/home-ops` |
| `gha-rs-ac-ai-k8s-sandbox` | `aviator-coding/ai-k8s-sandbox` |

Operational procedures, recovery, and secret rotation live in
[`../TROUBLESHOOTING.md`](../TROUBLESHOOTING.md).

GitHub App auth:
[Authenticating to the GitHub API](https://docs.github.com/en/actions/hosting-your-own-runners/managing-self-hosted-runners-with-actions-runner-controller/authenticating-to-the-github-api).
