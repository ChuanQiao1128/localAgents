"""RC-2A regression: eval harness must recognize a project-root
`package.json`, not only `apps/web/package.json`.

Surfaced by RC-2A dogfood: a flat-layout project (Vite default,
Next.js default, plain Node) had `package.json` at the project root
with a `build` script. Pre-fix, `_build_eval_harness` returned
`commands: []` because it only checked `apps/web/package.json`. That
made the promotion gate's `required_eval_declared` fail, every task
returned `needs-human-review`, and the autonomous controller paused
on task-001. See docs/rc2-dogfood-report.md for the full trace.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.core.agentic_runtime import (
    _build_context_pack,
    _build_eval_harness,
    _detect_constraints,
    _detect_unknowns,
)
from orchestrator.core.autonomous import build_integration_commands


class RootPackageJsonEvalHarnessTests(unittest.TestCase):
    """Pin the post-fix behavior: root-level package.json with a build
    script produces a required `build` command at cwd `.`."""

    def _seed_root_package(self, tmp: Path, *, scripts: dict[str, str]) -> Path:
        project_path = tmp / "p"
        project_path.mkdir()
        (project_path / "package.json").write_text(
            json.dumps({"name": "x", "scripts": scripts}), encoding="utf-8"
        )
        return project_path

    def test_root_package_json_build_script_yields_required_build_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = self._seed_root_package(Path(tmp), scripts={"build": "echo ok"})
            harness = _build_eval_harness(project_path, {"unknowns": []})
            commands = harness["commands"]
            build_cmds = [c for c in commands if c.get("name") == "build"]
            self.assertEqual(len(build_cmds), 1, f"expected one build command; got: {commands}")
            self.assertTrue(build_cmds[0]["required"])
            self.assertEqual(build_cmds[0]["cwd"], ".")
            self.assertIn("npm run build", build_cmds[0]["cmd"])

    def test_root_package_json_typecheck_and_test_scripts_are_picked_up(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = self._seed_root_package(Path(tmp), scripts={
                "typecheck": "tsc --noEmit",
                "build": "vite build",
                "test": "vitest run",
            })
            commands = _build_eval_harness(project_path, {"unknowns": []})["commands"]
            names = {c["name"] for c in commands}
            self.assertSetEqual(names, {"typecheck", "build", "unit-tests"})
            for c in commands:
                self.assertEqual(c["cwd"], ".", f"command {c['name']} should cwd to project root")

    def test_apps_web_package_still_takes_precedence_when_present(self) -> None:
        # Existing monorepo layouts must keep working unchanged.
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp) / "p"
            (project_path / "apps" / "web").mkdir(parents=True)
            (project_path / "apps" / "web" / "package.json").write_text(
                json.dumps({"name": "web", "scripts": {"build": "next build"}}), encoding="utf-8"
            )
            # And a root package.json with a different script — should be ignored.
            (project_path / "package.json").write_text(
                json.dumps({"name": "root", "scripts": {"build": "rollup -c"}}), encoding="utf-8"
            )
            commands = _build_eval_harness(project_path, {"unknowns": []})["commands"]
            build_cmds = [c for c in commands if c.get("name") == "build"]
            self.assertEqual(len(build_cmds), 1)
            self.assertEqual(build_cmds[0]["cwd"], "apps/web", "apps/web layout must still take precedence")

    def test_no_package_json_falls_back_to_apps_web_index_html_check(self) -> None:
        # Pre-existing fallback still works.
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp) / "p"
            (project_path / "apps" / "web").mkdir(parents=True)
            (project_path / "apps" / "web" / "index.html").write_text("<html></html>", encoding="utf-8")
            commands = _build_eval_harness(project_path, {"unknowns": []})["commands"]
            self.assertEqual(len(commands), 1)
            self.assertEqual(commands[0]["name"], "static-html-present")

    def test_no_signals_at_all_yields_empty_command_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp) / "p"
            project_path.mkdir()
            harness = _build_eval_harness(project_path, {"unknowns": []})
            self.assertEqual(harness["commands"], [])
            self.assertTrue(harness["manual_review_required"])


class RootPackageJsonConstraintTests(unittest.TestCase):
    def test_root_package_json_constraint_lists_npm_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp) / "p"
            project_path.mkdir()
            (project_path / "package.json").write_text(
                json.dumps({"scripts": {"build": "x"}}), encoding="utf-8"
            )
            constraints = _detect_constraints(project_path, ["package.json"])
            self.assertTrue(any("project-root JavaScript package" in c for c in constraints))
            self.assertTrue(any("Project root exposes an npm build command" in c for c in constraints))


class RootPackageJsonIntegrationCommandTests(unittest.TestCase):
    """build_integration_commands wraps _build_eval_harness, so the same
    fix must surface in the integration runner that the autonomous
    controller calls between tasks and at session-end."""

    def test_integration_commands_include_root_build(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp) / "p"
            project_path.mkdir()
            (project_path / "package.json").write_text(
                json.dumps({"scripts": {"build": "echo ok"}}), encoding="utf-8"
            )
            commands = build_integration_commands(project_path)
            names = [c["name"] for c in commands]
            self.assertIn("build", names)


class UnknownsRegressionTests(unittest.TestCase):
    def test_root_package_json_clears_no_build_command_unknown(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp) / "p"
            project_path.mkdir()
            (project_path / "package.json").write_text(
                json.dumps({"scripts": {"build": "x"}}), encoding="utf-8"
            )
            constraints = _detect_constraints(project_path, ["package.json"])
            unknowns = _detect_unknowns(project_path, [], constraints,
                                        {"has_source_files": True})
            self.assertNotIn("No required build command was detected.", unknowns)


if __name__ == "__main__":
    unittest.main()
