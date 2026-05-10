from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.prd_manual import ManualCodexPrdAgent
from orchestrator.agents.prd_product_fit import PrdProductFitAgent, evaluate_product_fit
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database
from tests.unit.test_prd_quality_agent import _artifact_paths
from tests.unit.test_prd_manual_agent import _valid_payload


class PrdProductFitAgentTests(unittest.TestCase):
    def test_product_fit_agent_writes_reports_for_valid_product(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a personal expense tracker", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            response_path = Path(tmp) / "prd-response.json"
            response_path.write_text(json.dumps(_valid_payload(), ensure_ascii=False), encoding="utf-8")
            ManualCodexPrdAgent(Database(paths.db_path)).import_result(
                project=project,
                run_id=run["run_id"],
                input_path=response_path,
            )

            result = PrdProductFitAgent(Database(paths.db_path)).run(project=project, run_id=run["run_id"])

            self.assertEqual(result.evaluation.status, "pass", result.evaluation.hard_failures)
            self.assertGreaterEqual(result.evaluation.final_score, 64)
            self.assertTrue(result.product_fit_md_path.exists())
            self.assertTrue(result.product_fit_json_path.exists())
            self.assertIn("Product Fit Evaluation", result.product_fit_md_path.read_text(encoding="utf-8"))

    def test_product_fit_fails_without_valuable_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            payload = _valid_payload()
            payload["prd_md"] = payload["prd_md"].replace(
                (
                    "The product is narrower than a full finance suite because it focuses on fast local entry and monthly review.\n"
                    "It should beat spreadsheets by producing a trustworthy monthly cash-flow artifact without bank sync or finance-platform setup."
                ),
                "The product lets users manage data with a simple dashboard.",
            )
            payload["prd_md"] = payload["prd_md"].replace("View monthly totals.", "View saved data.")
            payload["prd_md"] = payload["prd_md"].replace(
                "\n- Produce an inspectable monthly cash-flow summary artifact.",
                "",
            )
            payload["scope_md"] = payload["scope_md"].replace(
                "Monthly statistics.\n- Inspectable monthly cash-flow summary artifact: income, expenses, and net total.",
                "Data management.",
            )
            payload["acceptance_criteria_md"] = payload["acceptance_criteria_md"].replace(
                "monthly statistics, then income, expenses, and net total are shown",
                "data is shown",
            )
            for relative_path, content in _artifact_paths(payload).items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            evaluation = evaluate_product_fit(root)

            self.assertEqual(evaluation.status, "fail")
            self.assertTrue(any("artifact" in failure.lower() for failure in evaluation.hard_failures))


if __name__ == "__main__":
    unittest.main()
