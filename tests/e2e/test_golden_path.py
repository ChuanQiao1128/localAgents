"""RC-1 End-to-End Release Candidate Hardening — Golden Path tests.

These tests prove the full requirements.md → autonomous run → deploy →
smoke check → final report ladder is stable, deterministic, and produces
real-shape artifacts. No real Codex / Vercel / HTTP is touched.

Strategy:
- Use the deterministic CLI for `init` + `new --from <requirements.md>`
  (no LLM needed — the parser is pure Python).
- Drive the AutonomousController in-process with three injected fakes:
  * fake_inner_loop — writes a real run package (intent-contract,
    context-pack, eval-harness, task-slices, candidates/<id>/{patch.diff,
    changed-files.json, score.json}, promotion-report.json v2) and
    returns AgenticRunResult(decision="promote"). Each task's patch
    creates a NEW file, so it always applies cleanly against the latest
    HEAD even after prior tasks committed.
  * fake_deploy_runner — returns a synthetic CommandResult that hits the
    "deployment_url=https://golden.vercel.app" branch.
  * fake_smoke_http — a fake HttpClient that returns 200 + matching body
    for every smoke check.
- After the controller completes, run the real CLI subprocess for
  `status` and `logs --tail` to verify CLI surfaces work.
- Read final-run-status.md and assert every required section is present.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from orchestrator.config import resolve_paths
from orchestrator.core.autonomous import (
    AutonomousController,
    DEFAULT_DEPLOYMENT_STATE,
    find_active_session,
    read_task_graph,
)
from orchestrator.core.deploy import (
    DeployConfig, latest_deployment, latest_smoke_check,
    project_config_path,
)
from orchestrator.core.deploy_vercel import CommandResult
from orchestrator.core.ids import now_iso
from orchestrator.core.review_queue import list_review_items
from orchestrator.core.run_manager import create_engine
from orchestrator.core.run_package import apply_selected_candidate
from orchestrator.core.smoke import HttpClientResult


_FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "autonomous_golden_project"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------
def _copy_fixture_into(project_path: Path) -> None:
    """Copy the golden fixture's files (excluding requirements.md) into a
    project_path that already has a git repo. requirements.md is fed via
    the CLI separately. .gitkeep files are preserved so apps/web and tests
    parent dirs exist for the parser's repo-layout scan."""
    for src in _FIXTURE_ROOT.rglob("*"):
        if src.name in {".gitkeep"} or src.is_file():
            if src.name == "requirements.md":
                continue
            rel = src.relative_to(_FIXTURE_ROOT)
            dst = project_path / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_file():
                shutil.copyfile(src, dst)


def _cli(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LOCALAGENTS_FORCE_STUB": "1", "LOCALAGENTS_QUIET": "1"}
    return subprocess.run(
        [sys.executable, "-m", "orchestrator.cli", "--root", str(root), *args],
        cwd=Path(__file__).resolve().parents[2],
        text=True, capture_output=True, check=check, env=env,
    )


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=True,
    )


def _git_short_head(project_path: Path) -> str:
    return _git("rev-parse", "--short", "HEAD", cwd=project_path).stdout.strip()


# ---------------------------------------------------------------------------
# Fake inner loop — writes a real run package the Apply Gate accepts
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class _FakeRunResult:
    """Mirror of AgenticRunResult — we duck-type so we don't import the
    real dataclass into the e2e test layer."""
    run_id: str
    status: str
    decision: str
    candidate: str
    run_dir: Path


def _slugify(title: str) -> str:
    out = []
    for ch in title.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "task"


def _build_new_file_diff(rel_path: str, body_lines: list[str]) -> str:
    """Build a unified diff that creates a brand-new file. `git apply`
    accepts this minimal form when the file does not yet exist."""
    body = "".join(f"+{line}\n" for line in body_lines)
    return (
        f"diff --git a/{rel_path} b/{rel_path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{rel_path}\n"
        f"@@ -0,0 +1,{len(body_lines)} @@\n"
        f"{body}"
    )


