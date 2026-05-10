from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.sandbox.worktree_manager import WorktreeManager


class WorktreeManagerTests(unittest.TestCase):
    def test_create_worktree_for_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            (root / "README.md").write_text("hello\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test User",
                    "-c",
                    "user.email=test@example.com",
                    "commit",
                    "-m",
                    "initial",
                ],
                cwd=root,
                check=True,
                capture_output=True,
            )

            result = WorktreeManager(root).create("WEB-001")

            self.assertTrue(result.ok, result.stderr)
            self.assertTrue((result.path / "README.md").exists())


if __name__ == "__main__":
    unittest.main()

