# skills

Open-source skill collection for [OpenClaw](https://github.com/nicepkg/openclaw) browser automation.

Each skill is a **self-contained** folder that implements a deterministic browser automation workflow via OpenClaw browser control. Skills are designed to be used by AI agents, local runners, or any orchestration layer that speaks structured JSON.

## Available Skills

| Skill | Description | Status |
|---|---|---|
| [`chatgpt-chat`](./chatgpt-chat/) | Single-turn ChatGPT Web automation — submit a prompt, extract the answer and visible source links | Stable |
| [`gemini-chat`](./gemini-chat/) | Single-turn Gemini Web automation — submit a prompt, extract the answer with thinking/answer separation | Stable |

## Design Principles

- **Self-contained**: each skill is independent with zero cross-skill dependencies
- **Deterministic**: state machine driven, not improvised browser exploration
- **OpenClaw browser-first**: use managed profiles, stable tab labels, `suggestedTargetId` / `tabId` handles, snapshots, and page screenshots from OpenClaw instead of maintaining a separate CDP stack
- **CLI-first transport**: default to `openclaw browser ...` so runners reuse the user's paired OpenClaw gateway; direct Browser HTTP is available only with `--browser-transport http`
- **Structured output**: every skill returns JSON with `ok`, `error`, `errorCode`, `nextStep`, `debug`, etc.
- **Recovery-first**: blocked states (login walls, human verification) are detected, notified, and waited on — not silently swallowed
- **Failure is explainable**: every failure path produces a structured error code and a human-readable next step suggestion

## Prerequisites

- [OpenClaw](https://github.com/nicepkg/openclaw) CLI with `openclaw browser` available and paired
- A browser profile configured in OpenClaw (default: `openclaw`)
- Python 3.10+ (no pip dependencies — stdlib only)

## Quick Start

```bash
# ChatGPT: ask a question and get a structured JSON result
python3 chatgpt-chat/scripts/chatgpt_chat_runner.py \
  --prompt "What is quantum computing?" \
  --mode fetch-with-sources

# Gemini: ask a question and get a structured JSON result
python3 gemini-chat/scripts/gemini_chat_runner.py \
  --prompt "What is quantum computing?" \
  --mode fetch-with-sources
```

## Skill Structure

Each skill follows a consistent layout:

```
skill-name/
├── README.md          # Overview, scope, stable path, output contract
├── SKILL.md           # Agent-facing skill contract and instructions
├── scripts/
│   └── *_runner.py    # Self-contained runner (stdlib only, no pip deps)
└── schemas/           # (optional) Example output JSON
```

- **`README.md`** — for humans: what the skill does, how to use it, current limitations
- **`SKILL.md`** — for AI agents: when to use this skill, state machine steps, failure handling, prompting guidance
- **`scripts/*_runner.py`** — the actual runner: a single Python file that drives the browser automation end-to-end

## Modes

Both skills support a common mode system:

| Mode | Behavior |
|---|---|
| `fetch` | Submit the prompt, capture the raw answer |
| `fetch-with-sources` | Wrap the prompt to request sources, capture answer + source links |
| `search` | Ask the model to use web search and list sources |
| `report` | Ask for a structured report-style answer; optionally save to disk with `--save-report` |

## Output Contract

All runners return structured JSON to stdout. Key fields:

```json
{
  "ok": true,
  "mode": "fetch-with-sources",
  "prompt": "...",
  "wrapped_prompt": "...",
  "answer": "...",
  "conversationUrl": "https://...",
  "sources": [{"text": "...", "href": "https://..."}],
  "errorCode": null,
  "pageState": "ready",
  "authState": "authenticated-or-unknown",
  "extractionMode": "copy",
  "browserProfile": "openclaw",
  "browserTarget": "chatgpt-monitor",
  "partial": false,
  "nextStep": null,
  "debug": {}
}
```

Exit code: `0` on success, `2` on failure.

## Contributing

Skills should remain self-contained. When adding a new skill:

1. Create a new folder with `README.md`, `SKILL.md`, and `scripts/`
2. The runner should be a single Python file using only stdlib
3. Follow the existing error code and output contract conventions
4. Include both Chinese and English UI signal detection for bilingual compatibility

## License

[MIT](./LICENSE)
