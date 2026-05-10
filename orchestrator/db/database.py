from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Iterable


class Database:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        db = self.connect()
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def initialize(self) -> None:
        with self.connection() as db:
            db.executescript(SCHEMA)
            _apply_migrations(db)

    def execute(self, sql: str, params: Iterable[Any] = ()) -> None:
        with self.connection() as db:
            db.execute(sql, tuple(params))

    def query_all(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self.connection() as db:
            return list(db.execute(sql, tuple(params)))

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        rows = self.query_all(sql, params)
        return rows[0] if rows else None


SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    idea TEXT NOT NULL,
    path TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    workflow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_phase TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS phases (
    id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    phase_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    gate TEXT,
    depends_on TEXT NOT NULL DEFAULT '[]',
    sequence INTEGER NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    updated_at TEXT NOT NULL,
    UNIQUE(run_id, phase_id)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    phase_id TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    owner TEXT NOT NULL,
    status TEXT NOT NULL,
    priority TEXT NOT NULL,
    allowed_paths TEXT NOT NULL DEFAULT '[]',
    inputs TEXT NOT NULL DEFAULT '[]',
    outputs TEXT NOT NULL DEFAULT '[]',
    acceptance_criteria TEXT NOT NULL DEFAULT '[]',
    test_commands TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_dependencies (
    task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    PRIMARY KEY(task_id, depends_on_task_id)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    phase_id TEXT,
    task_id TEXT,
    type TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    phase_id TEXT,
    path TEXT NOT NULL,
    kind TEXT NOT NULL,
    summary TEXT NOT NULL,
    created_at TEXT NOT NULL,
    -- B1+B2: provenance and trust signals.
    -- source_type: 'llm' | 'stub' | 'fallback' | 'extra' | 'human'
    source_type TEXT DEFAULT 'unknown',
    -- trust_level: 'high' | 'medium' | 'low' | 'untrusted'
    trust_level TEXT DEFAULT 'medium',
    -- validation_status: 'passed' | 'partial' | 'failed' | 'not_run'
    validation_status TEXT DEFAULT 'not_run',
    -- 0-100 from validator(s); NULL means no validator ran
    validation_score INTEGER
);

CREATE TABLE IF NOT EXISTS approvals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    phase_id TEXT NOT NULL,
    gate TEXT NOT NULL,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    decided_at TEXT
);

CREATE TABLE IF NOT EXISTS costs (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    agent_id TEXT,
    provider TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd REAL NOT NULL DEFAULT 0.0,
    latency_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS file_locks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES runs(id) ON DELETE CASCADE,
    task_id TEXT NOT NULL,
    owner TEXT NOT NULL,
    path_pattern TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    released_at TEXT
);

CREATE TABLE IF NOT EXISTS memories (
    id TEXT PRIMARY KEY,
    project_id TEXT REFERENCES projects(id) ON DELETE CASCADE,
    scope TEXT NOT NULL,
    key TEXT NOT NULL,
    path TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, scope, key)
);

CREATE INDEX IF NOT EXISTS idx_runs_project ON runs(project_id);
CREATE INDEX IF NOT EXISTS idx_phases_run ON phases(run_id, sequence);
CREATE INDEX IF NOT EXISTS idx_tasks_run ON tasks(run_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_approvals_run ON approvals(run_id, status);
CREATE INDEX IF NOT EXISTS idx_costs_run ON costs(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_file_locks_project ON file_locks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_memories_project ON memories(project_id, scope);
"""


# Idempotent migrations for databases created before new columns existed.
# Keep additive only — never drop or rename columns here.
_MIGRATIONS: list[tuple[str, str]] = [
    # (table, ddl_fragment_after_ADD_COLUMN)
    # B-batch additions (B1-B3)
    ("artifacts", "source_type TEXT DEFAULT 'unknown'"),
    ("artifacts", "trust_level TEXT DEFAULT 'medium'"),
    ("artifacts", "validation_status TEXT DEFAULT 'not_run'"),
    ("artifacts", "validation_score INTEGER"),
    ("runs", "delivery_grade TEXT"),
    ("phases", "phase_score INTEGER"),
    # C0 additions (budget governor + lock + idempotency)
    ("artifacts", "is_current INTEGER DEFAULT 1"),
    ("artifacts", "repair_attempt INTEGER DEFAULT 0"),
    ("runs", "budget_json TEXT"),
    ("runs", "locked_by TEXT"),
    ("runs", "heartbeat_at TEXT"),
    ("phases", "attempt_count INTEGER DEFAULT 0"),
]


def _apply_migrations(db: sqlite3.Connection) -> None:
    """Apply ALTER TABLE ADD COLUMN migrations idempotently.

    SQLite raises ``OperationalError: duplicate column name`` if the column
    already exists; we swallow that and continue. Any other error propagates.
    """
    for table, column_ddl in _MIGRATIONS:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {column_ddl}")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower():
                continue
            raise
