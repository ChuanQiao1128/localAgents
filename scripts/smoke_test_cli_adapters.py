#!/usr/bin/env python3
"""End-to-end smoke test for ClaudeCliAdapter and CodexCliAdapter.

Run this once after wiring up the CLIs to verify each adapter actually returns
parseable AgentResult JSON when calling the real CLI. Consumes a tiny amount of
subscription quota — the prompt is intentionally minimal.

Usage:
    python3 scripts/smoke_test_cli_adapters.py            # both
    python3 scripts/smoke_test_cli_adapters.py claude     # only Claude
    python3 scripts/smoke_test_cli_adapters.py codex      # only Codex

Prerequisites:
    claude /login                 # OAuth into your Claude subscription
    codex login                   # log into your ChatGPT subscription
    which claude codex            # both must be on PATH
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from textwrap import shorten

# Allow `python3 scripts/smoke_test_cli_adapters.py` to find the orchestrator
# package without setting PYTHONPATH.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from orchestrator.agents.base import StructuredOutputParser
from orchestrator.model import (
    ClaudeCliAdapter,
    CodexCliAdapter,
    ModelMessage,
    ModelRequest,
)


SYSTEM_PROMPT = (
    "You are a careful agent in a deterministic software workflow. "
    "Always reply with a single JSON object matching the AgentResult schema."
)

USER_PROMPT = (
    "Smoke test run. Pretend you finished a trivial task and return:\n"
    "  status='completed', summary='smoke test ok', "
    "artifacts=[], tool_calls=[], next_tasks=[], requires_approval=false.\n"
    "Return AgentResult JSON only, no commentary."
)


def _run(name: str, adapter, model: str) -> bool:
    print(f"\n=== {name} ({model}) ===")
    request = ModelRequest(
        model=model,
        messages=[
            ModelMessage(role="system", content=SYSTEM_PROMPT),
            ModelMessage(role="user", content=USER_PROMPT),
        ],
        temperature=0.0,
    )
    started = time.perf_counter()
    try:
        response = adapter.complete(request)
    except Exception as exc:  # noqa: BLE001 — surface anything for debugging
        print(f"  FAIL: {type(exc).__name__}: {exc}")
        return False
    elapsed = time.perf_counter() - started
    print(f"  latency_ms (CLI side):   {response.latency_ms}")
    print(f"  total wall-clock:        {elapsed*1000:.0f}ms")
    print(f"  raw content (preview):   {shorten(response.content, width=200)}")
    try:
        parsed = StructuredOutputParser().parse_agent_result(response.content)
    except Exception as exc:  # noqa: BLE001
        print(f"  FAIL: parser rejected output: {exc}")
        return False
    print(f"  parsed.status:           {parsed.status}")
    print(f"  parsed.summary:          {parsed.summary}")
    print(f"  parsed.artifacts:        {parsed.artifacts}")
    return parsed.status == "completed"


def main() -> int:
    targets = sys.argv[1:] or ["claude", "codex"]
    targets = [t.lower() for t in targets]

    results: dict[str, bool] = {}
    if "claude" in targets:
        results["claude"] = _run("Claude CLI", ClaudeCliAdapter(), "claude_cli:sonnet")
    if "codex" in targets:
        # Use the subscription's default model rather than pinning gpt-5
        # (which only API users can access).
        results["codex"] = _run("Codex CLI", CodexCliAdapter(), "codex_cli")

    print("\n=== summary ===")
    for name, ok in results.items():
        print(f"  {name}: {'OK' if ok else 'FAIL'}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
