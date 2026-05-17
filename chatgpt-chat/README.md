# chatgpt-chat

Local workspace skill for deterministic ChatGPT Web automation via OpenClaw browser.

## Scope

Current scope:
- single-turn ask
- ask with visible sources
- save captured answer as markdown
- deterministic browser state machine
- structured result output for recovery / extraction / debug

## Stable path

Known reliable sequence:

1. open `https://chatgpt.com/`
2. label/focus the OpenClaw tab as `chatgpt-monitor`
3. detect page state
4. find prompt textbox
5. inject/type prompt
6. wait for send button
7. click send button
8. assert `/c/...`
9. wait for answer stabilization
10. extract answer via `copy`-first, DOM-markdown fallback
11. return structured JSON

## Runner

Current recommended local entrypoint:
- `scripts/chatgpt_chat_runner.py`

Assume the current working directory is:
- `skills/chatgpt-chat/`

Then use a relative path:

```bash
python3 scripts/chatgpt_chat_runner.py \
  --prompt "请整理最近一周比特币价格变化" \
  --mode report \
  --tab-label chatgpt-monitor
```

The runner uses OpenClaw's loopback Browser HTTP control surface. For OpenClaw 2026.5.12, the shared secret can be supplied by `OPENCLAW_GATEWAY_TOKEN`, `OPENCLAW_GATEWAY_PASSWORD`, `openclaw.json`, or the one-off `--browser-token` / `--browser-password` flags.

Current responsibilities of the runner:
- standardize request input
- wrap prompts by mode
- drive the ChatGPT page state machine
- classify page state / auth state
- use OpenClaw stable tab labels for open/find, then concrete CDP `targetId` for Browser HTTP actions
- wait for recovery on blocked states
- extract answer and visible sources
- normalize / dedupe sources
- render and save markdown reports
- return structured JSON for upper-layer orchestration

## Output contract

Typical structured output includes fields such as:

```json
{
  "ok": true,
  "conversationUrl": "https://chatgpt.com/c/...",
  "answer": "...",
  "sources": [
    {"text": "Yahoo Finance", "href": "https://..."}
  ],
  "pageState": "ready",
  "authState": "authenticated-or-unknown",
  "extractionMode": "dom-markdown",
  "browserProfile": "openclaw",
  "browserTarget": "chatgpt-monitor"
}
```

## Current behavior and limitations

- current preferred extraction path is:
  1. turn-level `Copy` button on the latest assistant reply
  2. `clipboard-read`
  3. `writeText` interception fallback
  4. DOM-markdown fallback
- for best results, allow `chatgpt.com` to read the clipboard in the browser; otherwise the runner may fall back to DOM extraction more often
- the runner is intentionally designed to target the **latest assistant reply** and its **bottom action bar** so future multi-turn support does not need a selector redesign
- Browser HTTP actions use the concrete `targetId`; the stable tab label is retained for opening and debugging
- default tab label is `chatgpt-monitor`; override with `--tab-label` when running multiple independent sessions
- use `--browser-base-url` only when debugging a non-default Browser HTTP port
- if the runner returns `ERR_BROWSER_UNAUTHORIZED`, pass the current gateway shared secret with `OPENCLAW_GATEWAY_TOKEN` / `OPENCLAW_GATEWAY_PASSWORD` or one-off flags instead of changing ChatGPT page state or prompt text
- actual user-visible notification delivery is expected to be handled by the upper orchestration layer; the runner outputs notification contract fields
- blocked-state handling is now structured, but deeper optimization should be driven by real samples rather than guessed edge cases
- browser-control transient issues such as `ERR_TAB_NOT_FOUND` now use a short readiness retry window before reopening the tab once

## Current status

- implemented as a workspace-local skill
- backed by a runnable local runner
- optimized for deterministic single-turn ChatGPT Web automation
- documented as a reference project for skill / tool development workflow
