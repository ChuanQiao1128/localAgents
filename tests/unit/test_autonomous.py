from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from orchestrator.core.autonomous import (
    AutonomousController,
    AutonomousSession,
    DEFAULT_BUDGETS,
    DEFAULT_INTEGRATION_POLICY,
    EVENT_TYPES,
    SCHEMA_VERSION_CORRECTIVE_TASK,
    SCHEMA_VERSION_INTEGRATION_FAILURE,
    SCHEMA_VERSION_TASK_GRAPH,
    build_corrective_task,
    build_integration_commands,
    controller_log_file,
    detect_integration_failure_type,
    extract_suspected_files,
    find_active_session,
    has_pending_corrective_for_fingerprint,
    ingest_requirements,
    integration_failure_artifact_path,
    integration_failure_summary_file,
    integration_failures_dir,
    integration_results_file,
    parse_requirements_md,
    read_integration_failures,
    read_integration_results,
    read_task_graph,
    record_integration_result,
    render_acceptance_criteria,
    render_architecture_md,
    render_prd_md,
    run_integration_check,
    session_file,
    write_integration_failure_artifact,
    write_task_graph,
)


@dataclass
class _StubResult:
    run_id: str
    decision: str
    candidate: str = ""
    run_dir: Path = Path("/tmp")


class ParserTests(unittest.TestCase):
    def test_h1_becomes_project_title(self) -> None:
        graph = parse_requirements_md("# My Project\n\nOverview text.\n\n## Task one\n\nIntent body.\n", Path("/tmp"))
        self.assertEqual(graph["project_title"], "My Project")
        self.assertEqual(graph["overview"].strip(), "Overview text.")
        self.assertEqual(len(graph["tasks"]), 1)

    def test_h2_sections_become_tasks(self) -> None:
        md = (
            "# Project\n\n"
            "## First task\n\nIntent A\n\n"
            "## Second task\n\nIntent B\n"
        )
        graph = parse_requirements_md(md, Path("/tmp"))
        self.assertEqual([t["title"] for t in graph["tasks"]], ["First task", "Second task"])
        self.assertEqual([t["id"] for t in graph["tasks"]], ["task-001", "task-002"])

    def test_acceptance_criteria_parsed_from_bullets(self) -> None:
        md = "## A task\n\nDo a thing.\n\n- Criterion one\n- Criterion two\n"
        graph = parse_requirements_md(md, Path("/tmp"))
        self.assertEqual(graph["tasks"][0]["acceptance_criteria"], ["Criterion one", "Criterion two"])

    def test_dependencies_parsed_from_depends_line_and_titles(self) -> None:
        md = (
            "## Build form\n\nIntent\n\n"
            "## Wire submit\n\nIntent\n\nDepends: Build form\n"
        )
        graph = parse_requirements_md(md, Path("/tmp"))
        self.assertEqual(graph["tasks"][0]["dependencies"], [])
        self.assertEqual(graph["tasks"][1]["dependencies"], ["task-001"])

    def test_scope_paths_default_from_repo_layout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            (project_path / "apps/web").mkdir(parents=True)
            (project_path / "tests").mkdir()
            graph = parse_requirements_md("## task\n\nIntent\n", project_path)
            scope = graph["tasks"][0]["scope_paths"]
            self.assertIn("apps/web/**", scope)
            self.assertIn("tests/**", scope)

    def test_explicit_scope_overrides_default(self) -> None:
        graph = parse_requirements_md("## task\n\nIntent\n\nScope: src/api/**, tests/api/**\n", Path("/tmp"))
        self.assertEqual(graph["tasks"][0]["scope_paths"], ["src/api/**", "tests/api/**"])

    def test_scope_strips_wrapping_backticks(self) -> None:
        """RC-4C.1.A regression: writers naturally write ``Scope: `app/**` ``
        because backticks render as code in markdown previewers. Pre-fix the
        parser captured the backticks literally and `fnmatch("app/page.tsx",
        "`app/**`")` returned False, making the Promotion Gate's
        diff_within_scope check fail on every patched file. The first real
        Codex run on RC-4B (run_0f41f8b7ee on ai-writing-quality-editor)
        surfaced this bug — the parser MUST strip wrapping backticks."""
        graph = parse_requirements_md(
            "## task\n\nIntent\n\nScope: `app/**`, `components/**`\n",
            Path("/tmp"),
        )
        self.assertEqual(graph["tasks"][0]["scope_paths"], ["app/**", "components/**"])

    def test_scope_strips_wrapping_backticks_single_value(self) -> None:
        graph = parse_requirements_md(
            "## task\n\nIntent\n\nScope: `app/**`\n",
            Path("/tmp"),
        )
        self.assertEqual(graph["tasks"][0]["scope_paths"], ["app/**"])

    def test_scope_multiline_bullet_form(self) -> None:
        """RC-4C.1.A: a `Scope:` line with no inline value opens a multi-line
        bullet block. Cleaner for writers than comma-separated, and what
        Chuan asked for explicitly in RC-4C.1.B."""
        md = (
            "## task\n\nIntent\n\n"
            "Scope:\n"
            "- app/**\n"
            "- components/**\n"
            "- lib/**\n"
        )
        graph = parse_requirements_md(md, Path("/tmp"))
        self.assertEqual(
            graph["tasks"][0]["scope_paths"],
            ["app/**", "components/**", "lib/**"],
        )

    def test_scope_multiline_bullets_with_backticks_also_cleaned(self) -> None:
        """Defense in depth: bullets inside a multi-line Scope block also
        get backtick-stripped, in case the writer mixes both habits."""
        md = (
            "## task\n\nIntent\n\n"
            "Scope:\n"
            "- `app/**`\n"
            "- `components/**`\n"
        )
        graph = parse_requirements_md(md, Path("/tmp"))
        self.assertEqual(
            graph["tasks"][0]["scope_paths"],
            ["app/**", "components/**"],
        )

    def test_scope_multiline_block_closes_on_next_meta_line(self) -> None:
        """Multi-line Scope block must close when the next non-bullet line
        appears (Risk:, prose, another `Foo:` line). Otherwise unrelated
        bullets later in the section would leak into scope_paths."""
        md = (
            "## task\n\nIntent\n\n"
            "Scope:\n"
            "- app/**\n"
            "\n"
            "Risk: low\n"
            "\n"
            "Acceptance:\n"
            "- the build passes\n"
        )
        graph = parse_requirements_md(md, Path("/tmp"))
        task = graph["tasks"][0]
        self.assertEqual(task["scope_paths"], ["app/**"])
        self.assertEqual(task["risk"], "low")
        self.assertEqual(task["acceptance_criteria"], ["the build passes"])

    def test_acceptance_multiline_bullet_block(self) -> None:
        """`Acceptance:` opener supports the same bullet-block form so
        writers can pick either inline or block form per metadata field."""
        md = (
            "## task\n\nIntent\n\n"
            "Scope:\n"
            "- app/**\n"
            "\n"
            "Acceptance:\n"
            "- first thing passes\n"
            "- second thing passes\n"
        )
        graph = parse_requirements_md(md, Path("/tmp"))
        self.assertEqual(
            graph["tasks"][0]["acceptance_criteria"],
            ["first thing passes", "second thing passes"],
        )

    def test_risk_parsed_low_medium_high(self) -> None:
        md = (
            "## low\n\nIntent\n\nRisk: low\n\n"
            "## high\n\nIntent\n\nRisk: high\n"
        )
        graph = parse_requirements_md(md, Path("/tmp"))
        self.assertEqual(graph["tasks"][0]["risk"], "low")
        self.assertEqual(graph["tasks"][1]["risk"], "high")

    def test_each_task_is_bounded(self) -> None:
        # MVP-4A acceptance #2: every parsed task must carry intent /
        # scope_paths / acceptance_criteria / dependencies / status.
        md = "# P\n## task one\nIntent one\n- AC\n"
        graph = parse_requirements_md(md, Path("/tmp"))
        for task in graph["tasks"]:
            self.assertTrue(task["intent"])
            self.assertIsInstance(task["scope_paths"], list)
            self.assertIsInstance(task["acceptance_criteria"], list)
            self.assertIsInstance(task["dependencies"], list)
            self.assertEqual(task["status"], "pending")
            self.assertIn("id", task)


