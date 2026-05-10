from __future__ import annotations

from .router import ModelRequest, ModelResponse


class OpenRouterAdapter:
    provider = "openrouter"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError("OpenRouter adapter is reserved for Phase 2 provider wiring.")

