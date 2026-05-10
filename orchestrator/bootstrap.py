from __future__ import annotations

from pathlib import Path

from .config import AppPaths
from .db import Database


def initialize_workspace(paths: AppPaths) -> None:
    paths.state_dir.mkdir(parents=True, exist_ok=True)
    paths.projects_dir.mkdir(parents=True, exist_ok=True)
    paths.agents_dir.mkdir(parents=True, exist_ok=True)
    paths.workflows_dir.mkdir(parents=True, exist_ok=True)
    for relative in [
        "orchestrator/core",
        "orchestrator/db",
        "orchestrator/agents",
        "apps/dashboard",
        "templates/docs",
        "templates/prompts",
        "tests/unit",
        "tests/integration",
        "tests/e2e",
    ]:
        (paths.root / relative).mkdir(parents=True, exist_ok=True)
    _write_default_configs(paths)
    Database(paths.db_path).initialize()


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content.strip() + "\n", encoding="utf-8")


def _write_default_configs(paths: AppPaths) -> None:
    for filename, content in DEFAULT_AGENT_CONFIGS.items():
        _write_if_missing(paths.agents_dir / filename, content)
    _write_if_missing(paths.workflows_dir / "software_project.yaml", DEFAULT_WORKFLOW)
    _write_if_missing(paths.workflows_dir / "agentic_project.yaml", DEFAULT_AGENTIC_WORKFLOW)


DEFAULT_AGENT_CONFIGS = {
    "lead.yaml": """
id: lead
name: Lead Agent
model: claude_cli:sonnet
temperature: 0.2
role: >
  Coordinate the deterministic software project workflow, enforce gates,
  and summarize run progress.
tools:
  - read_file
  - write_file
  - git_status
permissions:
  read:
    - "**/*"
  write:
    - ".agent/**"
    - "docs/**"
  deny:
    - "~/**"
required_outputs:
  - ".agent/project-brief.md"
""",
    "product_manager.yaml": """
id: product_manager
name: Product Manager
model: claude_cli:sonnet
temperature: 0.3
role: >
  Research the product space, define MVP scope, write PRDs, and produce
  testable acceptance criteria.
tools:
  - web_search
  - read_file
  - write_file
permissions:
  read:
    - "**/*"
  write:
    - "docs/product/**"
    - ".agent/artifacts/research/**"
  deny:
    - "apps/**"
    - ".env"
    - "~/**"
required_outputs:
  - "docs/product/research.md"
  - "docs/product/prd.md"
  - "docs/product/acceptance-criteria.md"
quality_rules:
  - "Research claims must include sources or be marked assumptions."
  - "PRD must separate MVP, V1, and future ideas."
  - "Acceptance criteria must be testable."
""",
    "ui_designer.yaml": """
id: ui_designer
name: UI Designer
model: claude_cli:sonnet
temperature: 0.3
role: >
  Turn product requirements into user flows, design system notes, and
  component specifications.
tools:
  - read_file
  - write_file
permissions:
  read:
    - "**/*"
  write:
    - "docs/design/**"
    - ".agent/artifacts/design/**"
  deny:
    - "apps/**"
    - "~/**"
required_outputs:
  - "docs/design/user-flow.md"
  - "docs/design/design-system.md"
  - "docs/design/component-spec.md"
""",
    "architect.yaml": """
id: architect
name: Architect
model: claude_cli:sonnet
temperature: 0.2
role: >
  Design the system architecture, API contract, data model, and executable
  implementation task graph.
tools:
  - read_file
  - write_file
permissions:
  read:
    - "**/*"
  write:
    - "docs/architecture/**"
    - ".agent/tasks/**"
  deny:
    - "~/**"
required_outputs:
  - "docs/architecture/architecture.md"
  - "docs/architecture/api.openapi.yaml"
  - "docs/architecture/database-schema.md"
  - ".agent/tasks/generated-tasks.json"
""",
    "developer.yaml": """
id: developer
name: Full-stack Developer
model: codex_cli:gpt-5.5
temperature: 0.2
role: >
  Implement tasks within allowed paths, add tests, and report diffs.
tools:
  - read_file
  - edit_file
  - grep
  - run_shell
  - git_diff
permissions:
  read:
    - "**/*"
  write:
    - "apps/**"
    - "packages/**"
    - "tests/**"
  ask:
    - "package.json"
    - "pnpm-lock.yaml"
    - "docker-compose.yml"
    - ".env.example"
  deny:
    - ".env"
    - "~/**"
required_checks:
  - "npm run lint"
  - "npm run typecheck"
  - "npm run test"
""",
    "qa.yaml": """
id: qa
name: QA Agent
model: codex_cli:gpt-5.5
temperature: 0.2
role: >
  Build test plans, run checks, record results, and create bug reports.
tools:
  - read_file
  - write_file
  - run_shell
permissions:
  read:
    - "**/*"
  write:
    - "docs/qa/**"
    - "tests/**"
  deny:
    - "~/**"
required_outputs:
  - "docs/qa/test-plan.md"
  - "docs/qa/test-results.md"
""",
    "reviewer.yaml": """
id: reviewer
name: Reviewer
model: claude_cli:sonnet
temperature: 0.2
role: >
  Review diffs against product, design, architecture, and test evidence.
tools:
  - read_file
  - git_diff
  - write_file
permissions:
  read:
    - "**/*"
  write:
    - "docs/review/**"
  deny:
    - "~/**"
required_outputs:
  - "docs/review/review-report.md"
""",
}


