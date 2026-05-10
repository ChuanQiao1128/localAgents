"""Mock-based unit tests for ClaudeCliAdapter and CodexCliAdapter.

These tests exercise the adapters without invoking the real CLIs so they
consume zero subscription quota. A separate scripts/smoke_test_cli_adapters.py
performs a real invocation when the user wants end-to-end verification.
"""
from __future__ import annotations

import json
import subprocess
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from orchestrator.model import (
    ClaudeCliAdapter,
    CodexCliAdapter,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    ModelRouter,
)
from orchestrator.model.router import _provider_from_model


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=["fake"], returncode=returncode, stdout=stdout, stderr=stderr)


def _agent_result_json() -> str:
    return json.dumps(
        {
            "status": "completed",
            "summary": "looks good",
            "artifacts": ["docs/x.md"],
            "tool_calls": [],
            "next_tasks": [],
            "requires_approval": False,
        },
        ensure_ascii=False,
    )


class ClaudeCliAdapterTests(unittest.TestCase):
    def _adapter(self, **kwargs: Any) -> tuple[ClaudeCliAdapter, MagicMock]:
        runner = MagicMock()
        adapter = ClaudeCliAdapter(
            executable="/usr/local/bin/claude",
            timeout_seconds=5,
            runner=runner,
            which=lambda _: "/usr/local/bin/claude",
            **kwargs,
        )
        return adapter, runner

    def _request(self, *, model: str = "claude_cli:sonnet") -> ModelRequest:
        return ModelRequest(
            model=model,
            messages=[
                ModelMessage(role="system", content="You are a careful agent."),
                ModelMessage(role="user", content="Generate a result."),
            ],
            temperature=0.3,
        )

    def test_complete_returns_parsed_envelope(self) -> None:
        adapter, runner = self._adapter()
        envelope = {"result": _agent_result_json(), "usage": {"input_tokens": 12, "output_tokens": 34}}
        runner.return_value = _completed(stdout=json.dumps(envelope))

        response = adapter.complete(self._request())

        self.assertIsInstance(response, ModelResponse)
        self.assertEqual(response.provider, "claude_cli")
        self.assertEqual(response.model, "sonnet")
        self.assertEqual(response.input_tokens, 12)
        self.assertEqual(response.output_tokens, 34)
        self.assertEqual(response.cost_usd, 0.0)
        parsed = json.loads(response.content)
        self.assertEqual(parsed["status"], "completed")

    def test_command_includes_expected_flags(self) -> None:
        adapter, runner = self._adapter()
        runner.return_value = _completed(stdout=json.dumps({"result": _agent_result_json()}))

        adapter.complete(self._request())

        cmd = runner.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/local/bin/claude")
        self.assertIn("-p", cmd)
        self.assertIn("--output-format", cmd)
        self.assertIn("json", cmd)
        self.assertIn("--no-session-persistence", cmd)
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")
        self.assertIn("--system-prompt", cmd)
        # The system prompt must include the original role plus the JSON
        # output directive appended by the adapter.
        framed = cmd[cmd.index("--system-prompt") + 1]
        self.assertIn("You are a careful agent.", framed)
        self.assertIn("Reply with a single JSON object only", framed)
        # We deliberately stopped passing --json-schema and --tools ""; the
        # former silently emptied results on some CLI versions.
        self.assertNotIn("--json-schema", cmd)
        self.assertNotIn("--tools", cmd)
        # crucial: --bare must NOT be passed (it disables OAuth)
        self.assertNotIn("--bare", cmd)

    def test_user_prompt_passed_via_stdin(self) -> None:
        adapter, runner = self._adapter()
        runner.return_value = _completed(stdout=json.dumps({"result": "x"}))

        adapter.complete(self._request())

        kwargs = runner.call_args.kwargs
        self.assertEqual(kwargs["input"], "Generate a result.")
        self.assertTrue(kwargs["text"])
        self.assertTrue(kwargs["capture_output"])

    def test_nonzero_exit_raises(self) -> None:
        adapter, runner = self._adapter()
        runner.return_value = _completed(returncode=1, stderr="boom")

        with self.assertRaises(RuntimeError) as ctx:
            adapter.complete(self._request())
        self.assertIn("exited 1", str(ctx.exception))
        self.assertIn("boom", str(ctx.exception))

    def test_timeout_raises_timeout_error(self) -> None:
        adapter, runner = self._adapter()
        runner.side_effect = subprocess.TimeoutExpired(cmd="claude", timeout=5)

        with self.assertRaises(TimeoutError):
            adapter.complete(self._request())

    def test_invalid_json_envelope_raises(self) -> None:
        adapter, runner = self._adapter()
        runner.return_value = _completed(stdout="not json at all")

        with self.assertRaises(RuntimeError) as ctx:
            adapter.complete(self._request())
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_empty_user_prompt_raises_value_error(self) -> None:
        adapter, _ = self._adapter()
        request = ModelRequest(
            model="claude_cli:sonnet",
            messages=[ModelMessage(role="system", content="sys")],
        )
        with self.assertRaises(ValueError):
            adapter.complete(request)

    def test_missing_executable_raises(self) -> None:
        runner = MagicMock()
        adapter = ClaudeCliAdapter(
            executable="claude",
            runner=runner,
            which=lambda _: None,
        )
        with self.assertRaises(RuntimeError) as ctx:
            adapter.complete(self._request())
        self.assertIn("not found", str(ctx.exception))
        runner.assert_not_called()

    def test_default_model_when_unspecified(self) -> None:
        adapter, runner = self._adapter()
        runner.return_value = _completed(stdout=json.dumps({"result": "x"}))

        adapter.complete(ModelRequest(model="claude_cli", messages=[ModelMessage(role="user", content="hi")]))
        cmd = runner.call_args.args[0]
        self.assertEqual(cmd[cmd.index("--model") + 1], "sonnet")

    def test_envelope_with_object_result_is_serialized(self) -> None:
        adapter, runner = self._adapter()
        # When --json-schema is enforced, claude may emit the structured object directly.
        runner.return_value = _completed(stdout=json.dumps({"result": {"status": "completed", "summary": "ok"}}))

        response = adapter.complete(self._request())
        parsed = json.loads(response.content)
        self.assertEqual(parsed["status"], "completed")


