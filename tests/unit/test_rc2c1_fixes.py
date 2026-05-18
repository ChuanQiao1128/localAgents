"""RC-2C.1: regression tests for the 5 fixes surfaced by the RC-2C
real-Vercel dogfood (session_b82c6d6a3c).

Fix areas:
  1. State reconciliation — manual smoke/rollback success branches
     persist session.deployment.latest_*
  2. advance_one_task: budget check must not fire when no eligible
     task remains; finalization should proceed first
  3. autonomous.budgets.max_candidates_per_task → runtime candidate_count
  4. _render_patch_worker_prompt includes success_criteria + previous
     completed tasks; drops apps/web hardcoded preference
  5. .dogfood/rc2-creator-tracker/vercel.json + scripts/rc2c.sh
     copies it
"""
from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from orchestrator.core.agentic_runtime import _render_patch_worker_prompt
from orchestrator.core.autonomous import (
    AutonomousController, AutonomousSession, DEFAULT_BUDGETS,
    SCHEMA_VERSION_SESSION, write_task_graph,
)


# ===========================================================================
# Fix 2: advance_one_task budget vs finalization order
# ===========================================================================
class BudgetVsFinalizationOrderTests(unittest.TestCase):
    """When all tasks are completed (next_task=None), the controller
    must drop into _maybe_continue_or_complete for finalization
    (integration → deploy → smoke). The budget check is meaningless
    once no work remains — it should not pre-empt the finalization."""

    def _make_project(self, tmp: str) -> dict[str, Any]:
        project_path = Path(tmp) / "proj"
        project_path.mkdir()
        return {"id": "project_x", "name": "x", "path": str(project_path)}

    def test_all_tasks_completed_advance_does_not_fire_budget_pause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            # 3 tasks, all already completed — same shape as
            # session_b82c6d6a3c after RC-2B.2 finished.
            graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": [
                {"id": f"task-{i:03d}", "title": f"T{i}", "intent": "x",
                 "scope_paths": [], "acceptance_criteria": [],
                 "dependencies": [], "status": "completed", "risk": "low",
                 "run_ids": [], "commit": f"deadbee{i}"}
                for i in range(1, 4)
            ]}
            write_task_graph(Path(project["path"]), graph)
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: None,
            )
            session = controller.start_or_resume()
            # Force budgets that look maxed out (completed_tasks already = 3,
            # budget = 3) — pre-fix this fires `budget:max_tasks_per_session`.
            session.budgets["max_tasks_per_session"] = 3
            session.counters["completed_tasks"] = 3
            controller.advance_one_task(session, graph)
            # The post-fix behavior is: no task to advance → drop into
            # _maybe_continue_or_complete → session.status becomes
            # "completed" (no integration / no deploy enabled in this
            # bare project). pause_reason MUST NOT be budget:*.
            self.assertNotEqual(session.pause_reason or "", "budget:max_tasks_per_session")
            # Either completed cleanly or paused with a smoke/deploy/etc
            # reason — both are valid; only a budget pause was wrong.
            if session.pause_reason:
                self.assertFalse(session.pause_reason.startswith("budget:"))

    def test_pending_tasks_still_get_budget_gated_when_caps_exhausted(self) -> None:
        # Belt-and-suspenders: when there IS an eligible task and the
        # budget is exhausted, we still pause on budget. The fix is
        # narrowly "no eligible task → skip budget", not "remove budget".
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": [
                {"id": "task-001", "title": "A", "intent": "x", "scope_paths": [],
                 "acceptance_criteria": [], "dependencies": [], "status": "pending",
                 "risk": "low", "run_ids": [], "commit": None},
            ]}
            write_task_graph(Path(project["path"]), graph)
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            session.budgets["max_tasks_per_session"] = 0  # impossible budget
            controller.advance_one_task(session, graph)
            self.assertEqual(session.status, "paused")
            self.assertTrue((session.pause_reason or "").startswith("budget:"))


