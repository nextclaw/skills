# chatgpt-chat

Deterministic single-turn ChatGPT Web automation skill powered by OpenClaw.

## Capabilities

- Single-turn ask and answer capture
- Optional visible source extraction
- Structured JSON output for LLM/orchestrator pipelines
- Optional Markdown report export

## Prerequisites

1. OpenClaw is running.
2. `~/.openclaw/openclaw.json` is present (or set `OPENCLAW_CONFIG`).
3. A valid OpenClaw profile exists (default: `openclaw`).
4. Python 3.10+ is available.

## Quick start

```bash
python3 scripts/chatgpt_chat_runner.py \
  --prompt "Explain quantum computing and list major sources." \
  --mode fetch-with-sources
```

## Orchestrator integration (`--stdin-json`)

```bash
cat <<'JSON' | python3 scripts/chatgpt_chat_runner.py --stdin-json
{
  "prompt": "Summarize Bitcoin price movement in the last 7 days.",
  "mode": "report",
  "save_report": true,
  "report_path": "./artifacts/btc-report.md"
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

- stdout: JSON (`ok`, `answer`, `sources`, `errorCode`, `nextStep`, `debug`, ...)
- exit code: `0` on success, `2` on failure
