from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_REQUIREMENTS_TEMPLATE = """# Demo Project

Tiny static portfolio.

## Add landing page

Provide a homepage with hero text.

- Page mounts at /
- Hero text visible

Scope: apps/web/**
Risk: low

## Add about page

Provide a static about page.

Depends: Add landing page

- Page mounts at /about
- Bio paragraph visible
"""


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LOCALAGENTS_FORCE_STUB": "1", "LOCALAGENTS_QUIET": "1"}
    return subprocess.run(
        [sys.executable, "-m", "orchestrator.cli", "--root", str(root), *args],
        cwd=Path(__file__).resolve().parents[2],
        text=True, capture_output=True, check=True, env=env,
    )


def _cli_no_check(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LOCALAGENTS_FORCE_STUB": "1", "LOCALAGENTS_QUIET": "1"}
    return subprocess.run(
        [sys.executable, "-m", "orchestrator.cli", "--root", str(root), *args],
        cwd=Path(__file__).resolve().parents[2],
        text=True, capture_output=True, check=False, env=env,
    )


def _project_path(root: Path, project_id: str) -> Path:
    from orchestrator.config import resolve_paths
    from orchestrator.core.run_manager import create_engine
    engine = create_engine(resolve_paths(root))
    return Path(engine.require_project(project_id)["path"])


def _setup_project_with_requirements(root: Path) -> tuple[str, Path]:
    """Init workspace, create project from requirements, init git, return (project_id, project_path)."""
    _cli(root, "init")
    req = root / "requirements.md"
    req.write_text(_REQUIREMENTS_TEMPLATE, encoding="utf-8")
    new = _cli(root, "new", "--from", str(req))
    project_id = next(t for t in new.stdout.split() if t.startswith("project_"))
    project_path = _project_path(root, project_id)
    # Init git in the project so autonomous start passes its preflight checks.
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project_path, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=project_path, check=True)
    # Add only the just-ingested artifacts; .agent/ is ignored by the worktree-clean check.
    subprocess.run(["git", "add", "requirements.md", "prd.md", "task-graph.json", "architecture.md", "acceptance-criteria.json"], cwd=project_path, check=True)
    subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"], cwd=project_path, check=True)
    return project_id, project_path


