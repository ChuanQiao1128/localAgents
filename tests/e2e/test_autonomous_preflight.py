"""RC-2B.11 e2e test for `agent-studio autonomous preflight`.

Drives the real CLI as a subprocess. No fakes — preflight is a pure
introspection command (no codex / vercel / network).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_REQUIREMENTS = """# Tiny

## A task

intent body.

- crit
"""


def _cli(root: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LOCALAGENTS_FORCE_STUB": "1", "LOCALAGENTS_QUIET": "1"}
    return subprocess.run(
        [sys.executable, "-m", "orchestrator.cli", "--root", str(root), *args],
        cwd=Path(__file__).resolve().parents[2],
        text=True, capture_output=True, check=check, env=env,
    )


def _setup_project(tmp: Path) -> tuple[str, Path]:
    _cli(tmp, "init")
    req = tmp / "requirements.md"
    req.write_text(_REQUIREMENTS, encoding="utf-8")
    new = _cli(tmp, "new", "--from", str(req))
    project_id = next(t for t in new.stdout.split() if t.startswith("project_"))
    from orchestrator.config import resolve_paths
    from orchestrator.core.run_manager import create_engine
    engine = create_engine(resolve_paths(tmp))
    project_path = Path(engine.require_project(project_id)["path"])
    return project_id, project_path


def _git_init(project_path: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project_path, check=True)
    subprocess.run(["git", "config", "user.email", "preflight@test"], cwd=project_path, check=True)
    subprocess.run(["git", "config", "user.name", "preflight"], cwd=project_path, check=True)
    subprocess.run(
        ["git", "add", "requirements.md", "prd.md", "task-graph.json",
         "architecture.md", "acceptance-criteria.json"],
        cwd=project_path, check=True,
    )
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"],
        cwd=project_path, check=True,
    )


class PreflightTests(unittest.TestCase):
    def test_no_git_repo_fails_git_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, _ = _setup_project(root)
            result = _cli(root, "autonomous", "preflight",
                          "--project", project_id, "--json", check=False)
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["overall"], "fail")
            git_check = next(c for c in payload["checks"] if c["name"] == "git_repo_present")
            self.assertEqual(git_check["status"], "fail")

    def test_clean_setup_with_patch_worker_none_passes_overall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project(root)
            _git_init(project_path)
            result = _cli(root, "autonomous", "preflight",
                          "--project", project_id, "--json")
            payload = json.loads(result.stdout)
            self.assertEqual(payload["overall"], "pass", f"unexpected fail: {payload}")
            # No codex_cli check should be present when patch_worker=none.
            names = [c["name"] for c in payload["checks"]]
            self.assertNotIn("codex_cli_available", names)

    def test_codex_configured_but_missing_fails_codex_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project(root)
            (project_path / "agent-studio.yaml").write_text(
                "agentic:\n"
                "  patch_worker: codex\n"
                "  codex:\n"
                "    command: this-codex-binary-definitely-does-not-exist-xyzzy\n",
                encoding="utf-8",
            )
            _git_init(project_path)
            # Need to commit the yaml too so worktree is clean.
            subprocess.run(["git", "add", "agent-studio.yaml"],
                           cwd=project_path, check=True)
            subprocess.run(["git", "-c", "commit.gpgsign=false",
                            "commit", "-q", "-m", "config"],
                           cwd=project_path, check=True)
            result = _cli(root, "autonomous", "preflight",
                          "--project", project_id, "--json", check=False)
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            codex_check = next(c for c in payload["checks"] if c["name"] == "codex_cli_available")
            self.assertEqual(codex_check["status"], "fail")
            self.assertIn("npm i -g @openai/codex", codex_check["detail"])

    def test_unknown_patch_worker_value_surfaces_via_config_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project(root)
            (project_path / "agent-studio.yaml").write_text(
                "agentic:\n  patch_worker: hand-of-god\n", encoding="utf-8"
            )
            _git_init(project_path)
            subprocess.run(["git", "add", "agent-studio.yaml"],
                           cwd=project_path, check=True)
            subprocess.run(["git", "-c", "commit.gpgsign=false",
                            "commit", "-q", "-m", "config"],
                           cwd=project_path, check=True)
            result = _cli(root, "autonomous", "preflight",
                          "--project", project_id, "--json", check=False)
            payload = json.loads(result.stdout)
            cfg_check = next(c for c in payload["checks"] if c["name"] == "agentic_config_loaded")
            self.assertEqual(cfg_check["status"], "fail")
            self.assertIn("hand-of-god", cfg_check["detail"])

    def test_uncommitted_agent_studio_yaml_is_caught_by_worktree_check(self) -> None:
        # RC-2D.3: real-world dogfood scenario — user edits agent-studio.yaml
        # but forgets to git-add + commit before `autonomous start`. Apply
        # Gate would refuse on the second task; preflight should catch it
        # earlier so the user fixes it before kicking off a run.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project(root)
            _git_init(project_path)
            (project_path / "agent-studio.yaml").write_text(
                "deploy:\n  enabled: false\n", encoding="utf-8"
            )
            # Note: NOT committed — uncommitted changes outside .agent/ +
            # task-graph.json should fail the worktree_clean check.
            result = _cli(root, "autonomous", "preflight",
                          "--project", project_id, "--json", check=False)
            self.assertNotEqual(result.returncode, 0)
            payload = json.loads(result.stdout)
            wt = next(c for c in payload["checks"] if c["name"] == "worktree_clean")
            self.assertEqual(wt["status"], "fail")
            self.assertIn("agent-studio.yaml", wt["detail"])

    def test_plain_text_output_lists_each_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path = _setup_project(root)
            _git_init(project_path)
            result = _cli(root, "autonomous", "preflight", "--project", project_id)
            self.assertIn("Overall: PASS", result.stdout)
            for name in ("git_repo_present", "worktree_clean",
                         "task_graph_has_tasks", "agentic_config_loaded",
                         "deploy_config_loaded"):
                self.assertIn(name, result.stdout)


if __name__ == "__main__":
    unittest.main()
