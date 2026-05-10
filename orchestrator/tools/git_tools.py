from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GitResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


class GitTools:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def status(self) -> GitResult:
        return self._git("status", "--short")

    def diff(self, *paths: str) -> GitResult:
        return self._git("diff", "--", *paths)

    def _git(self, *args: str) -> GitResult:
        completed = subprocess.run(
            ["git", *args],
            cwd=self.root,
            text=True,
            capture_output=True,
            timeout=30,
            check=False,
        )
        return GitResult(
            ok=completed.returncode == 0,
            stdout=completed.stdout,
            stderr=completed.stderr,
            returncode=completed.returncode,
        )

