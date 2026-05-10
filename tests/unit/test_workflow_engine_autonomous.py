"""Tests for autonomous-mode behavior: gates auto-approve, phase crashes
isolated, final-run-status report written.
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


def _autonomous_env():
    """Context manager that sets LOCALAGENTS_AUTONOMOUS=1 and restores it."""
    class _Ctx:
        def __enter__(self):
            self._old = os.environ.get("LOCALAGENTS_AUTONOMOUS")
            os.environ["LOCALAGENTS_AUTONOMOUS"] = "1"
            return self

        def __exit__(self, *args):
            if self._old is None:
                os.environ.pop("LOCALAGENTS_AUTONOMOUS", None)
            else:
                os.environ["LOCALAGENTS_AUTONOMOUS"] = self._old

    return _Ctx()


def _make_engine(tmp: str, runner: MagicMock):
    paths = resolve_paths(tmp)
    initialize_workspace(paths)
    engine = create_engine(paths)
    engine._agent_runner = runner
    return engine, paths


class AutonomousGateTests(unittest.TestCase):
    def test_autonomous_run_does_not_stop_at_prd_gate(self) -> None:
        runner = MagicMock()

        def respond(agent_config, context):
            outputs = list(context.output_paths or [])
            return AgentResult(
                status="completed",
                summary="ok",
                files={p: f"# {p}\nFrom {agent_config.get('id')}.\n" for p in outputs},
            )

        runner.run_task.side_effect = respond
        with _autonomous_env(), tempfile.TemporaryDirectory() as tmp:
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            result = engine.run(project["id"], "software_project")

            # In manual mode this would stop at needs_approval after PRD.
            # In autonomous mode the run drives all 9 phases through.
            self.assertEqual(result["status"], "completed")
            phases = engine.db.query_all(
                "SELECT phase_id, status FROM phases WHERE run_id = ? ORDER BY sequence",
                (result["run_id"],),
            )
            self.assertGreater(len(phases), 5)
            self.assertTrue(all(p["status"] == "completed" for p in phases))

    def test_manual_run_still_stops_at_prd_gate(self) -> None:
        runner = MagicMock()

        def respond(agent_config, context):
            outputs = list(context.output_paths or [])
            return AgentResult(
                status="completed",
                summary="ok",
                files={p: f"# {p}\nFrom {agent_config.get('id')}.\n" for p in outputs},
            )

        runner.run_task.side_effect = respond
        # Make sure autonomous is NOT set.
        old = os.environ.pop("LOCALAGENTS_AUTONOMOUS", None)
        try:
            with tempfile.TemporaryDirectory() as tmp:
                engine, _ = _make_engine(tmp, runner=runner)
                project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
                result = engine.run(project["id"], "software_project")
                self.assertEqual(result["status"], "needs_approval")
        finally:
            if old is not None:
                os.environ["LOCALAGENTS_AUTONOMOUS"] = old


class AutonomousPhaseCrashIsolationTests(unittest.TestCase):
    def test_phase_crash_does_not_stop_autonomous_run(self) -> None:
        # First phase (intake) succeeds, second phase (research) crashes
        # *outside* of LLM (e.g. a DB error inside _run_phase). Run should
        # still continue to subsequent phases.
        runner = MagicMock()
        call_count = {"n": 0}

        def respond(agent_config, context):
            call_count["n"] += 1
            outputs = list(context.output_paths or [])
            return AgentResult(
                status="completed",
                summary="ok",
                files={p: f"# {p}\n" for p in outputs},
            )

        runner.run_task.side_effect = respond

        with _autonomous_env(), tempfile.TemporaryDirectory() as tmp:
            engine, _ = _make_engine(tmp, runner=runner)
            # Patch _run_phase to throw on the research phase.
            original = engine._run_phase

            def maybe_crash(*, phase_row, **kwargs):
                if phase_row["phase_id"] == "research":
                    raise RuntimeError("synthetic non-LLM failure")
                return original(phase_row=phase_row, **kwargs)

            engine._run_phase = maybe_crash  # type: ignore[method-assign]

            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            result = engine.run(project["id"], "software_project")

            self.assertEqual(result["status"], "completed")
            phases = engine.db.query_all(
                "SELECT phase_id, status FROM phases WHERE run_id = ? ORDER BY sequence",
                (result["run_id"],),
            )
            phase_status = {p["phase_id"]: p["status"] for p in phases}
            self.assertEqual(phase_status.get("research"), "failed")
            # Downstream phases should still have completed despite research failing.
            self.assertEqual(phase_status.get("prd"), "completed")
            self.assertEqual(phase_status.get("merge"), "completed")


class AutonomousReportTests(unittest.TestCase):
    def test_autonomous_run_writes_final_status_report(self) -> None:
        runner = MagicMock()
        runner.run_task.return_value = AgentResult(
            status="completed",
            summary="ok",
            files={},  # force everything to stub fallback so we exercise the suspect-files list
        )
        with _autonomous_env(), tempfile.TemporaryDirectory() as tmp:
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            result = engine.run(project["id"], "software_project")

            report_path = Path(project["path"]) / ".agent" / "runs" / result["run_id"] / "final-run-status.md"
            self.assertTrue(report_path.exists(), f"report missing at {report_path}")
            text = report_path.read_text(encoding="utf-8")
            self.assertIn("Autonomous Run Report", text)
            # First-screen contract (B4)
            self.assertIn("Overall Grade:", text)
            self.assertIn("Ready to use:", text)
            self.assertIn("Top Risks", text)
            self.assertIn("Recommended Next Command", text)
            self.assertIn("Trust Summary", text)
            # Detail sections still rendered
            self.assertIn("Phase-by-phase", text)
            self.assertIn("Files needing spot-check", text)
            # B1: every required file got an explicit untrusted fallback.
            self.assertIn("(fallback)", text)
            self.assertIn("untrusted fallback", text)


if __name__ == "__main__":
    unittest.main()
