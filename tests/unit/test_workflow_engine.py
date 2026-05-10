from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


class WorkflowEngineTests(unittest.TestCase):
    def test_project_creation_creates_workspace_and_db_row(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)

            project = engine.create_project("Build a todo app", paths.projects_dir)

            self.assertTrue(Path(project["path"]).exists())
            self.assertTrue((Path(project["path"]) / ".agent/project.yaml").exists())
            stored = Database(paths.db_path).query_one("SELECT * FROM projects WHERE id = ?", (project["id"],))
            self.assertIsNotNone(stored)
            self.assertEqual(stored["status"], "created")

    def test_run_stops_at_prd_gate_then_approval_completes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build an expense tracker", paths.projects_dir)

            result = engine.run(project["id"], "software_project")

            self.assertEqual(result["status"], "needs_approval")
            self.assertEqual(result["phase_id"], "prd")
            status = engine.status(project["id"])
            phase_statuses = {phase["phase_id"]: phase["status"] for phase in status["phases"]}
            self.assertEqual(phase_statuses["intake"], "completed")
            self.assertEqual(phase_statuses["research"], "completed")
            self.assertEqual(phase_statuses["prd"], "needs_approval")
            self.assertEqual(phase_statuses["design"], "pending")
            self.assertEqual(len([a for a in status["approvals"] if a["status"] == "pending"]), 1)
            project_path = Path(project["path"])
            self.assertTrue((project_path / "docs/product/prd.md").exists())

            resumed = engine.approve(project["id"], "prd")

            self.assertEqual(resumed["status"], "completed")
            final_status = engine.status(project["id"])
            self.assertEqual(final_status["run"]["status"], "completed")
            self.assertTrue(all(phase["status"] == "completed" for phase in final_status["phases"]))
            self.assertTrue(all(task["status"] == "completed" for task in final_status["tasks"]))
            self.assertTrue((project_path / "docs/review/review-report.md").exists())
            self.assertTrue((project_path / ".agent/tasks/generated-tasks.json").exists())
            dependencies = Database(paths.db_path).query_all(
                """
                SELECT td.*
                FROM task_dependencies td
                JOIN tasks t ON t.id = td.task_id
                WHERE t.run_id = ?
                """,
                (result["run_id"],),
            )
            self.assertGreaterEqual(len(dependencies), 8)

    def test_reject_blocks_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a landing page", paths.projects_dir)
            engine.run(project["id"], "software_project")

            result = engine.reject(project["id"], "prd", "Scope needs edits.")

            self.assertEqual(result["status"], "blocked")
            status = engine.status(project["id"])
            self.assertEqual(status["run"]["status"], "blocked")
            prd = next(phase for phase in status["phases"] if phase["phase_id"] == "prd")
            self.assertEqual(prd["status"], "blocked")

    def test_retry_restarts_from_blocked_phase(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a landing page", paths.projects_dir)
            engine.run(project["id"], "software_project")
            engine.reject(project["id"], "prd", "Scope needs edits.")

            retry = engine.retry(project["id"], "prd")

            self.assertEqual(retry["status"], "needs_approval")
            status = engine.status(project["id"])
            prd = next(phase for phase in status["phases"] if phase["phase_id"] == "prd")
            design = next(phase for phase in status["phases"] if phase["phase_id"] == "design")
            self.assertEqual(prd["status"], "needs_approval")
            self.assertEqual(design["status"], "pending")

    def test_multiple_runs_have_unique_task_ids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a habit tracker", paths.projects_dir)

            first = engine.run(project["id"], "software_project")
            engine.approve(project["id"], "prd")
            second = engine.run(project["id"], "software_project")

            db = Database(paths.db_path)
            first_tasks = db.query_all("SELECT id FROM tasks WHERE run_id = ?", (first["run_id"],))
            second_tasks = db.query_all("SELECT id FROM tasks WHERE run_id = ?", (second["run_id"],))
            self.assertEqual(len(first_tasks), 9)
            self.assertEqual(len(second_tasks), 9)
            self.assertTrue({row["id"] for row in first_tasks}.isdisjoint({row["id"] for row in second_tasks}))


if __name__ == "__main__":
    unittest.main()
