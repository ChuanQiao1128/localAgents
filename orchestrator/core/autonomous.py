"""Autonomous controller for the AI-native SDLC outer loop (MVP-4A).

Translates a `requirements.md` PRD into a sequence of bounded change tasks,
then drives each one through the existing `agentic_project` inner loop:
multi-candidate generation → eval → repair → Promotion Gate → safe apply.

Design constraints:
- Sequential only. No parallel tasks (parallel is MVP-5+).
- No deploy. MVP-4A stops at "applied + committed". Deploy is MVP-4E.
- Resumable. Session state is on disk; restart picks up from the last
  incomplete task without losing evidence.
- No auto-push. Every run creates `agentic/autonomous/<session_id>` branch
  and commits per task; the user pushes / merges to main themselves.

Schemas (see SCHEMA_VERSION):
- task-graph.json:           project root
- autonomous-session.json:   .agent/autonomous/sessions/<session_id>/
- controller-log.jsonl:      .agent/autonomous/sessions/<session_id>/
- final-run-status.md:       .agent/autonomous/sessions/<session_id>/
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from orchestrator.core.deploy import (
    DeployConfig,
    deployment_artifact_path,
    deployments_dir,
    latest_deployment,
    latest_rollback,
    latest_smoke_check,
    list_deployments,
    list_rollbacks,
    list_smoke_checks,
    load_deploy_config,
    new_deployment_id,
    new_rollback_id,
    write_deployment_artifact,
    write_rollback_artifact,
)
from orchestrator.core.deploy_vercel import (
    DeployResult,
    RollbackResult,
    build_vercel_build_command,
    build_vercel_deploy_command,
    build_vercel_inspect_command,
    build_vercel_rollback_command,
    run_vercel_deploy,
    run_vercel_rollback,
    serialize_command_results,
)
from orchestrator.core.smoke import (
    SmokeRunResult,
    persist_smoke_run,
    run_smoke_checks,
)
from orchestrator.core.ids import now_iso, short_id
from orchestrator.core.review_queue import (
    DEFAULT_ALLOWED_ACTIONS,
    ReviewItem,
    SCHEMA_VERSION_REVIEW_ITEM,
    create_review_item,
    has_blocking_open_reviews,
    list_blocking_open,
    list_review_items,
    new_review_id,
)


SCHEMA_VERSION_TASK_GRAPH = 1
SCHEMA_VERSION_SESSION = 1


# ---------------------------------------------------------------------------
# Default budgets (Chuan's spec)
# ---------------------------------------------------------------------------
DEFAULT_BUDGETS: dict[str, int] = {
    "max_tasks_per_session": 20,
    "max_abandoned_tasks": 2,
    "max_needs_human_review_tasks": 1,
    "max_total_inner_runs": 30,
    # MVP-4C: cap on auto-generated corrective tasks per session. When the
    # cap is reached the session pauses with reason "too-many-corrective-tasks".
    "max_corrective_tasks": 3,
}

# Controller event types — kept narrow so log consumers can switch on `event`.
EVENT_TYPES: frozenset[str] = frozenset({
    "session_started",
    "session_resumed",
    "task_started",
    "inner_run_started",
    "inner_run_completed",
    "candidate_selected",
    "candidate_applied",
    "task_committed",
    "task_abandoned",
    "task_needs_human_review",
    "integration_started",
    "integration_passed",
    "integration_failed",
    "corrective_task_created",
    "corrective_task_started",
    "corrective_task_completed",
    "corrective_task_limit_reached",
    "review_item_created",
    "review_item_approved",
    "review_item_rejected",
    "review_item_resolved",
    "resume_blocked_by_open_reviews",
    "deployment_started",
    "deployment_command_completed",
    "deployment_inspect_started",
    "deployment_succeeded",
    "deployment_failed",
    "deployment_review_item_created",
    "smoke_check_started",
    "smoke_check_completed",
    "smoke_check_failed",
    "smoke_review_item_created",
    "rollback_started",
    "rollback_completed",
    "rollback_failed",
    "rollback_skipped",
    "rollback_review_item_created",
    "session_paused",
    "session_completed",
})


# MVP-4E: default deployment block stamped on a session before any deploy
# adapter has run. `enabled` mirrors the project's deploy.enabled config
# at the time of session start; status flows pending → deploying →
# (deployed | failed) as the controller drives the deploy.
DEFAULT_DEPLOYMENT_STATE: dict[str, Any] = {
    "enabled": False,
    "target": "vercel",
    "status": "not-configured",
    "latest_deployment_id": None,
    "latest_deployment_url": None,
    "latest_failure_type": None,
    # MVP-4F: smoke + rollback bookkeeping. status flow extends to
    #   verified | smoke-failed | rolled-back | rollback-failed.
    "latest_smoke_check_id": None,
    "latest_smoke_status": None,
    "latest_smoke_failure_type": None,
    "latest_rollback_id": None,
    "latest_rollback_status": None,
    "latest_rollback_failure_type": None,
}


# MVP-4B: integration phase policy. Periodically (and at session end) run
# the same eval commands the inner loop uses, but against the *cumulative*
# project working tree — catches regressions where task A passed in
# isolation but conflicts with task B's later commit.
DEFAULT_INTEGRATION_POLICY: dict[str, Any] = {
    "every_n_tasks": 3,         # run integration after every 3 successful task commits
    "run_at_session_end": True, # run a final integration before session_completed
    "timeout_sec": 600,
}


# ---------------------------------------------------------------------------
# PRD → task-graph parser (deterministic; no LLM)
# ---------------------------------------------------------------------------
_DEFAULT_SCOPE_PATTERNS: tuple[tuple[str, str], ...] = (
    ("apps/web", "apps/web/**"),
    ("apps", "apps/**"),
    ("src", "src/**"),
    ("packages", "packages/**"),
    ("tests", "tests/**"),
)


def _detect_scope_paths(project_path: Path) -> list[str]:
    """Pick a sensible default `scope_paths` for tasks that don't specify
    their own. We scan the repo for known top-level dirs and emit globs."""
    paths: list[str] = []
    for dirname, glob in _DEFAULT_SCOPE_PATTERNS:
        if (project_path / dirname).is_dir():
            paths.append(glob)
    if not paths:
        # No detected source layout — use a conservative default that still
        # allows the autonomous loop to make changes anywhere except .agent/.
        paths = ["**"]
    return paths


def _detect_repo_metadata(project_path: Path) -> dict[str, Any]:
    """Lightweight repo / framework / scripts detection. Used by the
    auto-generated architecture.md so the autonomous loop has a basic
    'what does this project look like' summary without inventing facts."""
    metadata: dict[str, Any] = {
        "framework": "unknown",
        "package_manager": "unknown",
        "known_scripts": [],
        "deploy_target_placeholder": "vercel",  # MVP-4E target; only a placeholder here
    }
    package_json = project_path / "apps/web/package.json"
    if not package_json.exists():
        package_json = project_path / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            scripts = data.get("scripts") or {}
            metadata["known_scripts"] = sorted(scripts.keys())
            metadata["package_manager"] = "npm"  # could refine later
            deps = {**(data.get("dependencies") or {}), **(data.get("devDependencies") or {})}
            if "next" in deps:
                metadata["framework"] = "next"
            elif "react" in deps:
                metadata["framework"] = "react"
        except (OSError, json.JSONDecodeError):
            pass
    if (project_path / "pyproject.toml").exists() or (project_path / "requirements.txt").exists():
        if metadata["framework"] == "unknown":
            metadata["framework"] = "python"
        metadata["package_manager"] = "pip"
    return metadata


_TASK_HEADER_RE = re.compile(r"^(#+)\s+(.*?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.*?)\s*$")
_DEPENDS_RE = re.compile(r"^\s*Depends:\s*(.+)$", re.IGNORECASE)
_SCOPE_RE = re.compile(r"^\s*Scope:\s*(.*)$", re.IGNORECASE)
_RISK_RE = re.compile(r"^\s*Risk:\s*(low|medium|high)\s*$", re.IGNORECASE)


def _clean_meta_value(raw: str) -> str:
    """Strip wrapping markdown decoration (backticks, double-quotes) from a
    meta-line value. RC-4C.1: writers tend to use ``Scope: `app/**` `` because
    backticks read as code in markdown previewers, but the parser used to
    capture the backticks literally and break fnmatch (`fnmatch.fnmatch(
    "app/page.tsx", "`app/**`") → False`). This helper normalizes the
    value back to a clean glob the Apply Gate can match.
    """
    value = raw.strip()
    while len(value) >= 2 and value[0] == value[-1] and value[0] in {"`", '"'}:
        value = value[1:-1].strip()
    return value


def parse_requirements_md(md_text: str, project_path: Path) -> dict[str, Any]:
    """Parse a PRD markdown into a task-graph dict.

    Conventions:
      - The first H1 (`# ...`) is the project goal/title.
      - Each H2 section (`## ...`) becomes one task.
      - Within a section:
          - `Depends:` line lists task ids this task waits for (auto-IDs are
            assigned in declaration order: task-001, task-002, ...).
          - `Scope:` line lists path globs for allowed_change_scope; defaults
            to detected repo layout when missing.
          - `Risk:` line is `low | medium | high`; defaults to `medium`.
          - `- ...` bullets become acceptance_criteria.
          - The first non-meta paragraph becomes the intent body.

    Free-form text outside H2 sections is preserved as the PRD overview.
    """
    lines = md_text.splitlines()
    overview_lines: list[str] = []
    title: str | None = None
    sections: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    for raw_line in lines:
        header_match = _TASK_HEADER_RE.match(raw_line)
        if header_match:
            depth, text = header_match.group(1), header_match.group(2).strip()
            if depth == "#" and title is None:
                title = text
                continue
            if depth == "##":
                if current is not None:
                    sections.append(current)
                current = {"title": text, "lines": []}
                continue
        if current is None:
            overview_lines.append(raw_line)
        else:
            current["lines"].append(raw_line)
    if current is not None:
        sections.append(current)

    overview = "\n".join(overview_lines).strip()
    detected_scope = _detect_scope_paths(project_path)

    tasks: list[dict[str, Any]] = []
    title_to_id: dict[str, str] = {}
    for index, section in enumerate(sections, start=1):
        task_id = f"task-{index:03d}"
        title_to_id[section["title"].lower()] = task_id
        body_lines = section["lines"]
        acceptance: list[str] = []
        depends: list[str] = []
        scope: list[str] = []
        risk = "medium"
        intent_lines: list[str] = []
        # RC-4C.1: a `Scope:` line with no inline value (e.g. just `Scope:`)
        # opens a multi-line bullet block; subsequent `- foo` / `* foo`
        # lines until the next non-bullet, non-blank line populate scope.
        # Same shape supported for `Acceptance:` so writers can pick the
        # form they prefer. `scope_open` / `acceptance_open` track whether
        # we're inside an open block.
        scope_open = False
        acceptance_open = False
        for raw in body_lines:
            depends_match = _DEPENDS_RE.match(raw)
            if depends_match:
                deps_raw = [d.strip() for d in depends_match.group(1).split(",") if d.strip()]
                depends = [_clean_meta_value(d) for d in deps_raw if _clean_meta_value(d)]
                scope_open = False
                acceptance_open = False
                continue
            scope_match = _SCOPE_RE.match(raw)
            if scope_match:
                inline = scope_match.group(1).strip()
                if inline:
                    scope = [
                        _clean_meta_value(s)
                        for s in inline.split(",")
                        if _clean_meta_value(s)
                    ]
                    scope_open = False
                else:
                    # `Scope:` alone — open a multi-line bullet block.
                    scope_open = True
                acceptance_open = False
                continue
            risk_match = _RISK_RE.match(raw)
            if risk_match:
                risk = risk_match.group(1).lower()
                scope_open = False
                acceptance_open = False
                continue
            # Detect `Acceptance:` opener so writers can use the same
            # multi-line bullet form for acceptance criteria.
            stripped = raw.strip()
            if stripped.lower() in {"acceptance:", "acceptance criteria:"}:
                acceptance_open = True
                scope_open = False
                continue
            bullet_match = _BULLET_RE.match(raw)
            if bullet_match:
                value = _clean_meta_value(bullet_match.group(1))
                if scope_open:
                    if value:
                        scope.append(value)
                    continue
                # Bullet inside an open Acceptance block, OR a free-floating
                # bullet (default acceptance criterion behavior). Both append
                # to acceptance_criteria.
                if value:
                    acceptance.append(value)
                continue
            # Any non-blank non-bullet line closes any open multi-line block.
            if stripped:
                scope_open = False
                acceptance_open = False
                intent_lines.append(stripped)
        # Resolve dependency references: allow task title or task-id form.
        resolved_deps: list[str] = []
        for dep in depends:
            if dep.startswith("task-"):
                resolved_deps.append(dep)
            else:
                lookup = title_to_id.get(dep.lower())
                if lookup:
                    resolved_deps.append(lookup)
        intent = " ".join(intent_lines).strip() or section["title"]
        tasks.append({
            "id": task_id,
            "title": section["title"],
            "intent": intent,
            "acceptance_criteria": acceptance,
            "scope_paths": scope or detected_scope,
            "dependencies": resolved_deps,
            "status": "pending",
            "risk": risk,
            "run_ids": [],
            "commit": None,
        })

    return {
        "schema_version": SCHEMA_VERSION_TASK_GRAPH,
        "project_title": title or "untitled",
        "overview": overview,
        "tasks": tasks,
    }


def render_prd_md(task_graph: dict[str, Any], requirements_md_text: str) -> str:
    """Stable, human-readable PRD summary derived from the task-graph and
    the original requirements. MVP-4A keeps this lightweight."""
    title = task_graph.get("project_title") or "Untitled"
    lines = [f"# PRD: {title}", ""]
    overview = task_graph.get("overview") or ""
    if overview:
        lines += ["## Overview", "", overview, ""]
    lines += ["## Tasks", ""]
    for task in task_graph.get("tasks") or []:
        lines.append(f"### {task['id']} — {task['title']} (risk: {task['risk']})")
        lines.append("")
        lines.append(task["intent"])
        if task.get("acceptance_criteria"):
            lines.append("")
            lines.append("Acceptance criteria:")
            for c in task["acceptance_criteria"]:
                lines.append(f"- {c}")
        if task.get("dependencies"):
            lines.append("")
            lines.append(f"Depends on: {', '.join(task['dependencies'])}")
        if task.get("scope_paths"):
            lines.append("")
            lines.append(f"Scope paths: {', '.join(task['scope_paths'])}")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Original requirements (verbatim)")
    lines.append("")
    lines.append(requirements_md_text.strip())
    lines.append("")
    return "\n".join(lines)


def render_acceptance_criteria(task_graph: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "project_title": task_graph.get("project_title"),
        "tasks": [
            {"id": t["id"], "title": t["title"], "criteria": list(t.get("acceptance_criteria") or [])}
            for t in (task_graph.get("tasks") or [])
        ],
    }


def render_architecture_md(task_graph: dict[str, Any], project_path: Path) -> str:
    metadata = _detect_repo_metadata(project_path)
    lines = [
        f"# Architecture (auto-generated, MVP-4A lightweight)",
        "",
        f"Project title: {task_graph.get('project_title')}",
        "",
        "## Detected repo metadata",
        "",
        f"- Framework: {metadata['framework']}",
        f"- Package manager: {metadata['package_manager']}",
        f"- Known scripts: {', '.join(metadata['known_scripts']) or 'none'}",
        f"- Deploy target placeholder: {metadata['deploy_target_placeholder']} (not active in MVP-4A)",
        "",
        "## Risk notes",
        "",
        "- This file is auto-generated by the deterministic decomposer; it does NOT contain LLM-generated architectural reasoning.",
        "- The autonomous controller treats per-task `scope_paths` as the binding contract, not anything written here.",
        "- A future MVP may upgrade this file to evidence-grounded architectural decisions.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Session state
# ---------------------------------------------------------------------------
@dataclass
class AutonomousSession:
    schema_version: int
    session_id: str
    project_id: str
    status: str  # running | paused | completed
    current_task_id: str | None
    branch: str
    started_at: str
    updated_at: str
    budgets: dict[str, int] = field(default_factory=lambda: dict(DEFAULT_BUDGETS))
    counters: dict[str, int] = field(default_factory=lambda: {
        "completed_tasks": 0,
        "abandoned_tasks": 0,
        "needs_review_tasks": 0,
        "inner_runs": 0,
        "integrations_run": 0,
        "integrations_passed": 0,
        "integrations_failed": 0,
        "corrective_tasks_created": 0,
        "corrective_tasks_completed": 0,
    })
    integration_policy: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_INTEGRATION_POLICY))
    last_integration_result: dict[str, Any] | None = None
    deployment: dict[str, Any] = field(default_factory=lambda: dict(DEFAULT_DEPLOYMENT_STATE))
    pause_reason: str | None = None
    halt_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "status": self.status,
            "current_task_id": self.current_task_id,
            "branch": self.branch,
            "started_at": self.started_at,
            "updated_at": self.updated_at,
            "budgets": self.budgets,
            "counters": self.counters,
            "integration_policy": self.integration_policy,
            "last_integration_result": self.last_integration_result,
            "deployment": self.deployment,
            "pause_reason": self.pause_reason,
            "halt_requested": self.halt_requested,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "AutonomousSession":
        # Backward-compat: pre-MVP-4B sessions lack integration_* fields.
        # We merge defaults so old session.json files keep loading.
        old_counters = payload.get("counters") or {}
        return cls(
            schema_version=int(payload.get("schema_version") or SCHEMA_VERSION_SESSION),
            session_id=str(payload["session_id"]),
            project_id=str(payload["project_id"]),
            status=str(payload.get("status") or "running"),
            current_task_id=payload.get("current_task_id"),
            branch=str(payload.get("branch") or ""),
            started_at=str(payload.get("started_at") or ""),
            updated_at=str(payload.get("updated_at") or ""),
            budgets={**DEFAULT_BUDGETS, **(payload.get("budgets") or {})},
            counters={
                "completed_tasks": int(old_counters.get("completed_tasks", 0)),
                "abandoned_tasks": int(old_counters.get("abandoned_tasks", 0)),
                "needs_review_tasks": int(old_counters.get("needs_review_tasks", 0)),
                "inner_runs": int(old_counters.get("inner_runs", 0)),
                "integrations_run": int(old_counters.get("integrations_run", 0)),
                "integrations_passed": int(old_counters.get("integrations_passed", 0)),
                "integrations_failed": int(old_counters.get("integrations_failed", 0)),
                "corrective_tasks_created": int(old_counters.get("corrective_tasks_created", 0)),
                "corrective_tasks_completed": int(old_counters.get("corrective_tasks_completed", 0)),
            },
            integration_policy={**DEFAULT_INTEGRATION_POLICY, **(payload.get("integration_policy") or {})},
            last_integration_result=payload.get("last_integration_result"),
            deployment={**DEFAULT_DEPLOYMENT_STATE, **(payload.get("deployment") or {})},
            pause_reason=payload.get("pause_reason"),
            halt_requested=bool(payload.get("halt_requested") or False),
        )


# ---------------------------------------------------------------------------
# Filesystem layout helpers
# ---------------------------------------------------------------------------
def session_dir(project_path: Path, session_id: str) -> Path:
    return project_path / ".agent" / "autonomous" / "sessions" / session_id


def session_file(project_path: Path, session_id: str) -> Path:
    return session_dir(project_path, session_id) / "autonomous-session.json"


def controller_log_file(project_path: Path, session_id: str) -> Path:
    return session_dir(project_path, session_id) / "controller-log.jsonl"


def final_run_status_file(project_path: Path, session_id: str) -> Path:
    return session_dir(project_path, session_id) / "final-run-status.md"


def task_graph_file(project_path: Path) -> Path:
    return project_path / "task-graph.json"


def find_active_session(project_path: Path) -> AutonomousSession | None:
    """Return the most recently updated session, if any."""
    sessions_root = project_path / ".agent" / "autonomous" / "sessions"
    if not sessions_root.is_dir():
        return None
    candidates: list[tuple[float, Path]] = []
    for sdir in sessions_root.iterdir():
        if not sdir.is_dir():
            continue
        spath = sdir / "autonomous-session.json"
        if not spath.exists():
            continue
        try:
            candidates.append((spath.stat().st_mtime, spath))
        except OSError:
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    try:
        payload = json.loads(candidates[0][1].read_text(encoding="utf-8"))
        return AutonomousSession.from_dict(payload)
    except (OSError, json.JSONDecodeError):
        return None


# ---------------------------------------------------------------------------
# Git ops
# ---------------------------------------------------------------------------
def _git(project_path: Path, *args: str, check: bool = True, capture: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=project_path,
        text=True,
        capture_output=capture,
        check=check,
    )


# Files at project root that the autonomous controller owns and writes to
# during a session. These mutate as tasks complete and must NOT block the
# worktree-clean check (otherwise resume / restart would be impossible).
# Single source of truth for "files the autonomous controller mutates as
# runtime bookkeeping at the project root." Any worktree-clean check that
# tolerates these (the controller's own `is_worktree_clean` AND the Apply
# Gate's worktree check in `run_package.py::apply_selected_candidate`)
# MUST import this constant — duplicating it has caused drift before
# (see docs/local-agent-dev-studio-audit.md, "Code Risks #1").
AUTONOMOUS_OWNED_PATHS: frozenset[str] = frozenset({"task-graph.json"})

# Backward-compat alias for any in-tree caller that was reading the older
# private name. New callers should import the public name above.
_AUTONOMOUS_OWNED_PATHS = AUTONOMOUS_OWNED_PATHS


def is_worktree_clean(project_path: Path) -> tuple[bool, str]:
    """True iff `git status --porcelain` shows nothing user-owned uncommitted.
    Tolerated as runtime bookkeeping: `.agent/` (evidence + session state)
    and `task-graph.json` (controller-owned task state at project root).
    Returns (clean, reason_when_not_clean)."""
    try:
        result = _git(project_path, "status", "--porcelain", check=False)
    except FileNotFoundError:
        return False, "git not installed"
    if result.returncode != 0:
        return False, f"git status failed: {result.stderr.strip() or 'no message'}"
    for line in result.stdout.splitlines():
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path.startswith(".agent/") or path == ".agent":
            continue
        if path in _AUTONOMOUS_OWNED_PATHS:
            continue
        return False, f"working tree not clean: `{path}` is uncommitted"
    return True, ""


def is_git_repo(project_path: Path) -> bool:
    return (project_path / ".git").exists()


def session_branch_name(session_id: str) -> str:
    return f"agentic/autonomous/{session_id}"


def create_session_branch(project_path: Path, session_id: str) -> str:
    """Create and check out the per-session branch."""
    branch = session_branch_name(session_id)
    # If branch already exists (e.g. resume), check it out instead.
    existing = _git(project_path, "branch", "--list", branch, check=False).stdout.strip()
    if existing:
        _git(project_path, "checkout", branch)
    else:
        _git(project_path, "checkout", "-b", branch)
    return branch


# ---------------------------------------------------------------------------
# Integration phase (MVP-4B)
# ---------------------------------------------------------------------------
def integration_results_file(project_path: Path, session_id: str) -> Path:
    return session_dir(project_path, session_id) / "integration-results.jsonl"


def integration_failure_summary_file(project_path: Path, session_id: str) -> Path:
    return session_dir(project_path, session_id) / "integration-failure-summary.md"


def build_integration_commands(project_path: Path) -> list[dict[str, Any]]:
    """Derive the integration-check command list from the project. We reuse
    the agentic_project eval-harness logic so per-task and integration
    checks share the same notion of 'is the build OK / do tests pass'."""
    # Late import: agentic_runtime imports from this module are not cyclic
    # because we only import inside the function.
    from orchestrator.core.agentic_runtime import _build_eval_harness, _build_context_pack
    intent = {"goal": "integration check", "allowed_change_scope": {"paths": ["**"]}}
    context = _build_context_pack(project_path, intent)
    harness = _build_eval_harness(project_path, context)
    return list(harness.get("commands") or [])


def run_integration_check(
    project_path: Path,
    commands: list[dict[str, Any]],
    *,
    timeout_sec: int = 600,
) -> dict[str, Any]:
    """Run every required command in the project working tree and return a
    structured result. Optional commands run too but their failure does
    not flip `passed`. Mirrors the agentic eval execution shape so consumers
    can read both with the same code."""
    from orchestrator.core.agentic_runtime import _execute_eval_command
    started_at = now_iso()
    started_ts = datetime.now(timezone.utc).timestamp()
    commands_run: list[dict[str, Any]] = []
    failed_required: list[str] = []
    if not commands:
        return {
            "schema_version": 1,
            "started_at": started_at,
            "finished_at": now_iso(),
            "duration_sec": 0.0,
            "commands_run": [],
            "passed": True,
            "failed_required_command_names": [],
            "reason": "no integration commands declared (treating as pass)",
        }
    for command in commands:
        result = _execute_eval_command(project_path, command, timeout_sec)
        commands_run.append({
            "name": result.get("name"),
            "cmd": result.get("cmd"),
            "required": bool(result.get("required")),
            "executed": bool(result.get("executed")),
            "exit_code": result.get("exit_code"),
            "passed": bool(result.get("passed")),
            "stdout_tail": str(result.get("stdout") or "")[-2000:],
            "stderr_tail": str(result.get("stderr") or "")[-2000:],
        })
        if result.get("required") and not result.get("passed"):
            failed_required.append(str(result.get("name") or ""))
    finished_ts = datetime.now(timezone.utc).timestamp()
    return {
        "schema_version": 1,
        "started_at": started_at,
        "finished_at": now_iso(),
        "duration_sec": round(finished_ts - started_ts, 3),
        "commands_run": commands_run,
        "passed": not failed_required,
        "failed_required_command_names": failed_required,
        "reason": "all required commands passed" if not failed_required else f"failed required: {', '.join(failed_required)}",
    }


def record_integration_result(project_path: Path, session_id: str, result: dict[str, Any]) -> str:
    """Append one integration result to the per-session JSONL."""
    log_path = integration_results_file(project_path, session_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(result, ensure_ascii=False) + "\n")
    return str(log_path)


def read_integration_results(project_path: Path, session_id: str) -> list[dict[str, Any]]:
    """Read all integration results for a session, **oldest-first**.

    Contract pinned by RC-2E.1 (audit Code Risks #4): the renderer in
    `_update_final_status` and the controller's "did the last
    integration pass" probe both rely on this ordering — they do
    `read_integration_results(...)[-1]` to get the most recent result.
    The producer (`record_integration_result`) appends to a JSONL file,
    so on-disk order IS chronological; this reader preserves that
    order. If a future implementation changes the storage format, it
    MUST keep this contract.
    """
    log_path = integration_results_file(project_path, session_id)
    if not log_path.exists():
        return []
    results: list[dict[str, Any]] = []
    for line in log_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            results.append(payload)
    return results


def write_integration_failure_summary(project_path: Path, session_id: str, result: dict[str, Any]) -> str:
    """Render a human-readable failure summary at integration-failure-summary.md."""
    path = integration_failure_summary_file(project_path, session_id)
    failed = result.get("failed_required_command_names") or []
    lines = [
        f"# Integration Failure — {session_id}",
        "",
        f"At: {result.get('started_at')}",
        f"Duration: {result.get('duration_sec')}s",
        f"Failed required commands: {', '.join(failed) or 'unknown'}",
        "",
        "## Per-command outcomes",
        "",
    ]
    for cmd in result.get("commands_run") or []:
        state = "passed" if cmd.get("passed") else ("failed" if cmd.get("executed") else "skipped")
        lines.append(f"### `{cmd.get('name')}` — {state} (exit_code={cmd.get('exit_code')})")
        lines.append("")
        lines.append(f"command: `{cmd.get('cmd')}`")
        if cmd.get("stderr_tail"):
            lines.append("")
            lines.append("stderr (tail):")
            lines.append("")
            lines.append("```")
            lines.append(str(cmd.get("stderr_tail"))[-1500:])
            lines.append("```")
        lines.append("")
    lines.append("## Recommendation")
    lines.append("")
    lines.append("- Inspect the failed command above; the cumulative working tree from this session's commits is at the `agentic/autonomous/<session_id>` branch.")
    lines.append("- Once you have a fix in mind, either commit it manually on the session branch or create a corrective task (MVP-4C will automate this).")
    lines.append(f"- Resume the session with `agent-studio autonomous resume --project <project_id>` after committing the fix.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)


def commit_task(
    project_path: Path,
    *,
    task: dict[str, Any],
    run_id: str,
    selected_candidate: str,
    candidate_strategy: str,
    promotion_decision: str,
    promotion_report_relpath: str,
    corrective: bool = False,
    source_failure_id: str | None = None,
    human_review_id: str | None = None,
    human_review_decision: str | None = None,
    human_review_override: bool = False,
) -> str | None:
    """Commit the working tree changes for one task with evidence trailers.

    Returns the new commit short-hash, or None if there was nothing to commit.

    MVP-4C: when `corrective=True`, append `Corrective-Task: true` and
    `Source-Failure-ID: <id>` trailers so the git history records that this
    commit was a self-healing response to an integration check failure.

    MVP-4D: when `human_review_id` is set, append `Human-Review-ID`,
    `Human-Review-Decision`, and (if `human_review_override`)
    `Human-Review-Override: true` trailers so audit can distinguish
    Promotion-Gate-said-yes from a-human-said-yes-despite-the-gate.
    """
    # Stage everything outside `.agent/` (the runtime bookkeeping is tracked
    # separately; we don't want to pollute task commits with run artifacts).
    # Use `git add -A -- ':!.agent'` to exclude the agent dir.
    _git(project_path, "add", "-A", "--", ":!.agent", check=False)
    diff_cached = _git(project_path, "diff", "--cached", "--name-only", check=False).stdout.strip()
    if not diff_cached:
        return None
    body_lines = [
        task["title"],
        "",
        f"Agent-Task-ID: {task['id']}",
        f"Agent-Run-ID: {run_id}",
        f"Selected-Candidate: {selected_candidate}",
        f"Candidate-Strategy: {candidate_strategy}",
        f"Promotion-Decision: {promotion_decision}",
        f"Promotion-Report: {promotion_report_relpath}",
    ]
    if corrective:
        body_lines.append("Corrective-Task: true")
        if source_failure_id:
            body_lines.append(f"Source-Failure-ID: {source_failure_id}")
    if human_review_id:
        body_lines.append(f"Human-Review-ID: {human_review_id}")
        if human_review_decision:
            body_lines.append(f"Human-Review-Decision: {human_review_decision}")
        if human_review_override:
            body_lines.append("Human-Review-Override: true")
    # RC-4A.2: change-mode trailers. When the task carries a `change_id` (i.e.
    # was synthesized by `change_runner.build_change_task_graph`), emit
    # `Change-Id:` and (when available) `Source-Change-Request:` so the
    # commit's audit trail is self-explanatory: a future operator can grep
    # `git log --grep Change-Id` to find every commit a change session
    # produced. Normal autonomous-mode tasks set neither field, so this
    # block is a no-op for them.
    change_id_trailer = task.get("change_id")
    if change_id_trailer:
        body_lines.append(f"Change-Id: {change_id_trailer}")
        source_request = task.get("source_change_request")
        if source_request:
            body_lines.append(f"Source-Change-Request: {source_request}")
    body = "\n".join(body_lines) + "\n"
    _git(project_path, "-c", "commit.gpgsign=false", "commit", "-m", body, "--no-verify")
    head = _git(project_path, "rev-parse", "--short", "HEAD").stdout.strip()
    return head or None


# ---------------------------------------------------------------------------
# MVP-4C: integration-failure artifact + corrective task injection
# ---------------------------------------------------------------------------
SCHEMA_VERSION_INTEGRATION_FAILURE = 1
SCHEMA_VERSION_CORRECTIVE_TASK = 1
_INTEGRATION_FAILURE_TAIL_BYTES = 3000
_SUSPECTED_FILE_RE = re.compile(
    r"\b((?:apps|src|packages|tests|app|lib|orchestrator|workflows|scripts)/[A-Za-z0-9_./-]+\.[A-Za-z0-9]+)"
)


def integration_failures_dir(project_path: Path, session_id: str) -> Path:
    return session_dir(project_path, session_id) / "integration-failures"


def integration_failure_artifact_path(project_path: Path, session_id: str, failure_id: str) -> Path:
    return integration_failures_dir(project_path, session_id) / failure_id / "integration-failure.json"


def new_integration_failure_id() -> str:
    return short_id("integration_failure")


def detect_integration_failure_type(failed_command: dict[str, Any]) -> str:
    """Map a failed integration command to the broad failure-type categories
    MVP-4C uses for corrective tasks. Reuses FAILURE_TAXONOMY's keyword
    intuition without importing the full classifier (we want a smaller fixed
    label set: build_failure / type_error / unit_test_failure / e2e_failure /
    unknown — distinct from the inner-loop's 9-way taxonomy)."""
    text = "\n".join(
        str(part or "")
        for part in (
            failed_command.get("name"),
            failed_command.get("cmd"),
            failed_command.get("stdout_tail"),
            failed_command.get("stderr_tail"),
        )
    ).lower()
    name = str(failed_command.get("name") or "").lower()
    cmd = str(failed_command.get("cmd") or "").lower()
    if "type error" in text or "typescript" in text or "tsc" in text:
        return "type_error"
    if "playwright" in text or "e2e" in name or "e2e" in cmd:
        return "e2e_failure"
    if any(token in text for token in ("assert", "expected", "test failed", "jest", "vitest")):
        return "unit_test_failure"
    if "build" in name or "build" in cmd or "compile" in text:
        return "build_failure"
    return "unknown"


def extract_suspected_files(failed_command: dict[str, Any], cap: int = 10) -> list[str]:
    """Best-effort: regex-scan the command's stderr/stdout tails for paths
    that look like project source files. Deduped, capped at `cap`."""
    seen: list[str] = []
    haystacks = (failed_command.get("stderr_tail") or "", failed_command.get("stdout_tail") or "")
    for blob in haystacks:
        for match in _SUSPECTED_FILE_RE.finditer(str(blob)):
            path = match.group(1)
            if path not in seen:
                seen.append(path)
            if len(seen) >= cap:
                return seen
    return seen


def write_integration_failure_artifact(
    project_path: Path,
    *,
    session_id: str,
    project_id: str,
    trigger: str,
    after_task_id: str | None,
    after_commit: str | None,
    integration_result: dict[str, Any],
) -> dict[str, Any]:
    """Write the structured `integration-failure.json` artifact and return
    the dict that was written (so callers can immediately consume failure_id
    / detected_failure_type without re-reading from disk).

    Only the FIRST failed required command is recorded as `failed_command`.
    All commands run during this integration check are kept in `all_commands`
    so a human / future tooling can inspect non-required failures too.
    """
    commands_run = list(integration_result.get("commands_run") or [])
    failed_required = [
        c for c in commands_run
        if c.get("required") and c.get("executed") and not c.get("passed")
    ]
    primary = failed_required[0] if failed_required else None
    failed_command_payload: dict[str, Any]
    if primary is not None:
        failed_command_payload = {
            "name": primary.get("name"),
            "cmd": primary.get("cmd"),
            "exit_code": primary.get("exit_code"),
            "stdout_tail": str(primary.get("stdout_tail") or "")[-_INTEGRATION_FAILURE_TAIL_BYTES:],
            "stderr_tail": str(primary.get("stderr_tail") or "")[-_INTEGRATION_FAILURE_TAIL_BYTES:],
        }
    else:
        failed_command_payload = {"name": None, "cmd": None, "exit_code": None, "stdout_tail": "", "stderr_tail": ""}
    detected = detect_integration_failure_type(failed_command_payload)
    suspected = extract_suspected_files(failed_command_payload)
    failure_id = new_integration_failure_id()
    payload = {
        "schema_version": SCHEMA_VERSION_INTEGRATION_FAILURE,
        "failure_id": failure_id,
        "session_id": session_id,
        "project_id": project_id,
        "trigger": trigger,
        "after_task_id": after_task_id,
        "after_commit": after_commit,
        "failed_command": failed_command_payload,
        "all_commands": [
            {
                "name": c.get("name"),
                "cmd": c.get("cmd"),
                "required": bool(c.get("required")),
                "executed": bool(c.get("executed")),
                "exit_code": c.get("exit_code"),
                "passed": bool(c.get("passed")),
            }
            for c in commands_run
        ],
        "detected_failure_type": detected,
        "suspected_files": suspected,
        "created_at": now_iso(),
    }
    out_path = integration_failure_artifact_path(project_path, session_id, failure_id)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


def read_integration_failures(project_path: Path, session_id: str) -> list[dict[str, Any]]:
    """Read every recorded integration-failure.json under the session, oldest-first.
    Defensive against missing dir / corrupt files."""
    root = integration_failures_dir(project_path, session_id)
    if not root.is_dir():
        return []
    out: list[tuple[float, dict[str, Any]]] = []
    for sub in root.iterdir():
        if not sub.is_dir():
            continue
        path = sub / "integration-failure.json"
        if not path.exists():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                mtime = 0.0
            out.append((mtime, payload))
    out.sort(key=lambda item: item[0])
    return [payload for _, payload in out]


# Default scope paths used by corrective tasks when the project layout
# doesn't suggest anything more specific. Kept broad on purpose: a corrective
# task should be free to fix what's broken, not constrained to one feature.
_CORRECTIVE_DEFAULT_SCOPE: tuple[str, ...] = ("apps/web/**", "apps/**", "src/**", "packages/**", "tests/**")


def build_corrective_task(failure: dict[str, Any], *, sequence: int, project_path: Path | None = None) -> dict[str, Any]:
    """Build a bounded corrective task from an integration-failure payload.

    The resulting task:
      - has id `task-fix-integration-NNN` (stable, sequence-numbered)
      - intent explicitly references the failed command and the after_task
      - acceptance_criteria FIRST line is "The failed integration command passes: <cmd>"
      - dependencies = [after_task_id] when present (already completed by the
        time we get here, but recording it makes intent traceable)
      - source / source_failure_id / source_failed_command_name / source_after_task_id /
        source_failure_type carry the fingerprint used by duplicate detection
        and by the commit trailer
      - corrective: True
    """
    after = failure.get("after_task_id")
    failed = failure.get("failed_command") or {}
    failed_cmd_name = str(failed.get("name") or "unknown")
    failed_cmd_text = str(failed.get("cmd") or "<unknown>")
    detected = str(failure.get("detected_failure_type") or "unknown")
    if project_path is not None:
        scope = _detect_scope_paths(project_path)
    else:
        scope = list(_CORRECTIVE_DEFAULT_SCOPE)
    title = (
        f"Fix integration failure after {after}" if after else "Fix integration failure (session_end)"
    )
    intent = (
        f"The integration check failed after {after or 'session_end'} "
        f"because the required command `{failed_cmd_name}` failed "
        f"(detected_failure_type={detected}). "
        "Make the smallest change required to fix the failing command. "
        "Do not introduce new product behavior. "
        "Do not weaken assertions or remove tests."
    )
    acceptance = [
        f"The failed integration command passes: {failed_cmd_text}",
        "No unrelated product behavior is changed",
        "Previously completed task behavior remains intact",
    ]
    return {
        "schema_version": SCHEMA_VERSION_CORRECTIVE_TASK,
        "id": f"task-fix-integration-{sequence:03d}",
        "title": title,
        "intent": intent,
        "acceptance_criteria": acceptance,
        "scope_paths": scope,
        "dependencies": [after] if after else [],
        "status": "pending",
        "risk": "medium",
        "source": "integration_failure",
        "source_failure_id": failure.get("failure_id"),
        "source_failed_command_name": failed_cmd_name,
        "source_after_task_id": after,
        "source_failure_type": detected,
        "corrective": True,
        "run_ids": [],
        "commit": None,
    }


def has_pending_corrective_for_fingerprint(
    task_graph: dict[str, Any],
    *,
    failed_command_name: str,
    after_task_id: str | None,
    failure_type: str,
) -> bool:
    """Duplicate guard: True iff the task graph already has a pending or
    running corrective task with the same (cmd, after_task, failure_type)
    fingerprint. Prevents the controller from queuing two corrective tasks
    for the same observed problem when the same integration check fires
    repeatedly (e.g. ad-hoc + auto)."""
    for task in task_graph.get("tasks") or []:
        if not task.get("corrective"):
            continue
        if task.get("status") not in {"pending", "running"}:
            continue
        if str(task.get("source_failed_command_name") or "") != str(failed_command_name or ""):
            continue
        if task.get("source_after_task_id") != after_task_id:
            continue
        if str(task.get("source_failure_type") or "") != str(failure_type or ""):
            continue
        return True
    return False


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------
@dataclass
class StepOutcome:
    """Result of advancing one task."""
    task_id: str
    decision: str  # promote | repair | abandoned | needs-human-review | needs-more-context | error
    new_status: str  # completed | needs-human-review | abandoned | error
    commit: str | None = None
    inner_run_id: str | None = None
    error: str | None = None


class AutonomousController:
    """Drives a session through the task-graph, calling the agentic_project
    inner loop for each task. Hold no state; everything is on disk."""

    def __init__(
        self,
        project: dict[str, Any],
        *,
        run_inner_loop: Callable[..., Any],
        apply_candidate: Callable[..., Any] | None = None,
    ):
        """`run_inner_loop` and `apply_candidate` are injected so this class
        is testable without subprocess/db dependencies. Production wiring
        passes the real `AgenticProjectRuntime.run` and a small helper that
        invokes the same Apply Gate the CLI uses.
        """
        self.project = project
        self.project_path = Path(project["path"])
        self.run_inner_loop = run_inner_loop
        self.apply_candidate = apply_candidate

    # -- session lifecycle ------------------------------------------------
    def start_or_resume(self) -> AutonomousSession:
        existing = find_active_session(self.project_path)
        now = now_iso()
        if existing is not None and existing.status in {"running", "paused"}:
            existing.status = "running"
            existing.updated_at = now
            existing.halt_requested = False
            existing.pause_reason = None
            self._save_session(existing)
            self._log(existing.session_id, {"event": "session_resumed", "session_id": existing.session_id})
            return existing
        session_id = "session_" + short_id("auto").split("_", 1)[-1]
        branch = session_branch_name(session_id)
        session = AutonomousSession(
            schema_version=SCHEMA_VERSION_SESSION,
            session_id=session_id,
            project_id=str(self.project["id"]),
            status="running",
            current_task_id=None,
            branch=branch,
            started_at=now,
            updated_at=now,
        )
        # RC-2C: merge optional `autonomous.budgets` + `integration:` blocks
        # from agent-studio.yaml into the new session. Existing on-disk
        # sessions are NOT migrated (they take the early-return branch
        # above). Defensive: any load failure leaves the session with
        # DEFAULT_BUDGETS / DEFAULT_INTEGRATION_POLICY untouched.
        try:
            from orchestrator.core.deploy import load_autonomous_overrides
            overrides = load_autonomous_overrides(self.project_path)
            if overrides.budgets:
                session.budgets = {**session.budgets, **overrides.budgets}
                self._log(session_id, {
                    "event": "session_budgets_overridden",
                    "overrides": dict(overrides.budgets),
                })
            if overrides.integration:
                session.integration_policy = {
                    **session.integration_policy, **overrides.integration,
                }
                self._log(session_id, {
                    "event": "session_integration_policy_overridden",
                    "overrides": dict(overrides.integration),
                })
        except Exception:  # noqa: BLE001 — defensive
            pass
        session_dir(self.project_path, session_id).mkdir(parents=True, exist_ok=True)
        self._save_session(session)
        self._log(session_id, {"event": "session_started", "session_id": session_id, "branch": branch})
        return session

    def request_halt(self) -> AutonomousSession | None:
        session = find_active_session(self.project_path)
        if session is None:
            return None
        session.halt_requested = True
        # MVP-4A model: `autonomous halt` runs in a separate process from
        # `autonomous start`. The CLI's start command is a synchronous loop
        # that drives the controller forward; if start is currently
        # running, the controller checks halt_requested at the top of each
        # iteration and pauses there. If start is NOT running (the most
        # common case — user invokes halt while session is between
        # invocations), we mark paused immediately so the next `start`
        # explicitly resumes from a paused state.
        if session.status == "running":
            session.status = "paused"
            session.pause_reason = "halt_requested"
        session.updated_at = now_iso()
        self._save_session(session)
        self._log(session.session_id, {"event": "session_paused", "reason": "halt_requested"})
        return session

    # -- task picker ------------------------------------------------------
    def next_task(self, task_graph: dict[str, Any]) -> dict[str, Any] | None:
        """Return the next pending task whose dependencies are completed.
        Returns None when the graph is exhausted (or has no eligible task).

        MVP-4C scheduling priority:
          1. corrective tasks (`task.corrective == True`) come first so the
             session focuses on fixing the broken integration before
             advancing the original feature graph.
          2. then normal pending tasks, in declaration order.

        Both groups still respect dependency satisfaction.
        """
        completed_ids = {t["id"] for t in task_graph["tasks"] if t["status"] == "completed"}

        def _eligible(task: dict[str, Any]) -> bool:
            if task.get("status") != "pending":
                return False
            deps = task.get("dependencies") or []
            return all(dep in completed_ids for dep in deps)

        for task in task_graph["tasks"]:
            if task.get("corrective") and _eligible(task):
                return task
        for task in task_graph["tasks"]:
            if not task.get("corrective") and _eligible(task):
                return task
        return None

    # -- one-step driver --------------------------------------------------
    def advance_one_task(self, session: AutonomousSession, task_graph: dict[str, Any]) -> StepOutcome | None:
        """Run the next eligible task once. Returns None when there is no
        next eligible task (graph exhausted or all blocked)."""
        # Short-circuit: an already-completed/paused session must not re-run
        # integration or deploy hooks just because the caller looped one
        # extra time. This was the original semantic before MVP-4E unified
        # the next_task=None path through `_maybe_continue_or_complete`.
        if session.status in {"completed", "paused"}:
            return None
        if session.halt_requested:
            self._pause(session, reason="halt_requested")
            return None

        # RC-2C.1.2: peek at next_task BEFORE the budget check. If no
        # eligible task remains (every task already completed or
        # blocked), the right next step is finalization
        # (_maybe_continue_or_complete → integration → deploy → smoke
        # → _complete), NOT pausing on a budget that's only meaningful
        # WHILE we're still trying to advance new tasks.
        #
        # Pre-fix: a session that finished 3/3 tasks could pause with
        # `budget:max_tasks_per_session` on a subsequent
        # `autonomous resume`, overwriting any deploy/smoke pause
        # reason and confusing the operator (real bug surfaced in
        # RC-2C against session_b82c6d6a3c).
        task = self.next_task(task_graph)
        if task is None:
            # Unify with _maybe_continue_or_complete so the session-end
            # hooks (final integration + deploy) run on this path too.
            self._maybe_continue_or_complete(session, task_graph)
            return None
        budget_breach = self._check_budgets(session)
        if budget_breach:
            self._pause(session, reason=f"budget:{budget_breach}")
            return None

        session.current_task_id = task["id"]
        session.updated_at = now_iso()
        self._save_session(session)
        self._log(session.session_id, {
            "event": "task_started",
            "task_id": task["id"],
            "title": task["title"],
            "corrective": bool(task.get("corrective")),
        })
        if task.get("corrective"):
            self._log(session.session_id, {
                "event": "corrective_task_started",
                "task_id": task["id"],
                "source_failure_id": task.get("source_failure_id"),
                "source_failure_type": task.get("source_failure_type"),
            })

        # RC-2C.1.4: surface predecessor commits so the patch worker
        # knows what task-001 / task-002 already shipped and won't undo
        # them while implementing task-003. We pull from the live
        # task_graph (status=completed && commit set), excluding the
        # current task itself. Cap at 8 entries to keep the prompt
        # bounded.
        previous_completed: list[dict[str, Any]] = []
        for prior in task_graph.get("tasks") or []:
            if prior.get("id") == task.get("id"):
                continue
            if prior.get("status") != "completed":
                continue
            if not prior.get("commit"):
                continue
            previous_completed.append({
                "id": prior.get("id"),
                "title": prior.get("title"),
                "commit": prior.get("commit"),
                "run_id": (prior.get("run_ids") or [None])[-1],
            })
        previous_completed = previous_completed[-8:]

        intent_overrides = {
            "goal": task["intent"],
            "success_criteria": list(task.get("acceptance_criteria") or []),
            "previous_completed_tasks": previous_completed,
            "allowed_change_scope": {
                "paths": list(task.get("scope_paths") or []),
                "max_files": 24,
                "allow_dependency_changes": False,
            },
        }
        self._log(session.session_id, {"event": "inner_run_started", "task_id": task["id"]})
        try:
            result = self.run_inner_loop(
                project=self.project,
                intent_overrides=intent_overrides,
            )
        except Exception as exc:  # noqa: BLE001
            task["status"] = "needs-human-review"
            session.counters["needs_review_tasks"] += 1
            self._save_task_graph(task_graph)
            self._log(session.session_id, {
                "event": "inner_run_completed",
                "task_id": task["id"],
                "error": str(exc),
            })
            self._pause(session, reason=f"inner_run_exception:{type(exc).__name__}")
            return StepOutcome(task_id=task["id"], decision="error", new_status="needs-human-review", error=str(exc))

        session.counters["inner_runs"] += 1
        run_id = getattr(result, "run_id", None)
        decision = getattr(result, "decision", "")
        if run_id and run_id not in task["run_ids"]:
            task["run_ids"].append(run_id)
        self._log(session.session_id, {
            "event": "inner_run_completed",
            "task_id": task["id"],
            "run_id": run_id,
            "decision": decision,
        })

        if decision == "promote" and self.apply_candidate is not None:
            self._log(session.session_id, {
                "event": "candidate_selected",
                "task_id": task["id"],
                "candidate": getattr(result, "candidate", ""),
                "run_id": run_id,
            })
            try:
                applied_record = self.apply_candidate(
                    project_path=self.project_path,
                    run_dir=getattr(result, "run_dir", None),
                    selected_candidate=getattr(result, "candidate", ""),
                )
            except Exception as exc:  # noqa: BLE001
                task["status"] = "needs-human-review"
                session.counters["needs_review_tasks"] += 1
                self._save_task_graph(task_graph)
                self._log(session.session_id, {
                    "event": "candidate_applied",
                    "task_id": task["id"],
                    "error": str(exc),
                })
                self._emit_review_item(
                    session,
                    source_type="apply_failure",
                    reason_code="failed-apply",
                    title=f"Apply Gate refused selected candidate for task {task['id']}",
                    summary=(
                        f"Promotion Gate selected candidate `{getattr(result, 'candidate', '')}` for task `{task['id']}` "
                        f"but the Apply Gate refused with: {exc}. Inspect the candidate's patch.diff and "
                        "changed-files.json, fix the underlying gate failure (e.g. resolve a HEAD/base mismatch "
                        "or a dirty worktree), then either approve this review (which will retry the safe apply) "
                        "or resolve it after a manual fix."
                        + (f" (corrective task; source_failure_id={task.get('source_failure_id')})" if task.get("corrective") else "")
                    ),
                    task=task,
                    run_id=run_id,
                    candidate_id=getattr(result, "candidate", None) or None,
                    promotion_decision=decision,
                    source_failure_id=task.get("source_failure_id") if task.get("corrective") else None,
                    evidence_paths=self._candidate_evidence_paths(run_id, getattr(result, "candidate", None) or None),
                    suggested_commands=self._suggested_inspect_commands(
                        str(self.project["id"]), run_id, getattr(result, "candidate", None) or None,
                    ),
                )
                # RC-2A bug fix: pause BEFORE rendering so the final
                # report records the post-pause state (Status: paused +
                # the right pause_reason). Pre-fix the report showed
                # Status: running because we rendered before _pause.
                self._pause(session, reason=f"apply_failed:{type(exc).__name__}")
                self._update_final_status(session, task_graph)
                return StepOutcome(task_id=task["id"], decision="promote", new_status="needs-human-review", error=str(exc))

            self._log(session.session_id, {
                "event": "candidate_applied",
                "task_id": task["id"],
                "applied_record": applied_record,
            })
            is_corrective = bool(task.get("corrective"))
            commit_hash = commit_task(
                self.project_path,
                task=task,
                run_id=run_id or "",
                selected_candidate=getattr(result, "candidate", ""),
                candidate_strategy=str(applied_record.get("strategy") or ""),
                promotion_decision=decision,
                promotion_report_relpath=str((Path(".agent") / "runs" / (run_id or "") / "promotion-report.json").as_posix()),
                corrective=is_corrective,
                source_failure_id=task.get("source_failure_id") if is_corrective else None,
            )
            task["status"] = "completed"
            task["commit"] = commit_hash
            session.counters["completed_tasks"] += 1
            if is_corrective:
                session.counters["corrective_tasks_completed"] += 1
            self._save_task_graph(task_graph)
            self._log(session.session_id, {
                "event": "task_committed",
                "task_id": task["id"],
                "commit": commit_hash,
                "corrective": is_corrective,
            })
            if is_corrective:
                self._log(session.session_id, {
                    "event": "corrective_task_completed",
                    "task_id": task["id"],
                    "commit": commit_hash,
                    "source_failure_id": task.get("source_failure_id"),
                })
            self._update_final_status(session, task_graph)
            # MVP-4B + 4C: after a corrective task we re-run integration
            # IMMEDIATELY (don't wait for the periodic every-N trigger),
            # because the whole point of the corrective is to make integration
            # pass. After a normal task we keep the periodic policy.
            if is_corrective:
                integration_paused = self._run_integration(
                    session, reason="post_corrective", task_graph=task_graph,
                    after_task_id=task["id"], after_commit=commit_hash,
                )
            else:
                integration_paused = self._maybe_run_integration(
                    session, reason="periodic", task_graph=task_graph,
                    after_task_id=task["id"], after_commit=commit_hash,
                )
            if integration_paused:
                return StepOutcome(task_id=task["id"], decision=decision, new_status="completed", commit=commit_hash, inner_run_id=run_id)
            self._maybe_continue_or_complete(session, task_graph)
            return StepOutcome(task_id=task["id"], decision=decision, new_status="completed", commit=commit_hash, inner_run_id=run_id)

        if decision == "needs-human-review":
            task["status"] = "needs-human-review"
            session.counters["needs_review_tasks"] += 1
            self._save_task_graph(task_graph)
            self._log(session.session_id, {"event": "task_needs_human_review", "task_id": task["id"]})
            corrective_note = ""
            if task.get("corrective"):
                corrective_note = (
                    f" (corrective task; source_failure_id={task.get('source_failure_id')})"
                )
            self._emit_review_item(
                session,
                source_type="task_run",
                reason_code="needs-human-review",
                title=f"Task {task['id']} needs human review: {task['title']}",
                summary=(
                    f"The inner agentic_project run for task `{task['id']}` returned "
                    f"decision=needs-human-review{corrective_note}. Inspect the candidate, "
                    f"its critic findings, and the promotion-report to decide."
                ),
                task=task,
                run_id=run_id,
                candidate_id=getattr(result, "candidate", None) or None,
                promotion_decision=decision,
                source_failure_id=task.get("source_failure_id") if task.get("corrective") else None,
                evidence_paths=self._candidate_evidence_paths(run_id, getattr(result, "candidate", None) or None),
                suggested_commands=self._suggested_inspect_commands(
                    str(self.project["id"]), run_id, getattr(result, "candidate", None) or None,
                ),
            )
            # RC-2A bug fix: pause first so the report sees Status: paused.
            self._pause(session, reason="needs_human_review")
            self._update_final_status(session, task_graph)
            return StepOutcome(task_id=task["id"], decision=decision, new_status="needs-human-review", inner_run_id=run_id)

        if decision == "abandoned":
            task["status"] = "abandoned"
            session.counters["abandoned_tasks"] += 1
            self._save_task_graph(task_graph)
            self._log(session.session_id, {"event": "task_abandoned", "task_id": task["id"]})
            self._update_final_status(session, task_graph)
            # Keep going if under threshold; pause otherwise.
            self._maybe_continue_or_complete(session, task_graph)
            return StepOutcome(task_id=task["id"], decision=decision, new_status="abandoned", inner_run_id=run_id)

        # repair / needs-more-context / unknown — treat as needs-human-review,
        # but distinguish needs-more-context with its own review reason_code.
        task["status"] = "needs-human-review"
        session.counters["needs_review_tasks"] += 1
        self._save_task_graph(task_graph)
        self._log(session.session_id, {"event": "task_needs_human_review", "task_id": task["id"], "decision": decision})
        if decision == "needs-more-context":
            review_reason = "needs-more-context"
            review_summary = (
                f"The inner agentic_project run for task `{task['id']}` returned "
                "decision=needs-more-context: the context pack lacked source files / unknowns "
                "or the eval harness was not declared. Inspect context-pack.json and decide "
                "whether to widen the task's scope_paths or add the missing evidence by hand."
            )
            review_source_type = "needs_more_context"
        else:
            review_reason = "needs-human-review"
            review_summary = (
                f"The inner agentic_project run for task `{task['id']}` returned an unhandled "
                f"decision `{decision}`. Treated as needs-human-review."
            )
            review_source_type = "task_run"
        self._emit_review_item(
            session,
            source_type=review_source_type,
            reason_code=review_reason,
            title=f"Task {task['id']} {review_reason}: {task['title']}",
            summary=review_summary + (
                f" (corrective task; source_failure_id={task.get('source_failure_id')})"
                if task.get("corrective") else ""
            ),
            task=task,
            run_id=run_id,
            candidate_id=getattr(result, "candidate", None) or None,
            promotion_decision=decision,
            source_failure_id=task.get("source_failure_id") if task.get("corrective") else None,
            evidence_paths=self._candidate_evidence_paths(run_id, getattr(result, "candidate", None) or None) + (
                [str((Path(".agent") / "runs" / run_id / "context-pack.json").as_posix())] if run_id and decision == "needs-more-context" else []
            ),
            suggested_commands=self._suggested_inspect_commands(
                str(self.project["id"]), run_id, getattr(result, "candidate", None) or None,
            ),
        )
        # RC-2A bug fix: pause first so the report sees Status: paused.
        self._pause(session, reason=f"unhandled_decision:{decision}")
        self._update_final_status(session, task_graph)
        return StepOutcome(task_id=task["id"], decision=decision, new_status="needs-human-review", inner_run_id=run_id)

    # -- helpers ----------------------------------------------------------
    def _check_budgets(self, session: AutonomousSession) -> str | None:
        if session.counters["completed_tasks"] + session.counters["abandoned_tasks"] >= session.budgets["max_tasks_per_session"]:
            return "max_tasks_per_session"
        if session.counters["abandoned_tasks"] >= session.budgets["max_abandoned_tasks"]:
            return "max_abandoned_tasks"
        if session.counters["needs_review_tasks"] >= session.budgets["max_needs_human_review_tasks"]:
            return "max_needs_human_review_tasks"
        if session.counters["inner_runs"] >= session.budgets["max_total_inner_runs"]:
            return "max_total_inner_runs"
        return None

    def _maybe_run_integration(
        self,
        session: AutonomousSession,
        *,
        reason: str,
        task_graph: dict[str, Any] | None = None,
        after_task_id: str | None = None,
        after_commit: str | None = None,
    ) -> bool:
        """Run periodic integration if completed_tasks aligns with the
        configured trigger. Returns True iff integration ran AND the
        session ended up paused (e.g. corrective injection refused)."""
        every_n = int(session.integration_policy.get("every_n_tasks") or 0)
        if every_n <= 0:
            return False
        if session.counters["completed_tasks"] == 0:
            return False
        if session.counters["completed_tasks"] % every_n != 0:
            return False
        return self._run_integration(
            session, reason=reason, task_graph=task_graph,
            after_task_id=after_task_id, after_commit=after_commit,
        )

    def _run_integration(
        self,
        session: AutonomousSession,
        *,
        reason: str,
        task_graph: dict[str, Any] | None = None,
        after_task_id: str | None = None,
        after_commit: str | None = None,
    ) -> bool:
        """Execute one integration check.

        Returns True iff the session ended up paused as a result:
          - on PASS: returns False (always continues).
          - on FAIL: writes integration-failure.json, then tries to inject
            a corrective task. Returns False (and continues) if a corrective
            was injected OR a pending duplicate already exists. Returns True
            (and pauses) only when the corrective budget is exhausted.
        """
        commands = build_integration_commands(self.project_path)
        timeout = int(session.integration_policy.get("timeout_sec") or 600)
        self._log(session.session_id, {
            "event": "integration_started",
            "reason": reason,
            "command_count": len(commands),
        })
        result = run_integration_check(self.project_path, commands, timeout_sec=timeout)
        result["trigger_reason"] = reason
        result["completed_tasks_at_trigger"] = session.counters["completed_tasks"]
        record_integration_result(self.project_path, session.session_id, result)
        session.counters["integrations_run"] += 1
        session.last_integration_result = {
            "trigger_reason": reason,
            "passed": bool(result["passed"]),
            "failed_required_command_names": result.get("failed_required_command_names") or [],
            "started_at": result.get("started_at"),
            "duration_sec": result.get("duration_sec"),
        }
        if result["passed"]:
            session.counters["integrations_passed"] += 1
            self._log(session.session_id, {
                "event": "integration_passed",
                "duration_sec": result.get("duration_sec"),
                "command_count": len(commands),
            })
            self._save_session(session)
            return False

        session.counters["integrations_failed"] += 1
        # Always preserve the human-readable summary AND write the structured
        # MVP-4C artifact. The summary is the file a human reads when the
        # session pauses; the artifact is what corrective-task generation
        # consumes (and what future tooling will index).
        summary_path = write_integration_failure_summary(self.project_path, session.session_id, result)
        failure_artifact = write_integration_failure_artifact(
            self.project_path,
            session_id=session.session_id,
            project_id=session.project_id,
            trigger=reason,
            after_task_id=after_task_id,
            after_commit=after_commit,
            integration_result=result,
        )
        self._log(session.session_id, {
            "event": "integration_failed",
            "failure_id": failure_artifact["failure_id"],
            "detected_failure_type": failure_artifact["detected_failure_type"],
            "trigger": reason,
            "after_task_id": after_task_id,
            "summary_path": summary_path,
        })

        # MVP-4C: try corrective task injection instead of pausing immediately.
        if task_graph is None:
            task_graph = read_task_graph(self.project_path)
        paused = self._try_inject_corrective_or_pause(session, task_graph, failure_artifact)
        return paused

    def _try_inject_corrective_or_pause(
        self,
        session: AutonomousSession,
        task_graph: dict[str, Any],
        failure_artifact: dict[str, Any],
    ) -> bool:
        """MVP-4C heart: convert an integration failure into a bounded
        corrective task, or pause when guardrails refuse.

        Returns True iff the session was paused. Order matters:
          1. Duplicate check first — if an identical (cmd, after_task,
             failure_type) corrective is already pending/running, do NOT
             insert a duplicate and do NOT pause; the existing one will
             handle it.
          2. Budget check second — if injecting would push
             corrective_tasks_created over max_corrective_tasks, pause with
             reason `too-many-corrective-tasks`.
          3. Otherwise: build, append, save, log corrective_task_created.
        """
        failed = failure_artifact.get("failed_command") or {}
        fingerprint_name = str(failed.get("name") or "")
        fingerprint_after = failure_artifact.get("after_task_id")
        fingerprint_type = str(failure_artifact.get("detected_failure_type") or "")

        if has_pending_corrective_for_fingerprint(
            task_graph,
            failed_command_name=fingerprint_name,
            after_task_id=fingerprint_after,
            failure_type=fingerprint_type,
        ):
            self._log(session.session_id, {
                "event": "corrective_task_created",
                "skipped": True,
                "reason": "duplicate_pending_corrective",
                "source_failure_id": failure_artifact.get("failure_id"),
            })
            self._save_session(session)
            return False

        max_corrective = int(session.budgets.get("max_corrective_tasks", 3))
        if session.counters["corrective_tasks_created"] >= max_corrective:
            self._log(session.session_id, {
                "event": "corrective_task_limit_reached",
                "max_corrective_tasks": max_corrective,
                "source_failure_id": failure_artifact.get("failure_id"),
            })
            self._emit_review_item(
                session,
                source_type="corrective_limit",
                reason_code="too-many-corrective-tasks",
                title="Corrective task budget exhausted; integration still failing",
                summary=(
                    f"This session has already generated {max_corrective} corrective task(s) "
                    "and integration is still failing. Inspect the failure artifacts and the "
                    "session branch to decide whether to (a) widen the next corrective's scope "
                    "and resolve this review, (b) rebuild from a clean state, or (c) roll back."
                ),
                task=None,
                run_id=None,
                candidate_id=None,
                promotion_decision=None,
                source_failure_id=failure_artifact.get("failure_id"),
                evidence_paths=[
                    str((Path(".agent") / "autonomous" / "sessions" / session.session_id /
                         "integration-failures" / str(failure_artifact.get("failure_id") or "") / "integration-failure.json").as_posix()),
                    str((Path(".agent") / "autonomous" / "sessions" / session.session_id / "integration-failure-summary.md").as_posix()),
                    str((Path(".agent") / "autonomous" / "sessions" / session.session_id / "final-run-status.md").as_posix()),
                ],
                suggested_commands=[
                    f"agent-studio autonomous reviews list --project {session.project_id}",
                    f"agent-studio autonomous status --project {session.project_id}",
                ],
                allowed_actions=["show", "reject", "resolve"],
            )
            # RC-2A bug fix: pause first so the report sees Status: paused.
            self._pause(session, reason="too-many-corrective-tasks")
            self._update_final_status(session, task_graph)
            return True

        sequence = session.counters["corrective_tasks_created"] + 1
        corrective = build_corrective_task(failure_artifact, sequence=sequence, project_path=self.project_path)
        task_graph["tasks"].append(corrective)
        self._save_task_graph(task_graph)
        session.counters["corrective_tasks_created"] += 1
        self._save_session(session)
        self._log(session.session_id, {
            "event": "corrective_task_created",
            "task_id": corrective["id"],
            "source_failure_id": corrective["source_failure_id"],
            "source_failure_type": corrective["source_failure_type"],
            "after_task_id": fingerprint_after,
        })
        return False

    def _maybe_continue_or_complete(self, session: AutonomousSession, task_graph: dict[str, Any]) -> None:
        # If no more pending eligible tasks → run final integration (if
        # policy says so) and complete.
        if self.next_task(task_graph) is None:
            if bool(session.integration_policy.get("run_at_session_end")):
                # Only run final integration if there were any committed tasks
                # since the last integration; avoid no-op runs.
                last_at = (session.last_integration_result or {}).get("completed_tasks_at_trigger") if isinstance(session.last_integration_result, dict) else None
                # last_integration_result keeps a summary, not the trigger count;
                # fall back: always run final integration when run_at_session_end
                # is on AND at least one completed task exists since session start.
                if session.counters["completed_tasks"] > 0:
                    failed = self._run_integration(session, reason="session_end")
                    if failed:
                        # Re-render so the report includes the integration failure
                        # and any pause reason before returning.
                        self._update_final_status(session, task_graph)
                        return  # paused; do not mark complete
            # MVP-4E: deploy hook before _complete. Deploy never runs when
            # disabled by config; on failure pauses and creates a review item.
            deploy_paused = self._maybe_deploy_at_session_end(session, task_graph)
            # RC-1: always re-render the final report after deploy/smoke/rollback
            # complete. The earlier _update_final_status call (after the last
            # task commit) doesn't know about the deployment outcome yet.
            self._update_final_status(session, task_graph)
            if deploy_paused:
                return
            self._complete(session)
            # _complete flips status → "completed"; the final-status reflects that.
            self._update_final_status(session, task_graph)
        else:
            # check budget after this advance
            breach = self._check_budgets(session)
            if breach:
                self._pause(session, reason=f"budget:{breach}")

    def _maybe_deploy_at_session_end(self, session: AutonomousSession, task_graph: dict[str, Any]) -> bool:
        """Run the configured deploy adapter before declaring the session
        completed. Returns True iff the deploy attempt paused the session
        (caller must NOT call `_complete`)."""
        config = load_deploy_config(self.project_path)
        if not config.enabled:
            return False
        # Mark the session's deployment block "enabled" + "pending" so status
        # commands and JSON consumers can see this is a deploy-bearing run
        # before the adapter runs.
        session.deployment["enabled"] = True
        session.deployment["target"] = config.target
        session.deployment["status"] = "pending"
        self._save_session(session)
        outcome = self.run_deploy_now(session, task_graph=task_graph, source="session_end", config=config)
        return outcome.get("session_paused", False)

    def run_deploy_now(
        self,
        session: AutonomousSession,
        *,
        task_graph: dict[str, Any] | None = None,
        source: str = "manual",
        config: DeployConfig | None = None,
        deploy_runner: Callable[..., DeployResult] | None = None,
        smoke_runner: Callable[..., SmokeRunResult] | None = None,
        rollback_runner: Callable[..., RollbackResult] | None = None,
    ) -> dict[str, Any]:
        """Run one deploy attempt + persist deployment.json + update session
        + (on failure) emit a review item & pause.

        Returns a dict with keys:
          - status: "ready" | "failed" | "unknown" | "skipped"
          - deployment_id: str | None
          - deployment_url: str | None
          - deployment_artifact_path: str | None
          - failure: dict | None
          - session_paused: bool

        `deploy_runner` lets tests inject a deterministic adapter that does
        not call the real Vercel CLI.
        """
        config = config or load_deploy_config(self.project_path)
        if not config.enabled:
            return {
                "status": "skipped",
                "deployment_id": None,
                "deployment_url": None,
                "deployment_artifact_path": None,
                "failure": None,
                "session_paused": False,
            }
        if config.target != "vercel":
            # MVP-4E only supports Vercel; other targets land later (Fly.io
            # is MVP-5, Docker compose / SSH is MVP-6 per the roadmap).
            return {
                "status": "failed",
                "deployment_id": None,
                "deployment_url": None,
                "deployment_artifact_path": None,
                "failure": {
                    "failure_type": "unknown",
                    "message": f"deploy target `{config.target}` is not supported in MVP-4E (vercel only)",
                    "failed_command": None,
                },
                "session_paused": False,
            }

        deployment_id = new_deployment_id()
        session.deployment["status"] = "deploying"
        session.deployment["latest_deployment_id"] = deployment_id
        session.deployment["latest_deployment_url"] = None
        session.deployment["latest_failure_type"] = None
        self._save_session(session)
        self._log(session.session_id, {
            "event": "deployment_started",
            "deployment_id": deployment_id,
            "target": config.target,
            "environment": "production" if config.vercel.prod else config.environment,
            "source": source,
        })

        project_root = (self.project_path / config.project_path).resolve()
        runner_callable = deploy_runner or run_vercel_deploy
        try:
            result: DeployResult = runner_callable(config, project_root)
        except Exception as exc:  # noqa: BLE001 — defensive net for adapter bugs
            self._log(session.session_id, {
                "event": "deployment_failed",
                "deployment_id": deployment_id,
                "failure_type": "unknown",
                "error": str(exc),
            })
            session.deployment["status"] = "failed"
            session.deployment["latest_failure_type"] = "unknown"
            artifact_path = write_deployment_artifact(
                self.project_path,
                session_id=session.session_id, project_id=session.project_id,
                config=config, deployment_id=deployment_id,
                status="failed", deployment_url=None,
                started_at=now_iso(), completed_at=now_iso(),
                git_branch=self._git_short_branch(), git_commit=self._git_short_head(),
                sanitized_commands=[],
                failure={"failure_type": "unknown", "message": str(exc), "failed_command": None},
                source_session_status=session.status,
                final_run_status_relpath=str((Path(".agent") / "autonomous" / "sessions" / session.session_id / "final-run-status.md").as_posix()),
                task_graph_relpath="task-graph.json",
            )
            return self._record_deploy_failure(
                session, deployment_id=deployment_id, artifact_path=artifact_path,
                failure_type="unknown", failure_message=str(exc), source=source,
            )

        sanitized_commands = serialize_command_results(result.commands_run)
        for cmd in result.commands_run:
            self._log(session.session_id, {
                "event": "deployment_command_completed",
                "deployment_id": deployment_id,
                "name": cmd.name,
                "exit_code": cmd.exit_code,
            })
        artifact_path = write_deployment_artifact(
            self.project_path,
            session_id=session.session_id, project_id=session.project_id,
            config=config, deployment_id=deployment_id,
            status=result.status, deployment_url=result.deployment_url,
            started_at=result.started_at, completed_at=result.completed_at,
            git_branch=self._git_short_branch(), git_commit=self._git_short_head(),
            sanitized_commands=sanitized_commands,
            failure=result.failure,
            source_session_status=session.status,
            final_run_status_relpath=str((Path(".agent") / "autonomous" / "sessions" / session.session_id / "final-run-status.md").as_posix()),
            task_graph_relpath="task-graph.json",
        )

        session.deployment["latest_deployment_url"] = result.deployment_url
        if result.status == "ready":
            session.deployment["status"] = "deployed"
            session.deployment["latest_failure_type"] = None
            self._save_session(session)
            self._log(session.session_id, {
                "event": "deployment_succeeded",
                "deployment_id": deployment_id,
                "deployment_url": result.deployment_url,
            })
            # MVP-4F: deploy succeeded → smoke check (if enabled) → maybe
            # rollback. The smoke helper handles status transitions
            # (deployed → verified | smoke-failed | rolled-back |
            # rollback-failed) and emits review items + pauses on failure.
            smoke_outcome = self._maybe_run_smoke_check_after_deploy(
                session,
                deployment_id=deployment_id,
                deployment_artifact_path=artifact_path,
                config=config,
                deployment_url=result.deployment_url,
                source=source,
                smoke_runner=smoke_runner,
                rollback_runner=rollback_runner,
            )
            base = {
                "deployment_id": deployment_id,
                "deployment_url": result.deployment_url,
                "deployment_artifact_path": str(artifact_path),
                "failure": None,
            }
            base.update(smoke_outcome)
            # `status` is ALWAYS set by _maybe_run_smoke_check_after_deploy:
            #   ready (smoke disabled or passed), smoke-failed, rolled-back,
            #   rollback-failed. session_paused is set by the same helper.
            return base

        # Failure path (status in {failed, unknown}).
        failure_type = (result.failure or {}).get("failure_type") or "unknown"
        failure_message = (result.failure or {}).get("message") or "deploy failed"
        return self._record_deploy_failure(
            session, deployment_id=deployment_id, artifact_path=artifact_path,
            failure_type=failure_type, failure_message=failure_message, source=source,
        )

    # ------------------------------------------------------------------
    # MVP-4F: smoke check after deploy + maybe rollback
    # ------------------------------------------------------------------
    def _maybe_run_smoke_check_after_deploy(
        self,
        session: AutonomousSession,
        *,
        deployment_id: str,
        deployment_artifact_path: Path,
        config: DeployConfig,
        deployment_url: str | None,
        source: str,
        smoke_runner: Callable[..., SmokeRunResult] | None = None,
        rollback_runner: Callable[..., RollbackResult] | None = None,
    ) -> dict[str, Any]:
        """Execute (or skip) smoke checks against a fresh deployment.

        Always returns a dict with at least:
          - status: "ready" (skipped or passed) | "smoke-failed" | "rolled-back" | "rollback-failed"
          - smoke_check_id: str | None
          - smoke_review_id: str | None
          - rollback_id: str | None
          - rollback_status: str | None
          - rollback_review_id: str | None
          - session_paused: bool

        Pause semantics: only when source == "session_end" do we actually
        pause the session. Manual deploys leave session.status alone.
        """
        if not config.smoke_checks.enabled:
            session.deployment["latest_smoke_check_id"] = None
            session.deployment["latest_smoke_status"] = "skipped"
            session.deployment["latest_smoke_failure_type"] = None
            self._save_session(session)
            return {
                "status": "ready",
                "smoke_check_id": None,
                "smoke_review_id": None,
                "rollback_id": None,
                "rollback_status": None,
                "rollback_review_id": None,
                "session_paused": False,
            }

        environment = "production" if config.vercel.prod else config.environment
        self._log(session.session_id, {
            "event": "smoke_check_started",
            "deployment_id": deployment_id,
            "check_count": len(config.smoke_checks.checks),
            "environment": environment,
        })
        smoke_call = smoke_runner or run_smoke_checks
        smoke_result: SmokeRunResult = smoke_call(config.smoke_checks, deployment_url)
        smoke_check_id, smoke_artifact_path = persist_smoke_run(
            self.project_path,
            session_id=session.session_id, project_id=session.project_id,
            deployment_id=deployment_id, deployment_url=deployment_url,
            environment=environment, result=smoke_result,
        )
        session.deployment["latest_smoke_check_id"] = smoke_check_id
        session.deployment["latest_smoke_status"] = smoke_result.status

        if smoke_result.status == "passed":
            session.deployment["status"] = "verified"
            session.deployment["latest_smoke_failure_type"] = None
            self._save_session(session)
            self._log(session.session_id, {
                "event": "smoke_check_completed",
                "smoke_check_id": smoke_check_id,
                "status": "passed",
                "check_count": len(smoke_result.checks),
            })
            return {
                "status": "ready",
                "smoke_check_id": smoke_check_id,
                "smoke_review_id": None,
                "rollback_id": None,
                "rollback_status": None,
                "rollback_review_id": None,
                "session_paused": False,
            }

        # smoke failed
        smoke_failure_type = (smoke_result.failure or {}).get("failure_type") or "unknown"
        smoke_failed_check = (smoke_result.failure or {}).get("failed_check") or "unknown"
        session.deployment["status"] = "smoke-failed"
        session.deployment["latest_smoke_failure_type"] = smoke_failure_type
        self._save_session(session)
        self._log(session.session_id, {
            "event": "smoke_check_failed",
            "smoke_check_id": smoke_check_id,
            "failure_type": smoke_failure_type,
            "failed_check": smoke_failed_check,
        })

        # Always emit a smoke-failure review item — user must explicitly
        # acknowledge before resume, regardless of whether rollback runs.
        smoke_artifact_relpath = str(smoke_artifact_path.relative_to(self.project_path).as_posix())
        deployment_artifact_relpath = str(deployment_artifact_path.relative_to(self.project_path).as_posix())
        smoke_review = self._emit_review_item(
            session,
            source_type="smoke_check_failure",
            reason_code="smoke-check-failed",
            title=f"Smoke check failed: {smoke_failure_type} on `{smoke_failed_check}`",
            summary=(
                f"The deployment at {deployment_url} responded but smoke check `{smoke_failed_check}` "
                f"failed with `{smoke_failure_type}`. The deployment artifact and the smoke-check "
                f"artifact carry the failed check's URL, expected vs actual status, and a 3KB body tail. "
                f"Resolve this review only after you've confirmed the deployment is intentionally fine "
                f"(or after a manual rollback)."
            ),
            task=None, run_id=None, candidate_id=None, promotion_decision=None,
            evidence_paths=[
                deployment_artifact_relpath,
                smoke_artifact_relpath,
                str((Path(".agent") / "autonomous" / "sessions" / session.session_id / "final-run-status.md").as_posix()),
                str((Path(".agent") / "autonomous" / "sessions" / session.session_id / "controller-log.jsonl").as_posix()),
            ],
            suggested_commands=[
                f"agent-studio autonomous status --project {session.project_id}",
                f"agent-studio autonomous reviews show <review_id> --project {session.project_id}",
                f"agent-studio autonomous deploy --project {session.project_id} --dry-run",
                f"agent-studio autonomous reviews resolve <review_id> --note '...' --project {session.project_id}",
            ],
            allowed_actions=["show", "reject", "resolve"],
        )
        self._log(session.session_id, {
            "event": "smoke_review_item_created",
            "review_id": smoke_review.review_id,
            "smoke_check_id": smoke_check_id,
        })

        # Optional: rollback. Rollback only runs when explicitly enabled,
        # `trigger_on_smoke_failure=True`, and (when production_only=True)
        # environment == "production". Anything else → no rollback, just
        # the smoke review item + a pause.
        rollback_outcome = self._maybe_run_rollback_after_smoke_failure(
            session,
            deployment_id=deployment_id,
            smoke_check_id=smoke_check_id,
            config=config,
            deployment_url=deployment_url,
            environment=environment,
            rollback_runner=rollback_runner,
        )

        if source == "session_end":
            if rollback_outcome["status"] == "completed":
                self._pause(session, reason="smoke-check-failed-rolled-back")
            elif rollback_outcome["status"] == "failed":
                self._pause(session, reason="rollback-failed")
            else:
                self._pause(session, reason="smoke-check-failed")

        # Return-status mapping: rolled-back wins over smoke-failed when
        # rollback completed; rollback-failed when the rollback itself failed.
        if rollback_outcome["status"] == "completed":
            ret_status = "rolled-back"
        elif rollback_outcome["status"] == "failed":
            ret_status = "rollback-failed"
        else:
            ret_status = "smoke-failed"

        return {
            "status": ret_status,
            "smoke_check_id": smoke_check_id,
            "smoke_review_id": smoke_review.review_id,
            "rollback_id": rollback_outcome.get("rollback_id"),
            "rollback_status": rollback_outcome.get("status"),
            "rollback_review_id": rollback_outcome.get("review_id"),
            "session_paused": session.status == "paused",
        }

    def _maybe_run_rollback_after_smoke_failure(
        self,
        session: AutonomousSession,
        *,
        deployment_id: str,
        smoke_check_id: str,
        config: DeployConfig,
        deployment_url: str | None,
        environment: str,
        rollback_runner: Callable[..., RollbackResult] | None = None,
    ) -> dict[str, Any]:
        """Decide whether to roll back, then run the adapter and write the
        rollback artifact. Returns dict with status ∈ {not-run, skipped,
        completed, failed} + rollback_id + review_id (if rollback_failure)."""
        if not config.rollback.enabled or not config.rollback.trigger_on_smoke_failure:
            return {"status": "not-run", "rollback_id": None, "review_id": None}

        # Production-only safety check. We still write a "skipped" rollback
        # artifact so the operator can see we considered + declined.
        if config.rollback.production_only and environment != "production":
            rollback_id = new_rollback_id()
            now = now_iso()
            artifact = write_rollback_artifact(
                self.project_path,
                session_id=session.session_id, project_id=session.project_id,
                rollback_id=rollback_id,
                deployment_id=deployment_id, smoke_check_id=smoke_check_id,
                target=config.target, environment=environment,
                status="skipped", started_at=now, completed_at=now,
                trigger="smoke-check-failed",
                sanitized_commands=[],
                failure={
                    "failure_type": "rollback_not_allowed",
                    "message": f"environment={environment} but rollback.production_only=True",
                    "failed_command": None,
                },
            )
            session.deployment["latest_rollback_id"] = rollback_id
            session.deployment["latest_rollback_status"] = "skipped"
            session.deployment["latest_rollback_failure_type"] = "rollback_not_allowed"
            self._save_session(session)
            self._log(session.session_id, {
                "event": "rollback_skipped",
                "rollback_id": rollback_id,
                "reason": "rollback_not_allowed",
                "environment": environment,
            })
            return {"status": "skipped", "rollback_id": rollback_id, "review_id": None}

        # Run rollback.
        rollback_id = new_rollback_id()
        project_root = (self.project_path / config.project_path).resolve()
        self._log(session.session_id, {
            "event": "rollback_started",
            "rollback_id": rollback_id,
            "deployment_id": deployment_id,
        })
        rollback_call = rollback_runner or run_vercel_rollback
        try:
            rollback_result: RollbackResult = rollback_call(config, project_root)
        except Exception as exc:  # noqa: BLE001
            now = now_iso()
            artifact = write_rollback_artifact(
                self.project_path,
                session_id=session.session_id, project_id=session.project_id,
                rollback_id=rollback_id,
                deployment_id=deployment_id, smoke_check_id=smoke_check_id,
                target=config.target, environment=environment,
                status="failed", started_at=now, completed_at=now,
                trigger="smoke-check-failed",
                sanitized_commands=[],
                failure={"failure_type": "unknown", "message": str(exc), "failed_command": None},
            )
            session.deployment["latest_rollback_id"] = rollback_id
            session.deployment["latest_rollback_status"] = "failed"
            session.deployment["latest_rollback_failure_type"] = "unknown"
            self._save_session(session)
            self._log(session.session_id, {
                "event": "rollback_failed", "rollback_id": rollback_id,
                "failure_type": "unknown", "error": str(exc),
            })
            review = self._emit_rollback_review(session, rollback_id, artifact, "unknown", str(exc), deployment_id, smoke_check_id)
            return {"status": "failed", "rollback_id": rollback_id, "review_id": review.review_id}

        sanitized_commands = serialize_command_results(rollback_result.commands_run)
        artifact_path = write_rollback_artifact(
            self.project_path,
            session_id=session.session_id, project_id=session.project_id,
            rollback_id=rollback_id,
            deployment_id=deployment_id, smoke_check_id=smoke_check_id,
            target=config.target, environment=environment,
            status=rollback_result.status,
            started_at=rollback_result.started_at, completed_at=rollback_result.completed_at,
            trigger="smoke-check-failed",
            sanitized_commands=sanitized_commands,
            failure=rollback_result.failure,
        )
        session.deployment["latest_rollback_id"] = rollback_id
        session.deployment["latest_rollback_status"] = rollback_result.status

        if rollback_result.status == "completed":
            session.deployment["status"] = "rolled-back"
            session.deployment["latest_rollback_failure_type"] = None
            self._save_session(session)
            self._log(session.session_id, {
                "event": "rollback_completed",
                "rollback_id": rollback_id,
            })
            return {"status": "completed", "rollback_id": rollback_id, "review_id": None}

        # Rollback failed.
        failure_type = (rollback_result.failure or {}).get("failure_type") or "unknown"
        failure_message = (rollback_result.failure or {}).get("message") or "rollback failed"
        session.deployment["status"] = "rollback-failed"
        session.deployment["latest_rollback_failure_type"] = failure_type
        self._save_session(session)
        self._log(session.session_id, {
            "event": "rollback_failed",
            "rollback_id": rollback_id,
            "failure_type": failure_type,
        })
        review = self._emit_rollback_review(session, rollback_id, artifact_path, failure_type, failure_message, deployment_id, smoke_check_id)
        return {"status": "failed", "rollback_id": rollback_id, "review_id": review.review_id}

    def _emit_rollback_review(
        self,
        session: AutonomousSession,
        rollback_id: str,
        artifact_path: Path,
        failure_type: str,
        failure_message: str,
        deployment_id: str,
        smoke_check_id: str,
    ) -> ReviewItem:
        review = self._emit_review_item(
            session,
            source_type="rollback_failure",
            reason_code="rollback-failed",
            title=f"Vercel rollback failed: {failure_type}",
            summary=(
                f"After smoke checks failed, the controller attempted to roll back the production "
                f"deployment but the rollback itself failed with `{failure_type}`. The production URL "
                f"may still be serving the bad deployment. Inspect the rollback artifact, run a manual "
                f"`agent-studio autonomous rollback --yes` after fixing the underlying issue, then "
                f"resolve this review. Message tail: {failure_message[:500]}"
            ),
            task=None, run_id=None, candidate_id=None, promotion_decision=None,
            evidence_paths=[
                str(artifact_path.relative_to(self.project_path).as_posix()),
                str((Path(".agent") / "autonomous" / "sessions" / session.session_id / "final-run-status.md").as_posix()),
                str((Path(".agent") / "autonomous" / "sessions" / session.session_id / "controller-log.jsonl").as_posix()),
            ],
            suggested_commands=[
                f"agent-studio autonomous rollback --project {session.project_id} --dry-run",
                f"agent-studio autonomous rollback --project {session.project_id} --yes",
                f"agent-studio autonomous reviews show <review_id> --project {session.project_id}",
                f"agent-studio autonomous reviews resolve <review_id> --note '...' --project {session.project_id}",
            ],
            allowed_actions=["show", "reject", "resolve"],
        )
        self._log(session.session_id, {
            "event": "rollback_review_item_created",
            "review_id": review.review_id,
            "rollback_id": rollback_id,
        })
        return review

    def _record_deploy_failure(
        self,
        session: AutonomousSession,
        *,
        deployment_id: str,
        artifact_path: Path,
        failure_type: str,
        failure_message: str,
        source: str,
    ) -> dict[str, Any]:
        session.deployment["status"] = "failed"
        session.deployment["latest_failure_type"] = failure_type
        self._save_session(session)
        self._log(session.session_id, {
            "event": "deployment_failed",
            "deployment_id": deployment_id,
            "failure_type": failure_type,
        })
        review = self._emit_review_item(
            session,
            source_type="deployment_failure",
            reason_code="deployment-failed",
            title=f"Vercel deploy failed: {failure_type}",
            summary=(
                f"The deploy adapter (target=vercel) failed with `{failure_type}`. "
                f"Inspect deployment.json for sanitized command output, then either "
                f"resolve this review (after fixing the underlying problem and rerunning "
                f"`agent-studio autonomous deploy --yes`) or reject the review to halt this session. "
                f"Message tail: {failure_message[:500]}"
            ),
            task=None, run_id=None, candidate_id=None, promotion_decision=None,
            evidence_paths=[
                str(artifact_path.relative_to(self.project_path).as_posix()),
                str((Path(".agent") / "autonomous" / "sessions" / session.session_id / "final-run-status.md").as_posix()),
                str((Path(".agent") / "autonomous" / "sessions" / session.session_id / "controller-log.jsonl").as_posix()),
            ],
            suggested_commands=[
                f"agent-studio autonomous deploy --project {session.project_id} --dry-run",
                f"agent-studio autonomous reviews show <review_id> --project {session.project_id}",
                f"agent-studio autonomous reviews resolve <review_id> --note '...' --project {session.project_id}",
            ],
            allowed_actions=["show", "reject", "resolve"],
        )
        self._log(session.session_id, {
            "event": "deployment_review_item_created",
            "deployment_id": deployment_id,
            "review_id": review.review_id,
        })
        # Pause only when triggered from session_end. Manual deploy
        # invocations don't pause an already-completed session — the user
        # called `deploy` knowing it might fail.
        if source == "session_end":
            self._pause(session, reason="deployment-failed")
            return {
                "status": "failed",
                "deployment_id": deployment_id,
                "deployment_url": session.deployment["latest_deployment_url"],
                "deployment_artifact_path": str(artifact_path),
                "failure": {"failure_type": failure_type, "message": failure_message},
                "session_paused": True,
                "review_id": review.review_id,
            }
        return {
            "status": "failed",
            "deployment_id": deployment_id,
            "deployment_url": session.deployment["latest_deployment_url"],
            "deployment_artifact_path": str(artifact_path),
            "failure": {"failure_type": failure_type, "message": failure_message},
            "session_paused": False,
            "review_id": review.review_id,
        }

    def _git_short_head(self) -> str | None:
        try:
            res = _git(self.project_path, "rev-parse", "--short", "HEAD", check=False)
            return res.stdout.strip() or None
        except FileNotFoundError:
            return None

    def _git_short_branch(self) -> str | None:
        try:
            res = _git(self.project_path, "rev-parse", "--abbrev-ref", "HEAD", check=False)
            return res.stdout.strip() or None
        except FileNotFoundError:
            return None

    def _pause(self, session: AutonomousSession, *, reason: str) -> None:
        session.status = "paused"
        session.pause_reason = reason
        session.updated_at = now_iso()
        self._save_session(session)
        self._log(session.session_id, {"event": "session_paused", "reason": reason})

    # ------------------------------------------------------------------
    # MVP-4D: Human Review Queue creation
    # ------------------------------------------------------------------
    def _emit_review_item(
        self,
        session: AutonomousSession,
        *,
        source_type: str,
        reason_code: str,
        title: str,
        summary: str,
        severity: str = "blocking",
        task: dict[str, Any] | None = None,
        run_id: str | None = None,
        candidate_id: str | None = None,
        promotion_decision: str | None = None,
        source_failure_id: str | None = None,
        evidence_paths: list[str] | None = None,
        suggested_commands: list[str] | None = None,
        allowed_actions: list[str] | None = None,
    ) -> ReviewItem:
        """Build + persist a review item, then log `review_item_created`.

        Centralized so every pause-with-human-decision path writes through
        the same code: easier to audit, easier to extend (e.g. severity
        rules) without scattering review-construction logic across the
        controller.
        """
        review = ReviewItem(
            schema_version=SCHEMA_VERSION_REVIEW_ITEM,
            review_id=new_review_id(),
            session_id=session.session_id,
            project_id=session.project_id,
            status="open",
            severity=severity,
            source_type=source_type,
            reason_code=reason_code,
            title=title,
            summary=summary,
            task_id=(task or {}).get("id") if task else None,
            run_id=run_id,
            candidate_id=candidate_id,
            promotion_decision=promotion_decision,
            source_failure_id=source_failure_id,
            evidence_paths=list(evidence_paths or []),
            suggested_commands=list(suggested_commands or []),
            allowed_actions=list(allowed_actions or DEFAULT_ALLOWED_ACTIONS),
        )
        create_review_item(self.project_path, review)
        self._log(session.session_id, {
            "event": "review_item_created",
            "review_id": review.review_id,
            "reason_code": review.reason_code,
            "source_type": review.source_type,
            "severity": review.severity,
            "task_id": review.task_id,
            "run_id": review.run_id,
        })
        return review

    @staticmethod
    def _candidate_evidence_paths(run_id: str | None, candidate_id: str | None) -> list[str]:
        if not run_id:
            return []
        paths = [str((Path(".agent") / "runs" / run_id / "promotion-report.json").as_posix())]
        if candidate_id:
            cdir = Path(".agent") / "runs" / run_id / "candidates" / candidate_id
            paths.extend([
                str((cdir / "patch.diff").as_posix()),
                str((cdir / "score.json").as_posix()),
                str((cdir / "changed-files.json").as_posix()),
                str((cdir / "critics" / "security.md").as_posix()),
                str((cdir / "critics" / "correctness.md").as_posix()),
            ])
        return paths

    @staticmethod
    def _suggested_inspect_commands(project_id: str, run_id: str | None, candidate_id: str | None) -> list[str]:
        commands: list[str] = []
        if run_id:
            commands.append(f"agent-studio agentic-runs show --project {project_id} --run {run_id}")
            if candidate_id:
                commands.append(
                    f"agent-studio agentic-candidates show --project {project_id} --run {run_id} --candidate {candidate_id}"
                )
        return commands

    def _complete(self, session: AutonomousSession) -> None:
        session.status = "completed"
        session.pause_reason = None
        session.current_task_id = None
        session.updated_at = now_iso()
        self._save_session(session)
        self._log(session.session_id, {"event": "session_completed"})

    def _save_session(self, session: AutonomousSession) -> None:
        path = session_file(self.project_path, session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(session.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _save_task_graph(self, task_graph: dict[str, Any]) -> None:
        path = task_graph_file(self.project_path)
        path.write_text(json.dumps(task_graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def _log(self, session_id: str, event: dict[str, Any]) -> None:
        event = {"ts": now_iso(), **event}
        path = controller_log_file(self.project_path, session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _update_final_status(self, session: AutonomousSession, task_graph: dict[str, Any]) -> None:
        path = final_run_status_file(self.project_path, session.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        completed = [t for t in task_graph["tasks"] if t["status"] == "completed"]
        review = [t for t in task_graph["tasks"] if t["status"] == "needs-human-review"]
        abandoned = [t for t in task_graph["tasks"] if t["status"] == "abandoned"]
        pending = [t for t in task_graph["tasks"] if t["status"] == "pending"]
        # RC-2B.12: surface patch_worker config so a reader of the report
        # alone can tell whether the controller produced any source diffs
        # or was running with the safe `none` default.
        try:
            from orchestrator.core.deploy import load_agentic_config
            patch_worker_label = load_agentic_config(self.project_path).patch_worker
        except Exception:  # noqa: BLE001
            patch_worker_label = "unknown"
        lines = [
            f"# Final Run Status — {session.session_id}",
            "",
            "## Summary",
            f"- Project: {task_graph.get('project_title') or self.project.get('name')}",
            f"- Branch: {session.branch}",
            f"- Status: {session.status}",
            f"- Pause reason: {session.pause_reason or 'n/a'}",
            f"- Patch worker: {patch_worker_label}",
            (
                f"- Counters: completed={session.counters['completed_tasks']}, "
                f"abandoned={session.counters['abandoned_tasks']}, "
                f"needs_review={session.counters['needs_review_tasks']}, "
                f"inner_runs={session.counters['inner_runs']}, "
                f"corrective_created={session.counters.get('corrective_tasks_created', 0)}, "
                f"corrective_completed={session.counters.get('corrective_tasks_completed', 0)}"
            ),
            "",
            f"## Tasks ({len(task_graph['tasks'])} total)",
            "",
            f"### Completed ({len(completed)})",
        ]
        for t in completed:
            commit_hint = f" ({t.get('commit')})" if t.get("commit") else ""
            corrective_hint = "  [corrective]" if t.get("corrective") else ""
            lines.append(f"- {t['id']} — {t['title']}{commit_hint}{corrective_hint}")
        lines += ["", f"### Needs human review ({len(review)})"]
        for t in review:
            corrective_hint = "  [corrective — session blocked by this corrective task]" if t.get("corrective") else ""
            lines.append(f"- {t['id']} — {t['title']}{corrective_hint}")
        lines += ["", f"### Abandoned ({len(abandoned)})"]
        for t in abandoned:
            corrective_hint = "  [corrective]" if t.get("corrective") else ""
            lines.append(f"- {t['id']} — {t['title']}{corrective_hint}")
        lines += ["", f"### Pending ({len(pending)})"]
        for t in pending:
            corrective_hint = "  [corrective — will run next]" if t.get("corrective") else ""
            lines.append(f"- {t['id']} — {t['title']}{corrective_hint}")

        # MVP-4B/4F: Integration section — surfaces last-run status + how
        # many integration runs total (failures get their own section below).
        all_integration_runs = read_integration_results(self.project_path, session.session_id)
        last_integration = all_integration_runs[-1] if all_integration_runs else None
        lines += ["", "## Integration"]
        if last_integration is None:
            lines.append("- status: not-run")
            lines.append("- runs: 0")
        else:
            last_status = "passed" if last_integration.get("passed") else "failed"
            lines.append(f"- last status: {last_status}")
            lines.append(f"- runs: {len(all_integration_runs)}")
            failed_names = last_integration.get("failed_required_command_names") or []
            if failed_names:
                lines.append(f"- last failed required commands: {', '.join(failed_names)}")

        # MVP-4C: dedicated corrective tasks + integration failures sections.
        corrective_tasks = [t for t in task_graph["tasks"] if t.get("corrective")]
        lines += ["", f"## Corrective Tasks ({len(corrective_tasks)})"]
        if not corrective_tasks:
            lines.append("- (none)")
        for t in corrective_tasks:
            lines.append(
                f"- {t['id']} — {t['title']} "
                f"[status={t['status']}, source_failure_id={t.get('source_failure_id')}, "
                f"failure_type={t.get('source_failure_type')}]"
            )
        failures = read_integration_failures(self.project_path, session.session_id)
        lines += ["", f"## Integration Failures ({len(failures)})"]
        if not failures:
            lines.append("- (none)")
        for f in failures:
            cmd_name = (f.get("failed_command") or {}).get("name") or "unknown"
            lines.append(
                f"- {f.get('failure_id')} trigger={f.get('trigger')} "
                f"after_task={f.get('after_task_id') or 'n/a'} "
                f"failed_command={cmd_name} "
                f"detected_failure_type={f.get('detected_failure_type')} "
                f"created_at={f.get('created_at')}"
            )

        # MVP-4D: Human Review Queue section.
        reviews = list_review_items(self.project_path, session.session_id)
        open_items = [r for r in reviews if r.status == "open"]
        approved = [r for r in reviews if r.status == "approved"]
        rejected = [r for r in reviews if r.status == "rejected"]
        resolved = [r for r in reviews if r.status == "resolved"]
        lines += ["", f"## Human Review Queue ({len(reviews)} total, {len(open_items)} open)"]

        def _render_review_block(label: str, items: list[Any]) -> None:
            lines.append("")
            lines.append(f"### {label} ({len(items)})")
            if not items:
                lines.append("- (none)")
                return
            for r in items:
                task_hint = f" (task={r.task_id})" if r.task_id else ""
                lines.append(
                    f"- {r.review_id} [{r.reason_code}] severity={r.severity}{task_hint} — {r.title}"
                )
                if r.suggested_commands:
                    lines.append(f"    next: `{r.suggested_commands[0]}`")
                if r.resolution:
                    res = r.resolution
                    lines.append(
                        f"    resolution: {res.get('decision') or res.get('action') or 'recorded'}"
                        + (f" — {res.get('reason') or res.get('note') or ''}" if (res.get('reason') or res.get('note')) else "")
                    )

        _render_review_block("Open", open_items)
        _render_review_block("Approved", approved)
        _render_review_block("Rejected", rejected)
        _render_review_block("Resolved", resolved)

        # MVP-4E: Deployment section
        deployment_state = session.deployment if isinstance(session.deployment, dict) else {}
        all_deployments = list_deployments(self.project_path, session.session_id)
        lines += ["", "## Deployment"]
        lines.append(f"- target: {deployment_state.get('target', 'vercel')}")
        lines.append(f"- enabled: {deployment_state.get('enabled', False)}")
        lines.append(f"- status: {deployment_state.get('status', 'not-configured')}")
        if deployment_state.get("latest_deployment_url"):
            lines.append(f"- deployment_url: {deployment_state['latest_deployment_url']}")
        if deployment_state.get("latest_failure_type"):
            lines.append(f"- latest_failure_type: {deployment_state['latest_failure_type']}")
        lines.append(f"- attempts: {len(all_deployments)}")
        if all_deployments:
            latest = all_deployments[-1]
            lines.append(
                f"- latest deployment.json: .agent/autonomous/sessions/{session.session_id}/deployments/"
                f"{latest.get('deployment_id')}/deployment.json"
            )

        # MVP-4F: Smoke Checks section
        all_smoke = list_smoke_checks(self.project_path, session.session_id)
        lines += ["", "## Smoke Checks"]
        smoke_status = deployment_state.get("latest_smoke_status") or "not-run"
        lines.append(f"- status: {smoke_status}")
        if deployment_state.get("latest_smoke_check_id"):
            lines.append(f"- latest: {deployment_state['latest_smoke_check_id']}")
        if deployment_state.get("latest_smoke_failure_type"):
            lines.append(f"- failure: {deployment_state['latest_smoke_failure_type']}")
        lines.append(f"- attempts: {len(all_smoke)}")
        if all_smoke:
            latest_smoke = all_smoke[-1]
            lines.append(
                f"- latest smoke-check.json: .agent/autonomous/sessions/{session.session_id}/smoke-checks/"
                f"{latest_smoke.get('smoke_check_id')}/smoke-check.json"
            )
            checks = latest_smoke.get("checks") or []
            if checks:
                passed_checks = [c for c in checks if c.get("passed")]
                failed_checks = [c for c in checks if not c.get("passed")]
                lines.append(f"- passed checks: {len(passed_checks)}/{len(checks)}")
                for c in failed_checks:
                    lines.append(f"  - failed `{c.get('name')}`: expected {c.get('expected_status')}, got {c.get('actual_status')} (error={c.get('error')})")

        # MVP-4F: Rollback section
        all_rollbacks = list_rollbacks(self.project_path, session.session_id)
        lines += ["", "## Rollback"]
        rollback_state = deployment_state.get("latest_rollback_status") or "not-run"
        lines.append(f"- status: {rollback_state}")
        if deployment_state.get("latest_rollback_id"):
            lines.append(f"- latest: {deployment_state['latest_rollback_id']}")
        if deployment_state.get("latest_rollback_failure_type"):
            lines.append(f"- failure: {deployment_state['latest_rollback_failure_type']}")
        lines.append(f"- attempts: {len(all_rollbacks)}")
        if all_rollbacks:
            latest_rb = all_rollbacks[-1]
            lines.append(
                f"- latest rollback.json: .agent/autonomous/sessions/{session.session_id}/rollbacks/"
                f"{latest_rb.get('rollback_id')}/rollback.json"
            )

        # MVP-4F: Evidence Trail — pointers to every artifact a debugger
        # might want to open. Keeps the report self-contained as a launchpad.
        lines += ["", "## Evidence Trail"]
        lines.append(f"- session state: .agent/autonomous/sessions/{session.session_id}/autonomous-session.json")
        lines.append(f"- controller log: .agent/autonomous/sessions/{session.session_id}/controller-log.jsonl")
        lines.append(f"- task graph: task-graph.json")
        if reviews:
            lines.append(f"- review items: .agent/autonomous/sessions/{session.session_id}/review-items/")
        if failures:
            lines.append(f"- integration failures: .agent/autonomous/sessions/{session.session_id}/integration-failures/")
        if all_deployments:
            lines.append(f"- deployments: .agent/autonomous/sessions/{session.session_id}/deployments/")
        if all_smoke:
            lines.append(f"- smoke checks: .agent/autonomous/sessions/{session.session_id}/smoke-checks/")
        if all_rollbacks:
            lines.append(f"- rollbacks: .agent/autonomous/sessions/{session.session_id}/rollbacks/")

        # MVP-4F: Next Actions — deterministic next-step hints based on the
        # current session state. Useful when the run pauses; harmless when
        # the run completed.
        lines += ["", "## Next Actions"]
        next_actions = self._render_next_actions(session, deployment_state, open_items, all_deployments)
        if next_actions:
            for action in next_actions:
                lines.append(f"- {action}")
        else:
            lines.append("- (none)")

        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _render_next_actions(
        self,
        session: AutonomousSession,
        deployment_state: dict[str, Any],
        open_review_items: list[Any],
        all_deployments: list[dict[str, Any]],
    ) -> list[str]:
        """Produce a short bulleted list of suggested next CLI commands
        based on what state the session ended in. Pure derivation."""
        actions: list[str] = []
        project_id = session.project_id
        if open_review_items:
            actions.append(
                f"`agent-studio autonomous reviews list --project {project_id}` to see {len(open_review_items)} open review(s)"
            )
        if session.status == "paused":
            actions.append(
                f"`agent-studio autonomous status --project {project_id}` to confirm pause reason: `{session.pause_reason}`"
            )
            if session.pause_reason in {"smoke-check-failed", "smoke-check-failed-rolled-back", "rollback-failed"}:
                actions.append(
                    f"`agent-studio autonomous smoke --project {project_id}` to re-run smoke checks after a manual fix"
                )
            if session.pause_reason in {"smoke-check-failed-rolled-back", "rollback-failed"}:
                actions.append(
                    f"`agent-studio autonomous rollback --project {project_id} --dry-run` to inspect a manual rollback"
                )
            # RC-1.1.4: deterministic per-pause-reason hints for the
            # remaining branches the audit flagged. Keep the existing
            # generic-status line above and append branch-specific hints.
            if session.pause_reason == "deployment-failed":
                actions.append(
                    f"`agent-studio autonomous deploy --project {project_id} --dry-run` to inspect the failed deploy command"
                )
                actions.append(
                    f"inspect the latest deployment artifact and the open `deployment_failure` review item before retrying with `--yes`"
                )
            if isinstance(session.pause_reason, str) and session.pause_reason.startswith("apply_failed"):
                actions.append(
                    f"`agent-studio agentic-candidates list --project {project_id}` to see the candidate that failed to apply"
                )
                actions.append(
                    f"after fixing the underlying gate failure, `agent-studio autonomous reviews approve <review_id> --yes` to retry the safe apply"
                )
            if isinstance(session.pause_reason, str) and (
                "max_abandoned_tasks" in session.pause_reason
                or "too-many-corrective-tasks" in session.pause_reason
            ):
                actions.append(
                    f"`agent-studio agentic-abandonments list --project {project_id}` to inspect why prior runs gave up"
                )
                actions.append(
                    f"`agent-studio autonomous reviews list --project {project_id}` to see what's blocking forward progress"
                )
            if session.pause_reason == "halt_requested":
                actions.append(
                    f"`agent-studio autonomous resume --project {project_id}` to continue from where halt paused"
                )
            if isinstance(session.pause_reason, str) and session.pause_reason.startswith("inner_run_exception"):
                actions.append(
                    f"inspect `agent-studio autonomous logs --project {project_id} --tail 100` for the failing task and exception class"
                )
        elif session.status == "completed":
            url = deployment_state.get("latest_deployment_url")
            status = deployment_state.get("status")
            if status == "verified" and url:
                actions.append(f"deployment ready and smoke-verified at {url}")
            elif status == "deployed" and url:
                actions.append(f"deployment ready at {url} (smoke checks were skipped)")
            elif not all_deployments and not deployment_state.get("enabled"):
                actions.append(
                    f"deploy is disabled in agent-studio.yaml; enable it and run "
                    f"`agent-studio autonomous deploy --project {project_id} --yes` to ship"
                )
        return actions


# ---------------------------------------------------------------------------
# Public helpers used by the CLI
# ---------------------------------------------------------------------------
def write_task_graph(project_path: Path, task_graph: dict[str, Any]) -> Path:
    path = task_graph_file(project_path)
    path.write_text(json.dumps(task_graph, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_task_graph(project_path: Path) -> dict[str, Any]:
    path = task_graph_file(project_path)
    if not path.exists():
        return {"schema_version": SCHEMA_VERSION_TASK_GRAPH, "tasks": [], "project_title": None, "overview": ""}
    return json.loads(path.read_text(encoding="utf-8"))


def ingest_requirements(project_path: Path, requirements_md_path: Path) -> dict[str, Any]:
    """Copy requirements into the project, derive PRD/architecture/task-graph,
    and write them all to the project root. Returns the task-graph dict."""
    if not requirements_md_path.exists():
        raise FileNotFoundError(f"requirements file not found: {requirements_md_path}")
    md_text = requirements_md_path.read_text(encoding="utf-8")
    target = project_path / "requirements.md"
    if requirements_md_path.resolve() != target.resolve():
        shutil.copyfile(requirements_md_path, target)
    task_graph = parse_requirements_md(md_text, project_path)
    write_task_graph(project_path, task_graph)
    (project_path / "prd.md").write_text(render_prd_md(task_graph, md_text), encoding="utf-8")
    (project_path / "acceptance-criteria.json").write_text(
        json.dumps(render_acceptance_criteria(task_graph), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (project_path / "architecture.md").write_text(render_architecture_md(task_graph, project_path), encoding="utf-8")
    return task_graph
