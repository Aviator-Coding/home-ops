---
name: image-generation
description: "Read before generating any image: hero art, icons, product shots, diagrams-as-art, social cards, or when calling the LiteLLM image-generation key (POST /v1/images/generations). Covers the executable script, the three models (gpt-image-2, gemini-3.1-flash-image, grok-imagine-image), how the key is read from 1Password, and how to write prompts that look professional rather than generic."
---

# Image generation through LiteLLM

One script, three models, one key. Run it directly; `uv` resolves everything.

```bash
.agents/skills/image-generation/scripts/generate_image.py "PROMPT"            # gpt-image-2, ./image-<model>-<ts>.png
.agents/skills/image-generation/scripts/generate_image.py "PROMPT" -m grok -o art/hero.png
.agents/skills/image-generation/scripts/generate_image.py "PROMPT" -m gpt -q high -s 1536x1024 -n 2
.agents/skills/image-generation/scripts/generate_image.py --list-models
```

Success prints JSON on stdout (`files`, `model`, `usage`, `cost_usd`, `elapsed_s`, `revised_prompt`). Failure prints JSON
on stderr (`error.type`, `message`, `hint`, `status`) and exits non-zero. Always Read the resulting file before calling
the work done.

## Models

| Alias | Model | Pick it for | Cost |
|---|---|---|---|
| `gpt` (default) | `gpt-image-2` | text and typography, UI-style assets, logos-as-art, exact instructions. `-s` and `-q` apply | ~$0.007 low, more at medium/high |
| `gemini` | `gemini-3.1-flash-image` | photoreal scenes, natural light, materials, describe-an-edit prompts | ~$0.07 |
| `grok` | `grok-imagine-image` | fast free drafts, stylized and illustrative looks | $0 (subscription) |

Workflow: explore the idea with `grok` (free), compare a photoreal take with `gemini`, finalize with `gpt -q high`.
`-s`/`-q` are ignored with a warning on the other two models.

## Key, cache and failures

- Key order: `IMAGEGEN_API_KEY` env, cache file, then `op read` of `op://Homelab/litellm-consumer-image-generation/key`
  (override with `--op-ref` or `IMAGEGEN_OP_REF`). A successful `op read` is cached once in
  `${XDG_CACHE_HOME:-~/.cache}/image-generation/key` (0600). A 401 clears the cache and re-reads once.
  `--refresh-key` forces a re-read. The script never prints the key.
- The gateway is `https://litellm.sklab.dev` (override `--base-url` / `IMAGEGEN_BASE_URL`); it resolves on the private
  VLAN only.
- The key lives in the `image-generation` LiteLLMVirtualKey (`kubernetes/apps/base/ai/litellm/app/virtualkeys/image-generation.yaml`):
  $10/30d, 30 rpm. See skill `litellm-proxy` for model and key changes.

| Exit | Meaning | Usual fix |
|---|---|---|
| 2 | usage (no prompt, bad model, bad flag) | read the hint; `--list-models` |
| 3 | key / 1Password | `op signin`; item may be in another vault: `--op-ref`; or set `IMAGEGEN_API_KEY` |
| 4 | network | be on the LAN/VPN, or fix `--base-url` |
| 5 | HTTP error from LiteLLM (`status` in the JSON) | 401 refresh key, 403 model not allowed, 429 rpm or budget, 400 prompt rejected |
| 6 | empty or malformed response | retry; try another model |

## Taste: how to get professional images

Models reward a clear art direction, not a long adjective list. Before writing the prompt, decide:

1. **Purpose and placement.** Where will it live (hero, card, avatar, slide, README)? That fixes aspect ratio
   (`1536x1024` landscape, `1024x1536` portrait, square for icons) and where the empty space must be.
2. **One idea.** One subject doing one thing. If you cannot say it in a sentence, it is two images.
3. **A palette of two or three named colors** plus one accent. Restraint reads as design.
4. **Light with a direction** (soft window light from the left, low golden sun, overcast diffuse). Light is what separates
   photography from stock render.
5. **A camera or medium** (35mm, 85mm portrait lens, shallow depth of field; or risograph print, gouache, isometric
   vector, matte clay render). Name it, do not hint at it.
6. **Composition**: rule of thirds, subject off-center, generous negative space where copy will go, clear foreground
   and background.
7. **Real materials and honest imperfection**: brushed aluminium, linen, wet stone, a slightly worn edge. Perfect surfaces
   look synthetic.

Prompt skeleton: `<subject + action>, <setting>, <composition>, <lens/medium>, <lighting>, <palette>, <mood>, <finish>. No <things to avoid>.`

Avoid the usual AI tells: glossy plastic skin, neon purple-teal gradients, centered symmetrical stock poses, floating
objects with no shadow, extra fingers, garbled small text, "ultra detailed 8k masterpiece" filler. State what to leave out
in one short sentence rather than a keyword dump.

Text inside images: keep it to a few words, quote the exact string, say where it sits and the type feel
("thin geometric sans, lower left, high contrast"). Use `gpt`; verify the spelling in the output.

Iteration loop: generate, Read the file, critique against the brief (composition, light, palette, stray artifacts, text),
change exactly one variable, regenerate. Two or three rounds usually beat one giant prompt. Use `-n 2-4` on a cheap model
to compare options.

Longer recipes per use case (hero banner, product shot, icon set, editorial illustration, portrait, diagram art) and
per-model notes are in [references/prompting.md](references/prompting.md).
