from __future__ import annotations

import importlib.util
import sys
import unittest
from dataclasses import asdict
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "gemini-chat" / "scripts" / "gemini_chat_runner.py"
SPEC = importlib.util.spec_from_file_location("gemini_chat_runner", SCRIPT_PATH)
assert SPEC is not None
gemini_chat_runner = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = gemini_chat_runner
SPEC.loader.exec_module(gemini_chat_runner)


class GeminiSourceExtractionTest(unittest.TestCase):
    def test_sources_from_link_items_dedupes_and_filters_links(self) -> None:
        sources = gemini_chat_runner.sources_from_link_items(
            [
                {"text": "TechRadar", "href": "https://www.techradar.com/vehicle-tech/dash-cams"},
                {"text": "Duplicate", "href": "https://www.techradar.com/vehicle-tech/dash-cams"},
                {"text": "Login", "href": "https://accounts.google.com/signin"},
                {"text": "Script", "href": "javascript:void(0)"},
                {"text": "Fragment", "href": "#sources"},
                {"text": "Vortex Radar", "href": "https://www.vortexradar.com/best-dashcams/"},
            ]
        )

        self.assertEqual(
            [asdict(source) for source in sources],
            [
                {"text": "TechRadar", "href": "https://www.techradar.com/vehicle-tech/dash-cams"},
                {"text": "Vortex Radar", "href": "https://www.vortexradar.com/best-dashcams/"},
            ],
        )

    def test_sources_from_link_items_normalizes_empty_text(self) -> None:
        sources = gemini_chat_runner.sources_from_link_items(
            [{"text": "", "href": "https://www.nytimes.com/wirecutter/reviews/best-dash-cam/"}]
        )

        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0].text, "nytimes.com")

    def test_sources_from_link_items_allows_empty_results(self) -> None:
        self.assertEqual(gemini_chat_runner.sources_from_link_items([]), [])


if __name__ == "__main__":
    unittest.main()
