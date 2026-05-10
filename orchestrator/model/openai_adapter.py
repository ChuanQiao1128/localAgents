from __future__ import annotations

from .router import ModelRequest, ModelResponse


class OpenAIAdapter:
    provider = "openai"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError("OpenAI adapter is reserved for Phase 2 provider wiring.")

