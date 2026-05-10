"""RC-2D.1: pin the defensive contract for the agent-studio.yaml loaders.

All three loaders — load_deploy_config, load_agentic_config,
load_autonomous_overrides — must return safe defaults when the file is
missing, malformed, not a dict, or has unexpected sub-block shapes.
The autonomous controller relies on this: a typo'd YAML must NOT crash
the runtime, only fall back to the controller-default behavior (the
controller's own default behavior is conservative — patch_worker=none,
DEFAULT_BUDGETS, deploy.enabled=false).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.core.deploy import (
    AgenticConfig, DeployConfig, AutonomousOverrides,
    load_agentic_config, load_autonomous_overrides, load_deploy_config,
    project_config_path,
)


class MalformedYamlTests(unittest.TestCase):
    def _write(self, project_path: Path, body: str) -> None:
        project_config_path(project_path).write_text(body, encoding="utf-8")

    def test_deploy_loader_returns_default_on_unparseable_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            self._write(project_path, "deploy: : :\n  invalid yaml structure\n")
            cfg = load_deploy_config(project_path)
            self.assertFalse(cfg.enabled)

    def test_agentic_loader_returns_default_on_unparseable_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            self._write(project_path, "agentic: : :\n  invalid\n")
            cfg = load_agentic_config(project_path)
            self.assertEqual(cfg.patch_worker, "none")

    def test_overrides_loader_returns_empty_on_unparseable_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            self._write(project_path, "autonomous: : :\n")
            o = load_autonomous_overrides(project_path)
            self.assertEqual(o.budgets, {})
            self.assertEqual(o.integration, {})

    def test_top_level_yaml_list_is_treated_as_no_config(self) -> None:
        # Top-level value is a list, not a dict → all 3 loaders return defaults.
        for loader, attr_check in [
            (load_deploy_config, lambda c: not c.enabled),
            (load_agentic_config, lambda c: c.patch_worker == "none"),
            (load_autonomous_overrides, lambda c: c.budgets == {} and c.integration == {}),
        ]:
            with tempfile.TemporaryDirectory() as tmp:
                project_path = Path(tmp)
                self._write(project_path, "- one\n- two\n")
                self.assertTrue(attr_check(loader(project_path)),
                                f"{loader.__name__} did not return default for top-level-list YAML")

    def test_agentic_block_with_non_dict_codex_subblock_falls_back(self) -> None:
        # codex value is a string, not a dict — loader should not crash.
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            self._write(project_path,
                        "agentic:\n  patch_worker: codex\n  codex: \"oops a string\"\n")
            cfg = load_agentic_config(project_path)
            self.assertEqual(cfg.patch_worker, "codex")
            # Defaults applied.
            self.assertEqual(cfg.codex.sandbox, "workspace-write")
            self.assertEqual(cfg.codex.ask_for_approval, "on-request")

    def test_autonomous_block_with_non_dict_budgets_subblock_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            self._write(project_path,
                        "autonomous:\n  budgets: \"not a dict\"\n")
            o = load_autonomous_overrides(project_path)
            self.assertEqual(o.budgets, {})


if __name__ == "__main__":
    unittest.main()
