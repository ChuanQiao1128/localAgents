from __future__ import annotations

from dataclasses import dataclass

from orchestrator.core.ids import now_iso, short_id
from orchestrator.db import Database


class LockConflictError(RuntimeError):
    pass


@dataclass(frozen=True)
class PathLock:
    id: str
    project_id: str
    run_id: str | None
    task_id: str
    owner: str
    path_pattern: str
    status: str


class PathLocker:
    def __init__(self, db: Database):
        self.db = db

    def acquire(
        self,
        *,
        project_id: str,
        task_id: str,
        owner: str,
        path_patterns: list[str],
        run_id: str | None = None,
    ) -> list[PathLock]:
        active = self.list_active(project_id)
        for requested in path_patterns:
            for existing in active:
                if existing.task_id != task_id and patterns_conflict(requested, existing.path_pattern):
                    raise LockConflictError(
                        f"{requested} conflicts with {existing.path_pattern} held by {existing.task_id}"
                    )
        locks: list[PathLock] = []
        for pattern in path_patterns:
            lock = PathLock(
                id=short_id("lock"),
                project_id=project_id,
                run_id=run_id,
                task_id=task_id,
                owner=owner,
                path_pattern=pattern,
                status="active",
            )
            self.db.execute(
                """
                INSERT INTO file_locks (
                    id, project_id, run_id, task_id, owner, path_pattern, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    lock.id,
                    lock.project_id,
                    lock.run_id,
                    lock.task_id,
                    lock.owner,
                    lock.path_pattern,
                    lock.status,
                    now_iso(),
                ),
            )
            locks.append(lock)
        return locks

    def release_task(self, project_id: str, task_id: str) -> None:
        self.db.execute(
            """
            UPDATE file_locks
            SET status = 'released', released_at = ?
            WHERE project_id = ? AND task_id = ? AND status = 'active'
            """,
            (now_iso(), project_id, task_id),
        )

    def list_active(self, project_id: str) -> list[PathLock]:
        rows = self.db.query_all(
            """
            SELECT * FROM file_locks
            WHERE project_id = ? AND status = 'active'
            ORDER BY created_at ASC
            """,
            (project_id,),
        )
        return [
            PathLock(
                id=row["id"],
                project_id=row["project_id"],
                run_id=row["run_id"],
                task_id=row["task_id"],
                owner=row["owner"],
                path_pattern=row["path_pattern"],
                status=row["status"],
            )
            for row in rows
        ]


def patterns_conflict(left: str, right: str) -> bool:
    if left == right:
        return True
    left_prefix = _static_prefix(left)
    right_prefix = _static_prefix(right)
    if not left_prefix or not right_prefix:
        return True
    return left_prefix.startswith(right_prefix) or right_prefix.startswith(left_prefix)


def _static_prefix(pattern: str) -> str:
    wildcard_at = len(pattern)
    for marker in ["*", "?", "["]:
        index = pattern.find(marker)
        if index != -1:
            wildcard_at = min(wildcard_at, index)
    prefix = pattern[:wildcard_at]
    if "/" in prefix:
        return prefix.rsplit("/", 1)[0].rstrip("/") + "/"
    return prefix

