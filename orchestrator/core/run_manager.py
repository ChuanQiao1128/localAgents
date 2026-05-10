from __future__ import annotations

from orchestrator.config import AppPaths
from orchestrator.db import Database

from .workflow_engine import WorkflowEngine


def create_engine(paths: AppPaths) -> WorkflowEngine:
    return WorkflowEngine(
        Database(paths.db_path),
        paths.workflows_dir,
        agents_dir=paths.agents_dir,
    )

