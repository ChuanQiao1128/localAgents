from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.developer import DeveloperAgent
from orchestrator.agents.product_review_team import ProductBuildReviewAgent, evaluate_product_build
from orchestrator.agents.qa import QAAgent
from orchestrator.agents.reviewer import ReviewerAgent


class ProductReviewTeamTests(unittest.TestCase):
    def test_product_review_team_passes_remediated_portfolio_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_dir = root / ".agent/tasks"
            tasks_dir.mkdir(parents=True)
            tasks = [
                {"id": "WEB-001", "title": "Build profile editor with avatar upload states"},
                {"id": "WEB-002", "title": "Build project gallery editor"},
                {"id": "WEB-003", "title": "Build theme selector and live preview"},
                {"id": "EXPORT-001", "title": "Implement static HTML export from preview render model"},
            ]
            (tasks_dir / "generated-tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
            _write_review_context(root)
            DeveloperAgent(root).implement_generated_tasks()
            QAAgent(root).run_checks()
            ReviewerAgent(root).review_diff()

            result = ProductBuildReviewAgent().run(
                project={"id": "project_test", "path": str(root), "idea": "Build a portfolio builder"},
                run_id=None,
            )

            self.assertEqual(result.evaluation.domain_type, "portfolio")
            self.assertEqual(result.evaluation.status, "pass")
            self.assertFalse(result.evaluation.blockers)
            self.assertGreaterEqual(result.evaluation.final_score, 90)

    def test_product_review_team_fails_runnable_but_generic_portfolio_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_dir = root / ".agent/tasks"
            tasks_dir.mkdir(parents=True)
            tasks = [
                {"id": "WEB-001", "title": "Build profile editor with avatar upload states"},
                {"id": "WEB-002", "title": "Build project gallery editor"},
                {"id": "WEB-003", "title": "Build theme selector and live preview"},
                {"id": "EXPORT-001", "title": "Implement static HTML export from preview render model"},
            ]
            (tasks_dir / "generated-tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
            DeveloperAgent(root).implement_generated_tasks()
            QAAgent(root).run_checks()
            ReviewerAgent(root).review_diff()

            result = ProductBuildReviewAgent().run(
                project={"id": "project_test", "path": str(root), "idea": "Build a portfolio builder"},
                run_id=None,
            )
            report = result.review_md_path.read_text(encoding="utf-8")

            self.assertEqual(result.evaluation.domain_type, "portfolio")
            self.assertEqual(result.evaluation.status, "fail")
            self.assertIn("not yet a strong product", report)
            self.assertIn("Downstream Agent Team Plan", result.downstream_team_plan_path.read_text(encoding="utf-8"))

    def test_product_review_team_scores_generic_build_without_portfolio_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_dir = root / ".agent/tasks"
            tasks_dir.mkdir(parents=True)
            (tasks_dir / "generated-tasks.json").write_text(
                json.dumps([{"id": "WEB-001", "title": "Build invoice tracker shell"}]),
                encoding="utf-8",
            )
            DeveloperAgent(root).implement_generated_tasks()
            QAAgent(root).run_checks()
            evaluation = evaluate_product_build(root)

            self.assertIn(evaluation.domain_type, {"freelance", "generic"})
            self.assertLess(evaluation.final_score, evaluation.max_score)

def _write_review_context(root: Path) -> None:
    files = {
        "docs/product/prd.md": "portfolio council critique product-fit differentiation local-first publishable proof reference competitor pattern benchmark",
        "docs/product/product-fit.md": "portfolio product-fit local-first publishable proof differentiation",
        "docs/product/prd-critique.md": "critique council product-fit portfolio proof quality",
        "docs/product/reference-products/index.md": "reference competitor pattern benchmark Webflow Framer Behance Dribbble",
        "docs/product/ux-patterns.md": "portfolio onboarding guided proof workflow",
        "docs/design/user-flow.md": "portfolio flow critique score gate empty failure validation responsive",
        "docs/design/design-system.md": "template visual proof accessibility responsive",
        "docs/design/component-spec.md": "project template image alt text layout preset crop",
        "docs/design/design-critique.md": "critique score gate validation responsive",
        "docs/design/ui-team-plan.md": "UI team remediation plan",
        "docs/design/ui-team/lead-synthesis.md": "UI team lead synthesis",
        "docs/design/ui-team/ui-team-contracts.json": "{\"team\":\"ui team\"}",
        "docs/implementation/developer-team-plan.md": "Developer team remediation plan",
        "docs/implementation/implementation-contract.json": "{\"team\":\"developer team\"}",
        "docs/implementation/developer-team-task-plan.json": "{\"team\":\"developer team\"}",
        "docs/implementation/acceptance-matrix.md": "developer team acceptance matrix",
        "docs/qa/qa-team-plan.md": "QA team plan",
        "docs/review/review-team-plan.md": "Review team plan",
        "docs/architecture/architecture.md": "product-fit design critique prd score",
        ".agent/teams/downstream-agent-contracts.json": "{\"teams\":[\"ui team\",\"developer team\",\"qa team\",\"review team\"]}",
        ".agent/teams/team-maturity.json": "{\"teams\":[\"ui team\",\"developer team\",\"qa team\",\"review team\"]}",
    }
    for relative_path, text in files.items():
        path = root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
