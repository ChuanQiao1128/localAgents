"""Tests for WorkflowEngine's LLM-driven phase production.

The new path tries AgentRunner.run_task to generate file contents and falls
back to the deterministic stub when anything goes wrong. These tests use
mocks so they consume zero CLI quota.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from orchestrator.agents.base import AgentResult
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine


def _make_engine(tmp: str, *, runner: MagicMock | None = None):
    paths = resolve_paths(tmp)
    initialize_workspace(paths)
    engine = create_engine(paths)
    if runner is not None:
        engine._agent_runner = runner
    return engine, paths


class WorkflowEngineLlmTests(unittest.TestCase):
    def test_phase_writes_llm_supplied_file_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            runner.run_task.return_value = AgentResult(
                status="completed",
                summary="generated brief from mock",
                artifacts=[".agent/project-brief.md"],
                files={".agent/project-brief.md": "# Real LLM Brief\n\nMarkdown todo CLI.\n"},
            )
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            engine.run(project["id"], "software_project")

            brief_path = Path(project["path"]) / ".agent/project-brief.md"
            self.assertTrue(brief_path.exists())
            content = brief_path.read_text(encoding="utf-8")
            self.assertEqual(content, "# Real LLM Brief\n\nMarkdown todo CLI.\n")
            self.assertNotIn("local deterministic MVP stub", content)
            runner.run_task.assert_called()

    def test_phase_falls_back_to_stub_when_llm_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            runner.run_task.side_effect = RuntimeError("CLI not available in CI")
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            engine.run(project["id"], "software_project")

            brief_path = Path(project["path"]) / ".agent/project-brief.md"
            self.assertTrue(brief_path.exists())
            content = brief_path.read_text(encoding="utf-8")
            # Stub fallback content has the well-known marker.
            self.assertIn("local deterministic MVP stub", content)

    def test_phase_backfills_missing_files_with_stub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            # research phase declares ONE output (research.md). Pretend the
            # LLM only supplied a different path; the required file should
            # still appear, populated by the stub.
            runner.run_task.return_value = AgentResult(
                status="completed",
                summary="partial",
                files={"docs/product/something-else.md": "decoy"},
            )
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            engine.run(project["id"], "software_project")

            research_path = Path(project["path"]) / "docs/product/research.md"
            self.assertTrue(research_path.exists())
            self.assertIn("local deterministic MVP stub", research_path.read_text(encoding="utf-8"))

    def test_force_stub_env_var_skips_llm_entirely(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            runner.run_task.return_value = AgentResult(
                status="completed",
                summary="should not be used",
                files={".agent/project-brief.md": "LLM content that should be ignored"},
            )
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            old = os.environ.get("LOCALAGENTS_FORCE_STUB")
            os.environ["LOCALAGENTS_FORCE_STUB"] = "1"
            try:
                engine.run(project["id"], "software_project")
            finally:
                if old is None:
                    os.environ.pop("LOCALAGENTS_FORCE_STUB", None)
                else:
                    os.environ["LOCALAGENTS_FORCE_STUB"] = old

            brief_path = Path(project["path"]) / ".agent/project-brief.md"
            self.assertIn("local deterministic MVP stub", brief_path.read_text(encoding="utf-8"))
            runner.run_task.assert_not_called()

    def test_failed_status_with_no_files_falls_back_to_stub(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            runner.run_task.return_value = AgentResult(
                status="failed",
                summary="LLM gave up — nothing to show",
                files={},
            )
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            engine.run(project["id"], "software_project")

            brief_path = Path(project["path"]) / ".agent/project-brief.md"
            self.assertIn("local deterministic MVP stub", brief_path.read_text(encoding="utf-8"))

    def test_failed_status_with_files_keeps_llm_content(self) -> None:
        # If the agent reports `failed` but still produced file content (e.g.
        # QA writing a "blocked because no impl" report), we keep its output —
        # it's usually more informative than a stub.
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            runner.run_task.return_value = AgentResult(
                status="failed",
                summary="QA blocked: no implementation to test",
                files={".agent/project-brief.md": "# Blocked\n\nReason: no impl yet.\n"},
            )
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            engine.run(project["id"], "software_project")

            brief_path = Path(project["path"]) / ".agent/project-brief.md"
            content = brief_path.read_text(encoding="utf-8")
            self.assertIn("Blocked", content)
            self.assertNotIn("local deterministic MVP stub", content)


if __name__ == "__main__":
    unittest.main()
