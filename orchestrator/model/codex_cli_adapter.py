"""Adapter that shells out to OpenAI's `codex` CLI (Codex) in non-interactive mode.

Uses the user's ChatGPT subscription quota when the user is logged in via
`codex login` (no API tokens billed). Requires `codex` to be on PATH.

The adapter forces ``--sandbox read-only`` so Codex cannot edit files or run
shell on its own — our orchestrator's permission engine and tool layer handle
those side-effects. The CLI is used as a structured-text generator only.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

from .router import ModelMessage, ModelRequest, ModelResponse


class CodexCliAdapter:
    """Route ModelRequest through the Codex CLI."""

    provider = "codex_cli"

    # Codex with a ChatGPT subscription does NOT accept arbitrary OpenAI model
    # names like "gpt-5" — those are API-only. With an empty default we let
    # the codex CLI pick whatever the subscription is entitled to.
    DEFAULT_MODEL = ""
    DEFAULT_TIMEOUT_SECONDS = 1200

    def __init__(
        self,
        *,
        executable: str | None = None,
        timeout_seconds: int | None = None,
        sandbox: str = "read-only",
        runner: Callable[..., subprocess.CompletedProcess] | None = None,
        which: Callable[[str], str | None] | None = None,
        tempdir: str | None = None,
    ):
        self.executable = executable or os.environ.get("CODEX_CLI_PATH") or "codex"
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else int(os.environ.get("CODEX_CLI_TIMEOUT_SECONDS", str(self.DEFAULT_TIMEOUT_SECONDS)))
        )
        self.sandbox = sandbox
        self._runner = runner or subprocess.run
        self._which = which or shutil.which
        self._tempdir = tempdir

    def complete(self, request: ModelRequest) -> ModelResponse:
        if not os.path.isabs(self.executable) and self._which(self.executable) is None:
            raise RuntimeError(
                f"Codex CLI executable '{self.executable}' not found on PATH. "
                f"Install Codex CLI (https://github.com/openai/codex) or set CODEX_CLI_PATH."
            )

        model = self._resolve_model(request.model)
        prompt = self._merge_messages(request.messages)
        if not prompt.strip():
            raise ValueError("Codex CLI adapter requires non-empty prompt.")

        with tempfile.NamedTemporaryFile(
            mode="w+",
            suffix=".txt",
            delete=False,
            dir=self._tempdir,
            encoding="utf-8",
        ) as fh:
            output_path = Path(fh.name)

        try:
            cmd: list[str] = [
                self.executable,
                "exec",
                "--skip-git-repo-check",
                "--sandbox", self.sandbox,
                "--output-last-message", str(output_path),
            ]
            # Only pass --model when the caller explicitly named one. With
            # ChatGPT-account auth, only the subscription-allowed model is
            # accepted; passing the wrong name (e.g. gpt-5) errors with HTTP
            # 400. Letting codex pick its default is safest.
            if model and model != self.DEFAULT_MODEL:
                cmd += ["--model", model]
            # `-` tells codex to read the prompt from stdin
            cmd += ["-"]

            started = time.perf_counter()
            try:
                result = self._runner(
                    cmd,
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    check=False,
                )
            except subprocess.TimeoutExpired as exc:
                raise TimeoutError(
                    f"Codex CLI timed out after {self.timeout_seconds}s. "
                    f"Set CODEX_CLI_TIMEOUT_SECONDS to override."
                ) from exc
            latency_ms = int((time.perf_counter() - started) * 1000)

            if result.returncode != 0:
                stderr_tail = (result.stderr or "").strip()[-500:]
                raise RuntimeError(
                    f"Codex CLI exited {result.returncode}: {stderr_tail or '<no stderr>'}"
                )

            content = self._read_last_message(output_path, fallback_stdout=result.stdout)
        finally:
            try:
                output_path.unlink()
            except FileNotFoundError:
                pass

        return ModelResponse(
            provider=self.provider,
            model=model or "codex-default",
            content=content,
            input_tokens=0,  # Codex CLI does not expose token usage in non-JSON mode
            output_tokens=0,
            latency_ms=latency_ms,
            cost_usd=0.0,  # subscription quota, not metered
        )

    def _resolve_model(self, raw: str) -> str:
        if ":" in raw:
            tail = raw.split(":", 1)[1].strip()
            # `codex_cli:default` and `codex_cli:` both mean "let codex pick"
            if tail in {"", "default"}:
                return self.DEFAULT_MODEL
            return tail
        if raw and raw not in {"codex_cli", "codex-cli"}:
            return raw
        return self.DEFAULT_MODEL

    @staticmethod
    def _merge_messages(messages: list[ModelMessage]) -> str:
        # Codex CLI takes a single prompt — we prepend system content as a
        # framing block so the model treats it as instructions.
        system_parts = [m.content for m in messages if m.role == "system" and m.content.strip()]
        user_parts = [m.content for m in messages if m.role != "system"]
        sections: list[str] = []
        if system_parts:
            sections.append("## System Instructions\n" + "\n\n".join(system_parts))
        if user_parts:
            sections.append("## Task\n" + "\n\n".join(user_parts))
        return "\n\n".join(sections).strip()

    @staticmethod
    def _read_last_message(path: Path, fallback_stdout: str) -> str:
        try:
            content = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            content = ""
        content = content.strip()
        if content:
            return content
        # Fallback: codex sometimes writes the final message to stdout when
        # --output-last-message is unsupported. Strip ANSI/control noise.
        return (fallback_stdout or "").strip()
