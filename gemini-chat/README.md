# gemini-chat

Local workspace skill for deterministic Gemini Web automation via OpenClaw browser.

## Scope

Current scope:
- single-turn ask
- structured result output (`fetch` / `fetch-with-sources` / `search` / `report`)
- capture Gemini Web answer text
- separate final `answer` from Gemini thinking metadata (`thoughtLabels` / `thinking`)
- copy-first probing with DOM fallback for answer extraction
- deterministic browser state machine
- structured result output for recovery / debug / extraction work

## Design direction

This skill should follow the same project discipline proven by `chatgpt-chat`, but it should **not** directly copy `/opt/vault/codehub/gemini/chat.py`.

Current implementation principle:
- prefer **OpenClaw browser** capabilities first
- use `/opt/vault/codehub/gemini` as a **reference source**, not as the default runtime
- keep the product shape aligned with `chatgpt-chat`: mode-driven prompt wrapping + structured JSON output
- prioritize a maintainable single-turn Gemini Web flow over heavy stealth or batch orchestration

## Expected stable path

Target sequence:

1. open Gemini Web
2. label/focus the OpenClaw tab as `gemini-monitor`
3. use stable tab labels for open/find, then concrete CDP `targetId` values for Browser HTTP actions
4. detect page state / auth state
5. find the Gemini input box
6. input the prompt with a stable strategy
7. submit the prompt
8. wait for Gemini answer stabilization
9. extract the latest answer text
10. return structured JSON

## Current status

- project docs now live under `docs/projects/gemini-chat/`
- project setup is aligned with the `chatgpt-chat` template
- implementation is available as a deterministic local runner
- current implementation uses OpenClaw loopback Browser HTTP, with stable tab-label handling and concrete `targetId` values for actions
- on OpenClaw 2026.5.x, provide the gateway shared secret through env, `openclaw.json`, or one-off `--browser-token` / `--browser-password` flags if the runner reports `ERR_BROWSER_UNAUTHORIZED`
