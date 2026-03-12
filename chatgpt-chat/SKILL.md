---
name: chatgpt-chat
description: Run deterministic single-turn ChatGPT Web automation via OpenClaw and return structured JSON output for orchestrators and LLM agents.
---

# chatgpt-chat

## When to use

Use this skill when:
- The user explicitly wants **ChatGPT Web** (`https://chatgpt.com/`).
- You need a **single-turn** prompt -> answer workflow.
- You need machine-readable output (`ok`, `answer`, `sources`, `errorCode`, `nextStep`, `debug`).

Do not use this skill when:
- The task is long multi-turn session management.
- The task does not require ChatGPT Web behavior.

## Prerequisites

Required:
1. OpenClaw is installed and running locally.
2. OpenClaw config is available at `~/.openclaw/openclaw.json` (or `OPENCLAW_CONFIG`).
3. A working OpenClaw browser profile exists (default: `openclaw`).
4. Python 3.10+ is available.

Recommended preflight checks:

```bash
# 1) Verify config file is readable
cat ~/.openclaw/openclaw.json

# 2) Print browser-control base URL derived from gateway.port
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
- `conversation_url` (if omitted, opens `https://chatgpt.com/`)
- `save_report` (boolean)
- `report_path` (string)
- `title` (string)
- `profile` (default: `openclaw`)
- `timeout_seconds` (default: `45`)
- `recovery_timeout_seconds` (default: `180`)
- `recovery_poll_ms` (default: `3000`)

## Output contract

The runner always prints JSON to stdout.

Key fields:
- `ok`
- `answer`
- `sources` (`[{"text": "...", "href": "..."}]`)
- `conversationUrl`
- `error`, `errorCode`, `nextStep`
- `pageState`, `authState`, `pageBlockReason`
- `partial`
- `debug`

Exit codes:
- `0` success
- `2` failure

## Integration

### Option A: CLI flags

```bash
python3 scripts/chatgpt_chat_runner.py \
  --prompt "Explain quantum computing and list major sources." \
  --mode fetch-with-sources
```

### Option B: stdin JSON (recommended for orchestrators)

```bash
cat <<'JSON' | python3 scripts/chatgpt_chat_runner.py --stdin-json
{
  "prompt": "Summarize Bitcoin price movement in the last 7 days.",
  "mode": "report",
  "save_report": true,
  "report_path": "./artifacts/btc-report.md",
  "profile": "openclaw",
  "timeout_seconds": 45,
  "recovery_timeout_seconds": 180,
  "recovery_poll_ms": 3000
}
JSON
```

## Runtime flow (state machine)

1. Open `conversation_url` or `https://chatgpt.com/`.
2. Detect page readiness and blocked/auth state.
3. Inject the prompt (wrapped by mode when applicable).
4. Confirm submission.
5. Wait for answer stabilization.
6. Extract answer and visible sources (copy-first, DOM fallback).
7. Emit structured JSON and close the opened tab.

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
2. If `notificationNeeded=true`, surface `notificationMessage` to the user.
3. If `partial=true`, inform the user and optionally retry once using `nextStep`.

## Minimal LLM agent template

```text
1) Build request JSON (at least `prompt`)
2) Run: python3 scripts/chatgpt_chat_runner.py --stdin-json
3) Parse stdout JSON
4) If ok=true: return answer/sources
5) If ok=false: return errorCode + nextStep and apply retry/user-notification policy
```
