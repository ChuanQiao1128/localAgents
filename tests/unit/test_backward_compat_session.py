"""RC-1.1: backward-compatibility regression tests.

Proves that on-disk artifacts written by older controller versions still
load + behave correctly under the current (MVP-4F + RC-1.1) controller.
The brief explicitly called out MVP-4D-era sessions (which lack every
MVP-4F deployment subkey) as the highest-risk drift point.
"""
from __future__ import annotations

import json
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
    find_active_session,
    read_task_graph,
    session_dir,
    session_file,
    write_task_graph,
)


# ---------------------------------------------------------------------------
# Hand-crafted MVP-4D-era session payload.
#
# At MVP-4D: the deployment block existed for review-queue alignment but
# had no MVP-4F subkeys — no latest_smoke_check_id / latest_smoke_status /
# latest_smoke_failure_type / latest_rollback_id / latest_rollback_status /
# latest_rollback_failure_type. Some early sessions also lacked
# `corrective_tasks_*` counters (MVP-4C) — we cover that too as a bonus.
#
# The MVP-4D controller is gone, so we cannot re-run it; instead we pin
# the on-disk shape it produced and prove the current controller can pick
# the session up without crashing.
# ---------------------------------------------------------------------------
def _mvp_4d_era_session_dict(*, session_id: str = "session_legacy", project_id: str = "project_legacy") -> dict[str, Any]:
    return {
        "schema_version": 1,
        "session_id": session_id,
        "project_id": project_id,
        "status": "paused",
        "current_task_id": "task-001",
        "branch": f"agentic/autonomous/{session_id}",
        "started_at": "2026-04-01T10:00:00+00:00",
        "updated_at": "2026-04-01T10:05:00+00:00",
        "budgets": {
            "max_tasks_per_session": 20,
            "max_total_inner_runs": 30,
            "max_abandoned_tasks": 2,
            "max_needs_review_tasks": 1,
            # NOTE: no max_corrective_tasks — that came with MVP-4C.
        },
        "counters": {
            "completed_tasks": 1,
            "abandoned_tasks": 0,
            "needs_review_tasks": 1,
            "inner_runs": 1,
            # NOTE: no integrations_* / corrective_* counters here.
        },
        "integration_policy": {
            "every_n_tasks": 3,
            "run_at_session_end": True,
            # NOTE: no timeout_sec — added later.
        },
        "last_integration_result": None,
        "deployment": {
            # MVP-4D-era deployment block: enabled-flag exists, no smoke
            # or rollback subkeys.
            "enabled": False,
            "target": "vercel",
            "status": "not-configured",
            "latest_deployment_id": None,
            "latest_deployment_url": None,
            "latest_failure_type": None,
        },
        "pause_reason": "needs_human_review",
        "halt_requested": False,
    }


def _seed_legacy_session_on_disk(tmp: Path) -> tuple[dict[str, Any], Path, Path]:
    """Materialize an MVP-4D-era session in a temp project_path. Returns
    (project_dict, project_path, sess_dir)."""
    project_path = tmp / "legacy-project"
    project_path.mkdir()
    payload = _mvp_4d_era_session_dict()
    sess_dir = session_dir(project_path, payload["session_id"])
    sess_dir.mkdir(parents=True)
    session_file(project_path, payload["session_id"]).write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )
    # Minimal task-graph at project root so read/write is safe.
    write_task_graph(project_path, {
        "schema_version": 1, "project_title": "Legacy",
        "overview": "", "tasks": [
            {"id": "task-001", "title": "Pre-existing task",
             "intent": "do something", "scope_paths": ["**"],
             "acceptance_criteria": [], "dependencies": [],
             "status": "needs-human-review", "risk": "low",
             "run_ids": [], "commit": None},
        ],
    })
    project = {"id": payload["project_id"], "name": "legacy-project", "path": str(project_path)}
    return project, project_path, sess_dir


