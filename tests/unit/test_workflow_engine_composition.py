"""Tests for inter-phase artifact composition.

These cover the structural fix that lets agents see each other's work:
  1. Upstream phase outputs flow into downstream phases via AgentContext.inputs
  2. LLMs can return extra files beyond required_outputs (e.g. source code
     from the developer phase) and the orchestrator writes them safely
  3. Path-safety rejects absolute paths and parent-traversal escapes
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from orchestrator.agents.base import AgentResult
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.run_manager import create_engine
from orchestrator.core.workflow_engine import (
    _is_safe_relative_path,
    _phase_instructions,
)


def _make_engine(tmp: str, runner: MagicMock):
    paths = resolve_paths(tmp)
    initialize_workspace(paths)
    engine = create_engine(paths)
    engine._agent_runner = runner
    return engine, paths


class UpstreamPropagationTests(unittest.TestCase):
    def test_downstream_phase_sees_upstream_files(self) -> None:
        """The PRD phase must see the project-brief.md content the intake
        phase produced one step earlier."""
        seen_inputs: list[dict[str, str]] = []

        runner = MagicMock()

        def capture(agent_config, context):
            seen_inputs.append(dict(context.inputs))
            # Return a tiny valid AgentResult so the run progresses.
            outputs = list(context.output_paths or [])
            return AgentResult(
                status="completed",
                summary=f"{agent_config.get('id')} produced {len(outputs)} file(s)",
                files={p: f"# {p}\nFrom {agent_config.get('id')}.\n" for p in outputs},
            )

        runner.run_task.side_effect = capture
        with tempfile.TemporaryDirectory() as tmp:
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            engine.run(project["id"], "software_project")

        # Three phases ran before the PRD gate (intake, research, prd).
        self.assertEqual(len(seen_inputs), 3)

        # First phase (intake) sees no upstream files.
        self.assertEqual(seen_inputs[0], {})

        # Research sees the project-brief from intake.
        self.assertIn(".agent/project-brief.md", seen_inputs[1])
        self.assertIn("From lead.", seen_inputs[1][".agent/project-brief.md"])

        # PRD sees both the brief AND the research markdown.
        self.assertIn(".agent/project-brief.md", seen_inputs[2])
        self.assertIn("docs/product/research.md", seen_inputs[2])

    def test_phase_instructions_mention_phase_role(self) -> None:
        text = _phase_instructions(
            "architecture",
            "architect",
            "Build a todo CLI",
            {"docs/product/prd.md": "...", "docs/design/user-flow.md": "..."},
        )
        self.assertIn("architect", text)
        self.assertIn("Build a todo CLI", text)
        self.assertIn("OpenAPI", text)
        self.assertIn("docs/product/prd.md", text)
        self.assertIn("docs/design/user-flow.md", text)

    def test_unknown_phase_falls_back_to_generic_instructions(self) -> None:
        text = _phase_instructions("custom-thing", "custom_agent", "X", {})
        self.assertIn("custom_agent", text)
        self.assertIn("custom-thing", text)


class ExtraFilesTests(unittest.TestCase):
    def test_llm_can_write_files_beyond_required_outputs(self) -> None:
        runner = MagicMock()
        runner.run_task.return_value = AgentResult(
            status="completed",
            summary="brief + bonus",
            files={
                ".agent/project-brief.md": "# Brief\n",
                # Extras the LLM also produced — should be written too.
                "docs/scratch/notes.md": "# Notes\nExtra context.\n",
                "scripts/helper.sh": "#!/bin/sh\necho hi\n",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            engine.run(project["id"], "software_project")

            base = Path(project["path"])
            self.assertTrue((base / ".agent/project-brief.md").exists())
            self.assertTrue((base / "docs/scratch/notes.md").exists())
            self.assertEqual(
                (base / "scripts/helper.sh").read_text(encoding="utf-8"),
                "#!/bin/sh\necho hi\n",
            )

    def test_unsafe_paths_are_rejected(self) -> None:
        runner = MagicMock()
        runner.run_task.return_value = AgentResult(
            status="completed",
            summary="malicious",
            files={
                ".agent/project-brief.md": "# OK\n",
                "/etc/passwd": "should never write",
                "../escape.txt": "should never write",
                "~/clobber.txt": "should never write",
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            engine.run(project["id"], "software_project")

            base = Path(project["path"])
            self.assertTrue((base / ".agent/project-brief.md").exists())
            # None of the unsafe paths should be written anywhere reachable.
            self.assertFalse(Path("/etc/passwd-test").exists())
            self.assertFalse((base / ".." / "escape.txt").resolve().exists())


class PathSafetyTests(unittest.TestCase):
    def test_normal_relative_path_is_safe(self) -> None:
        self.assertTrue(_is_safe_relative_path("apps/web/index.html"))
        self.assertTrue(_is_safe_relative_path("docs/qa/test-plan.md"))
        self.assertTrue(_is_safe_relative_path("a.txt"))

    def test_absolute_path_is_unsafe(self) -> None:
        self.assertFalse(_is_safe_relative_path("/etc/passwd"))
        self.assertFalse(_is_safe_relative_path("/tmp/file"))

    def test_parent_traversal_is_unsafe(self) -> None:
        self.assertFalse(_is_safe_relative_path("../etc/passwd"))
        self.assertFalse(_is_safe_relative_path("a/../../etc/passwd"))
        self.assertFalse(_is_safe_relative_path(".."))

    def test_home_expansion_is_unsafe(self) -> None:
        self.assertFalse(_is_safe_relative_path("~/secret.txt"))

    def test_empty_or_non_string_is_unsafe(self) -> None:
        self.assertFalse(_is_safe_relative_path(""))
        self.assertFalse(_is_safe_relative_path(None))  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
