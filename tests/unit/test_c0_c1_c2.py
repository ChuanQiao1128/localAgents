"""Tests for batch-3 first cut (C0a–C2):

  C0b — critical-artifact floor caps phase_score
  C0c — re-running a phase replaces previous artifacts in current view
  C0d — run lock prevents concurrent advance / reclaims after staleness
  C0e — format-preserving fallback wrappers (mostly covered in test_b2_b3_b4_b5)
  C1a — project type detection
  C1b — executor allowlist + project type detection wiring
  C1c — implementation fix loop runs, repairs, records evidence
  C2  — final report shows Executable Evidence; delivery grade capped on failure
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from orchestrator.agents.base import AgentResult
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.executor import (
    CommandResult,
    Executor,
    ExecutionEvidence,
    ProjectType,
    detect_project_type,
    _is_allowed,
)
from orchestrator.core.run_manager import create_engine
from orchestrator.core.workflow_engine import _cap_grade


def _autonomous_env():
    class _Ctx:
        def __enter__(self):
            self._old = os.environ.get("LOCALAGENTS_AUTONOMOUS")
            os.environ["LOCALAGENTS_AUTONOMOUS"] = "1"
            return self

        def __exit__(self, *args):
            if self._old is None:
                os.environ.pop("LOCALAGENTS_AUTONOMOUS", None)
            else:
                os.environ["LOCALAGENTS_AUTONOMOUS"] = self._old

    return _Ctx()


def _make_engine(tmp: str, runner: MagicMock):
    paths = resolve_paths(tmp)
    initialize_workspace(paths)
    engine = create_engine(paths)
    engine._agent_runner = runner
    return engine, paths


# ---------------------------------------------------------------------------
# C0b — critical artifact floor
# ---------------------------------------------------------------------------


class CriticalFloorTests(unittest.TestCase):
    def test_critical_artifact_failure_floors_phase_score(self) -> None:
        # PRD is critical. If LLM omits prd.md, phase_score must be ≤ 59 even
        # if other required outputs in the same phase pass.
        runner = MagicMock()

        def respond(agent_config, context):
            outputs = list(context.output_paths or [])
            files = {}
            for p in outputs:
                if p.endswith("prd.md"):
                    # Skip it — forces fallback (which is failed for criticals).
                    continue
                # Acceptance criteria with valid AC IDs to satisfy contract.
                if p.endswith("acceptance-criteria.md"):
                    files[p] = "# AC\n" + "\n".join(
                        f"- AC-{i:03d} body text" for i in range(1, 9)
                    ) + "\n" + ("filler " * 200)
                else:
                    files[p] = "# x\n" + ("filler " * 400)
            return AgentResult(status="completed", summary="ok", files=files)

        runner.run_task.side_effect = respond
        with _autonomous_env(), tempfile.TemporaryDirectory() as tmp:
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            engine.run(project["id"], "software_project")
            row = engine.db.query_one(
                "SELECT phase_score FROM phases WHERE phase_id = 'prd' "
                "AND run_id IN (SELECT id FROM runs WHERE project_id = ?)",
                (project["id"],),
            )
            self.assertIsNotNone(row)
            score = row["phase_score"]
            self.assertLessEqual(score, 59, f"expected critical-floor cap, got score={score}")


# ---------------------------------------------------------------------------
# C0c — artifact idempotency
# ---------------------------------------------------------------------------


class ArtifactIdempotencyTests(unittest.TestCase):
    def test_re_register_marks_old_row_not_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            from orchestrator.core.artifact_store import ArtifactStore
            from orchestrator.db import Database

            db_path = Path(tmp) / "db.sqlite3"
            Database(db_path).initialize()
            db = Database(db_path)
            # FK requires project + run rows.
            db.execute(
                "INSERT INTO projects (id, name, idea, path, status, created_at, updated_at) "
                "VALUES ('p', 'p', 'i', '/t', 'created', '2026-01-01', '2026-01-01')"
            )
            db.execute(
                "INSERT INTO runs (id, project_id, workflow_id, status, current_phase, created_at, updated_at) "
                "VALUES ('r', 'p', 'w', 'running', NULL, '2026-01-01', '2026-01-01')"
            )
            store = ArtifactStore(db)
            store.register(
                project_id="p", run_id="r", phase_id="design",
                path="docs/design/spec.md", kind="markdown",
                summary="first", source_type="llm", trust_level="medium",
            )
            store.register(
                project_id="p", run_id="r", phase_id="design",
                path="docs/design/spec.md", kind="markdown",
                summary="second", source_type="repaired", trust_level="medium",
                repair_attempt=1,
            )
            current = db.query_all(
                "SELECT summary, repair_attempt FROM artifacts "
                "WHERE run_id = 'r' AND path = 'docs/design/spec.md' AND is_current = 1"
            )
            history = db.query_all(
                "SELECT summary FROM artifacts WHERE run_id = 'r' AND path = 'docs/design/spec.md'"
            )
            self.assertEqual(len(current), 1)
            self.assertEqual(current[0]["summary"], "second")
            self.assertEqual(current[0]["repair_attempt"], 1)
            self.assertEqual(len(history), 2)


# ---------------------------------------------------------------------------
# C0d — run lock
# ---------------------------------------------------------------------------


class RunLockTests(unittest.TestCase):
    def test_acquire_then_release(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            runner.run_task.return_value = AgentResult(status="completed", summary="ok", files={})
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("idea", Path(tmp) / "projects")
            # Start a manual run; cmd_run path bypasses helper but engine.run
            # itself locks/unlocks. We simulate by inserting a run row.
            from orchestrator.core.ids import short_id, now_iso
            run_id = short_id("run")
            engine.db.execute(
                "INSERT INTO runs (id, project_id, workflow_id, status, current_phase, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, project["id"], "software_project", "running", None, now_iso(), now_iso()),
            )
            owner1 = engine.acquire_run_lock(run_id, owner_id="me-1")
            self.assertEqual(owner1, "me-1")
            engine.release_run_lock(run_id)
            # After release, another caller can acquire freely.
            owner2 = engine.acquire_run_lock(run_id, owner_id="me-2")
            self.assertEqual(owner2, "me-2")

    def test_concurrent_acquire_blocks_when_fresh(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("idea", Path(tmp) / "projects")
            from orchestrator.core.ids import short_id, now_iso
            run_id = short_id("run")
            engine.db.execute(
                "INSERT INTO runs (id, project_id, workflow_id, status, current_phase, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, project["id"], "software_project", "running", None, now_iso(), now_iso()),
            )
            engine.acquire_run_lock(run_id, owner_id="first")
            with self.assertRaises(RuntimeError) as ctx:
                engine.acquire_run_lock(run_id, owner_id="second")
            self.assertIn("locked by first", str(ctx.exception))

    def test_stale_lock_is_reclaimed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("idea", Path(tmp) / "projects")
            from orchestrator.core.ids import short_id
            run_id = short_id("run")
            # Manually insert run with stale heartbeat
            engine.db.execute(
                "INSERT INTO runs (id, project_id, workflow_id, status, current_phase, "
                "created_at, updated_at, locked_by, heartbeat_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id, project["id"], "software_project", "running", None,
                    "2026-01-01T00:00:00+00:00", "2026-01-01T00:00:00+00:00",
                    "ghost", "2020-01-01T00:00:00+00:00",  # very old heartbeat
                ),
            )
            owner = engine.acquire_run_lock(run_id, owner_id="reclaimer")
            self.assertEqual(owner, "reclaimer")


# ---------------------------------------------------------------------------
# C1a — project type detection
# ---------------------------------------------------------------------------


class ProjectTypeTests(unittest.TestCase):
    def test_node_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "package.json").write_text('{"name":"x"}')
            pt = detect_project_type(p)
            self.assertIsNotNone(pt)
            self.assertEqual(pt.name, "node")

    def test_python_detection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)
            (p / "requirements.txt").write_text("")
            pt = detect_project_type(p)
            self.assertIsNotNone(pt)
            self.assertEqual(pt.name, "python")

    def test_unknown_returns_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(detect_project_type(Path(tmp)))


# ---------------------------------------------------------------------------
# C1b — executor allowlist
# ---------------------------------------------------------------------------


class ExecutorAllowlistTests(unittest.TestCase):
    def test_allowlist_accepts_npm_install(self) -> None:
        self.assertTrue(_is_allowed(["npm", "install"]))

    def test_allowlist_rejects_rm(self) -> None:
        self.assertFalse(_is_allowed(["rm", "-rf", "/"]))

    def test_allowlist_rejects_curl(self) -> None:
        self.assertFalse(_is_allowed(["curl", "https://x"]))

    def test_allowlist_accepts_pytest_via_python_module(self) -> None:
        self.assertTrue(_is_allowed(["python3", "-m", "pytest"]))

    def test_executor_skips_missing_binary(self) -> None:
        # pretend the runner shouldn't be called because the binary is missing.
        runner_mock = MagicMock()
        ex = Executor(runner=runner_mock)
        with tempfile.TemporaryDirectory() as tmp:
            with patch("orchestrator.core.executor.shutil.which", return_value=None):
                pt = ProjectType(
                    name="node",
                    install=[["nonexistent-cmd"]],
                    build=None,
                    test=None,
                )
                ev = ex.run_phase_checks(Path(tmp), pt)
                self.assertIsNone(ev.install)
                runner_mock.assert_not_called()


# ---------------------------------------------------------------------------
# D0.5 — Per-project Python venv bootstrap
# ---------------------------------------------------------------------------
#
# Why these tests exist: macOS Homebrew Python (and a growing set of
# Linux distros) reject `python3 -m pip install` to the system site
# packages with PEP 668 "externally-managed-environment". Without a venv,
# every Python project the orchestrator generates fails install on those
# hosts before pip even reads pyproject.toml. We bootstrap a per-project
# `.venv/` and reroute install/build/test commands through it. These
# tests pin both branches: the success branch (commands rewritten to
# venv python) and the failure branch (we record the bootstrap failure
# in notes and continue with the original commands so the failure is
# still visible in stderr_tail rather than masked).


class ExecutorPythonVenvTests(unittest.TestCase):
    def test_python_project_bootstraps_venv_and_rewrites_commands(self) -> None:
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            (project_path / "pyproject.toml").write_text(
                "[project]\nname = 'demo'\nversion = '0.1'\n"
            )

            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                calls.append(list(argv))
                # Simulate venv module: actually create the python binary
                # so the subsequent `.venv/bin/python` exists check passes.
                if len(argv) >= 4 and argv[1:3] == ["-m", "venv"]:
                    venv_dir = Path(argv[3])
                    if sys.platform == "win32":
                        bin_dir = venv_dir / "Scripts"
                        py_name = "python.exe"
                    else:
                        bin_dir = venv_dir / "bin"
                        py_name = "python"
                    bin_dir.mkdir(parents=True, exist_ok=True)
                    py_path = bin_dir / py_name
                    py_path.write_text("#!/bin/sh\nexit 0\n")
                    py_path.chmod(0o755)
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout="ok", stderr="")

            ex = Executor(runner=fake_run)
            pt = detect_project_type(project_path)
            self.assertIsNotNone(pt)
            self.assertEqual(pt.name, "python")

            ev = ex.run_phase_checks(project_path, pt)

            # 1. venv bootstrap call happened.
            bootstrap_calls = [c for c in calls if len(c) >= 3 and c[1:3] == ["-m", "venv"]]
            self.assertEqual(
                len(bootstrap_calls), 1,
                f"expected exactly one venv bootstrap call, got: {calls}",
            )

            # 2. Subsequent install/test commands used venv python (absolute
            #    path containing `.venv`), not the literal `python3`.
            non_bootstrap = [c for c in calls if not (len(c) >= 3 and c[1:3] == ["-m", "venv"])]
            self.assertTrue(non_bootstrap, "expected at least one post-bootstrap call")
            for c in non_bootstrap:
                # Only python entries were rewritten; npm/yarn entries (if any)
                # would be untouched, but Python project type only emits python entries.
                self.assertNotEqual(c[0], "python3", f"command {c} should have been rewritten to venv python")
                self.assertIn(".venv", c[0], f"command {c} did not route through venv python")

            # 3. Install + test recorded as passed (fake_run returns 0 for everything
            #    except venv bootstrap and pip install/pytest, which all return 0).
            self.assertIsNotNone(ev.install)
            self.assertTrue(ev.install.passed, f"install should have passed; argv={ev.install.argv}")
            self.assertIsNotNone(ev.test)
            self.assertTrue(ev.test.passed)
            # 4. No error notes.
            self.assertEqual(
                [n for n in ev.notes if "venv" in n], [],
                f"unexpected venv-related notes: {ev.notes}",
            )

    def test_python_project_records_note_when_venv_bootstrap_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            (project_path / "requirements.txt").write_text("")

            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                calls.append(list(argv))
                # venv bootstrap returns non-zero and does NOT create the python binary.
                if len(argv) >= 3 and argv[1:3] == ["-m", "venv"]:
                    return subprocess.CompletedProcess(
                        args=argv, returncode=1, stdout="", stderr="boom: cannot create venv"
                    )
                # System pip install also fails (simulating PEP 668).
                if argv[0] == "python3" and "pip" in argv:
                    return subprocess.CompletedProcess(
                        args=argv, returncode=1, stdout="",
                        stderr="error: externally-managed-environment",
                    )
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

            ex = Executor(runner=fake_run)
            pt = detect_project_type(project_path)
            self.assertIsNotNone(pt)

            with patch("orchestrator.core.executor.shutil.which", return_value="/usr/bin/python3"):
                ev = ex.run_phase_checks(project_path, pt)

            # 1. There is a clear note explaining the venv bootstrap failure.
            self.assertTrue(
                any("venv" in n and "bootstrap failed" in n for n in ev.notes),
                f"expected a venv bootstrap-failed note, got: {ev.notes}",
            )
            # 2. install was attempted with system python3 (no rewrite happened).
            self.assertIsNotNone(ev.install)
            self.assertEqual(ev.install.argv[0], "python3")
            # 3. install failed (PEP 668 simulation), and the failure is preserved
            #    in stderr_tail rather than masked.
            self.assertFalse(ev.install.passed)
            self.assertIn("externally-managed-environment", ev.install.stderr_tail)

    def test_python_project_reuses_existing_venv(self) -> None:
        """A second run on the same project should not re-bootstrap the venv."""
        import sys

        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            (project_path / "pyproject.toml").write_text("[project]\nname='x'\nversion='1'\n")

            # Pre-create the venv so _ensure_python_venv short-circuits.
            if sys.platform == "win32":
                bin_dir = project_path / ".venv" / "Scripts"
                py_name = "python.exe"
            else:
                bin_dir = project_path / ".venv" / "bin"
                py_name = "python"
            bin_dir.mkdir(parents=True)
            py_path = bin_dir / py_name
            py_path.write_text("#!/bin/sh\nexit 0\n")
            py_path.chmod(0o755)

            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                calls.append(list(argv))
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

            ex = Executor(runner=fake_run)
            pt = detect_project_type(project_path)
            ev = ex.run_phase_checks(project_path, pt)

            # No venv-module bootstrap should have been attempted.
            bootstrap_calls = [c for c in calls if len(c) >= 3 and c[1:3] == ["-m", "venv"]]
            self.assertEqual(
                bootstrap_calls, [],
                f"existing venv should not be re-bootstrapped, got: {calls}",
            )
            # But install/test still routed through venv python.
            self.assertIsNotNone(ev.install)
            self.assertIn(".venv", ev.install.argv[0])


# ---------------------------------------------------------------------------
# C1c — implementation fix loop
# ---------------------------------------------------------------------------


class ImplementationFixLoopTests(unittest.TestCase):
    def test_fix_loop_repairs_after_failed_test_then_passes(self) -> None:
        # Simulate: first test fails, repair returns updated files, second test passes.
        call_count = {"developer": 0}
        runner = MagicMock()

        def respond(agent_config, context):
            outputs = list(context.output_paths or [])
            agent_id = agent_config.get("id")
            if agent_id == "developer":
                call_count["developer"] += 1
            files = {}
            for p in outputs:
                if p.endswith(".json"):
                    files[p] = '[{"id":"T1","title":"x","phase":"implementation"},{"id":"T2","title":"y","phase":"implementation"},{"id":"T3","title":"z","phase":"implementation"},{"id":"T4","title":"w","phase":"implementation"}]'
                elif p.endswith(".yaml"):
                    files[p] = "openapi: 3.0.0\ninfo:\n  title: x\n  version: '1'\npaths: {}\n"
                elif p.endswith(".md"):
                    files[p] = "# Document\n## Section\nbody " * 200
            # For implementation phase, also write a package.json so executor runs.
            if agent_id == "developer":
                files["package.json"] = '{"name":"demo","scripts":{"test":"echo ok"}}'
                files["index.js"] = 'console.log("hello")\n'
            return AgentResult(status="completed", summary="ok", files=files)

        runner.run_task.side_effect = respond

        # Mock the executor's subprocess runner — first test fails, repair
        # writes new files, second test passes.
        test_calls = {"n": 0}

        def fake_subprocess(argv, **kwargs):
            # install always succeeds.
            if argv[0] == "npm" and len(argv) > 1 and argv[1] == "install":
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")
            # Build / run commands succeed (just a no-op echo behavior).
            if argv[0] == "npm" and len(argv) > 1 and argv[1] == "run":
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")
            # npm test: fail on first call, pass thereafter.
            if argv[0] == "npm" and len(argv) > 1 and argv[1] == "test":
                test_calls["n"] += 1
                if test_calls["n"] == 1:
                    return subprocess.CompletedProcess(
                        args=argv, returncode=1, stdout="", stderr="FAIL: expected 1 got 0"
                    )
                return subprocess.CompletedProcess(args=argv, returncode=0, stdout="ok", stderr="")
            return subprocess.CompletedProcess(args=argv, returncode=0, stdout="", stderr="")

        with _autonomous_env(), tempfile.TemporaryDirectory() as tmp:
            engine, _ = _make_engine(tmp, runner=runner)
            # Inject a fake subprocess runner at module level.
            with patch("orchestrator.core.executor.subprocess.run", side_effect=fake_subprocess), \
                 patch("orchestrator.core.executor.shutil.which", return_value="/usr/bin/npm"):
                project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
                engine.run(project["id"], "software_project")

            # Expect at least 2 developer calls (initial + repair) for implementation.
            self.assertGreaterEqual(call_count["developer"], 2)

            # Execution evidence file written
            run_id = engine.latest_run(project["id"])["id"]
            evidence_path = Path(project["path"]) / ".agent" / "runs" / run_id / "execution" / "implementation-checks.json"
            self.assertTrue(evidence_path.exists())
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            self.assertEqual(evidence["project_type"], "node")
            self.assertGreaterEqual(len(evidence["attempts"]), 2)

            # D0.6: attempt_count is written back to the phases table for
            # implementation. Without this, the future D5 budget governor
            # would silently see 0 attempts no matter how many times the
            # fix loop ran.
            phase_row = engine.db.query_one(
                "SELECT attempt_count FROM phases WHERE run_id = ? AND phase_id = ?",
                (run_id, "implementation"),
            )
            self.assertIsNotNone(phase_row)
            self.assertEqual(
                phase_row["attempt_count"],
                len(evidence["attempts"]),
                f"attempt_count in DB ({phase_row['attempt_count']}) does not match "
                f"the actual fix-loop attempts ({len(evidence['attempts'])})",
            )

            # Other phases should have attempt_count = 1 (they ran once
            # successfully and produced their phase scoring).
            other_phase_row = engine.db.query_one(
                "SELECT attempt_count FROM phases WHERE run_id = ? AND phase_id = 'intake'",
                (run_id,),
            )
            self.assertIsNotNone(other_phase_row)
            self.assertEqual(other_phase_row["attempt_count"], 1)


# ---------------------------------------------------------------------------
# C2 — executable evidence in final report + grade caps
# ---------------------------------------------------------------------------


class ReportAndGradeCapsTests(unittest.TestCase):
    def test_grade_capped_to_d_when_install_failed(self) -> None:
        # Cap rule unit test, doesn't need full pipeline.
        self.assertEqual(_cap_grade("A", "D"), "D")
        self.assertEqual(_cap_grade("B", "C"), "C")
        self.assertEqual(_cap_grade("D", "C"), "D")  # already worse than ceiling
        self.assertEqual(_cap_grade("F", "A"), "F")

    def test_first_screen_includes_executable_evidence_line(self) -> None:
        runner = MagicMock()
        runner.run_task.return_value = AgentResult(status="completed", summary="ok", files={})
        with _autonomous_env(), tempfile.TemporaryDirectory() as tmp:
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            result = engine.run(project["id"], "software_project")
            report_path = Path(project["path"]) / ".agent" / "runs" / result["run_id"] / "final-run-status.md"
            text = report_path.read_text(encoding="utf-8")
            self.assertIn("Executable Evidence:", text)


if __name__ == "__main__":
    unittest.main()
