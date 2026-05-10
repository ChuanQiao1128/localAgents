from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .base import AgentResult


class ArchitectAgent:
    id = "architect"

    def generate_plan(self, *, project_path: Path, idea: str) -> AgentResult:
        context = _load_context(project_path)
        domain_type = _domain_type(idea, context)
        arch_dir = project_path / "docs/architecture"
        tasks_dir = project_path / ".agent/tasks"
        (arch_dir / "adr").mkdir(parents=True, exist_ok=True)
        tasks_dir.mkdir(parents=True, exist_ok=True)
        files = {
            arch_dir / "architecture.md": _architecture(idea, domain_type, context),
            arch_dir / "api.openapi.yaml": _openapi(idea, domain_type),
            arch_dir / "database-schema.md": _schema(domain_type),
            arch_dir / "adr/001-tech-stack.md": _adr(domain_type),
            tasks_dir / "generated-tasks.json": json.dumps(_tasks(domain_type), ensure_ascii=False, indent=2) + "\n",
        }
        artifacts: list[str] = []
        for path, content in files.items():
            path.write_text(content, encoding="utf-8")
            artifacts.append(str(path.relative_to(project_path)))
        return AgentResult(
            status="completed",
            summary="Generated deterministic architecture artifacts and task graph.",
            artifacts=artifacts,
        )


def _load_context(project_path: Path) -> dict[str, str]:
    relative_paths = [
        "docs/product/prd.md",
        "docs/product/product-fit.md",
        "docs/product/prd-score.md",
        "docs/product/prd-critique.md",
        "docs/product/scope.md",
        "docs/product/acceptance-criteria.md",
        "docs/design/design-critique.md",
        "docs/design/component-spec.md",
        "docs/design/user-flow.md",
        "docs/design/design-system.md",
    ]
    context: dict[str, str] = {}
    for relative_path in relative_paths:
        path = project_path / relative_path
        context[relative_path] = path.read_text(encoding="utf-8") if path.exists() else ""
    return context


def _domain_type(idea: str, context: dict[str, str]) -> str:
    lower = f"{idea}\n" + "\n".join(context.values()).lower()
    if any(term in lower for term in ["portfolio", "作品集", "personal site", "personal website"]):
        return "portfolio"
    if any(term in lower for term in ["invoice", "freelance", "billable", "time tracking"]):
        return "freelance"
    if any(term in lower for term in ["expense", "transaction", "income", "支出", "预算"]):
        return "expense"
    return "generic"


def _score_line(context: dict[str, str], key: str) -> str:
    text = context.get(key, "")
    for line in text.splitlines():
        if "Final score:" in line or "PRD score:" in line or "Design score:" in line:
            return line.strip()
    return "not available"


def _architecture(idea: str, domain_type: str, context: dict[str, str]) -> str:
    product_fit = _score_line(context, "docs/product/product-fit.md")
    prd_score = _score_line(context, "docs/product/prd-score.md")
    design_score = _score_line(context, "docs/design/design-critique.md")
    domain_section = _domain_architecture(domain_type)
    return f"""# Architecture

Project idea: {idea}

Domain type: `{domain_type}`

## Gate Inputs

- Product fit: {product_fit}
- PRD score: {prd_score}
- Design critique: {design_score}

Architecture must preserve the approved product and design gates. If design critique or PRD validation fails, architecture should not proceed.

## Stack

- Next.js frontend for the generated web app surface.
- Local persistence for MVP state.
- Static artifact generation where the PRD requires export or inspectable output.
- CLI/orchestrator artifacts remain local and auditable.

## Control Model

Workflow order is code-controlled. Agents produce bounded artifacts inside
phase-specific allowed paths.

## Product And Design Constraints

{domain_section}

## Handoff Rules

- Developer tasks must trace back to PRD acceptance criteria and design component states.
- QA tasks must cover product-fit value, PRD hard gates, and design critique hard gates.
- Reviewer must reject scope creep that bypasses MVP/non-goals or weakens the final user artifact.
"""