def _materialize_run_package(
    project_path: Path,
    *,
    run_id: str,
    task: dict[str, Any],
    candidate_id: str = "candidate-b",
    strategy_label: str = "test-focused",
) -> Path:
    """Write the full per-run artifact ladder under .agent/runs/<run_id>/.

    Designed so apply_selected_candidate's 11 hard rules all pass:
    - promotion-report.json schema_version=v2, decision=promote, selected
    - candidates/<id>/patch.diff that applies cleanly (creates new file)
    - candidates/<id>/changed-files.json with base_commit = HEAD short
    - candidates/<id>/score.json with source_patch_present=true
    """
    run_dir = project_path / ".agent" / "runs" / run_id
    cand_dir = run_dir / "candidates" / candidate_id
    cand_dir.mkdir(parents=True, exist_ok=True)

    # Each task creates one new HTML file under apps/web/<slug>.html so
    # diffs never overlap and always apply against current HEAD.
    rel_path = f"apps/web/{_slugify(task['title'])}.html"
    body_lines = [
        "<!doctype html>",
        f"<title>{task['title']}</title>",
        f"<h1>{task['title']}</h1>",
        f"<p>Implements task {task['id']} for the Tiny Creator Tracker.</p>",
        "<p>Hero text mentions Creator project tracker.</p>",
    ]
    patch = _build_new_file_diff(rel_path, body_lines)
    (cand_dir / "patch.diff").write_text(patch, encoding="utf-8")

    base_commit = _git_short_head(project_path)
    changed_files = {
        "schema_version": "agentic.changed_files.v1",
        "candidate": candidate_id,
        "base_commit": base_commit,
        "changed_files": [{"path": rel_path, "status": "added"}],
        "source_patch_present": True,
        "out_of_scope_changes": [],
    }
    (cand_dir / "changed-files.json").write_text(
        json.dumps(changed_files, indent=2), encoding="utf-8"
    )

    score = {
        "schema_version": "agentic.candidate_score.v1",
        "candidate": candidate_id,
        "source_patch_present": True,
        "diff_within_scope": True,
        "score": 0.95,
        "components": {"hard_gates": True},
        "penalties": {},
    }
    (cand_dir / "score.json").write_text(json.dumps(score, indent=2), encoding="utf-8")

    promotion = {
        "schema_version": "agentic.promotion_report.v2",
        "run_id": run_id,
        "decision": "promote",
        "candidate": candidate_id,
        "selected_candidate": candidate_id,
        "candidate_count": 1,
        "hard_gates": {"all_pass": True},
        "gate_details": [
            {"id": "source_patch_present", "passed": True},
            {"id": "diff_within_scope", "passed": True},
        ],
        "eval": {"required_pass": True, "failed_required": []},
        "repair": {"loops": 0, "exhausted": False},
        "soft_scores": {"score": 0.95},
        "remaining_risks": [],
        "abandonment_pattern": {"prior_runs": 0, "abandoned_count": 0},
        "candidates": [
            {
                "id": candidate_id,
                "strategy": strategy_label,
                "score": 0.95,
                "decision": "promote",
                "hard_gates_passed": True,
            }
        ],
        "candidate_diversity": {"method": "jaccard", "average": 0.0, "pairs": []},
    }
    (run_dir / "promotion-report.json").write_text(
        json.dumps(promotion, indent=2), encoding="utf-8"
    )

    # Lightweight envelope artifacts — not required by Apply Gate, but
    # make the run package look like the real thing for inspection.
    (run_dir / "intent-contract.json").write_text(json.dumps({
        "schema_version": "agentic.intent_contract.v1",
        "goal": task["intent"],
        "success_criteria": list(task.get("acceptance_criteria") or []),
        "allowed_change_scope": {
            "paths": list(task.get("scope_paths") or []),
            "max_files": 24,
        },
    }, indent=2), encoding="utf-8")
    (run_dir / "task-slices.json").write_text(json.dumps({
        "schema_version": "agentic.task_slices.v1",
        "slices": [{"id": "s1", "title": task["title"]}],
        "candidate_strategies": [
            {"id": candidate_id, "label": strategy_label, "prompt_hint": "fake"},
        ],
    }, indent=2), encoding="utf-8")
    (run_dir / "trace.jsonl").write_text(
        json.dumps({"ts": now_iso(), "stage": "fake", "message": "synthetic"}) + "\n",
        encoding="utf-8",
    )
    return run_dir


def make_fake_inner_loop(project_path: Path) -> Callable[..., _FakeRunResult]:
    """Return a callable matching `run_inner_loop(**kwargs)` that writes a
    real run package per task and returns decision=promote."""
    counter = {"i": 0}

    def runner(**kwargs: Any) -> _FakeRunResult:
        intent = kwargs.get("intent_overrides") or {}
        # Reconstruct a minimal task dict — the controller already passes
        # everything we need via intent_overrides.
        title = (intent.get("goal") or "Task").splitlines()[0][:80]
        counter["i"] += 1
        run_id = f"run_golden_{counter['i']:03d}"
        synthetic_task = {
            "id": f"task-{counter['i']:03d}",
            "title": title,
            "intent": intent.get("goal") or "",
            "acceptance_criteria": list(intent.get("success_criteria") or []),
            "scope_paths": list(((intent.get("allowed_change_scope") or {}).get("paths")) or []),
        }
        run_dir = _materialize_run_package(
            project_path, run_id=run_id, task=synthetic_task,
        )
        return _FakeRunResult(
            run_id=run_id, status="ready", decision="promote",
            candidate="candidate-b", run_dir=run_dir,
        )
    return runner


