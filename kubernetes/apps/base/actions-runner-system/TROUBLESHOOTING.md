# GitHub Actions runners

Operational playbook (queued jobs, ghost jobs, broker timeouts, expired
Talos client cert, no Docker on the home-ops scale set): skill `github-ci`
([arc-runners.md](../../../../.agents/skills/github-ci/references/arc-runners.md)).

Image and chart pins live in the HelmReleases. Taskfile recipes cover
`aviator-coding/home-ops` only. `task actions-runner:diagnose` is the status
entry.

An `image-pull` failure `remote error: tls: expired certificate` is the
runner client cert. Delete Secret `actions-runner` in `actions-runner-system`
so the controller reissues it.
