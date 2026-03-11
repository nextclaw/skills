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
2. detect page state / auth state
3. find the Gemini input box
4. input the prompt with a stable strategy
5. submit the prompt
6. wait for Gemini answer stabilization
7. extract the latest answer text
8. return structured JSON

## Current status

- project docs now live under `docs/projects/gemini-chat/`
- project setup is aligned with the `chatgpt-chat` template
- implementation is still in design / MVP-planning phase
- next step is to define Gemini-specific state machine and submission/extraction strategy based on OpenClaw browser abilities
