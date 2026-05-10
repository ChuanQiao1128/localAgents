from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class ModelMessage:
    role: str
    content: str


@dataclass(frozen=True)
class ModelRequest:
    model: str
    messages: list[ModelMessage]
    temperature: float = 0.2
    response_format: str | None = "agent_result"
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResponse:
    provider: str
    model: str
    content: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: float = 0.0


class ModelAdapter(Protocol):
    provider: str

    def complete(self, request: ModelRequest) -> ModelResponse:
        ...


class ModelRouter:
    def __init__(self, adapters: list[ModelAdapter] | None = None):
        self.adapters: dict[str, ModelAdapter] = {}
        for adapter in adapters or _default_adapters():
            self.register(adapter)

    def register(self, adapter: ModelAdapter) -> None:
        self.adapters[adapter.provider] = adapter

    def complete(self, request: ModelRequest, provider: str | None = None) -> ModelResponse:
        selected_provider = provider or _provider_from_model(request.model)
        if selected_provider not in self.adapters:
            available = ", ".join(sorted(self.adapters)) or "none"
            raise KeyError(f"No model adapter registered for {selected_provider}. Available: {available}")
        return self.adapters[selected_provider].complete(request)


class StubModelAdapter:
    provider = "stub"

    def complete(self, request: ModelRequest) -> ModelResponse:
        started = time.perf_counter()
        prompt = "\n".join(message.content for message in request.messages)
        content = json.dumps(
            {
                "status": "completed",
                "summary": _stub_summary(prompt),
                "artifacts": [],
                "tool_calls": [],
                "next_tasks": [],
                "requires_approval": False,
            },
            ensure_ascii=False,
        )
        return ModelResponse(
            provider=self.provider,
            model=request.model,
            content=content,
            input_tokens=_rough_token_count(prompt),
            output_tokens=_rough_token_count(content),
            latency_ms=int((time.perf_counter() - started) * 1000),
            cost_usd=0.0,
        )


def _provider_from_model(model: str) -> str:
    if model.startswith("claude_cli") or model.startswith("claude-cli"):
        return "claude_cli"
    if model.startswith("codex_cli") or model.startswith("codex-cli"):
        return "codex_cli"
    if model.startswith("openai:"):
        return "openai"
    if model.startswith("anthropic:"):
        return "anthropic"
    if model.startswith("ollama:"):
        return "ollama"
    if model.startswith("openrouter:"):
        return "openrouter"
    if model in {"local-stub", "stub"} or model.startswith("stub:"):
        return "stub"
    return "stub"


def _default_adapters() -> list[ModelAdapter]:
    """Build the default adapter set lazily so tests can override.

    Stub is always registered as a safe fallback. The CLI adapters are
    registered eagerly because they only fail at call-time (when the CLI is
    actually invoked) — registration itself is cheap.
    """
    # Local imports keep router.py free of CLI-adapter side-effects when only
    # the stub is needed (e.g. fast unit tests of unrelated modules).
    from .claude_cli_adapter import ClaudeCliAdapter
    from .codex_cli_adapter import CodexCliAdapter

    return [
        StubModelAdapter(),
        ClaudeCliAdapter(),
        CodexCliAdapter(),
    ]


def _rough_token_count(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text.split()))


def _stub_summary(prompt: str) -> str:
    first_line = next((line.strip() for line in prompt.splitlines() if line.strip()), "No prompt")
    return f"Stub model completed request: {first_line[:120]}"

