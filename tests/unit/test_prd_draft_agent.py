from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.prd_draft import LocalPrdDraftAgent, build_prd_payload, load_research_sources
from orchestrator.agents.prd_manual import validate_prd_files
from orchestrator.agents.prd_options import PrdOptionsAgent
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


class LocalPrdDraftAgentTests(unittest.TestCase):
    def test_build_prd_payload_contains_required_keys_and_source_refs(self) -> None:
        payload = build_prd_payload("Build a tracker", [_source("S1", 0.9), _source("S2", 0.5)])
        self.assertEqual(
            set(payload),
            {
                "research_md",
                "competitor_matrix_md",
                "pm_debate_md",
                "prd_md",
                "user_stories_md",
                "acceptance_criteria_md",
                "scope_md",
                "prd_quality_score_md",
            },
        )
        self.assertIn("[S1]", payload["research_md"])
        self.assertIn("Evidence Chain", payload["research_md"])
        self.assertIn("Product Management Operating Model", payload["prd_md"])
        self.assertIn("Given", payload["acceptance_criteria_md"])
        self.assertIn("Final score:", payload["prd_quality_score_md"])

    def test_draft_and_import_writes_valid_docs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a freelance invoice tracker", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            project_path = Path(project["path"])
            sources_path = project_path / ".agent/artifacts/research" / run["run_id"] / "sources.json"
            sources_path.parent.mkdir(parents=True, exist_ok=True)
            sources_path.write_text(
                json.dumps([_source("S1", 0.9), _source("S2", 0.4)], ensure_ascii=False),
                encoding="utf-8",
            )

            path, validation = LocalPrdDraftAgent(Database(paths.db_path)).draft_and_import(
                project=project,
                run_id=run["run_id"],
            )

            self.assertTrue(path.exists())
            self.assertTrue(validation.ok, validation.errors)
            self.assertTrue(validate_prd_files(project_path).ok)

    def test_selected_option_shapes_generated_prd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a freelance invoice tracker", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            project_path = Path(project["path"])
            sources_path = project_path / ".agent/artifacts/research" / run["run_id"] / "sources.json"
            sources_path.parent.mkdir(parents=True, exist_ok=True)
            sources_path.write_text(json.dumps([_source("S1", 0.9)], ensure_ascii=False), encoding="utf-8")
            options = PrdOptionsAgent(Database(paths.db_path))
            options.generate(project=project, run_id=run["run_id"])
            options.select(project=project, run_id=run["run_id"], option_id="option-b")

            path, validation = LocalPrdDraftAgent(Database(paths.db_path)).draft_and_import(
                project=project,
                run_id=run["run_id"],
            )

            prd = (project_path / "docs/product/prd.md").read_text(encoding="utf-8")
            self.assertTrue(path.exists())
            self.assertTrue(validation.ok, validation.errors)
            self.assertIn("Selected option: option-b - Invoice-Ready Workflow", prd)
            self.assertIn("Invoice draft generation", prd)
            self.assertNotIn("way to Users need", prd)
            self.assertNotIn("cleanup. with", prd)
            self.assertNotIn("cleanup with minimal friction", prd)

    def test_portfolio_selected_option_shapes_generated_prd(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a personal portfolio builder web app", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            project_path = Path(project["path"])
            sources_path = project_path / ".agent/artifacts/research" / run["run_id"] / "sources.json"
            sources_path.parent.mkdir(parents=True, exist_ok=True)
            sources_path.write_text(json.dumps([_portfolio_source("S1", 0.9)], ensure_ascii=False), encoding="utf-8")
            options = PrdOptionsAgent(Database(paths.db_path))
            options.generate(project=project, run_id=run["run_id"])
            options.select(project=project, run_id=run["run_id"], option_id="option-b")

            _, validation = LocalPrdDraftAgent(Database(paths.db_path)).draft_and_import(
                project=project,
                run_id=run["run_id"],
            )

            prd = (project_path / "docs/product/prd.md").read_text(encoding="utf-8")
            research = (project_path / "docs/product/research.md").read_text(encoding="utf-8")
            competitor_matrix = (project_path / "docs/product/competitor-matrix.md").read_text(encoding="utf-8")
            pm_debate = (project_path / "docs/product/pm-debate.md").read_text(encoding="utf-8")
            quality_score = (project_path / "docs/product/prd-quality-score.md").read_text(encoding="utf-8")
            acceptance = (project_path / "docs/product/acceptance-criteria.md").read_text(encoding="utf-8")
            council_market = project_path / "docs/product/council/market-pm.md"
            council_critic = project_path / "docs/product/council/critic.md"
            self.assertTrue(validation.ok, validation.errors)
            self.assertTrue(council_market.exists())
            self.assertTrue(council_critic.exists())
            self.assertTrue((project_path / "docs/product/benchmark-library/index.md").exists())
            self.assertTrue((project_path / "docs/product/benchmark-library/portfolio-template.md").exists())
            self.assertIn("Selected option: option-b - Publishable Portfolio Workflow", prd)
            self.assertIn("Reference Product Patterns", prd)
            self.assertIn("Product Strategy And Differentiation", prd)
            self.assertIn("Product Management Operating Model", prd)
            self.assertIn("UX Quality Bar", prd)
            self.assertIn("AI And Visual Asset Strategy", prd)
            self.assertIn("Profile editor with avatar upload", prd)
            self.assertIn("Project gallery CRUD with screenshots", prd)
            self.assertIn("Static HTML export", prd)
            self.assertIn("AI image generation", prd)
            self.assertIn("Competitive Research Synthesis", research)
            self.assertIn("Evidence Chain", research)
            self.assertIn("Competitor Matrix", competitor_matrix)
            self.assertIn("Product Takeaway", competitor_matrix)
            self.assertIn("Visual/AI PM", pm_debate)
            self.assertIn("Critic", pm_debate)
            self.assertIn("Council artifacts", pm_debate)
            self.assertIn("Status: pass", quality_score)
            self.assertIn("selected theme", acceptance.lower())
            self.assertNotIn("summary view", acceptance.lower())
            self.assertNotIn("Core CRUD", prd)

    def test_load_research_sources_returns_empty_without_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(load_research_sources(Path(tmp), "run_missing"), [])


def _source(source_id: str, relevance: float) -> dict[str, object]:
    return {
        "id": source_id,
        "query": "freelance invoice tracker MVP",
        "title": "Freelance invoice tracker reference",
        "url": f"https://example.com/{source_id}",
        "summary": "Freelancers need quick time tracking, invoice generation, and reliable billing records.",
        "relevance": relevance,
        "evidence_type": "tavily",
    }


def _portfolio_source(source_id: str, relevance: float) -> dict[str, object]:
    return {
        "id": source_id,
        "query": "portfolio builder upload project screenshots static export",
        "title": "Portfolio builder reference",
        "url": f"https://example.com/{source_id}",
        "summary": "Portfolio builders help users upload images, describe projects, preview pages, and publish sites.",
        "relevance": relevance,
        "evidence_type": "tavily",
    }


if __name__ == "__main__":
    unittest.main()
