from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.agents import AgentContext, AgentRunner
from orchestrator.agents.architect import ArchitectAgent
from orchestrator.agents.base import StructuredOutputParser
from orchestrator.agents.product_manager import ProductManagerAgent
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.agent_registry import AgentRegistry
from orchestrator.core.cost_tracker import CostTracker
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


class AgentRuntimeTests(unittest.TestCase):
    def test_stub_agent_runner_returns_agent_result_and_records_cost(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            db = Database(paths.db_path)
            engine = create_engine(paths)
            project = engine.create_project("Build an expense tracker", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            agent_config = dict(AgentRegistry(paths.agents_dir).require("product_manager"))
            # Pin to the stub adapter so this test exercises router wiring
            # without invoking a real CLI (the YAML default points at
            # claude_cli for actual use).
            agent_config["model"] = "local-stub"
            runner = AgentRunner(cost_tracker=CostTracker(db))

            result = runner.run_task(
                agent_config,
                AgentContext(
                    project_id=project["id"],
                    run_id=run["run_id"],
                    task_id="PM-001",
                    idea="Build an expense tracker",
                    instructions="Generate PM artifacts.",
                    output_paths=["docs/product/prd.md"],
                ),
            )

            self.assertEqual(result.status, "completed")
            self.assertIn("Stub model completed request", result.summary)
            totals = CostTracker(db).totals_for_run(run["run_id"])
            self.assertGreater(totals["input_tokens"], 0)
            self.assertGreater(totals["output_tokens"], 0)
            self.assertEqual(totals["cost_usd"], 0.0)

    def test_structured_output_parser_extracts_json_object(self) -> None:
        parser = StructuredOutputParser()
        result = parser.parse_agent_result(
            'prefix {"status":"completed","summary":"ok","requires_approval":true} suffix'
        )
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.summary, "ok")
        self.assertTrue(result.requires_approval)

    def test_product_manager_agent_writes_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            result = ProductManagerAgent().generate_prd(
                project_path=project_path,
                idea="Build a personal finance app",
            )

            self.assertEqual(result.status, "completed")
            self.assertTrue((project_path / "docs/product/prd.md").exists())
            self.assertTrue((project_path / "docs/product/acceptance-criteria.md").exists())

    def test_architect_agent_writes_plan_and_task_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            result = ArchitectAgent().generate_plan(
                project_path=project_path,
                idea="Build a personal finance app",
            )

            self.assertEqual(result.status, "completed")
            self.assertTrue((project_path / "docs/architecture/architecture.md").exists())
            self.assertTrue((project_path / ".agent/tasks/generated-tasks.json").exists())

    def test_architect_agent_consumes_product_and_design_gates_for_portfolio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            (project_path / "docs/product").mkdir(parents=True)
            (project_path / "docs/design").mkdir(parents=True)
            (project_path / "docs/product/product-fit.md").write_text(
                "# Product Fit Evaluation\n\nFinal score: 76/80\n",
                encoding="utf-8",
            )
            (project_path / "docs/product/prd-score.md").write_text(
                "# Independent PRD Score\n\nFinal score: 78/80\n",
                encoding="utf-8",
            )
            (project_path / "docs/product/prd-critique.md").write_text(
                "# PRD Critique\n\nProceed.\n",
                encoding="utf-8",
            )
            (project_path / "docs/design/design-critique.md").write_text(
                "# Design Critique\n\nFinal score: 79/80\n",
                encoding="utf-8",
            )
            (project_path / "docs/design/component-spec.md").write_text(
                "# Component Spec\n\nPortfolio Preview and Export Panel.\n",
                encoding="utf-8",
            )

            ArchitectAgent().generate_plan(
                project_path=project_path,
                idea="Build a personal portfolio builder web app",
            )

            architecture = (project_path / "docs/architecture/architecture.md").read_text(encoding="utf-8")
            tasks = (project_path / ".agent/tasks/generated-tasks.json").read_text(encoding="utf-8")
            self.assertIn("Product fit: Final score: 76/80", architecture)
            self.assertIn("Design critique: Final score: 79/80", architecture)
            self.assertIn("Preview and static export must share the same render model", architecture)
            self.assertIn("WEB-001", tasks)
            self.assertIn("EXPORT-001", tasks)


if __name__ == "__main__":
    unittest.main()
