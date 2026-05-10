from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.developer_team import DeveloperTeamAgent


class DeveloperTeamAgentTests(unittest.TestCase):
    def test_developer_team_generates_portfolio_implementation_contract(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_dir = root / "docs/design"
            product_dir = root / "docs/product"
            design_dir.mkdir(parents=True)
            product_dir.mkdir(parents=True)
            (design_dir / "design-contract.json").write_text(
                json.dumps(
                    {
                        "domain_type": "portfolio",
                        "screens": [
                            {"id": "intent_template_picker"},
                            {"id": "profile_editor"},
                            {"id": "guided_project_case_study_editor"},
                            {"id": "live_preview"},
                            {"id": "export_panel"},
                        ],
                        "templates": [
                            {"id": "editorial_case_study"},
                            {"id": "visual_gallery"},
                            {"id": "builder_resume"},
                            {"id": "proof_first_landing_page"},
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (design_dir / "ui-team-dev-handoff.md").write_text(
                "# Handoff\n\nAdd guided project case-study fields.\n",
                encoding="utf-8",
            )
            (design_dir / "selected-visual-direction.md").write_text(
                "# Selected Visual Direction\n\nWinner: `dense-dashboard`.\n",
                encoding="utf-8",
            )
            artifact_dir = root / ".agent/artifacts/visual_directions"
            artifact_dir.mkdir(parents=True)
            (artifact_dir / "variants.json").write_text(
                json.dumps(
                    {
                        "multimodal_review": {
                            "winner_id": "dense-dashboard",
                            "report_path": "docs/design/visual-direction-multimodal-review.md",
                        },
                        "variants": [
                            {
                                "id": "dense-dashboard",
                                "screenshot_path": ".agent/artifacts/visual_directions/dense-dashboard/screenshot.png",
                                "screenshot_quality": {"score": 80, "valid": True},
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (design_dir / "visual-direction-multimodal-review.json").write_text(
                json.dumps({"winner_id": "dense-dashboard"}),
                encoding="utf-8",
            )
            (product_dir / "post-build-product-review.json").write_text(
                json.dumps(
                    {
                        "status": "needs_revision",
                        "blockers": [
                            "The builder does not coach the user through what makes a portfolio persuasive.",
                            "Project screenshot lifecycle is incomplete; replacement exists, removal is missing.",
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = DeveloperTeamAgent().run(
                project={"id": "project_test", "path": str(root), "idea": "portfolio builder"},
                run_id=None,
            )
            contract = json.loads(result.implementation_contract_path.read_text(encoding="utf-8"))
            task_plan = json.loads(result.task_plan_path.read_text(encoding="utf-8"))
            score = json.loads(result.score_json_path.read_text(encoding="utf-8"))

            self.assertTrue(result.editor_workflow_path.exists())
            self.assertTrue(result.preview_export_path.exists())
            self.assertTrue(result.asset_handling_path.exists())
            self.assertTrue(result.browser_test_path.exists())
            self.assertEqual(contract["gate"], "developer_team_implementation_contract")
            self.assertEqual(contract["source_visual_direction"], "docs/design/selected-visual-direction.md")
            self.assertEqual(contract["selected_visual_direction"]["id"], "dense-dashboard")
            self.assertEqual(contract["selected_visual_direction"]["selection_method"], "multimodal_review")
            self.assertEqual(contract["selected_visual_direction"]["review_artifact"], "docs/design/visual-direction-multimodal-review.md")
            self.assertIn("editor_workflow", {module["id"] for module in contract["modules"]})
            self.assertIn("DEV-ASSET-001", {task["id"] for task in task_plan})
            self.assertEqual(score["status"], "ready_for_remediation_implementation")
            self.assertEqual(score["dimensions"]["visual_evidence_ready"], 10)


if __name__ == "__main__":
    unittest.main()
