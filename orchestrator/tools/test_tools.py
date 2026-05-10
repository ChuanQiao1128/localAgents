from __future__ import annotations

from pathlib import Path

from .shell_tools import ShellResult, ShellTools


class TestTools:
    def __init__(self, root: Path):
        self.shell = ShellTools(root, timeout_seconds=180)

    def run_commands(self, commands: list[str]) -> list[ShellResult]:
        return [self.shell.run(command) for command in commands]