class IngestTests(unittest.TestCase):
    def test_ingest_writes_all_five_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            req = project_path / "src.md"
            req.write_text("# X\n\n## T1\n\nIntent\n", encoding="utf-8")
            graph = ingest_requirements(project_path, req)
            self.assertTrue((project_path / "requirements.md").exists())
            self.assertTrue((project_path / "prd.md").exists())
            self.assertTrue((project_path / "acceptance-criteria.json").exists())
            self.assertTrue((project_path / "architecture.md").exists())
            self.assertTrue((project_path / "task-graph.json").exists())
            ac = json.loads((project_path / "acceptance-criteria.json").read_text(encoding="utf-8"))
            self.assertEqual(len(ac["tasks"]), 1)
            self.assertEqual(graph["schema_version"], SCHEMA_VERSION_TASK_GRAPH)


class ControllerTests(unittest.TestCase):
    """Cover the controller's task-picker, budget, halt, and decision branches
    using injected callbacks (no real AgenticProjectRuntime, no git)."""

    def _make_project(self, tmp: str) -> dict[str, Any]:
        project_path = Path(tmp) / "proj"
        project_path.mkdir()
        return {"id": "project_x", "name": "x", "path": str(project_path)}

    def _seed_graph(self, project_path: Path, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": tasks}
        write_task_graph(project_path, graph)
        return graph

    def _result(self, run_id: str, decision: str, candidate: str = "candidate-a") -> _StubResult:
        return _StubResult(run_id=run_id, decision=decision, candidate=candidate)

    def test_next_task_respects_dependencies(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "A", "intent": "x", "acceptance_criteria": [], "scope_paths": [],
                 "dependencies": [], "status": "completed", "risk": "low", "run_ids": [], "commit": None},
                {"id": "task-002", "title": "B", "intent": "x", "acceptance_criteria": [], "scope_paths": [],
                 "dependencies": ["task-003"], "status": "pending", "risk": "low", "run_ids": [], "commit": None},
                {"id": "task-003", "title": "C", "intent": "x", "acceptance_criteria": [], "scope_paths": [],
                 "dependencies": ["task-001"], "status": "pending", "risk": "low", "run_ids": [], "commit": None},
            ])
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            self.assertEqual(controller.next_task(graph)["id"], "task-003")

    def test_next_task_returns_none_when_all_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "A", "intent": "x", "acceptance_criteria": [], "scope_paths": [],
                 "dependencies": [], "status": "completed", "risk": "low", "run_ids": [], "commit": None},
            ])
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            self.assertIsNone(controller.next_task(graph))

    def test_promote_path_calls_apply_and_marks_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "A", "intent": "x", "acceptance_criteria": [], "scope_paths": ["**"],
                 "dependencies": [], "status": "pending", "risk": "low", "run_ids": [], "commit": None},
            ])
            apply_calls: list[dict[str, Any]] = []

            def _apply(**kw):
                apply_calls.append(kw)
                return {"strategy": "test-focused", "candidate": kw["selected_candidate"], "applied": True}

            # Patch commit_task to avoid invoking real git from this unit test.
            with patch("orchestrator.core.autonomous.commit_task", return_value="abc1234"):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: self._result("run_1", "promote", candidate="candidate-b"),
                    apply_candidate=_apply,
                )
                session = controller.start_or_resume()
                outcome = controller.advance_one_task(session, graph)
            self.assertEqual(outcome.decision, "promote")
            self.assertEqual(outcome.new_status, "completed")
            self.assertEqual(outcome.commit, "abc1234")
            self.assertEqual(graph["tasks"][0]["status"], "completed")
            self.assertEqual(graph["tasks"][0]["commit"], "abc1234")
            self.assertEqual(session.counters["completed_tasks"], 1)
            self.assertEqual(len(apply_calls), 1)

    def test_needs_human_review_pauses_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "A", "intent": "x", "acceptance_criteria": [], "scope_paths": [],
                 "dependencies": [], "status": "pending", "risk": "low", "run_ids": [], "commit": None},
            ])
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: self._result("run_1", "needs-human-review"),
            )
            session = controller.start_or_resume()
            outcome = controller.advance_one_task(session, graph)
            self.assertEqual(outcome.new_status, "needs-human-review")
            self.assertEqual(session.status, "paused")
            self.assertEqual(session.pause_reason, "needs_human_review")

    def test_abandoned_continues_until_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": f"task-{i:03d}", "title": f"T{i}", "intent": "x", "acceptance_criteria": [], "scope_paths": [],
                 "dependencies": [], "status": "pending", "risk": "low", "run_ids": [], "commit": None}
                for i in range(1, 4)
            ])
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: self._result("run_x", "abandoned"),
            )
            session = controller.start_or_resume()
            # First abandoned: counter=1, under threshold (max=2), session stays running.
            controller.advance_one_task(session, graph)
            self.assertEqual(session.counters["abandoned_tasks"], 1)
            self.assertEqual(session.status, "running")
            # Second abandoned: counter=2, EQUALS threshold → next budget check pauses.
            controller.advance_one_task(session, graph)
            self.assertEqual(session.counters["abandoned_tasks"], 2)
            self.assertEqual(session.status, "paused")
            self.assertIn("max_abandoned_tasks", session.pause_reason or "")

    def test_inner_run_exception_is_caught_and_pauses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "A", "intent": "x", "acceptance_criteria": [], "scope_paths": [],
                 "dependencies": [], "status": "pending", "risk": "low", "run_ids": [], "commit": None},
            ])
            def _boom(**kw):
                raise RuntimeError("inner exploded")
            controller = AutonomousController(project=project, run_inner_loop=_boom)
            session = controller.start_or_resume()
            outcome = controller.advance_one_task(session, graph)
            self.assertEqual(outcome.new_status, "needs-human-review")
            self.assertEqual(session.status, "paused")
            self.assertIn("inner_run_exception", session.pause_reason or "")

    def test_failed_apply_marks_task_needs_human_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "A", "intent": "x", "acceptance_criteria": [], "scope_paths": ["**"],
                 "dependencies": [], "status": "pending", "risk": "low", "run_ids": [], "commit": None},
            ])
            def _boom_apply(**kw):
                raise RuntimeError("apply failed")
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: self._result("run_1", "promote"),
                apply_candidate=_boom_apply,
            )
            session = controller.start_or_resume()
            outcome = controller.advance_one_task(session, graph)
            self.assertEqual(outcome.new_status, "needs-human-review")
            self.assertEqual(session.status, "paused")
            self.assertIn("apply_failed", session.pause_reason or "")

    def test_halt_request_pauses_before_next_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "A", "intent": "x", "acceptance_criteria": [], "scope_paths": [],
                 "dependencies": [], "status": "pending", "risk": "low", "run_ids": [], "commit": None},
            ])
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            session.halt_requested = True
            outcome = controller.advance_one_task(session, graph)
            self.assertIsNone(outcome)
            self.assertEqual(session.status, "paused")
            self.assertEqual(session.pause_reason, "halt_requested")

    def test_session_completes_when_no_more_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [])
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            outcome = controller.advance_one_task(session, graph)
            self.assertIsNone(outcome)
            self.assertEqual(session.status, "completed")

    def test_session_state_persists_between_instances(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "A", "intent": "x", "acceptance_criteria": [], "scope_paths": [],
                 "dependencies": [], "status": "pending", "risk": "low", "run_ids": [], "commit": None},
            ])
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: self._result("run_1", "needs-human-review"),
            )
            session = controller.start_or_resume()
            session_id = session.session_id
            controller.advance_one_task(session, graph)
            # New controller instance reads the same session.
            controller2 = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session2 = controller2.start_or_resume()
            # start_or_resume reactivates the paused session (status → running again).
            self.assertEqual(session2.session_id, session_id)
            self.assertEqual(session2.counters["needs_review_tasks"], 1)

    def test_commit_message_trailers_on_successful_promote(self) -> None:
        # Patch commit_task to capture its kwargs and verify trailer fields.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "Add feature X", "intent": "x", "acceptance_criteria": [],
                 "scope_paths": ["**"], "dependencies": [], "status": "pending", "risk": "low", "run_ids": [], "commit": None},
            ])
            captured: dict[str, Any] = {}

            def _capture_commit(_project_path, **kwargs):
                captured.update(kwargs)
                return "abc1234"

            with patch("orchestrator.core.autonomous.commit_task", side_effect=_capture_commit):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: self._result("run_42", "promote", candidate="candidate-c"),
                    apply_candidate=lambda **kw: {"strategy": "broader-fix", "candidate": "candidate-c"},
                )
                session = controller.start_or_resume()
                controller.advance_one_task(session, graph)
            self.assertEqual(captured["task"]["title"], "Add feature X")
            self.assertEqual(captured["run_id"], "run_42")
            self.assertEqual(captured["selected_candidate"], "candidate-c")
            self.assertEqual(captured["candidate_strategy"], "broader-fix")
            self.assertEqual(captured["promotion_decision"], "promote")
            self.assertIn("run_42", captured["promotion_report_relpath"])

    def test_event_types_are_consistent(self) -> None:
        # Smoke: every event the controller emits during a happy promote path
        # must be one of the declared EVENT_TYPES.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "A", "intent": "x", "acceptance_criteria": [], "scope_paths": ["**"],
                 "dependencies": [], "status": "pending", "risk": "low", "run_ids": [], "commit": None},
            ])
            with patch("orchestrator.core.autonomous.commit_task", return_value="abc1234"):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: self._result("run_1", "promote"),
                    apply_candidate=lambda **kw: {"strategy": "x"},
                )
                session = controller.start_or_resume()
                controller.advance_one_task(session, graph)
            log_path = controller_log_file(Path(project["path"]), session.session_id)
            events = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line]
            for event in events:
                self.assertIn(event["event"], EVENT_TYPES, f"Unknown event type: {event['event']}")


