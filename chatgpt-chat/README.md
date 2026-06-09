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

The runner uses OpenClaw 2026.6.x CDP transport for the `openclaw` browser profile. The default CDP URL is `http://127.0.0.1:18800`; override it with `OPENCLAW_CDP_URL` or `--cdp-url` when `openclaw browser --browser-profile openclaw status` reports a different `cdpUrl`.

Current responsibilities of the runner:
- standardize request input
- wrap prompts by mode
- drive the ChatGPT page state machine
- classify page state / auth state
- use concrete CDP `targetId` values for page actions
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
- the runner targets the **latest assistant reply** using multiple ChatGPT Web message structures (`article`, assistant role nodes, message test ids, and markdown containers) before falling back to diagnostics
- CDP actions use the concrete `targetId`; the stable tab label is retained for debugging
- default tab label is `chatgpt-monitor`; override with `--tab-label` when running multiple independent sessions
- use `--cdp-url` only when debugging a non-default OpenClaw CDP port
- if the runner returns `ERR_BROWSER_CDP_UNAVAILABLE`, check `openclaw browser --browser-profile openclaw status` and `curl http://127.0.0.1:18800/json/version`
- actual user-visible notification delivery is expected to be handled by the upper orchestration layer; the runner outputs notification contract fields
- blocked-state handling is now structured, but deeper optimization should be driven by real samples rather than guessed edge cases
- browser-control transient issues such as `ERR_TAB_NOT_FOUND` now use a short readiness retry window before reopening the tab once

## Current status

- implemented as a workspace-local skill
- backed by a runnable local runner
- optimized for deterministic single-turn ChatGPT Web automation
- documented as a reference project for skill / tool development workflow
