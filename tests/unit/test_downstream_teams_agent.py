from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.downstream_teams import DownstreamTeamsAgent


class DownstreamTeamsAgentTests(unittest.TestCase):
    def test_downstream_teams_agent_generates_team_plans_and_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product_dir = root / "docs/product"
            product_dir.mkdir(parents=True)
            (product_dir / "post-build-product-review.json").write_text(
                json.dumps(
                    {
                        "domain_type": "portfolio",
                        "status": "needs_revision",
                        "blockers": [
                            "Research does not visibly shape the generated product experience.",
                            "No browser automation or screenshot evidence exists for the generated UI.",
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = DownstreamTeamsAgent().run(
                project={"id": "project_test", "path": str(root), "idea": "portfolio builder"},
                run_id=None,
            )
            tasks = json.loads(result.remediation_tasks_path.read_text(encoding="utf-8"))
            contracts = json.loads(result.contracts_json_path.read_text(encoding="utf-8"))

            self.assertTrue(result.ui_team_plan_path.exists())
            self.assertTrue(result.developer_team_plan_path.exists())
            self.assertTrue(result.qa_team_plan_path.exists())
            self.assertTrue(result.review_team_plan_path.exists())
            self.assertIn("failed_post_build_review", contracts["gate_semantics"])
            self.assertGreaterEqual(len(tasks), 5)
            self.assertIn("QA-TEAM-001", {task["id"] for task in tasks})


if __name__ == "__main__":
    unittest.main()
