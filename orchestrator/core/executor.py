"""Run install / build / test against a project directory.

Used by the implementation phase's build/test/fix loop. Hard rules:

  1. **No LLM-supplied shell.** The agent NEVER writes raw shell commands;
     this module decides what to run from the detected project type. The
     agent only describes the project structure or test failures.
  2. **Allowlist only.** Every command we execute is in
     ``_ALLOWED_COMMAND_PREFIXES``. The runner refuses anything else even
     if a future caller passes one.
  3. **Bounded.** Each command has its own timeout; total commands per
     loop are capped by the run budget.
  4. **No network surprises.** We invoke ``npm install`` / ``pip install``
     etc. — those reach the network, but no other network commands are
     allowed.

Intentionally minimal v1: no Docker, no service-up, no cross-process
fixtures. The point is to prove "code installs, builds, tests pass" or to
produce a useful failure log otherwise.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path


# Each entry is (executable, allowed_first_arg_prefixes).
# A command must be (allowed_executable, *allowed-args) — we check the
# argv list against this manifest before exec.
_ALLOWED_COMMAND_PREFIXES: list[tuple[str, tuple[str, ...]]] = [
    ("npm", ("install", "ci", "test", "run")),
    ("yarn", ("install", "test", "build", "run")),
    ("pnpm", ("install", "test", "build", "run")),
    ("python", ("-m",)),
    ("python3", ("-m",)),
    ("pytest", ()),
    ("go", ("test", "build", "vet", "mod")),
    ("cargo", ("test", "build", "check")),
    ("mvn", ("test", "compile", "verify", "package")),
    ("gradle", ("test", "build", "check")),
    ("npx", ("--no-install",)),  # very restricted; explicit subset
]


@dataclass(frozen=True)
class CommandResult:
    name: str
    argv: list[str]
    exit_code: int
    duration_ms: int
    stdout_tail: str = ""
    stderr_tail: str = ""

    @property
    def passed(self) -> bool:
        return self.exit_code == 0


@dataclass(frozen=True)
class ExecutionEvidence:
    project_type: str
    install: CommandResult | None = None
    build: CommandResult | None = None
    test: CommandResult | None = None
    extra: list[CommandResult] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def install_status(self) -> str:
        return _status(self.install)

    @property
    def build_status(self) -> str:
        return _status(self.build)

    @property
    def test_status(self) -> str:
        return _status(self.test)

    @property
    def overall_passed(self) -> bool:
        for cmd in (self.install, self.build, self.test):
            if cmd is not None and not cmd.passed:
                return False
        return self.test is not None  # require at least a test result

    def all_failed(self) -> list[CommandResult]:
        out: list[CommandResult] = []
        for cmd in (self.install, self.build, self.test, *self.extra):
            if cmd is not None and not cmd.passed:
                out.append(cmd)
        return out


def _status(cmd: CommandResult | None) -> str:
    if cmd is None:
        return "missing"
    return "passed" if cmd.passed else "failed"


# ---------------------------------------------------------------------------
# Project type detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProjectType:
    name: str
    install: list[list[str]] | None
    build: list[list[str]] | None
    test: list[list[str]] | None


def detect_project_type(project_path: Path) -> ProjectType | None:
    """Return a ProjectType with install/build/test command lists, or None
    if no recognized manifest is found.

    Each command is a full argv. Multiple commands per phase get tried in
    order (e.g. yarn vs npm) but we run the first one whose executable is
    available.
    """
    if (project_path / "package.json").exists():
        # Pick the first available package manager.
        npm_install: list[list[str]] = [["npm", "install"]]
        if (project_path / "pnpm-lock.yaml").exists():
            npm_install = [["pnpm", "install"]]
        elif (project_path / "yarn.lock").exists():
            npm_install = [["yarn", "install"]]
        # Build only if scripts.build exists (we'll detect at run time).
        return ProjectType(
            name="node",
            install=npm_install,
            build=[["npm", "run", "build"]],
            test=[["npm", "test"]],
        )
    if (project_path / "pyproject.toml").exists() or (project_path / "requirements.txt").exists():
        install_cmd: list[list[str]] = []
        if (project_path / "requirements.txt").exists():
            install_cmd.append(["python3", "-m", "pip", "install", "-r", "requirements.txt"])
        if (project_path / "pyproject.toml").exists():
            install_cmd.append(["python3", "-m", "pip", "install", "-e", "."])
        return ProjectType(
            name="python",
            install=install_cmd or None,
            build=None,
            test=[["python3", "-m", "pytest", "-q"]],
        )
    if (project_path / "Cargo.toml").exists():
        return ProjectType(
            name="rust",
            install=None,
            build=[["cargo", "build"]],
            test=[["cargo", "test"]],
        )
    if (project_path / "go.mod").exists():
        return ProjectType(
            name="go",
            install=None,
            build=[["go", "build", "./..."]],
            test=[["go", "test", "./..."]],
        )
    return None


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------


class Executor:
    def __init__(
        self,
        *,
        per_command_timeout: int = 600,
        max_log_chars: int = 6000,
        runner=None,
    ):
        self.per_command_timeout = per_command_timeout
        self.max_log_chars = max_log_chars
        self._runner = runner or subprocess.run

    def run_phase_checks(
        self,
        project_path: Path,
        project_type: ProjectType,
    ) -> ExecutionEvidence:
        """Run install / build / test for the detected project type."""
        notes: list[str] = []
        # For Python projects, bootstrap a per-project venv. This avoids
        # PEP 668 / "externally-managed-environment" rejections from
        # Homebrew/system Python on macOS, and isolates installed packages
        # from the user's system Python. If the venv bootstrap fails we
        # deliberately continue with the original commands — the resulting
        # failure is still recorded transparently in stderr_tail rather
        # than silently masked.
        if project_type.name == "python":
            venv_python = self._ensure_python_venv(project_path, notes)
            if venv_python is not None:
                project_type = _rewrite_python_commands(project_type, venv_python)
        install_result = self._run_first_available(project_type.install, project_path, "install", notes)
        build_result = None
        if install_result is None or install_result.passed:
            build_result = self._run_first_available(project_type.build, project_path, "build", notes)
        test_result = None
        if (install_result is None or install_result.passed) and (
            build_result is None or build_result.passed
        ):
            test_result = self._run_first_available(project_type.test, project_path, "test", notes)
        return ExecutionEvidence(
            project_type=project_type.name,
            install=install_result,
            build=build_result,
            test=test_result,
            notes=notes,
        )

    def _ensure_python_venv(self, project_path: Path, notes: list[str]) -> Path | None:
        """Ensure a ``.venv`` exists inside ``project_path``; return its python.

        Idempotent: if a venv already exists from a prior run we just return
        its python path without re-creating it. Returns ``None`` if the
        bootstrap fails — the caller will fall back to whatever was in the
        ProjectType (usually system ``python3``), and that failure will be
        recorded in install's stderr_tail rather than masked.
        """
        venv_python = _venv_python_path(project_path)
        if venv_python.exists():
            return venv_python

        bootstrap_python = shutil.which("python3") or shutil.which("python")
        if bootstrap_python is None:
            notes.append("python venv: no python3/python on PATH; cannot bootstrap venv")
            return None

        bootstrap_argv = [bootstrap_python, "-m", "venv", str(project_path / ".venv")]
        try:
            proc = self._runner(
                bootstrap_argv,
                cwd=str(project_path),
                capture_output=True,
                text=True,
                timeout=120,
                check=False,
            )
        except subprocess.TimeoutExpired:
            notes.append("python venv: bootstrap timed out after 120s")
            return None
        except FileNotFoundError as exc:
            notes.append(f"python venv: bootstrap python missing ({exc})")
            return None

        if getattr(proc, "returncode", 1) != 0:
            tail = _tail((proc.stderr or "") if proc else "", 400)
            notes.append(f"python venv: bootstrap failed exit={proc.returncode}: {tail}")
            return None

        if not venv_python.exists():
            notes.append(f"python venv: bootstrap returned 0 but {venv_python} is missing")
            return None
        return venv_python

    def _run_first_available(
        self,
        commands: list[list[str]] | None,
        project_path: Path,
        kind: str,
        notes: list[str],
    ) -> CommandResult | None:
        if not commands:
            return None
        for argv in commands:
            if not _is_allowed(argv):
                notes.append(f"{kind}: refused disallowed command {argv}")
                continue
            executable_path = shutil.which(argv[0])
            if executable_path is None:
                notes.append(f"{kind}: {argv[0]} not found on PATH; skipping {' '.join(argv)}")
                continue
            return self._exec(argv, project_path, kind)
        # All candidates skipped — record an explicit "missing" outcome
        # rather than silently dropping.
        notes.append(f"{kind}: no executable available for any of {commands}")
        return None

    def _exec(self, argv: list[str], project_path: Path, kind: str) -> CommandResult:
        started = time.perf_counter()
        try:
            proc = self._runner(
                argv,
                cwd=str(project_path),
                capture_output=True,
                text=True,
                timeout=self.per_command_timeout,
                check=False,
            )
            elapsed = int((time.perf_counter() - started) * 1000)
            return CommandResult(
                name=kind,
                argv=list(argv),
                exit_code=proc.returncode,
                duration_ms=elapsed,
                stdout_tail=_tail(proc.stdout or "", self.max_log_chars),
                stderr_tail=_tail(proc.stderr or "", self.max_log_chars),
            )
        except subprocess.TimeoutExpired as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            return CommandResult(
                name=kind,
                argv=list(argv),
                exit_code=-1,
                duration_ms=elapsed,
                stdout_tail=(_tail(exc.stdout.decode("utf-8", "replace"), self.max_log_chars)
                             if isinstance(getattr(exc, "stdout", None), bytes) else ""),
                stderr_tail=f"timeout after {self.per_command_timeout}s",
            )
        except FileNotFoundError as exc:
            return CommandResult(
                name=kind,
                argv=list(argv),
                exit_code=-127,
                duration_ms=0,
                stdout_tail="",
                stderr_tail=f"executable not found: {exc}",
            )


def _is_allowed(argv: list[str]) -> bool:
    if not argv:
        return False
    name = os.path.basename(argv[0])
    # On Windows the venv python ends in `.exe`. Strip a single trailing
    # `.exe` so the allowlist key match still succeeds for `python.exe`.
    if name.endswith(".exe"):
        name = name[:-4]
    for allowed_name, allowed_args in _ALLOWED_COMMAND_PREFIXES:
        if name == allowed_name:
            if not allowed_args:
                return True
            if len(argv) < 2:
                return False
            return any(argv[1].startswith(prefix) or argv[1] == prefix for prefix in allowed_args)
    return False


def _tail(text: str, max_chars: int) -> str:
    if not text or len(text) <= max_chars:
        return text
    return "...(truncated)\n" + text[-max_chars:]


# ---------------------------------------------------------------------------
# Per-project Python venv bootstrap
# ---------------------------------------------------------------------------
#
# Why we do this: macOS Homebrew Python (and an increasing number of Linux
# distros) ship a "PEP 668 externally-managed-environment" marker that makes
# `python3 -m pip install -e .` fail outright with exit 1 before pip even
# reads pyproject.toml. The first real-spec D0 run on the user's Mac hit
# this immediately on every fix-loop attempt, with no way for the LLM to
# repair it (the failure is in the host environment, not the generated
# code). Bootstrapping a per-project `.venv/` and routing all subsequent
# install/build/test commands through `.venv/bin/python` sidesteps the
# whole problem and isolates the project's deps from the user's system —
# which is also what every modern Python project does anyway.


def _venv_python_path(project_path: Path) -> Path:
    """Return the expected path to the venv's python interpreter.

    POSIX: ``<project>/.venv/bin/python``
    Windows: ``<project>\\.venv\\Scripts\\python.exe``
    """
    if sys.platform == "win32":
        return project_path / ".venv" / "Scripts" / "python.exe"
    return project_path / ".venv" / "bin" / "python"


def _rewrite_python_commands(project_type: ProjectType, venv_python: Path) -> ProjectType:
    """Return a new ProjectType with python/python3 entries swapped for venv python.

    Non-python entries are left untouched. The argv lists are deep-copied so
    the original ProjectType is unchanged.
    """
    venv_path = str(venv_python)

    def rewrite(commands: list[list[str]] | None) -> list[list[str]] | None:
        if not commands:
            return commands
        out: list[list[str]] = []
        for argv in commands:
            if argv and os.path.basename(argv[0]) in ("python", "python3"):
                out.append([venv_path, *argv[1:]])
            else:
                out.append(list(argv))
        return out

    return replace(
        project_type,
        install=rewrite(project_type.install),
        build=rewrite(project_type.build),
        test=rewrite(project_type.test),
    )
