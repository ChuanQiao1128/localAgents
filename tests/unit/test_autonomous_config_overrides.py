"""RC-2C: agent-studio.yaml `autonomous:` + `integration:` overrides.

Closes the RC-2A-004 finding: pre-RC-2C, agent-studio.yaml only loaded
the deploy: block; budgets and integration cadence were hardcoded in
DEFAULT_BUDGETS / DEFAULT_INTEGRATION_POLICY. RC-2C adds:

  - load_autonomous_overrides() in deploy.py
  - merge into AutonomousSession.budgets + integration_policy at
    session creation (start_or_resume)
  - existing on-disk sessions are NOT migrated (take the resume path
    that skips the override merge entirely)

These tests pin the new behavior and the no-op default.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from orchestrator.core.autonomous import (
    AutonomousController, DEFAULT_BUDGETS, DEFAULT_INTEGRATION_POLICY,
    find_active_session,
)
from orchestrator.core.deploy import (
    AutonomousOverrides, load_autonomous_overrides, project_config_path,
)


# ---------------------------------------------------------------------------
# load_autonomous_overrides
# ---------------------------------------------------------------------------
class LoadOverridesTests(unittest.TestCase):
    def test_no_yaml_returns_empty_overrides(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            o = load_autonomous_overrides(Path(tmp))
            self.assertEqual(o.budgets, {})
            self.assertEqual(o.integration, {})

    def test_no_relevant_blocks_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            project_config_path(project_path).write_text(
                "deploy:\n  enabled: false\n", encoding="utf-8"
            )
            o = load_autonomous_overrides(project_path)
            self.assertEqual(o.budgets, {})
            self.assertEqual(o.integration, {})

    def test_autonomous_budgets_block_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            project_config_path(project_path).write_text(
                "autonomous:\n"
                "  budgets:\n"
                "    max_tasks_per_session: 5\n"
                "    max_total_inner_runs: 8\n"
                "    max_corrective_tasks: 2\n",
                encoding="utf-8",
            )
            o = load_autonomous_overrides(project_path)
            self.assertEqual(o.budgets["max_tasks_per_session"], 5)
            self.assertEqual(o.budgets["max_total_inner_runs"], 8)
            self.assertEqual(o.budgets["max_corrective_tasks"], 2)

    def test_integration_block_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            project_config_path(project_path).write_text(
                "integration:\n"
                "  every_n_tasks: 1\n"
                "  run_at_session_end: true\n"
                "  timeout_sec: 120\n",
                encoding="utf-8",
            )
            o = load_autonomous_overrides(project_path)
            self.assertEqual(o.integration["every_n_tasks"], 1)
            self.assertTrue(o.integration["run_at_session_end"])
            self.assertEqual(o.integration["timeout_sec"], 120)

    def test_non_int_budget_values_are_dropped_silently(self) -> None:
        # Defensive: typo'd value (e.g. "5" instead of 5) shouldn't poison
        # the controller's int-typed budget arithmetic.
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            project_config_path(project_path).write_text(
                "autonomous:\n"
                "  budgets:\n"
                "    max_tasks_per_session: \"oops not an int\"\n"
                "    max_total_inner_runs: 8\n",
                encoding="utf-8",
            )
            o = load_autonomous_overrides(project_path)
            self.assertNotIn("max_tasks_per_session", o.budgets)
            self.assertEqual(o.budgets["max_total_inner_runs"], 8)


# ---------------------------------------------------------------------------
# Override merge at session creation
# ---------------------------------------------------------------------------
def _make_project(tmp: str) -> dict[str, Any]:
    project_path = Path(tmp) / "proj"
    project_path.mkdir()
    return {"id": "project_x", "name": "x", "path": str(project_path)}


class StartOrResumeOverrideMergeTests(unittest.TestCase):
    def test_no_overrides_session_uses_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(tmp)
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            self.assertEqual(session.budgets, DEFAULT_BUDGETS)
            self.assertEqual(session.integration_policy, DEFAULT_INTEGRATION_POLICY)

    def test_budgets_override_is_merged_into_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(tmp)
            project_config_path(Path(project["path"])).write_text(
                "autonomous:\n"
                "  budgets:\n"
                "    max_tasks_per_session: 5\n"
                "    max_corrective_tasks: 1\n",
                encoding="utf-8",
            )
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            self.assertEqual(session.budgets["max_tasks_per_session"], 5)
            self.assertEqual(session.budgets["max_corrective_tasks"], 1)
            # Other budgets keep their defaults.
            self.assertEqual(
                session.budgets["max_total_inner_runs"],
                DEFAULT_BUDGETS["max_total_inner_runs"],
            )

    def test_integration_override_is_merged_into_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(tmp)
            project_config_path(Path(project["path"])).write_text(
                "integration:\n"
                "  every_n_tasks: 1\n"
                "  timeout_sec: 90\n",
                encoding="utf-8",
            )
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            self.assertEqual(session.integration_policy["every_n_tasks"], 1)
            self.assertEqual(session.integration_policy["timeout_sec"], 90)
            # run_at_session_end keeps its default.
            self.assertEqual(
                session.integration_policy["run_at_session_end"],
                DEFAULT_INTEGRATION_POLICY["run_at_session_end"],
            )

    def test_resume_does_not_re_merge_overrides(self) -> None:
        # Existing on-disk session is loaded as-is — change to YAML
        # AFTER session creation does NOT take effect.
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(tmp)
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session_v1 = controller.start_or_resume()
            v1_max_tasks = session_v1.budgets["max_tasks_per_session"]
            # NOW write an override.
            project_config_path(Path(project["path"])).write_text(
                "autonomous:\n  budgets:\n    max_tasks_per_session: 999\n",
                encoding="utf-8",
            )
            controller2 = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session_v2 = controller2.start_or_resume()
            self.assertEqual(session_v2.session_id, session_v1.session_id)
            # Same value as v1 — override was NOT re-applied on resume.
            self.assertEqual(session_v2.budgets["max_tasks_per_session"], v1_max_tasks)

    def test_log_event_emitted_when_overrides_apply(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = _make_project(tmp)
            project_config_path(Path(project["path"])).write_text(
                "autonomous:\n  budgets:\n    max_tasks_per_session: 5\n"
                "integration:\n  every_n_tasks: 1\n",
                encoding="utf-8",
            )
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            log_path = Path(project["path"]) / ".agent/autonomous/sessions" / session.session_id / "controller-log.jsonl"
            log_text = log_path.read_text(encoding="utf-8")
            self.assertIn("session_budgets_overridden", log_text)
            self.assertIn("session_integration_policy_overridden", log_text)


if __name__ == "__main__":
    unittest.main()
