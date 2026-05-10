from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.agent_registry import AgentRegistry
from orchestrator.core.yaml_loader import load_yaml


class YamlLoaderTests(unittest.TestCase):
    def test_loads_default_workflow_and_agents(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)

            workflow = load_yaml(paths.workflows_dir / "software_project.yaml")
            self.assertEqual(workflow["id"], "software_project")
            self.assertEqual(len(workflow["phases"]), 9)
            self.assertEqual(workflow["phases"][2]["gate"], "prd_approval")
            self.assertEqual(workflow["phases"][3]["depends_on"], ["prd"])
            agentic = load_yaml(paths.workflows_dir / "agentic_project.yaml")
            self.assertEqual(agentic["id"], "agentic_project")
            self.assertEqual(agentic["runtime"], "agentic_project")
            self.assertEqual(agentic["stages"][0]["id"], "intent-contract")

            agents = AgentRegistry(paths.agents_dir).load_all()
            self.assertIn("product_manager", agents)
            self.assertIn("developer", agents)
            self.assertEqual(agents["developer"]["permissions"]["write"], ["apps/**", "packages/**", "tests/**"])

    def test_loads_json_subset_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.yaml"
            path.write_text('{"id": "sample", "items": [1, 2]}', encoding="utf-8")
            self.assertEqual(load_yaml(path), {"id": "sample", "items": [1, 2]})


if __name__ == "__main__":
    unittest.main()
