from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.prd_council import PrdCouncilAgent
from orchestrator.agents.prd_options import PrdOptionsAgent
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


class PrdCouncilAgentTests(unittest.TestCase):
    def test_generate_writes_role_artifacts_and_debate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a personal portfolio builder web app", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            project_path = Path(project["path"])
            _write_sources(project_path, run["run_id"])
            options = PrdOptionsAgent(Database(paths.db_path))
            options.generate(project=project, run_id=run["run_id"])
            options.select(project=project, run_id=run["run_id"], option_id="option-b")

            result = PrdCouncilAgent(Database(paths.db_path)).generate(project=project, run_id=run["run_id"])

            self.assertEqual(len(result.roles), 6)
            self.assertTrue(result.debate_path.exists())
            self.assertIn("Market PM", result.debate_markdown)
            self.assertIn("UX Researcher", result.debate_markdown)
            self.assertIn("Visual/AI PM", result.debate_markdown)
            self.assertTrue((project_path / "docs/product/council/market-pm.md").exists())
            self.assertTrue((project_path / "docs/product/council/critic.md").exists())
            critic = (project_path / "docs/product/council/critic.md").read_text(encoding="utf-8")
            self.assertIn("generic form builder", critic)

    def test_prepare_and_import_manual_role_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a personal portfolio builder web app", paths.projects_dir)
            run = engine.run(project["id"], "software_project")
            project_path = Path(project["path"])
            _write_sources(project_path, run["run_id"])
            options = PrdOptionsAgent(Database(paths.db_path))
            options.generate(project=project, run_id=run["run_id"])
            options.select(project=project, run_id=run["run_id"], option_id="option-b")
            agent = PrdCouncilAgent(Database(paths.db_path))

            pack = agent.prepare_prompt_pack(project=project, run_id=run["run_id"])
            for prompt_path in pack.role_prompt_paths:
                role_id = prompt_path.parent.name
                response_path = prompt_path.parent / "response.json"
                response_path.write_text(
                    json.dumps(
                        {
                            "role_id": role_id,
                            "role_name": prompt_path.parent.name,
                            "findings": [f"{role_id} finding one", f"{role_id} finding two"],
                            "recommendations": [f"{role_id} recommendation"],
                            "risks": [f"{role_id} risk"],
                            "handoff": [f"{role_id} handoff"],
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )

            result = agent.import_role_outputs(project=project, run_id=run["run_id"], input_dir=pack.directory)

            self.assertEqual(len(result.roles), 6)
            self.assertTrue((project_path / "docs/product/council/market-pm.md").exists())
            self.assertIn("market-pm finding one", result.debate_markdown)
            self.assertIn("Council artifacts", result.debate_markdown)


def _write_sources(project_path: Path, run_id: str) -> None:
    sources_path = project_path / ".agent/artifacts/research" / run_id / "sources.json"
    sources_path.parent.mkdir(parents=True, exist_ok=True)
    sources_path.write_text(
        json.dumps(
            [
                {
                    "id": "S1",
                    "query": "portfolio builder upload screenshots static export",
                    "title": "Portfolio builder reference",
                    "url": "https://example.com/portfolio",
                    "summary": "Portfolio builders help users preview and publish polished pages from project media.",
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
