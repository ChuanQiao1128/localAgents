from __future__ import annotations

from .router import ModelRequest, ModelResponse


class AnthropicAdapter:
    provider = "anthropic"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError("Anthropic adapter is reserved for Phase 2 provider wiring.")

