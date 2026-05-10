from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.prd_research_v2 import PrdResearchV2Agent
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


class PrdResearchV2AgentTests(unittest.TestCase):
    def test_research_v2_writes_enriched_artifacts_from_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a personal portfolio builder web app", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            project_path = Path(project["path"])
            _write_sources(project_path, run["run_id"])

            result = PrdResearchV2Agent(Database(paths.db_path)).run(project=project, run_id=run["run_id"])

            self.assertTrue(result.research_plan_path.exists())
            self.assertTrue(result.research_planner_json_path.exists())
            self.assertTrue(result.source_quality_path.exists())
            self.assertTrue(result.reference_products_path.exists())
            self.assertTrue(result.reference_critic_path.exists())
            self.assertTrue(result.feature_patterns_path.exists())
            self.assertTrue(result.ux_patterns_path.exists())
            self.assertTrue(result.product_management_benchmarks_path.exists())
            self.assertTrue(result.evidence_chain_path.exists())
            self.assertTrue(result.evidence_gate_path.exists())
            self.assertTrue(result.screenshots_readme_path.exists())
            self.assertTrue(result.visual_reference_analysis_path.exists())
            reference_products = result.reference_products_path.read_text(encoding="utf-8")
            reference_critic = result.reference_critic_path.read_text(encoding="utf-8")
            ux_patterns = result.ux_patterns_path.read_text(encoding="utf-8")
            benchmarks = result.product_management_benchmarks_path.read_text(encoding="utf-8")
            evidence_chain = result.evidence_chain_path.read_text(encoding="utf-8")
            evidence_gate = result.evidence_gate_path.read_text(encoding="utf-8")
            visual_analysis = result.visual_reference_analysis_path.read_text(encoding="utf-8")
            self.assertIn("Reference Products", reference_products)
            self.assertIn("theme/template selection", reference_products)
            self.assertIn("seed_profile", reference_products)
            self.assertIn("Reference Critic", reference_critic)
            self.assertIn("Borrow", reference_critic)
            self.assertIn("Seed profiles needing live verification", reference_critic)
            self.assertIn("Profile content -> project gallery -> theme -> preview -> static export", ux_patterns)
            self.assertIn("Product Management Benchmarks", benchmarks)
            self.assertIn("Productboard", benchmarks)
            self.assertIn("Evidence Chain", evidence_chain)
            self.assertIn("Evidence Status", evidence_chain)
            self.assertIn("Theme/template selection belongs in MVP", evidence_chain)
            self.assertIn("Evidence Gate", evidence_gate)
            self.assertIn("Visual Reference Analysis", visual_analysis)
            self.assertTrue((project_path / "docs/product/reference-products/reference-products.json").exists())


def _write_sources(project_path: Path, run_id: str) -> None:
    sources_path = project_path / ".agent/artifacts/research" / run_id / "sources.json"
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(
        json.dumps(
            [
                {
                    "id": "S1",
                    "query": "portfolio builder templates upload preview export",
                    "title": "Portfolio Builder with Themes",
                    "url": "https://example.com/portfolio",
                    "summary": "Users can upload images, choose templates, preview the website, and publish a polished portfolio page.",
                    "relevance": 0.91,
                    "evidence_type": "tavily",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