DEFAULT_WORKFLOW = """
id: software_project
name: Software Project Workflow

phases:
  - id: intake
    owner: lead
    output:
      - ".agent/project-brief.md"

  - id: research
    owner: product_manager
    depends_on: [intake]
    output:
      - "docs/product/research.md"

  - id: prd
    owner: product_manager
    depends_on: [research]
    output:
      - "docs/product/prd.md"
      - "docs/product/acceptance-criteria.md"
    gate: prd_approval

  - id: design
    owner: ui_designer
    depends_on: [prd]
    output:
      - "docs/design/user-flow.md"
      - "docs/design/design-system.md"
      - "docs/design/component-spec.md"

  - id: architecture
    owner: architect
    depends_on: [design]
    output:
      - "docs/architecture/architecture.md"
      - "docs/architecture/api.openapi.yaml"
      - "docs/architecture/database-schema.md"
      - ".agent/tasks/generated-tasks.json"

  - id: implementation
    owner: developer
    depends_on: [architecture]
    parallelizable: true
    output:
      - ".agent/runs/{run_id}/implementation-summary.md"

  - id: qa
    owner: qa
    depends_on: [implementation]
    output:
      - "docs/qa/test-plan.md"
      - "docs/qa/test-results.md"

  - id: review
    owner: reviewer
    depends_on: [qa]
    output:
      - "docs/review/review-report.md"

  - id: merge
    owner: lead
    depends_on: [review]
    output:
      - ".agent/runs/{run_id}/final-report.md"
"""


DEFAULT_AGENTIC_WORKFLOW = """
id: agentic_project
name: Agentic Coding Runtime
description: AI-native workflow for traceable, candidate-based, self-repairing verified patch generation.

runtime: agentic_project

stages:
  - id: intent-contract
    agent: orchestrator
    writes:
      - ".agent/runs/{run_id}/intent-contract.json"

  - id: context-pack
    agent: context-explorer
    mode: read_only
    writes:
      - ".agent/runs/{run_id}/context-pack.json"

  - id: eval-harness
    agent: spec-compiler
    mode: read_only_plus_eval_write
    writes:
      - ".agent/runs/{run_id}/eval-harness.json"

  - id: task-slicing
    agent: orchestrator
    writes:
      - ".agent/runs/{run_id}/task-slices.json"

  - id: candidate-patches
    agent: patch-worker
    isolation: git_worktree
    candidates: 1
    writes:
      - ".agent/runs/{run_id}/candidates/*/patch.diff"
      - ".agent/runs/{run_id}/candidates/*/changed-files.json"
      - ".agent/runs/{run_id}/candidates/*/run-log.jsonl"
      - ".agent/runs/{run_id}/candidates/*/score.json"
      - ".agent/runs/{run_id}/candidates/*/eval-results.json"

  - id: repair-loop
    agent: repair-agent
    max_loops: 5
    stop_on_repeated_failure_type: true
    writes:
      - ".agent/runs/{run_id}/candidates/*/repair-history.json"

  - id: critic-panel
    agents:
      - correctness-critic
      - regression-critic
      - security-critic
      - ux-critic
      - overfit-critic
    mode: read_only
    writes:
      - ".agent/runs/{run_id}/critics/*.md"

  - id: promotion-gate
    agent: integration-lead
    deterministic_checks: true
    writes:
      - ".agent/runs/{run_id}/promotion-report.json"

  - id: memory-update
    agent: memory-curator
    mode: proposed_only
    writes:
      - ".agent/runs/{run_id}/memory-update.proposed.json"
"""
