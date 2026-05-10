from __future__ import annotations

from .router import ModelRequest, ModelResponse


class OllamaAdapter:
    provider = "ollama"

    def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError("Ollama adapter is reserved for Phase 2 provider wiring.")

