# Prompt recipes

Fill the brackets; delete clauses you do not need. Change one thing per iteration.

## Hero banner (website or README, landscape `-s 1536x1024`)
`[abstract or concrete subject], composed with the subject in the right third and calm negative space on the left for a
headline, [medium or lens], [light direction and quality], palette of [color 1], [color 2] and a single [accent] highlight,
[mood], subtle film grain. No text, no logos, no people unless stated.`

## Product or object shot
`A [object] on [surface], 85mm macro lens, shallow depth of field, large soft window light from the left with a gentle
shadow falling right, [background color] seamless backdrop, slight real-world wear on the [material], centered with 20%
margin on every side, true-to-life color.` (Great on `gemini`; finalize on `gpt -q high` when you need exact colors.)

## Icon or logo-as-art (square)
`A minimal [subject] icon, flat vector style, [2-3] colors from [palette], consistent 8px-equivalent stroke, rounded
corners, centered on a plain [color] background with generous padding, no gradients, no text.` Generate a set by keeping
the sentence identical and swapping only the subject.

## Editorial illustration
`[Scene in one sentence] in the style of mid-century [print or gouache or risograph] illustration, limited palette of
[three colors], visible paper texture, bold shapes, [composition: strong diagonal / layered planes], [mood].`
(`grok` is a good free first pass for this.)

## Portrait or lifestyle photo (`gemini`)
`Candid photo of [person description, age, clothing] [doing X] in [location], 35mm lens, natural window light, slightly
off-center framing, shallow depth of field, muted [palette], visible skin texture and flyaway hair, documentary feel.`
Say what the person is doing; static smiling poses look like stock.

## Diagram or infographic art (`gpt`)
Describe structure, not decoration: `A clean isometric illustration of [system], [N] labeled blocks named exactly
"A", "B", "C", connected by thin lines, off-white background, one accent color for the main path.` Check every label.

## Text rendering checklist (`gpt`)
- Exact string in quotes, 1-5 words, one line.
- Position and size ("lower left, about one fifth of the height"), type feel, contrast with the background.
- If a letter is wrong, regenerate with the same prompt at `-q high` before rewording.

## Editing by description
There is no mask/edit endpoint here. Re-describe the whole image with the change ("same scene, but at dusk with warm
lamps on") and keep the original wording for everything you want unchanged.

## Model notes
- `gpt-image-2`: obeys structure and spelling best; `-q low` for drafts (~$0.007), `-q high` for finals. Sizes
  `1024x1024`, `1536x1024`, `1024x1536`, `auto`.
- `gemini-3.1-flash-image`: best natural light and materials; slower and ~10x the draft cost of gpt low. Ignores size flags,
  so state the aspect ratio in the prompt.
- `grok-imagine-image`: quick and free; stronger on stylized looks than on exact text. Ignores size flags.
- Content policy rejections come back as HTTP 400; rephrase the subject rather than retrying verbatim.
