from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.prd_benchmark import PrdBenchmarkAgent
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


class PrdBenchmarkAgentTests(unittest.TestCase):
    def test_benchmark_agent_writes_portfolio_library_without_external_calls(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a personal portfolio builder web app", paths.projects_dir)
            run = engine.run(project["id"], "software_project")

            result = PrdBenchmarkAgent(Database(paths.db_path)).run(project=project, run_id=run["run_id"])

            self.assertTrue(result.index_path.exists())
            self.assertTrue(result.domain_template_path.exists())
            self.assertTrue(result.quality_gates_path.exists())
            self.assertTrue(result.decision_playbook_path.exists())
            self.assertTrue(result.development_handoff_path.exists())
            self.assertTrue(result.library_json_path.exists())
            index = result.index_path.read_text(encoding="utf-8")
            domain_template = result.domain_template_path.read_text(encoding="utf-8")
            quality_gates = result.quality_gates_path.read_text(encoding="utf-8")
            handoff = result.development_handoff_path.read_text(encoding="utf-8")
            library = json.loads(result.library_json_path.read_text(encoding="utf-8"))
            self.assertIn("Local PRD Benchmark Library", index)
            self.assertIn("Productboard", index)
            self.assertIn("What AI visuals are allowed", domain_template)
            self.assertIn("Portfolio visual quality", quality_gates)
            self.assertIn("Portfolio-Specific Handoff", handoff)
            self.assertEqual(library["domain_type"], "portfolio")


if __name__ == "__main__":
    unittest.main()
