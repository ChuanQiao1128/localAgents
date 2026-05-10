from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.team_system_review import TeamSystemReviewAgent


class TeamSystemReviewAgentTests(unittest.TestCase):
    def test_team_system_review_generates_maturity_and_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "docs/product").mkdir(parents=True)
            (root / "docs/design/ui-team").mkdir(parents=True)
            (root / "docs/implementation/developer-team").mkdir(parents=True)
            (root / "docs/qa").mkdir(parents=True)
            (root / "docs/review").mkdir(parents=True)
            (root / ".agent/teams").mkdir(parents=True)
            (root / ".agent/tasks").mkdir(parents=True)
            for path in [
                "docs/product/prd.md",
                "docs/product/product-fit.md",
                "docs/product/post-build-product-review.md",
                "docs/design/design-contract.json",
                "docs/design/ui-team/ui-team-contracts.json",
                "docs/implementation/implementation-contract.json",
                "docs/implementation/developer-team-task-plan.json",
                "docs/qa/test-results.md",
                "docs/review/review-report.md",
            ]:
                (root / path).parent.mkdir(parents=True, exist_ok=True)
                (root / path).write_text("{}" if path.endswith(".json") else "# Artifact\n", encoding="utf-8")

            result = TeamSystemReviewAgent().run(
                project={"id": "project_test", "path": str(root), "idea": "portfolio builder"},
                run_id=None,
            )
            maturity = json.loads(result.maturity_json_path.read_text(encoding="utf-8"))
            tasks = json.loads(result.optimization_tasks_path.read_text(encoding="utf-8"))
            qa_contract = json.loads(result.qa_team_contract_path.read_text(encoding="utf-8"))

            self.assertTrue(result.overall_review_path.exists())
            self.assertGreaterEqual(len(maturity["teams"]), 7)
            self.assertTrue(tasks)
            self.assertEqual(qa_contract["team_id"], "qa_team")
            self.assertTrue((root / "docs/team-review/qa_team-review.md").exists())


if __name__ == "__main__":
    unittest.main()
