from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "chatgpt-chat" / "scripts" / "chatgpt_chat_runner.py"
SPEC = importlib.util.spec_from_file_location("chatgpt_chat_runner", SCRIPT_PATH)
assert SPEC is not None
chatgpt_chat_runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = chatgpt_chat_runner
SPEC.loader.exec_module(chatgpt_chat_runner)


class FakeClient:
    def __init__(self) -> None:
        self.snapshot_calls = 0

    def snapshot(self, **_: object) -> dict[str, object]:
        self.snapshot_calls += 1
        if self.snapshot_calls >= 2:
            return {"url": "https://chatgpt.com/c/test-conversation"}
        return {"url": "https://chatgpt.com/"}


class FakeOpenClient:
    def __init__(self) -> None:
        self.open_calls = 0

    def open_tab(self, *_: object) -> dict[str, object]:
        self.open_calls += 1
        if self.open_calls == 1:
            raise RuntimeError('CDP Target.createTarget failed: {"error":"Browser launch is cooling down. Retry in 30s"}')
        return {
            "targetId": "target-1",
            "tabId": "tab-1",
            "label": "chatgpt-monitor",
            "url": "https://chatgpt.com/",
        }


class ChatGPTReadinessTest(unittest.TestCase):
    def test_wait_for_page_ready_allows_hydration_delay(self) -> None:
        req = chatgpt_chat_runner.Request(prompt="hello", page_ready_timeout_seconds=30)
        states = [
            {"ok": True, "state": "unknown", "authState": "authenticated-or-unknown", "hasTextbox": False, "href": "https://chatgpt.com/"},
            {"ok": True, "state": "unknown", "authState": "authenticated-or-unknown", "hasTextbox": False, "href": "https://chatgpt.com/"},
            {"ok": True, "state": "ready", "authState": "authenticated-or-usable", "hasTextbox": True, "href": "https://chatgpt.com/"},
        ]
        debug: dict[str, object] = {}

        with (
            patch.object(chatgpt_chat_runner, "_detect_page_state", side_effect=states) as detect,
            patch.object(chatgpt_chat_runner, "_wait", return_value=None),
        ):
            state = chatgpt_chat_runner._wait_for_page_ready_state(FakeClient(), req, "target-1", debug)

        self.assertEqual(state["state"], "ready")
        self.assertEqual(detect.call_count, 3)
        self.assertIn("pageStateChecks", debug)

    def test_wait_for_page_ready_returns_blocking_states_immediately(self) -> None:
        req = chatgpt_chat_runner.Request(prompt="hello", page_ready_timeout_seconds=30)
        debug: dict[str, object] = {}
        blocked = {
            "ok": True,
            "state": "human_verification",
            "authState": "authenticated-or-unknown",
            "hasTextbox": False,
            "href": "https://chatgpt.com/",
            "verificationMatched": ["Verify you are human"],
        }

        with (
            patch.object(chatgpt_chat_runner, "_detect_page_state", return_value=blocked) as detect,
            patch.object(chatgpt_chat_runner, "_wait", return_value=None),
        ):
            state = chatgpt_chat_runner._wait_for_page_ready_state(FakeClient(), req, "target-1", debug)

        self.assertEqual(state["state"], "human_verification")
        self.assertEqual(detect.call_count, 1)

    def test_submit_retries_when_editor_text_remains_on_homepage(self) -> None:
        req = chatgpt_chat_runner.Request(prompt="hello", submit_retry_after_seconds=0, submit_timeout_seconds=10)
        client = FakeClient()
        debug: dict[str, object] = {}
        submit_state = {
            "ok": True,
            "href": "https://chatgpt.com/",
            "hasEditor": True,
            "editorTextLength": 12,
            "hasSendButton": True,
            "sendDisabled": False,
            "hasStopButton": False,
            "assistantCount": 0,
        }

        with (
            patch.object(chatgpt_chat_runner, "_ensure_prompt_injected", return_value={"ok": True}),
            patch.object(chatgpt_chat_runner, "_find_send_button", return_value={"ok": True}),
            patch.object(chatgpt_chat_runner, "_click_send", return_value={"ok": True}),
            patch.object(chatgpt_chat_runner, "_submission_state", return_value=submit_state),
            patch.object(chatgpt_chat_runner, "_fallback_submit", return_value={"ok": True, "method": "reclick-send"}) as fallback,
            patch.object(chatgpt_chat_runner, "_wait", return_value=None),
        ):
            url, error = chatgpt_chat_runner._submit_prompt_and_get_conversation_url(client, req, "target-1", debug)

        self.assertIsNone(error)
        self.assertEqual(url, "https://chatgpt.com/c/test-conversation")
        fallback.assert_called_once()
        self.assertIn("submitFallback", debug)
        self.assertIn("submitPolls", debug)

    def test_open_ready_tab_retries_transient_cdp_failures(self) -> None:
        req = chatgpt_chat_runner.Request(prompt="hello")
        client = FakeOpenClient()
        debug: dict[str, object] = {}
        result = chatgpt_chat_runner.Result(ok=False, mode="fetch-with-sources", prompt="hello", wrapped_prompt="hello")

        with (
            patch.object(chatgpt_chat_runner, "_wait_until_tab_ready", return_value=None),
            patch.object(chatgpt_chat_runner, "_wait", return_value=None),
            patch.object(chatgpt_chat_runner.time, "sleep", return_value=None) as sleep,
        ):
            target_id = chatgpt_chat_runner._open_ready_tab(client, req, "https://chatgpt.com/", debug, result)

        self.assertEqual(target_id, "target-1")
        self.assertEqual(client.open_calls, 2)
        sleep.assert_called_once_with(30.0)
        self.assertEqual(debug["openTabAttempts"][0]["retryable"], True)


if __name__ == "__main__":
    unittest.main()
