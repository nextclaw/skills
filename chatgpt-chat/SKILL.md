---
name: chatgpt-chat
description: Use ChatGPT Web (`https://chatgpt.com/`) for deterministic browser-based Q&A, source extraction, and single-turn report capture. Prefer this skill when the user explicitly wants ChatGPT web behavior, wants a browser-grounded answer with visible cited links, or wants ChatGPT web automation validated.
---

# ChatGPT Chat (browser-driven, single-turn)

This skill now has two layers:
- **skill contract / state machine**: defines when to use ChatGPT Web and how the flow should behave
- **local runner implementation**: `scripts/chatgpt_chat_runner.py` is the current recommended execution entrypoint

For local/manual use, assume the current working directory is:
- `/opt/workspace/skills/chatgpt-chat/`

Then run the runner with a **relative path**, for example:

```bash
python3 scripts/chatgpt_chat_runner.py \
  --prompt "What is quantum computing?"
```

Do **not** document machine-specific absolute paths as the normal usage example.

## When to use

Use this skill when all of the following are true:
- the user wants a **ChatGPT web** answer specifically, or wants ChatGPT web behavior validated
- the task is primarily a **single-turn** question/answer or report-capture flow
- the goal is to **submit one prompt, capture the answer, and optionally extract visible sources**

Use this skill especially for:
- validating ChatGPT web automation
- extracting ChatGPT web answers as markdown/text
- extracting visible source links from the answer area
- testing browser-grounded outputs against other tools

Do **not** use this skill for:
- routine web search → prefer `searxng-local`
- deep offline report writing that does not require ChatGPT web specifically
- long multi-turn browser chat unless the user explicitly wants ChatGPT web session behavior
- general coding or data processing unrelated to the ChatGPT web UI

## Model guidance

This skill should be run in a **deterministic, workflow-first style**.
The model's job is to follow the state machine, not improvise.

Use a stronger model only if:
- the page structure changed
- source extraction keeps failing
- recovery logic is needed after multiple browser failures

## Core state machine

Follow this exact order whenever possible:

1. Open `https://chatgpt.com/` with browser profile `openclaw`
2. Use a stable OpenClaw tab label, default `chatgpt-monitor`
3. Use the concrete CDP `targetId` for page actions
4. Verify page usability / login state
5. Locate the prompt textbox
6. Inject the prompt text
7. Wait for `发送提示` to appear
8. Click `发送提示`
9. Wait for URL to switch to `/c/...`
10. Wait for `你说：`
11. Wait for `ChatGPT 说：`
12. Extract answer text / markdown-ish body
13. Extract visible source links from the assistant article when requested
14. Return structured output

## Browser profile

Always prefer:
- profile: `openclaw`
- tab label: `chatgpt-monitor`

Use the existing logged-in browser state. Do not ask the user to log in again unless the page clearly requires it.

## Page-open procedure

### 1) Open ChatGPT web
Use browser open with:
- profile: `openclaw`
- url: `https://chatgpt.com/`

### 2) Snapshot the page
Take an aria snapshot and confirm the page is usable.

Expected success signs:
- root titled `ChatGPT`
- prompt textbox exists
- profile menu exists
- prompt placeholder such as `有问题，尽管问`

A visible `登录` signal or a broken prompt state means the skill should stop and report that ChatGPT web is not currently usable.

## Prompt injection strategy

Prefer this order:

1. Use browser `evaluate` to find `#prompt-textarea` or `[role="textbox"]`
2. Focus the element
3. Inject text via contenteditable/textbox-safe DOM operations
4. Dispatch input/change events as needed

Avoid relying on raw `Enter` submission as the primary path.

## Submission strategy

### Required rule
**Do not treat Enter as the primary submit path.**

Primary strategy:
1. after prompt injection, explicitly check for a button labeled `发送提示`
2. only when that button appears, click it
3. then assert URL transition to `/c/...`

This is the most reliable known path.

## Completion and extraction strategy

After clicking send:

1. wait for URL to change to `https://chatgpt.com/c/...`
2. snapshot again
3. confirm the presence of:
   - `你说：`
   - `ChatGPT 说：`
4. extract the latest assistant message
5. use `innerText` for robust text capture
6. when visible source links are requested, collect `a[href]` elements inside the assistant article

### Extraction target
Prefer the latest assistant message in the conversation region. Current ChatGPT Web builds may expose it as an `article`, a `data-message-author-role="assistant"` node, a message/testid container, or a markdown container.