# ===========================================================================
# Fix 4a: prompt — success_criteria block
# ===========================================================================
class PromptSuccessCriteriaTests(unittest.TestCase):
    def _intent(self, **overrides) -> dict[str, Any]:
        base = {"goal": "do the thing", "allowed_change_scope": {"paths": ["src/**"]}}
        base.update(overrides)
        return base

    def _context(self) -> dict[str, Any]:
        return {"relevant_files": [{"path": "src/index.html"}]}

    def _eval(self) -> dict[str, Any]:
        return {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}

    def test_prompt_includes_success_criteria_list(self) -> None:
        intent = self._intent(success_criteria=[
            "The page contains the text 'No projects yet'",
            "The empty state is hidden when at least one project exists",
        ])
        prompt = _render_patch_worker_prompt(intent, self._context(), self._eval())
        self.assertIn("Success criteria", prompt)
        self.assertIn("No projects yet", prompt)
        self.assertIn("hidden when at least one project exists", prompt)

    def test_prompt_says_none_provided_when_success_criteria_empty(self) -> None:
        prompt = _render_patch_worker_prompt(self._intent(), self._context(), self._eval())
        self.assertIn("Success criteria: none provided", prompt)

    def test_prompt_no_longer_says_apps_web_as_universal_preference(self) -> None:
        prompt = _render_patch_worker_prompt(self._intent(), self._context(), self._eval())
        self.assertNotIn("Prefer touching existing apps/web source", prompt)
        # Replacement language should still steer toward the right files.
        self.assertIn("Prefer touching", prompt)


# ===========================================================================
# Fix 4b: prompt — previous_completed_tasks block
# ===========================================================================
class PromptPreviousTasksTests(unittest.TestCase):
    def _basic(self, previous: list[dict[str, Any]]) -> str:
        intent = {
            "goal": "do",
            "allowed_change_scope": {"paths": ["src/**"]},
            "previous_completed_tasks": previous,
        }
        ctx = {"relevant_files": []}
        ev = {"commands": []}
        return _render_patch_worker_prompt(intent, ctx, ev)

    def test_empty_previous_tasks_does_not_emit_block(self) -> None:
        prompt = self._basic([])
        self.assertNotIn("Previous completed tasks", prompt)

    def test_one_previous_task_renders_id_title_commit(self) -> None:
        prompt = self._basic([
            {"id": "task-001", "title": "Add filter UI",
             "commit": "8b020c0", "run_id": "run_c4f5cfa2a5"},
        ])
        self.assertIn("Previous completed tasks", prompt)
        self.assertIn("task-001", prompt)
        self.assertIn("Add filter UI", prompt)
        self.assertIn("8b020c0", prompt)
        self.assertIn("run_c4f5cfa2a5", prompt)

    def test_multiple_previous_tasks_render_in_order(self) -> None:
        prompt = self._basic([
            {"id": "task-001", "title": "A", "commit": "c1", "run_id": "r1"},
            {"id": "task-002", "title": "B", "commit": "c2", "run_id": "r2"},
        ])
        self.assertLess(prompt.index("task-001"), prompt.index("task-002"))


# ===========================================================================
# Fix 4 plumbing: advance_one_task populates previous_completed_tasks
# ===========================================================================
@dataclass
class _StubResult:
    run_id: str = "run_x"
    decision: str = "needs-human-review"
    candidate: str = "candidate-a"
    run_dir: Path = Path("/tmp/x")


class IntentOverridesPlumbingTests(unittest.TestCase):
    def test_intent_overrides_includes_previous_completed_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = {"id": "project_x", "name": "x", "path": str(Path(tmp) / "proj")}
            Path(project["path"]).mkdir()
            graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": [
                {"id": "task-001", "title": "First", "intent": "x", "scope_paths": [],
                 "acceptance_criteria": [], "dependencies": [], "status": "completed",
                 "risk": "low", "run_ids": ["run_aaa"], "commit": "c0ffee1"},
                {"id": "task-002", "title": "Second", "intent": "y", "scope_paths": [],
                 "acceptance_criteria": ["criterion-1"], "dependencies": ["task-001"],
                 "status": "pending", "risk": "low", "run_ids": [], "commit": None},
            ]}
            write_task_graph(Path(project["path"]), graph)

            captured = {}

            def _capture(**kw):
                captured["intent_overrides"] = kw.get("intent_overrides")
                return _StubResult()

            controller = AutonomousController(project=project, run_inner_loop=_capture)
            session = controller.start_or_resume()
            controller.advance_one_task(session, graph)
            ov = captured["intent_overrides"]
            self.assertIn("previous_completed_tasks", ov)
            self.assertEqual(len(ov["previous_completed_tasks"]), 1)
            self.assertEqual(ov["previous_completed_tasks"][0]["id"], "task-001")
            self.assertEqual(ov["previous_completed_tasks"][0]["commit"], "c0ffee1")
            self.assertEqual(ov["previous_completed_tasks"][0]["run_id"], "run_aaa")
            self.assertEqual(ov["success_criteria"], ["criterion-1"])

    def test_pending_tasks_with_no_predecessors_get_empty_previous_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = {"id": "project_y", "name": "y", "path": str(Path(tmp) / "proj")}
            Path(project["path"]).mkdir()
            graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": [
                {"id": "task-001", "title": "First", "intent": "x", "scope_paths": [],
                 "acceptance_criteria": [], "dependencies": [], "status": "pending",
                 "risk": "low", "run_ids": [], "commit": None},
            ]}
            write_task_graph(Path(project["path"]), graph)
            captured = {}

            def _capture(**kw):
                captured["intent_overrides"] = kw.get("intent_overrides")
                return _StubResult()

            controller = AutonomousController(project=project, run_inner_loop=_capture)
            session = controller.start_or_resume()
            controller.advance_one_task(session, graph)
            self.assertEqual(captured["intent_overrides"]["previous_completed_tasks"], [])


