from __future__ import annotations

import json
from typing import Any

from orchestrator.core.ids import now_iso
from orchestrator.db import Database


class EventBus:
    def __init__(self, db: Database):
        self.db = db

    def emit(
        self,
        *,
        event_type: str,
        message: str,
        project_id: str | None = None,
        run_id: str | None = None,
        phase_id: str | None = None,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO events (
                project_id, run_id, phase_id, task_id, type, message, payload, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                run_id,
                phase_id,
                task_id,
                event_type,
                message,
                json.dumps(payload or {}, ensure_ascii=False),
                now_iso(),
            ),
        )

    def list_for_run(self, run_id: str) -> list[dict[str, Any]]:
        rows = self.db.query_all(
            "SELECT * FROM events WHERE run_id = ? ORDER BY id ASC",
            (run_id,),
        )
        return [dict(row) for row in rows]