def _domain_architecture(domain_type: str) -> str:
    if domain_type == "portfolio":
        return """- Preview and static export must share the same render model.
- Image upload must preserve local preview, replace, remove, invalid type, oversized, and failure states.
- Theme presets must be constrained data/config, not an open-ended theme marketplace.
- AI-generated visual assets must be marked as placeholders and separated from user-owned proof.
- Static HTML export must include profile content, project content, image references, contact links, and selected theme."""
    if domain_type == "freelance":
        return """- Time entries are the source of truth for invoice drafts.
- Billable and non-billable states must be explicit in data and UI.
- Invoice draft totals must be recalculated from source entries after create, edit, delete, rate, and duration changes.
- Client/date filters must not mutate source entries.
- Exported invoice drafts must remain traceable to included time entries."""
    if domain_type == "expense":
        return """- Transactions are the source of truth for monthly summaries.
- Monthly income, expense, and net total must be recalculated after create, edit, delete, and category changes.
- Category filtering must not mutate source records.
- Summary output must remain inspectable and traceable to transactions."""
    return """- Source records must remain traceable to the useful output artifact.
- MVP implementation should optimize the primary workflow before integrations.
- Non-goals stay out of task scope unless explicitly approved."""


def _openapi(idea: str, domain_type: str) -> str:
    paths = _domain_paths(domain_type)
    path_yaml = "\n".join(paths)
    return f"""openapi: 3.1.0
info:
  title: Generated API
  version: 0.1.0
  description: API contract placeholder for {idea}
paths:
  /health:
    get:
      responses:
        "200":
          description: OK
{path_yaml}
"""


def _domain_paths(domain_type: str) -> list[str]:
    if domain_type == "portfolio":
        return [
            "  /portfolio/profile:",
            "    get:",
            "      responses:",
            "        \"200\":",
            "          description: Profile content",
            "  /portfolio/projects:",
            "    get:",
            "      responses:",
            "        \"200\":",
            "          description: Portfolio projects",
            "  /portfolio/export:",
            "    post:",
            "      responses:",
            "        \"200\":",
            "          description: Static HTML export",
        ]
    if domain_type == "freelance":
        return [
            "  /time-entries:",
            "    get:",
            "      responses:",
            "        \"200\":",
            "          description: Time entries",
            "  /invoice-drafts:",
            "    post:",
            "      responses:",
            "        \"200\":",
            "          description: Invoice draft",
        ]
    if domain_type == "expense":
        return [
            "  /transactions:",
            "    get:",
            "      responses:",
            "        \"200\":",
            "          description: Transactions",
            "  /monthly-summary:",
            "    get:",
            "      responses:",
            "        \"200\":",
            "          description: Monthly summary",
        ]
    return []


def _schema(domain_type: str) -> str:
    if domain_type == "portfolio":
        return """# Database Schema

- profiles: avatar_path, name, title, bio, skills, contact_links, social_links
- projects: title, role, description, tags, screenshot_path, project_url, repository_url, sort_order
- themes: id, name, typography, colors, layout_config
- exports: created_at, theme_id, output_path, source_snapshot_hash
"""
    if domain_type == "freelance":
        return """# Database Schema

- time_entries: client, project, date, duration, billable, hourly_rate, notes
- invoice_drafts: client, date_range, included_entry_ids, subtotal, status, output_path
- clients: name, contact, default_rate
"""
    if domain_type == "expense":
        return """# Database Schema

- transactions: amount, date, type, category, note
- categories: name, type
- monthly_summaries: month, income_total, expense_total, net_total, source_snapshot_hash
"""
    return """# Database Schema

- projects
- runs
- phases
- tasks
- events
- artifacts
- approvals
- costs
"""


def _adr(domain_type: str) -> str:
    return f"""# ADR 001: Gate-Aware Architecture

## Decision

Architecture must consume product-fit, PRD score, PRD critique, design critique, and component spec artifacts before task decomposition.

## Consequences

- Implementation tasks preserve the product's core value instead of generic CRUD scope.
- QA can test product and design gates directly.
- Scope creep is easier to identify during review.

## Domain

`{domain_type}`
"""


