from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.prd_manual import ManualCodexPrdAgent, normalize_prd_payload, validate_prd_files
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


class ManualCodexPrdAgentTests(unittest.TestCase):
    def test_prepare_prompt_pack_writes_prompt_template_and_schema(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build an expense tracker", paths.projects_dir)
            run = engine.run(project["id"], "software_project")

            pack = ManualCodexPrdAgent(Database(paths.db_path)).prepare_prompt_pack(
                project=project,
                run_id=run["run_id"],
            )

            self.assertTrue(pack.prompt_path.exists())
            self.assertTrue(pack.template_path.exists())
            self.assertTrue(pack.schema_path.exists())
            prompt = pack.prompt_path.read_text(encoding="utf-8")
            self.assertIn("Return **JSON only**", prompt)
            self.assertIn("market-grade PRD", prompt)
            self.assertIn("Productboard-style insight-to-feature traceability", prompt)
            self.assertIn("evidence chain", prompt.lower())

    def test_prepare_prompt_pack_includes_existing_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build an expense tracker", paths.projects_dir)
            project_path = Path(project["path"])
            (project_path / "docs/product/research.md").write_text(
                "# Research\n\n### [S1] Source\n\nEvidence.",
                encoding="utf-8",
            )
            (project_path / "docs/product/evidence-chain.md").write_text(
                "# Evidence Chain\n\nSource evidence maps to a PRD decision.",
                encoding="utf-8",
            )

            pack = ManualCodexPrdAgent(Database(paths.db_path)).prepare_prompt_pack(
                project=project,
                run_id=None,
            )

            prompt = pack.prompt_path.read_text(encoding="utf-8")
            self.assertIn("Existing research context", prompt)
            self.assertIn("[S1]", prompt)
            self.assertIn("docs/product/evidence-chain.md", prompt)

    def test_import_result_writes_docs_and_validates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build an expense tracker", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            response_path = Path(tmp) / "prd-response.json"
            response_path.write_text(
                json.dumps(_valid_payload(), ensure_ascii=False),
                encoding="utf-8",
            )

            validation = ManualCodexPrdAgent(Database(paths.db_path)).import_result(
                project=project,
                run_id=run["run_id"],
                input_path=response_path,
            )

            self.assertTrue(validation.ok, validation.errors)
            project_path = Path(project["path"])
            self.assertTrue((project_path / "docs/product/research.md").exists())
            self.assertTrue((project_path / "docs/product/scope.md").exists())

    def test_validate_reports_missing_sections(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs/product").mkdir(parents=True)
            for name in ["research.md", "prd.md", "user-stories.md", "acceptance-criteria.md", "scope.md"]:
                (root / "docs/product" / name).write_text("# Short\n", encoding="utf-8")

            validation = validate_prd_files(root)

            self.assertFalse(validation.ok)
            self.assertTrue(any("too short" in error for error in validation.errors))

    def test_normalize_prd_payload_accepts_artifacts_object(self) -> None:
        payload = {"artifacts": _valid_payload()}
        normalized = normalize_prd_payload(payload)
        self.assertEqual(set(normalized), set([
            "docs/product/research.md",
            "docs/product/competitor-matrix.md",
            "docs/product/pm-debate.md",
            "docs/product/prd.md",
            "docs/product/user-stories.md",
            "docs/product/acceptance-criteria.md",
            "docs/product/scope.md",
            "docs/product/prd-quality-score.md",
        ]))

    def test_validate_rejects_low_prd_quality_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = normalize_prd_payload(_valid_payload())
            artifacts["docs/product/prd-quality-score.md"] = """# PRD Quality Score

- Research depth: 4/10
- Differentiation: 4/10
- UX specificity: 4/10
- Visual strategy: 4/10
- Feasibility: 4/10
- Testability: 4/10

Final score: 24/60
Status: fail
"""
            for relative_path, content in artifacts.items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            validation = validate_prd_files(root)

            self.assertFalse(validation.ok)
            self.assertTrue(any("below gate" in error for error in validation.errors))


def _valid_payload() -> dict[str, str]:
    return {
        "research_md": """# Research

## Sources Or Assumptions

- Assumption: Personal finance users need fast entry because this local test cannot browse.
- Assumption: The painful current alternative is a spreadsheet or notes app that is fast to edit but weak for trustworthy monthly review.
- Source: Placeholder local product brief.

## Insights

- Users need fast capture, category review, and monthly visibility.
- The valuable product artifact is an inspectable monthly cash-flow summary showing income, expenses, and net total from source transactions.
- Repeat use comes from adding transactions throughout the month and reviewing the monthly summary before budget decisions.

## Evidence Chain

- Source evidence -> insight: monthly review matters -> PRD decision: keep monthly totals in MVP -> QA gate: verify totals after transaction changes.
""",
        "competitor_matrix_md": """# Competitor Matrix

| Competitor / Reference | Source | Pattern | Opportunity | Caution |
| --- | --- | --- | --- | --- |
| Spreadsheet workflow | Source | Manual tracking is flexible. | Faster guided entry. | Too generic without summaries. |
| Budgeting app | Source | Category summaries create value. | Keep monthly review clear. | Bank sync is out of scope. |
| Notebook workflow | Assumption | Fast capture matters. | Reduce friction. | Low structure hurts reporting. |
| Dashboard app | Assumption | Summary screens drive review. | Make totals inspectable. | Avoid dashboard bloat. |

## Product Takeaway

Competitor patterns point toward fast capture and reliable monthly review.
""",
        "pm_debate_md": """# PM Debate

## Market PM

The market rewards fast transaction capture and reliable monthly review.

## UX Researcher

The core journey is add transaction, categorize it, and verify monthly totals.

## Product Designer

The UI should make entry and review feel direct.

## Technical PM

The MVP is feasible with local CRUD, validation, and monthly summaries.

## Visual/AI PM

AI visuals are optional and should not distract from data entry.

## Critic

The main risk is producing generic CRUD without a useful monthly review.

## Decision

Proceed with a narrow local expense tracker.
""",
        "prd_md": """# Product Requirements

## Background

The project is a personal expense tracker for one local user.

## Product Strategy And Differentiation

The product is narrower than a full finance suite because it focuses on fast local entry and monthly review.
It should beat spreadsheets by producing a trustworthy monthly cash-flow artifact without bank sync or finance-platform setup.

## Product Management Operating Model

- Aha!-style lifecycle keeps strategy, MVP, non-goals, and delivery connected.
- Productboard-style traceability keeps each MVP feature tied to user value.

## Users

- A solo user who wants quick income and expense tracking.

## MVP

- Add income and expenses.
- Categorize transactions.
- View monthly totals.
- Produce an inspectable monthly cash-flow summary artifact.

## Non-goals

- Bank sync.
- Cloud collaboration.

## Risks

- Manual data entry may be tedious.
""",
        "user_stories_md": """# User Stories

- As a budget-conscious user, I want to add an expense quickly, so that I can keep records current.
- As a user, I want to categorize transactions, so that monthly totals are meaningful.
- As a user, I want to see monthly income and spending, so that I can understand trends.
""",
        "acceptance_criteria_md": """# Acceptance Criteria

- Given I am on the transaction form, when I submit a valid expense, then it appears in the transaction list.
- Given I enter a category, when I save the transaction, then the category is visible in monthly summaries.
- Given a month has income and expenses, when I open monthly statistics, then income, expenses, and net total are shown.
""",
        "scope_md": """# Scope

## MVP

- Local transaction CRUD.
- Category field.
- Monthly statistics.
- Inspectable monthly cash-flow summary artifact: income, expenses, and net total.

## V1

- Recurring transactions.
- CSV export.

## Future

- Bank import.
- Budget alerts.

## Non-goals

- Multi-user collaboration.
- Cloud deployment.
""",
        "prd_quality_score_md": """# PRD Quality Score

- Research depth: 8/10
- Differentiation: 8/10
- UX specificity: 8/10
- Visual strategy: 7/10
- Feasibility: 8/10
- Testability: 9/10

Final score: 48/60
Status: pass
""",
    }


if __name__ == "__main__":
    unittest.main()
