from __future__ import annotations

import tempfile
import unittest

from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database
from orchestrator.sandbox.path_locker import LockConflictError, PathLocker, patterns_conflict


class PathLockerTests(unittest.TestCase):
    def test_pattern_conflict_detection(self) -> None:
        self.assertTrue(patterns_conflict("apps/web/**", "apps/web/app/**"))
        self.assertTrue(patterns_conflict("apps/**", "apps/api/**"))
        self.assertFalse(patterns_conflict("docs/product/**", "apps/web/**"))

    def test_acquire_conflict_and_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a todo app", paths.projects_dir)
            locker = PathLocker(Database(paths.db_path))

            first = locker.acquire(
                project_id=project["id"],
                run_id=None,
                task_id="WEB-001",
                owner="developer",
                path_patterns=["apps/web/**"],
            )

            self.assertEqual(len(first), 1)
            with self.assertRaises(LockConflictError):
                locker.acquire(
                    project_id=project["id"],
                    run_id=None,
                    task_id="WEB-002",
                    owner="developer",
                    path_patterns=["apps/web/app/**"],
                )

            locker.release_task(project["id"], "WEB-001")
            second = locker.acquire(
                project_id=project["id"],
                run_id=None,
                task_id="WEB-002",
                owner="developer",
                path_patterns=["apps/web/app/**"],
            )
            self.assertEqual(len(second), 1)


if __name__ == "__main__":
    unittest.main()