# ---------------------------------------------------------------------------
# Fake deploy runner + fake smoke http client
# ---------------------------------------------------------------------------
def make_fake_deploy_runner(*, deployment_url: str = "https://golden.vercel.app"):
    """Returns a `run_vercel_deploy(config, project_root)` substitute that
    skips real Vercel and yields a synthetic DeployResult."""
    from orchestrator.core.deploy_vercel import DeployResult

    def runner(config, project_root, *, command_runner=None, timeout_sec: int = 600) -> DeployResult:
        cmd = CommandResult(
            args=["vercel", "deploy", "--yes"],
            sanitized_args=["vercel", "deploy", "--yes"],
            name="vercel_deploy", cwd=str(project_root),
            started_at=now_iso(), completed_at=now_iso(),
            exit_code=0,
            stdout=f"Deploying...\nProduction: {deployment_url}\n{deployment_url}\n",
            stderr="",
        )
        return DeployResult(
            status="ready",
            deployment_url=deployment_url,
            commands_run=[cmd],
            failure=None,
            started_at=now_iso(),
            completed_at=now_iso(),
        )
    return runner


def make_fake_smoke_http(*, status: int = 200, body: str = "Creator project tracker") -> Callable[..., HttpClientResult]:
    def runner(url, method, timeout, headers):
        return HttpClientResult(status=status, body=body, duration_ms=12, error=None)
    return runner


# ---------------------------------------------------------------------------
# Project setup
# ---------------------------------------------------------------------------
def _setup_golden_project(root: Path) -> tuple[str, Path]:
    """Init workspace, ingest the golden requirements via real CLI, copy
    fixture files, init git + commit, return (project_id, project_path)."""
    _cli(root, "init")
    req_src = _FIXTURE_ROOT / "requirements.md"
    req_root = root / "requirements.md"
    shutil.copyfile(req_src, req_root)
    new = _cli(root, "new", "--from", str(req_root))
    project_id = next(t for t in new.stdout.split() if t.startswith("project_"))
    engine = create_engine(resolve_paths(root))
    project_path = Path(engine.require_project(project_id)["path"])

    # Drop fixture files (apps/web placeholder + package.json + tests/) into
    # the project so the repo-layout scan picked apps/web/** as scope.
    _copy_fixture_into(project_path)

    # Init git + commit the ingested artifacts AND the fixture files.
    _git("init", "-q", "-b", "main", cwd=project_path)
    _git("config", "user.email", "rc1@golden", cwd=project_path)
    _git("config", "user.name", "rc1", cwd=project_path)
    # Stage everything that exists; .agent/ is git-ignored already.
    _git("add", "-A", cwd=project_path)
    _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "init", cwd=project_path)
    return project_id, project_path


def _enable_full_deploy_config(project_path: Path) -> None:
    """Write the agent-studio.yaml deploy block with smoke + rollback."""
    project_config_path(project_path).write_text(textwrap.dedent("""\
        deploy:
          enabled: true
          target: vercel
          environment: preview
          project_path: "."
          vercel:
            inspect: false
          smoke_checks:
            enabled: true
            timeout_sec: 5
            retries: 0
            checks:
              - name: home
                method: GET
                path: /
                expect_status: [200]
                expect_body_contains: Creator project tracker
          rollback:
            enabled: false
        """), encoding="utf-8")


