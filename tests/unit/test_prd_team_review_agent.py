from __future__ import annotations

import json
import tempfile
import unittest

from orchestrator.agents.prd_team_review import PrdTeamReviewAgent
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


class PrdTeamReviewAgentTests(unittest.TestCase):
    def test_team_review_writes_review_workflow_and_contracts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a personal portfolio builder web app", paths.projects_dir)
            run = engine.run(project["id"], "software_project")

            result = PrdTeamReviewAgent(Database(paths.db_path)).run(project=project, run_id=run["run_id"])

            self.assertTrue(result.review_path.exists())
            self.assertTrue(result.optimized_workflow_path.exists())
            self.assertTrue(result.contracts_json_path.exists())
            self.assertIn("PRD Agent Team Review", result.review_path.read_text(encoding="utf-8"))
            self.assertIn("design critique", result.optimized_workflow_path.read_text(encoding="utf-8"))
            contracts = json.loads(result.contracts_json_path.read_text(encoding="utf-8"))
            self.assertIn("prd_product_fit", [agent["id"] for agent in contracts["agents"]])


if __name__ == "__main__":
    unittest.main()
