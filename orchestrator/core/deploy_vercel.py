"""Vercel-specific deploy adapter (MVP-4E).

Public surface:
  - `build_vercel_deploy_command(config, project_root, *, sanitized=False)`
  - `build_vercel_build_command(config, project_root, *, sanitized=False)`
  - `build_vercel_inspect_command(deployment_url, timeout, config, *, sanitized=False)`
  - `extract_deployment_url(stdout)`
  - `run_vercel_deploy(config, project_root, *, command_runner=None)`

The adapter is fully testable: pass a `command_runner` callable
`(args: list[str], cwd: Path, env: dict, timeout: float | None) -> CommandResult`
to substitute subprocess execution. The default runner is
`_default_command_runner`, which calls `subprocess.run`.

Token handling: the runner ALWAYS receives the real token (so Vercel can
authenticate). The sanitized command list — used for artifacts, logs, and
dry-run output — replaces every occurrence of the token value with
`<redacted>`. Stdout/stderr written to artifacts and logs are also
sanitized via `redact_text`.
"""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from orchestrator.core.deploy import (
    DEPLOY_FAILURE_TYPES,
    DeployConfig,
    REDACTED,
    redact_text,
    sanitize_command_args,
)
from orchestrator.core.ids import now_iso


VERCEL_DEPLOY_TIMEOUT_SEC = 600  # 10 min default for the deploy + inspect path
_VERCEL_URL_RE = re.compile(r"https://[A-Za-z0-9._-]+\.vercel\.app")
_FALLBACK_URL_RE = re.compile(r"https://[^\s]+")


# ---------------------------------------------------------------------------
# Result structs
# ---------------------------------------------------------------------------
@dataclass
class CommandResult:
    """Output of one shell invocation. Stdout/stderr should already be
    truncated by the runner if they're huge (we cap at write time too)."""
    args: list[str]
    sanitized_args: list[str]
    name: str
    cwd: str
    started_at: str
    completed_at: str
    exit_code: int
    stdout: str
    stderr: str

    def stdout_tail(self, n: int = 3000) -> str:
        return self.stdout[-n:]

    def stderr_tail(self, n: int = 3000) -> str:
        return self.stderr[-n:]


@dataclass
class DeployResult:
    status: str  # ready | failed | unknown
    deployment_url: str | None
    commands_run: list[CommandResult] = field(default_factory=list)
    failure: dict[str, Any] | None = None
    started_at: str = field(default_factory=now_iso)
    completed_at: str = field(default_factory=now_iso)


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------
def _token_value(config: DeployConfig) -> str:
    return os.environ.get(config.vercel.token_env, "")


def _scope_args(config: DeployConfig) -> list[str]:
    args: list[str] = []
    if config.vercel.scope:
        args.extend(["--scope", str(config.vercel.scope)])
    if config.vercel.project:
        args.extend(["--project", str(config.vercel.project)])
    return args


def _token_args(config: DeployConfig) -> list[str]:
    token = _token_value(config)
    if not token:
        return []
    return ["--token", token]


def _resolve_target_args(config: DeployConfig) -> list[str]:
    """Translate config.environment + vercel.prod into Vercel CLI flags."""
    if config.vercel.prod or config.environment == "production":
        out = ["--prod"]
        if config.vercel.skip_domain:
            out.append("--skip-domain")
        return out
    if config.environment and config.environment not in {"preview", "production"}:
        # Custom target — Vercel CLI accepts arbitrary target names.
        return [f"--target={config.environment}"]
    return []  # preview is the implicit default


def build_vercel_deploy_command(
    config: DeployConfig,
    project_root: Path,
    *,
    sanitized: bool = False,
) -> tuple[list[str], list[str]]:
    """Return (full_args, sanitized_args). The full args carry the real
    token; sanitized args have it replaced with <redacted>."""
    base = ["vercel", "deploy", "--yes", "--cwd", str(project_root)]
    base.extend(_resolve_target_args(config))
    base.extend(_scope_args(config))
    if config.vercel.prebuilt:
        base.append("--prebuilt")
    full = list(base) + _token_args(config)
    sanitized_args = sanitize_command_args(full, secret_values=[_token_value(config)])
    if sanitized:
        return sanitized_args, sanitized_args
    return full, sanitized_args