def _drive_session_to_completion(project_path: Path, *, smoke_status: int = 200, smoke_body: str = "Creator project tracker") -> Any:
    """Run the controller in-process with all three fakes injected.
    Returns the final session object."""
    from orchestrator.core.deploy import load_deploy_config
    project_id_from_path = project_path.name  # not the studio project id; only for project dict
    project = {"id": "project_golden", "name": "tiny-creator-tracker", "path": str(project_path)}
    controller = AutonomousController(
        project=project,
        run_inner_loop=make_fake_inner_loop(project_path),
        apply_candidate=apply_selected_candidate,
    )
    session = controller.start_or_resume()
    graph = read_task_graph(project_path)

    # Drive each task through advance_one_task. The controller short-circuits
    # when no more tasks are eligible, then runs final integration + deploy.
    safety = 25
    while safety > 0:
        outcome = controller.advance_one_task(session, graph)
        if outcome is None:
            break
        if session.status in {"paused", "completed"}:
            break
        graph = read_task_graph(project_path)
        safety -= 1

    # If session reached "no eligible tasks + integration passed" but the
    # auto-deploy hook needs explicit fake injection, run it manually.
    if session.deployment.get("status") in {"not-configured", "pending", None}:
        config = load_deploy_config(project_path)
        if config.enabled:
            graph = read_task_graph(project_path)
            controller.run_deploy_now(
                session, source="session_end", task_graph=graph, config=config,
                deploy_runner=make_fake_deploy_runner(),
                smoke_runner=None,  # let it use default — but inject http_client below
            )

    return session


# ===========================================================================
# Tests
# ===========================================================================
class GoldenHappyPathTests(unittest.TestCase):
    """End-to-end happy path: PRD → 2 tasks → 2 commits → deploy ready →
    smoke pass → final-run-status.md complete with all sections."""

    def test_full_happy_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_golden_project(root)
            _enable_full_deploy_config(project_path)
            _git("add", "agent-studio.yaml", cwd=project_path)
            _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "deploy config", cwd=project_path)

            # 1. Verify ingestion produced the expected artifacts.
            for name in ("prd.md", "task-graph.json", "acceptance-criteria.json", "architecture.md"):
                self.assertTrue((project_path / name).exists(), f"missing {name}")
            graph_pre = read_task_graph(project_path)
            self.assertEqual(len(graph_pre["tasks"]), 2)

            # 2. Drive the controller in-process.
            from orchestrator.core import autonomous as autonomous_mod
            from orchestrator.core import smoke as smoke_mod
            project = {
                "id": "project_golden", "name": "tiny-creator-tracker",
                "path": str(project_path),
            }
            controller = AutonomousController(
                project=project,
                run_inner_loop=make_fake_inner_loop(project_path),
                apply_candidate=apply_selected_candidate,
            )
            session = controller.start_or_resume()
            graph = read_task_graph(project_path)

            # Patch the deploy + smoke entry points at the autonomous module
            # level so the auto-deploy → auto-smoke chain (which doesn't take
            # injected runners through advance_one_task) uses our fakes.
            import unittest.mock as mock
            with mock.patch.object(autonomous_mod, "run_vercel_deploy",
                                    side_effect=make_fake_deploy_runner()), \
                 mock.patch.object(smoke_mod, "default_http_client",
                                    side_effect=make_fake_smoke_http()):
                # advance until exhausted
                for _ in range(20):
                    outcome = controller.advance_one_task(session, graph)
                    if outcome is None or session.status in {"paused", "completed"}:
                        break
                    graph = read_task_graph(project_path)

            # 3. Assert artifacts.
            graph_post = read_task_graph(project_path)
            completed = [t for t in graph_post["tasks"] if t["status"] == "completed"]
            self.assertGreaterEqual(len(completed), 2, "expected both tasks to complete")
            for t in completed:
                self.assertTrue(t.get("commit"), f"task {t['id']} has no commit hash")

            # 4. Real git commits exist.
            log = _git("log", "--oneline", cwd=project_path).stdout.strip().splitlines()
            self.assertGreaterEqual(len(log), 3, f"expected init + >=2 task commits, got: {log}")

            # 5. Deployment artifact written.
            deployment = latest_deployment(project_path, session.session_id)
            self.assertIsNotNone(deployment)
            self.assertEqual(deployment["status"], "ready")
            self.assertEqual(deployment["deployment_url"], "https://golden.vercel.app")

            # 6. Smoke artifact written.
            smoke = latest_smoke_check(project_path, session.session_id)
            self.assertIsNotNone(smoke)
            self.assertEqual(smoke["status"], "passed")

            # 7. final-run-status.md present + contains every required section.
            sess_dir = project_path / ".agent" / "autonomous" / "sessions" / session.session_id
            final_md = (sess_dir / "final-run-status.md").read_text(encoding="utf-8")
            for section in ("Final Run Status", "## Tasks", "## Deployment",
                            "## Smoke Checks", "## Evidence Trail", "## Next Actions"):
                self.assertIn(section, final_md, f"final report missing section: {section}")

            # 8. No open blocking review items.
            opens = list_review_items(project_path, session.session_id, only_open=True)
            blocking = [r for r in opens if getattr(r, "severity", "blocking") == "blocking"]
            self.assertEqual(len(blocking), 0, f"unexpected blocking reviews: {blocking}")

            # 9. CLI status + logs --tail surface the run.
            status_out = _cli(root, "autonomous", "status", "--project", project_id).stdout
            self.assertIn("Status:", status_out)
            self.assertIn("Deployment:", status_out)
            self.assertIn("Smoke checks:", status_out)
            logs_out = _cli(root, "autonomous", "logs", "--project", project_id, "--tail", "200").stdout
            self.assertIn("session_started", logs_out)
            self.assertIn("task_committed", logs_out)
            self.assertIn("smoke_check_completed", logs_out)

            # 10. Cross-artifact schema validation — no errors anywhere.
            from orchestrator.core.artifact_validation import (
                has_validation_errors, validate_session_directory,
            )
            report = validate_session_directory(sess_dir)
            self.assertFalse(
                has_validation_errors(report),
                f"artifact validation surfaced errors: {report}",
            )

            # 11. The validate-artifacts CLI surfaces the same result.
            cli_out = _cli(root, "autonomous", "validate-artifacts",
                           "--project", project_id, "--json").stdout
            cli_payload = json.loads(cli_out)
            self.assertTrue(cli_payload["ok"], f"CLI reported errors: {cli_payload}")
            self.assertEqual(cli_payload["session_id"], session.session_id)


