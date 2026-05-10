"""RC-2A regression: when a task ends in a pause, the final report
must reflect Status: paused with the right pause_reason.

Surfaced by RC-2A dogfood: the needs-human-review pause path called
`_update_final_status` BEFORE `_pause`, so the final-run-status.md
recorded `Status: running` even though the session ended paused.
The pre-fix order across 4 paths (apply_failed, needs_human_review,
unhandled_decision, too-many-corrective-tasks) is now swapped so the
report sees the post-pause state.
"""
from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from orchestrator.core.autonomous import (
    AutonomousController,
    final_run_status_file,
    write_task_graph,
)


@dataclass
class _StubResult:
    run_id: str
    decision: str
    candidate: str = "candidate-a"
    run_dir: Path = Path("/tmp")


def _make_project(tmp: str) -> dict[str, Any]:
    project_path = Path(tmp) / "proj"
    project_path.mkdir()
    return {"id": "project_x", "name": "x", "path": str(project_path)}


def _seed_one_task(project_path: Path) -> dict[str, Any]:
    graph = {
        "schema_version": 1, "project_title": "p", "overview": "", "tasks": [
            {"id": "task-001", "title": "A", "intent": "x",
             "scope_paths": ["**"], "acceptance_criteria": [],
             "dependencies": [], "status": "pending", "risk": "low",
             "run_ids": [], "commit": None},
        ],
    }
    write_task_graph(project_path, graph)
    return graph


def _final_report(project_path: Path, session_id: str) -> str:
    return final_run_status_file(project_path, session_id).read_text(encoding="utf-8")


class PauseThenRenderTests(unittest.TestCase):
    def test_needs_human_review_report_shows_paused_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(tmp)
            graph = _seed_one_task(Path(project["path"]))
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: _StubResult(run_id="r1", decision="needs-human-review"),
            )
            session = controller.start_or_resume()
            controller.advance_one_task(session, graph)
            report = _final_report(Path(project["path"]), session.session_id)
            self.assertIn("Status: paused", report)
            self.assertIn("Pause reason: needs_human_review", report)

    def test_apply_failed_report_shows_paused_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(tmp)
            graph = _seed_one_task(Path(project["path"]))

            def _boom_apply(**kw):
                raise RuntimeError("apply failed")

            with patch("orchestrator.core.autonomous.commit_task", return_value="abc1234"):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: _StubResult(run_id="r1", decision="promote"),
                    apply_candidate=_boom_apply,
                )
                session = controller.start_or_resume()
                controller.advance_one_task(session, graph)
            report = _final_report(Path(project["path"]), session.session_id)
            self.assertIn("Status: paused", report)
            self.assertIn("apply_failed", report)

    def test_unhandled_decision_report_shows_paused_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(tmp)
            graph = _seed_one_task(Path(project["path"]))
            # An unknown decision falls through to the unhandled_decision path.
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: _StubResult(run_id="r1", decision="some-future-decision"),
            )
            session = controller.start_or_resume()
            controller.advance_one_task(session, graph)
            report = _final_report(Path(project["path"]), session.session_id)
            self.assertIn("Status: paused", report)
            self.assertIn("unhandled_decision", report)


if __name__ == "__main__":
    unittest.main()