def build_vercel_build_command(
    config: DeployConfig,
    project_root: Path,
    *,
    sanitized: bool = False,
) -> tuple[list[str], list[str]]:
    base = ["vercel", "build", "--yes", "--cwd", str(project_root)]
    if config.vercel.prod or config.environment == "production":
        base.append("--prod")
    base.extend(_scope_args(config))
    full = list(base) + _token_args(config)
    sanitized_args = sanitize_command_args(full, secret_values=[_token_value(config)])
    if sanitized:
        return sanitized_args, sanitized_args
    return full, sanitized_args


def build_vercel_inspect_command(
    deployment_url: str,
    config: DeployConfig,
    *,
    sanitized: bool = False,
) -> tuple[list[str], list[str]]:
    base = [
        "vercel", "inspect", deployment_url,
        "--wait",
        f"--timeout={config.vercel.inspect_timeout}",
    ]
    base.extend(_scope_args(config))
    full = list(base) + _token_args(config)
    sanitized_args = sanitize_command_args(full, secret_values=[_token_value(config)])
    if sanitized:
        return sanitized_args, sanitized_args
    return full, sanitized_args


# ---------------------------------------------------------------------------
# URL extraction
# ---------------------------------------------------------------------------
def extract_deployment_url(stdout: str) -> str | None:
    """Pick the first https://...vercel.app URL from stdout.
    Falls back to the first https:// URL when no .vercel.app match found."""
    if not stdout:
        return None
    match = _VERCEL_URL_RE.search(stdout)
    if match:
        return match.group(0)
    match = _FALLBACK_URL_RE.search(stdout)
    if match:
        # Strip trailing punctuation that often follows URLs in shell output.
        return match.group(0).rstrip(".,;:)")
    return None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------
CommandRunner = Callable[[list[str], list[str], str, Path, dict[str, str], float | None], CommandResult]


def _default_command_runner(
    args: list[str],
    sanitized_args: list[str],
    name: str,
    cwd: Path,
    env: dict[str, str],
    timeout: float | None,
) -> CommandResult:
    started_at = now_iso()
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        exit_code = completed.returncode
        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
    except FileNotFoundError:
        exit_code = 127
        stdout = ""
        stderr = "vercel CLI not found on PATH"
    except subprocess.TimeoutExpired as exc:
        exit_code = 124
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = ((exc.stderr or "") if isinstance(exc.stderr, str) else "") + "\nvercel command timed out"
    return CommandResult(
        args=args,
        sanitized_args=sanitized_args,
        name=name,
        cwd=str(cwd),
        started_at=started_at,
        completed_at=now_iso(),
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
    )


def _classify_command_failure(name: str, result: CommandResult) -> str:
    """Return a `failure_type` from `DEPLOY_FAILURE_TYPES`."""
    if result.exit_code == 127 and "not found" in (result.stderr or "").lower():
        return "vercel_cli_missing"
    text = ((result.stdout or "") + " " + (result.stderr or "")).lower()
    if "no token" in text or "unauthorized" in text or "no credentials" in text or "vercel_token" in text:
        return "vercel_auth_missing"
    if name == "vercel_deploy" or name == "vercel_build":
        return "vercel_deploy_failed"
    if name == "vercel_inspect":
        return "vercel_inspect_failed"
    return "unknown"


