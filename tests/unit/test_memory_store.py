from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.memory_store import MemoryStore
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


class MemoryStoreTests(unittest.TestCase):
    def test_write_and_append_memory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a todo app", paths.projects_dir)
            project_path = Path(project["path"])
            store = MemoryStore(Database(paths.db_path))

            relative = store.write(
                project_id=project["id"],
                project_path=project_path,
                scope="project",
                key="project-decisions",
                content="# Project Decisions\n\nUse SQLite first.",
            )
            store.append(
                project_id=project["id"],
                project_path=project_path,
                scope="project",
                key="project-decisions",
                content="- Add workflow gates before real agents.",
            )

            self.assertEqual(relative, ".agent/memory/project-decisions.md")
            content = (project_path / relative).read_text(encoding="utf-8")
            self.assertIn("Use SQLite first.", content)
            self.assertIn("workflow gates", content)
            memories = store.list_for_project(project["id"])
            self.assertEqual(len(memories), 1)


if __name__ == "__main__":
    unittest.main()

