from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "chatgpt-chat" / "scripts" / "chatgpt_chat_runner.py"
SPEC = importlib.util.spec_from_file_location("chatgpt_chat_runner_cdp_test", SCRIPT_PATH)
assert SPEC is not None
chatgpt_chat_runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = chatgpt_chat_runner
SPEC.loader.exec_module(chatgpt_chat_runner)


class FakeCdpClient(chatgpt_chat_runner.BrowserClient):
    def __init__(self) -> None:
        super().__init__(cdp_url="http://127.0.0.1:18800")
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.list_calls = 0

    def request_json(self, path: str, timeout: int = 20) -> object:
        if path == "/json/version":
            return {"webSocketDebuggerUrl": "ws://browser"}
        if path == "/json/list":
            self.list_calls += 1
            return [
                {
                    "id": "target-1",
                    "type": "page",
                    "url": "https://chatgpt.com/",
                    "title": "ChatGPT",
                    "webSocketDebuggerUrl": "ws://page",
                }
            ]
        raise AssertionError(path)

    def _send_cdp(
        self,
        ws_url: str,
        method: str,
        params: dict[str, object] | None = None,
        timeout: int = 20,
    ) -> object:
        self.calls.append((method, params or {}))
        if method == "Target.createTarget":
            return {"targetId": "target-1"}
        if method == "Runtime.evaluate":
            return {"result": {"value": {"ok": True, "href": "https://chatgpt.com/"}}}
        if method == "Target.closeTarget":
            return {"success": True}
        raise AssertionError(method)


class CdpBrowserClientTest(unittest.TestCase):
    def test_tabs_parse_page_targets_from_json_list(self) -> None:
        client = FakeCdpClient()

        tabs = client.tabs("openclaw")

        self.assertEqual(tabs[0]["targetId"], "target-1")
        self.assertEqual(tabs[0]["wsUrl"], "ws://page")

    def test_open_tab_uses_target_create_target(self) -> None:
        client = FakeCdpClient()

        opened = client.open_tab("https://chatgpt.com/", "openclaw", "chatgpt-monitor")

        self.assertEqual(opened["targetId"], "target-1")
        self.assertEqual(client.calls[0][0], "Target.createTarget")
        self.assertEqual(client.calls[0][1]["url"], "https://chatgpt.com/")

    def test_evaluate_returns_plain_object_result(self) -> None:
        client = FakeCdpClient()

        result = client.act(profile="openclaw", payload={"kind": "evaluate", "targetId": "target-1", "fn": "() => ({ ok: true })"})

        self.assertEqual(result["result"], {"ok": True, "href": "https://chatgpt.com/"})
        self.assertEqual(client.calls[-1][0], "Runtime.evaluate")

    def test_close_tab_uses_target_close_target(self) -> None:
        client = FakeCdpClient()

        client.close_tab("target-1", "openclaw")

        self.assertEqual(client.calls[-1], ("Target.closeTarget", {"targetId": "target-1"}))

    def test_cdp_unavailable_maps_to_specific_error_code(self) -> None:
        req = chatgpt_chat_runner.Request(prompt="hello", cdp_url="http://127.0.0.1:9")

        result = chatgpt_chat_runner.execute_state_machine(req)

        self.assertFalse(result.ok)
        self.assertEqual(result.errorCode, "ERR_BROWSER_CDP_UNAVAILABLE")
        self.assertEqual(result.debug["browserTransport"], "cdp")
        self.assertEqual(result.debug["cdpUrl"], "http://127.0.0.1:9")


if __name__ == "__main__":
    unittest.main()