class FindActiveSessionTests(unittest.TestCase):
    def test_find_returns_none_when_no_sessions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(find_active_session(Path(tmp)))

    def test_find_returns_session_after_start_or_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = {"id": "p", "name": "x", "path": tmp}
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            found = find_active_session(Path(tmp))
            self.assertIsNotNone(found)
            self.assertEqual(found.session_id, session.session_id)


class StartOrResumeChangeIdTests(unittest.TestCase):
    """RC-5A.13: when a new change_id arrives, `start_or_resume` must NOT
    reuse a session that belongs to a different change. This was the
    root cause of session_c1de... being silently re-paused on
    `budget:max_needs_human_review_tasks` when change_10f6... ran."""

    def _new_controller(self, tmp: str) -> AutonomousController:
        project = {"id": "p", "name": "x", "path": tmp}
        return AutonomousController(project=project, run_inner_loop=lambda **kw: None)

    def test_resumes_when_change_id_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctl = self._new_controller(tmp)
            first = ctl.start_or_resume(change_id="change_aaa")
            self.assertEqual(first.change_id, "change_aaa")
            second = ctl.start_or_resume(change_id="change_aaa")
            self.assertEqual(
                second.session_id, first.session_id,
                msg="same change_id must resume the same session",
            )

    def test_creates_new_session_for_different_change_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ctl = self._new_controller(tmp)
            first = ctl.start_or_resume(change_id="change_aaa")
            # Simulate the prior change pausing on a budget reason —
            # exactly the dogfood scenario.
            first.status = "paused"
            first.pause_reason = "budget:max_needs_human_review_tasks"
            ctl._save_session(first)

            second = ctl.start_or_resume(change_id="change_bbb")
            self.assertNotEqual(
                second.session_id, first.session_id,
                msg="different change_id MUST get a fresh session",
            )
            self.assertEqual(second.change_id, "change_bbb")
            self.assertEqual(second.status, "running")
            self.assertIsNone(second.pause_reason)

    def test_creates_new_session_when_prior_paused_on_budget_same_change(self) -> None:
        """Even with the SAME change_id, if the prior session is paused
        on a budget reason, we should create a fresh one — otherwise the
        budget would re-fire immediately on resume."""
        with tempfile.TemporaryDirectory() as tmp:
            ctl = self._new_controller(tmp)
            first = ctl.start_or_resume(change_id="change_aaa")
            first.status = "paused"
            first.pause_reason = "budget:max_needs_human_review_tasks"
            ctl._save_session(first)

            second = ctl.start_or_resume(change_id="change_aaa")
            self.assertNotEqual(second.session_id, first.session_id)
            self.assertIsNone(second.pause_reason)

    def test_change_run_does_not_resume_autonomous_mode_session(self) -> None:
        """An existing autonomous-mode session (change_id is None) must
        NOT be resumed by a change run — the change should get a fresh
        change-bound session."""
        with tempfile.TemporaryDirectory() as tmp:
            ctl = self._new_controller(tmp)
            autonomous = ctl.start_or_resume()  # change_id=None
            self.assertIsNone(autonomous.change_id)

            change_session = ctl.start_or_resume(change_id="change_aaa")
            self.assertNotEqual(change_session.session_id, autonomous.session_id)
            self.assertEqual(change_session.change_id, "change_aaa")

    def test_plain_autonomous_resume_still_resumes_paused_budget(self) -> None:
        """Backward-compat: plain `autonomous resume` (no change_id)
        keeps the historical "always resume" semantics, even if the
        prior session was budget-paused. The "fresh on budget" rule only
        applies when a change_id is involved."""
        with tempfile.TemporaryDirectory() as tmp:
            ctl = self._new_controller(tmp)
            first = ctl.start_or_resume()
            first.status = "paused"
            first.pause_reason = "budget:max_needs_human_review_tasks"
            ctl._save_session(first)

            second = ctl.start_or_resume()
            self.assertEqual(
                second.session_id, first.session_id,
                msg="plain autonomous resume must keep historical resume behavior",
            )

    def test_change_id_persisted_through_round_trip(self) -> None:
        """AutonomousSession.change_id survives to_dict / from_dict."""
        with tempfile.TemporaryDirectory() as tmp:
            ctl = self._new_controller(tmp)
            ctl.start_or_resume(change_id="change_ccc")
            found = find_active_session(Path(tmp))
            self.assertIsNotNone(found)
            self.assertEqual(found.change_id, "change_ccc")


