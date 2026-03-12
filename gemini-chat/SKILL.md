---
name: gemini-chat
description: Run deterministic single-turn Gemini Web automation via OpenClaw and return structured JSON output for orchestrators and LLM agents.
---

# gemini-chat

## When to use

Use this skill when:
- The user explicitly wants **Gemini Web** (`https://gemini.google.com/`).
- You need a **single-turn** prompt -> answer workflow.
- You need machine-readable output for follow-up orchestration.

Do not use this skill when:
- The task is long multi-turn session management.
- The task does not require Gemini Web behavior.

## Prerequisites

Required:
1. OpenClaw is installed and running locally.
2. OpenClaw config is available at `~/.openclaw/openclaw.json` (or `OPENCLAW_CONFIG`).
3. A working OpenClaw browser profile exists (default: `openclaw`).
4. Python 3.10+ is available.

Recommended preflight check:

```bash
python3 - <<'PY'
import json, pathlib
p = pathlib.Path('~/.openclaw/openclaw.json').expanduser()
cfg = json.loads(p.read_text())
port = int(((cfg.get('gateway') or {}).get('port')) or 18789)
print('browser-control base:', f'http://127.0.0.1:{port+2}')
PY
```

## Input contract

Required field:
- `prompt` (string)

Optional fields:
- `mode`: `fetch` | `fetch-with-sources` | `search` | `report` (default: `fetch-with-sources`)
- `conversation_url`
- `save_report`
- `report_path`
- `title`
- `profile` (default: `openclaw`)
- `timeout_seconds` (default: `45`)
- `recovery_timeout_seconds` (default: `120`)
- `recovery_poll_ms` (default: `3000`)

## Output contract

The runner prints JSON to stdout.

Key fields:
- `ok`
- `answer`
- `thinking`, `thoughtLabels` (when available)
- `sources`
- `conversationUrl`
- `error`, `errorCode`, `nextStep`
- `pageState`, `authState`
- `partial`
- `debug`

Exit codes:
- `0` success
- `2` failure

## Integration

### Option A: CLI flags

```bash
python3 scripts/gemini_chat_runner.py \
  --prompt "What is quantum computing?" \
  --mode fetch-with-sources
```

### Option B: stdin JSON (recommended for orchestrators)

```bash
cat <<'JSON' | python3 scripts/gemini_chat_runner.py --stdin-json
{
  "prompt": "Summarize recent AI Agent trends.",
  "mode": "report",
  "save_report": true,
  "report_path": "./artifacts/gemini-agent-report.md",
  "profile": "openclaw",
  "timeout_seconds": 45,
  "recovery_timeout_seconds": 120,
  "recovery_poll_ms": 3000
}
JSON
```

## Runtime flow (state machine)

1. Open `conversation_url` or `https://gemini.google.com/`.
2. Detect page readiness and blocked/auth state.
3. Inject and submit prompt.
4. Wait for answer stabilization.
5. Extract answer (and thinking metadata when available).
6. Emit structured JSON and close the opened tab.

## Failure handling

Common error codes:
- `ERR_OPEN_TAB`
- `ERR_INPUT_NOT_FOUND`
- `ERR_SUBMIT_NOT_CONFIRMED`
- `ERR_ANSWER_TIMEOUT`
- `ERR_TAB_NOT_FOUND`
- `ERR_UNKNOWN_BLOCKED_STATE`

Orchestration guidance:
1. Retry once for transient page/session issues.
2. Surface `nextStep` directly to users for login/verification guidance.
3. If `partial=true`, inform users and optionally run one short retry.

## Minimal LLM agent template

```text
1) Build request JSON (at least `prompt`)
2) Run: python3 scripts/gemini_chat_runner.py --stdin-json
3) Parse stdout JSON
4) If ok=true: return answer/sources/thinking
5) If ok=false: return errorCode + nextStep and apply retry/user-notification policy
```