# ===========================================================================
# Fix 5: vercel.json + rc2c.sh seed
# ===========================================================================
class VercelSeedTests(unittest.TestCase):
    def test_dogfood_vercel_json_exists_and_has_outputDirectory_dist(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        path = repo_root / ".dogfood" / "rc2-creator-tracker" / "vercel.json"
        self.assertTrue(path.exists(), f"missing {path}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(payload["outputDirectory"], "dist")
        self.assertEqual(payload["buildCommand"], "npm run build")

    def test_rc2c_script_copies_vercel_json(self) -> None:
        repo_root = Path(__file__).resolve().parents[2]
        script = (repo_root / "scripts" / "rc2c.sh").read_text(encoding="utf-8")
        self.assertIn("vercel.json", script)


# ===========================================================================
# Fix 3: max_candidates_per_task → candidate_count
# ===========================================================================
class CandidateBudgetPropagationTests(unittest.TestCase):
    """The cli.py closure reads session.budgets at call time. Pin the
    contract for both default + override behavior."""

    def test_default_budgets_does_not_force_candidate_cap(self) -> None:
        # Default behavior: no max_candidates_per_task → closure passes
        # no candidate_count override → runtime keeps its default (3).
        # If anyone adds the key to DEFAULT_BUDGETS, that's a behavior
        # change that needs explicit thought (it would silently cap to
        # whatever value, hiding from operators that an override is in
        # play). So the default MUST stay absent.
        self.assertNotIn("max_candidates_per_task", DEFAULT_BUDGETS)

    def test_yaml_override_merges_into_session_budgets(self) -> None:
        # When agent-studio.yaml sets autonomous.budgets.max_candidates_per_task,
        # AutonomousOverrides.from_dict + start_or_resume's merge must
        # land it on session.budgets so the cli.py closure can read it.
        from orchestrator.core.deploy import (
            AutonomousOverrides, project_config_path, load_autonomous_overrides,
        )
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            project_config_path(project_path).write_text(
                "autonomous:\n  budgets:\n    max_candidates_per_task: 1\n",
                encoding="utf-8",
            )
            o = load_autonomous_overrides(project_path)
            self.assertEqual(o.budgets.get("max_candidates_per_task"), 1)

    def test_session_with_override_carries_candidate_cap(self) -> None:
        # End-to-end: project YAML override → session.budgets → closure
        # would read it. We exercise everything except the actual call
        # to AgenticProjectRuntime.run (which would need a real or fake
        # runtime; the closure is in cli.py and not unit-imported here).
        from orchestrator.core.deploy import project_config_path
        with tempfile.TemporaryDirectory() as tmp:
            project = {"id": "project_x", "name": "x", "path": str(Path(tmp) / "proj")}
            Path(project["path"]).mkdir()
            project_config_path(Path(project["path"])).write_text(
                "autonomous:\n  budgets:\n    max_candidates_per_task: 1\n",
                encoding="utf-8",
            )
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            self.assertEqual(session.budgets.get("max_candidates_per_task"), 1)

    def test_change_run_translates_yaml_budgets_to_runtime_kwargs(self) -> None:
        # Change mode does not create an AutonomousSession; it must translate
        # agent-studio.yaml autonomous.budgets directly into runtime.run kwargs.
        from orchestrator.cli import _agentic_runtime_budget_kwargs_from_overrides

        self.assertEqual(
            _agentic_runtime_budget_kwargs_from_overrides(
                {
                    "max_candidates_per_task": 1,
                    "max_repair_attempts_per_candidate": 1,
                }
            ),
            {"candidate_count": 1, "max_repair_loops": 1},
        )

    def test_change_run_ignores_invalid_candidate_budget(self) -> None:
        from orchestrator.cli import _agentic_runtime_budget_kwargs_from_overrides

        self.assertEqual(
            _agentic_runtime_budget_kwargs_from_overrides(
                {"max_candidates_per_task": 0, "max_repair_attempts_per_candidate": 0}
            ),
            {"max_repair_loops": 0},
        )


if __name__ == "__main__":
    unittest.main()
