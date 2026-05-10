"""RC-1: unit tests for orchestrator/core/artifact_validation.py."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.core.artifact_validation import (
    REQUIRED_FINAL_REPORT_SECTIONS,
    has_validation_errors,
    validate_applied_candidate,
    validate_autonomous_session,
    validate_deployment,
    validate_final_run_status_md,
    validate_integration_failure,
    validate_promotion_report,
    validate_review_item,
    validate_rollback,
    validate_session_directory,
    validate_smoke_check,
    validate_task_graph,
)


# ---------------------------------------------------------------------------
# autonomous-session.json
# ---------------------------------------------------------------------------
class AutonomousSessionTests(unittest.TestCase):
    def _valid(self) -> dict:
        return {
            "schema_version": 1,
            "session_id": "session_x",
            "project_id": "project_y",
            "status": "running",
            "branch": "agentic/autonomous/session_x",
            "counters": {
                "completed_tasks": 0, "abandoned_tasks": 0,
                "needs_review_tasks": 0, "inner_runs": 0,
            },
            "deployment": {},
        }

    def test_valid_session_passes(self) -> None:
        self.assertEqual(validate_autonomous_session(self._valid()), [])

    def test_missing_session_id_reports(self) -> None:
        payload = self._valid()
        del payload["session_id"]
        errors = validate_autonomous_session(payload)
        self.assertTrue(any("session_id" in e for e in errors))

    def test_invalid_status_reports(self) -> None:
        payload = self._valid()
        payload["status"] = "exploded"
        errors = validate_autonomous_session(payload)
        self.assertTrue(any("status" in e and "exploded" in e for e in errors))

    def test_counters_must_have_required_subkeys(self) -> None:
        payload = self._valid()
        payload["counters"] = {"completed_tasks": 0}
        errors = validate_autonomous_session(payload)
        self.assertTrue(any("inner_runs" in e for e in errors))


# ---------------------------------------------------------------------------
# task-graph.json
# ---------------------------------------------------------------------------
class TaskGraphTests(unittest.TestCase):
    def _valid(self) -> dict:
        return {
            "schema_version": 1,
            "project_title": "P",
            "tasks": [
                {
                    "id": "task-001", "title": "A", "intent": "do",
                    "scope_paths": ["**"], "acceptance_criteria": ["x"],
                    "dependencies": [], "status": "pending",
                }
            ],
        }

    def test_valid_graph_passes(self) -> None:
        self.assertEqual(validate_task_graph(self._valid()), [])

    def test_invalid_status_reports(self) -> None:
        payload = self._valid()
        payload["tasks"][0]["status"] = "huh"
        errors = validate_task_graph(payload)
        self.assertTrue(any("status" in e and "huh" in e for e in errors))

    def test_missing_intent_reports(self) -> None:
        payload = self._valid()
        del payload["tasks"][0]["intent"]
        errors = validate_task_graph(payload)
        self.assertTrue(any("intent" in e for e in errors))


# ---------------------------------------------------------------------------
# deployment.json
# ---------------------------------------------------------------------------
class DeploymentTests(unittest.TestCase):
    def _valid(self) -> dict:
        return {
            "schema_version": 1, "deployment_id": "d1",
            "session_id": "s1", "project_id": "p1",
            "target": "vercel", "status": "ready",
        }

    def test_valid_deployment_passes(self) -> None:
        self.assertEqual(validate_deployment(self._valid()), [])

    def test_invalid_status_reports(self) -> None:
        payload = self._valid()
        payload["status"] = "weird"
        errors = validate_deployment(payload)
        self.assertTrue(any("status" in e and "weird" in e for e in errors))

    def test_failure_block_must_have_failure_type(self) -> None:
        payload = self._valid()
        payload["status"] = "failed"
        payload["failure"] = {"message": "boom"}
        errors = validate_deployment(payload)
        self.assertTrue(any("failure_type" in e for e in errors))

    def test_unredacted_token_in_args_is_flagged(self) -> None:
        payload = self._valid()
        payload["commands"] = [
            {"name": "vercel_deploy", "args": ["vercel", "deploy",
             "--token", "deadbeef0123456789deadbeef0123456789deadbeef0123"]}
        ]
        errors = validate_deployment(payload)
        self.assertTrue(any("unredacted secret" in e for e in errors))


# ---------------------------------------------------------------------------
# smoke-check.json
# ---------------------------------------------------------------------------
class SmokeCheckTests(unittest.TestCase):
    def _valid(self) -> dict:
        return {
            "schema_version": 1, "smoke_check_id": "sm1",
            "session_id": "s1", "project_id": "p1", "status": "passed",
            "checks": [
                {"name": "home", "expected_status": [200],
                 "headers_redacted": {"Authorization": "<redacted>"}}
            ],
        }

    def test_valid_smoke_passes(self) -> None:
        self.assertEqual(validate_smoke_check(self._valid()), [])

    def test_invalid_status_reports(self) -> None:
        payload = self._valid()
        payload["status"] = "spurious"
        errors = validate_smoke_check(payload)
        self.assertTrue(any("status" in e and "spurious" in e for e in errors))

    def test_unredacted_header_is_flagged(self) -> None:
        payload = self._valid()
        payload["checks"][0]["headers_redacted"]["Authorization"] = "Bearer real-secret"
        errors = validate_smoke_check(payload)
        self.assertTrue(any("Authorization" in e and "<redacted>" in e for e in errors))


# ---------------------------------------------------------------------------
# rollback.json
# ---------------------------------------------------------------------------
class RollbackTests(unittest.TestCase):
    def test_valid_rollback_passes(self) -> None:
        payload = {
            "schema_version": 1, "rollback_id": "rb1",
            "session_id": "s1", "project_id": "p1", "status": "completed",
        }
        self.assertEqual(validate_rollback(payload), [])

    def test_invalid_status_reports(self) -> None:
        payload = {
            "schema_version": 1, "rollback_id": "rb1",
            "session_id": "s1", "project_id": "p1", "status": "weird",
        }
        errors = validate_rollback(payload)
        self.assertTrue(any("status" in e and "weird" in e for e in errors))


# ---------------------------------------------------------------------------
# review-items/<id>.json (RC-2B.9)
# ---------------------------------------------------------------------------
class ReviewItemTests(unittest.TestCase):
    def _valid(self) -> dict:
        return {
            "schema_version": 1,
            "review_id": "rv_1",
            "session_id": "session_x",
            "project_id": "project_x",
            "status": "open",
            "severity": "blocking",
            "source_type": "task_run",
            "reason_code": "needs-human-review",
            "title": "Task task-001 needs human review",
            "summary": "Inner loop returned needs-human-review.",
            "evidence_paths": [".agent/runs/run_x/promotion-report.json"],
            "suggested_commands": ["agent-studio agentic-runs show --run run_x"],
            "allowed_actions": ["show", "approve", "reject", "resolve"],
        }

    def test_valid_review_item_passes(self) -> None:
        self.assertEqual(validate_review_item(self._valid()), [])

    def test_invalid_status_reports(self) -> None:
        payload = self._valid()
        payload["status"] = "weird"
        errors = validate_review_item(payload)
        self.assertTrue(any("status" in e and "weird" in e for e in errors))

    def test_invalid_severity_reports(self) -> None:
        payload = self._valid()
        payload["severity"] = "loud"
        errors = validate_review_item(payload)
        self.assertTrue(any("severity" in e and "loud" in e for e in errors))

    def test_missing_summary_reports(self) -> None:
        payload = self._valid()
        del payload["summary"]
        errors = validate_review_item(payload)
        self.assertTrue(any("summary" in e for e in errors))

    def test_resolution_must_be_dict_or_null(self) -> None:
        payload = self._valid()
        payload["resolution"] = "not a dict"
        errors = validate_review_item(payload)
        self.assertTrue(any("resolution" in e for e in errors))


# ---------------------------------------------------------------------------
# integration-failures/<id>/integration-failure.json (RC-2B.10)
# ---------------------------------------------------------------------------
class IntegrationFailureTests(unittest.TestCase):
    def _valid(self) -> dict:
        return {
            "schema_version": 1,
            "failure_id": "fail_x",
            "session_id": "session_x",
            "project_id": "project_x",
            "trigger": "session_end",
            "after_task_id": "task-001",
            "failed_command": {"name": "build", "cmd": "npm run build"},
            "detected_failure_type": "build_failure",
            "created_at": "2026-05-10T05:00:00+00:00",
        }

    def test_valid_failure_passes(self) -> None:
        self.assertEqual(validate_integration_failure(self._valid()), [])

    def test_missing_failed_command_reports(self) -> None:
        payload = self._valid()
        del payload["failed_command"]
        errors = validate_integration_failure(payload)
        self.assertTrue(any("failed_command" in e for e in errors))

    def test_failed_command_must_have_name(self) -> None:
        payload = self._valid()
        payload["failed_command"] = {"cmd": "x"}
        errors = validate_integration_failure(payload)
        self.assertTrue(any("failed_command.name" in e for e in errors))

    def test_failed_command_must_be_dict(self) -> None:
        payload = self._valid()
        payload["failed_command"] = "npm run build"
        errors = validate_integration_failure(payload)
        self.assertTrue(any("expected dict" in e for e in errors))


# ---------------------------------------------------------------------------
# applied-candidate.json
# ---------------------------------------------------------------------------
class AppliedCandidateTests(unittest.TestCase):
    def _valid(self) -> dict:
        return {
            "schema_version": 1,
            "run_id": "run_x",
            "candidate": "candidate-b",
            "strategy": "test-focused",
            "decision_at_apply_time": "promote",
            "human_override": False,
            "project_id": None,
            "base_commit": "abc1234",
            "applied_to_commit": "def5678",
            "patch_sha256": "a" * 64,
            "dry_run": False,
            "applied": True,
            "changed_files": ["apps/web/index.html"],
            "timestamp_utc": "2026-05-10T04:50:00+00:00",
        }

    def test_valid_record_passes(self) -> None:
        self.assertEqual(validate_applied_candidate(self._valid()), [])

    def test_missing_run_id_fails(self) -> None:
        payload = self._valid()
        del payload["run_id"]
        errors = validate_applied_candidate(payload)
        self.assertTrue(any("run_id" in e for e in errors))

    def test_missing_patch_sha_fails(self) -> None:
        payload = self._valid()
        del payload["patch_sha256"]
        errors = validate_applied_candidate(payload)
        self.assertTrue(any("patch_sha256" in e for e in errors))

    def test_wrong_human_override_type_fails(self) -> None:
        payload = self._valid()
        payload["human_override"] = "yes"
        errors = validate_applied_candidate(payload)
        self.assertTrue(any("human_override" in e and "bool" in e for e in errors))

    def test_changed_files_must_be_str_list(self) -> None:
        payload = self._valid()
        payload["changed_files"] = ["good.html", 42]
        errors = validate_applied_candidate(payload)
        self.assertTrue(any("changed_files[1]" in e for e in errors))

    def test_token_leak_in_strategy_label_is_flagged(self) -> None:
        payload = self._valid()
        # JWT-shaped string smuggled into the strategy field.
        payload["strategy"] = "broader-fix-deadbeef0123456789deadbeef0123456789deadbeef0123"
        errors = validate_applied_candidate(payload)
        self.assertTrue(any("unredacted secret" in e for e in errors))

    def test_legacy_record_without_optional_fields_passes(self) -> None:
        # A historical artifact may not have `strategy` / `timestamp_utc`.
        # Required core fields are still present; validator must tolerate.
        payload = self._valid()
        for opt in ("strategy", "decision_at_apply_time", "timestamp_utc",
                    "project_id", "human_override", "changed_files"):
            payload.pop(opt, None)
        self.assertEqual(validate_applied_candidate(payload), [])


# ---------------------------------------------------------------------------
# final-run-status.md
# ---------------------------------------------------------------------------
class FinalRunStatusMdTests(unittest.TestCase):
    def test_complete_report_passes(self) -> None:
        text = "\n\n".join([f"{s}\n- body" for s in REQUIRED_FINAL_REPORT_SECTIONS])
        self.assertEqual(validate_final_run_status_md(text), [])

    def test_missing_section_reports(self) -> None:
        # Drop "## Rollback" from the report.
        sections = [s for s in REQUIRED_FINAL_REPORT_SECTIONS if s != "## Rollback"]
        text = "\n\n".join([f"{s}\n- body" for s in sections])
        errors = validate_final_run_status_md(text)
        self.assertTrue(any("Rollback" in e for e in errors))


# ---------------------------------------------------------------------------
# Cross-artifact: validate_session_directory
# ---------------------------------------------------------------------------
class SessionDirTests(unittest.TestCase):
    def _build(self, tmp: Path, *, with_deployment: bool = False, with_smoke: bool = False) -> Path:
        project_path = tmp / "project"
        sess_dir = project_path / ".agent" / "autonomous" / "sessions" / "session_x"
        (sess_dir / "deployments" / "d1").mkdir(parents=True)
        (sess_dir / "smoke-checks" / "sm1").mkdir(parents=True)
        (sess_dir / "rollbacks" / "rb1").mkdir(parents=True)
        (project_path / "task-graph.json").write_text(json.dumps({
            "schema_version": 1, "project_title": "P", "tasks": [],
        }), encoding="utf-8")
        (sess_dir / "autonomous-session.json").write_text(json.dumps({
            "schema_version": 1, "session_id": "session_x",
            "project_id": "p1", "status": "completed",
            "branch": "agentic/autonomous/session_x",
            "counters": {
                "completed_tasks": 0, "abandoned_tasks": 0,
                "needs_review_tasks": 0, "inner_runs": 0,
            },
            "deployment": {},
        }), encoding="utf-8")
        text = "\n\n".join([f"{s}\n- body" for s in REQUIRED_FINAL_REPORT_SECTIONS])
        (sess_dir / "final-run-status.md").write_text(text, encoding="utf-8")
        if with_deployment:
            (sess_dir / "deployments" / "d1" / "deployment.json").write_text(json.dumps({
                "schema_version": 1, "deployment_id": "d1",
                "session_id": "session_x", "project_id": "p1",
                "target": "vercel", "status": "ready",
            }), encoding="utf-8")
        if with_smoke:
            (sess_dir / "smoke-checks" / "sm1" / "smoke-check.json").write_text(json.dumps({
                "schema_version": 1, "smoke_check_id": "sm1",
                "session_id": "session_x", "project_id": "p1",
                "status": "passed", "checks": [],
            }), encoding="utf-8")
        return sess_dir

    def test_clean_session_dir_reports_no_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sess_dir = self._build(Path(tmp), with_deployment=True, with_smoke=True)
            report = validate_session_directory(sess_dir)
            self.assertFalse(has_validation_errors(report), f"unexpected errors: {report}")

    def test_invalid_session_status_surfaces(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            sess_dir = self._build(Path(tmp))
            sess_path = sess_dir / "autonomous-session.json"
            payload = json.loads(sess_path.read_text(encoding="utf-8"))
            payload["status"] = "weird"
            sess_path.write_text(json.dumps(payload), encoding="utf-8")
            report = validate_session_directory(sess_dir)
            self.assertTrue(has_validation_errors(report))
            self.assertTrue(any("weird" in e for e in report["autonomous-session.json"]))


# ---------------------------------------------------------------------------
# RC-1.1.6: producer-side validation hook (opt-in via env var)
# ---------------------------------------------------------------------------
class ProducerValidationHookTests(unittest.TestCase):
    """When `AGENT_STUDIO_VALIDATE_WRITES=1` is set, every artifact writer
    runs the matching consumer validator and raises on errors. Default
    off — production write paths are unchanged."""

    def _project_path(self, tmp: Path) -> Path:
        from orchestrator.core.deploy import DeployConfig
        project_path = tmp / "p"
        project_path.mkdir()
        return project_path

    def test_writer_is_silent_when_env_var_off(self) -> None:
        # Patch out the env var and write an OBVIOUSLY invalid payload —
        # the writer should NOT raise.
        from orchestrator.core.deploy import (
            DeployConfig, write_deployment_artifact, _producer_validate,
        )
        with tempfile.TemporaryDirectory() as tmp:
            project_path = self._project_path(Path(tmp))
            config = DeployConfig(enabled=True, target="vercel")
            # Status='ready' is valid → writer accepts. We just need to
            # prove no exception is raised when env var is unset.
            os.environ.pop("AGENT_STUDIO_VALIDATE_WRITES", None)
            path = write_deployment_artifact(
                project_path,
                session_id="s1", project_id="p1", config=config,
                deployment_id="d1", status="ready", deployment_url="https://x",
                started_at="now", completed_at="now",
                git_branch="main", git_commit="abc1234",
                sanitized_commands=[], failure=None,
                source_session_status="completed",
                final_run_status_relpath="x", task_graph_relpath="task-graph.json",
            )
            self.assertTrue(path.exists())

    def test_review_writer_raises_when_env_var_on_and_payload_invalid(self) -> None:
        # RC-2B.13: same hook pattern, applied to write_review_item via
        # review_queue.create_review_item. Force the validator to return
        # a non-empty error list and prove the writer raises.
        import orchestrator.core.artifact_validation as av_mod
        from orchestrator.core.deploy import ProducerValidationFailed
        from orchestrator.core.review_queue import (
            ReviewItem, SCHEMA_VERSION_REVIEW_ITEM, create_review_item,
        )
        original = av_mod.validate_review_item
        try:
            av_mod.validate_review_item = lambda payload: ["forced review error for test"]
            with tempfile.TemporaryDirectory() as tmp:
                project_path = Path(tmp)
                (project_path / ".agent/autonomous/sessions/sx").mkdir(parents=True)
                item = ReviewItem(
                    schema_version=SCHEMA_VERSION_REVIEW_ITEM,
                    review_id="rv_z", session_id="sx", project_id="px",
                    status="open", severity="blocking",
                    source_type="task_run", reason_code="needs-human-review",
                    title="t", summary="s",
                )
                os.environ["AGENT_STUDIO_VALIDATE_WRITES"] = "1"
                try:
                    with self.assertRaises(ProducerValidationFailed) as ctx:
                        create_review_item(project_path, item)
                    self.assertIn("forced review error", str(ctx.exception))
                finally:
                    os.environ.pop("AGENT_STUDIO_VALIDATE_WRITES", None)
        finally:
            av_mod.validate_review_item = original

    def test_writer_raises_when_env_var_on_and_payload_is_invalid(self) -> None:
        # We force the situation by monkey-patching the validator to
        # always return a non-empty error list. Cleaner than crafting an
        # already-rejected payload (the writer also has its own status
        # check that runs first).
        import orchestrator.core.artifact_validation as av_mod
        from orchestrator.core.deploy import (
            DeployConfig, write_deployment_artifact, ProducerValidationFailed,
        )
        original = av_mod.validate_deployment
        try:
            av_mod.validate_deployment = lambda payload: ["forced error for test"]
            with tempfile.TemporaryDirectory() as tmp:
                project_path = self._project_path(Path(tmp))
                config = DeployConfig(enabled=True, target="vercel")
                os.environ["AGENT_STUDIO_VALIDATE_WRITES"] = "1"
                try:
                    with self.assertRaises(ProducerValidationFailed) as ctx:
                        write_deployment_artifact(
                            project_path,
                            session_id="s1", project_id="p1", config=config,
                            deployment_id="d1", status="ready",
                            deployment_url="https://x",
                            started_at="now", completed_at="now",
                            git_branch="main", git_commit="abc1234",
                            sanitized_commands=[], failure=None,
                            source_session_status="completed",
                            final_run_status_relpath="x", task_graph_relpath="task-graph.json",
                        )
                    self.assertIn("forced error for test", str(ctx.exception))
                finally:
                    os.environ.pop("AGENT_STUDIO_VALIDATE_WRITES", None)
        finally:
            av_mod.validate_deployment = original


import os  # noqa: E402  (kept here to scope the import to the new tests)


if __name__ == "__main__":
    unittest.main()
