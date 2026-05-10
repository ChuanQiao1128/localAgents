from __future__ import annotations

import unittest
import json
from unittest.mock import patch

from orchestrator.tools.search_tools import SearchTools, TavilySearchProvider


class ResearchToolTests(unittest.TestCase):
    def test_mock_search_returns_bounded_results(self) -> None:
        results = SearchTools().search("personal finance onboarding UX", limit=3)

        self.assertEqual(len(results), 3)
        self.assertIn("personal finance", results[0].title)

    def test_search_rejects_empty_query_and_bad_limit(self) -> None:
        tools = SearchTools()
        with self.assertRaises(ValueError):
            tools.search("")
        with self.assertRaises(ValueError):
            tools.search("query", limit=0)

    def test_tavily_provider_parses_response(self) -> None:
        response = _FakeResponse(
            b'{"results":[{"title":"A","url":"https://example.com/a","content":"Summary","score":0.9}]}'
        )
        with patch("urllib.request.urlopen", return_value=response) as urlopen:
            provider = TavilySearchProvider(api_key="tvly-dev-test-key-with-enough-length-for-validation")
            results = provider.search("expense tracker", limit=1)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "A")
        request = urlopen.call_args.args[0]
        self.assertEqual(request.full_url, TavilySearchProvider.endpoint)
        self.assertEqual(
            request.headers["Authorization"],
            "Bearer tvly-dev-test-key-with-enough-length-for-validation",
        )
        body = json.loads(request.data.decode("utf-8"))
        self.assertNotIn("api_key", body)
        self.assertEqual(body["query"], "expense tracker")

    def test_tavily_requires_api_key(self) -> None:
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(ValueError):
                TavilySearchProvider()

    def test_tavily_rejects_placeholder_key(self) -> None:
        with self.assertRaises(ValueError):
            TavilySearchProvider(api_key="tvly-dev-your-key-here")


class _FakeResponse:
    def __init__(self, body: bytes):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self) -> bytes:
        return self.body


if __name__ == "__main__":
    unittest.main()
