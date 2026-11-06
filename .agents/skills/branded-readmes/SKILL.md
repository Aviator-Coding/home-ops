---
name: branded-readmes
description: "Write or refresh a skrumbach-branded README for a home-ops app or directory: what it does, why, how it works, which settings matter. Renders a brand SVG banner and step infographic, draws an AI illustrated infographic, and keeps a generated facts table and freshness stamp in sync with the manifests. Use for /branded-readmes <app>, 'add a README', 'update the READMEs', stale README checks."
---

# Branded READMEs

One README per Flux app at `kubernetes/apps/base/<ns>/<app>/README.md`, in the skrumbach design system (dark night
background, neon cyan, violet and magenta, Saira, Barlow and JetBrains Mono). Reference build:
`kubernetes/apps/base/cert-manager/cert-manager/` (README, `assets/`). Brand rules: [references/brand.md](references/brand.md).
Section skeleton and `readme.json` schema: [references/readme-template.md](references/readme-template.md).

```bash
R=.agents/skills/branded-readmes/scripts/readme.py
$R list                      # every app: missing | legacy (no stamp) | stale | fresh
$R list --only stale         # what drifted since its README was written
$R facts cert-manager        # JSON facts from the overlay + base manifests (ns/app or a path also work)
$R init cert-manager         # assets/readme.json with defaults; then edit it
$R banner cert-manager       # assets/banner.svg + assets/explainer.svg from readme.json (free, deterministic)
$R art cert-manager          # assets/infographic.jpg, AI, from explainer.steps[].scene (~$0.17 at -q high)
$R art cert-manager concept  # optional mood art from art.subject (assets/concept.jpg)
$R sync cert-manager         # re-render SVGs, regenerate the At-a-glance table, restamp
$R sync --all                # same for every stamped README
$R index cert-manager        # namespace index README (only if none is hand-written)
```

`uv` resolves the dependencies. Fonts (@fontsource 5.3.0) and Lucide icons (0.460.0) download once into
`~/.cache/branded-readmes`. `art` calls skill `image-generation` and retries without `OP_SERVICE_ACCOUNT_TOKEN` when
the service account hides the `Homelab` vault.

## What each piece is

| Asset | Made by | Changes when | Purpose |
|---|---|---|---|
| `assets/banner.svg` | `banner`/`sync` from `readme.json.banner` | config edit | Title, one-line tagline, 4 chips, Lucide icon |
| `assets/explainer.svg` | `banner`/`sync` from `readme.json.explainer` | config edit | The infographic with exact text: 3-6 numbered steps, each an icon, a title and 1-2 sentences, plus 4 key-number tiles |
| `assets/infographic.jpg` | `art` (gpt-image-2) | rerun by hand | Illustrated overview: same steps as stations, labels only. Never put detail here; image models misspell long text |
| Mermaid block | written by hand, in `<details>` | manifests change | Every object by real name, for operators |
| `<!-- glance:start -->` table | `sync` | every `sync` | Namespace, Flux Kustomization, chart, images, deps, consumers, routes, secrets, volumes, backup, health checks, alerts |
| `<!-- branded-readme v1 sources=... -->` | `sync` | every `sync` | Hash of the structural facts and Helm values, versions stripped. `list` compares it, so Renovate bumps never mark a README stale but a new route, secret, dependency or values change does |

## Workflow: new README

1. **Gather facts, do not guess.** `$R facts <app>`, then read every file in the app dir and its overlay. Read the
   subsystem skill from the CLAUDE.md SKILL INDEX, and grep who consumes the app (Secrets, issuers, Services, CRDs).
   Check the upstream docs for what each non-default value does.
   Note mismatches you find. They go in **Known gaps**; do not fix them in the README PR.
2. `$R init <app>`, then edit `assets/readme.json`: the banner (tagline is one sentence of what it does, chips are
   short uppercase facts, icon is a Lucide name that exists in 0.460.0) and 3-6 explainer steps with `scene`.
3. `$R banner <app>` and check both SVGs (below). Iterate on the config, not the SVG.
4. `$R art <app>` once the steps are final. Read the JPG: every label spelled right, the stations in order,
   nothing warm-toned or photographic. Wrong? Change one `scene` or the `art.title`, regenerate (two tries maximum,
   then fall back to `-m grok` drafts or ship without it).
5. Write `README.md` from [references/readme-template.md](references/readme-template.md). Keep the markers.
6. `$R sync <app>`, then check the result (below) and run the gates.

## Workflow: refresh

`$R list --only stale`, then for each: rerun `facts`, compare with the README prose (settings table, steps, Known
gaps), fix the prose and `readme.json`, and `$R sync`. Re-run `art` only when the steps changed. A **legacy** README
(hand-written, no stamp; 17 exist, several hold operator invariants that skills point at) is converted in place: keep
the file path (skills and tests link to it), keep every invariant as an `## Invariants` section, word for word unless
it is wrong, and add the branded sections around it.

## Checking the output

- SVGs: screenshot with headless Chrome at their own width, then Read the PNG:
  `"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new --hide-scrollbars --screenshot=<scratch>/b.png --window-size=1200,300 file://$PWD/<dir>/assets/banner.svg`
  (`explainer.svg` height is in its root `height` attribute). `qlmanage` crops wide SVGs; do not trust it.
- The page: `gh api /markdown` with `mode=gfm` renders it as GitHub does. Wrap it in `github-markdown-css` (dark and
  light) and screenshot both; the banner and explainer must read on white too.
- Gates: `python3 scripts/ci/doc-links-test.py` (every relative link must resolve),
  `python3 scripts/ci/docs-budget-test.py`, `pre-commit run --files <changed>` (check-xml parses the SVGs, 800 KB
  file cap).

## Rules

- **Facts over adjectives.** Every value in a table is read from Git. No chart or image versions in prose (Renovate
  owns them); link `ocirepository.yaml` or the HelmRelease instead.
- **Settings that matter** lists only values that differ from the chart default or encode a decision, each with its
  why. Not a values dump.
- A README explains; the skill owns tripwires and runbooks. Link skill `<name>` rather than copying its content.
- **Secrets:** name 1Password items and Secret names, never values. `${SECRET_DOMAIN}` stays literal in prose (Flux
  does not substitute README files, and they are outside every Kustomization path).
- **Images stay small.** JPG for AI art (Flux's source-controller excludes `*.jpg`/`*.png` from the artifact; SVG is
  included, so keep SVGs under ~120 KB, which glyph reuse gives). Never commit raw PNG output.
- **Budget:** the `image-generation` key is $10 per 30 days. A `-q high` infographic is about $0.17; batch work uses
  `-q medium` or skips `art` for small apps (banner and explainer are free).
- One app per commit, `docs(<app>): add branded README`. Batches of up to about 10 apps per PR.
