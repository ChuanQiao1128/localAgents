"""Shared deploy primitives (MVP-4E).

`deploy.py` holds:
  - the deploy config schema + defaults + loader (reads
    `<project>/agent-studio.yaml` if present, otherwise falls back to
    "disabled" defaults)
  - filesystem layout helpers for deployment artifacts
  - `write_deployment_artifact` (the structured per-deployment record)
  - failure classification

Provider-specific code (right now: Vercel) lives in
`orchestrator/core/deploy_vercel.py`.

Security invariants:
  - tokens are NEVER persisted to disk (deployment.json, summary.md,
    controller-log.jsonl) and NEVER printed to stdout. The sanitized
    command always uses `<redacted>` in place of the real token.
  - deployment.json records `token_present: bool` and `token_env: <name>`
    only — enough to debug "is the env var set" without leaking the value.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.core.ids import now_iso, short_id
from orchestrator.core.yaml_loader import load_yaml


SCHEMA_VERSION_DEPLOYMENT = 1
SCHEMA_VERSION_SMOKE_CHECK = 1
SCHEMA_VERSION_ROLLBACK = 1


# RC-1.1.6: optional producer-side validation hook. When the env var
# `AGENT_STUDIO_VALIDATE_WRITES=1` is set, every artifact writer below
# routes its payload through the matching `validate_*` in
# `artifact_validation.py` BEFORE the file is written. Any error raises
# `ProducerValidationFailed` so a regression (writing a payload that
# would fail the consumer-side validator) is caught at write time. Off
# by default — production write paths are NOT penalized.
_VALIDATE_ENV_VAR = "AGENT_STUDIO_VALIDATE_WRITES"


class ProducerValidationFailed(ValueError):
    """Raised by an artifact writer when AGENT_STUDIO_VALIDATE_WRITES=1
    and the payload it is about to write fails its own validator."""


def _producer_validate(payload: dict[str, Any], validator_name: str) -> None:
    """Lazy-import the consumer validator and run it iff the env var is
    set. Lazy import avoids an artifact_validation → deploy circular
    import at module load."""
    if os.environ.get(_VALIDATE_ENV_VAR) != "1":
        return
    from orchestrator.core import artifact_validation as _av
    validator = getattr(_av, validator_name, None)
    if validator is None:
        raise ProducerValidationFailed(
            f"AGENT_STUDIO_VALIDATE_WRITES=1 but artifact_validation.{validator_name} not found"
        )
    errors = validator(payload)
    if errors:
        raise ProducerValidationFailed(
            f"producer-side validation failed for {validator_name}:\n  - " + "\n  - ".join(errors)
        )

DEPLOY_FAILURE_TYPES: frozenset[str] = frozenset({
    "vercel_cli_missing",
    "vercel_auth_missing",
    "vercel_deploy_failed",
    "vercel_inspect_failed",
    "deployment_url_missing",
    "unknown",
})

DEPLOY_STATUSES: frozenset[str] = frozenset({"ready", "failed", "unknown"})

# MVP-4F: smoke check + rollback failure taxonomies. Kept as frozensets so
# adapters / writers can validate what they emit against a single source.
SMOKE_FAILURE_TYPES: frozenset[str] = frozenset({
    "status_mismatch",
    "timeout",
    "connection_error",
    "expected_text_missing",
    "deployment_url_missing",
    "unknown",
})
SMOKE_STATUSES: frozenset[str] = frozenset({"passed", "failed", "skipped"})

ROLLBACK_FAILURE_TYPES: frozenset[str] = frozenset({
    "vercel_rollback_failed",
    "vercel_rollback_status_failed",
    "rollback_not_allowed",
    "unknown",
})
ROLLBACK_STATUSES: frozenset[str] = frozenset({"completed", "failed", "requested", "skipped"})

REDACTED = "<redacted>"


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
@dataclass
class VercelDeployConfig:
    mode: str = "source"           # source | prebuilt
    prod: bool = False
    prebuilt: bool = False
    build_before_deploy: bool = False
    inspect: bool = True
    inspect_timeout: str = "5m"
    skip_domain: bool = False
    token_env: str = "VERCEL_TOKEN"
    org_id_env: str = "VERCEL_ORG_ID"
    project_id_env: str = "VERCEL_PROJECT_ID"
    scope: str | None = None
    project: str | None = None


# MVP-4F: smoke check defaults — if the project doesn't override anything,
# we hit `/` and expect HTTP 200. That's the minimum reasonable signal that
# "the URL is up" without making any assumption about the app's routes.
def _default_smoke_checks() -> list[dict[str, Any]]:
    return [{"name": "home", "method": "GET", "path": "/", "expected_status": 200}]


@dataclass
class SmokeCheckConfig:
    enabled: bool = True
    timeout_sec: int = 10
    retries: int = 0
    checks: list[dict[str, Any]] = field(default_factory=_default_smoke_checks)


@dataclass
class RollbackConfig:
    enabled: bool = False
    production_only: bool = True
    trigger_on_smoke_failure: bool = True
    timeout: str = "30s"
    status_timeout: str = "30s"


@dataclass
class DeployConfig:
    enabled: bool = False
    target: str = "vercel"
    environment: str = "preview"   # preview | production | custom
    project_path: str = "."
    vercel: VercelDeployConfig = field(default_factory=VercelDeployConfig)
    smoke_checks: SmokeCheckConfig = field(default_factory=SmokeCheckConfig)
    rollback: RollbackConfig = field(default_factory=RollbackConfig)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "DeployConfig":
        deploy = payload or {}
        vercel_raw = (deploy.get("vercel") or {}) if isinstance(deploy.get("vercel"), dict) else {}
        defaults = VercelDeployConfig()
        vercel = VercelDeployConfig(
            mode=str(vercel_raw.get("mode") or defaults.mode),
            prod=bool(vercel_raw.get("prod", defaults.prod)),
            prebuilt=bool(vercel_raw.get("prebuilt", defaults.prebuilt)),
            build_before_deploy=bool(vercel_raw.get("build_before_deploy", defaults.build_before_deploy)),
            inspect=bool(vercel_raw.get("inspect", defaults.inspect)),
            inspect_timeout=str(vercel_raw.get("inspect_timeout") or defaults.inspect_timeout),
            skip_domain=bool(vercel_raw.get("skip_domain", defaults.skip_domain)),
            token_env=str(vercel_raw.get("token_env") or defaults.token_env),
            org_id_env=str(vercel_raw.get("org_id_env") or defaults.org_id_env),
            project_id_env=str(vercel_raw.get("project_id_env") or defaults.project_id_env),
            scope=vercel_raw.get("scope"),
            project=vercel_raw.get("project"),
        )
        smoke_raw = (deploy.get("smoke_checks") or {}) if isinstance(deploy.get("smoke_checks"), dict) else {}
        smoke_defaults = SmokeCheckConfig()
        checks_raw = smoke_raw.get("checks")
        if isinstance(checks_raw, list) and checks_raw:
            checks = [c for c in checks_raw if isinstance(c, dict)]
        else:
            checks = _default_smoke_checks()
        smoke = SmokeCheckConfig(
            enabled=bool(smoke_raw.get("enabled", smoke_defaults.enabled)),
            timeout_sec=int(smoke_raw.get("timeout_sec", smoke_defaults.timeout_sec)),
            retries=int(smoke_raw.get("retries", smoke_defaults.retries)),
            checks=checks,
        )
        rollback_raw = (deploy.get("rollback") or {}) if isinstance(deploy.get("rollback"), dict) else {}
        rollback_defaults = RollbackConfig()
        rollback = RollbackConfig(
            enabled=bool(rollback_raw.get("enabled", rollback_defaults.enabled)),
            production_only=bool(rollback_raw.get("production_only", rollback_defaults.production_only)),
            trigger_on_smoke_failure=bool(rollback_raw.get("trigger_on_smoke_failure", rollback_defaults.trigger_on_smoke_failure)),
            timeout=str(rollback_raw.get("timeout") or rollback_defaults.timeout),
            status_timeout=str(rollback_raw.get("status_timeout") or rollback_defaults.status_timeout),
        )
        return cls(
            enabled=bool(deploy.get("enabled", False)),
            target=str(deploy.get("target") or "vercel"),
            environment=str(deploy.get("environment") or "preview"),
            project_path=str(deploy.get("project_path") or "."),
            vercel=vercel,
            smoke_checks=smoke,
            rollback=rollback,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "target": self.target,
            "environment": self.environment,
            "project_path": self.project_path,
            "vercel": {
                "mode": self.vercel.mode,
                "prod": self.vercel.prod,
                "prebuilt": self.vercel.prebuilt,
                "build_before_deploy": self.vercel.build_before_deploy,
                "inspect": self.vercel.inspect,
                "inspect_timeout": self.vercel.inspect_timeout,
                "skip_domain": self.vercel.skip_domain,
                "token_env": self.vercel.token_env,
                "org_id_env": self.vercel.org_id_env,
                "project_id_env": self.vercel.project_id_env,
                "scope": self.vercel.scope,
                "project": self.vercel.project,
            },
            "smoke_checks": {
                "enabled": self.smoke_checks.enabled,
                "timeout_sec": self.smoke_checks.timeout_sec,
                "retries": self.smoke_checks.retries,
                "checks": list(self.smoke_checks.checks),
            },
            "rollback": {
                "enabled": self.rollback.enabled,
                "production_only": self.rollback.production_only,
                "trigger_on_smoke_failure": self.rollback.trigger_on_smoke_failure,
                "timeout": self.rollback.timeout,
                "status_timeout": self.rollback.status_timeout,
            },
        }


def project_config_path(project_path: Path) -> Path:
    return project_path / "agent-studio.yaml"


def load_deploy_config(project_path: Path) -> DeployConfig:
    """Read deploy block from <project>/agent-studio.yaml. Returns a
    disabled default config when the file is missing or malformed."""
    path = project_config_path(project_path)
    if not path.exists():
        return DeployConfig()
    try:
        loaded = load_yaml(path)
    except (OSError, ValueError):
        return DeployConfig()
    if not isinstance(loaded, dict):
        return DeployConfig()
    deploy_block = loaded.get("deploy") if isinstance(loaded.get("deploy"), dict) else None
    if deploy_block is None:
        return DeployConfig()
    return DeployConfig.from_dict(deploy_block)


# ---------------------------------------------------------------------------
# RC-2B: agentic.patch_worker configuration
# ---------------------------------------------------------------------------
@dataclass
class CodexPatchWorkerConfig:
    """Codex CLI knobs for the autonomous patch worker. Conservative
    defaults: workspace-write sandbox + on-request approval (no
    --dangerously-bypass-* / --yolo). The autonomous controller never
    overrides these from a CLI flag — config-only."""
    command: str = "codex"
    sandbox: str = "workspace-write"
    ask_for_approval: str = "on-request"
    timeout_sec: int = 600
    max_prompt_chars: int = 60_000


@dataclass
class AgenticConfig:
    """`agentic:` block of agent-studio.yaml. Drives the inner
    AgenticProjectRuntime when invoked from the autonomous controller."""
    patch_worker: str = "none"  # "none" | "codex"
    codex: CodexPatchWorkerConfig = field(default_factory=CodexPatchWorkerConfig)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AgenticConfig":
        worker = str(payload.get("patch_worker") or "none")
        if worker not in {"none", "codex"}:
            # Fail loud on typos rather than silently downgrade — RC-2A
            # taught us that silent default behavior masks real bugs.
            raise ValueError(
                f"agentic.patch_worker `{worker}` is not supported. "
                f"Allowed: ['none', 'codex']."
            )
        codex_block = payload.get("codex") or {}
        if not isinstance(codex_block, dict):
            codex_block = {}
        codex = CodexPatchWorkerConfig(
            command=str(codex_block.get("command") or "codex"),
            sandbox=str(codex_block.get("sandbox") or "workspace-write"),
            ask_for_approval=str(codex_block.get("ask_for_approval") or "on-request"),
            timeout_sec=int(codex_block.get("timeout_sec") or 600),
            max_prompt_chars=int(codex_block.get("max_prompt_chars") or 60_000),
        )
        return cls(patch_worker=worker, codex=codex)

    def to_dict(self) -> dict[str, Any]:
        return {
            "patch_worker": self.patch_worker,
            "codex": {
                "command": self.codex.command,
                "sandbox": self.codex.sandbox,
                "ask_for_approval": self.codex.ask_for_approval,
                "timeout_sec": self.codex.timeout_sec,
                "max_prompt_chars": self.codex.max_prompt_chars,
            },
        }


# ---------------------------------------------------------------------------
# RC-2C: project-level autonomous + integration overrides
# ---------------------------------------------------------------------------
@dataclass
class AutonomousOverrides:
    """Optional `autonomous:` block of agent-studio.yaml. When absent,
    the controller falls back to DEFAULT_BUDGETS / DEFAULT_INTEGRATION_POLICY
    in autonomous.py. Both sub-blocks are merged shallow into the new
    session; existing on-disk sessions are NOT migrated."""
    budgets: dict[str, Any] = field(default_factory=dict)
    integration: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, autonomous_block: dict[str, Any] | None,
                  integration_block: dict[str, Any] | None) -> "AutonomousOverrides":
        budgets = {}
        if isinstance(autonomous_block, dict):
            raw = autonomous_block.get("budgets")
            if isinstance(raw, dict):
                # Defensive: only accept int values for budget keys.
                for k, v in raw.items():
                    if isinstance(k, str) and isinstance(v, int):
                        budgets[k] = v
        integration = {}
        if isinstance(integration_block, dict):
            for k, v in integration_block.items():
                if isinstance(k, str):
                    integration[k] = v
        return cls(budgets=budgets, integration=integration)


def load_autonomous_overrides(project_path: Path) -> AutonomousOverrides:
    """Read the optional `autonomous:` and `integration:` top-level
    blocks from <project>/agent-studio.yaml. Returns empty overrides
    when absent or malformed — the caller falls back to controller
    defaults. RC-2C closes the RC-2A-004 finding."""
    path = project_config_path(project_path)
    if not path.exists():
        return AutonomousOverrides()
    try:
        loaded = load_yaml(path)
    except (OSError, ValueError):
        return AutonomousOverrides()
    if not isinstance(loaded, dict):
        return AutonomousOverrides()
    return AutonomousOverrides.from_dict(
        loaded.get("autonomous"),
        loaded.get("integration"),
    )


def load_agentic_config(project_path: Path) -> AgenticConfig:
    """Read `agentic:` block from <project>/agent-studio.yaml. Returns
    AgenticConfig() (patch_worker=`none`) when missing or malformed.
    Raises ValueError when `patch_worker` is set to an unsupported value."""
    path = project_config_path(project_path)
    if not path.exists():
        return AgenticConfig()
    try:
        loaded = load_yaml(path)
    except (OSError, ValueError):
        return AgenticConfig()
    if not isinstance(loaded, dict):
        return AgenticConfig()
    block = loaded.get("agentic")
    if not isinstance(block, dict):
        return AgenticConfig()
    return AgenticConfig.from_dict(block)


# ---------------------------------------------------------------------------
# Filesystem layout
# ---------------------------------------------------------------------------
def deployments_dir(project_path: Path, session_id: str) -> Path:
    return project_path / ".agent" / "autonomous" / "sessions" / session_id / "deployments"


def deployment_dir(project_path: Path, session_id: str, deployment_id: str) -> Path:
    return deployments_dir(project_path, session_id) / deployment_id


def deployment_artifact_path(project_path: Path, session_id: str, deployment_id: str) -> Path:
    return deployment_dir(project_path, session_id, deployment_id) / "deployment.json"


def new_deployment_id() -> str:
    return short_id("deployment")


# MVP-4F: smoke-check / rollback artifact layout
def smoke_checks_dir(project_path: Path, session_id: str) -> Path:
    return project_path / ".agent" / "autonomous" / "sessions" / session_id / "smoke-checks"


def smoke_check_dir(project_path: Path, session_id: str, smoke_check_id: str) -> Path:
    return smoke_checks_dir(project_path, session_id) / smoke_check_id


def smoke_check_artifact_path(project_path: Path, session_id: str, smoke_check_id: str) -> Path:
    return smoke_check_dir(project_path, session_id, smoke_check_id) / "smoke-check.json"


def new_smoke_check_id() -> str:
    return short_id("smoke")


def rollbacks_dir(project_path: Path, session_id: str) -> Path:
    return project_path / ".agent" / "autonomous" / "sessions" / session_id / "rollbacks"


def rollback_dir(project_path: Path, session_id: str, rollback_id: str) -> Path:
    return rollbacks_dir(project_path, session_id) / rollback_id


def rollback_artifact_path(project_path: Path, session_id: str, rollback_id: str) -> Path:
    return rollback_dir(project_path, session_id, rollback_id) / "rollback.json"


def new_rollback_id() -> str:
    return short_id("rollback")


# ---------------------------------------------------------------------------
# Sanitization
# ---------------------------------------------------------------------------
def sanitize_command_args(args: list[str], *, secret_values: list[str]) -> list[str]:
    """Replace any occurrence of a secret value in args with REDACTED.

    Used so the sanitized command list (the one we persist to artifacts /
    print to stdout) never contains a real token.
    """
    redacted: list[str] = []
    secret_set = {s for s in secret_values if s}
    for arg in args:
        if arg in secret_set:
            redacted.append(REDACTED)
            continue
        # Defensive: handle "--token=<value>" form if anyone ever uses it.
        replaced = arg
        for s in secret_set:
            if s and s in replaced:
                replaced = replaced.replace(s, REDACTED)
        redacted.append(replaced)
    return redacted


def redact_text(text: str, *, secret_values: list[str]) -> str:
    """Best-effort: strip secret values from arbitrary text (stdout/stderr)
    before persisting it. Strings shorter than 8 chars are not treated as
    secrets to avoid false-positive on common short tokens like 'true'."""
    out = text
    for s in secret_values:
        if s and len(s) >= 8:
            out = out.replace(s, REDACTED)
    return out


# ---------------------------------------------------------------------------
# Deployment artifact
# ---------------------------------------------------------------------------
def write_deployment_artifact(
    project_path: Path,
    *,
    session_id: str,
    project_id: str,
    config: DeployConfig,
    deployment_id: str,
    status: str,
    deployment_url: str | None,
    started_at: str,
    completed_at: str,
    git_branch: str | None,
    git_commit: str | None,
    sanitized_commands: list[dict[str, Any]],
    failure: dict[str, Any] | None,
    source_session_status: str,
    final_run_status_relpath: str,
    task_graph_relpath: str,
) -> Path:
    """Write deployment.json. All command args / stdout / stderr in
    sanitized_commands MUST already have token values redacted."""
    if status not in DEPLOY_STATUSES:
        raise ValueError(f"invalid deployment status: {status}")
    if failure is not None and failure.get("failure_type") not in DEPLOY_FAILURE_TYPES:
        raise ValueError(f"invalid failure_type: {failure.get('failure_type')}")
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION_DEPLOYMENT,
        "deployment_id": deployment_id,
        "session_id": session_id,
        "project_id": project_id,
        "target": config.target,
        "environment": _resolve_environment(config),
        "project_path": config.project_path,
        "status": status,
        "deployment_url": deployment_url,
        "started_at": started_at,
        "completed_at": completed_at,
        "git": {"branch": git_branch, "commit": git_commit},
        "commands": sanitized_commands,
        "vercel": {
            "mode": config.vercel.mode,
            "prod": config.vercel.prod,
            "prebuilt": config.vercel.prebuilt,
            "inspect": config.vercel.inspect,
            "inspect_timeout": config.vercel.inspect_timeout,
            "scope": config.vercel.scope,
            "project": config.vercel.project,
            "token_env": config.vercel.token_env,
            "token_present": bool(os.environ.get(config.vercel.token_env)),
            "org_id_env": config.vercel.org_id_env,
            "org_id_present": bool(os.environ.get(config.vercel.org_id_env)),
            "project_id_env": config.vercel.project_id_env,
            "project_id_present": bool(os.environ.get(config.vercel.project_id_env)),
        },
        "source": {
            "session_status": source_session_status,
            "task_graph_path": task_graph_relpath,
            "final_run_status_path": final_run_status_relpath,
        },
        "failure": failure,
    }
    out_path = deployment_artifact_path(project_path, session_id, deployment_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _producer_validate(payload, "validate_deployment")
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def list_deployments(project_path: Path, session_id: str) -> list[dict[str, Any]]:
    root = deployments_dir(project_path, session_id)
    if not root.is_dir():
        return []
    out: list[tuple[float, dict[str, Any]]] = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        path = sub / "deployment.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append((mtime, payload))
    out.sort(key=lambda item: item[0])
    return [p for _, p in out]


def latest_deployment(project_path: Path, session_id: str) -> dict[str, Any] | None:
    items = list_deployments(project_path, session_id)
    return items[-1] if items else None


def write_smoke_check_artifact(
    project_path: Path,
    *,
    session_id: str,
    project_id: str,
    smoke_check_id: str,
    deployment_id: str | None,
    deployment_url: str | None,
    environment: str,
    status: str,
    started_at: str,
    completed_at: str,
    checks: list[dict[str, Any]],
    failure: dict[str, Any] | None,
) -> Path:
    """Write the per-smoke-check artifact. Caller is responsible for
    truncating response_body_tail and redacting any user-configured headers
    BEFORE passing them in `checks`."""
    if status not in SMOKE_STATUSES:
        raise ValueError(f"invalid smoke status: {status}")
    if failure is not None and failure.get("failure_type") not in SMOKE_FAILURE_TYPES:
        raise ValueError(f"invalid smoke failure_type: {failure.get('failure_type')}")
    payload = {
        "schema_version": SCHEMA_VERSION_SMOKE_CHECK,
        "smoke_check_id": smoke_check_id,
        "session_id": session_id,
        "project_id": project_id,
        "deployment_id": deployment_id,
        "deployment_url": deployment_url,
        "environment": environment,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "checks": checks,
        "failure": failure,
    }
    out_path = smoke_check_artifact_path(project_path, session_id, smoke_check_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _producer_validate(payload, "validate_smoke_check")
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def list_smoke_checks(project_path: Path, session_id: str) -> list[dict[str, Any]]:
    root = smoke_checks_dir(project_path, session_id)
    if not root.is_dir():
        return []
    out: list[tuple[float, dict[str, Any]]] = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        path = sub / "smoke-check.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append((mtime, payload))
    out.sort(key=lambda item: item[0])
    return [p for _, p in out]


def latest_smoke_check(project_path: Path, session_id: str) -> dict[str, Any] | None:
    items = list_smoke_checks(project_path, session_id)
    return items[-1] if items else None


def write_rollback_artifact(
    project_path: Path,
    *,
    session_id: str,
    project_id: str,
    rollback_id: str,
    deployment_id: str | None,
    smoke_check_id: str | None,
    target: str,
    environment: str,
    status: str,
    started_at: str,
    completed_at: str,
    trigger: str,
    sanitized_commands: list[dict[str, Any]],
    failure: dict[str, Any] | None,
) -> Path:
    """Write the per-rollback artifact. Sanitized command args MUST be
    pre-redacted by the caller."""
    if status not in ROLLBACK_STATUSES:
        raise ValueError(f"invalid rollback status: {status}")
    if failure is not None and failure.get("failure_type") not in ROLLBACK_FAILURE_TYPES:
        raise ValueError(f"invalid rollback failure_type: {failure.get('failure_type')}")
    payload = {
        "schema_version": SCHEMA_VERSION_ROLLBACK,
        "rollback_id": rollback_id,
        "session_id": session_id,
        "project_id": project_id,
        "deployment_id": deployment_id,
        "smoke_check_id": smoke_check_id,
        "target": target,
        "environment": environment,
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "trigger": trigger,
        "commands": sanitized_commands,
        "failure": failure,
    }
    out_path = rollback_artifact_path(project_path, session_id, rollback_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _producer_validate(payload, "validate_rollback")
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out_path


def list_rollbacks(project_path: Path, session_id: str) -> list[dict[str, Any]]:
    root = rollbacks_dir(project_path, session_id)
    if not root.is_dir():
        return []
    out: list[tuple[float, dict[str, Any]]] = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        path = sub / "rollback.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0.0
        out.append((mtime, payload))
    out.sort(key=lambda item: item[0])
    return [p for _, p in out]


def latest_rollback(project_path: Path, session_id: str) -> dict[str, Any] | None:
    items = list_rollbacks(project_path, session_id)
    return items[-1] if items else None


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------
def _resolve_environment(config: DeployConfig) -> str:
    """Materialize environment from explicit config + the prod flag.
    `prod=true` always wins (matches Vercel CLI semantics)."""
    if config.vercel.prod:
        return "production"
    return config.environment or "preview"
