from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ShellResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int
    blocked: bool = False


class ShellTools:
    def __init__(
        self,
        root: Path,
        timeout_seconds: int = 60,
        max_output_chars: int = 12000,
    ):
        self.root = root.resolve()
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars

    def run(self, command: str | list[str], cwd: str | None = None) -> ShellResult:
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        block_reason = self._blocked_reason(argv)
        if block_reason:
            return ShellResult(
                ok=False,
                stdout="",
                stderr=block_reason,
                returncode=126,
                blocked=True,
            )
        workdir = self._resolve_workdir(cwd)
        try:
            completed = subprocess.run(
                argv,
                cwd=workdir,
                text=True,
                capture_output=True,
                timeout=self.timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            return ShellResult(
                ok=False,
                stdout=_truncate(exc.stdout or "", self.max_output_chars),
                stderr=_truncate(exc.stderr or "Command timed out.", self.max_output_chars),
                returncode=124,
            )
        return ShellResult(
            ok=completed.returncode == 0,
            stdout=_truncate(completed.stdout, self.max_output_chars),
            stderr=_truncate(completed.stderr, self.max_output_chars),
            returncode=completed.returncode,
        )

    def _resolve_workdir(self, cwd: str | None) -> Path:
        path = (self.root / cwd).resolve() if cwd else self.root
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"cwd escapes root: {cwd}") from exc
        return path

    def _blocked_reason(self, argv: list[str]) -> str | None:
        if not argv:
            return "Empty command is not allowed."
        command = " ".join(argv)
        executable = argv[0]
        if executable == "sudo":
            return "Blocked forbidden command: sudo"
        if executable == "rm" and "-rf" in argv and ("/" in argv or "~" in argv):
            return "Blocked dangerous rm -rf target."
        if executable == "cat" and any(arg.startswith("~/.ssh") for arg in argv[1:]):
            return "Blocked forbidden sensitive file read."
        if "curl" == executable and any(flag in argv for flag in ["-T", "--upload-file"]):
            return "Blocked curl upload operation."
        if "rm -rf /" in command or "rm -rf ~" in command:
            return "Blocked dangerous rm -rf command."
        return None


def _truncate(value: str, max_chars: int) -> str:
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n...[truncated]"