def _tasks(domain_type: str) -> list[dict[str, object]]:
    if domain_type == "portfolio":
        return [
            {
                "id": "WEB-001",
                "title": "Build profile editor with avatar upload states",
                "owner": "developer",
                "allowed_paths": ["apps/web/**", "tests/**"],
                "acceptance_criteria": [
                    "Profile fields save locally",
                    "Avatar upload covers empty, preview, replace, remove, invalid type, oversized, and failure states",
                ],
                "test_commands": ["npm run test", "npx playwright test portfolio-profile"],
            },
            {
                "id": "WEB-002",
                "title": "Build project gallery editor",
                "owner": "developer",
                "allowed_paths": ["apps/web/**", "tests/**"],
                "acceptance_criteria": [
                    "Projects support screenshot, title, role, description, tags, and links",
                    "Projects can be added, edited, reordered, duplicated, and deleted",
                ],
                "test_commands": ["npm run test", "npx playwright test portfolio-projects"],
            },
            {
                "id": "WEB-003",
                "title": "Build theme selector and live preview",
                "owner": "developer",
                "allowed_paths": ["apps/web/**", "tests/**"],
                "acceptance_criteria": [
                    "Theme changes update preview without losing content",
                    "Desktop and mobile previews preserve content hierarchy",
                ],
                "test_commands": ["npx playwright test portfolio-preview"],
            },
            {
                "id": "EXPORT-001",
                "title": "Implement static HTML export from preview render model",
                "owner": "developer",
                "allowed_paths": ["apps/web/**", "packages/**", "tests/**"],
                "acceptance_criteria": [
                    "Exported HTML includes profile, projects, images, contact links, and selected theme",
                    "Preview and exported content match",
                ],
                "test_commands": ["npx playwright test portfolio-export"],
            },
            {
                "id": "QA-001",
                "title": "Verify product-fit and design gates",
                "owner": "qa",
                "allowed_paths": ["tests/**", "docs/qa/**"],
                "acceptance_criteria": [
                    "QA covers preview/export fidelity",
                    "QA covers AI placeholder integrity and user-owned proof boundaries",
                ],
                "test_commands": ["python3 -m unittest discover -s tests"],
            },
        ]
    if domain_type == "freelance":
        return [
            {
                "id": "TIME-001",
                "title": "Build time entry workflow",
                "owner": "developer",
                "allowed_paths": ["apps/web/**", "tests/**"],
                "acceptance_criteria": ["Time entries include client, project, date, duration, billable status, rate, and notes"],
            },
            {
                "id": "INV-001",
                "title": "Build invoice draft preview",
                "owner": "developer",
                "allowed_paths": ["apps/web/**", "tests/**"],
                "acceptance_criteria": ["Invoice draft totals trace to included billable entries"],
            },
            {
                "id": "QA-001",
                "title": "Verify invoice total consistency",
                "owner": "qa",
                "allowed_paths": ["tests/**", "docs/qa/**"],
                "acceptance_criteria": ["Totals update after create, edit, delete, rate, duration, and billable toggles"],
            },
        ]
    if domain_type == "expense":
        return [
            {
                "id": "TXN-001",
                "title": "Build transaction entry workflow",
                "owner": "developer",
                "allowed_paths": ["apps/web/**", "tests/**"],
                "acceptance_criteria": ["Transactions include amount, date, type, category, and note"],
            },
            {
                "id": "SUMMARY-001",
                "title": "Build monthly summary artifact",
                "owner": "developer",
                "allowed_paths": ["apps/web/**", "tests/**"],
                "acceptance_criteria": ["Monthly income, expenses, and net total trace to source transactions"],
            },
        ]
    return [
        {
            "id": "CORE-001",
            "title": "Maintain CLI and SQLite project creation",
            "owner": "developer",
            "allowed_paths": ["orchestrator/**", "tests/**"],
            "acceptance_criteria": ["Project creation remains covered by tests"],
        },
        {
            "id": "AGENT-001",
            "title": "Wire real model adapters",
            "owner": "developer",
            "allowed_paths": ["orchestrator/model/**", "orchestrator/agents/**"],
            "acceptance_criteria": ["Provider calls are isolated behind ModelRouter"],
        },
    ]
