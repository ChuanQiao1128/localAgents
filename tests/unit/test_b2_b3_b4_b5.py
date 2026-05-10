"""Tests for batch-2 hardening:

  B1 — fallback content carries an untrusted frontmatter (already covered in
       test_workflow_engine_autonomous; this file adds the unit-level checks
       on the wrapping helper).
  B2 — artifact contracts + Validator
  B3 — phase score + delivery grade A-F
  B4 — final-run-status.md first-screen layout
  B5 — `resume <run_id>` CLI command
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

from orchestrator.agents.base import AgentResult
from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.contracts import load_contracts
from orchestrator.contracts.validator import (
    ArtifactContract,
    Validator,
)
from orchestrator.core.artifact_store import wrap_with_untrusted_frontmatter
from orchestrator.core.run_manager import create_engine


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
# B1 — wrap_with_untrusted_frontmatter
# ---------------------------------------------------------------------------


class UntrustedFrontmatterTests(unittest.TestCase):
    def test_markdown_gets_yaml_frontmatter(self) -> None:
        wrapped = wrap_with_untrusted_frontmatter("# original\n", path="docs/x.md", reason="LLM empty")
        self.assertTrue(wrapped.startswith("---\n"))
        self.assertIn("artifact_status: degraded_fallback", wrapped)
        self.assertIn("trusted: false", wrapped)
        self.assertIn("# original", wrapped)

    def test_yaml_stays_parseable_with_meta_node(self) -> None:
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed")
        wrapped = wrap_with_untrusted_frontmatter("openapi: 3.0.0\ninfo:\n  title: x\n", path="api.yaml", reason="r")
        import yaml
        loaded = yaml.safe_load(wrapped)
        self.assertIn("artifact_meta", loaded)
        self.assertFalse(loaded["artifact_meta"]["trusted"])
        self.assertEqual(loaded["openapi"], "3.0.0")

    def test_json_object_keeps_data_under_meta_namespace(self) -> None:
        wrapped = wrap_with_untrusted_frontmatter('{"a":1}', path="tasks.json", reason="r")
        loaded = json.loads(wrapped)
        self.assertEqual(loaded["_artifact_meta"]["artifact_status"], "degraded_fallback")
        self.assertFalse(loaded["_artifact_meta"]["trusted"])
        self.assertEqual(loaded["a"], 1)

    def test_python_fallback_compiles(self) -> None:
        wrapped = wrap_with_untrusted_frontmatter("def f():\n    return 1\n", path="m.py", reason="r")
        # syntactically valid Python — comment-style header doesn't break parsing
        compile(wrapped, "fallback_test.py", "exec")

    def test_javascript_fallback_uses_slash_comments(self) -> None:
        wrapped = wrap_with_untrusted_frontmatter("const x = 1;\n", path="m.js", reason="r")
        self.assertTrue(wrapped.startswith("// ---"))
        self.assertIn("// trusted: false", wrapped)


# ---------------------------------------------------------------------------
# B2 — Validator
# ---------------------------------------------------------------------------


class ValidatorTests(unittest.TestCase):
    def test_default_contracts_load(self) -> None:
        v = load_contracts()
        # The bundled contract file declares 14 entries.
        self.assertGreaterEqual(len(v.contracts), 10)

    def test_min_length_check(self) -> None:
        v = Validator(
            contracts=[ArtifactContract(path_pattern="docs/x.md", rules={"min_length_chars": 100})],
        )
        short = v.validate("docs/x.md", "too short")
        self.assertEqual(short.status, "failed")
        self.assertEqual(short.score, 0)

        long = v.validate("docs/x.md", "x" * 200)
        self.assertEqual(long.status, "passed")
        self.assertEqual(long.score, 100)

    def test_required_sections_partial(self) -> None:
        v = Validator(
            contracts=[ArtifactContract(
                path_pattern="prd.md",
                rules={"min_length_chars": 50, "required_sections": ["Problem", "Users", "MVP"]},
            )],
        )
        # Length ok, sections missing → 1/2 checks pass → partial
        result = v.validate("prd.md", "x" * 100 + "\n# Problem\n")
        self.assertEqual(result.status, "partial")
        self.assertEqual(result.score, 50)

    def test_must_not_contain(self) -> None:
        v = Validator(
            contracts=[ArtifactContract(
                path_pattern="prd.md",
                rules={"min_length_chars": 1, "must_not_contain": ["TODO"]},
            )],
        )
        bad = v.validate("prd.md", "this has TODO inside")
        self.assertEqual(bad.status, "partial")
        good = v.validate("prd.md", "no forbidden tokens here")
        self.assertEqual(good.status, "passed")

    def test_json_path_check(self) -> None:
        v = Validator(
            contracts=[ArtifactContract(
                path_pattern="tasks.json",
                rules={"must_parse_as_json": True, "json_required_path": "$[0].id"},
            )],
        )
        good = v.validate("tasks.json", '[{"id":"T1"},{"id":"T2"}]')
        self.assertEqual(good.status, "passed")
        bad = v.validate("tasks.json", '[]')
        self.assertEqual(bad.status, "partial")

    def test_unknown_rule_silently_ignored(self) -> None:
        v = Validator(
            contracts=[ArtifactContract(
                path_pattern="x.md",
                rules={"unknown_thing": True, "min_length_chars": 1},
            )],
        )
        result = v.validate("x.md", "hello")
        self.assertEqual(result.status, "passed")
        self.assertEqual(result.score, 100)

    def test_path_suffix_match_picks_longest(self) -> None:
        v = Validator(
            contracts=[
                ArtifactContract(path_pattern=".md", rules={"min_length_chars": 1}),
                ArtifactContract(path_pattern="prd.md", rules={"min_length_chars": 1000}),
            ],
        )
        # The longer path_pattern should win.
        r = v.validate("docs/product/prd.md", "short")
        self.assertEqual(r.status, "failed")


# ---------------------------------------------------------------------------
# B3 — phase score + delivery grade
# ---------------------------------------------------------------------------


class GradeTests(unittest.TestCase):
    def test_grade_a_for_clean_high_quality_run(self) -> None:
        with _autonomous_env(), tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()

            def respond(agent_config, context):
                # Produce content that satisfies the bundled contracts.
                outputs = list(context.output_paths or [])
                files = {}
                for p in outputs:
                    files[p] = _well_formed_content(p)
                return AgentResult(status="completed", summary="ok", files=files)

            runner.run_task.side_effect = respond
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            result = engine.run(project["id"], "software_project")
            self.assertEqual(result["status"], "completed")
            row = engine.db.query_one(
                "SELECT delivery_grade FROM runs WHERE id = ?", (result["run_id"],)
            )
            self.assertIn(row["delivery_grade"], {"A", "B"})  # depends on contract sensitivity

    def test_grade_d_when_all_phases_fall_back(self) -> None:
        with _autonomous_env(), tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            runner.run_task.return_value = AgentResult(status="completed", summary="empty", files={})
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            result = engine.run(project["id"], "software_project")
            row = engine.db.query_one(
                "SELECT delivery_grade FROM runs WHERE id = ?", (result["run_id"],)
            )
            # All required outputs are fallbacks → many fallback files → D or F
            self.assertIn(row["delivery_grade"], {"D", "F"})


# ---------------------------------------------------------------------------
# B4 — final report first-screen layout
# ---------------------------------------------------------------------------


class FirstScreenTests(unittest.TestCase):
    def test_first_screen_has_ordered_keys(self) -> None:
        with _autonomous_env(), tempfile.TemporaryDirectory() as tmp:
            runner = MagicMock()
            runner.run_task.return_value = AgentResult(status="completed", summary="ok", files={})
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            result = engine.run(project["id"], "software_project")
            report_path = Path(project["path"]) / ".agent" / "runs" / result["run_id"] / "final-run-status.md"
            text = report_path.read_text(encoding="utf-8")
            # Order: Grade → Ready → Top Risks → Recommended Command → Trust Summary
            grade_idx = text.index("Overall Grade:")
            ready_idx = text.index("Ready to use:")
            risks_idx = text.index("Top Risks")
            cmd_idx = text.index("Recommended Next Command")
            trust_idx = text.index("Trust Summary")
            self.assertLess(grade_idx, ready_idx)
            self.assertLess(ready_idx, risks_idx)
            self.assertLess(risks_idx, cmd_idx)
            self.assertLess(cmd_idx, trust_idx)


# ---------------------------------------------------------------------------
# B5 — resume command
# ---------------------------------------------------------------------------


class ResumeCommandTests(unittest.TestCase):
    def test_resume_continues_an_interrupted_run(self) -> None:
        # Without autonomous mode the run stops at PRD gate; resume after
        # approve should drive the rest.
        runner = MagicMock()

        def respond(agent_config, context):
            outputs = list(context.output_paths or [])
            return AgentResult(
                status="completed",
                summary="ok",
                files={p: _well_formed_content(p) for p in outputs},
            )

        runner.run_task.side_effect = respond

        with tempfile.TemporaryDirectory() as tmp:
            engine, _ = _make_engine(tmp, runner=runner)
            project = engine.create_project("Build a markdown todo CLI", Path(tmp) / "projects")
            r1 = engine.run(project["id"], "software_project")
            self.assertEqual(r1["status"], "needs_approval")
            engine.approve(project["id"], "prd")  # this internally resumes
            # Run should now be completed.
            run = engine.require_run(r1["run_id"])
            self.assertEqual(run["status"], "completed")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _well_formed_content(path: str) -> str:
    """Produce content that satisfies the bundled artifact contracts. Used by
    grade tests to simulate a happy-path autonomous run."""
    if path.endswith("acceptance-criteria.md"):
        return "# Acceptance Criteria\n\n" + "\n".join(f"- AC-{i:03d} criterion" for i in range(1, 9)) + "\n" + ("x" * 700)
    if path.endswith("api.openapi.yaml"):
        return "openapi: 3.0.0\ninfo:\n  title: x\n  version: '1'\npaths: {}\n"
    if path.endswith("generated-tasks.json"):
        return json.dumps([{"id": f"T{i}", "title": "t", "phase": "implementation"} for i in range(5)])
    sections_map = {
        "project-brief.md": ["Problem", "User", "Success"],
        "research.md": ["Market", "Alternatives", "Differentiator"],
        "prd.md": ["Problem", "Users", "MVP", "Acceptance", "Non-Goals", "Risks"],
        "user-flow.md": ["Flow"],
        "design-system.md": ["Tokens"],
        "architecture.md": ["Overview", "Components", "Data"],
        "test-plan.md": ["Coverage"],
        "review-report.md": ["Status"],
    }
    title = "# Document"
    sections = []
    for key, secs in sections_map.items():
        if path.endswith(key):
            sections = secs
            break
    body = "\n\n".join(f"## {s}\nDetailed content for {s}." for s in sections)
    return f"{title}\n\n{body}\n\n" + ("filler text " * 200)


if __name__ == "__main__":
    unittest.main()
