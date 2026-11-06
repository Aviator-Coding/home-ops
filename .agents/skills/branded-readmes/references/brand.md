# skrumbach brand, applied to READMEs

Source of truth: the claude.ai/design project **Sascha Krumbach Design System** (`tokens/colors.css`,
`readme.md`), the same system as `github.com/skrumbach` (`skrumbach/skrumbach`, `scripts/render/lib/svg.mjs`).
Read it with the `DesignSync` tool (`list_projects`, then `get_file`; needs `/design-login`). The older purple
**sklab.dev Design System** is not this brand. `scripts/readme.py` carries the tokens in `C`; change them there.

## Tokens

| Role | Token | Hex |
|---|---|---|
| Page | night-900 | `#060818` |
| Card | night-850 | `#090C22` |
| Raised / Mermaid node | night-800 | `#0D102C` |
| Primary accent (links, step rules, first chip, stat values) | cyan-400 | `#22D8F0` |
| Wordmark gradient | cyan → blue → blue | `#22D8F0` → `#2F9BFF` → `#3A6BFF` |
| Secondary (borders, halos) | violet-500 / 400 | `#8B55F5` / `#A57BFF` |
| Tertiary, sparing (step numbers, one accent) | magenta-500 | `#D946E6` |
| Neon hairline gradient | cyan → indigo → magenta | `#22D8F0` → `#6C5CFF` → `#D946E6` |
| Live status only | green-400 | `#2EE08A` |
| Headline / body / muted text | white / ink-200 / ink-300 | `#FFFFFF` / `#CDD2E6` / `#A9AFCB` |

Type: display **Saira 800** uppercase (titles, stat values), labels **Barlow Semi Condensed 600** uppercase tracked
.12-.24em, body **Barlow 400**, mono **JetBrains Mono 500**. Radii 3-4px chips, 6px cards. 1px hairlines, glow
instead of shadows. Separators `•`, `→`, `—`; no emoji.

## Voice

Builder to builder: direct, technical, never salesy. Labels and chips are UPPERCASE ("LET'S ENCRYPT", "2 REPLICAS •
PDB"). Taglines are one sentence that says what the thing does. Numbers are real and compact ("90 D", "2 × 3").
Status lines start with a keyword and a colon or a bullet ("RECONCILED BY FLUX • ns/app").

## Why SVG text is outlines

GitHub serves README images with `Content-Security-Policy: default-src 'none'`, which blocks web fonts inside an
SVG. `readme.py` converts every string to glyph paths (each glyph defined once, placed with `<use>`), so the brand
fonts render everywhere with no font loading. A character missing from all four fonts fails the render on purpose:
rephrase it. Gradient-filled text (the banner title) is drawn inline, because a `userSpaceOnUse` gradient behind
`<use>` would be computed per glyph.

## AI imagery

Dark, neon-lit digital illustration on night black. Light comes only from cyan and violet, magenta on one element.
Particles, thin circuit traces, hairline geometry, soft bloom. Cool palette only. No photography, no people, no
logos, no full-frame gradient wash. Infographics: isometric stations on a single cyan data path, labels of one to
three words only, title at top left. `ART_STYLE` and `infographic_prompt()` in `readme.py` hold the exact wording;
skill `image-generation` covers models, cost and prompt craft.

## Mermaid (GitHub renders it natively)

Dark nodes with light text stay readable in GitHub's light and dark themes. Do not set `fontFamily`: GitHub's
renderer cannot load Barlow, and mismatched metrics clip the labels.

```
%%{init: {'theme':'base','themeVariables':{'primaryColor':'#0D102C','primaryTextColor':'#E9ECF7','primaryBorderColor':'#22D8F0','lineColor':'#8B55F5','secondaryColor':'#151941','tertiaryColor':'#090C22','edgeLabelBackground':'#151941'}}}%%
flowchart LR
  ...
  classDef accent fill:#0D102C,stroke:#D946E6,stroke-width:2px,color:#FFFFFF
  classDef ext fill:#090C22,stroke:#A57BFF,color:#CDD2E6
```

Use `accent` for the one object the app is about, `ext` for things outside the cluster (1Password, Cloudflare,
upstream services). No subgraphs: their titles sit on the page background and vanish in one of the two themes.
