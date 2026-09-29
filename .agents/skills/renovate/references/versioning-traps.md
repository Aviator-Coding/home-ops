# Versioning traps

`allowedVersions` filters candidates Renovate might propose. It does not roll
a tracked value back. A bare `<X.Y.Z` ceiling goes silent once every match sits
at or above that ceiling: nothing is both newer than current and under the cap.

The Talos pin is `!/^v?1\.13\.3$/` on:

- `ghcr.io/siderolabs/installer` (feeds `TalosUpgrade`, `rebootMode: powercycle`)
- `ghcr.io/siderolabs/talosctl` (CLI image in `.github/docker/talosctl-busybox/Dockerfile`)
- `siderolabs/talos` (factory.talos.dev custom datasource, semver template)
- `/factory\.talos\.dev/`

`v?` covers a `v` prefix and a bare tag. Check which form a registry actually
publishes before anchoring a new regex. The exclusion is institutional memory
of one failed upgrade (PR #867). The three causes (int-marshal panic, talos-1
RAM, tuppr reboot misclassification) are fixed. Do not widen it back into a ceiling.

## Docker isStable

Packages on the `docker` scheme (installer, talosctl) parse only the
dash-separated prefix. `isStable("v1.14.0-alpha.0")` is true, so
`ignoreUnstable` does not drop it, and `getUpdateType` calls the bump a minor.
The "Never auto merge Talos" rule in `.renovate/autoMerge.json5` blocks
automerge for all four packages, stable bumps included. The factory datasource
uses semver and does treat a dash suffix as unstable. It is on the same
exclusion because it feeds the same installer.

## Other pins in `.renovate/overrides.json5`

| Package | Rule |
|---|---|
| `quay.io/ceph/ceph` | `/^v?\d+\.2\.\d+$/` (x.0.z dev, x.1.z RC, x.2.z stable). Not for `ghcr.io/rook/ceph` |
| `public.ecr.aws/emqx/emqx` | `<5.9.0` (5.9+ is Enterprise-only). Open-source line ends at 5.8.x |
| `emqx-operator` | `<2.3.0` (2.3 calls an Enterprise-only API and sticks the OSS broker) |
| `docker.io/plexinc/pms-docker` | regex versioning. Docker compatibility treats the dash-suffix as a qualifier that must match the installed tag, and Plex's suffix is a per-release hash, so every candidate is incompatible. The regex scheme is what lets updates surface. A Plex bump classifies as patch and follows the blanket patch automerge rule |
| authentik provider | `automerge: false`, coupled to the server release line the provider was generated from (`goauthentik/authentik`) |
| mise `1password-cli` | no GitHub repo. Resolve via docker `1password/op` with `versioning: semver`, or docker `isStable` ranks a `-beta` tag as stable |
| agentgateway charts | `<2.0.0`. The same OCI repo also publishes a v2 kgateway-bundled lineage |
| `ghcr.io/berriai/litellm-non_root` minors | `minimumReleaseAgeBehaviour: timestamp-optional`, because GHCR docker tags carry no `releaseTimestamp` and `timestamp-required` leaves the PR pending forever. `automerge` stays false |

When a pin starts proposing again, re-check that package's versioning scheme
against installed Renovate (`getRegexPredicate`, `filterVersions`, `isStable`,
`applyPackageRules`), not the docs alone. `scripts/ci/talos-renovate-pin-test.py`
is the pattern.

## Annotations

```yaml
# renovate: datasource=docker depName=ghcr.io/example/image
image: ghcr.io/example/image:1.2.3
```

The dep line is the next line. A comment edit that separates them makes the
customManager miss the pin. `functional-comments-guard` rejects a removed or
reordered `# renovate:` line.

## Branch automerge

No workflow triggers on `push` to `renovate/**` (every `push:` is `branches:
[main]`). A branch-automerge update sits `pending` with zero checks.
`ignoreTests: false` then refuses it. `:automergeBranch` is absent from
`.renovaterc.json5` for that reason. Digest and patch rules leave
`automergeType` unset, so they use Renovate's default `"pr"`.
