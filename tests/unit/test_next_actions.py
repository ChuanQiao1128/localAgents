"""RC-1.1.4: branch tests for AutonomousController._render_next_actions.

The Next Actions block in final-run-status.md is the user's primary
"what do I do now" surface. Each pause reason should produce a
*different*, *deterministic* set of suggested CLI commands. Before
RC-1.1 only the smoke-failed branch was asserted (via the golden
failure path). These tests pin every other branch.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from orchestrator.core.autonomous import (
    AutonomousController,
    AutonomousSession,
    DEFAULT_BUDGETS,
    DEFAULT_DEPLOYMENT_STATE,
    DEFAULT_INTEGRATION_POLICY,
    SCHEMA_VERSION_SESSION,
)


def _make_session(**overrides) -> AutonomousSession:
    """Build an in-memory AutonomousSession for renderer tests. No disk
    state is touched; the controller's `_render_next_actions` is pure."""
    base = dict(
        schema_version=SCHEMA_VERSION_SESSION,
        session_id="session_x",
        project_id="project_x",
        status="paused",
        current_task_id=None,
        branch="agentic/autonomous/session_x",
        started_at="2026-05-10T00:00:00+00:00",
        updated_at="2026-05-10T00:01:00+00:00",
        pause_reason=None,
        halt_requested=False,
    )
    base.update(overrides)
    return AutonomousSession(**base)


def _make_controller(tmp: Path) -> AutonomousController:
    project = {"id": "project_x", "name": "x", "path": str(tmp / "p")}
    Path(project["path"]).mkdir()
    return AutonomousController(project=project, run_inner_loop=lambda **kw: None)


def _render(controller: AutonomousController, session: AutonomousSession,
            *, deployment_state: dict[str, Any] | None = None,
            open_review_items: list[Any] | None = None,
            all_deployments: list[dict[str, Any]] | None = None) -> list[str]:
    """Convenience wrapper around the private renderer. Returns the list
    of action strings (each will be prefixed with `- ` in the report)."""
    deployment_state = deployment_state if deployment_state is not None else dict(DEFAULT_DEPLOYMENT_STATE)
    return controller._render_next_actions(  # noqa: SLF001
        session, deployment_state, open_review_items or [], all_deployments or [],
    )


# ===========================================================================
# Each pause_reason gets at least one branch-specific assertion
# ===========================================================================
class PausedBranchTests(unittest.TestCase):
    def test_open_review_items_yield_reviews_list_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session()

            class _Stub:
                review_id = "rv_1"
            actions = _render(controller, session, open_review_items=[_Stub(), _Stub()])
            self.assertTrue(any("autonomous reviews list" in a and "2 open review" in a for a in actions))

    def test_smoke_check_failed_suggests_smoke_rerun(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="paused", pause_reason="smoke-check-failed")
            actions = _render(controller, session)
            self.assertTrue(any("autonomous smoke" in a for a in actions))
            self.assertFalse(any("autonomous rollback" in a for a in actions),
                             "smoke-check-failed alone should NOT suggest rollback")

    def test_rolled_back_pause_suggests_both_smoke_and_rollback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="paused", pause_reason="smoke-check-failed-rolled-back")
            actions = _render(controller, session)
            self.assertTrue(any("autonomous smoke" in a for a in actions))
            self.assertTrue(any("autonomous rollback" in a and "--dry-run" in a for a in actions))

    def test_rollback_failed_suggests_rollback_dry_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="paused", pause_reason="rollback-failed")
            actions = _render(controller, session)
            self.assertTrue(any("autonomous rollback" in a and "--dry-run" in a for a in actions))

    def test_deployment_failed_suggests_dry_run_and_artifact_inspection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="paused", pause_reason="deployment-failed")
            actions = _render(controller, session)
            self.assertTrue(any("autonomous deploy" in a and "--dry-run" in a for a in actions))
            self.assertTrue(any("deployment artifact" in a or "review item" in a for a in actions))

    def test_apply_failed_suggests_candidate_inspection_and_review_approve(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            # Real pause_reason values include the exception type:
            # "apply_failed:ApplyGateRefused".
            session = _make_session(status="paused", pause_reason="apply_failed:ApplyGateRefused")
            actions = _render(controller, session)
            self.assertTrue(any("agentic-candidates list" in a for a in actions))
            self.assertTrue(any("reviews approve" in a for a in actions))

    def test_max_abandoned_pause_suggests_abandonments_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="paused", pause_reason="budget:max_abandoned_tasks")
            actions = _render(controller, session)
            self.assertTrue(any("agentic-abandonments list" in a for a in actions))

    def test_too_many_corrective_pause_suggests_reviews_and_abandonments(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="paused", pause_reason="too-many-corrective-tasks")
            actions = _render(controller, session)
            self.assertTrue(any("agentic-abandonments list" in a for a in actions))
            self.assertTrue(any("autonomous reviews list" in a for a in actions))

    def test_halt_requested_suggests_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="paused", pause_reason="halt_requested")
            actions = _render(controller, session)
            self.assertTrue(any("autonomous resume" in a for a in actions))

    def test_inner_run_exception_suggests_logs_tail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="paused", pause_reason="inner_run_exception:RuntimeError")
            actions = _render(controller, session)
            self.assertTrue(any("autonomous logs" in a and "--tail" in a for a in actions))


