from __future__ import annotations

import json
from typing import Any

from orchestrator.core.ids import now_iso
from orchestrator.db import Database


class TaskStore:
    def __init__(self, db: Database):
        self.db = db

    def create_phase_task(
        self,
        *,
        project_id: str,
        run_id: str,
        phase: dict[str, Any],
        sequence: int,
    ) -> str:
        phase_id = str(phase["id"])
        task_id = f"{run_id}:{phase_id.upper()}-{sequence + 1:03d}"
        existing = self.db.query_one("SELECT id FROM tasks WHERE id = ?", (task_id,))
        if existing:
            return task_id
        outputs = list(phase.get("output") or [])
        now = now_iso()
        self.db.execute(
            """
            INSERT INTO tasks (
                id, project_id, run_id, phase_id, title, description, owner, status,
                priority, allowed_paths, inputs, outputs, acceptance_criteria,
                test_commands, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                project_id,
                run_id,
                phase_id,
                f"Run {phase_id} phase",
                f"Execute the {phase_id} workflow phase and produce required outputs.",
                str(phase.get("owner", "lead")),
                "pending",
                "medium",
                json.dumps(_allowed_paths_for_phase(phase_id)),
                json.dumps(list(phase.get("depends_on") or [])),
                json.dumps(outputs),
                json.dumps(_acceptance_for_phase(phase_id, outputs)),
                json.dumps(_test_commands_for_phase(phase_id)),
                now,
                now,
            ),
        )
        return task_id

    def set_phase_tasks_status(self, run_id: str, phase_id: str, status: str) -> None:
        self.db.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE run_id = ? AND phase_id = ?",
            (status, now_iso(), run_id, phase_id),
        )

    def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM tasks WHERE run_id = ? ORDER BY created_at, id",
            (run_id,),
        )
        return [dict(row) for row in rows]


def _allowed_paths_for_phase(phase_id: str) -> list[str]:
    mapping = {
        "intake": [".agent/**"],
        "research": ["docs/product/**", ".agent/artifacts/research/**"],
        "prd": ["docs/product/**"],
        "design": ["docs/design/**"],
        "architecture": ["docs/architecture/**", ".agent/tasks/**"],
        "implementation": ["apps/**", "packages/**", "tests/**"],
        "qa": ["docs/qa/**", "tests/**"],
        "review": ["docs/review/**"],
        "merge": [".agent/runs/**"],
    }
    return mapping.get(phase_id, ["docs/**"])


def _acceptance_for_phase(phase_id: str, outputs: list[str]) -> list[str]:
    if outputs:
        return [f"Artifact exists: {path}" for path in outputs]
    return [f"{phase_id} phase completes without errors"]


def _test_commands_for_phase(phase_id: str) -> list[str]:
    if phase_id in {"implementation", "qa", "review"}:
        return ["python3 -m unittest discover -s tests"]
    return []
