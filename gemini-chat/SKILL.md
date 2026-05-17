---
name: gemini-chat
description: Use Gemini Web chat (`https://gemini.google.com/`) for single-turn browser-based Q&A when the task explicitly needs Gemini web behavior or when validating Gemini web output. Prefer this skill for deterministic, single-question web chat flows; use `searxng-local` for daily search and `gemini-cli-provider` for complex research reports.
---

# Gemini Chat (browser-driven, single-turn)

This skill now has two layers:
- **skill contract / state machine**: defines when to use Gemini Web and how the flow should behave
- **local runner implementation**: `scripts/gemini_chat_runner.py` is the current recommended execution entrypoint

Implementation principle:
- prefer **OpenClaw browser** capabilities first
- use `/opt/vault/codehub/gemini` only as a reference source
- do **not** treat Playwright automation as the default runtime path for this skill

For local/manual use, assume the current working directory is:
- `skills/gemini-chat/`

Then run the runner with a relative path, for example:

```bash
python3 scripts/gemini_chat_runner.py \
  --prompt "什么是量子计算？"
```

## When to use

Use this skill when all of the following are true:
- The user wants a **Gemini web** answer specifically, or wants Gemini web behavior validated.
- The task is a **single-turn** question/answer flow.
- The goal is to **submit one prompt and capture Gemini's answer**.

Do **not** use this skill for:
- routine web search → use `searxng-local`
- deep research / analysis reports that require multi-step synthesis beyond a single Gemini Web turn → use `gemini-cli-provider`
- long multi-turn chat sessions unless the user explicitly wants Gemini web chat behavior

## Model guidance

This skill is suitable for a **lighter model** because the workflow is highly structured:
1. open Gemini web
2. find the prompt input
3. submit one question
4. wait for answer text
5. return the captured result

Only upgrade to a stronger model if:
- the page structure changes repeatedly
- answer extraction keeps failing
- the user wants post-processing into a deep report

## Runner usage

Current recommended local entrypoint:
- `scripts/gemini_chat_runner.py`

Common examples:

```bash
python3 scripts/gemini_chat_runner.py \
  --mode fetch-with-sources \
  --prompt "什么是量子计算？"

python3 scripts/gemini_chat_runner.py \
  --mode report \
  --prompt "总结量子计算的核心机制"

python3 scripts/gemini_chat_runner.py \
  --conversation-url "https://gemini.google.com/app/..." \
  --prompt "继续展开第二部分"
```

Key flags:
- `--prompt`
- `--mode` (`fetch` / `fetch-with-sources` / `search` / `report`)
- `--conversation-url`
- `--save-report` (optional export only; not the meaning of `mode=report`)
- `--report-path`
- `--timeout-seconds`
- `--recovery-timeout-seconds`
- `--recovery-poll-ms`
- `--tab-label`

The runner currently returns structured JSON including fields such as:
- `ok`
- `mode`
- `wrapped_prompt`
- `answer`
- `conversationUrl`
- `errorCode`
- `pageState`
- `authState`
- `browserProfile`
- `browserTarget`
- `notificationNeeded`
- `partial`
- `debug`

## Default operating procedure

### 1) Open Gemini web

Use OpenClaw browser control with the OpenClaw-managed browser:
- profile: `openclaw`
- tab label: `gemini-monitor`
- url: `https://gemini.google.com/`
- transport: loopback Browser HTTP; if OpenClaw 2026.5.x returns `ERR_BROWSER_UNAUTHORIZED`, provide the current gateway shared secret via env or `--browser-token` / `--browser-password`

Use the stable tab label for opening/finding the tab, then use the concrete CDP `targetId` for Browser HTTP actions. OpenClaw 2026.5.x rejects `/act` calls when the action target does not match the request target.

### 2) Detect page state

Inspect whether Gemini web is usable.

Expected success signs:
- root area titled `Google Gemini`
- prompt textbox similar to `为 Gemini 输入提示`
- optional `登录` signals may exist; that alone does **not** mean chat is unusable if the editor is still present

### 3) Input strategy

Current runner strategy:
1. locate a visible Gemini editor (`.ql-editor`, `rich-textarea .ql-editor`, `div[contenteditable="true"]`, `textarea`), preferring contenteditable editors
2. prefer chunked contenteditable injection when possible
3. submit via the actual Gemini send button when visible
4. treat Enter only as a weak fallback
5. require post-submit confirmation signals before continuing to extraction

## 4) Wait for answer completion

After submission:
- confirm a `Gemini 说` block appears
- use the presence of `停止回答` / `Stop responding` button as the primary streaming signal
- treat stable repeated answer text as a completion hint
- avoid aggressive polling; prefer short, state-based waits

## 5) Extraction and shutdown strategy

Current extraction direction:
- anchor extraction around the latest `Gemini 说` answer block
- strip UI scaffolding such as `显示思路` / `立即回答` / footer disclaimers
- return the answer text directly when the user asked for Gemini's raw answer
- actively close the Gemini tab opened for the run after capture

This skill is intentionally single-shot and stateless: open → ask → capture → close.

## Failure handling

### Case A: page opens but no prompt box found
- retry with a fresh snapshot
- try `发起新对话` / `New chat`
- then rescan for the textbox

### Case B: type/fill fails on aria ref
- fallback to `evaluate`-based contenteditable injection

### Case C: prompt submitted but answer not visible yet
- check for `停止回答`
- check whether the editor cleared, copy action appeared, or another Gemini response signal appeared
- if present, wait and snapshot again
- if absent, fail as submission-not-confirmed rather than pretending extraction failed

### Case D: Gemini web unavailable
If Gemini web cannot be used, say so clearly and offer fallback:
- `gemini-cli-provider` for complex research/report generation
- `searxng-local` for ordinary search

## Prompting guidance

Use short, explicit prompts for best reliability.

Examples:
- `最佳的行车记录仪有哪些？`
- `请比较 iPhone 16 Pro 和 Pixel 10 Pro，列出优缺点。`
- `请总结这篇文章的核心观点，并用 5 条 bullet 输出。`

For stronger extraction reliability, you may wrap the user request:
- `请直接回答以下问题，并尽量结构化输出：<question>`

## Output mode

Current mode system is aligned with `chatgpt-chat`:

- `fetch`
  - submit the user's question
  - capture Gemini's answer
  - return structured result with the raw/lightly cleaned answer

- `fetch-with-sources`
  - wrap the question to explicitly request major sources
  - still return structured result, not a local file by default

- `search`
  - ask Gemini to use its web/search capability and list major sources

- `report`
  - ask Gemini to answer in a concise structured-report style
  - this still means structured result output first
  - local file export is optional and separate from the meaning of `mode=report`

## Practical notes

- Anonymous Gemini web access may still allow one-shot questions; do not assume login is required merely because a `登录` link exists.
- The page may show mixed Chinese/English labels (`发起新对话`, `New chat`). Treat them as equivalent.
- This skill is intentionally scoped to **single-turn browser automation**. Keep it simple and deterministic.
- Current MVP uses OpenClaw browser control as the primary automation surface; Playwright ideas may inform design, but they are not the default implementation path.
- The current runner is usable for project closeout in its current scope: single-turn Gemini Web ask/capture with structured JSON, thinking/answer separation, and copy-first probe with DOM fallback.
- Further work, if any, should be treated as enhancement work rather than blocker work for this phase.