# ---------------------------------------------------------------------------
# Top-level
# ---------------------------------------------------------------------------
def run_vercel_deploy(
    config: DeployConfig,
    project_root: Path,
    *,
    command_runner: CommandRunner | None = None,
    timeout_sec: float | None = VERCEL_DEPLOY_TIMEOUT_SEC,
) -> DeployResult:
    """Execute the configured deploy plan (optional build → deploy → inspect).
    Returns a DeployResult. Pure function modulo the injected runner."""
    runner = command_runner or _default_command_runner
    started_at = now_iso()
    commands_run: list[CommandResult] = []
    env = dict(os.environ)
    secret_values = [_token_value(config)]

    def _record(name: str, full_args: list[str], sanitized_args: list[str]) -> CommandResult:
        result = runner(full_args, sanitized_args, name, project_root, env, timeout_sec)
        # Sanitize stdout/stderr before they ever reach disk.
        result.stdout = redact_text(result.stdout, secret_values=secret_values)
        result.stderr = redact_text(result.stderr, secret_values=secret_values)
        commands_run.append(result)
        return result

    # Optional build step.
    if config.vercel.build_before_deploy or config.vercel.prebuilt:
        full, sanitized = build_vercel_build_command(config, project_root)
        result = _record("vercel_build", full, sanitized)
        if result.exit_code != 0:
            failure_type = _classify_command_failure("vercel_build", result)
            return DeployResult(
                status="failed",
                deployment_url=None,
                commands_run=commands_run,
                failure={
                    "failure_type": failure_type,
                    "message": result.stderr_tail() or result.stdout_tail() or "vercel build failed",
                    "failed_command": "vercel_build",
                },
                started_at=started_at,
                completed_at=now_iso(),
            )

    # Deploy.
    full, sanitized = build_vercel_deploy_command(config, project_root)
    deploy_result = _record("vercel_deploy", full, sanitized)
    if deploy_result.exit_code != 0:
        failure_type = _classify_command_failure("vercel_deploy", deploy_result)
        return DeployResult(
            status="failed",
            deployment_url=None,
            commands_run=commands_run,
            failure={
                "failure_type": failure_type,
                "message": deploy_result.stderr_tail() or deploy_result.stdout_tail() or "vercel deploy failed",
                "failed_command": "vercel_deploy",
            },
            started_at=started_at,
            completed_at=now_iso(),
        )

    deployment_url = extract_deployment_url(deploy_result.stdout)
    if not deployment_url:
        return DeployResult(
            status="unknown",
            deployment_url=None,
            commands_run=commands_run,
            failure={
                "failure_type": "deployment_url_missing",
                "message": "vercel deploy returned exit_code=0 but no https URL was found in stdout",
                "failed_command": "vercel_deploy",
            },
            started_at=started_at,
            completed_at=now_iso(),
        )

    # Inspect (optional).
    if not config.vercel.inspect:
        return DeployResult(
            status="ready",
            deployment_url=deployment_url,
            commands_run=commands_run,
            failure=None,
            started_at=started_at,
            completed_at=now_iso(),
        )
    full, sanitized = build_vercel_inspect_command(deployment_url, config)
    inspect_result = _record("vercel_inspect", full, sanitized)
    if inspect_result.exit_code != 0:
        failure_type = _classify_command_failure("vercel_inspect", inspect_result)
        return DeployResult(
            status="failed",
            deployment_url=deployment_url,
            commands_run=commands_run,
            failure={
                "failure_type": failure_type,
                "message": inspect_result.stderr_tail() or inspect_result.stdout_tail() or "vercel inspect failed",
                "failed_command": "vercel_inspect",
            },
            started_at=started_at,
            completed_at=now_iso(),
        )
    return DeployResult(
        status="ready",
        deployment_url=deployment_url,
        commands_run=commands_run,
        failure=None,
        started_at=started_at,
        completed_at=now_iso(),
    )


def serialize_command_results(commands_run: list[CommandResult]) -> list[dict[str, Any]]:
    """Convert CommandResult list into the deployment.json `commands` block.
    Uses sanitized_args + tail-truncated stdout/stderr."""
    return [
        {
            "name": c.name,
            "cmd": " ".join(c.sanitized_args),
            "args": c.sanitized_args,
            "exit_code": c.exit_code,
            "stdout_tail": c.stdout_tail(),
            "stderr_tail": c.stderr_tail(),
            "started_at": c.started_at,
            "completed_at": c.completed_at,
        }
        for c in commands_run
    ]


# ---------------------------------------------------------------------------
# MVP-4F: Rollback
# ---------------------------------------------------------------------------
@dataclass
class RollbackResult:
    status: str  # completed | failed | requested | skipped
    commands_run: list[CommandResult] = field(default_factory=list)
    failure: dict[str, Any] | None = None
    started_at: str = field(default_factory=now_iso)
    completed_at: str = field(default_factory=now_iso)


