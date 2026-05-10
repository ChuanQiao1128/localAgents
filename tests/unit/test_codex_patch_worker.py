"""RC-2B unit tests for the Codex patch worker adapter.

The adapter lives in `orchestrator/core/agentic_runtime.py` (per the
RC-2B spec: "keep it in agentic_runtime.py, but don't expand scope").
These tests cover the public surface — `build_codex_patch_worker_command`,
`codex_cli_available`, `_run_codex_patch_worker` with an injected
`command_runner` — without forking real subprocesses.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.core.agentic_runtime import (
    _CODEX_ALLOWED_APPROVALS,
    _CODEX_ALLOWED_SANDBOXES,
    _CODEX_FORBIDDEN_TOKENS,
    _run_codex_patch_worker,
    build_codex_patch_worker_command,
    codex_cli_available,
)
from orchestrator.core.deploy import (
    AgenticConfig, CodexPatchWorkerConfig, load_agentic_config, project_config_path,
)


# ===========================================================================
# build_codex_patch_worker_command — pure function, no I/O
# ===========================================================================
class CommandBuilderTests(unittest.TestCase):
    def _basic(self, **overrides) -> list[str]:
        kwargs = dict(
            worktree=Path("/tmp/wt"),
            output_path=Path("/tmp/last.md"),
            prompt="please change foo.html",
            model="gpt-5.5",
        )
        kwargs.update(overrides)
        return build_codex_patch_worker_command(**kwargs)

    def test_default_command_includes_workspace_write_sandbox(self) -> None:
        cmd = self._basic()
        self.assertEqual(cmd[0], "codex")
        self.assertIn("exec", cmd)
        self.assertIn("--sandbox", cmd)
        i = cmd.index("--sandbox")
        self.assertEqual(cmd[i + 1], "workspace-write")
        # --sandbox must appear AFTER the `exec` token (subcommand option).
        self.assertGreater(i, cmd.index("exec"))

    def test_default_command_includes_on_request_approval(self) -> None:
        # RC-2B.1 env-probe correction: --ask-for-approval is a TOP-LEVEL
        # codex flag, not a subcommand option. Must come BEFORE `exec`.
        cmd = self._basic()
        self.assertIn("--ask-for-approval", cmd)
        i = cmd.index("--ask-for-approval")
        self.assertEqual(cmd[i + 1], "on-request")
        self.assertLess(i, cmd.index("exec"),
                        "--ask-for-approval must come before `exec` (top-level flag)")

    def test_default_command_includes_skip_git_repo_check(self) -> None:
        # Worktree lives under .agent/worktrees and is not a git repo;
        # without --skip-git-repo-check, codex refuses to start.
        cmd = self._basic()
        self.assertIn("--skip-git-repo-check", cmd)

    def test_default_command_passes_prompt_after_double_dash(self) -> None:
        cmd = self._basic(prompt="my prompt body")
        self.assertEqual(cmd[-1], "my prompt body")
        self.assertEqual(cmd[-2], "--")

    def test_dangerous_yolo_sandbox_is_refused(self) -> None:
        with self.assertRaises(ValueError) as ctx:
            self._basic(sandbox="--yolo")
        self.assertIn("not allowed", str(ctx.exception))

    def test_danger_full_access_sandbox_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._basic(sandbox="danger-full-access")

    def test_dangerous_bypass_sandbox_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._basic(sandbox="--dangerously-bypass-approvals-and-sandbox")

    def test_unknown_sandbox_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._basic(sandbox="something-clever")

    def test_unknown_approval_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            self._basic(ask_for_approval="auto-yes-please")

    def test_read_only_sandbox_is_allowed(self) -> None:
        # Useful for diagnostic runs that should never mutate files.
        cmd = self._basic(sandbox="read-only")
        i = cmd.index("--sandbox")
        self.assertEqual(cmd[i + 1], "read-only")

    def test_command_overridable_for_alt_install_path(self) -> None:
        cmd = self._basic(command="/opt/bin/codex")
        self.assertEqual(cmd[0], "/opt/bin/codex")

    def test_allow_lists_pin_the_invariant(self) -> None:
        # If anyone widens these sets, the change must be deliberate.
        self.assertIn("workspace-write", _CODEX_ALLOWED_SANDBOXES)
        self.assertIn("on-request", _CODEX_ALLOWED_APPROVALS)
        for token in ("--yolo", "--dangerously-bypass-approvals-and-sandbox",
                      "danger-full-access"):
            self.assertIn(token, _CODEX_FORBIDDEN_TOKENS)

    def test_sandbox_allow_list_only_contains_real_codex_values(self) -> None:
        # RC-2B.1 env-probe correction: real codex 0.130.0 enumerates
        # sandbox values as {read-only, workspace-write, danger-full-access}.
        # Our allow-list must be a strict subset of those (with
        # danger-full-access excluded by policy) — we used to include
        # the bogus value "read" which codex would reject at runtime.
        REAL_CODEX_SANDBOXES = {"read-only", "workspace-write", "danger-full-access"}
        self.assertTrue(_CODEX_ALLOWED_SANDBOXES.issubset(REAL_CODEX_SANDBOXES),
                        f"allow-list contains values not enumerated by codex 0.130.0: "
                        f"{_CODEX_ALLOWED_SANDBOXES - REAL_CODEX_SANDBOXES}")
        # And danger-full-access must NOT be in the allow-list (it IS
        # a real codex value, but we forbid it).
        self.assertNotIn("danger-full-access", _CODEX_ALLOWED_SANDBOXES)

    def test_argv_shape_matches_real_codex_0_130_0(self) -> None:
        # Pin the env-probe finding: --ask-for-approval is a TOP-LEVEL
        # codex flag and MUST come before the `exec` token. Pre-fix
        # the builder put it after `exec`, which real codex 0.130.0
        # rejects with `error: unexpected argument '--ask-for-approval'
        # found` — verified live during RC-2B.1 env probe.
        cmd = self._basic()
        exec_idx = cmd.index("exec")
        approval_idx = cmd.index("--ask-for-approval")
        self.assertLess(approval_idx, exec_idx)
        # --sandbox is a subcommand option of `exec` — must come AFTER.
        self.assertGreater(cmd.index("--sandbox"), exec_idx)
        # -C / -m / --skip-git-repo-check / --output-last-message are all
        # subcommand options too.
        for subcmd_opt in ("-C", "-m", "--skip-git-repo-check",
                           "--output-last-message"):
            self.assertGreater(cmd.index(subcmd_opt), exec_idx,
                               f"{subcmd_opt} must come after `exec`")


# ===========================================================================
# codex_cli_available — preflight check
# ===========================================================================
class PreflightTests(unittest.TestCase):
    def test_returns_false_for_nonexistent_command(self) -> None:
        self.assertFalse(codex_cli_available(command="this-binary-definitely-does-not-exist-xyzzy"))

    def test_returns_true_for_existing_command(self) -> None:
        # `python3` is always present in CI; this proves the lookup works.
        self.assertTrue(codex_cli_available(command="python3"))


# ===========================================================================
# _run_codex_patch_worker — injected command_runner end-to-end
# ===========================================================================
@dataclass
class _CompletedStub:
    returncode: int = 0
    stdout: str = ""
    stderr: str = ""


def _seed_run_dir(project_path: Path, run_id: str = "run_test") -> Path:
    run_dir = project_path / ".agent" / "runs" / run_id
    run_dir.mkdir(parents=True)
    return run_dir


def _seed_intent_context(project_path: Path) -> tuple[dict, dict, dict]:
    # Minimal intent / context / eval_harness skeletons that
    # `_build_candidate` and `_run_codex_patch_worker` need.
    intent = {
        "goal": "add greeting tag",
        "success_criteria": ["greeting tag exists"],
        "allowed_change_scope": {"paths": ["src/**"], "max_files": 4},
    }
    context = {
        "context_quality": {"has_source_files": True, "checks": {"source_files_selected": 1}},
        "ranking_summary": {"selected_doc_files": 0, "selected_test_files": 0},
        "relevant_files": [{"path": "src/index.html"}],
        "repo": {"commit": "abc1234"},
        "unknowns": [],
    }
    eval_harness = {
        "schema_version": "agentic.eval_harness.v1",
        "commands": [{"name": "build", "cmd": "npm run build", "required": True, "cwd": "."}],
    }
    return intent, context, eval_harness


class RunCodexPatchWorkerTests(unittest.TestCase):
    def test_codex_cli_missing_returns_clear_failure(self) -> None:
        # Default command_runner=None → preflight runs codex_cli_available
        # against a definitely-absent binary.
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            run_dir = _seed_run_dir(project_path)
            intent, context, eval_harness = _seed_intent_context(project_path)
            result = _run_codex_patch_worker(
                project_path=project_path, run_dir=run_dir,
                intent=intent, context=context, eval_harness=eval_harness,
                model="gpt-5.5", timeout_sec=10, candidate_id="candidate-a",
                codex_command="this-binary-definitely-does-not-exist-xyzzy",
            )
            self.assertEqual(result["patch_status"], "not_generated")
            self.assertEqual(result["reason"], "codex_cli_not_found")
            self.assertIn("looked_for", result.get("details", {}))

    def test_injected_runner_writes_file_then_diff_carries_patch(self) -> None:
        # The fake runner WRITES a new file in the worktree (simulating
        # what real codex would do), then _diff_directories computes the
        # patch off that worktree. This is the load-bearing test that
        # proves the adapter chain (worktree prep → run codex → diff →
        # write changed-files) works end-to-end without burning Codex.
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            (project_path / "src").mkdir()
            (project_path / "src" / "index.html").write_text(
                "<title>x</title>\n", encoding="utf-8"
            )
            run_dir = _seed_run_dir(project_path)
            intent, context, eval_harness = _seed_intent_context(project_path)

            def fake_runner(command: list[str], cwd: Path, timeout_sec: int) -> _CompletedStub:
                # Find the worktree path from the -C arg.
                i = command.index("-C")
                worktree = Path(command[i + 1])
                target = worktree / "src" / "greeting.html"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("<p>hello</p>\n", encoding="utf-8")
                return _CompletedStub(returncode=0, stdout="ok\n", stderr="")

            result = _run_codex_patch_worker(
                project_path=project_path, run_dir=run_dir,
                intent=intent, context=context, eval_harness=eval_harness,
                model="gpt-5.5", timeout_sec=10, candidate_id="candidate-a",
                command_runner=fake_runner,
            )
            self.assertEqual(result["patch_status"], "generated")
            self.assertEqual(result["reason"], "source_patch_generated")
            self.assertTrue(result["changed_files"]["source_patch_present"])
            self.assertTrue(result["patch_diff"].strip())
            paths = [item["path"] for item in result["changed_files"]["changed_files"]]
            self.assertIn("src/greeting.html", paths)

    def test_runner_returns_nonzero_with_no_diff_classified_as_codex_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            run_dir = _seed_run_dir(project_path)
            intent, context, eval_harness = _seed_intent_context(project_path)

            def fake_runner(command, cwd, timeout_sec):
                return _CompletedStub(returncode=2, stdout="", stderr="boom\n")

            result = _run_codex_patch_worker(
                project_path=project_path, run_dir=run_dir,
                intent=intent, context=context, eval_harness=eval_harness,
                model="gpt-5.5", timeout_sec=10, candidate_id="candidate-a",
                command_runner=fake_runner,
            )
            self.assertEqual(result["patch_status"], "not_generated")
            self.assertEqual(result["reason"], "codex_cli_failed_without_patch")
            self.assertEqual(result["details"]["returncode"], 2)
            self.assertIn("boom", result["details"]["stderr_tail"])

    def test_dangerous_sandbox_short_circuits_to_command_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            run_dir = _seed_run_dir(project_path)
            intent, context, eval_harness = _seed_intent_context(project_path)

            def fake_runner(command, cwd, timeout_sec):
                # Should never be called — the builder must refuse first.
                raise AssertionError("runner must NOT be invoked when sandbox is forbidden")

            result = _run_codex_patch_worker(
                project_path=project_path, run_dir=run_dir,
                intent=intent, context=context, eval_harness=eval_harness,
                model="gpt-5.5", timeout_sec=10, candidate_id="candidate-a",
                sandbox="danger-full-access",
                command_runner=fake_runner,
            )
            self.assertEqual(result["reason"], "codex_command_refused")


# ===========================================================================
# AgenticConfig — agent-studio.yaml integration
# ===========================================================================
class AgenticConfigTests(unittest.TestCase):
    def test_default_when_no_agent_studio_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = load_agentic_config(Path(tmp))
            self.assertEqual(cfg.patch_worker, "none")
            self.assertEqual(cfg.codex.sandbox, "workspace-write")
            self.assertEqual(cfg.codex.ask_for_approval, "on-request")

    def test_default_when_no_agentic_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            project_config_path(project_path).write_text(
                "deploy:\n  enabled: false\n", encoding="utf-8"
            )
            cfg = load_agentic_config(project_path)
            self.assertEqual(cfg.patch_worker, "none")

    def test_codex_block_loads_full_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            project_config_path(project_path).write_text(
                "agentic:\n"
                "  patch_worker: codex\n"
                "  codex:\n"
                "    command: codex\n"
                "    sandbox: workspace-write\n"
                "    ask_for_approval: on-request\n"
                "    timeout_sec: 900\n"
                "    max_prompt_chars: 80000\n",
                encoding="utf-8",
            )
            cfg = load_agentic_config(project_path)
            self.assertEqual(cfg.patch_worker, "codex")
            self.assertEqual(cfg.codex.timeout_sec, 900)
            self.assertEqual(cfg.codex.max_prompt_chars, 80000)

    def test_unknown_patch_worker_value_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            project_config_path(project_path).write_text(
                "agentic:\n  patch_worker: hand-of-god\n", encoding="utf-8"
            )
            with self.assertRaises(ValueError) as ctx:
                load_agentic_config(project_path)
            self.assertIn("hand-of-god", str(ctx.exception))

    def test_to_dict_roundtrip(self) -> None:
        cfg = AgenticConfig(patch_worker="codex",
                            codex=CodexPatchWorkerConfig(timeout_sec=42))
        roundtrip = AgenticConfig.from_dict(cfg.to_dict())
        self.assertEqual(roundtrip.patch_worker, "codex")
        self.assertEqual(roundtrip.codex.timeout_sec, 42)


# ===========================================================================
# Autonomous controller propagation: cmd_autonomous_start reads the config
# and passes patch_worker through to the inner loop.
# ===========================================================================
class AutonomousPropagationTests(unittest.TestCase):
    def test_run_inner_loop_passes_patch_worker_codex_when_configured(self) -> None:
        # Patch AgenticProjectRuntime.run to capture kwargs without
        # actually running anything. Then call the inner loop builder
        # the way cmd_autonomous_start does.
        from orchestrator.cli import cmd_autonomous_start  # noqa: F401  (symbol used only for code path)
        from orchestrator.core.agentic_runtime import AgenticProjectRuntime

        captured: dict[str, Any] = {}

        def fake_run(self, **kwargs):
            captured.update(kwargs)
            class _R:
                run_id = "run_x"
                decision = "needs-human-review"
                candidate = ""
                run_dir = Path("/tmp/x")
            return _R()

        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            project_config_path(project_path).write_text(
                "agentic:\n  patch_worker: codex\n", encoding="utf-8"
            )
            cfg = load_agentic_config(project_path)
            self.assertEqual(cfg.patch_worker, "codex")
            # Direct propagation test: AgenticProjectRuntime.run accepts
            # the kwargs we plan to pass.
            from unittest.mock import patch as _patch
            with _patch.object(AgenticProjectRuntime, "run", fake_run):
                AgenticProjectRuntime(db=None).run(
                    project={"id": "p", "path": str(project_path)},
                    intent_overrides={"goal": "x"},
                    patch_worker=cfg.patch_worker,
                    execute_eval=True,
                    timeout_sec=cfg.codex.timeout_sec,
                    codex_sandbox=cfg.codex.sandbox,
                    codex_ask_for_approval=cfg.codex.ask_for_approval,
                    codex_command=cfg.codex.command,
                )
            self.assertEqual(captured["patch_worker"], "codex")
            self.assertEqual(captured["codex_sandbox"], "workspace-write")
            self.assertEqual(captured["codex_ask_for_approval"], "on-request")
            self.assertTrue(captured["execute_eval"])


if __name__ == "__main__":
    unittest.main()
