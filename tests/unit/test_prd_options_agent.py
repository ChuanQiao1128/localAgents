from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.prd_options import PrdOptionsAgent, load_selected_option
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


class PrdOptionsAgentTests(unittest.TestCase):
    def test_generate_writes_multiple_options_and_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a freelance invoice tracker", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            _write_sources(Path(project["path"]), run["run_id"])

            result = PrdOptionsAgent(Database(paths.db_path)).generate(project=project, run_id=run["run_id"])

            self.assertEqual(len(result.options), 3)
            self.assertEqual(result.recommended_option_id, "option-b")
            self.assertTrue(result.options_json_path.exists())
            self.assertTrue(result.options_md_path.exists())
            self.assertTrue(result.review_md_path.exists())
            self.assertIn("Invoice-Ready Workflow", result.options_md_path.read_text(encoding="utf-8"))
            self.assertIn("Recommended option: option-b", result.review_md_path.read_text(encoding="utf-8"))

    def test_portfolio_options_are_domain_specific(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a personal portfolio builder web app", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            _write_sources(Path(project["path"]), run["run_id"])

            result = PrdOptionsAgent(Database(paths.db_path)).generate(project=project, run_id=run["run_id"])
            options_md = result.options_md_path.read_text(encoding="utf-8")

            self.assertEqual(result.recommended_option_id, "option-b")
            self.assertIn("Publishable Portfolio Workflow", options_md)
            self.assertIn("Profile editor with avatar upload", options_md)
            self.assertIn("Static HTML export", options_md)
            self.assertNotIn("Core CRUD", options_md)

    def test_select_records_decision_and_loads_selected_option(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a freelance invoice tracker", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            _write_sources(Path(project["path"]), run["run_id"])
            agent = PrdOptionsAgent(Database(paths.db_path))
            agent.generate(project=project, run_id=run["run_id"])

            decision_path = agent.select(
                project=project,
                run_id=run["run_id"],
                option_id="option-b",
                notes="Prefer invoice draft as the first strong workflow.",
            )
            selected = load_selected_option(Path(project["path"]), run["run_id"])

            self.assertTrue(decision_path.exists())
            self.assertIsNotNone(selected)
            self.assertEqual(selected.id, "option-b")
            self.assertIn("Prefer invoice draft", decision_path.read_text(encoding="utf-8"))


def _write_sources(project_path: Path, run_id: str) -> None:
    sources_path = project_path / ".agent/artifacts/research" / run_id / "sources.json"
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(
        json.dumps(
            [
                {
                    "id": "S1",
                    "query": "freelance invoice tracker MVP",
                    "title": "Freelance invoice workflow reference",
                    "url": "https://example.com/freelance",
                    "summary": "Freelancers need time tracking that turns into invoice-ready records.",
                    "relevance": 0.9,
                    "evidence_type": "tavily",
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