class CompletedBranchTests(unittest.TestCase):
    def test_completed_verified_with_url_reports_smoke_verified(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="completed", pause_reason=None)
            deployment_state = dict(DEFAULT_DEPLOYMENT_STATE)
            deployment_state["enabled"] = True
            deployment_state["status"] = "verified"
            deployment_state["latest_deployment_url"] = "https://verified.vercel.app"
            actions = _render(controller, session, deployment_state=deployment_state,
                              all_deployments=[{"deployment_id": "d1"}])
            self.assertTrue(any("smoke-verified" in a and "https://verified.vercel.app" in a for a in actions))

    def test_completed_deployed_without_smoke_reports_skipped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="completed", pause_reason=None)
            deployment_state = dict(DEFAULT_DEPLOYMENT_STATE)
            deployment_state["enabled"] = True
            deployment_state["status"] = "deployed"
            deployment_state["latest_deployment_url"] = "https://only-deploy.vercel.app"
            actions = _render(controller, session, deployment_state=deployment_state,
                              all_deployments=[{"deployment_id": "d1"}])
            self.assertTrue(any("smoke checks were skipped" in a for a in actions))

    def test_completed_with_deploy_disabled_suggests_enabling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            session = _make_session(status="completed", pause_reason=None)
            deployment_state = dict(DEFAULT_DEPLOYMENT_STATE)  # enabled=False, no deployments
            actions = _render(controller, session, deployment_state=deployment_state,
                              all_deployments=[])
            self.assertTrue(any("deploy is disabled" in a and "autonomous deploy" in a for a in actions))


class DifferentReasonsRenderDifferentTextTests(unittest.TestCase):
    """Pin the property the brief specifically called out: distinct
    pause_reasons MUST produce distinct Next Actions text."""

    def test_distinct_pause_reasons_render_distinct_text(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            controller = _make_controller(Path(tmp))
            renders: dict[str, str] = {}
            for reason in (
                "smoke-check-failed",
                "smoke-check-failed-rolled-back",
                "rollback-failed",
                "deployment-failed",
                "apply_failed:ApplyGateRefused",
                "budget:max_abandoned_tasks",
                "too-many-corrective-tasks",
                "halt_requested",
                "inner_run_exception:RuntimeError",
            ):
                session = _make_session(status="paused", pause_reason=reason)
                renders[reason] = "\n".join(_render(controller, session))
            # Build the set of unique renderings; require all distinct.
            self.assertEqual(
                len(set(renders.values())), len(renders),
                f"two pause reasons produced identical Next Actions: {renders}",
            )


if __name__ == "__main__":
    unittest.main()
