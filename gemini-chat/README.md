# gemini-chat

Deterministic single-turn Gemini Web automation skill powered by OpenClaw.

## Capabilities

- Single-turn ask and answer capture
- `fetch` / `fetch-with-sources` / `search` / `report` modes
- Structured JSON output with state/error/debug fields
- Optional Markdown report export

## Prerequisites

1. OpenClaw is running.
2. `~/.openclaw/openclaw.json` is readable (or set `OPENCLAW_CONFIG`).
3. A valid OpenClaw profile exists (default: `openclaw`).
4. Python 3.10+ is available.

## Quick start

```bash
python3 scripts/gemini_chat_runner.py \
  --prompt "What is quantum computing?" \
  --mode fetch-with-sources
```

## Orchestrator integration (`--stdin-json`)

```bash
cat <<'JSON' | python3 scripts/gemini_chat_runner.py --stdin-json
{
  "prompt": "Summarize recent AI Agent trends.",
  "mode": "report",
  "save_report": true,
  "report_path": "./artifacts/gemini-agent-report.md"
}
JSON
```

## Key flags

- `--prompt` (required)
- `--mode`: `fetch` | `fetch-with-sources` | `search` | `report`
- `--conversation-url`
- `--save-report`
- `--report-path`
- `--profile`
- `--timeout-seconds`
- `--recovery-timeout-seconds`
- `--recovery-poll-ms`
- `--stdin-json`

## Output and exit code

- stdout: JSON (`ok`, `answer`, `thinking`, `sources`, `errorCode`, `nextStep`, `debug`, ...)
- exit code: `0` on success, `2` on failure