class IntegrationRunnerTests(unittest.TestCase):
    """MVP-4B unit tests for the integration phase plumbing."""

    def test_build_integration_commands_returns_empty_for_bare_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            commands = build_integration_commands(project_path)
            # Bare project: no apps/web, no package.json → no required commands.
            self.assertIsInstance(commands, list)
            self.assertEqual([c for c in commands if c.get("required")], [])

    def test_build_integration_commands_picks_up_static_html(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            (project_path / "apps/web").mkdir(parents=True)
            (project_path / "apps/web/index.html").write_text("<html></html>", encoding="utf-8")
            commands = build_integration_commands(project_path)
            names = [c.get("name") for c in commands]
            self.assertIn("static-html-present", names)

    def test_run_integration_check_no_commands_returns_passed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_integration_check(Path(tmp), [])
            self.assertTrue(result["passed"])
            self.assertEqual(result["failed_required_command_names"], [])
            self.assertEqual(result["commands_run"], [])

    def test_run_integration_check_executes_required_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            (project_path / "apps/web").mkdir(parents=True)
            (project_path / "apps/web/index.html").write_text("<html></html>", encoding="utf-8")
            commands = build_integration_commands(project_path)
            result = run_integration_check(project_path, commands)
            self.assertTrue(result["passed"], f"unexpected failure: {result}")
            self.assertEqual(result["failed_required_command_names"], [])
            self.assertGreaterEqual(len(result["commands_run"]), 1)

    def test_run_integration_check_failure_records_failed_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            # Construct a required command that always fails.
            commands = [{
                "name": "broken",
                "cmd": "node -e \"process.exit(2)\"",
                "required": True,
                "cwd": ".",
                "timeout_sec": 10,
                "type": "shell",
            }]
            result = run_integration_check(project_path, commands)
            self.assertFalse(result["passed"])
            self.assertEqual(result["failed_required_command_names"], ["broken"])

    def test_record_and_read_integration_results_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            for i in range(3):
                record_integration_result(project_path, "session_x", {"i": i, "passed": True})
            results = read_integration_results(project_path, "session_x")
            self.assertEqual([r["i"] for r in results], [0, 1, 2])

    def test_session_serialization_includes_integration_fields(self) -> None:
        # MVP-4B: AutonomousSession must round-trip the new fields.
        session = AutonomousSession(
            schema_version=1, session_id="s", project_id="p", status="running",
            current_task_id=None, branch="b", started_at="t", updated_at="t",
        )
        session.counters["integrations_run"] = 2
        session.counters["integrations_passed"] = 1
        session.counters["integrations_failed"] = 1
        session.last_integration_result = {"passed": False, "failed_required_command_names": ["x"]}
        round_tripped = AutonomousSession.from_dict(session.to_dict())
        self.assertEqual(round_tripped.counters["integrations_run"], 2)
        self.assertEqual(round_tripped.counters["integrations_passed"], 1)
        self.assertEqual(round_tripped.counters["integrations_failed"], 1)
        self.assertEqual(round_tripped.last_integration_result["failed_required_command_names"], ["x"])
        self.assertEqual(round_tripped.integration_policy["every_n_tasks"], 3)


class ControllerIntegrationTriggerTests(unittest.TestCase):
    """MVP-4B controller integration trigger logic, with mocked inner loop."""

    def _make_project(self, tmp: str) -> dict[str, Any]:
        project_path = Path(tmp) / "proj"
        project_path.mkdir()
        return {"id": "project_x", "name": "x", "path": str(project_path)}

    def _seed_graph(self, project_path: Path, count: int) -> dict[str, Any]:
        tasks = [
            {"id": f"task-{i:03d}", "title": f"T{i}", "intent": "x", "acceptance_criteria": [],
             "scope_paths": ["**"], "dependencies": [], "status": "pending", "risk": "low",
             "run_ids": [], "commit": None}
            for i in range(1, count + 1)
        ]
        graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": tasks}
        write_task_graph(project_path, graph)
        return graph

    def _result(self, run_id: str, decision: str = "promote", candidate: str = "candidate-a") -> Any:
        @dataclass
        class _R:
            run_id: str
            decision: str
            candidate: str = ""
            run_dir: Path = Path("/tmp")
        return _R(run_id=run_id, decision=decision, candidate=candidate)

    def test_periodic_integration_runs_after_every_n_completed_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), 4)
            integration_calls: list[str] = []

            def _fake_run_integration(project_path, commands, timeout_sec=600):
                integration_calls.append("run")
                return {
                    "schema_version": 1, "started_at": "t", "finished_at": "t", "duration_sec": 0.1,
                    "commands_run": [], "passed": True, "failed_required_command_names": [],
                    "reason": "ok",
                }

            with patch("orchestrator.core.autonomous.run_integration_check", side_effect=_fake_run_integration), \
                 patch("orchestrator.core.autonomous.commit_task", return_value="abc1234"), \
                 patch("orchestrator.core.autonomous.build_integration_commands", return_value=[]):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: self._result("run_x"),
                    apply_candidate=lambda **kw: {"strategy": "x"},
                )
                session = controller.start_or_resume()
                # every_n_tasks defaults to 3 → integration runs after task 3.
                # Then one more task (task 4) completes.
                # Then session is done → final integration runs at session_end.
                while True:
                    outcome = controller.advance_one_task(session, graph)
                    if outcome is None:
                        break

            # We expect: 1 periodic (after task 3) + 1 final (at session_end) = 2.
            self.assertEqual(len(integration_calls), 2)
            self.assertEqual(session.counters["integrations_run"], 2)
            self.assertEqual(session.counters["integrations_passed"], 2)

    def test_integration_failure_injects_corrective_and_pauses_at_budget(self) -> None:
        # MVP-4C semantics: integration failure no longer pauses immediately;
        # it injects a corrective task and the controller continues. Pause
        # only fires when the corrective budget is exhausted. We verify both:
        # (1) corrective is injected on first failure, (2) when the corrective
        # task itself fails to fix the build and we hit max_corrective_tasks=1,
        # the session pauses with reason "too-many-corrective-tasks".
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), 3)

            def _fake_failing(project_path, commands, timeout_sec=600):
                return {
                    "schema_version": 1, "started_at": "t", "finished_at": "t", "duration_sec": 0.1,
                    "commands_run": [{"name": "build", "passed": False, "executed": True, "exit_code": 1, "required": True, "stderr_tail": "Type error: x", "stdout_tail": ""}],
                    "passed": False, "failed_required_command_names": ["build"], "reason": "build failed",
                }

            with patch("orchestrator.core.autonomous.run_integration_check", side_effect=_fake_failing), \
                 patch("orchestrator.core.autonomous.commit_task", return_value="abc1234"), \
                 patch("orchestrator.core.autonomous.build_integration_commands", return_value=[{"name": "build", "required": True}]):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: self._result("run_x"),
                    apply_candidate=lambda **kw: {"strategy": "x"},
                )
                session = controller.start_or_resume()
                # Force a tight corrective budget so we exhaust quickly.
                session.budgets["max_corrective_tasks"] = 1
                # First normal task -> commit -> not yet at every_n=3.
                # Tasks 1,2,3 all promote+commit. After task 3, integration runs
                # and fails → injects corrective-001 (budget 1/1).
                # Then the corrective runs, commits, post-corrective integration
                # fails again → tries to inject another corrective; budget full
                # → pause with too-many-corrective-tasks.
                while session.status == "running":
                    outcome = controller.advance_one_task(session, graph)
                    if outcome is None:
                        break
            self.assertEqual(session.status, "paused")
            self.assertEqual(session.pause_reason, "too-many-corrective-tasks")
            self.assertEqual(session.counters["corrective_tasks_created"], 1)
            self.assertGreaterEqual(session.counters["integrations_failed"], 2)
            # Per-failure artifact written.
            failures_dir = (Path(project["path"]) / ".agent/autonomous/sessions" / session.session_id / "integration-failures")
            self.assertTrue(failures_dir.is_dir())
            failure_files = list(failures_dir.glob("*/integration-failure.json"))
            self.assertGreaterEqual(len(failure_files), 2)
            # Summary md still exists for human reading.
            summary_path = integration_failure_summary_file(Path(project["path"]), session.session_id)
            self.assertTrue(summary_path.exists())

    def test_run_at_session_end_skipped_when_no_completed_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), 0)  # no tasks
            ran: list[str] = []
            with patch("orchestrator.core.autonomous.run_integration_check", side_effect=lambda *a, **kw: ran.append("run") or {"passed": True, "failed_required_command_names": [], "commands_run": [], "schema_version": 1, "started_at": "t", "finished_at": "t", "duration_sec": 0.0, "reason": "ok"}):
                controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
                session = controller.start_or_resume()
                outcome = controller.advance_one_task(session, graph)
            self.assertIsNone(outcome)
            self.assertEqual(session.status, "completed")
            self.assertEqual(ran, [], "no completed tasks → no final integration")

    def test_every_n_tasks_zero_disables_periodic_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), 3)
            ran: list[str] = []

            def _f(project_path, commands, timeout_sec=600):
                ran.append("run")
                return {"passed": True, "failed_required_command_names": [], "commands_run": [], "schema_version": 1, "started_at": "t", "finished_at": "t", "duration_sec": 0.0, "reason": "ok"}

            with patch("orchestrator.core.autonomous.run_integration_check", side_effect=_f), \
                 patch("orchestrator.core.autonomous.commit_task", return_value="abc1234"), \
                 patch("orchestrator.core.autonomous.build_integration_commands", return_value=[]):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: self._result("run_x"),
                    apply_candidate=lambda **kw: {"strategy": "x"},
                )
                session = controller.start_or_resume()
                # Disable periodic integration; keep run_at_session_end on.
                session.integration_policy["every_n_tasks"] = 0
                while True:
                    outcome = controller.advance_one_task(session, graph)
                    if outcome is None:
                        break
            # Only the session_end integration runs.
            self.assertEqual(len(ran), 1)


