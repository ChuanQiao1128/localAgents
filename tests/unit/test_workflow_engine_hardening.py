"""Tests for the validation, retry, and diagnose hardening added on top of
WorkflowEngine's LLM-driven phase production.
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from orchestrator.agents.base import AgentResult
from orchestrator.bootstrap import initialize_workspace
from orchestrator.cli import cmd_diagnose
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.core.workflow_engine import _validate_artifact


def _make_engine(tmp: str, runner: MagicMock | None = None):
    paths = resolve_paths(tmp)
    initialize_workspace(paths)
    engine = create_engine(paths)
    if runner is not None:
        engine._agent_runner = runner
    return engine, paths


class ValidateArtifactTests(unittest.TestCase):
    def test_valid_json_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            path.write_text('{"tasks": []}', encoding="utf-8")
            self.assertIsNone(_validate_artifact(path, '{"tasks": []}'))

    def test_invalid_json_returns_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tasks.json"
            err = _validate_artifact(path, '{"tasks": [')
            self.assertIsNotNone(err)
            assert err is not None  # for type narrowing
            self.assertIn("Expecting", err)

    def test_valid_yaml_passes(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "api.yaml"
            self.assertIsNone(_validate_artifact(path, "openapi: 3.0.0\ninfo:\n  title: t\n"))

    def test_invalid_yaml_returns_error(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "api.yaml"
            err = _validate_artifact(path, "openapi: 3.0.0\ninfo:\n  title: [unclosed")
            self.assertIsNotNone(err)

    def test_markdown_always_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "doc.md"
            self.assertIsNone(_validate_artifact(path, "# anything goes here"))


class RetryTests(unittest.TestCase):
    def test_first_attempt_failure_then_success_uses_llm_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            successful = AgentResult(
                status="completed",
                summary="ok on retry",
                files={".agent/project-brief.md": "# Real Brief\nRetry succeeded.\n"},
            )
            # The retry-on-transient-failure behaviour we want to verify is
            # local to a single phase: first call raises, second call returns
            # success. Subsequent phases will see StopIteration but that's OK
            # for this test — we only assert the intake brief was filled by
            # the LLM result, proving the retry path executed.
            runner.run_task.side_effect = [
                RuntimeError("transient: HTTP 429"),
                successful,
            ]
            engine, _ = _make_engine(tmp, runner=runner)
            os.environ["LOCALAGENTS_LLM_ATTEMPTS"] = "2"
            try:
                import orchestrator.core.workflow_engine as we
                original_sleep = we.time.sleep
                we.time.sleep = lambda _s: None
                try:
                    project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
                    engine.run(project["id"], "software_project")
                finally:
                    we.time.sleep = original_sleep
            finally:
                os.environ.pop("LOCALAGENTS_LLM_ATTEMPTS", None)

            brief_path = Path(project["path"]) / ".agent/project-brief.md"
            self.assertEqual(brief_path.read_text(encoding="utf-8"), "# Real Brief\nRetry succeeded.\n")
            # Intake retried once, so at least 2 run_task calls happened in total
            # (later phases failed because the side_effect list was exhausted —
            # that's expected and exercises the fallback path too).
            self.assertGreaterEqual(runner.run_task.call_count, 2)

    def test_all_attempts_fail_records_fallback_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            runner.run_task.side_effect = RuntimeError("CLI permanently broken")
            engine, paths = _make_engine(tmp, runner=runner)
            os.environ["LOCALAGENTS_LLM_ATTEMPTS"] = "2"
            try:
                import orchestrator.core.workflow_engine as we
                original_sleep = we.time.sleep
                we.time.sleep = lambda _s: None
                try:
                    project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
                    engine.run(project["id"], "software_project")
                finally:
                    we.time.sleep = original_sleep
            finally:
                os.environ.pop("LOCALAGENTS_LLM_ATTEMPTS", None)

            # Both attempts should have run for the intake phase before falling back.
            # (Retries happen per-phase, so call count >= 2.)
            self.assertGreaterEqual(runner.run_task.call_count, 2)
            from orchestrator.db import Database
            db = Database(paths.db_path)
            rows = db.query_all(
                "SELECT phase_id, message FROM events WHERE type = 'phase.llm_fallback'",
                (),
            )
            self.assertGreater(len(rows), 0)
            self.assertIn("after 2 attempt(s)", rows[0]["message"])


class ValidationEventsTests(unittest.TestCase):
    def test_invalid_json_emits_validation_event(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            runner.run_task.return_value = AgentResult(
                status="completed",
                summary="bad json",
                files={".agent/tasks/generated-tasks.json": "{not valid json"},
            )
            engine, paths = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            # Insert a project + run row so events have valid foreign keys.
            from orchestrator.core.ids import short_id, now_iso
            run_id = short_id("run")
            engine.db.execute(
                "INSERT INTO runs (id, project_id, workflow_id, status, current_phase, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, project["id"], "software_project", "running", None, now_iso(), now_iso()),
            )
            # Drive _produce_phase_outputs directly with a .json output so we
            # actually exercise the JSON validator (the standard workflow's
            # early phases all emit markdown, never JSON).
            engine._produce_phase_outputs(
                project=project,
                project_path=Path(project["path"]),
                run_id=run_id,
                phase_id="architecture",
                phase_config={"owner": "architect"},
                outputs=[".agent/tasks/generated-tasks.json"],
            )

            from orchestrator.db import Database
            db = Database(paths.db_path)
            rows = db.query_all(
                "SELECT message FROM events WHERE type = 'phase.validation_failed'",
                (),
            )
            self.assertGreaterEqual(len(rows), 1)
            self.assertTrue(any("generated-tasks.json" in r["message"] for r in rows))


class DiagnoseTests(unittest.TestCase):
    def test_diagnose_prints_summary_for_clean_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()

            def respond(agent_config, context):
                outputs = list(context.output_paths or [])
                # Produce content well above min_length_chars and containing
                # the section markers the bundled contracts look for, so the
                # validators report a clean run.
                return AgentResult(
                    status="completed",
                    summary="ok",
                    files={p: _passing_content_for(p) for p in outputs},
                )

            runner.run_task.side_effect = respond
            engine, paths = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            engine.run(project["id"], "software_project")

            args = type("Args", (), {})()
            args.root = tmp
            args.run_id = None
            args.project = project["id"]
            buf = io.StringIO()
            old_stdout = sys.stdout
            sys.stdout = buf
            try:
                cmd_diagnose(args)
            finally:
                sys.stdout = old_stdout

            output = buf.getvalue()
            self.assertIn("Run id:", output)
            self.assertIn("LLM fallbacks: none", output)
            self.assertIn("Format validation failures: none", output)


def _passing_content_for(path: str) -> str:
    """Produce content satisfying the bundled artifact contracts."""
    import json as _json
    if path.endswith("acceptance-criteria.md"):
        body = "\n".join(f"- AC-{i:03d} criterion description text " * 3 for i in range(1, 9))
        return "# Acceptance Criteria\n\n" + body + "\nfiller " * 200
    if path.endswith("api.openapi.yaml"):
        return "openapi: 3.0.0\ninfo:\n  title: x\n  version: '1'\npaths: {}\n"
    if path.endswith("generated-tasks.json"):
        return _json.dumps([{"id": f"T{i}", "title": "t", "phase": "implementation"} for i in range(5)])
    section_map = {
        "project-brief.md": ["Problem", "User", "Success"],
        "research.md": ["Market", "Alternatives", "Differentiator"],
        "prd.md": ["Problem", "Users", "MVP", "Acceptance", "Non-Goals", "Risks"],
        "user-flow.md": ["Flow"],
        "design-system.md": ["Tokens"],
        "architecture.md": ["Overview", "Components", "Data"],
        "test-plan.md": ["Coverage"],
        "review-report.md": ["Status"],
    }
    sections: list[str] = []
    for key, secs in section_map.items():
        if path.endswith(key):
            sections = secs
            break
    body = "\n\n".join(f"## {s}\nDetailed content for {s} section." for s in sections)
    return f"# Document\n\n{body}\n\n" + ("filler text " * 250)


if __name__ == "__main__":
    unittest.main()
