# Local Patches

## 2026-03-09
- file: SKILL.md
- change: Created local `gemini-chat` skill for single-turn Gemini web Q&A using OpenClaw browser automation.
- reason: Separate Gemini web chat automation from `gemini-cli-provider` (CLI) and `searxng-local` (daily search).
- risk: medium

## 2026-03-09
- file: SKILL.md
- change: Narrowed `gemini-chat` to fetch-only mode; removed brief/report-style positioning.
- reason: Keep the skill deterministic and tightly scoped to Gemini web answer capture.
- risk: low

## 2026-03-09
- file: SKILL.md
- change: Added mandatory shutdown rule: close the opened Gemini browser page after capturing the answer.
- reason: Keep the skill single-shot, stateless, and avoid stale tabs/session pollution.
- risk: low