class GoldenSmokeFailureTests(unittest.TestCase):
    """End-to-end failure exit: smoke returns 500 → review item +
    session paused + final report names the failure + resume blocked."""

    def test_smoke_failure_pauses_session_and_blocks_resume(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_golden_project(root)
            _enable_full_deploy_config(project_path)
            _git("add", "agent-studio.yaml", cwd=project_path)
            _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "deploy config", cwd=project_path)
            from orchestrator.core import autonomous as autonomous_mod
            from orchestrator.core import smoke as smoke_mod
            project = {
                "id": "project_golden", "name": "tiny-creator-tracker",
                "path": str(project_path),
            }
            controller = AutonomousController(
                project=project,
                run_inner_loop=make_fake_inner_loop(project_path),
                apply_candidate=apply_selected_candidate,
            )
            session = controller.start_or_resume()
            graph = read_task_graph(project_path)

            import unittest.mock as mock
            # Deploy succeeds, smoke returns 500 → status_code_mismatch.
            with mock.patch.object(autonomous_mod, "run_vercel_deploy",
                                    side_effect=make_fake_deploy_runner()), \
                 mock.patch.object(smoke_mod, "default_http_client",
                                    side_effect=make_fake_smoke_http(status=500, body="boom")):
                for _ in range(20):
                    outcome = controller.advance_one_task(session, graph)
                    if outcome is None or session.status in {"paused", "completed"}:
                        break
                    graph = read_task_graph(project_path)

            # Smoke artifact failed.
            smoke = latest_smoke_check(project_path, session.session_id)
            self.assertIsNotNone(smoke)
            self.assertEqual(smoke["status"], "failed")
            self.assertIn(smoke.get("failure", {}).get("failure_type"),
                          {"status_mismatch", "body_assertion_failed"})

            # Review item created.
            opens = list_review_items(project_path, session.session_id, only_open=True)
            smoke_reviews = [r for r in opens if "smoke" in (r.reason_code or "")]
            self.assertGreaterEqual(len(smoke_reviews), 1)

            # Session paused with smoke-related reason.
            self.assertEqual(session.status, "paused")
            self.assertIn("smoke", (session.pause_reason or "").lower())

            # final-run-status.md mentions the failure + a review id +
            # contains a Next Actions block with a follow-up command.
            sess_dir = project_path / ".agent" / "autonomous" / "sessions" / session.session_id
            final_md = (sess_dir / "final-run-status.md").read_text(encoding="utf-8")
            self.assertIn("## Smoke Checks", final_md)
            self.assertIn("failed", final_md.lower())
            self.assertIn("## Next Actions", final_md)
            self.assertIn("autonomous reviews", final_md)

            # Resume CLI is blocked by the open review.
            result = _cli(root, "autonomous", "resume", "--project", project_id, check=False)
            combined = (result.stdout + result.stderr).lower()
            self.assertNotEqual(result.returncode, 0,
                                f"expected non-zero exit; got stdout={result.stdout!r} stderr={result.stderr!r}")
            self.assertIn("review", combined)


if __name__ == "__main__":
    unittest.main()
