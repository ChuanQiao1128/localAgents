"""Adapter that shells out to the `claude` CLI in non-interactive mode.

Uses the user's Claude subscription quota (no API tokens billed) when the user
is logged in via `claude /login` (OAuth) or has a long-lived token from
`claude setup-token`. Requires `claude` to be on PATH.

The adapter intentionally disables all tools so Claude only emits structured
text: our orchestrator's permission engine + tools layer handle the actual
filesystem/shell work.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from typing import Any, Callable

from .router import ModelMessage, ModelRequest, ModelResponse


AGENT_RESULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "status": {"type": "string"},
        "summary": {"type": "string"},
        "artifacts": {"type": "array", "items": {"type": "string"}},
        "tool_calls": {"type": "array"},
        "next_tasks": {"type": "array"},
        "requires_approval": {"type": "boolean"},
    },
    "required": ["status", "summary"],
    "additionalProperties": True,
}


JSON_OUTPUT_DIRECTIVE = (
    "\n\nIMPORTANT OUTPUT FORMAT: Reply with a single JSON object only. "
    "No markdown code fences, no commentary, no leading/trailing text. "
    "Required keys: status (string), summary (string). "
    "Optional keys: artifacts (string[]), tool_calls (array), "
    "next_tasks (array), requires_approval (boolean)."
)


class ClaudeCliAdapter:
    """Route ModelRequest through the Claude Code CLI.

    Notes on subscription auth:
      - We deliberately do NOT pass ``--bare``. ``--bare`` disables OAuth and
        keychain reads, which would force API-key billing. Without it, the CLI
        reads the user's logged-in subscription credentials.
      - We also disable tools and session persistence so the call is a pure
        structured-text generation, not an agentic session.
    """

    provider = "claude_cli"

    DEFAULT_MODEL = "sonnet"
    # 1200s (20 min) accommodates architecture-style phases that emit 4+
    # large structured files in one call. Override per-call by setting
    # CLAUDE_CLI_TIMEOUT_DURATION on the env or passing timeout_seconds.
    DEFAULT_TIMEOUT_SECONDS = 1200

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout_seconds: int | None = None,
        enforce_schema: bool = True,
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
        which: Callable[[str], str | None] | None = None,
    ):
        self.executable = executable or os.environ.get("CLAUDE_CLI_PATH") or "claude"
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else int(os.environ.get("CLAUDE_CLI_TIMEOUT_SECONDS", str(self.DEFAULT_TIMEOUT_SECONDS)))
        )
        self.enforce_schema = enforce_schema
        self._runner = runner or subprocess.run
        self._which = which or shutil.which

    def complete(self, request: ModelRequest) -> ModelResponse:
        if not os.path.isabs(self.executable) and self._which(self.executable) is None:
            raise RuntimeError(
                f"Claude CLI executable '{self.executable}' not found on PATH. "
                f"Install Claude Code or set CLAUDE_CLI_PATH."
            )

        model = self._resolve_model(request.model)
        system_prompt = self._extract_system_prompt(request.messages)
        user_prompt = self._extract_user_prompt(request.messages)
        if not user_prompt.strip():
            raise ValueError("Claude CLI adapter requires non-empty user prompt.")

        cmd: list[str] = [
            self.executable,
            "-p",
            "--output-format", "json",
            "--no-session-persistence",
            # Cold-start trims: skip project/local CLAUDE.md and slash commands.
            # We deliberately do NOT pass --strict-mcp-config / --mcp-config "{}"
            # because some Claude CLI versions reject the empty-object form,
            # causing the call to fail before anything else runs.
            "--setting-sources", "user",
            "--disable-slash-commands",
        ]
        if model:
            cmd += ["--model", model]
        # Append the JSON-output directive to the system prompt rather than
        # using --json-schema. Older/newer CLIs differ in schema support and
        # have been observed to emit empty results when schema validation
        # fails silently. Asking for JSON in the prompt + relying on
        # StructuredOutputParser's regex fallback is more portable.
        framed_system = (system_prompt or "").strip()
        framed_system = (framed_system + JSON_OUTPUT_DIRECTIVE) if framed_system else JSON_OUTPUT_DIRECTIVE.lstrip()
        cmd += ["--system-prompt", framed_system]

        started = time.perf_counter()
        try:
            result = self._runner(
                cmd,
                input=user_prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(
                f"Claude CLI timed out after {self.timeout_seconds}s. "
                f"Set CLAUDE_CLI_TIMEOUT_SECONDS to override."
            ) from exc
        latency_ms = int((time.perf_counter() - started) * 1000)

        if result.returncode != 0:
            stderr_tail = (result.stderr or "").strip()[-500:]
            raise RuntimeError(
                f"Claude CLI exited {result.returncode}: {stderr_tail or '<no stderr>'}"
            )

        envelope = self._parse_envelope(result.stdout)
        content = self._extract_content(envelope)
        usage = envelope.get("usage") if isinstance(envelope, dict) else None
        input_tokens = self._safe_int(usage, "input_tokens")
        output_tokens = self._safe_int(usage, "output_tokens")

        return ModelResponse(
            provider=self.provider,
            model=model or self.DEFAULT_MODEL,
            content=content,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=0.0,  # subscription quota, not metered
        )

    def _resolve_model(self, raw: str) -> str:
        if ":" in raw:
            tail = raw.split(":", 1)[1].strip()
            return tail or self.DEFAULT_MODEL
        if raw and raw not in {"claude_cli", "claude-cli"}:
            return raw
        return self.DEFAULT_MODEL

    @staticmethod
    def _extract_system_prompt(messages: list[ModelMessage]) -> str:
        return "\n\n".join(m.content for m in messages if m.role == "system")

    @staticmethod
    def _extract_user_prompt(messages: list[ModelMessage]) -> str:
        return "\n\n".join(m.content for m in messages if m.role != "system")

    @staticmethod
    def _parse_envelope(stdout: str) -> dict[str, Any]:
        stripped = stdout.strip()
        if not stripped:
            raise RuntimeError("Claude CLI returned empty stdout.")
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError as exc:
            preview = stripped[:500]
            raise RuntimeError(f"Claude CLI output was not valid JSON: {preview}") from exc
        if not isinstance(loaded, dict):
            raise RuntimeError(
                f"Claude CLI output JSON was not an object: {type(loaded).__name__}"
            )
        return loaded

    @staticmethod
    def _extract_content(envelope: dict[str, Any]) -> str:
        # `claude -p --output-format json` returns {"result": "...", ...}.
        # When --json-schema is supplied the result is the structured JSON
        # already, but still serialized as a string. Either way we hand the
        # string back to StructuredOutputParser.
        if "result" in envelope:
            value = envelope["result"]
        elif "text" in envelope:
            value = envelope["text"]
        else:
            raise RuntimeError(
                f"Claude CLI envelope missing 'result' field: keys={list(envelope)}"
            )
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _safe_int(payload: Any, key: str) -> int:
        if not isinstance(payload, dict):
            return 0
        try:
            return int(payload.get(key, 0) or 0)
        except (TypeError, ValueError):
            return 0