class AutonomousCliTests(unittest.TestCase):
    """MVP-4A acceptance — CLI-level e2e for the Resumable Autonomous Controller.

    Real AgenticProjectRuntime is invoked. With no patch_worker (default
    `none`), every inner run yields decision=needs-human-review (no source
    patch), which exercises the pause-on-needs-review branch. Promote /
    abandoned / commit branches are covered in tests/unit/test_autonomous.
    """

    # ------------------------------------------------------------------
    # Acceptance #1, #2 — requirements ingest
    # ------------------------------------------------------------------
    def test_new_from_requirements_creates_prd_acceptance_and_task_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            for name in ("requirements.md", "prd.md", "acceptance-criteria.json", "architecture.md", "task-graph.json"):
                self.assertTrue((project_path / name).exists(), f"missing {name}")
            graph = json.loads((project_path / "task-graph.json").read_text(encoding="utf-8"))
            self.assertEqual(len(graph["tasks"]), 2)

    def test_task_graph_tasks_are_bounded_with_scope_paths_and_acceptance_criteria(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, project_path = _setup_project_with_requirements(root)
            graph = json.loads((project_path / "task-graph.json").read_text(encoding="utf-8"))
            for task in graph["tasks"]:
                self.assertTrue(task["intent"])
                self.assertTrue(task["scope_paths"])
                self.assertGreaterEqual(len(task["acceptance_criteria"]), 1)
                self.assertEqual(task["status"], "pending")
                self.assertIn("id", task)

    # ------------------------------------------------------------------
    # Acceptance #3, #4 — session lifecycle and branch
    # ------------------------------------------------------------------
    def test_autonomous_start_creates_session_state_and_controller_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id)
            sessions_dir = project_path / ".agent/autonomous/sessions"
            self.assertTrue(sessions_dir.is_dir())
            session_dirs = list(sessions_dir.iterdir())
            self.assertEqual(len(session_dirs), 1)
            session_dir = session_dirs[0]
            self.assertTrue((session_dir / "autonomous-session.json").exists())
            self.assertTrue((session_dir / "controller-log.jsonl").exists())

    def test_autonomous_start_creates_session_branch_without_touching_main(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id)
            current_branch = subprocess.run(
                ["git", "rev-parse", "--abbrev-ref", "HEAD"],
                cwd=project_path, capture_output=True, text=True, check=True,
            ).stdout.strip()
            self.assertTrue(current_branch.startswith("agentic/autonomous/"))
            # Switch to main and verify it has no autonomous commits beyond init.
            log_main = subprocess.run(
                ["git", "log", "main", "--oneline"],
                cwd=project_path, capture_output=True, text=True, check=True,
            ).stdout.strip().splitlines()
            self.assertEqual(len(log_main), 1, f"main should have only the init commit, got: {log_main}")

    # ------------------------------------------------------------------
    # Acceptance #5, #8 — task ordering and pause on needs-human-review
    # ------------------------------------------------------------------
    def test_autonomous_runs_next_dependency_satisfied_task(self) -> None:
        # The two-task graph has task-002 depending on task-001. After one
        # advance, task-001 (no deps) is the one that was attempted; task-002
        # remains pending.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id, "--max-steps", "1")
            graph = json.loads((project_path / "task-graph.json").read_text(encoding="utf-8"))
            self.assertEqual(graph["tasks"][0]["status"], "needs-human-review")  # attempted
            self.assertEqual(graph["tasks"][1]["status"], "pending")  # blocked / not yet picked

    def test_needs_human_review_pauses_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id)
            sess_path = next((project_path / ".agent/autonomous/sessions").iterdir()) / "autonomous-session.json"
            sess = json.loads(sess_path.read_text(encoding="utf-8"))
            self.assertEqual(sess["status"], "paused")
            self.assertEqual(sess["pause_reason"], "needs_human_review")

    # ------------------------------------------------------------------
    # Acceptance #11, #12 — status / logs CLIs
    # ------------------------------------------------------------------
    def test_status_reports_current_task_counts_and_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id)
            out = _cli(root, "autonomous", "status", "--project", project_id)
            self.assertIn("Session:", out.stdout)
            self.assertIn("Status:", out.stdout)
            self.assertIn("Branch: agentic/autonomous/", out.stdout)
            self.assertIn("Budget:", out.stdout)
            self.assertIn("max_total_inner_runs", out.stdout)
            self.assertIn("needs-human-review:", out.stdout)

    def test_logs_tail_reads_controller_log_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id)
            out = _cli(root, "autonomous", "logs", "--project", project_id, "--tail", "50")
            # Multiple events should appear
            self.assertIn("session_started", out.stdout)
            self.assertIn("task_started", out.stdout)
            self.assertIn("session_paused", out.stdout)

    # ------------------------------------------------------------------
    # Acceptance #13, #14, #15 — halt / resume / restart recovery
    # ------------------------------------------------------------------
    def test_halt_sets_halt_requested_and_pauses_after_current_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id, "--max-steps", "1")
            # Reset session to running so we can test halt → pause path.
            sess_dir = next((project_path / ".agent/autonomous/sessions").iterdir())
            sess_path = sess_dir / "autonomous-session.json"
            sess = json.loads(sess_path.read_text(encoding="utf-8"))
            sess["status"] = "running"
            sess["pause_reason"] = None
            sess_path.write_text(json.dumps(sess), encoding="utf-8")
            _cli(root, "autonomous", "halt", "--project", project_id)
            sess = json.loads(sess_path.read_text(encoding="utf-8"))
            self.assertTrue(sess["halt_requested"])
            self.assertEqual(sess["status"], "paused")

    def test_resume_continues_from_last_incomplete_task(self) -> None:
        # First start hits needs-human-review on task-001 and pauses, creating
        # a blocking review item. MVP-4D resume gating refuses until the
        # review is resolved. We resolve + mark task-001 completed, then
        # resume.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id)
            graph_path = project_path / "task-graph.json"
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            graph["tasks"][0]["status"] = "completed"
            graph_path.write_text(json.dumps(graph), encoding="utf-8")
            sess_path = next((project_path / ".agent/autonomous/sessions").iterdir()) / "autonomous-session.json"
            sess = json.loads(sess_path.read_text(encoding="utf-8"))
            sess["counters"]["needs_review_tasks"] = 0
            sess_path.write_text(json.dumps(sess), encoding="utf-8")
            # Resolve any open blocking reviews before resume.
            review_dir = sess_path.parent / "review-items"
            for review_file in review_dir.glob("*.json"):
                review = json.loads(review_file.read_text(encoding="utf-8"))
                if review.get("status") == "open":
                    _cli(root, "autonomous", "reviews", "resolve", review["review_id"],
                         "--project", project_id, "--note", "manually fixed for test")
            _cli(root, "autonomous", "resume", "--project", project_id)
            graph = json.loads(graph_path.read_text(encoding="utf-8"))
            self.assertIn(graph["tasks"][1]["status"], {"needs-human-review", "completed", "abandoned"})

    def test_restart_recovers_from_existing_session_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id)
            sessions_before = list((project_path / ".agent/autonomous/sessions").iterdir())
            self.assertEqual(len(sessions_before), 1)
            # First start created a blocking review (needs-human-review on
            # task-001). Resolve it, then resume.
            review_dir = sessions_before[0] / "review-items"
            for review_file in review_dir.glob("*.json"):
                review = json.loads(review_file.read_text(encoding="utf-8"))
                if review.get("status") == "open":
                    _cli(root, "autonomous", "reviews", "resolve", review["review_id"],
                         "--project", project_id, "--note", "test resolution")
            _cli(root, "autonomous", "resume", "--project", project_id)
            sessions_after = list((project_path / ".agent/autonomous/sessions").iterdir())
            self.assertEqual(len(sessions_after), 1)

    # ------------------------------------------------------------------
    # Acceptance #16 — dirty worktree blocks start
    # ------------------------------------------------------------------
    def test_dirty_worktree_blocks_autonomous_start(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            (project_path / "uncommitted.txt").write_text("dirty", encoding="utf-8")
            result = _cli_no_check(root, "autonomous", "start", "--project", project_id)
            self.assertNotEqual(result.returncode, 0)
            err = result.stdout + result.stderr
            self.assertIn("not clean", err)
            self.assertIn("uncommitted.txt", err)

    # ------------------------------------------------------------------
    # Acceptance #18 — final-run-status.md written after each task
    # ------------------------------------------------------------------
    def test_final_run_status_is_written_after_each_task(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id)
            sess_dir = next((project_path / ".agent/autonomous/sessions").iterdir())
            final_path = sess_dir / "final-run-status.md"
            self.assertTrue(final_path.exists())
            body = final_path.read_text(encoding="utf-8")
            self.assertIn("Final Run Status", body)
            self.assertIn("Add landing page", body)

    # ------------------------------------------------------------------
    # MVP-4B: integration phase
    # ------------------------------------------------------------------
    def test_status_includes_integration_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id)
            out = _cli(root, "autonomous", "status", "--project", project_id)
            self.assertIn("Integration:", out.stdout)
            self.assertIn("runs:", out.stdout)
            # No integration ran (session paused on first needs-human-review),
            # so "no integration runs yet" should be shown.
            self.assertIn("no integration runs yet", out.stdout)

    def test_autonomous_integrate_runs_check_against_working_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            # Add a static html file so integration command (static-html-present) is declared.
            (project_path / "apps/web").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/index.html").write_text("<html></html>", encoding="utf-8")
            subprocess.run(["git", "add", "apps/web/index.html"], cwd=project_path, check=True)
            subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add index"], cwd=project_path, check=True)
            _cli(root, "autonomous", "start", "--project", project_id)
            # Manually integrate.
            out = _cli(root, "autonomous", "integrate", "--project", project_id)
            self.assertIn("Integration: passed", out.stdout)
            # JSONL recorded.
            sess_dir = next((project_path / ".agent/autonomous/sessions").iterdir())
            jsonl = sess_dir / "integration-results.jsonl"
            self.assertTrue(jsonl.exists())
            lines = [l for l in jsonl.read_text(encoding="utf-8").splitlines() if l.strip()]
            self.assertGreaterEqual(len(lines), 1)
            payload = json.loads(lines[-1])
            self.assertTrue(payload["passed"])
            self.assertEqual(payload["trigger_reason"], "manual")

    def test_status_reports_corrective_counts_section(self) -> None:
        """MVP-4C acceptance #11: `autonomous status` surfaces the corrective
        task counters and budget."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            _cli(root, "autonomous", "start", "--project", project_id)
            out = _cli(root, "autonomous", "status", "--project", project_id)
            self.assertIn("Corrective tasks:", out.stdout)
            self.assertIn("created: 0", out.stdout)
            self.assertIn("max budget:", out.stdout)

    def test_autonomous_integrate_fails_when_required_command_fails(self) -> None:
        # No apps/web/index.html → no required command → empty commands → passes.
        # To force failure, we need a required command to be declared and to fail.
        # The simplest way: declare apps/web/package.json with a build script that exits non-zero.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project_with_requirements(root)
            (project_path / "apps/web").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/package.json").write_text(
                json.dumps({"scripts": {"build": "node -e \"process.exit(2)\""}}),
                encoding="utf-8",
            )
            (project_path / "apps/web/app").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/app/page.tsx").write_text("export default function P(){return null}", encoding="utf-8")
            subprocess.run(["git", "add", "apps/web"], cwd=project_path, check=True)
            subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "add web"], cwd=project_path, check=True)
            _cli(root, "autonomous", "start", "--project", project_id)
            # Manually integrate — should fail (exit 1 reports integration
            # failure regardless of corrective injection).
            result = _cli_no_check(root, "autonomous", "integrate", "--project", project_id)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("Integration: FAILED", result.stdout)
            self.assertIn("build", result.stdout)
            sess_dir = next((project_path / ".agent/autonomous/sessions").iterdir())
            # MVP-4C: structured failure artifact written.
            failure_files = list((sess_dir / "integration-failures").glob("*/integration-failure.json"))
            self.assertEqual(len(failure_files), 1)
            artifact = json.loads(failure_files[0].read_text(encoding="utf-8"))
            self.assertEqual(artifact["failed_command"]["name"], "build")
            self.assertEqual(artifact["trigger"], "manual")
            self.assertEqual(artifact["detected_failure_type"], "build_failure")
            # Summary md still produced for human reading.
            self.assertTrue((sess_dir / "integration-failure-summary.md").exists())
            # MVP-4C: a corrective task was injected, so session is NOT paused
            # by integration_failed. Stdout should mention the corrective.
            self.assertIn("corrective task was injected", result.stdout)
            graph = json.loads((project_path / "task-graph.json").read_text(encoding="utf-8"))
            corrective_tasks = [t for t in graph["tasks"] if t.get("corrective")]
            self.assertEqual(len(corrective_tasks), 1)
            self.assertTrue(corrective_tasks[0]["id"].startswith("task-fix-integration-"))


class AutonomousReviewsCliTests(unittest.TestCase):
    """MVP-4D acceptance: CLI flow for `autonomous reviews list/show/approve/reject/resolve`
    plus resume gating + final-run-status surface + JSON modes."""

    def _setup(self, tmp: Path) -> tuple[str, Path]:
        project_id, project_path = _setup_project_with_requirements(tmp)
        # Trigger one start → produces a needs-human-review review item.
        _cli(tmp, "autonomous", "start", "--project", project_id)
        return project_id, project_path

    def _read_open_review_id(self, project_path: Path) -> str:
        sess_dir = next((project_path / ".agent/autonomous/sessions").iterdir())
        for review_file in (sess_dir / "review-items").glob("*.json"):
            review = json.loads(review_file.read_text(encoding="utf-8"))
            if review.get("status") == "open":
                return str(review["review_id"])
        raise AssertionError("no open review item found")

    def test_reviews_list_outputs_open_reviews(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            out = _cli(root, "autonomous", "reviews", "list", "--project", project_id)
            self.assertIn("Open review items: 1", out.stdout)
            self.assertIn("needs-human-review", out.stdout)
            self.assertIn("task-001", out.stdout)

    def test_reviews_list_json_includes_review_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            out = _cli(root, "autonomous", "reviews", "list", "--project", project_id, "--json")
            payload = json.loads(out.stdout)
            self.assertEqual(payload["project_id"], project_id)
            self.assertEqual(len(payload["review_items"]), 1)
            item = payload["review_items"][0]
            self.assertEqual(item["status"], "open")
            self.assertEqual(item["reason_code"], "needs-human-review")

    def test_status_includes_review_queue_section(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            out = _cli(root, "autonomous", "status", "--project", project_id)
            self.assertIn("Review queue:", out.stdout)
            self.assertIn("open: 1", out.stdout)
            self.assertIn("blocking: 1", out.stdout)

    def test_status_json_includes_review_status_block(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            out = _cli(root, "autonomous", "status", "--project", project_id, "--json")
            payload = json.loads(out.stdout)
            self.assertIn("review_status", payload)
            self.assertEqual(payload["review_status"]["open_review_count"], 1)
            self.assertEqual(payload["review_status"]["blocking_review_count"], 1)

    def test_reviews_show_prints_evidence_and_suggested_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            review_id = self._read_open_review_id(project_path)
            out = _cli(root, "autonomous", "reviews", "show", review_id, "--project", project_id)
            self.assertIn(f"Review id: {review_id}", out.stdout)
            self.assertIn("Reason: needs-human-review", out.stdout)
            self.assertIn("Evidence:", out.stdout)
            self.assertIn("Suggested next commands:", out.stdout)
            self.assertIn("agent-studio agentic-runs show", out.stdout)

    def test_resume_refuses_when_blocking_open_review_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            with self.assertRaises(subprocess.CalledProcessError) as cm:
                _cli(root, "autonomous", "resume", "--project", project_id)
            out = (cm.exception.stdout or "") + (cm.exception.stderr or "")
            self.assertIn("Refusing to start/resume", out)
            self.assertIn("blocking review(s) open", out)
            self.assertIn("agent-studio autonomous reviews", out)

    def test_reviews_reject_marks_review_rejected_and_task_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            review_id = self._read_open_review_id(project_path)
            _cli(root, "autonomous", "reviews", "reject", review_id,
                 "--project", project_id, "--reason", "patch makes no sense")
            sess_dir = next((project_path / ".agent/autonomous/sessions").iterdir())
            review = json.loads((sess_dir / "review-items" / f"{review_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(review["status"], "rejected")
            self.assertEqual(review["resolution"]["reason"], "patch makes no sense")
            graph = json.loads((project_path / "task-graph.json").read_text(encoding="utf-8"))
            t = next(t for t in graph["tasks"] if t["id"] == "task-001")
            self.assertEqual(t["status"], "blocked")
            self.assertEqual(t["block_reason"], "human_rejected")

    def test_reviews_resolve_marks_review_resolved_and_can_mark_task_pending(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            review_id = self._read_open_review_id(project_path)
            _cli(root, "autonomous", "reviews", "resolve", review_id,
                 "--project", project_id, "--note", "fixed manually", "--mark-task", "pending")
            sess_dir = next((project_path / ".agent/autonomous/sessions").iterdir())
            review = json.loads((sess_dir / "review-items" / f"{review_id}.json").read_text(encoding="utf-8"))
            self.assertEqual(review["status"], "resolved")
            self.assertEqual(review["resolution"]["note"], "fixed manually")
            self.assertEqual(review["resolution"]["mark_task"], "pending")
            graph = json.loads((project_path / "task-graph.json").read_text(encoding="utf-8"))
            t = next(t for t in graph["tasks"] if t["id"] == "task-001")
            self.assertEqual(t["status"], "pending")

    def test_resume_continues_after_review_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            review_id = self._read_open_review_id(project_path)
            _cli(root, "autonomous", "reviews", "resolve", review_id,
                 "--project", project_id, "--note", "test")
            # Reset the needs_review counter so resume isn't budget-blocked.
            sess_path = next((project_path / ".agent/autonomous/sessions").iterdir()) / "autonomous-session.json"
            sess = json.loads(sess_path.read_text(encoding="utf-8"))
            sess["counters"]["needs_review_tasks"] = 0
            sess_path.write_text(json.dumps(sess), encoding="utf-8")
            # Resume must now succeed (no longer blocked by open review).
            _cli(root, "autonomous", "resume", "--project", project_id)

    def test_reviews_approve_refuses_for_apply_failure_when_patch_missing(self) -> None:
        # No real candidate patch was generated by this run (patch_worker=none),
        # so approving the review (which has run_id+candidate_id="candidate-a")
        # should hit the safe Apply Gate refusal because patch.diff is empty.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            review_id = self._read_open_review_id(project_path)
            with self.assertRaises(subprocess.CalledProcessError) as cm:
                _cli(root, "autonomous", "reviews", "approve", review_id,
                     "--project", project_id, "--yes")
            out = (cm.exception.stdout or "") + (cm.exception.stderr or "")
            self.assertIn("Apply Gate REFUSED", out)

    def test_final_run_status_lists_human_review_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = self._setup(root)
            sess_dir = next((project_path / ".agent/autonomous/sessions").iterdir())
            body = (sess_dir / "final-run-status.md").read_text(encoding="utf-8")
            self.assertIn("## Human Review Queue", body)
            self.assertIn("### Open", body)
            self.assertIn("needs-human-review", body)


if __name__ == "__main__":
    unittest.main()
