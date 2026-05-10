from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.prd_research import PrdResearchAgent, plan_research_queries
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database
from orchestrator.tools.search_tools import MockSearchProvider


class PrdResearchAgentTests(unittest.TestCase):
    def test_plan_research_queries_returns_prd_relevant_queries(self) -> None:
        queries = plan_research_queries("Build a personal expense tracker")
        self.assertGreaterEqual(len(queries), 6)
        self.assertTrue(any("MVP" in query for query in queries))

    def test_plan_research_queries_sharpens_portfolio_research(self) -> None:
        queries = plan_research_queries(
            "做一个 creator project tracker web app，支持作品集导出页面、项目截图、任务和复盘"
        )

        joined = "\n".join(queries).lower()
        self.assertIn("framer portfolio templates", joined)
        self.assertIn("webflow portfolio templates", joined)
        self.assertIn("semplice portfolio examples", joined)
        self.assertIn("readymag portfolio examples", joined)
        self.assertIn("contra creator profile", joined)
        self.assertIn("ux designer portfolio case studies", joined)
        self.assertIn("project tracker", joined)

    def test_research_agent_writes_research_and_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build an expense tracker", paths.projects_dir)
            run = engine.run(project["id"], "software_project")

            result = PrdResearchAgent(provider=MockSearchProvider(), db=Database(paths.db_path)).run(
                project=project,
                run_id=run["run_id"],
                max_queries=2,
                results_per_query=2,
            )

            self.assertEqual(len(result.queries), 2)
            self.assertEqual(len(result.sources), 4)
            self.assertTrue(result.research_path.exists())
            self.assertTrue(result.sources_path.exists())
            self.assertIsNotNone(result.research_v2)
            self.assertTrue(result.research_v2.reference_products_path.exists())
            content = Path(result.research_path).read_text(encoding="utf-8")
            self.assertIn("[S1]", content)
            self.assertIn("Source-backed PRD claims", content)


if __name__ == "__main__":
    unittest.main()
