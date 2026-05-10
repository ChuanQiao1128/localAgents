from __future__ import annotations

from pathlib import Path

from orchestrator.core.ids import now_iso, short_id
from orchestrator.db import Database


class MemoryStore:
    def __init__(self, db: Database):
        self.db = db

    def write(
        self,
        *,
        project_id: str,
        project_path: Path,
        scope: str,
        key: str,
        content: str,
    ) -> str:
        relative = f".agent/memory/{key}.md"
        target = project_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content.rstrip() + "\n", encoding="utf-8")
        now = now_iso()
        self.db.execute(
            """
            INSERT INTO memories (id, project_id, scope, key, path, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, scope, key)
            DO UPDATE SET path = excluded.path, updated_at = excluded.updated_at
            """,
            (short_id("memory"), project_id, scope, key, relative, now, now),
        )
        return relative

    def append(
        self,
        *,
        project_id: str,
        project_path: Path,
        scope: str,
        key: str,
        content: str,
    ) -> str:
        relative = f".agent/memory/{key}.md"
        target = project_path / relative
        existing = target.read_text(encoding="utf-8") if target.exists() else ""
        return self.write(
            project_id=project_id,
            project_path=project_path,
            scope=scope,
            key=key,
            content=(existing.rstrip() + "\n\n" + content).strip(),
        )

    def list_for_project(self, project_id: str) -> list[dict[str, str]]:
        rows = self.db.query_all(
            "SELECT * FROM memories WHERE project_id = ? ORDER BY scope, key",
            (project_id,),
        )
        return [dict(row) for row in rows]

