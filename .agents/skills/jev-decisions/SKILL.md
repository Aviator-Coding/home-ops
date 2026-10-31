---
name: jev-decisions
description: "Read before asking Jev (TypeSafe's decision model) anything: yes/no rule checks, choices or scores over a diff, a document or a PR, as a second opinion for an agent. Covers the JSONL-in/JSONL-out script, the predicate/choice/score question shapes, the jev-decisions key from 1Password, what Jev is good and bad at, and thresholds."
---

# Jev decisions through LiteLLM

Jev answers questions about a piece of state with calibrated probabilities. It gives no
reasoning text. One script: JSONL requests in, JSONL answers out.

```bash
S=.agents/skills/jev-decisions/scripts/jev_decide.py
$S requests.jsonl > answers.jsonl          # or: ... | $S
echo '{"id":"x","input":"<text or JSON>","questions":[{"type":"predicate","name":"q","instructions":"Is ...?"}]}' | $S
```

Each request line is LiteLLM's `POST /v1/decisions` body without `model`, plus an optional
`id`; a non-string `input` is JSON-serialised. Each output line is
`{"id", "ok": true, "answers": {name: ...}, "usage", "model"}` or
`{"id", "ok": false, "error": {type, status, message, hint}}`.

| Question | Request fields | Answer |
|---|---|---|
| `predicate` | `name`, `instructions` | `probability` of yes, 0 to 1 |
| `choice` | `name`, `instructions`, `choices: [{value, description?}]` (2-255) | `choice`, `probabilities`, `confidence` |
| `score` | `name`, `instructions`, `levels: [{label, description?}]` (2-10, low to high) | `score` (level index), `probabilities`, `confidence` |

## What Jev is good at, and not

- **Good:** narrow, checkable yes/no rules about concrete text, one predicate per rule. The AI
  review gate (`scripts/ci/ai_review_gate.py`) asks one predicate per blocker rule and caught 5 of
  5 seeded blockers with 0 false failures (goal §8, D6).
- **Weak:** holistic judgement. Asked for the risk tier and severity of a change that breaks a
  backup rule, it answered `low` and `minor` at low confidence while scoring the rule itself 0.93.
  Ask "does this break rule X?", never "is this safe?".
- Give it everything the rule depends on: the diff alone hid a kopiur include. Send the full
  changed files and any lists the rule needs (for example the defined Flux variables).
- Scores vary by a few hundredths between runs. Set thresholds from the cost of each mistake and
  calibrate on known-good and known-bad examples. Re-calibrate when `model` (the Jev version) changes.
- Over about 32 KB of text Jev switches to a slower, less accurate long-context mode.

## Key, gateway, exits

- Key order: `JEV_API_KEY` env, cache file, then `op read op://Homelab/litellm-consumer-jev-decisions/key`
  (override `--op-ref` / `JEV_OP_REF`), cached once in `${XDG_CACHE_HOME:-~/.cache}/jev-decisions/key`
  (0600). The script never prints the key.
- Gateway `https://litellm.sklab.dev` (override `--base-url` / `JEV_BASE_URL`), private VLAN only.
- The key is the `jev-decisions` LiteLLMVirtualKey ($1/30d, 300 rpm); about $0.00002 per 400 input
  tokens, output is free. Model and key changes: skill `litellm-proxy`.

| Exit | Meaning | Fix |
|---|---|---|
| 2 | no requests | pass a JSONL file or stdin |
| 3 | key / 1Password | `op signin`, `--op-ref`, or set `JEV_API_KEY` |
| 4 | network on every line | be on the LAN/VPN, or fix `--base-url` |
| 5 | some lines failed | read that line's `error`: 400 bad body, 401 `--refresh-key`, 403 key lacks `jev`, 429 limits |