class CodexCliAdapterTests(unittest.TestCase):
    def _adapter(self, *, output_text: str = _agent_result_json(), tmp_path: Path | None = None, **kwargs: Any) -> tuple[CodexCliAdapter, MagicMock, Path]:
        # When the adapter writes the temp file path, the mocked runner has to
        # pretend Codex wrote the result there.
        runner = MagicMock()
        captured: dict[str, Path] = {}

        def fake_runner(cmd: list[str], **rkwargs: Any) -> subprocess.CompletedProcess:
            # find --output-last-message <path>
            idx = cmd.index("--output-last-message")
            path = Path(cmd[idx + 1])
            path.write_text(output_text, encoding="utf-8")
            captured["path"] = path
            return _completed(stdout="streaming events ignored", returncode=0)

        runner.side_effect = fake_runner
        adapter = CodexCliAdapter(
            executable="/usr/local/bin/codex",
            timeout_seconds=5,
            runner=runner,
            which=lambda _: "/usr/local/bin/codex",
            tempdir=str(tmp_path) if tmp_path else None,
            **kwargs,
        )
        # Return a placeholder Path; tests that need it should read captured.
        return adapter, runner, Path(captured.get("path", Path("/dev/null")))

    def _request(self, *, model: str = "codex_cli") -> ModelRequest:
        return ModelRequest(
            model=model,
            messages=[
                ModelMessage(role="system", content="System rules."),
                ModelMessage(role="user", content="Do the thing."),
            ],
        )

    def test_complete_returns_last_message(self) -> None:
        adapter, _runner, _path = self._adapter()
        response = adapter.complete(self._request())
        self.assertEqual(response.provider, "codex_cli")
        # Default model resolves to "codex-default" placeholder when unset
        # (we no longer pin gpt-5 because ChatGPT-account auth rejects it).
        self.assertEqual(response.model, "codex-default")
        self.assertEqual(response.cost_usd, 0.0)
        parsed = json.loads(response.content)
        self.assertEqual(parsed["status"], "completed")

    def test_command_uses_read_only_sandbox_and_skip_git(self) -> None:
        adapter, runner, _ = self._adapter()
        adapter.complete(self._request())
        cmd = runner.call_args.args[0]
        self.assertEqual(cmd[0], "/usr/local/bin/codex")
        self.assertEqual(cmd[1], "exec")
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertIn("--sandbox", cmd)
        self.assertEqual(cmd[cmd.index("--sandbox") + 1], "read-only")
        self.assertIn("--output-last-message", cmd)
        # No --model when caller did not specify one — we let codex pick the
        # subscription's default.
        self.assertNotIn("--model", cmd)
        # last argument is "-" so codex reads prompt from stdin
        self.assertEqual(cmd[-1], "-")

    def test_command_passes_model_when_explicitly_named(self) -> None:
        adapter, runner, _ = self._adapter()
        adapter.complete(self._request(model="codex_cli:o3"))
        cmd = runner.call_args.args[0]
        self.assertIn("--model", cmd)
        self.assertEqual(cmd[cmd.index("--model") + 1], "o3")

    def test_prompt_includes_system_and_user_sections(self) -> None:
        adapter, runner, _ = self._adapter()
        adapter.complete(self._request())
        stdin = runner.call_args.kwargs["input"]
        self.assertIn("## System Instructions", stdin)
        self.assertIn("System rules.", stdin)
        self.assertIn("## Task", stdin)
        self.assertIn("Do the thing.", stdin)

    def test_temp_output_file_is_cleaned_up(self) -> None:
        adapter, runner, _ = self._adapter()
        adapter.complete(self._request())
        cmd = runner.call_args.args[0]
        path = Path(cmd[cmd.index("--output-last-message") + 1])
        self.assertFalse(path.exists())

    def test_nonzero_exit_raises(self) -> None:
        runner = MagicMock(return_value=_completed(returncode=2, stderr="auth required"))
        adapter = CodexCliAdapter(
            executable="/usr/local/bin/codex",
            timeout_seconds=5,
            runner=runner,
            which=lambda _: "/usr/local/bin/codex",
        )
        with self.assertRaises(RuntimeError) as ctx:
            adapter.complete(self._request())
        self.assertIn("exited 2", str(ctx.exception))

    def test_timeout_raises_timeout_error(self) -> None:
        runner = MagicMock(side_effect=subprocess.TimeoutExpired(cmd="codex", timeout=5))
        adapter = CodexCliAdapter(
            executable="/usr/local/bin/codex",
            timeout_seconds=5,
            runner=runner,
            which=lambda _: "/usr/local/bin/codex",
        )
        with self.assertRaises(TimeoutError):
            adapter.complete(self._request())

    def test_missing_executable_raises(self) -> None:
        runner = MagicMock()
        adapter = CodexCliAdapter(
            executable="codex",
            runner=runner,
            which=lambda _: None,
        )
        with self.assertRaises(RuntimeError) as ctx:
            adapter.complete(self._request())
        self.assertIn("not found", str(ctx.exception))

    def test_falls_back_to_stdout_when_output_file_empty(self) -> None:
        runner = MagicMock()

        def fake_runner(cmd: list[str], **rkwargs: Any) -> subprocess.CompletedProcess:
            idx = cmd.index("--output-last-message")
            Path(cmd[idx + 1]).write_text("", encoding="utf-8")
            return _completed(stdout=_agent_result_json(), returncode=0)

        runner.side_effect = fake_runner
        adapter = CodexCliAdapter(
            executable="/usr/local/bin/codex",
            timeout_seconds=5,
            runner=runner,
            which=lambda _: "/usr/local/bin/codex",
        )
        response = adapter.complete(self._request())
        parsed = json.loads(response.content)
        self.assertEqual(parsed["status"], "completed")


class ProviderRoutingTests(unittest.TestCase):
    def test_provider_from_model_recognises_cli_prefixes(self) -> None:
        self.assertEqual(_provider_from_model("claude_cli:sonnet"), "claude_cli")
        self.assertEqual(_provider_from_model("claude_cli"), "claude_cli")
        self.assertEqual(_provider_from_model("claude-cli:opus"), "claude_cli")
        self.assertEqual(_provider_from_model("codex_cli:gpt-5"), "codex_cli")
        self.assertEqual(_provider_from_model("codex_cli"), "codex_cli")
        self.assertEqual(_provider_from_model("local-stub"), "stub")

    def test_default_router_registers_all_three_adapters(self) -> None:
        router = ModelRouter()
        self.assertEqual(sorted(router.adapters), ["claude_cli", "codex_cli", "stub"])


if __name__ == "__main__":
    unittest.main()
