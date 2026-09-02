# scripts/add-app

Scaffolds a new app's manifests to match this repo's app-structure convention (single-component
`app/`, multi-component family, or CRD-split). Read
[`docs/app-structure.md`](../../docs/app-structure.md) first - it explains the three shapes and
the traps this scaffold encodes by construction (correct `# yaml-language-server: $schema=`
headers, `healthChecks` on the workload with `wait: false`, the correctly-computed `components:`
relative-path depth, `defaultPodOptions` spelled and nested correctly).

## Usage

```
scripts/add-app/generate-app.sh <namespace> <app> [options]
```

Run `scripts/add-app/generate-app.sh --help` for the full option list (`--shape=`, `--secrets`,
`--dragonfly`, `--dry-run`). Always `--dry-run` first to review before writing.

This is a plain script, not an interactive generator - it produces a skeleton with `TODO`
placeholders (image/tag, ports, uid/gid, 1Password keys), not a finished app. After running it:

1. Fill in every `TODO` in the generated files.
2. Decide whether the app needs a `components/volsync` and/or `components/kopiur` backup
   include. This is deliberately **not** auto-generated - it requires measuring the app's live
   file ownership after first deploy (see `kubernetes/components/volsync/Readme.md` and
   `kubernetes/components/kopiur/Readme.md`), which a template can't know in advance.
3. Validate: `mise exec -- task flux:test:all`.

## What it does not do

- Does not generate the `parameterized instance` shape's parent-directory wiring (one live
  precedent in this repo, its own `resources:` list is hand-maintained - the script writes the
  instance's own files and tells you the one line to add by hand).
- Does not wire VolSync/kopiur, ingress auth, Gatus endpoints, or Homepage annotations - these are
  per-app judgment calls, not structural boilerplate.
- Does not touch any existing app. It refuses to overwrite a file that already exists.