# ===========================================================================
# Tests
# ===========================================================================
class FromDictBackwardCompatTests(unittest.TestCase):
    def test_from_dict_tolerates_mvp_4d_era_payload(self) -> None:
        # AutonomousSession.from_dict must not crash on missing fields.
        payload = _mvp_4d_era_session_dict()
        session = AutonomousSession.from_dict(payload)
        self.assertEqual(session.session_id, payload["session_id"])
        self.assertEqual(session.status, "paused")

    def test_from_dict_merges_default_deployment_subkeys(self) -> None:
        # The MVP-4F subkeys must default to None when absent on disk.
        payload = _mvp_4d_era_session_dict()
        session = AutonomousSession.from_dict(payload)
        for key in (
            "latest_smoke_check_id", "latest_smoke_status",
            "latest_smoke_failure_type", "latest_rollback_id",
            "latest_rollback_status", "latest_rollback_failure_type",
        ):
            self.assertIn(key, session.deployment, f"deployment.{key} not merged from defaults")
            self.assertIsNone(session.deployment[key], f"deployment.{key} should default to None")

    def test_from_dict_merges_default_integration_policy(self) -> None:
        payload = _mvp_4d_era_session_dict()
        session = AutonomousSession.from_dict(payload)
        for key in ("every_n_tasks", "run_at_session_end", "timeout_sec"):
            self.assertIn(key, session.integration_policy)

    def test_from_dict_merges_default_counters(self) -> None:
        payload = _mvp_4d_era_session_dict()
        session = AutonomousSession.from_dict(payload)
        for key in (
            "integrations_run", "integrations_passed", "integrations_failed",
            "corrective_tasks_created", "corrective_tasks_completed",
        ):
            self.assertEqual(session.counters[key], 0,
                             f"missing counter {key} should default to 0")

    def test_from_dict_merges_default_budgets(self) -> None:
        payload = _mvp_4d_era_session_dict()
        session = AutonomousSession.from_dict(payload)
        # max_corrective_tasks (MVP-4C) was not in the legacy payload's
        # budgets; default must merge in.
        self.assertIn("max_corrective_tasks", session.budgets)

    def test_to_dict_roundtrip_is_stable(self) -> None:
        payload = _mvp_4d_era_session_dict()
        session = AutonomousSession.from_dict(payload)
        # Roundtrip: to_dict → from_dict produces the same logical session.
        round = AutonomousSession.from_dict(session.to_dict())
        self.assertEqual(round.session_id, session.session_id)
        self.assertEqual(round.deployment, session.deployment)
        self.assertEqual(round.counters, session.counters)


class ControllerResumeBackwardCompatTests(unittest.TestCase):
    def test_find_active_session_loads_legacy_payload(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, project_path, _ = _seed_legacy_session_on_disk(Path(tmp))
            session = find_active_session(project_path)
            self.assertIsNotNone(session)
            self.assertEqual(session.session_id, "session_legacy")

    def test_controller_resumes_legacy_paused_session_without_crash(self) -> None:
        # A legacy paused session re-loaded by start_or_resume should
        # reactivate to "running" without crashing on missing fields.
        # (No blocking review item is seeded, so resume gating allows it.)
        with tempfile.TemporaryDirectory() as tmp:
            project, project_path, _ = _seed_legacy_session_on_disk(Path(tmp))
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: None,  # never called — graph has no eligible tasks
            )
            session = controller.start_or_resume()
            # Session id is preserved, status is back to running.
            self.assertEqual(session.session_id, "session_legacy")
            # MVP-4F deployment subkeys are now present + defaulted.
            self.assertIsNone(session.deployment["latest_smoke_check_id"])
            self.assertIsNone(session.deployment["latest_rollback_id"])

    def test_status_renderer_handles_legacy_session_dict(self) -> None:
        # _update_final_status must not raise on a session whose
        # deployment block is missing MVP-4F subkeys (defaults merged
        # via from_dict, so by the time _update_final_status sees the
        # session it's in the canonical shape).
        with tempfile.TemporaryDirectory() as tmp:
            project, project_path, sess_dir = _seed_legacy_session_on_disk(Path(tmp))
            controller = AutonomousController(
                project=project,
                run_inner_loop=lambda **kw: None,
            )
            session = controller.start_or_resume()
            graph = read_task_graph(project_path)
            controller._update_final_status(session, graph)  # noqa: SLF001
            final = (sess_dir / "final-run-status.md").read_text(encoding="utf-8")
            for section in ("## Summary", "## Tasks", "## Integration",
                            "## Deployment", "## Smoke Checks", "## Rollback",
                            "## Evidence Trail", "## Next Actions"):
                self.assertIn(section, final, f"final report missing {section}")


class StatusJsonShapeTests(unittest.TestCase):
    def test_session_to_dict_always_includes_mvp_4f_deployment_keys(self) -> None:
        # Even a legacy session, once loaded through from_dict and
        # serialized back, exposes every MVP-4F deployment field for any
        # downstream consumer (status JSON, dashboard, validators).
        payload = _mvp_4d_era_session_dict()
        session = AutonomousSession.from_dict(payload)
        out = session.to_dict()
        self.assertIn("deployment", out)
        for key in DEFAULT_DEPLOYMENT_STATE:
            self.assertIn(key, out["deployment"],
                          f"to_dict must expose deployment.{key} even for legacy sessions")


if __name__ == "__main__":
    unittest.main()
