from __future__ import annotations

from pathlib import Path

from .base import AgentResult


class ProductManagerAgent:
    id = "product_manager"

    def generate_prd(self, *, project_path: Path, idea: str) -> AgentResult:
        product_dir = project_path / "docs/product"
        product_dir.mkdir(parents=True, exist_ok=True)
        outputs = {
            "prd.md": _prd(idea),
            "user-stories.md": _user_stories(idea),
            "acceptance-criteria.md": _acceptance_criteria(),
            "scope.md": _scope(),
        }
        artifacts: list[str] = []
        for filename, content in outputs.items():
            path = product_dir / filename
            path.write_text(content, encoding="utf-8")
            artifacts.append(f"docs/product/{filename}")
        return AgentResult(
            status="completed",
            summary="Generated deterministic PM artifacts from the project brief.",
            artifacts=artifacts,
        )


def _prd(idea: str) -> str:
    return f"""# Product Requirements

## Background

{idea}

## MVP

- Create a local web application workflow.
- Support clear user input, generated artifacts, and approval gates.
- Keep all state local and inspectable.

## Non Goals

- Cloud deployment.
- Multi-user collaboration.
- Enterprise permission management.

## Risks

- Agent output quality depends on future model provider wiring.
- Research claims must be source-backed once web research is enabled.
"""


def _user_stories(_: str) -> str:
    return """# User Stories

- As a local builder, I can create a project from a short idea.
- As a reviewer, I can approve PRD output before downstream phases run.
- As a developer, I can inspect generated artifacts and task state locally.
"""


def _acceptance_criteria() -> str:
    return """# Acceptance Criteria

- Project creation writes a local workspace.
- Workflow execution records phases, tasks, events, artifacts, and approvals.
- PRD approval pauses execution until approved.
- Tests pass with `python3 -m unittest discover -s tests`.
"""


def _scope() -> str:
    return """# Scope

## MVP

- CLI-driven local workflow.
- SQLite state store.
- Deterministic PM, architecture, QA, and review stubs.

## Later

- Real model router providers.
- Web research and citations.
- Developer worktree and sandbox execution.
"""