class CorrectiveHelperTests(unittest.TestCase):
    """MVP-4C low-level helpers: failure-type detection, suspected files,
    artifact writer, builder, duplicate detection."""

    def test_detect_failure_type_classifies_known_categories(self) -> None:
        cases = [
            ({"name": "build", "stderr_tail": "Type error: x"}, "type_error"),
            ({"name": "e2e", "stderr_tail": "playwright failed"}, "e2e_failure"),
            ({"name": "unit", "stdout_tail": "expected 1 to equal 2"}, "unit_test_failure"),
            ({"name": "build", "cmd": "npm run build"}, "build_failure"),
            ({"name": "smoke", "stderr_tail": "opaque thing"}, "unknown"),
        ]
        for failed_command, expected in cases:
            self.assertEqual(
                detect_integration_failure_type(failed_command),
                expected,
                f"failed for {failed_command}",
            )

    def test_extract_suspected_files_pulls_paths(self) -> None:
        paths = extract_suspected_files({
            "stderr_tail": "src/api/users.ts:12 something wrong\nalso apps/web/app/page.tsx:3",
            "stdout_tail": "",
        })
        self.assertIn("src/api/users.ts", paths)
        self.assertIn("apps/web/app/page.tsx", paths)

    def test_write_integration_failure_artifact_records_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            integration_result = {
                "schema_version": 1, "started_at": "t", "finished_at": "t", "duration_sec": 0.1,
                "commands_run": [{
                    "name": "build", "cmd": "npm run build", "required": True,
                    "executed": True, "exit_code": 1, "passed": False,
                    "stderr_tail": "Type error: bad in apps/web/app/page.tsx",
                    "stdout_tail": "",
                }],
                "passed": False, "failed_required_command_names": ["build"], "reason": "build failed",
            }
            artifact = write_integration_failure_artifact(
                project_path,
                session_id="session_x", project_id="project_y",
                trigger="after_task", after_task_id="task-007", after_commit="abc1234",
                integration_result=integration_result,
            )
            self.assertEqual(artifact["schema_version"], SCHEMA_VERSION_INTEGRATION_FAILURE)
            self.assertTrue(artifact["failure_id"].startswith("integration_failure_"))
            self.assertEqual(artifact["session_id"], "session_x")
            self.assertEqual(artifact["after_task_id"], "task-007")
            self.assertEqual(artifact["failed_command"]["name"], "build")
            self.assertEqual(artifact["failed_command"]["exit_code"], 1)
            self.assertEqual(artifact["detected_failure_type"], "type_error")
            self.assertIn("apps/web/app/page.tsx", artifact["suspected_files"])
            # File was written.
            disk_path = integration_failure_artifact_path(project_path, "session_x", artifact["failure_id"])
            self.assertTrue(disk_path.exists())
            on_disk = json.loads(disk_path.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["failure_id"], artifact["failure_id"])

    def test_write_integration_failure_artifact_truncates_long_tails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            big = "x" * 50_000
            integration_result = {
                "commands_run": [{
                    "name": "build", "cmd": "npm run build", "required": True,
                    "executed": True, "exit_code": 1, "passed": False,
                    "stderr_tail": big, "stdout_tail": big,
                }],
                "passed": False, "failed_required_command_names": ["build"],
            }
            artifact = write_integration_failure_artifact(
                Path(tmp), session_id="s", project_id="p", trigger="manual",
                after_task_id=None, after_commit=None, integration_result=integration_result,
            )
            self.assertLessEqual(len(artifact["failed_command"]["stderr_tail"]), 3001)
            self.assertLessEqual(len(artifact["failed_command"]["stdout_tail"]), 3001)

    def test_build_corrective_task_shape_and_acceptance(self) -> None:
        failure = {
            "failure_id": "integration_failure_abcd",
            "after_task_id": "task-007",
            "detected_failure_type": "type_error",
            "failed_command": {"name": "build", "cmd": "npm run build"},
        }
        task = build_corrective_task(failure, sequence=2)
        self.assertEqual(task["id"], "task-fix-integration-002")
        self.assertEqual(task["status"], "pending")
        self.assertTrue(task["corrective"])
        self.assertEqual(task["source_failure_id"], "integration_failure_abcd")
        self.assertEqual(task["source_failed_command_name"], "build")
        self.assertEqual(task["source_after_task_id"], "task-007")
        self.assertEqual(task["source_failure_type"], "type_error")
        self.assertEqual(task["dependencies"], ["task-007"])
        self.assertGreaterEqual(len(task["acceptance_criteria"]), 1)
        # Acceptance criteria first line MUST reference the failed command.
        self.assertIn("npm run build", task["acceptance_criteria"][0])
        self.assertIn("task-007", task["title"])
        self.assertIn("task-007", task["intent"])

    def test_has_pending_corrective_detects_duplicates(self) -> None:
        graph = {"tasks": [
            {"id": "task-001", "status": "completed", "corrective": False},
            {"id": "task-fix-integration-001", "status": "pending", "corrective": True,
             "source_failed_command_name": "build", "source_after_task_id": "task-007", "source_failure_type": "type_error"},
        ]}
        self.assertTrue(has_pending_corrective_for_fingerprint(
            graph, failed_command_name="build", after_task_id="task-007", failure_type="type_error",
        ))
        # Different failure_type → not a duplicate.
        self.assertFalse(has_pending_corrective_for_fingerprint(
            graph, failed_command_name="build", after_task_id="task-007", failure_type="e2e_failure",
        ))
        # Different after_task_id → not a duplicate.
        self.assertFalse(has_pending_corrective_for_fingerprint(
            graph, failed_command_name="build", after_task_id="task-008", failure_type="type_error",
        ))

    def test_completed_corrective_does_not_block_new_one(self) -> None:
        graph = {"tasks": [
            {"id": "task-fix-integration-001", "status": "completed", "corrective": True,
             "source_failed_command_name": "build", "source_after_task_id": "task-007", "source_failure_type": "type_error"},
        ]}
        # Completed corrective should NOT block a new one — only pending/running do.
        self.assertFalse(has_pending_corrective_for_fingerprint(
            graph, failed_command_name="build", after_task_id="task-007", failure_type="type_error",
        ))


