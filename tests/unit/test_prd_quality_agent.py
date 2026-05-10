from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.prd_manual import ManualCodexPrdAgent
from orchestrator.agents.prd_quality import PrdCritiqueAgent, PrdScoreAgent, evaluate_prd_quality
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database
from tests.unit.test_prd_manual_agent import _valid_payload


class PrdQualityAgentTests(unittest.TestCase):
    def test_score_and_critique_agents_write_reports_for_valid_prd(self) -> None:
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

            score = PrdScoreAgent(Database(paths.db_path)).run(project=project, run_id=run["run_id"])
            critique = PrdCritiqueAgent(Database(paths.db_path)).run(project=project, run_id=run["run_id"])

            self.assertEqual(score.evaluation.status, "pass", score.evaluation.hard_failures)
            self.assertGreaterEqual(score.evaluation.final_score, 64)
            self.assertTrue(score.score_md_path.exists())
            self.assertTrue(score.score_json_path.exists())
            self.assertTrue(critique.critique_path.exists())
            self.assertIn("Lead PM Decision", critique.critique_path.read_text(encoding="utf-8"))

    def test_evaluate_fails_missing_evidence_chain(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = _valid_payload()
            artifacts["research_md"] = artifacts["research_md"].replace("## Evidence Chain", "## Weak Notes")
            for relative_path, content in _artifact_paths(artifacts).items():
                path = root / relative_path
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding="utf-8")

            evaluation = evaluate_prd_quality(root)

            self.assertEqual(evaluation.status, "fail")
            self.assertTrue(any("Evidence chain" in failure or "evidence chain" in failure for failure in evaluation.hard_failures))


def _artifact_paths(payload: dict[str, str]) -> dict[str, str]:
    return {
        "docs/product/research.md": payload["research_md"],
        "docs/product/competitor-matrix.md": payload["competitor_matrix_md"],
        "docs/product/pm-debate.md": payload["pm_debate_md"],
        "docs/product/prd.md": payload["prd_md"],
        "docs/product/user-stories.md": payload["user_stories_md"],
        "docs/product/acceptance-criteria.md": payload["acceptance_criteria_md"],
        "docs/product/scope.md": payload["scope_md"],
        "docs/product/prd-quality-score.md": payload["prd_quality_score_md"],
    }


if __name__ == "__main__":
    unittest.main()
