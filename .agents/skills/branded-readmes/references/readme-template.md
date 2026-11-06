# README template

Copy the skeleton, fill every `<...>`, drop a section only when it would be empty. The cert-manager README
(`kubernetes/apps/base/cert-manager/cert-manager/README.md`) is the worked example.

````markdown
<p align="center">
  <img src="./assets/banner.svg" width="100%" alt="<app>: <tagline>">
</p>

> **<Five-word promise.>** <Two sentences: what it does for the cluster, in plain words.>

## At a glance

<!-- glance:start -->
<!-- glance:end -->

## What it does

- **<Verb>** <one capability, with the real object names>.
- ...

## Why it is built this way

| Choice | Why |
|---|---|
| **<Decision, not X>** | <The reason, the alternative, what breaks otherwise.> |

## How it works

<p align="center">
  <img src="./assets/infographic.jpg" width="100%" alt="Infographic: <title>. 1 <Step>: <scene>. ...">
</p>

<p align="center">
  <img src="./assets/explainer.svg" width="100%" alt="Step by step: 1 <Step>, <text>. ... <facts>.">
</p>

<details>
<summary><b>Resource graph</b>: every object involved, by name</summary>

```mermaid
<init line from references/brand.md>
flowchart LR
  ...
```
</details>

In more detail:

1. <What Flux applies and which workloads run.>
2. <The runtime path, one step per line, with the reason for anything non-obvious.>

## Settings that matter

All values are in [`app/helmrelease.yaml`](app/helmrelease.yaml) unless noted.

| Setting | Value | Why |
|---|---|---|
| `<key.path>` | `<value>` | <why this value, what the default would do> |

## Alerts

| Alert | Fires when | First look |
|---|---|---|

## Operate it

```bash
flux -n <ns> get ks <app>
kubectl -n <ns> get <the CRs that show health>
```

## Invariants

<Rules that must hold, kept word for word from a legacy README. Omit for new READMEs; tripwires live in the skill.>

## Known gaps

- **<Short title.>** <What is wrong, where, and its effect. Found while writing; not fixed here.>

## Files

| File | Holds |
|---|---|

Upstream: [<project> docs](<url>) • [<the page that explains the non-default setting>](<url>)
````

`sync` appends the stamp line at the end; leave it there.

## `assets/readme.json`

```json
{
  "banner": {
    "eyebrow": "HOME-OPS • TLS • PKI",
    "title": "cert-manager",
    "tagline": "One sentence: what it does.",
    "chips": ["UP TO", "FOUR", "SHORT FACTS", "FIRST IS CYAN"],
    "icon": "shield-check",
    "status": "RECONCILED BY FLUX • ns/app"
  },
  "explainer": {
    "eyebrow": "HOW IT WORKS",
    "title": "From a DNS proof to a certificate on every Gateway",
    "steps": [
      {"icon": "key-round", "title": "Token", "text": "One or two sentences with real names.", "scene": "a key emerging from a vault"}
    ],
    "facts": [{"value": "90 D", "label": "certificate lifetime"}]
  },
  "art": {"about": "how a TLS certificate is issued", "title": "How cert-manager issues TLS", "model": "gpt", "subject": ""}
}
```

- `icon`: a [Lucide](https://lucide.dev/icons) name present in `lucide-static@0.460.0`
  (`curl -sI https://cdn.jsdelivr.net/npm/lucide-static@0.460.0/icons/<name>.svg`).
- `steps`: 3-6. Five fit at about 30 words each; with six, keep each `text` under 22 words.
- `scene`: what the AI station shows, concrete objects only, no words except a short quoted label such as `"TXT"`.
- `facts`: 0-4 tiles. `value` is 2-6 characters, `label` is at most about 40 characters.
- `art.subject` is only for the optional `concept` mood image.
- pre-commit's `pretty-format-json` keeps `ensure_ascii`, so non-ASCII characters are stored as `\uXXXX` escapes.