class CorrectiveTaskInjectionTests(unittest.TestCase):
    """MVP-4C: 12 acceptance scenarios at controller level."""

    def _make_project(self, tmp: str) -> dict[str, Any]:
        project_path = Path(tmp) / "proj"
        project_path.mkdir()
        return {"id": "project_x", "name": "x", "path": str(project_path)}

    def _seed_graph(self, project_path: Path, count: int) -> dict[str, Any]:
        tasks = [
            {"id": f"task-{i:03d}", "title": f"T{i}", "intent": "x", "acceptance_criteria": [],
             "scope_paths": ["**"], "dependencies": [], "status": "pending", "risk": "low",
             "run_ids": [], "commit": None}
            for i in range(1, count + 1)
        ]
        graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": tasks}
        write_task_graph(project_path, graph)
        return graph

    def _result(self, run_id: str, decision: str = "promote", candidate: str = "candidate-a") -> Any:
        @dataclass
        class _R:
            run_id: str
            decision: str
            candidate: str = ""
            run_dir: Path = Path("/tmp")
        return _R(run_id=run_id, decision=decision, candidate=candidate)

    def _run_until_pause_or_end(self, controller, session, graph, *, hard_stop: int = 50):
        steps = 0
        while session.status == "running":
            outcome = controller.advance_one_task(session, graph)
            steps += 1
            if outcome is None or steps >= hard_stop:
                break

    # --- Acceptance #1: failure artifact written
    def test_integration_failure_writes_artifact_with_failed_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), 3)
            with patch("orchestrator.core.autonomous.run_integration_check", return_value={
                "schema_version": 1, "started_at": "t", "finished_at": "t", "duration_sec": 0.1,
                "commands_run": [{"name": "build", "cmd": "npm run build", "required": True,
                                  "executed": True, "exit_code": 1, "passed": False,
                                  "stderr_tail": "Type error: src/x.ts", "stdout_tail": ""}],
                "passed": False, "failed_required_command_names": ["build"], "reason": "build failed",
            }), \
                 patch("orchestrator.core.autonomous.commit_task", return_value="abc1234"), \
                 patch("orchestrator.core.autonomous.build_integration_commands", return_value=[{"name": "build", "required": True}]):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: self._result("run_x"),
                    apply_candidate=lambda **kw: {"strategy": "x"},
                )
                session = controller.start_or_resume()
                self._run_until_pause_or_end(controller, session, graph)
            failures = read_integration_failures(Path(project["path"]), session.session_id)
            self.assertGreaterEqual(len(failures), 1)
            self.assertEqual(failures[0]["failed_command"]["name"], "build")
            self.assertEqual(failures[0]["detected_failure_type"], "type_error")

    # --- Acceptance #2 + #3: bounded corrective task with source_failure_id and acceptance referencing failed command
    def test_integration_failure_injects_bounded_corrective_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), 3)
            with patch("orchestrator.core.autonomous.run_integration_check", return_value={
                "schema_version": 1, "started_at": "t", "finished_at": "t", "duration_sec": 0.1,
                "commands_run": [{"name": "build", "cmd": "npm run build", "required": True,
                                  "executed": True, "exit_code": 1, "passed": False,
                                  "stderr_tail": "build error", "stdout_tail": ""}],
                "passed": False, "failed_required_command_names": ["build"], "reason": "build failed",
            }), \
                 patch("orchestrator.core.autonomous.commit_task", return_value="abc1234"), \
                 patch("orchestrator.core.autonomous.build_integration_commands", return_value=[{"name": "build", "required": True}]):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: self._result("run_x"),
                    apply_candidate=lambda **kw: {"strategy": "x"},
                )
                session = controller.start_or_resume()
                # Allow several correctives before pausing.
                session.budgets["max_corrective_tasks"] = 5
                self._run_until_pause_or_end(controller, session, graph, hard_stop=20)
            corrective_tasks = [t for t in graph["tasks"] if t.get("corrective")]
            self.assertGreaterEqual(len(corrective_tasks), 1)
            ct = corrective_tasks[0]
            # bounded
            self.assertTrue(ct["intent"])
            self.assertTrue(ct["scope_paths"])
            self.assertGreaterEqual(len(ct["acceptance_criteria"]), 1)
            # source traceable
            self.assertTrue(ct["source_failure_id"])
            self.assertEqual(ct["source_failed_command_name"], "build")
            # acceptance references failed command
            self.assertIn("npm run build", ct["acceptance_criteria"][0])

    # --- Acceptance #4: scheduler prioritizes ready corrective task
    def test_scheduler_prioritizes_ready_corrective_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            # 1 normal pending task + 1 pending corrective task; corrective MUST win.
            graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": [
                {"id": "task-001", "title": "normal", "intent": "x", "acceptance_criteria": [],
                 "scope_paths": [], "dependencies": [], "status": "pending", "risk": "low",
                 "run_ids": [], "commit": None},
                {"id": "task-fix-integration-001", "title": "corrective", "intent": "x",
                 "acceptance_criteria": [], "scope_paths": [], "dependencies": [], "status": "pending",
                 "risk": "medium", "corrective": True, "source_failure_id": "f", "source_failed_command_name": "build",
                 "source_after_task_id": None, "source_failure_type": "build_failure",
                 "run_ids": [], "commit": None},
            ]}
            write_task_graph(Path(project["path"]), graph)
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            picked = controller.next_task(graph)
            self.assertEqual(picked["id"], "task-fix-integration-001")

    # --- Acceptance #5: duplicate fingerprint does not insert duplicate
    def test_duplicate_integration_failure_does_not_insert_duplicate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), 0)
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            # Manually construct an integration failure and inject twice.
            failure = write_integration_failure_artifact(
                Path(project["path"]),
                session_id=session.session_id, project_id="project_x", trigger="manual",
                after_task_id="task-007", after_commit=None,
                integration_result={
                    "commands_run": [{"name": "build", "cmd": "npm run build", "required": True,
                                      "executed": True, "exit_code": 1, "passed": False, "stderr_tail": "Type error", "stdout_tail": ""}],
                    "passed": False, "failed_required_command_names": ["build"],
                },
            )
            paused1 = controller._try_inject_corrective_or_pause(session, graph, failure)
            paused2 = controller._try_inject_corrective_or_pause(session, graph, failure)
            self.assertFalse(paused1)
            self.assertFalse(paused2)
            corrective_tasks = [t for t in graph["tasks"] if t.get("corrective")]
            self.assertEqual(len(corrective_tasks), 1, "duplicate fingerprint must not double-insert")

    # --- Acceptance #6: max_corrective_tasks pauses session
    def test_max_corrective_tasks_pauses_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), 0)
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            session.budgets["max_corrective_tasks"] = 1
            session.counters["corrective_tasks_created"] = 1  # pretend we already injected one
            failure = write_integration_failure_artifact(
                Path(project["path"]),
                session_id=session.session_id, project_id="project_x", trigger="session_end",
                after_task_id=None, after_commit=None,
                integration_result={
                    "commands_run": [{"name": "build", "cmd": "npm run build", "required": True,
                                      "executed": True, "exit_code": 1, "passed": False, "stderr_tail": "Type error", "stdout_tail": ""}],
                    "passed": False, "failed_required_command_names": ["build"],
                },
            )
            paused = controller._try_inject_corrective_or_pause(session, graph, failure)
            self.assertTrue(paused)
            self.assertEqual(session.status, "paused")
            self.assertEqual(session.pause_reason, "too-many-corrective-tasks")

    # --- Acceptance #7: corrective task promote applies and commits with corrective trailers
    def test_corrective_task_commit_uses_corrective_trailers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            # Single corrective-only graph so we don't need to exercise the
            # full integration path; just verify commit_task receives the
            # corrective + source_failure_id kwargs.
            graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": [
                {"id": "task-fix-integration-001", "title": "Fix it", "intent": "x",
                 "acceptance_criteria": [], "scope_paths": [], "dependencies": [], "status": "pending",
                 "risk": "medium", "corrective": True, "source_failure_id": "integration_failure_xyz",
                 "source_failed_command_name": "build", "source_after_task_id": None,
                 "source_failure_type": "build_failure", "run_ids": [], "commit": None},
            ]}
            write_task_graph(Path(project["path"]), graph)
            captured: dict[str, Any] = {}

            def _capture(_project_path, **kwargs):
                captured.update(kwargs)
                return "abcd123"

            with patch("orchestrator.core.autonomous.commit_task", side_effect=_capture), \
                 patch("orchestrator.core.autonomous.run_integration_check", return_value={
                     "schema_version": 1, "started_at": "t", "finished_at": "t", "duration_sec": 0.0,
                     "commands_run": [], "passed": True, "failed_required_command_names": [], "reason": "ok",
                 }), \
                 patch("orchestrator.core.autonomous.build_integration_commands", return_value=[]):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: self._result("run_corrective"),
                    apply_candidate=lambda **kw: {"strategy": "broader-fix"},
                )
                session = controller.start_or_resume()
                controller.advance_one_task(session, graph)
            self.assertTrue(captured.get("corrective"))
            self.assertEqual(captured.get("source_failure_id"), "integration_failure_xyz")

    # --- Acceptance #8 + #9: corrective completion immediately reruns integration; pass continues normal task graph
    def test_corrective_completion_reruns_integration_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            # 1 normal task + 1 corrective (already injected). Inner loop
            # always promotes; integration check is patched to fail FIRST,
            # then pass on the post-corrective re-run, then pass at session_end.
            graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": [
                {"id": "task-001", "title": "T1", "intent": "x", "acceptance_criteria": [], "scope_paths": [],
                 "dependencies": [], "status": "completed", "risk": "low", "run_ids": [], "commit": "x"},
                {"id": "task-fix-integration-001", "title": "Fix", "intent": "x",
                 "acceptance_criteria": [], "scope_paths": [], "dependencies": [], "status": "pending",
                 "risk": "medium", "corrective": True, "source_failure_id": "f1",
                 "source_failed_command_name": "build", "source_after_task_id": None,
                 "source_failure_type": "build_failure", "run_ids": [], "commit": None},
            ]}
            write_task_graph(Path(project["path"]), graph)

            integration_call_count = {"n": 0}

            def _integration(_pp, _cmds, timeout_sec=600):
                integration_call_count["n"] += 1
                return {
                    "schema_version": 1, "started_at": "t", "finished_at": "t", "duration_sec": 0.1,
                    "commands_run": [{"name": "build", "cmd": "npm run build", "required": True,
                                      "executed": True, "exit_code": 0, "passed": True, "stderr_tail": "", "stdout_tail": ""}],
                    "passed": True, "failed_required_command_names": [], "reason": "ok",
                }

            with patch("orchestrator.core.autonomous.run_integration_check", side_effect=_integration), \
                 patch("orchestrator.core.autonomous.commit_task", return_value="cccc"), \
                 patch("orchestrator.core.autonomous.build_integration_commands", return_value=[{"name": "build", "required": True}]):
                controller = AutonomousController(
                    project=project,
                    run_inner_loop=lambda **kw: self._result("run_x"),
                    apply_candidate=lambda **kw: {"strategy": "x"},
                )
                session = controller.start_or_resume()
                # Set completed_tasks to 1 to reflect the seeded completed task.
                session.counters["completed_tasks"] = 1
                self._run_until_pause_or_end(controller, session, graph)

            # The corrective task completes → immediate post_corrective integration runs.
            log_path = controller_log_file(Path(project["path"]), session.session_id)
            events = [json.loads(l) for l in log_path.read_text(encoding="utf-8").splitlines() if l]
            event_types = [e["event"] for e in events]
            self.assertIn("corrective_task_started", event_types)
            self.assertIn("corrective_task_completed", event_types)
            # An integration_started event with reason=post_corrective must exist.
            post_corrective_starts = [e for e in events if e.get("event") == "integration_started" and e.get("reason") == "post_corrective"]
            self.assertGreaterEqual(len(post_corrective_starts), 1)
            self.assertEqual(session.counters["corrective_tasks_completed"], 1)

    # --- Acceptance #10: corrective task needs-human-review pauses session
    def test_corrective_task_needs_human_review_pauses_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": [
                {"id": "task-fix-integration-001", "title": "Fix", "intent": "x",
                 "acceptance_criteria": [], "scope_paths": [], "dependencies": [], "status": "pending",
                 "risk": "medium", "corrective": True, "source_failure_id": "f1",
                 "source_failed_command_name": "build", "source_after_task_id": None,
                 "source_failure_type": "build_failure", "run_ids": [], "commit": None},
            ]}
            write_task_graph(Path(project["path"]), graph)
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: self._result("run_x", decision="needs-human-review"),
            )
            session = controller.start_or_resume()
            outcome = controller.advance_one_task(session, graph)
            self.assertEqual(outcome.new_status, "needs-human-review")
            self.assertEqual(session.status, "paused")
            self.assertEqual(session.pause_reason, "needs_human_review")
            corrective = next(t for t in graph["tasks"] if t["id"] == "task-fix-integration-001")
            self.assertEqual(corrective["status"], "needs-human-review")

    # --- Acceptance #12: final-run-status lists integration failures + corrective tasks
    def test_final_run_status_lists_integration_failures_and_corrective_tasks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": [
                {"id": "task-fix-integration-001", "title": "Fix", "intent": "x",
                 "acceptance_criteria": [], "scope_paths": [], "dependencies": [], "status": "pending",
                 "risk": "medium", "corrective": True, "source_failure_id": "integration_failure_x",
                 "source_failed_command_name": "build", "source_after_task_id": None,
                 "source_failure_type": "build_failure", "run_ids": [], "commit": None},
            ]}
            write_task_graph(Path(project["path"]), graph)
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            # Seed a failure artifact so the report has something to list.
            write_integration_failure_artifact(
                Path(project["path"]),
                session_id=session.session_id, project_id="project_x", trigger="manual",
                after_task_id="task-007", after_commit=None,
                integration_result={
                    "commands_run": [{"name": "build", "cmd": "npm run build", "required": True,
                                      "executed": True, "exit_code": 1, "passed": False, "stderr_tail": "x", "stdout_tail": ""}],
                    "passed": False, "failed_required_command_names": ["build"],
                },
            )
            controller._update_final_status(session, graph)
            body = (Path(project["path"]) / ".agent/autonomous/sessions" / session.session_id / "final-run-status.md").read_text(encoding="utf-8")
            self.assertIn("## Corrective Tasks", body)
            self.assertIn("task-fix-integration-001", body)
            self.assertIn("## Integration Failures", body)
            self.assertIn("build", body)
            self.assertIn("after_task=task-007", body)


