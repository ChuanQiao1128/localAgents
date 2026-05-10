from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class WorktreeResult:
    ok: bool
    path: Path
    stdout: str
    stderr: str


class WorktreeManager:
    def __init__(self, repo_root: Path):
        self.repo_root = repo_root.resolve()

    def create(self, task_id: str, base_ref: str = "HEAD") -> WorktreeResult:
        target = self.repo_root / ".agent-studio/worktrees" / _safe_task_id(task_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            ["git", "worktree", "add", str(target), base_ref],
            cwd=self.repo_root,
            text=True,
            capture_output=True,
            timeout=60,
            check=False,
        )
        return WorktreeResult(
            ok=completed.returncode == 0,
            path=target,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )


def _safe_task_id(task_id: str) -> str:
    return "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in task_id)