def build_vercel_rollback_command(
    config: DeployConfig,
    project_root: Path,
    deployment_url: str | None = None,
    *,
    sanitized: bool = False,
) -> tuple[list[str], list[str]]:
    """`vercel rollback [<deployment_url>] --cwd <root> [--scope ...] [--token ...]`.

    The Vercel CLI accepts an optional positional argument identifying the
    target deployment (URL or id). When omitted, Vercel rolls back to the
    previous production deployment automatically.
    """
    base = ["vercel", "rollback"]
    if deployment_url:
        base.append(deployment_url)
    base.extend(["--cwd", str(project_root)])
    base.extend(_scope_args(config))
    full = list(base) + _token_args(config)
    sanitized_args = sanitize_command_args(full, secret_values=[_token_value(config)])
    if sanitized:
        return sanitized_args, sanitized_args
    return full, sanitized_args


def build_vercel_rollback_status_command(
    config: DeployConfig,
    project_root: Path,
    *,
    sanitized: bool = False,
) -> tuple[list[str], list[str]]:
    base = ["vercel", "rollback", "status", "--cwd", str(project_root)]
    base.extend(_scope_args(config))
    full = list(base) + _token_args(config)
    sanitized_args = sanitize_command_args(full, secret_values=[_token_value(config)])
    if sanitized:
        return sanitized_args, sanitized_args
    return full, sanitized_args


def _classify_rollback_failure(name: str, result: CommandResult) -> str:
    if name == "vercel_rollback":
        return "vercel_rollback_failed"
    if name == "vercel_rollback_status":
        return "vercel_rollback_status_failed"
    return "unknown"


def run_vercel_rollback(
    config: DeployConfig,
    project_root: Path,
    deployment_url: str | None = None,
    *,
    command_runner: CommandRunner | None = None,
    timeout_sec: float | None = VERCEL_DEPLOY_TIMEOUT_SEC,
) -> RollbackResult:
    """Execute `vercel rollback [<url>]` and then `vercel rollback status`
    so the artifact captures both routing-recovery + verification."""
    runner = command_runner or _default_command_runner
    started_at = now_iso()
    commands_run: list[CommandResult] = []
    env = dict(os.environ)
    secret_values = [_token_value(config)]

    def _record(name: str, full_args: list[str], sanitized_args: list[str]) -> CommandResult:
        result = runner(full_args, sanitized_args, name, project_root, env, timeout_sec)
        result.stdout = redact_text(result.stdout, secret_values=secret_values)
        result.stderr = redact_text(result.stderr, secret_values=secret_values)
        commands_run.append(result)
        return result

    full, sanitized = build_vercel_rollback_command(config, project_root, deployment_url=deployment_url)
    rollback = _record("vercel_rollback", full, sanitized)
    if rollback.exit_code != 0:
        return RollbackResult(
            status="failed",
            commands_run=commands_run,
            failure={
                "failure_type": _classify_rollback_failure("vercel_rollback", rollback),
                "message": rollback.stderr_tail() or rollback.stdout_tail() or "vercel rollback failed",
                "failed_command": "vercel_rollback",
            },
            started_at=started_at,
            completed_at=now_iso(),
        )
    full, sanitized = build_vercel_rollback_status_command(config, project_root)
    status_cmd = _record("vercel_rollback_status", full, sanitized)
    if status_cmd.exit_code != 0:
        return RollbackResult(
            status="failed",
            commands_run=commands_run,
            failure={
                "failure_type": _classify_rollback_failure("vercel_rollback_status", status_cmd),
                "message": status_cmd.stderr_tail() or status_cmd.stdout_tail() or "vercel rollback status failed",
                "failed_command": "vercel_rollback_status",
            },
            started_at=started_at,
            completed_at=now_iso(),
        )
    return RollbackResult(
        status="completed",
        commands_run=commands_run,
        failure=None,
        started_at=started_at,
        completed_at=now_iso(),
    )