class ReviewQueueUnitTests(unittest.TestCase):
    """MVP-4D: low-level review_queue helpers + controller emit_review_item."""

    def _make_project(self, tmp: str) -> dict[str, Any]:
        project_path = Path(tmp) / "proj"
        project_path.mkdir()
        return {"id": "project_x", "name": "x", "path": str(project_path)}

    def test_create_read_update_review_item_round_trip(self) -> None:
        from orchestrator.core.review_queue import (
            ReviewItem, SCHEMA_VERSION_REVIEW_ITEM,
            create_review_item, read_review_item, update_review_item,
            list_review_items,
        )
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            item = ReviewItem(
                schema_version=SCHEMA_VERSION_REVIEW_ITEM,
                review_id="review_test1", session_id="sess1", project_id="proj1",
                status="open", severity="blocking", source_type="task_run",
                reason_code="needs-human-review",
                title="Foo", summary="Bar", task_id="task-007", run_id="run_xyz",
                candidate_id="candidate-b", promotion_decision="needs-human-review",
                evidence_paths=["a.json"], suggested_commands=["agent-studio status"],
            )
            create_review_item(project_path, item)
            loaded = read_review_item(project_path, "sess1", "review_test1")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.review_id, "review_test1")
            self.assertEqual(loaded.task_id, "task-007")
            loaded.status = "approved"
            update_review_item(project_path, loaded)
            again = read_review_item(project_path, "sess1", "review_test1")
            self.assertEqual(again.status, "approved")
            # list returns it
            all_items = list_review_items(project_path, "sess1")
            self.assertEqual(len(all_items), 1)
            open_only = list_review_items(project_path, "sess1", only_open=True)
            self.assertEqual(open_only, [])

    def test_list_review_items_skips_corrupt_files(self) -> None:
        from orchestrator.core.review_queue import (
            review_items_dir, list_review_items, ReviewItem, SCHEMA_VERSION_REVIEW_ITEM,
            create_review_item,
        )
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            create_review_item(project_path, ReviewItem(
                schema_version=SCHEMA_VERSION_REVIEW_ITEM,
                review_id="review_ok", session_id="s", project_id="p",
                status="open", severity="blocking", source_type="task_run",
                reason_code="needs-human-review", title="t", summary="s",
            ))
            corrupt_path = review_items_dir(project_path, "s") / "corrupt.json"
            corrupt_path.write_text("{not-json", encoding="utf-8")
            items = list_review_items(project_path, "s")
            self.assertEqual([i.review_id for i in items], ["review_ok"])

    def test_has_blocking_open_reviews_returns_true_only_when_blocking_open(self) -> None:
        from orchestrator.core.review_queue import (
            ReviewItem, SCHEMA_VERSION_REVIEW_ITEM, create_review_item,
            has_blocking_open_reviews,
        )
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            self.assertFalse(has_blocking_open_reviews(project_path, "sess"))
            create_review_item(project_path, ReviewItem(
                schema_version=SCHEMA_VERSION_REVIEW_ITEM,
                review_id="r1", session_id="sess", project_id="p",
                status="approved", severity="blocking", source_type="task_run",
                reason_code="needs-human-review", title="t", summary="s",
            ))
            self.assertFalse(has_blocking_open_reviews(project_path, "sess"))
            create_review_item(project_path, ReviewItem(
                schema_version=SCHEMA_VERSION_REVIEW_ITEM,
                review_id="r2", session_id="sess", project_id="p",
                status="open", severity="warning", source_type="task_run",
                reason_code="needs-human-review", title="t", summary="s",
            ))
            self.assertFalse(has_blocking_open_reviews(project_path, "sess"))
            create_review_item(project_path, ReviewItem(
                schema_version=SCHEMA_VERSION_REVIEW_ITEM,
                review_id="r3", session_id="sess", project_id="p",
                status="open", severity="blocking", source_type="task_run",
                reason_code="needs-human-review", title="t", summary="s",
            ))
            self.assertTrue(has_blocking_open_reviews(project_path, "sess"))


