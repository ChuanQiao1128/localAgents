"""MVP-4E unit tests: deploy config, command construction, URL parsing,
adapter happy/failure paths, controller wiring (auto + manual). All tests
inject a fake command runner so no real Vercel CLI is invoked."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from orchestrator.core.autonomous import (
    AutonomousController,
    AutonomousSession,
    SCHEMA_VERSION_SESSION,
    DEFAULT_DEPLOYMENT_STATE,
    write_task_graph,
)
from orchestrator.core.deploy import (
    DeployConfig, VercelDeployConfig, REDACTED,
    list_deployments, latest_deployment,
    load_deploy_config, project_config_path,
    redact_text, sanitize_command_args,
)
from orchestrator.core.deploy_vercel import (
    CommandResult, DeployResult,
    build_vercel_build_command, build_vercel_deploy_command,
    build_vercel_inspect_command,
    extract_deployment_url, run_vercel_deploy,
    serialize_command_results,
)
from orchestrator.core.review_queue import list_review_items
from orchestrator.core.ids import now_iso


# ---------------------------------------------------------------------------
# Fake runner
# ---------------------------------------------------------------------------
def _fake_runner(plan: list[dict[str, Any]]):
    """Build a CommandRunner that returns canned results in `plan` order.

    Each plan entry: {name?, exit_code?, stdout?, stderr?}. Missing fields
    default to (exit_code=0, stdout="", stderr="").
    """
    cursor = {"i": 0}

    def runner(args, sanitized_args, name, cwd, env, timeout):
        i = cursor["i"]
        cursor["i"] += 1
        canned = plan[i] if i < len(plan) else {}
        return CommandResult(
            args=args, sanitized_args=sanitized_args, name=name, cwd=str(cwd),
            started_at=now_iso(), completed_at=now_iso(),
            exit_code=int(canned.get("exit_code", 0)),
            stdout=str(canned.get("stdout", "")),
            stderr=str(canned.get("stderr", "")),
        )
    return runner


def _vercel_config(**overrides) -> DeployConfig:
    config = DeployConfig(enabled=True, target="vercel", environment="preview", project_path=".")
    if "vercel" in overrides:
        for k, v in overrides.pop("vercel").items():
            setattr(config.vercel, k, v)
    for k, v in overrides.items():
        setattr(config, k, v)
    return config


# ---------------------------------------------------------------------------
# Acceptance tests
# ---------------------------------------------------------------------------
class DeployConfigTests(unittest.TestCase):
    def test_deploy_config_defaults_to_disabled_vercel_preview(self) -> None:
        # Acceptance #1.
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            cfg = load_deploy_config(project_path)
            self.assertFalse(cfg.enabled)
            self.assertEqual(cfg.target, "vercel")
            self.assertEqual(cfg.environment, "preview")
            self.assertFalse(cfg.vercel.prod)
            self.assertFalse(cfg.vercel.prebuilt)
            self.assertTrue(cfg.vercel.inspect)
            self.assertEqual(cfg.vercel.inspect_timeout, "5m")
            self.assertEqual(cfg.vercel.token_env, "VERCEL_TOKEN")

    def test_deploy_config_loads_from_yaml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            project_config_path(project_path).write_text(
                "deploy:\n  enabled: true\n  environment: production\n  vercel:\n    prod: true\n    inspect: false\n",
                encoding="utf-8",
            )
            cfg = load_deploy_config(project_path)
            self.assertTrue(cfg.enabled)
            self.assertEqual(cfg.environment, "production")
            self.assertTrue(cfg.vercel.prod)
            self.assertFalse(cfg.vercel.inspect)


class CommandConstructionTests(unittest.TestCase):
    def test_vercel_deploy_command_uses_preview_by_default(self) -> None:
        # Acceptance #3.
        cfg = _vercel_config()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VERCEL_TOKEN", None)
            full, sanitized = build_vercel_deploy_command(cfg, Path("/tmp/proj"))
        self.assertIn("vercel", full)
        self.assertIn("deploy", full)
        self.assertIn("--yes", full)
        self.assertIn("--cwd", full)
        self.assertNotIn("--prod", full)  # preview is implicit
        self.assertEqual(full, sanitized)  # no token → no redaction needed

    def test_vercel_deploy_command_adds_prod_when_configured(self) -> None:
        # Acceptance #4.
        cfg = _vercel_config(vercel={"prod": True})
        full, _ = build_vercel_deploy_command(cfg, Path("/tmp/proj"))
        self.assertIn("--prod", full)

    def test_vercel_deploy_command_skip_domain_only_with_prod(self) -> None:
        cfg = _vercel_config(vercel={"prod": True, "skip_domain": True})
        full, _ = build_vercel_deploy_command(cfg, Path("/tmp/proj"))
        self.assertIn("--prod", full)
        self.assertIn("--skip-domain", full)

    def test_vercel_deploy_command_redacts_token_in_sanitized_args(self) -> None:
        # Acceptance #5.
        cfg = _vercel_config()
        with patch.dict(os.environ, {"VERCEL_TOKEN": "TOPSECRET_TOKEN_VALUE_123"}, clear=False):
            full, sanitized = build_vercel_deploy_command(cfg, Path("/tmp/proj"))
        self.assertIn("TOPSECRET_TOKEN_VALUE_123", full)
        self.assertNotIn("TOPSECRET_TOKEN_VALUE_123", " ".join(sanitized))
        self.assertIn(REDACTED, sanitized)

    def test_vercel_inspect_command_includes_wait_and_timeout(self) -> None:
        cfg = _vercel_config(vercel={"inspect_timeout": "10m"})
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VERCEL_TOKEN", None)
            full, _ = build_vercel_inspect_command("https://x.vercel.app", cfg)
        self.assertIn("inspect", full)
        self.assertIn("https://x.vercel.app", full)
        self.assertIn("--wait", full)
        self.assertIn("--timeout=10m", full)

    def test_sanitize_redacts_short_appearances_via_args(self) -> None:
        cleaned = sanitize_command_args(["a", "secret-1234567890", "b"], secret_values=["secret-1234567890"])
        self.assertEqual(cleaned, ["a", REDACTED, "b"])

    def test_redact_text_replaces_long_secrets(self) -> None:
        out = redact_text("token is secret-1234567890 ok", secret_values=["secret-1234567890"])
        self.assertNotIn("secret-1234567890", out)
        self.assertIn(REDACTED, out)


class UrlExtractionTests(unittest.TestCase):
    def test_extract_deployment_url_from_stdout(self) -> None:
        # Acceptance #7.
        stdout = "Deploying...\nProject: myapp\nhttps://myapp-abc123.vercel.app\n"
        self.assertEqual(extract_deployment_url(stdout), "https://myapp-abc123.vercel.app")

    def test_extract_deployment_url_falls_back_to_https(self) -> None:
        stdout = "deployed at https://my-custom-domain.example.com"
        self.assertEqual(extract_deployment_url(stdout), "https://my-custom-domain.example.com")

    def test_extract_deployment_url_returns_none_when_missing(self) -> None:
        self.assertIsNone(extract_deployment_url("nothing happened"))


class AdapterRunVercelDeployTests(unittest.TestCase):
    def test_happy_path_writes_url_and_runs_inspect(self) -> None:
        # Acceptance #6 + #8.
        cfg = _vercel_config()
        runner = _fake_runner([
            {"exit_code": 0, "stdout": "https://app-1.vercel.app\n"},  # vercel deploy
            {"exit_code": 0, "stdout": "ready"},                       # vercel inspect
        ])
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("VERCEL_TOKEN", None)
            result = run_vercel_deploy(cfg, Path("/tmp/proj"), command_runner=runner)
        self.assertEqual(result.status, "ready")
        self.assertEqual(result.deployment_url, "https://app-1.vercel.app")
        self.assertEqual([c.name for c in result.commands_run], ["vercel_deploy", "vercel_inspect"])
        self.assertIsNone(result.failure)

    def test_inspect_can_be_disabled(self) -> None:
        cfg = _vercel_config(vercel={"inspect": False})
        runner = _fake_runner([{"exit_code": 0, "stdout": "https://x.vercel.app"}])
        result = run_vercel_deploy(cfg, Path("/tmp/proj"), command_runner=runner)
        self.assertEqual(result.status, "ready")
        self.assertEqual([c.name for c in result.commands_run], ["vercel_deploy"])

    def test_deploy_failure_classified_as_vercel_deploy_failed(self) -> None:
        # Acceptance #9 (adapter side).
        cfg = _vercel_config()
        runner = _fake_runner([{"exit_code": 1, "stderr": "Error: build failed\n"}])
        result = run_vercel_deploy(cfg, Path("/tmp/proj"), command_runner=runner)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure["failure_type"], "vercel_deploy_failed")
        self.assertEqual(result.failure["failed_command"], "vercel_deploy")

    def test_inspect_failure_classified_as_vercel_inspect_failed(self) -> None:
        # Acceptance #10 (adapter side).
        cfg = _vercel_config()
        runner = _fake_runner([
            {"exit_code": 0, "stdout": "https://x.vercel.app"},
            {"exit_code": 1, "stderr": "Error: deployment status: ERROR"},
        ])
        result = run_vercel_deploy(cfg, Path("/tmp/proj"), command_runner=runner)
        self.assertEqual(result.status, "failed")
        self.assertEqual(result.failure["failure_type"], "vercel_inspect_failed")

    def test_deployment_url_missing_when_stdout_has_no_url(self) -> None:
        cfg = _vercel_config()
        runner = _fake_runner([{"exit_code": 0, "stdout": "no url here"}])
        result = run_vercel_deploy(cfg, Path("/tmp/proj"), command_runner=runner)
        self.assertEqual(result.status, "unknown")
        self.assertEqual(result.failure["failure_type"], "deployment_url_missing")

    def test_auth_failure_detected_in_stderr(self) -> None:
        cfg = _vercel_config()
        runner = _fake_runner([{"exit_code": 1, "stderr": "Error: No token provided"}])
        result = run_vercel_deploy(cfg, Path("/tmp/proj"), command_runner=runner)
        self.assertEqual(result.failure["failure_type"], "vercel_auth_missing")

    def test_prebuilt_runs_build_then_deploy_prebuilt(self) -> None:
        # Acceptance #17.
        cfg = _vercel_config(vercel={"build_before_deploy": True, "prebuilt": True})
        runner = _fake_runner([
            {"exit_code": 0, "stdout": "build ok"},
            {"exit_code": 0, "stdout": "https://prebuilt.vercel.app"},
            {"exit_code": 0, "stdout": "ready"},
        ])
        result = run_vercel_deploy(cfg, Path("/tmp/proj"), command_runner=runner)
        self.assertEqual([c.name for c in result.commands_run], ["vercel_build", "vercel_deploy", "vercel_inspect"])
        self.assertEqual(result.status, "ready")
        # The deploy command must include --prebuilt.
        deploy_cmd = next(c for c in result.commands_run if c.name == "vercel_deploy")
        self.assertIn("--prebuilt", deploy_cmd.sanitized_args)

    def test_token_never_appears_in_serialized_artifact_or_logs(self) -> None:
        # Acceptance #5 (artifact side): given a real-looking token in env,
        # neither the sanitized_args list nor stdout/stderr in the
        # serialized command block should contain it.
        cfg = _vercel_config()
        runner = _fake_runner([
            {"exit_code": 0, "stdout": "https://x.vercel.app\nDONE"},
            {"exit_code": 0, "stdout": "ready"},
        ])
        with patch.dict(os.environ, {"VERCEL_TOKEN": "SUPER_SECRET_TOKEN_VAL_2026"}, clear=False):
            result = run_vercel_deploy(cfg, Path("/tmp/proj"), command_runner=runner)
        serialized = serialize_command_results(result.commands_run)
        full_text = json.dumps(serialized, ensure_ascii=False)
        self.assertNotIn("SUPER_SECRET_TOKEN_VAL_2026", full_text)


class ControllerDeployIntegrationTests(unittest.TestCase):
    """Controller-level tests for auto-deploy on session_end + manual deploy
    + failure → review item flow. No real CLI subprocess; we patch
    `run_vercel_deploy` so the tests are deterministic."""

    def _make_project(self, tmp: str) -> dict[str, Any]:
        project_path = Path(tmp) / "proj"
        project_path.mkdir()
        return {"id": "project_x", "name": "x", "path": str(project_path)}

    def _seed_graph(self, project_path: Path, tasks: list[dict[str, Any]]) -> dict[str, Any]:
        graph = {"schema_version": 1, "project_title": "p", "overview": "", "tasks": tasks}
        write_task_graph(project_path, graph)
        return graph

    def _enable_deploy_config(self, project_path: Path, smoke_enabled: bool = False) -> None:
        # MVP-4E tests default to smoke disabled so the deploy success path
        # doesn't fall through into smoke (and try to hit a real URL).
        # MVP-4F tests that exercise smoke flip this to True and inject a
        # fake smoke_runner via run_deploy_now.
        smoke_block = "    enabled: false\n" if not smoke_enabled else "    enabled: true\n"
        project_config_path(project_path).write_text(
            "deploy:\n"
            "  enabled: true\n"
            "  target: vercel\n"
            "  environment: preview\n"
            "  smoke_checks:\n"
            f"{smoke_block}",
            encoding="utf-8",
        )

    def test_autonomous_completed_session_runs_deploy_when_enabled(self) -> None:
        # Acceptance #11.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            self._enable_deploy_config(Path(project["path"]))
            graph = self._seed_graph(Path(project["path"]), [])
            # Empty graph → controller goes straight to _maybe_continue_or_complete
            # → no integration (no completed tasks) → tries deploy.
            captured: list[Any] = []

            def fake_runner(config, project_root, **kwargs):
                captured.append((config, project_root))
                return DeployResult(
                    status="ready", deployment_url="https://demo.vercel.app",
                    commands_run=[CommandResult(
                        args=[], sanitized_args=["vercel", "deploy", "--yes", "--cwd", str(project_root)],
                        name="vercel_deploy", cwd=str(project_root),
                        started_at=now_iso(), completed_at=now_iso(),
                        exit_code=0, stdout="https://demo.vercel.app", stderr="",
                    )],
                    failure=None, started_at=now_iso(), completed_at=now_iso(),
                )

            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            with patch("orchestrator.core.autonomous.run_vercel_deploy", side_effect=fake_runner):
                outcome = controller.advance_one_task(session, graph)
            self.assertIsNone(outcome)
            self.assertEqual(session.status, "completed")
            self.assertEqual(session.deployment["status"], "deployed")
            self.assertEqual(session.deployment["latest_deployment_url"], "https://demo.vercel.app")
            # deployment.json was written.
            deployments = list_deployments(Path(project["path"]), session.session_id)
            self.assertEqual(len(deployments), 1)
            self.assertEqual(deployments[0]["status"], "ready")
            self.assertEqual(deployments[0]["deployment_url"], "https://demo.vercel.app")
            self.assertEqual(captured, [(captured[0][0], captured[0][1])])  # called once

    def test_autonomous_completed_session_does_not_deploy_when_disabled(self) -> None:
        # Acceptance #12.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            graph = self._seed_graph(Path(project["path"]), [])
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            with patch("orchestrator.core.autonomous.run_vercel_deploy") as mocked:
                controller.advance_one_task(session, graph)
            mocked.assert_not_called()
            self.assertEqual(session.status, "completed")
            self.assertEqual(session.deployment["status"], "not-configured")

    def test_deploy_failure_pauses_session_with_pause_reason_deployment_failed(self) -> None:
        # Acceptance #18.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            self._enable_deploy_config(Path(project["path"]))
            graph = self._seed_graph(Path(project["path"]), [])

            def fake_runner(config, project_root, **kwargs):
                return DeployResult(
                    status="failed", deployment_url=None,
                    commands_run=[CommandResult(
                        args=[], sanitized_args=["vercel", "deploy", "--yes"],
                        name="vercel_deploy", cwd=str(project_root),
                        started_at=now_iso(), completed_at=now_iso(),
                        exit_code=1, stdout="", stderr="Error: build failed",
                    )],
                    failure={"failure_type": "vercel_deploy_failed", "message": "build failed", "failed_command": "vercel_deploy"},
                    started_at=now_iso(), completed_at=now_iso(),
                )

            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            with patch("orchestrator.core.autonomous.run_vercel_deploy", side_effect=fake_runner):
                controller.advance_one_task(session, graph)
            self.assertEqual(session.status, "paused")
            self.assertEqual(session.pause_reason, "deployment-failed")
            self.assertEqual(session.deployment["status"], "failed")
            self.assertEqual(session.deployment["latest_failure_type"], "vercel_deploy_failed")
            # A deployment review item was created.
            reviews = list_review_items(Path(project["path"]), session.session_id, only_open=True)
            self.assertEqual(len(reviews), 1)
            self.assertEqual(reviews[0].source_type, "deployment_failure")
            self.assertEqual(reviews[0].reason_code, "deployment-failed")
            self.assertEqual(reviews[0].allowed_actions, ["show", "reject", "resolve"])

    def test_manual_deploy_does_not_pause_completed_session_on_failure(self) -> None:
        # source="manual" must not pause an already-completed session;
        # only auto-deploy at session_end pauses. Manual user invoked
        # deploy intentionally, so they decide what to do.
        with tempfile.TemporaryDirectory() as tmp:
            project = self._make_project(tmp)
            self._enable_deploy_config(Path(project["path"]))
            controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
            session = controller.start_or_resume()
            session.status = "completed"
            session.deployment["enabled"] = True

            def fake_runner(config, project_root, **kwargs):
                return DeployResult(
                    status="failed", deployment_url=None,
                    commands_run=[CommandResult(
                        args=[], sanitized_args=["vercel", "deploy"],
                        name="vercel_deploy", cwd=str(project_root),
                        started_at=now_iso(), completed_at=now_iso(),
                        exit_code=1, stdout="", stderr="boom",
                    )],
                    failure={"failure_type": "vercel_deploy_failed", "message": "boom", "failed_command": "vercel_deploy"},
                    started_at=now_iso(), completed_at=now_iso(),
                )

            with patch("orchestrator.core.autonomous.run_vercel_deploy", side_effect=fake_runner):
                outcome = controller.run_deploy_now(session, source="manual")
            self.assertEqual(outcome["status"], "failed")
            self.assertFalse(outcome["session_paused"])
            self.assertEqual(session.status, "completed")  # NOT paused by manual deploy
            # But review item was still created so the failure is auditable.
            reviews = list_review_items(Path(project["path"]), session.session_id, only_open=True)
            self.assertEqual(len(reviews), 1)


if __name__ == "__main__":
    unittest.main()
