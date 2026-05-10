from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from hashlib import sha1
from typing import Protocol


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    summary: str
    relevance: float = 0.0
    evidence_type: str = "research"


class SearchProvider(Protocol):
    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        ...


class MockSearchProvider:
    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        query_hash = sha1(query.encode("utf-8")).hexdigest()[:8]
        return [
            SearchResult(
                title=f"Mock source {index + 1}: {query}",
                url=f"https://example.local/research/{query_hash}/{index + 1}",
                summary=f"Mock evidence for query: {query}",
                relevance=max(0.0, 1.0 - index * 0.1),
                evidence_type="mock",
            )
            for index in range(limit)
        ]


class TavilySearchProvider:
    endpoint = "https://api.tavily.com/search"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        search_depth: str = "basic",
        include_answer: bool = False,
        timeout_seconds: int = 30,
    ):
        self.api_key = api_key or os.environ.get("TAVILY_API_KEY")
        self.search_depth = search_depth
        self.include_answer = include_answer
        self.timeout_seconds = timeout_seconds
        if not self.api_key:
            raise ValueError("TAVILY_API_KEY is required for TavilySearchProvider.")
        if _looks_like_placeholder_key(self.api_key):
            raise ValueError("TAVILY_API_KEY looks like a placeholder. Replace it with a real Tavily key.")

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        payload = {
            "query": query,
            "search_depth": self.search_depth,
            "include_answer": self.include_answer,
            "max_results": limit,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        project_id = os.environ.get("TAVILY_PROJECT")
        if project_id:
            headers["X-Project-ID"] = project_id
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Tavily search failed with HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Tavily search failed: {exc.reason}") from exc

        data = json.loads(body)
        results = data.get("results") or []
        parsed: list[SearchResult] = []
        for item in results[:limit]:
            parsed.append(
                SearchResult(
                    title=str(item.get("title") or item.get("url") or "Untitled"),
                    url=str(item.get("url") or ""),
                    summary=str(item.get("content") or item.get("snippet") or ""),
                    relevance=float(item.get("score") or 0.0),
                    evidence_type="tavily",
                )
            )
        return parsed


def default_search_provider(use_mock: bool = False) -> SearchProvider:
    if use_mock or not os.environ.get("TAVILY_API_KEY"):
        return MockSearchProvider()
    return TavilySearchProvider()


def _looks_like_placeholder_key(value: str) -> bool:
    return "your-key-here" in value or len(value.strip()) < 40


class SearchTools:
    def __init__(self, provider: SearchProvider | None = None):
        self.provider = provider or default_search_provider()

    def search(self, query: str, limit: int = 5) -> list[SearchResult]:
        query = query.strip()
        if not query:
            raise ValueError("Search query must not be empty.")
        if limit < 1 or limit > 20:
            raise ValueError("Search limit must be between 1 and 20.")
        return self.provider.search(query, limit)