Return:
- answer text
- conversation URL
- source links when requested

## Output modes

### 1) fetch mode
Use when the user wants ChatGPT's raw answer.

Return:
- assistant answer text
- optionally conversation URL

### 2) fetch-with-sources mode
Use when the user wants:
- the markdown/text answer
- visible cited links / source URLs

Return:
- answer text
- conversation URL
- source list as `{text, href}` items

### 3) report mode
Use when the user wants the result written to disk.

Preferred path:
- capture answer text
- normalize into markdown
- write to the explicit `--report-path` when provided
- otherwise default to the current working directory
- include a `Sources` section with URLs

## Failure handling

### Case A: no textbox found
- take a fresh snapshot
- retry once after a short wait
- if still missing, report that the ChatGPT prompt box is unavailable

### Case B: prompt injected but `发送提示` not present
- re-focus the textbox
- re-inject the prompt once
- check again for `发送提示`
- if still absent, fail clearly

### Case C: clicked send but URL did not enter `/c/...`
- verify whether the send button was actually enabled
- snapshot again
- if still on homepage, report submission failure clearly

### Case D: `ChatGPT 说：` missing
- wait briefly and snapshot again
- if the user bubble exists but assistant bubble does not, report that submission succeeded but answer extraction failed

### Case E: source extraction empty
- return the answer anyway
- clearly state that no visible source links were found in the assistant article

## Prompting guidance

For best reliability, wrap the user's request in a minimal explicit instruction only when needed.

Examples:
- `Answer in English. Answer the question below and include a "Sources" section listing the main sources or references you relied on, with links where available.`
- `Answer in English. Use web browsing/search if available. Answer the question below and include a "Sources" section listing the main sources or references you relied on, with links where available.`

Avoid overly long wrapper prompts unless the user asked for a formal report.

## Runner usage

Current recommended local entrypoint:
- `scripts/chatgpt_chat_runner.py`

Common examples:

```bash
python3 scripts/chatgpt_chat_runner.py \
  --prompt "What is quantum computing?"

python3 scripts/chatgpt_chat_runner.py \
  --mode report \
  --prompt "Summarize the main characteristics of popular North American dash cam brands."

python3 scripts/chatgpt_chat_runner.py \
  --conversation-url "https://chatgpt.com/c/..." \
  --prompt "继续展开第二部分"
```

Key flags:
- `--prompt`
- `--mode`
- `--conversation-url`
- `--save-report`
- `--report-path`
- `--timeout-seconds`
- `--recovery-timeout-seconds`
- `--recovery-poll-ms`
- `--tab-label`

The runner returns structured JSON including fields such as:
- `ok`
- `answer`
- `conversationUrl`
- `sources`
- `errorCode`
- `pageState`
- `authState`
- `extractionMode`
- `browserProfile`
- `browserTarget`
- `notificationNeeded`

## Practical notes

- This skill is intentionally **single-turn first**, but its copy selector is already designed around the **latest assistant reply**, so future multi-turn support does not need a selector redesign.
- The known stable send path is `inject -> wait send button -> click`.
- Preferred extraction path is now:
  1. latest assistant reply turn-level `Copy`
  2. `clipboard-read`
  3. `writeText` interception fallback
  4. DOM-markdown fallback
- For the best chance of getting native ChatGPT markdown, allow `chatgpt.com` to read the clipboard in the browser.
- `Copy code` inside code blocks is intentionally excluded; the runner targets the copy button that lives with turn-level actions such as feedback/share/retry.
- Browser-control transient issues such as `ERR_TAB_NOT_FOUND` now first use a short readiness retry window before reopening the tab once.
- The runner uses concrete CDP `targetId` values for page actions.
- The default transport is OpenClaw 2026.6.x CDP at `http://127.0.0.1:18800`; use `--cdp-url` only when `openclaw browser --browser-profile openclaw status` reports a different `cdpUrl`.
- Keep behavior deterministic; do not improvise browser exploration when the state machine already has a known path.

## Implementation status

Current implementation is no longer just a future direction:
- the state machine is defined in this skill doc
- the main runnable implementation lives in `scripts/chatgpt_chat_runner.py`
- turn-level `copy` extraction has been validated when clipboard-read permission is available
- notification delivery is currently expected to be handled by the upper orchestration layer, while the runner outputs notification contract fields
