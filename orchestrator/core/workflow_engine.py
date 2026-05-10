from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import (
    ArtifactStore,
    _artifact_kind,
    render_stub_artifact,
    wrap_with_untrusted_frontmatter,
)
from orchestrator.contracts import Validator, load_contracts
from orchestrator.core.executor import (
    Executor,
    ExecutionEvidence,
    detect_project_type,
)
from orchestrator.core.event_bus import EventBus
from orchestrator.core.ids import now_iso, short_id, slugify
from orchestrator.core.task_store import TaskStore
from orchestrator.core.yaml_loader import load_yaml
from orchestrator.db import Database


class WorkflowEngine:
    def __init__(
        self,
        db: Database,
        workflows_dir: Path,
        *,
        agents_dir: Path | None = None,
        agent_runner: Any | None = None,
        agent_registry: Any | None = None,
    ):
        self.db = db
        self.workflows_dir = workflows_dir
        self.events = EventBus(db)
        self.artifacts = ArtifactStore(db)
        self.tasks = TaskStore(db)
        self.agents_dir = agents_dir
        # agent_runner and agent_registry are injected by tests. In production
        # they are constructed lazily inside _run_llm_phase so that bare unit
        # tests of WorkflowEngine that don't care about LLMs do not pay the
        # cost of importing every agent module.
        self._agent_runner = agent_runner
        self._agent_registry = agent_registry
        # Lazily loaded so tests can inject a custom validator (or skip it
        # entirely by setting ``engine._validator = None`` after construction).
        self._validator: Validator | None = None

    def create_project(self, idea: str, projects_dir: Path, name: str | None = None) -> dict[str, Any]:
        project_id = short_id("project")
        project_name = name or _name_from_idea(idea)
        slug = slugify(project_name)
        project_path = projects_dir / f"{slug}-{project_id[-6:]}"
        _initialize_project_dir(project_path, idea, project_id, project_name)
        now = now_iso()
        self.db.execute(
            """
            INSERT INTO projects (id, name, idea, path, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (project_id, project_name, idea, str(project_path), "created", now, now),
        )
        self.events.emit(
            event_type="project.created",
            project_id=project_id,
            message=f"Created project {project_name}.",
            payload={"path": str(project_path)},
        )
        return {
            "id": project_id,
            "name": project_name,
            "idea": idea,
            "path": str(project_path),
            "status": "created",
        }

    def run(self, project_id: str, workflow_id: str = "software_project") -> dict[str, Any]:
        workflow = self.load_workflow(workflow_id)
        project = self.require_project(project_id)
        run_id = short_id("run")
        # newly-created run can't conflict with anyone else; just stamp the
        # owner so concurrent-resume detection works.
        # (Insert happens below — we lock right after.)
        now = now_iso()
        self.db.execute(
            """
            INSERT INTO runs (id, project_id, workflow_id, status, current_phase, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, project_id, workflow_id, "running", None, now, now),
        )
        self.db.execute(
            "UPDATE projects SET status = 'running', updated_at = ? WHERE id = ?",
            (now, project_id),
        )
        self.events.emit(
            event_type="run.created",
            project_id=project_id,
            run_id=run_id,
            message=f"Created run {run_id} for workflow {workflow_id}.",
        )
        self._create_phases_and_tasks(project_id, run_id, workflow)
        self.acquire_run_lock(run_id)
        try:
            return self._advance(run_id)
        finally:
            self.release_run_lock(run_id)

    def resume(self, run_id: str) -> dict[str, Any]:
        run = self.require_run(run_id)
        self.acquire_run_lock(run_id)
        try:
            self.db.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE id = ?",
                ("running", now_iso(), run_id),
            )
            self.db.execute(
                "UPDATE projects SET status = 'running', updated_at = ? WHERE id = ?",
                (now_iso(), run["project_id"]),
            )
            return self._advance(run["id"])
        finally:
            self.release_run_lock(run_id)

    def approve(self, project_id: str, target: str) -> dict[str, Any]:
        run = self.latest_run(project_id)
        if not run:
            raise ValueError("No runs exist for this project.")
        approval = self.db.query_one(
            """
            SELECT * FROM approvals
            WHERE project_id = ? AND run_id = ? AND status = 'pending'
              AND (phase_id = ? OR gate = ? OR gate = ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project_id, run["id"], target, target, f"{target}_approval"),
        )
        if not approval:
            raise ValueError(f"No pending approval found for {target}.")
        now = now_iso()
        self.db.execute(
            "UPDATE approvals SET status = 'approved', decided_at = ? WHERE id = ?",
            (now, approval["id"]),
        )
        self.db.execute(
            """
            UPDATE phases
            SET status = 'completed', completed_at = ?, updated_at = ?
            WHERE run_id = ? AND phase_id = ?
            """,
            (now, now, run["id"], approval["phase_id"]),
        )
        self.tasks.set_phase_tasks_status(run["id"], approval["phase_id"], "completed")
        self.events.emit(
            event_type="approval.approved",
            project_id=project_id,
            run_id=run["id"],
            phase_id=approval["phase_id"],
            message=f"Approved {approval['gate']}.",
        )
        return self.resume(run["id"])

    def reject(self, project_id: str, target: str, reason: str = "Rejected by user.") -> dict[str, Any]:
        run = self.latest_run(project_id)
        if not run:
            raise ValueError("No runs exist for this project.")
        approval = self.db.query_one(
            """
            SELECT * FROM approvals
            WHERE project_id = ? AND run_id = ? AND status = 'pending'
              AND (phase_id = ? OR gate = ? OR gate = ?)
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (project_id, run["id"], target, target, f"{target}_approval"),
        )
        if not approval:
            raise ValueError(f"No pending approval found for {target}.")
        now = now_iso()
        self.db.execute(
            "UPDATE approvals SET status = 'rejected', reason = ?, decided_at = ? WHERE id = ?",
            (reason, now, approval["id"]),
        )
        self.db.execute(
            """
            UPDATE phases
            SET status = 'blocked', updated_at = ?
            WHERE run_id = ? AND phase_id = ?
            """,
            (now, run["id"], approval["phase_id"]),
        )
        self.db.execute(
            "UPDATE runs SET status = 'blocked', current_phase = ?, updated_at = ? WHERE id = ?",
            (approval["phase_id"], now, run["id"]),
        )
        self.db.execute(
            "UPDATE projects SET status = 'blocked', updated_at = ? WHERE id = ?",
            (now, project_id),
        )
        self.tasks.set_phase_tasks_status(run["id"], approval["phase_id"], "blocked")
        self.events.emit(
            event_type="approval.rejected",
            project_id=project_id,
            run_id=run["id"],
            phase_id=approval["phase_id"],
            message=f"Rejected {approval['gate']}: {reason}",
        )
        return {"status": "blocked", "run_id": run["id"], "phase_id": approval["phase_id"]}

    def retry(self, project_id: str, target: str) -> dict[str, Any]:
        run = self.latest_run(project_id)
        if not run:
            raise ValueError("No runs exist for this project.")
        phase = self.db.query_one(
            """
            SELECT * FROM phases
            WHERE run_id = ? AND phase_id = ?
            LIMIT 1
            """,
            (run["id"], target),
        )
        if not phase:
            raise ValueError(f"Phase not found in latest run: {target}")
        now = now_iso()
        downstream = self.db.query_all(
            """
            SELECT phase_id FROM phases
            WHERE run_id = ? AND sequence >= ?
            ORDER BY sequence ASC
            """,
            (run["id"], phase["sequence"]),
        )
        for row in downstream:
            self.db.execute(
                """
                UPDATE phases
                SET status = 'pending', started_at = NULL, completed_at = NULL, updated_at = ?
                WHERE run_id = ? AND phase_id = ?
                """,
                (now, run["id"], row["phase_id"]),
            )
            self.tasks.set_phase_tasks_status(run["id"], row["phase_id"], "pending")
        self.db.execute(
            "UPDATE runs SET status = 'running', current_phase = ?, completed_at = NULL, updated_at = ? WHERE id = ?",
            (target, now, run["id"]),
        )
        self.db.execute(
            "UPDATE projects SET status = 'running', updated_at = ? WHERE id = ?",
            (now, project_id),
        )
        self.events.emit(
            event_type="phase.retry",
            project_id=project_id,
            run_id=run["id"],
            phase_id=target,
            message=f"Retry requested from phase {target}.",
        )
        return self._advance(run["id"])

    def load_workflow(self, workflow_id: str) -> dict[str, Any]:
        path = self.workflows_dir / f"{workflow_id}.yaml"
        if not path.exists():
            raise FileNotFoundError(f"Workflow not found: {path}")
        workflow = load_yaml(path)
        if "phases" not in workflow or not isinstance(workflow["phases"], list):
            raise ValueError(f"Workflow {workflow_id} must define a phases list.")
        return workflow

    def latest_project(self) -> dict[str, Any] | None:
        row = self.db.query_one("SELECT * FROM projects ORDER BY created_at DESC LIMIT 1")
        return dict(row) if row else None

    def require_project(self, project_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not row:
            raise ValueError(f"Project not found: {project_id}")
        return dict(row)

    # ------------------------------------------------------------------
    # C0d — run-level locking. Two-process safety: only one orchestrator
    # process at a time may advance a given run. Locks expire after
    # _LOCK_STALE_SECONDS without a heartbeat so a crashed process doesn't
    # permanently block resume.
    # ------------------------------------------------------------------

    def acquire_run_lock(self, run_id: str, owner_id: str | None = None) -> str:
        """Reserve the run for the calling process. Returns the owner id used.

        Raises ``RuntimeError`` if another live process holds the lock.
        Stale locks (no heartbeat for > 10 minutes) are reclaimed.
        """
        import os as _os
        import socket as _socket

        owner = owner_id or f"{_socket.gethostname()}:{_os.getpid()}"
        now = now_iso()
        run = self.require_run(run_id)
        existing = run.get("locked_by")
        heartbeat = run.get("heartbeat_at")
        if existing and existing != owner:
            if heartbeat and not self._lock_is_stale(heartbeat):
                raise RuntimeError(
                    f"run {run_id} is locked by {existing} (last heartbeat {heartbeat}). "
                    f"Wait for it to finish, or kill that process."
                )
            # Stale → reclaim.
        self.db.execute(
            "UPDATE runs SET locked_by = ?, heartbeat_at = ?, updated_at = ? WHERE id = ?",
            (owner, now, now, run_id),
        )
        return owner

    def heartbeat(self, run_id: str) -> None:
        self.db.execute(
            "UPDATE runs SET heartbeat_at = ? WHERE id = ?",
            (now_iso(), run_id),
        )

    def release_run_lock(self, run_id: str) -> None:
        self.db.execute(
            "UPDATE runs SET locked_by = NULL, heartbeat_at = NULL, updated_at = ? WHERE id = ?",
            (now_iso(), run_id),
        )

    @staticmethod
    def _lock_is_stale(heartbeat_iso: str) -> bool:
        from datetime import datetime, timezone

        try:
            ts = datetime.fromisoformat(heartbeat_iso.replace("Z", "+00:00"))
        except ValueError:
            return True
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        delta = (datetime.now(timezone.utc) - ts).total_seconds()
        return delta > _LOCK_STALE_SECONDS

    def latest_run(self, project_id: str, *, include_cancelled: bool = False) -> dict[str, Any] | None:
        """Most recent run for a project. Cancelled runs are skipped by default
        so that operations like `approve` and `status` snap back to the
        previous live run after a Ctrl+C / explicit cancel.
        """
        if include_cancelled:
            row = self.db.query_one(
                "SELECT * FROM runs WHERE project_id = ? ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            )
        else:
            row = self.db.query_one(
                "SELECT * FROM runs WHERE project_id = ? AND status != 'cancelled' "
                "ORDER BY created_at DESC LIMIT 1",
                (project_id,),
            )
        return dict(row) if row else None

    def require_run(self, run_id: str) -> dict[str, Any]:
        row = self.db.query_one("SELECT * FROM runs WHERE id = ?", (run_id,))
        if not row:
            raise ValueError(f"Run not found: {run_id}")
        return dict(row)

    def status(self, project_id: str | None = None) -> dict[str, Any]:
        project = self.require_project(project_id) if project_id else self.latest_project()
        if not project:
            return {"project": None, "run": None, "phases": [], "tasks": [], "approvals": []}
        run = self.latest_run(project["id"])
        phases = []
        tasks = []
        approvals = []
        if run:
            phases = [
                dict(row)
                for row in self.db.query_all(
                    "SELECT * FROM phases WHERE run_id = ? ORDER BY sequence ASC",
                    (run["id"],),
                )
            ]
            tasks = self.tasks.list_for_run(run["id"])
            approvals = [
                dict(row)
                for row in self.db.query_all(
                    "SELECT * FROM approvals WHERE run_id = ? ORDER BY created_at ASC",
                    (run["id"],),
                )
            ]
        return {"project": project, "run": run, "phases": phases, "tasks": tasks, "approvals": approvals}

    def _create_phases_and_tasks(
        self,
        project_id: str,
        run_id: str,
        workflow: dict[str, Any],
    ) -> None:
        now = now_iso()
        task_by_phase: dict[str, str] = {}
        for sequence, phase in enumerate(workflow["phases"]):
            phase_id = str(phase["id"])
            self.db.execute(
                """
                INSERT INTO phases (
                    id, run_id, phase_id, owner, status, gate, depends_on,
                    sequence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"{run_id}:{phase_id}",
                    run_id,
                    phase_id,
                    str(phase.get("owner", "lead")),
                    "pending",
                    phase.get("gate"),
                    json.dumps(list(phase.get("depends_on") or [])),
                    sequence,
                    now,
                ),
            )
            task_by_phase[phase_id] = self.tasks.create_phase_task(
                project_id=project_id,
                run_id=run_id,
                phase=phase,
                sequence=sequence,
            )
        for phase in workflow["phases"]:
            task_id = task_by_phase[str(phase["id"])]
            for dependency_phase_id in list(phase.get("depends_on") or []):
                dependency_task_id = task_by_phase.get(str(dependency_phase_id))
                if dependency_task_id:
                    self.db.execute(
                        """
                        INSERT OR IGNORE INTO task_dependencies (task_id, depends_on_task_id)
                        VALUES (?, ?)
                        """,
                        (task_id, dependency_task_id),
                    )
        self.events.emit(
            event_type="workflow.planned",
            project_id=project_id,
            run_id=run_id,
            message=f"Planned {len(workflow['phases'])} workflow phases.",
        )

    def _advance(self, run_id: str) -> dict[str, Any]:
        run = self.require_run(run_id)
        workflow = self.load_workflow(run["workflow_id"])
        phases_by_id = {str(phase["id"]): phase for phase in workflow["phases"]}
        project = self.require_project(run["project_id"])
        project_path = Path(project["path"])
        while True:
            self.heartbeat(run_id)
            phase = self._next_incomplete_phase(run_id)
            if not phase:
                now = now_iso()
                grade, grade_reason = self._compute_delivery_grade(run_id)
                self.db.execute(
                    """
                    UPDATE runs
                    SET status = 'completed', current_phase = NULL, completed_at = ?, updated_at = ?,
                        delivery_grade = ?
                    WHERE id = ?
                    """,
                    (now, now, grade, run_id),
                )
                self.db.execute(
                    "UPDATE projects SET status = 'completed', updated_at = ? WHERE id = ?",
                    (now, project["id"]),
                )
                self.events.emit(
                    event_type="run.completed",
                    project_id=project["id"],
                    run_id=run_id,
                    message=f"Run {run_id} completed with grade {grade} ({grade_reason}).",
                    payload={"delivery_grade": grade, "grade_reason": grade_reason},
                )
                _progress(f"run completed · grade={grade} ({grade_reason})")
                if _is_autonomous():
                    try:
                        report_path = self._write_autonomous_report(
                            project=project,
                            project_path=project_path,
                            run_id=run_id,
                            workflow=workflow,
                        )
                        _progress(f"autonomous report written: {report_path}")
                    except Exception as exc:  # noqa: BLE001 — report failure must not block completion
                        _progress(f"autonomous report failed: {type(exc).__name__}: {str(exc)[:160]}")
                return {"status": "completed", "run_id": run_id}

            dependencies = json.loads(phase["depends_on"])
            incomplete_dependency = self._first_incomplete_dependency(run_id, dependencies)
            if incomplete_dependency:
                now = now_iso()
                self.db.execute(
                    """
                    UPDATE phases SET status = 'blocked', updated_at = ?
                    WHERE run_id = ? AND phase_id = ?
                    """,
                    (now, run_id, phase["phase_id"]),
                )
                self.tasks.set_phase_tasks_status(run_id, phase["phase_id"], "blocked")
                self.events.emit(
                    event_type="phase.blocked",
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id=phase["phase_id"],
                    message=f"Phase {phase['phase_id']} blocked by {incomplete_dependency}.",
                )
                return {"status": "blocked", "run_id": run_id, "phase_id": phase["phase_id"]}

            try:
                self._run_phase(
                    project=project,
                    project_path=project_path,
                    run_id=run_id,
                    phase_row=phase,
                    phase_config=phases_by_id[phase["phase_id"]],
                )
            except Exception as exc:  # noqa: BLE001 — autonomous mode must keep moving
                if not _is_autonomous():
                    raise
                # Autonomous mode: log, mark this phase failed (so it won't
                # block downstream via dependency check), continue.
                phase_id_str = phase["phase_id"]
                now = now_iso()
                self.db.execute(
                    "UPDATE phases SET status = 'failed', completed_at = ?, updated_at = ? "
                    "WHERE run_id = ? AND phase_id = ?",
                    (now, now, run_id, phase_id_str),
                )
                self.tasks.set_phase_tasks_status(run_id, phase_id_str, "failed")
                self.events.emit(
                    event_type="phase.crashed",
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id=phase_id_str,
                    message=f"Phase {phase_id_str} crashed in autonomous mode: {type(exc).__name__}: {str(exc)[:200]}",
                    payload={"error_type": type(exc).__name__, "error": str(exc)},
                )
                _progress(f"[{phase_id_str}] CRASHED → autonomous mode continues: {type(exc).__name__}: {str(exc)[:160]}")
                # fall through; loop picks the next incomplete phase
                continue

            updated_phase = self.db.query_one(
                "SELECT * FROM phases WHERE run_id = ? AND phase_id = ?",
                (run_id, phase["phase_id"]),
            )
            if updated_phase and updated_phase["status"] == "needs_approval":
                return {"status": "needs_approval", "run_id": run_id, "phase_id": phase["phase_id"]}

    def _run_phase(
        self,
        *,
        project: dict[str, Any],
        project_path: Path,
        run_id: str,
        phase_row: dict[str, Any],
        phase_config: dict[str, Any],
    ) -> None:
        phase_id = phase_row["phase_id"]
        now = now_iso()
        self.db.execute(
            """
            UPDATE phases SET status = 'running', started_at = COALESCE(started_at, ?), updated_at = ?
            WHERE run_id = ? AND phase_id = ?
            """,
            (now, now, run_id, phase_id),
        )
        self.db.execute(
            "UPDATE runs SET status = 'running', current_phase = ?, updated_at = ? WHERE id = ?",
            (phase_id, now, run_id),
        )
        self.tasks.set_phase_tasks_status(run_id, phase_id, "running")
        self.events.emit(
            event_type="phase.started",
            project_id=project["id"],
            run_id=run_id,
            phase_id=phase_id,
            message=f"Started phase {phase_id}.",
        )
        owner_label = str(phase_config.get("owner") or "?")
        _progress(f"[{phase_id}] starting (owner={owner_label})")
        phase_started = time.perf_counter()
        outputs = [str(o).format(run_id=run_id) for o in (phase_config.get("output") or [])]
        written, source = self._produce_phase_outputs(
            project=project,
            project_path=project_path,
            run_id=run_id,
            phase_id=phase_id,
            phase_config=phase_config,
            outputs=outputs,
        )
        elapsed_ms = int((time.perf_counter() - phase_started) * 1000)
        _progress(f"[{phase_id}] done in {elapsed_ms}ms via {source} ({len(written)} file(s))")
        self.events.emit(
            event_type="phase.artifacts",
            project_id=project["id"],
            run_id=run_id,
            phase_id=phase_id,
            message=f"Wrote {len(written)} artifact(s) for {phase_id} via {source}.",
            payload={"artifacts": written, "source": source, "elapsed_ms": elapsed_ms},
        )
        gate = phase_config.get("gate")
        if gate and not self._has_approved_gate(run_id, phase_id, str(gate)):
            if _is_autonomous():
                # Autonomous mode: stamp the gate as approved-by-system and
                # keep going. We still record an approval row for traceability
                # so `diagnose` and audits can see what was auto-approved.
                approval_id = short_id("approval")
                now = now_iso()
                self.db.execute(
                    """
                    INSERT INTO approvals (
                        id, project_id, run_id, phase_id, gate, status, reason, created_at, decided_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        project["id"],
                        run_id,
                        phase_id,
                        str(gate),
                        "approved",
                        "Auto-approved by autonomous mode.",
                        now,
                        now,
                    ),
                )
                self.events.emit(
                    event_type="approval.auto_approved",
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id=phase_id,
                    message=f"Auto-approved {gate} (autonomous mode).",
                )
                _progress(f"[{phase_id}] gate '{gate}' auto-approved (autonomous mode)")
                # fall through to _complete_phase below
            else:
                approval_id = short_id("approval")
                self.db.execute(
                    """
                    INSERT INTO approvals (
                        id, project_id, run_id, phase_id, gate, status, reason, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        project["id"],
                        run_id,
                        phase_id,
                        str(gate),
                        "pending",
                        f"{phase_id} requires approval before dependent phases can run.",
                        now_iso(),
                    ),
                )
                self.db.execute(
                    """
                    UPDATE phases SET status = 'needs_approval', updated_at = ?
                    WHERE run_id = ? AND phase_id = ?
                    """,
                    (now_iso(), run_id, phase_id),
                )
                self.db.execute(
                    "UPDATE runs SET status = 'needs_approval', current_phase = ?, updated_at = ? WHERE id = ?",
                    (phase_id, now_iso(), run_id),
                )
                self.db.execute(
                    "UPDATE projects SET status = 'needs_approval', updated_at = ? WHERE id = ?",
                    (now_iso(), project["id"]),
                )
                self.tasks.set_phase_tasks_status(run_id, phase_id, "needs_approval")
                self.events.emit(
                    event_type="approval.requested",
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id=phase_id,
                    message=f"Approval requested for {gate}.",
                )
                return

        self._complete_phase(project["id"], run_id, phase_id)

    # --- LLM-driven phase generation ---------------------------------------

    def _produce_phase_outputs(
        self,
        *,
        project: dict[str, Any],
        project_path: Path,
        run_id: str,
        phase_id: str,
        phase_config: dict[str, Any],
        outputs: list[str],
    ) -> tuple[list[str], str]:
        """Generate the phase's required output files.

        Returns (written_paths, source) where source is "llm" or "stub".
        Tries the LLM path first (loads the agent YAML, calls AgentRunner).
        Falls back to deterministic stub content if the LLM is unavailable,
        the response is malformed, or any required file is missing. Missing
        files are individually backfilled with stubs so partial LLM responses
        still yield a complete phase artifact set.
        """
        # Default mode is the deterministic stub. The LLM path is opt-in via
        # LOCALAGENTS_USE_LLM=1 (orchestrator/cli.py main() sets this) or by
        # injecting an agent_runner directly (tests). Without opt-in, calls to
        # `engine.run()` from tests in environments where the CLI happens to
        # be installed-but-unauthenticated would hang on real subprocess
        # invocations. FORCE_STUB stays as a hard kill switch.
        if os.environ.get("LOCALAGENTS_FORCE_STUB") == "1":
            return self._write_stub_outputs(
                project=project,
                project_path=project_path,
                run_id=run_id,
                phase_id=phase_id,
                outputs=outputs,
            ), "stub"

        llm_enabled = (
            os.environ.get("LOCALAGENTS_USE_LLM") == "1"
            or self._agent_runner is not None
        )

        owner = phase_config.get("owner")
        llm_files: dict[str, str] = {}
        if llm_enabled and owner and self.agents_dir is not None:
            attempts = int(os.environ.get("LOCALAGENTS_LLM_ATTEMPTS", "2") or "2")
            attempts = max(1, min(attempts, 5))
            last_exc: Exception | None = None
            for attempt in range(1, attempts + 1):
                try:
                    llm_files = self._run_llm_phase(
                        owner=str(owner),
                        project=project,
                        project_path=project_path,
                        run_id=run_id,
                        phase_id=phase_id,
                        outputs=outputs,
                    )
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001 — fallback path needs to be wide
                    last_exc = exc
                    if attempt < attempts:
                        _progress(
                            f"[{phase_id}] LLM attempt {attempt}/{attempts} failed: "
                            f"{type(exc).__name__}: {str(exc)[:140]} — retrying after backoff"
                        )
                        time.sleep(min(5 * attempt, 15))
                        continue
            if last_exc is not None:
                self.events.emit(
                    event_type="phase.llm_fallback",
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id=phase_id,
                    message=f"LLM path failed for {phase_id} after {attempts} attempt(s); falling back to stub: {last_exc}",
                    payload={"error": str(last_exc), "attempts": attempts},
                )
                _progress(
                    f"[{phase_id}] LLM gave up after {attempts} attempts → stub fallback: "
                    f"{type(last_exc).__name__}: {str(last_exc)[:160]}"
                )
                llm_files = {}

        written: list[str] = []
        source = "llm" if llm_files else "stub"
        validation_errors: list[tuple[str, str]] = []
        required_set = set(outputs)
        validator = self._get_validator()
        # Track per-file validation outcomes for phase score aggregation (B3).
        file_scores: list[int] = []
        # C0b: collect full ValidationResult objects so we can apply a
        # critical-artifact floor afterwards.
        critical_results: list[Any] = []

        # First, write each required output (using LLM content where present,
        # backfilling with an explicitly-marked untrusted fallback when the
        # LLM omitted it).
        for relative in outputs:
            target = project_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            content = llm_files.get(relative)
            if content:
                file_source = "llm"
                trust_level = "medium"
            else:
                # B1: do not write a silent stub. Wrap the deterministic
                # template content with a frontmatter that loudly marks it as
                # untrusted, so downstream phases (and human readers) can tell
                # this is not a real deliverable.
                base = render_stub_artifact(phase_id, relative, project["idea"], run_id)
                content = wrap_with_untrusted_frontmatter(
                    base,
                    path=relative,
                    reason=f"LLM did not produce content for {relative} in phase {phase_id}",
                )
                file_source = "fallback"
                trust_level = "untrusted"
                if llm_files:
                    source = "mixed"
            target.write_text(content, encoding="utf-8")
            written.append(relative)
            # B2: programmatic contract validator (replaces the older
            # syntax-only _validate_artifact path).
            v = validator.validate(relative, content)
            validation_status = v.status
            validation_score = v.score
            if file_source == "fallback":
                # Fallback content is by definition untrusted regardless of
                # whether it happens to satisfy structural rules.
                validation_status = "failed"
                validation_score = 0
                # Record a synthetic failed result for the critical-floor
                # check (a missing critical file is, naturally, a failure).
                from orchestrator.contracts.validator import ValidationResult as _VR
                critical_results.append(_VR(
                    path=relative, score=0, status="failed", critical=v.critical
                ))
            else:
                critical_results.append(v)
            file_scores.append(validation_score)
            for c in v.failed_checks():
                validation_errors.append((relative, f"{c.name}: {c.detail}"))
            self.artifacts.register(
                project_id=project["id"],
                run_id=run_id,
                phase_id=phase_id,
                path=relative,
                kind=_artifact_kind(relative),
                summary=f"Generated {relative} for {phase_id} via {file_source} (score={validation_score}).",
                source_type=file_source,
                trust_level=trust_level,
                validation_status=validation_status,
                validation_score=validation_score,
            )

        # Then write any extra files the LLM produced that were NOT in the
        # required list. This is how implementation phase delivers actual
        # source code: the only required output is implementation-summary.md,
        # but Codex returns dozens of additional source files in `files`.
        # We refuse paths that look like absolute paths or escape the project
        # directory to prevent agents from clobbering arbitrary files.
        for relative, content in llm_files.items():
            if relative in required_set:
                continue
            if not _is_safe_relative_path(relative):
                _progress(f"[{phase_id}] skipping unsafe path from LLM: {relative}")
                continue
            target = project_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            written.append(relative)
            v = validator.validate(relative, content)
            for c in v.failed_checks():
                validation_errors.append((relative, f"{c.name}: {c.detail}"))
            # Extra files are not part of the phase score (those are scoped to
            # required outputs); their score is recorded but does not feed
            # phase aggregation.
            self.artifacts.register(
                project_id=project["id"],
                run_id=run_id,
                phase_id=phase_id,
                path=relative,
                kind=_artifact_kind(relative),
                summary=f"Extra file {relative} for {phase_id} via llm (score={v.score}).",
                source_type="extra",
                trust_level="medium",
                validation_status=v.status,
                validation_score=v.score,
            )
        if validation_errors:
            for relative, err in validation_errors:
                self.events.emit(
                    event_type="phase.validation_failed",
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id=phase_id,
                    message=f"{relative} failed format validation: {err}",
                    payload={"file": relative, "error": err},
                )
                _progress(f"[{phase_id}] validation: {relative} → {err}")

        # C1c — implementation phase build/test/fix loop. Runs only for
        # `implementation`. Modifies file_scores / source if repair attempts
        # write new content, and sets self._latest_execution_evidence for
        # downstream report consumption.
        execution_evidence: ExecutionEvidence | None = None
        if phase_id == "implementation" and llm_files:
            execution_evidence = self._run_implementation_fix_loop(
                project=project,
                project_path=project_path,
                run_id=run_id,
                phase_id=phase_id,
                phase_config=phase_config,
                outputs=outputs,
                initial_files=llm_files,
            )
        if execution_evidence is not None:
            self._record_execution_evidence(
                project=project,
                project_path=project_path,
                run_id=run_id,
                phase_id=phase_id,
                evidence=execution_evidence,
            )

        # B3: aggregate per-file scores into a phase_score and derive a richer
        # status. Required outputs feed the score (extras don't, they're not
        # part of the phase contract).
        phase_score = int(round(sum(file_scores) / len(file_scores))) if file_scores else 0
        had_fallback = source in {"stub", "mixed"}
        # C0b: critical-artifact floor. If any contract-marked-critical file
        # ends up failed (or absent → fallback), the phase score is capped at
        # 59 regardless of how high other artifacts scored. This prevents the
        # mean from masking a blocking failure (e.g. PRD scoring 90 but
        # acceptance-criteria.md missing).
        had_critical_failure = any(
            r.critical and r.status in {"failed", "partial"} for r in critical_results
        )
        if had_critical_failure:
            phase_score = min(phase_score, 59)
        if source == "llm" and phase_score >= 85 and not had_critical_failure:
            phase_qual_status = "completed_verified"
        elif had_fallback or phase_score < 60 or had_critical_failure:
            phase_qual_status = "completed_degraded"
        else:
            phase_qual_status = "completed_unverified"

        # D0.6: write attempt_count back to phases. Without this, the
        # implementation phase shows attempt_count=0 even when the C1c fix
        # loop ran 3 times — which silently breaks the future D5 budget
        # governor (which reads this field to decide caps). For non-fix-loop
        # phases the value is 1 (the phase ran once and produced this
        # scoring); for implementation we use the real attempts count off
        # the evidence object.
        attempt_count = 1
        if execution_evidence is not None:
            history = getattr(execution_evidence, "_attempts", None)
            if history:
                attempt_count = len(history)

        self.db.execute(
            "UPDATE phases SET phase_score = ?, attempt_count = ?, updated_at = ? "
            "WHERE run_id = ? AND phase_id = ?",
            (phase_score, attempt_count, now_iso(), run_id, phase_id),
        )
        self.events.emit(
            event_type="phase.scored",
            project_id=project["id"],
            run_id=run_id,
            phase_id=phase_id,
            message=f"phase {phase_id} scored {phase_score} ({phase_qual_status})",
            payload={
                "score": phase_score,
                "quality_status": phase_qual_status,
                "source": source,
                "attempt_count": attempt_count,
            },
        )
        _progress(f"[{phase_id}] score={phase_score} quality={phase_qual_status}")
        return written, source

    def _write_stub_outputs(
        self,
        *,
        project: dict[str, Any],
        project_path: Path,
        run_id: str,
        phase_id: str,
        outputs: list[str],
    ) -> list[str]:
        return self.artifacts.write_outputs(
            project_id=project["id"],
            run_id=run_id,
            phase_id=phase_id,
            project_path=project_path,
            idea=project["idea"],
            outputs=outputs,
        )

    def _run_llm_phase(
        self,
        *,
        owner: str,
        project: dict[str, Any],
        project_path: Path,
        run_id: str,
        phase_id: str,
        outputs: list[str],
    ) -> dict[str, str]:
        if not outputs:
            return {}
        registry = self._get_agent_registry()
        agent_config = registry.require(owner)
        runner = self._get_agent_runner()

        # Local import keeps WorkflowEngine importable even if agents/ is not
        # installed (e.g. during minimal unit tests).
        from orchestrator.agents.base import AgentContext

        # Gather upstream artifacts so the LLM can compose on prior work
        # rather than reinventing context from `idea` alone.
        upstream_inputs = self._gather_upstream_inputs(
            run_id=run_id,
            current_phase=phase_id,
            project_path=project_path,
        )
        instructions = _phase_instructions(phase_id, owner, project.get("idea") or "", upstream_inputs)

        context = AgentContext(
            project_id=project["id"],
            run_id=run_id,
            task_id=phase_id,
            project_path=project_path,
            idea=project.get("idea"),
            instructions=instructions,
            output_paths=list(outputs),
            inputs=upstream_inputs,
        )
        result = runner.run_task(agent_config, context)
        if result.status == "failed":
            # The agent ran but explicitly judged the task can't be completed
            # (e.g. QA refusing because no implementation exists). If it gave
            # us file content alongside, use that — it usually contains a
            # well-formed "blocked" report. Only treat as a hard failure if
            # the agent returned no files at all (no useful work product).
            files = dict(result.files or {})
            if files:
                self.events.emit(
                    event_type="phase.agent_blocked",
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id=phase_id,
                    message=f"{owner} reported blocked status but produced files: {result.summary}",
                    payload={"summary": result.summary},
                )
                _progress(f"[{phase_id}] agent reported blocked but produced {len(files)} file(s) — using them")
                return files
            raise RuntimeError(f"Agent reported failure: {result.summary}")
        return dict(result.files or {})

    def _write_autonomous_report(
        self,
        *,
        project: dict[str, Any],
        project_path: Path,
        run_id: str,
        workflow: dict[str, Any],
    ) -> Path:
        """Produce the autonomous run report.

        First screen is fixed:
            - delivery grade
            - ready-to-use verdict
            - top risks (≤5 items)
            - recommended next command
            - trust summary table (per phase)
        Detail (per-file artifact list, full event logs) follows.
        """
        report_path = project_path / ".agent" / "runs" / run_id / "final-run-status.md"
        report_path.parent.mkdir(parents=True, exist_ok=True)

        run_row = self.db.query_one(
            "SELECT delivery_grade FROM runs WHERE id = ?", (run_id,)
        )
        grade = (run_row["delivery_grade"] if run_row and "delivery_grade" in run_row.keys() else None) or "?"

        # C2: pull execution evidence (if C1c ran on the implementation phase).
        execution_summary = self._read_execution_evidence(project_path, run_id)

        phases = self.db.query_all(
            "SELECT phase_id, status, phase_score, started_at, completed_at FROM phases "
            "WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        )
        artifacts = self.db.query_all(
            "SELECT phase_id, path, summary, source_type, trust_level, validation_status, validation_score "
            "FROM artifacts WHERE run_id = ? AND COALESCE(is_current, 1) = 1 "
            "ORDER BY phase_id, path",
            (run_id,),
        )
        fallbacks = self.db.query_all(
            "SELECT phase_id, message FROM events WHERE run_id = ? AND type = 'phase.llm_fallback' ORDER BY id",
            (run_id,),
        )
        validations = self.db.query_all(
            "SELECT phase_id, message FROM events WHERE run_id = ? AND type = 'phase.validation_failed' ORDER BY id",
            (run_id,),
        )
        crashes = self.db.query_all(
            "SELECT phase_id, message FROM events WHERE run_id = ? AND type = 'phase.crashed' ORDER BY id",
            (run_id,),
        )
        auto_approvals = self.db.query_all(
            "SELECT phase_id, message FROM events WHERE run_id = ? AND type = 'approval.auto_approved' ORDER BY id",
            (run_id,),
        )

        # Index artifacts by phase, classify provenance.
        per_phase_artifacts: dict[str, list[tuple[str, str, int | None]]] = {}
        suspect_files: list[tuple[str, str, str, int]] = []  # (phase, path, reason, severity)
        for row in artifacts:
            phase_id = row["phase_id"]
            path = row["path"]
            source_type = (row["source_type"] or "unknown") if "source_type" in row.keys() else "unknown"
            trust_level = (row["trust_level"] or "medium") if "trust_level" in row.keys() else "medium"
            v_status = (row["validation_status"] or "not_run") if "validation_status" in row.keys() else "not_run"
            v_score_val = row["validation_score"] if "validation_score" in row.keys() else None
            summary = row["summary"] or ""
            if source_type in {"fallback", "stub"} or "via fallback" in summary or "via stub" in summary:
                provenance = "fallback"
                suspect_files.append((phase_id, path, "untrusted fallback (no LLM content)", 100))
            elif source_type == "extra":
                provenance = "extra-llm"
            elif "via mixed" in summary:
                provenance = "mixed"
            else:
                provenance = "llm"
            if v_status == "failed":
                suspect_files.append((phase_id, path, "validation failed", 80))
            elif v_status == "partial":
                suspect_files.append((phase_id, path, f"partial validation (score={v_score_val})", 50))
            if trust_level == "untrusted":
                suspect_files.append((phase_id, path, "trust=untrusted", 90))
            per_phase_artifacts.setdefault(phase_id, []).append((path, provenance, v_score_val))

        # De-dup suspect_files (same phase+path may have triggered multiple
        # reasons); keep the highest-severity reason.
        dedup: dict[tuple[str, str], tuple[str, int]] = {}
        for phase_id, path, reason, severity in suspect_files:
            key = (phase_id, path)
            if key not in dedup or dedup[key][1] < severity:
                dedup[key] = (reason, severity)
        suspect_files_dedup = sorted(
            ((k[0], k[1], v[0], v[1]) for k, v in dedup.items()),
            key=lambda x: -x[3],
        )

        crashed_phase_ids = {row["phase_id"] for row in crashes}
        ready_to_use, ready_reason = _decide_ready_to_use(grade, suspect_files_dedup, crashed_phase_ids)

        # Top risks: collapse fallbacks, crashes, validation failures into
        # human-readable bullets, ranked by impact.
        top_risks = _collect_top_risks(
            fallbacks=fallbacks,
            crashes=crashes,
            validations=validations,
            suspect_files=suspect_files_dedup,
            grade=grade,
        )

        # Recommended next command: prefer rerunning the worst-scoring phase.
        recommended_command = _recommend_next_command(
            phases=phases, project=project, workflow=workflow, crashed_phase_ids=crashed_phase_ids
        )

        owners_by_phase = {str(ph["id"]): str(ph.get("owner", "")) for ph in workflow.get("phases", [])}

        lines: list[str] = []
        # ---- FIRST SCREEN ----
        lines.append("# Autonomous Run Report")
        lines.append("")
        lines.append(f"**Overall Grade:** {grade}  ")
        lines.append(f"**Ready to use:** {'Yes' if ready_to_use else 'Partially' if grade in {'B','C'} else 'No'}  ")
        lines.append(f"**Human review required:** {'No' if ready_to_use else 'Yes'} — {ready_reason}  ")
        lines.append(f"**Executable Evidence:** {_format_execution_line(execution_summary)}  ")
        lines.append(f"**Run id:** `{run_id}`  ")
        lines.append(f"**Generated:** {now_iso()}")
        lines.append("")
        lines.append("## Top Risks")
        lines.append("")
        if top_risks:
            for i, risk in enumerate(top_risks, 1):
                lines.append(f"{i}. {risk}")
        else:
            lines.append("_No risks detected. Every required output is LLM-sourced and passed validation._")
        lines.append("")
        lines.append("## Recommended Next Command")
        lines.append("")
        lines.append("```bash")
        lines.append(recommended_command)
        lines.append("```")
        lines.append("")
        lines.append("## Trust Summary")
        lines.append("")
        lines.append("| Phase | Quality | Score | Files | Provenance |")
        lines.append("|---|---|---|---|---|")
        for p in phases:
            phase_id = p["phase_id"]
            arts = per_phase_artifacts.get(phase_id, [])
            score = p["phase_score"] if "phase_score" in p.keys() else None
            score_str = str(score) if score is not None else "—"
            quality = _quality_label(p["status"], score, phase_id in crashed_phase_ids, arts)
            file_count = len(arts)
            prov_set = sorted({a[1] for a in arts})
            lines.append(
                f"| `{phase_id}` | {quality} | {score_str} | {file_count} | {', '.join(prov_set) or '—'} |"
            )
        lines.append("")

        # ---- DETAIL SECTIONS ----
        lines.append("---")
        lines.append("")
        lines.append(f"**Project:** {project.get('name', project['id'])}  ")
        lines.append(f"**Idea:** {project.get('idea', '(none)')}")
        lines.append("")

        lines.append("## Phase-by-phase")
        lines.append("")
        lines.append("| Phase | Status | Score | Files | Notes |")
        lines.append("|---|---|---|---|---|")
        for p in phases:
            phase_id = p["phase_id"]
            status = p["status"]
            score = p["phase_score"] if "phase_score" in p.keys() else None
            score_str = str(score) if score is not None else "—"
            arts = per_phase_artifacts.get(phase_id, [])
            file_summary = ", ".join(f"{a[0]} ({a[1]})" for a in arts) if arts else "(none)"
            notes_parts: list[str] = []
            if phase_id in crashed_phase_ids:
                notes_parts.append("CRASHED")
            if any(row["phase_id"] == phase_id for row in fallbacks):
                notes_parts.append("LLM fallback")
            phase_validation_files = [
                row["message"].split(" failed format validation:", 1)[0]
                for row in validations if row["phase_id"] == phase_id
            ]
            if phase_validation_files:
                notes_parts.append(f"validation: {', '.join(phase_validation_files)}")
            notes = "; ".join(notes_parts) if notes_parts else ""
            lines.append(f"| `{phase_id}` | {status} | {score_str} | {file_summary} | {notes} |")
        lines.append("")

        if suspect_files_dedup:
            lines.append("## Files needing spot-check (most suspicious first)")
            lines.append("")
            for phase_id, path, reason, _sev in suspect_files_dedup:
                lines.append(f"- `{path}` — phase `{phase_id}` — {reason}")
            lines.append("")

        degraded_phases = sorted(
            {phase_id for phase_id, _, _, _ in suspect_files_dedup} | crashed_phase_ids
        )
        if degraded_phases:
            lines.append("## To re-run a degraded phase manually")
            lines.append("")
            for phase_id in degraded_phases:
                owner = owners_by_phase.get(phase_id) or phase_id
                lines.append(
                    f"```bash\npython3 -m orchestrator.cli run-agent {owner} "
                    f"--project {project['id']}\n```"
                )
            lines.append("")

        if fallbacks:
            lines.append("## LLM fallback details")
            lines.append("")
            for row in fallbacks:
                lines.append(f"- `[{row['phase_id']}]` {row['message']}")
            lines.append("")

        if crashes:
            lines.append("## Phase crashes")
            lines.append("")
            for row in crashes:
                lines.append(f"- `[{row['phase_id']}]` {row['message']}")
            lines.append("")

        if auto_approvals:
            lines.append("## Auto-approved gates")
            lines.append("")
            for row in auto_approvals:
                lines.append(f"- `[{row['phase_id']}]` {row['message']}")
            lines.append("")

        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return report_path

    def _compute_delivery_grade(self, run_id: str) -> tuple[str, str]:
        """Roll phase scores + provenance + executable evidence up into an A-F
        delivery grade.

        Grade rubric (artifacts):
          A — every phase ≥ 85, source='llm', no fallback artifacts, no crashes
          B — every phase ≥ 70, at most 1 phase scored 60-69 OR ≤ 1 fallback file
          C — multiple degraded phases or 2-4 fallback files; main path complete
          D — failed_unblocked phase exists or > 4 fallback files
          F — failed_blocking phase, or > half of phases under 50, or run had crashes

        Then **C2 caps** based on executable evidence (when present):
          install_failed → max D
          build_failed   → max D
          test_failed    → max C
          tests_missing + project_type detected → max C-
          no project type detected → max C (no executable evidence at all)
        """
        phases = self.db.query_all(
            "SELECT phase_id, status, phase_score FROM phases WHERE run_id = ? ORDER BY sequence",
            (run_id,),
        )
        artifacts = self.db.query_all(
            "SELECT path, source_type, validation_score FROM artifacts "
            "WHERE run_id = ? AND COALESCE(is_current, 1) = 1",
            (run_id,),
        )
        crashes = self.db.query_all(
            "SELECT id FROM events WHERE run_id = ? AND type = 'phase.crashed'",
            (run_id,),
        )
        fallback_count = sum(
            1 for r in artifacts
            if "source_type" in r.keys() and r["source_type"] in {"fallback", "stub"}
        )
        scores = [(r["phase_score"] or 0) for r in phases]
        failed_phases = sum(1 for r in phases if r["status"] == "failed")
        crash_count = len(list(crashes))
        if not scores:
            return "F", "no phases recorded"
        min_score = min(scores)
        below_50 = sum(1 for s in scores if s < 50)
        below_70 = sum(1 for s in scores if s < 70)

        # First pass: artifact-based grade.
        if crash_count > 0 or (failed_phases > 0 and below_50 > len(scores) / 2):
            base_grade, reason = "F", f"{failed_phases} failed phase(s), {crash_count} crash(es)"
        elif failed_phases > 0:
            base_grade, reason = "D", f"{failed_phases} failed phase(s); {fallback_count} fallback file(s)"
        elif fallback_count > 4 or below_50 > 0:
            base_grade, reason = "D", f"{fallback_count} fallback file(s); min phase score {min_score}"
        elif fallback_count >= 2 or below_70 > 1:
            base_grade, reason = "C", f"{fallback_count} fallback file(s); {below_70} phase(s) below 70"
        elif fallback_count == 1 or below_70 == 1:
            base_grade, reason = "B", f"{fallback_count} fallback file(s); min phase score {min_score}"
        elif min_score >= 85:
            base_grade, reason = "A", f"all phases ≥ 85; {fallback_count} fallback files"
        else:
            base_grade, reason = "B", f"clean run, min phase score {min_score}"

        # Second pass: executable-evidence cap.
        # Find the latest run's project_path through the runs row.
        run = self.db.query_one("SELECT project_id FROM runs WHERE id = ?", (run_id,))
        if run:
            project = self.db.query_one(
                "SELECT path FROM projects WHERE id = ?", (run["project_id"],)
            )
            if project:
                summary = self._read_execution_evidence(Path(project["path"]), run_id)
                if summary is not None:
                    install = summary.get("install")
                    build = summary.get("build")
                    test = summary.get("test")
                    if install is not None and not install.get("passed"):
                        return _cap_grade(base_grade, "D"), reason + " | install failed"
                    if build is not None and not build.get("passed"):
                        return _cap_grade(base_grade, "D"), reason + " | build failed"
                    if test is not None and not test.get("passed"):
                        return _cap_grade(base_grade, "C"), reason + " | tests failed"
                    if test is None:
                        return _cap_grade(base_grade, "C"), reason + " | tests not run"
                    # All execution checks passed → no cap, base_grade stands.
                else:
                    # No execution evidence at all and there IS code expected
                    # → cap to C since we can't prove it runs.
                    impl_artifacts = [
                        r for r in artifacts
                        if r["path"].startswith(("apps/", "packages/", "src/"))
                    ]
                    if impl_artifacts:
                        return _cap_grade(base_grade, "C"), reason + " | no executable evidence"
        return base_grade, reason

    def _get_validator(self) -> Validator:
        if self._validator is None:
            self._validator = load_contracts()
        return self._validator

    def _gather_upstream_inputs(
        self,
        *,
        run_id: str,
        current_phase: str,
        project_path: Path,
    ) -> dict[str, str]:
        """Read every artifact written by phases earlier in this run.

        Returns a mapping {relative_path: file_content}. AgentRunner's prompt
        renderer will interleave these as named inputs so the LLM can compose
        on top of them. Files exceeding ``MAX_INPUT_BYTES_PER_FILE`` are
        truncated with a marker so we never blow the model context window with
        a single oversize file.

        Untrusted upstream files (B1: degraded fallbacks) are tagged with a
        ``[UNTRUSTED ...]`` prefix in the value so the downstream LLM is told
        directly not to treat their content as authoritative.
        """
        rows = self.db.query_all(
            "SELECT phase_id, path, trust_level FROM artifacts WHERE run_id = ? AND phase_id != ? "
            "ORDER BY id",
            (run_id, current_phase),
        )
        inputs: dict[str, str] = {}
        seen: set[str] = set()
        for row in rows:
            relative = row["path"]
            if relative in seen:
                continue
            seen.add(relative)
            target = project_path / relative
            try:
                content = target.read_text(encoding="utf-8")
            except (FileNotFoundError, OSError, UnicodeDecodeError):
                continue
            if len(content) > _MAX_INPUT_BYTES_PER_FILE:
                content = content[:_MAX_INPUT_BYTES_PER_FILE] + (
                    f"\n\n[truncated: {len(content) - _MAX_INPUT_BYTES_PER_FILE} more bytes]\n"
                )
            trust_level = (row["trust_level"] or "medium") if "trust_level" in row.keys() else "medium"
            if trust_level == "untrusted":
                content = (
                    f"[UNTRUSTED — upstream phase did not produce real content. "
                    f"Do NOT take the body below as authoritative requirements. "
                    f"Treat it as a placeholder and proceed best-effort, flagging "
                    f"any decisions that depend on this file as unverified.]\n\n"
                    + content
                )
            inputs[relative] = content
        return inputs

    # ------------------------------------------------------------------
    # C1c — implementation build/test/fix loop
    # ------------------------------------------------------------------

    def _run_implementation_fix_loop(
        self,
        *,
        project: dict[str, Any],
        project_path: Path,
        run_id: str,
        phase_id: str,
        phase_config: dict[str, Any],
        outputs: list[str],
        initial_files: dict[str, str],
    ) -> ExecutionEvidence | None:
        """Run install/build/test against the just-written implementation
        files. On failure, ask the developer agent to fix, write the new
        files (replacing prior versions; ``register`` keeps history), and
        re-run. Bounded by ``LOCALAGENTS_MAX_FIX_ATTEMPTS`` (default 3).
        """
        max_attempts = int(os.environ.get("LOCALAGENTS_MAX_FIX_ATTEMPTS", "3"))
        max_attempts = max(1, min(max_attempts, 5))

        project_type = detect_project_type(project_path)
        if project_type is None:
            _progress(f"[{phase_id}] no recognized project type — skipping execution checks")
            return None
        executor = Executor()

        owner = phase_config.get("owner") or "developer"
        attempt_history: list[dict[str, Any]] = []
        current_files = dict(initial_files)

        for attempt in range(1, max_attempts + 1):
            _progress(f"[{phase_id}] execution attempt {attempt}/{max_attempts} (project_type={project_type.name})")
            evidence = executor.run_phase_checks(project_path, project_type)
            attempt_history.append({
                "attempt": attempt,
                "install": _command_summary(evidence.install),
                "build": _command_summary(evidence.build),
                "test": _command_summary(evidence.test),
                "passed": evidence.overall_passed,
                "notes": list(evidence.notes),
            })
            self.events.emit(
                event_type="phase.execution",
                project_id=project["id"],
                run_id=run_id,
                phase_id=phase_id,
                message=(
                    f"attempt {attempt}: install={evidence.install_status}, "
                    f"build={evidence.build_status}, test={evidence.test_status}"
                ),
                payload={"attempt": attempt, "summary": attempt_history[-1]},
            )

            if evidence.overall_passed:
                _progress(f"[{phase_id}] execution passed on attempt {attempt}")
                evidence_with_history = _attach_history(evidence, attempt_history)
                return evidence_with_history

            if attempt >= max_attempts:
                _progress(f"[{phase_id}] execution failed after {max_attempts} attempt(s) — giving up")
                return _attach_history(evidence, attempt_history)

            # Build a repair prompt with the failures and ask developer to fix.
            try:
                repaired_files = self._request_repair_files(
                    owner=str(owner),
                    project=project,
                    project_path=project_path,
                    run_id=run_id,
                    phase_id=phase_id,
                    outputs=outputs,
                    current_files=current_files,
                    failed=evidence.all_failed(),
                )
            except Exception as exc:  # noqa: BLE001
                _progress(
                    f"[{phase_id}] repair attempt {attempt} could not call developer agent: "
                    f"{type(exc).__name__}: {str(exc)[:140]}"
                )
                return _attach_history(evidence, attempt_history)

            if not repaired_files:
                _progress(f"[{phase_id}] developer returned no files on repair attempt {attempt} — giving up")
                return _attach_history(evidence, attempt_history)

            # Apply the repaired files (path-safe), register with provenance.
            self._apply_repaired_files(
                project=project,
                project_path=project_path,
                run_id=run_id,
                phase_id=phase_id,
                files=repaired_files,
                attempt=attempt,
            )
            current_files = {**current_files, **repaired_files}
        return None

    def _request_repair_files(
        self,
        *,
        owner: str,
        project: dict[str, Any],
        project_path: Path,
        run_id: str,
        phase_id: str,
        outputs: list[str],
        current_files: dict[str, str],
        failed: list,
    ) -> dict[str, str]:
        from orchestrator.agents.base import AgentContext

        registry = self._get_agent_registry()
        agent_config = registry.require(owner)
        runner = self._get_agent_runner()

        # Build a focused repair context: failure logs + the files we just
        # wrote (truncated to keep prompt manageable).
        failure_excerpt = _format_failures(failed)
        files_excerpt: dict[str, str] = {}
        for path, content in current_files.items():
            files_excerpt[f"current::{path}"] = content[:_MAX_INPUT_BYTES_PER_FILE]

        instructions = (
            f"You are repairing the implementation phase for project '{project.get('idea', '')}'. "
            "The previous attempt wrote source files but they fail to install / build / pass tests. "
            "Read the failure logs below, decide what's wrong, and return ONLY the files you "
            "want to overwrite (no unchanged files). You may change implementation source and "
            "test files. You MUST NOT modify PRD, architecture, or acceptance-criteria documents.\n\n"
            "Return JSON with `files: {path: full_new_content}` covering exactly the files that "
            "need to change. The orchestrator will overwrite each path in place."
        )
        context = AgentContext(
            project_id=project["id"],
            run_id=run_id,
            task_id=phase_id,
            project_path=project_path,
            idea=project.get("idea"),
            instructions=instructions,
            output_paths=list(outputs),
            inputs={"failure_logs": failure_excerpt, **files_excerpt},
        )
        result = runner.run_task(agent_config, context)
        return dict(result.files or {})

    def _apply_repaired_files(
        self,
        *,
        project: dict[str, Any],
        project_path: Path,
        run_id: str,
        phase_id: str,
        files: dict[str, str],
        attempt: int,
    ) -> None:
        for relative, content in files.items():
            if not _is_safe_relative_path(relative):
                _progress(f"[{phase_id}] repair refused unsafe path: {relative}")
                continue
            target = project_path / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            self.artifacts.register(
                project_id=project["id"],
                run_id=run_id,
                phase_id=phase_id,
                path=relative,
                kind=_artifact_kind(relative),
                summary=f"Repaired {relative} in attempt {attempt}.",
                source_type="repaired",
                trust_level="medium",
                validation_status="not_run",
                repair_attempt=attempt,
            )

    def _read_execution_evidence(self, project_path: Path, run_id: str) -> dict[str, Any] | None:
        path = project_path / ".agent" / "runs" / run_id / "execution" / "implementation-checks.json"
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def _record_execution_evidence(
        self,
        *,
        project: dict[str, Any],
        project_path: Path,
        run_id: str,
        phase_id: str,
        evidence: ExecutionEvidence,
    ) -> None:
        out_dir = project_path / ".agent" / "runs" / run_id / "execution"
        out_dir.mkdir(parents=True, exist_ok=True)
        json_path = out_dir / "implementation-checks.json"
        payload = {
            "project_type": evidence.project_type,
            "install": _command_summary(evidence.install),
            "build": _command_summary(evidence.build),
            "test": _command_summary(evidence.test),
            "overall_passed": evidence.overall_passed,
            "notes": list(evidence.notes),
            "attempts": getattr(evidence, "_attempts", None) or [],
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.artifacts.register(
            project_id=project["id"],
            run_id=run_id,
            phase_id=phase_id,
            path=str(json_path.relative_to(project_path)),
            kind="json",
            summary=(
                f"Execution evidence for {phase_id}: install={evidence.install_status}, "
                f"build={evidence.build_status}, test={evidence.test_status}."
            ),
            source_type="executor",
            trust_level="high" if evidence.overall_passed else "low",
            validation_status="passed" if evidence.overall_passed else "failed",
            validation_score=100 if evidence.overall_passed else 30,
        )

    def _get_agent_registry(self):
        if self._agent_registry is not None:
            return self._agent_registry
        if self.agents_dir is None:
            raise RuntimeError("agents_dir not configured on WorkflowEngine")
        from orchestrator.core.agent_registry import AgentRegistry

        self._agent_registry = AgentRegistry(self.agents_dir)
        return self._agent_registry

    def _get_agent_runner(self):
        if self._agent_runner is not None:
            return self._agent_runner
        from orchestrator.agents.base import AgentRunner
        from orchestrator.core.cost_tracker import CostTracker

        self._agent_runner = AgentRunner(cost_tracker=CostTracker(self.db))
        return self._agent_runner

    # --- end LLM-driven phase generation -----------------------------------

    def _complete_phase(self, project_id: str, run_id: str, phase_id: str) -> None:
        now = now_iso()
        self.db.execute(
            """
            UPDATE phases SET status = 'completed', completed_at = ?, updated_at = ?
            WHERE run_id = ? AND phase_id = ?
            """,
            (now, now, run_id, phase_id),
        )
        self.tasks.set_phase_tasks_status(run_id, phase_id, "completed")
        self.events.emit(
            event_type="phase.completed",
            project_id=project_id,
            run_id=run_id,
            phase_id=phase_id,
            message=f"Completed phase {phase_id}.",
        )

    def _next_incomplete_phase(self, run_id: str) -> dict[str, Any] | None:
        # 'failed' is also a terminal state — autonomous mode marks crashed
        # phases failed and we want to move on, not retry them on the next
        # _advance() iteration.
        row = self.db.query_one(
            """
            SELECT * FROM phases
            WHERE run_id = ? AND status NOT IN ('completed', 'skipped', 'failed')
            ORDER BY sequence ASC
            LIMIT 1
            """,
            (run_id,),
        )
        return dict(row) if row else None

    def _first_incomplete_dependency(self, run_id: str, dependencies: list[str]) -> str | None:
        # In autonomous mode, treat 'failed' upstream as a degraded-but-done
        # signal and let downstream proceed (it will work with stub artifacts).
        # In manual mode, failed upstream blocks (forces human decision).
        terminal_states = {"completed"}
        if _is_autonomous():
            terminal_states.update({"failed", "skipped"})
        for dependency in dependencies:
            row = self.db.query_one(
                "SELECT status FROM phases WHERE run_id = ? AND phase_id = ?",
                (run_id, dependency),
            )
            if not row or row["status"] not in terminal_states:
                return dependency
        return None

    def _has_approved_gate(self, run_id: str, phase_id: str, gate: str) -> bool:
        row = self.db.query_one(
            """
            SELECT id FROM approvals
            WHERE run_id = ? AND phase_id = ? AND gate = ? AND status = 'approved'
            LIMIT 1
            """,
            (run_id, phase_id, gate),
        )
        return bool(row)


# Per-file content cap when injecting upstream artifacts into a downstream
# phase's prompt. Sized to keep total input under ~150KB even if 5+ phases
# each produce sizeable docs. Truncated content is marked clearly so the LLM
# knows it didn't get the whole picture.
_MAX_INPUT_BYTES_PER_FILE = 30_000

# Run-lock staleness threshold (C0d). After this many seconds without a
# heartbeat we assume the lock-holder process died and let another caller
# reclaim the lock.
_LOCK_STALE_SECONDS = 600


def _phase_instructions(phase_id: str, owner: str, idea: str, upstream: dict[str, str]) -> str:
    """Phase-specific instruction text, telling the LLM exactly what role it's
    playing, what came before, and what consistency obligations it has.

    The generic fallback is used for phases not in the table (custom workflows
    or future phases). The per-phase texts assume the standard
    ``software_project`` workflow's phase ids.
    """
    upstream_list = ", ".join(sorted(upstream)) if upstream else "(none — you are the first phase)"
    table = {
        "intake": (
            f"You are the lead agent kicking off project '{idea}'. "
            "Produce a project brief covering the problem, target user, success criteria, and major risks. "
            "This brief anchors the entire downstream pipeline — be concrete, not generic."
        ),
        "research": (
            f"You are the product manager researching '{idea}'. "
            "Survey alternatives, user pain, market context, and what differentiates a worthwhile MVP. "
            "Cite assumptions explicitly. The PRD phase will build on this research."
        ),
        "prd": (
            f"You are the product manager writing the PRD and acceptance criteria for '{idea}'. "
            "Use the upstream research and project brief as the source of truth — do not contradict them. "
            "Separate MVP scope from future ideas. Acceptance criteria must be testable."
        ),
        "design": (
            f"You are the UI designer for '{idea}'. "
            "Translate the PRD's MVP scope into concrete user flows, design system notes, and component specs. "
            "Reference PRD acceptance criteria by name to make the trace explicit."
        ),
        "architecture": (
            f"You are the architect for '{idea}'. "
            "Design the system architecture, API contract (OpenAPI 3 YAML), database schema, and an executable task graph. "
            "Every API endpoint and DB table must trace back to a PRD requirement or design component. "
            "The OpenAPI YAML must be syntactically valid; the tasks JSON must parse and contain id+title+phase fields."
        ),
        "implementation": (
            f"You are the full-stack developer implementing '{idea}'. "
            "Read the architecture, OpenAPI contract, database schema, and generated-tasks.json. "
            "Produce the actual application code — return EVERY source file you create in the `files` field "
            "(e.g. apps/web/index.html, apps/web/main.js, apps/api/server.py, package.json, etc). "
            "Also produce the implementation-summary.md describing what you built. Keep code minimal but functional."
        ),
        "qa": (
            f"You are QA for '{idea}'. "
            "Read the acceptance criteria, architecture, and any implementation files. "
            "Write a test plan that covers each acceptance criterion, and write test results based on what's actually verifiable from the code. "
            "If implementation is missing or stubbed, document that clearly in the test plan and mark blocked tests."
        ),
        "review": (
            f"You are the reviewer for '{idea}'. "
            "Read PRD, design, architecture, implementation summary, and QA results. "
            "Identify concrete inconsistencies, gaps, or risks — name specific files and lines where possible. "
            "Status must be approve/changes-requested/reject. Be specific, avoid platitudes."
        ),
        "merge": (
            f"You are the lead summarizing the completed run for '{idea}'. "
            "Produce a final report listing what each phase delivered, what was approved, what was deferred, "
            "and the recommended next 1-3 actions for the human owner. Reference upstream artifacts by path."
        ),
    }
    body = table.get(phase_id) or (
        f"You are the {owner} agent running the {phase_id} phase for project '{idea}'. "
        "Produce the required outputs based on the upstream artifacts."
    )
    return f"{body}\n\nUpstream artifacts available to you: {upstream_list}"


def _command_summary(cmd) -> dict[str, Any] | None:
    if cmd is None:
        return None
    return {
        "name": cmd.name,
        "argv": list(cmd.argv),
        "exit_code": cmd.exit_code,
        "duration_ms": cmd.duration_ms,
        "passed": cmd.passed,
        "stdout_tail": cmd.stdout_tail,
        "stderr_tail": cmd.stderr_tail,
    }


def _attach_history(evidence: ExecutionEvidence, history: list[dict[str, Any]]) -> ExecutionEvidence:
    """ExecutionEvidence is frozen — but we can stash attempt history under
    a non-field attribute by going through ``object.__setattr__``."""
    object.__setattr__(evidence, "_attempts", history)
    return evidence


def _format_failures(failed) -> str:
    out_lines: list[str] = []
    for cmd in failed:
        out_lines.append(
            f"=== {cmd.name} ({' '.join(cmd.argv)}) → exit {cmd.exit_code} in {cmd.duration_ms}ms ==="
        )
        if cmd.stderr_tail:
            out_lines.append("stderr (tail):")
            out_lines.append(cmd.stderr_tail)
        if cmd.stdout_tail:
            out_lines.append("stdout (tail):")
            out_lines.append(cmd.stdout_tail)
    return "\n".join(out_lines)


_GRADE_ORDER = {"A": 5, "B": 4, "C": 3, "D": 2, "F": 1}


def _cap_grade(current: str, ceiling: str) -> str:
    """Return the lower of (current, ceiling) using the A-F ordering."""
    cur = _GRADE_ORDER.get(current, 0)
    cap = _GRADE_ORDER.get(ceiling, 0)
    if cap < cur:
        return ceiling
    return current


def _format_execution_line(summary: dict[str, Any] | None) -> str:
    """One-line summary of executor outcomes for the first-screen header."""
    if not summary:
        return "missing — no recognized project manifest, executor did not run"
    parts: list[str] = []
    for key in ("install", "build", "test"):
        cmd = summary.get(key)
        if cmd is None:
            parts.append(f"{key}: missing")
            continue
        if cmd.get("passed"):
            parts.append(f"{key}: passed")
        else:
            parts.append(f"{key}: failed (exit {cmd.get('exit_code')})")
    attempts = summary.get("attempts") or []
    if len(attempts) > 1:
        parts.append(f"after {len(attempts)} attempt(s)")
    return ", ".join(parts)


def _decide_ready_to_use(
    grade: str,
    suspect_files: list[tuple[str, str, str, int]],
    crashed_phases: set[str],
) -> tuple[bool, str]:
    """Boolean + one-line reason."""
    if crashed_phases:
        return False, f"{len(crashed_phases)} phase(s) crashed"
    if grade in {"D", "F"}:
        return False, f"delivery grade {grade}; major degradation"
    high_severity = [s for s in suspect_files if s[3] >= 80]
    if high_severity:
        return False, f"{len(high_severity)} high-risk file(s) need review"
    if grade == "C":
        return False, "delivery grade C; minor degradation across multiple phases"
    if suspect_files:
        return False, f"{len(suspect_files)} file(s) flagged for review"
    return True, "all phases produced LLM content and passed validation"


def _collect_top_risks(
    *,
    fallbacks: list,
    crashes: list,
    validations: list,
    suspect_files: list[tuple[str, str, str, int]],
    grade: str,
) -> list[str]:
    """Build up to 5 short, ranked risk bullets."""
    risks: list[tuple[int, str]] = []
    for row in crashes:
        risks.append((100, f"`{row['phase_id']}` phase CRASHED — {row['message'][:120]}"))
    for row in fallbacks:
        risks.append((90, f"`{row['phase_id']}` fell back to untrusted content — {row['message'][:120]}"))
    # Per-validation risks (one per offending file, capped to top 5).
    for row in validations[:5]:
        risks.append((70, f"`{row['phase_id']}` validation failed — {row['message'][:140]}"))
    # Suspect files not already covered.
    for phase_id, path, reason, severity in suspect_files[:3]:
        risks.append((severity - 1, f"`{path}` ({reason}) — phase `{phase_id}`"))
    if grade in {"D", "F"} and not risks:
        risks.append((100, f"delivery grade is {grade} but no specific risk recorded; inspect run logs"))
    risks.sort(key=lambda x: -x[0])
    return [r[1] for r in risks[:5]]


def _recommend_next_command(
    *,
    phases: list,
    project: dict[str, Any],
    workflow: dict[str, Any],
    crashed_phase_ids: set[str],
) -> str:
    """One-line CLI command the user should consider running first."""
    project_id = project.get("id", "")
    owners_by_phase = {str(ph["id"]): str(ph.get("owner", "")) for ph in workflow.get("phases", [])}
    # Crashed phases first.
    if crashed_phase_ids:
        phase_id = sorted(crashed_phase_ids)[0]
        owner = owners_by_phase.get(phase_id) or phase_id
        return (
            f"python3 -m orchestrator.cli run-agent {owner} --project {project_id}  "
            f"# rerun crashed `{phase_id}`"
        )
    # Otherwise: weakest phase by score.
    weakest: tuple[str, int] | None = None
    for p in phases:
        score = p["phase_score"] if "phase_score" in p.keys() else None
        if score is None:
            continue
        if weakest is None or score < weakest[1]:
            weakest = (p["phase_id"], score)
    if weakest and weakest[1] < 75:
        owner = owners_by_phase.get(weakest[0]) or weakest[0]
        return (
            f"python3 -m orchestrator.cli run-agent {owner} --project {project_id}  "
            f"# rerun weakest phase `{weakest[0]}` (score={weakest[1]})"
        )
    return f"python3 -m orchestrator.cli diagnose --project {project_id}  # full status"


def _quality_label(
    status: str,
    score: int | None,
    crashed: bool,
    artifacts: list[tuple[str, str, int | None]],
) -> str:
    if crashed:
        return "🛑 crashed"
    if status == "failed":
        return "❌ failed"
    if not artifacts:
        return "⚠ empty"
    has_fallback = any(a[1] == "fallback" for a in artifacts)
    if has_fallback:
        return "⚠ degraded"
    if score is None:
        return "✓ ok"
    if score >= 85:
        return "✅ verified"
    if score >= 70:
        return "✓ ok"
    if score >= 50:
        return "⚠ partial"
    return "⚠ weak"


def _is_autonomous() -> bool:
    """Autonomous mode: auto-approve gates, isolate phase failures, write
    a final-run-status report. Enabled by ``LOCALAGENTS_AUTONOMOUS=1`` (set
    by ``cli run --autonomous``)."""
    return os.environ.get("LOCALAGENTS_AUTONOMOUS") == "1"


def _is_safe_relative_path(candidate: str) -> bool:
    """Reject paths that are absolute or attempt to escape the project root."""
    if not candidate or not isinstance(candidate, str):
        return False
    p = Path(candidate)
    if p.is_absolute():
        return False
    parts = p.parts
    if any(part == ".." for part in parts):
        return False
    # Reject paths starting with ~ (home directory) or empty segments.
    if candidate.startswith("~") or candidate.startswith("/"):
        return False
    return True


def _validate_artifact(path: Path, content: str) -> str | None:
    """Return an error string if the file's content fails format validation.

    Only enforces structural validity for files whose extension implies a
    parseable format (.json, .yaml, .yml). Markdown and other freeform files
    always pass. Returns None on success.
    """
    suffix = path.suffix.lower()
    try:
        if suffix == ".json":
            json.loads(content)
        elif suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore[import-not-found]
            except ImportError:
                # PyYAML is optional; if not installed we skip yaml validation
                # rather than reporting a false negative.
                return None
            yaml.safe_load(content)
    except Exception as exc:  # noqa: BLE001 — surface any parser error to user
        message = str(exc)
        return message[:200] if len(message) > 200 else message
    return None


def _progress(message: str) -> None:
    """Print a progress line to stderr unless silenced.

    Set LOCALAGENTS_QUIET=1 to suppress (handy for tests). Always flushes so
    the user sees phase-by-phase progress during long LLM-driven runs.
    """
    if os.environ.get("LOCALAGENTS_QUIET") == "1":
        return
    timestamp = time.strftime("%H:%M:%S")
    print(f"  {timestamp}  {message}", file=sys.stderr, flush=True)


def _name_from_idea(idea: str) -> str:
    compact = " ".join(idea.split())
    return compact[:40] or "Untitled Project"


def _initialize_project_dir(project_path: Path, idea: str, project_id: str, name: str) -> None:
    for relative in [
        ".agent/runs",
        ".agent/artifacts",
        ".agent/tasks",
        ".agent/memory",
        ".agent/locks",
        "docs/product",
        "docs/design",
        "docs/architecture/adr",
        "docs/qa",
        "docs/review",
        "apps/web",
        "apps/api",
        "packages",
        "tests",
    ]:
        (project_path / relative).mkdir(parents=True, exist_ok=True)
    (project_path / ".agent/project.yaml").write_text(
        f"""id: {project_id}
name: "{name}"
status: created
""",
        encoding="utf-8",
    )
    (project_path / ".agent/project-brief.md").write_text(
        f"""# Project Brief

{idea}
""",
        encoding="utf-8",
    )