class ControllerReviewTriggerTests(unittest.TestCase):
    """MVP-4D: controller emits review items on the 5 spec triggers."""

    def _make_project(self, tmp: str) -> dict[str, Any]:
        project_path = Path(tmp) / "proj"
        project_path.mkdir()
        return {"id": "project_x", "name": "x", "path": str(project_path)}

    def _seed_graph(self, project_path: Path, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": tasks}
        write_task_graph(project_path, graph)
        return graph

    def _result(self, run_id: str, decision: str = "promote", candidate: str = "candidate-a") -> Any:
        @dataclass
        class _R:
            run_id: str
            decision: str
            candidate: str = ""
            run_dir: Path = Path("/tmp")
        return _R(run_id=run_id, decision=decision, candidate=candidate)

    def test_needs_human_review_creates_review_item(self) -> None:
        from orchestrator.core.review_queue import list_review_items
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "T1", "intent": "x", "acceptance_criteria": [],
                 "scope_paths": [], "dependencies": [], "status": "pending", "risk": "low",
                 "run_ids": [], "commit": None},
            ])
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: self._result("run_x", "needs-human-review", "candidate-b"),
            )
            session = controller.start_or_resume()
            controller.advance_one_task(session, graph)
            items = list_review_items(Path(project["path"]), session.session_id, only_open=True)
            self.assertEqual(len(items), 1)
            r = items[0]
            self.assertEqual(r.reason_code, "needs-human-review")
            self.assertEqual(r.task_id, "task-001")
            self.assertEqual(r.run_id, "run_x")
            self.assertEqual(r.candidate_id, "candidate-b")
            self.assertEqual(r.severity, "blocking")
            self.assertTrue(r.evidence_paths)
            self.assertTrue(any("promotion-report.json" in p for p in r.evidence_paths))

    def test_needs_more_context_creates_review_item(self) -> None:
        from orchestrator.core.review_queue import list_review_items
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "T1", "intent": "x", "acceptance_criteria": [],
                 "scope_paths": [], "dependencies": [], "status": "pending", "risk": "low",
                 "run_ids": [], "commit": None},
            ])
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: self._result("run_x", "needs-more-context"),
            )
            session = controller.start_or_resume()
            controller.advance_one_task(session, graph)
            items = list_review_items(Path(project["path"]), session.session_id, only_open=True)
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0].reason_code, "needs-more-context")
            self.assertEqual(items[0].source_type, "needs_more_context")
            # Evidence should include context-pack.json path.
            self.assertTrue(any("context-pack.json" in p for p in items[0].evidence_paths))

    def test_failed_apply_creates_review_item_with_patch_evidence(self) -> None:
        from orchestrator.core.review_queue import list_review_items
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-001", "title": "T1", "intent": "x", "acceptance_criteria": [],
                 "scope_paths": ["**"], "dependencies": [], "status": "pending", "risk": "low",
                 "run_ids": [], "commit": None},
            ])
            def _boom_apply(**kw):
                raise RuntimeError("apply gate refused: out_of_scope")
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: self._result("run_x", "promote", "candidate-c"),
                apply_candidate=_boom_apply,
            )
            session = controller.start_or_resume()
            controller.advance_one_task(session, graph)
            items = list_review_items(Path(project["path"]), session.session_id, only_open=True)
            self.assertEqual(len(items), 1)
            r = items[0]
            self.assertEqual(r.reason_code, "failed-apply")
            self.assertEqual(r.source_type, "apply_failure")
            self.assertEqual(r.task_id, "task-001")
            self.assertIn("out_of_scope", r.summary)
            self.assertTrue(any("patch.diff" in p for p in r.evidence_paths))

    def test_corrective_task_needs_review_creates_review_item_with_corrective_marker(self) -> None:
        from orchestrator.core.review_queue import list_review_items
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [
                {"id": "task-fix-integration-001", "title": "Fix it", "intent": "x",
                 "acceptance_criteria": [], "scope_paths": [], "dependencies": [], "status": "pending",
                 "risk": "medium", "corrective": True, "source_failure_id": "integration_failure_xx",
                 "source_failed_command_name": "build", "source_after_task_id": None,
                 "source_failure_type": "build_failure", "run_ids": [], "commit": None},
            ])
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: self._result("run_x", "needs-human-review"),
            )
            session = controller.start_or_resume()
            controller.advance_one_task(session, graph)
            items = list_review_items(Path(project["path"]), session.session_id, only_open=True)
            self.assertEqual(len(items), 1)
            r = items[0]
            self.assertEqual(r.task_id, "task-fix-integration-001")
            self.assertEqual(r.source_failure_id, "integration_failure_xx")
            self.assertIn("corrective task", r.summary)

    def test_too_many_corrective_tasks_creates_session_level_review_item(self) -> None:
        from orchestrator.core.review_queue import list_review_items
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [])
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            session.budgets["max_corrective_tasks"] = 1
            session.counters["corrective_tasks_created"] = 1  # already at limit
            failure = write_integration_failure_artifact(
                Path(project["path"]),
                session_id=session.session_id, project_id="project_x", trigger="session_end",
                after_task_id=None, after_commit=None,
                integration_result={
                    "commands_run": [{"name": "build", "cmd": "npm run build", "required": True,
                                      "executed": True, "exit_code": 1, "passed": False, "stderr_tail": "x", "stdout_tail": ""}],
                    "passed": False, "failed_required_command_names": ["build"],
                },
            )
            paused = controller._try_inject_corrective_or_pause(session, graph, failure)
            self.assertTrue(paused)
            items = list_review_items(Path(project["path"]), session.session_id, only_open=True)
            self.assertEqual(len(items), 1)
            r = items[0]
            self.assertEqual(r.reason_code, "too-many-corrective-tasks")
            self.assertEqual(r.source_type, "corrective_limit")
            self.assertIsNone(r.task_id)
            self.assertEqual(r.allowed_actions, ["show", "reject", "resolve"])


if __name__ == "__main__":
    unittest.main()
