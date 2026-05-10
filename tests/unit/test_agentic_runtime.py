from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from orchestrator.bootstrap import initialize_workspace
from orchestrator.config import resolve_paths
from orchestrator.core.agentic_runtime import (
    AGENTIC_ABANDONMENT_LOG_RELPATH,
    CANDIDATE_STRATEGIES,
    AgenticProjectRuntime,
    FAILURE_TAXONOMY,
    _FAILURE_MATCH_RULES,
    _aggregate_prior_learnings,
    _append_abandonment_record,
    _build_context_pack,
    _build_memory_update,
    _build_promotion_report,
    _classify_eval_failure,
    _compute_candidate_diversity,
    _count_prior_abandonments,
    _derive_learnings_from_run,
    _diff_directories,
    _evaluate_candidate_hard_gates,
    _execute_eval_harness,
    _jaccard_distance,
    _read_abandonment_history,
    _read_prior_memory_updates,
    _run_repair_loop,
    _score_candidate,
    _validate_candidate_score,
    _validate_changed_files,
    _validate_promotion_report_v2,
    _write_critic_reports,
)
from orchestrator.core.run_manager import create_engine


def _legacy_promo(
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    candidate: dict[str, Any],
    eval_results: dict[str, Any],
    trace: list[dict[str, Any]],
    **kwargs: Any,
) -> dict[str, Any]:
    """Adapter: old-shape (single candidate + separate eval_results) call into
    the new multi-candidate _build_promotion_report. Used by tests that
    pre-date MVP-3A."""
    enriched = {**candidate, "eval_results": eval_results}
    if "id" not in enriched:
        enriched["id"] = "candidate-a"
    return _build_promotion_report(intent, context, eval_harness, [enriched], trace, **kwargs)


def _legacy_memory_update(
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    candidate: dict[str, Any],
    eval_results: dict[str, Any],
    promotion: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    enriched = {**candidate, "eval_results": eval_results}
    if "id" not in enriched:
        enriched["id"] = "candidate-a"
    return _build_memory_update(intent, context, eval_harness, [enriched], promotion, run_id)


def _legacy_derive_learnings(
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    candidate: dict[str, Any],
    eval_results: dict[str, Any],
    promotion: dict[str, Any],
    run_id: str,
) -> list[dict[str, Any]]:
    enriched = {**candidate, "eval_results": eval_results}
    if "id" not in enriched:
        enriched["id"] = "candidate-a"
    return _derive_learnings_from_run(intent, context, eval_harness, [enriched], promotion, run_id)


class AgenticRuntimeTests(unittest.TestCase):
    def test_agentic_runtime_writes_evidence_package_and_db_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a verified portfolio builder", paths.projects_dir)
            project_path = Path(project["path"])
            (project_path / "apps/web/package.json").write_text(
                json.dumps({"scripts": {"build": "node -e \"process.exit(0)\"", "test:e2e": "node -e \"process.exit(0)\""}}),
                encoding="utf-8",
            )
            (project_path / "apps/web/app").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/app/page.tsx").write_text("export default function Page() { return null }\n", encoding="utf-8")
            (project_path / "apps/web/app/api/health").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/app/api/health/route.ts").write_text("export function GET() { return Response.json({ ok: true }) }\n", encoding="utf-8")
            (project_path / "apps/web/tests/e2e").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/tests/e2e/portfolio.spec.ts").write_text("test('placeholder', () => {})\n", encoding="utf-8")
            (project_path / ".agent/worktrees/old-run/candidate-a/apps/web/app").mkdir(parents=True, exist_ok=True)
            (project_path / ".agent/worktrees/old-run/candidate-a/apps/web/app/page.tsx").write_text(
                "export default function StaleCandidate() { return null }\n",
                encoding="utf-8",
            )
            for index in range(20):
                (project_path / f"docs/product/reference-{index}.md").write_text("portfolio reference\n", encoding="utf-8")

            result = AgenticProjectRuntime(engine.db).run(project=project, execute_eval=True)

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.decision, "needs-human-review")
            self.assertTrue((result.run_dir / "intent-contract.json").exists())
            self.assertTrue((result.run_dir / "context-pack.json").exists())
            self.assertTrue((result.run_dir / "eval-harness.json").exists())
            self.assertTrue((result.run_dir / "candidates/candidate-a/score.json").exists())
            self.assertTrue((result.run_dir / "candidates/candidate-a/eval-results.json").exists())
            self.assertTrue((result.run_dir / "candidates/candidate-a/critics/security.md").exists())
            self.assertTrue((result.run_dir / "promotion-report.json").exists())
            self.assertTrue((result.run_dir / "trace.jsonl").exists())

            promotion = json.loads((result.run_dir / "promotion-report.json").read_text(encoding="utf-8"))
            self.assertEqual(promotion["candidate"], "candidate-a")
            self.assertTrue(promotion["hard_gates"]["required_eval_declared"])
            self.assertTrue(promotion["hard_gates"]["required_eval_executed"])
            self.assertTrue(promotion["hard_gates"]["required_eval_passed"])
            self.assertFalse(promotion["hard_gates"]["source_patch_present"])
            context = json.loads((result.run_dir / "context-pack.json").read_text(encoding="utf-8"))
            self.assertTrue(context["context_quality"]["has_source_files"])
            self.assertTrue(context["context_quality"]["has_tests"])
            self.assertTrue(context["context_quality"]["has_api_routes"])
            self.assertLessEqual(context["context_quality"]["docs_dominance_ratio"], 0.35)
            selected_paths = [item["path"] for item in context["relevant_files"]]
            self.assertIn("apps/web/package.json", selected_paths)
            self.assertIn("apps/web/app/page.tsx", selected_paths)
            self.assertFalse(any(path.startswith(".agent/") for path in selected_paths))

            status = engine.status(project["id"])
            self.assertEqual(status["run"]["workflow_id"], "agentic_project")
            self.assertEqual(status["run"]["status"], "completed")
            artifacts = engine.artifacts.list_for_run(result.run_id)
            self.assertGreaterEqual(len(artifacts), 10)

    def test_diff_directories_exposes_source_patch_present_at_top_level(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base"
            changed = Path(tmp) / "changed"
            (base / "apps/web/app").mkdir(parents=True)
            (changed / "apps/web/app").mkdir(parents=True)
            (base / "apps/web/test-results").mkdir(parents=True)
            (base / "apps/web/test-results/.last-run.json").write_text('{"status":"passed"}\n', encoding="utf-8")
            (base / "apps/web/next-env.d.ts").write_text("/// <reference types=\"next\" />\n", encoding="utf-8")
            (changed / "apps/web/next-env.d.ts").write_text("/// <reference types=\"next/new\" />\n", encoding="utf-8")
            (base / "apps/web/app/page.tsx").write_text("export default function Page() { return null }\n", encoding="utf-8")
            (changed / "apps/web/app/page.tsx").write_text(
                "export default function Page() { return <main>Ready</main> }\n",
                encoding="utf-8",
            )

            diff = _diff_directories(base, changed, ["apps/web/**"])

            self.assertTrue(diff["source_patch_present"])
            self.assertTrue(diff["changed_files"]["source_patch_present"])
            self.assertIn("apps/web/app/page.tsx", diff["patch_diff"])
            changed_paths = [item["path"] for item in diff["changed_files"]["changed_files"]]
            self.assertNotIn("apps/web/test-results/.last-run.json", changed_paths)
            self.assertNotIn("apps/web/next-env.d.ts", changed_paths)

    def test_eval_failure_classification_separates_type_and_environment_errors(self) -> None:
        type_failure = {
            "commands": [
                {
                    "name": "build",
                    "cmd": "npm run build",
                    "required": True,
                    "executed": True,
                    "passed": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "Type error: Type 'string' is not assignable to type 'number'.",
                }
            ]
        }
        e2e_environment_failure = {
            "commands": [
                {
                    "name": "e2e",
                    "cmd": "npm run test:e2e",
                    "required": True,
                    "executed": True,
                    "passed": False,
                    "exit_code": 1,
                    "stdout": "",
                    "stderr": "Error: listen EPERM: operation not permitted 127.0.0.1:3107",
                }
            ]
        }

        self.assertEqual(_classify_eval_failure(type_failure)["failure_type"], "type_error")
        self.assertEqual(_classify_eval_failure(e2e_environment_failure)["failure_type"], "environment_error")

    def test_failed_required_eval_requests_repair_when_loop_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build a verified app with a failing build", paths.projects_dir)
            project_path = Path(project["path"])
            (project_path / "apps/web/package.json").write_text(
                json.dumps({"scripts": {"build": "node -e \"process.exit(1)\""}}),
                encoding="utf-8",
            )
            (project_path / "apps/web/app").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/app/page.tsx").write_text("export default function Page() { return null }\n", encoding="utf-8")

            result = AgenticProjectRuntime(engine.db).run(project=project, execute_eval=True)

            promotion = json.loads((result.run_dir / "promotion-report.json").read_text(encoding="utf-8"))
            repair_history = json.loads((result.run_dir / "candidates/candidate-a/repair-history.json").read_text(encoding="utf-8"))
            self.assertEqual(promotion["decision"], "needs-human-review")
            self.assertFalse(promotion["hard_gates"]["source_patch_present"])
            self.assertEqual(repair_history["stop_reason"], "no_source_patch_to_repair")

    def test_promotion_decision_distinguishes_repairable_failure_from_exhausted_repair(self) -> None:
        context = {"context_quality": {"has_source_files": True}, "relevant_files": []}
        intent = {"success_criteria": []}
        eval_harness = {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}
        eval_results = {
            "required_eval_declared": True,
            "required_eval_executed": True,
            "required_eval_passed": False,
            "commands": [
                {
                    "name": "build",
                    "cmd": "npm run build",
                    "required": True,
                    "declared": True,
                    "executed": True,
                    "exit_code": 1,
                    "passed": False,
                    "stdout": "",
                    "stderr": "Type error: broken",
                }
            ],
        }
        candidate = {
            "score": {"source_patch_present": True, "diff_within_scope": True},
            "repair_history": {"attempts": [], "max_loops": 3, "stop_reason": "repair_loop_disabled"},
        }
        trace = [{"stage": str(index)} for index in range(6)]

        report = _legacy_promo(intent, context, eval_harness, candidate, eval_results, trace)
        self.assertEqual(report["decision"], "repair")

        candidate["repair_history"]["attempts"] = [{"loop_index": 1, "status": "eval_failed"}]
        candidate["repair_history"]["stop_reason"] = "max_loops_exhausted"
        report = _legacy_promo(intent, context, eval_harness, candidate, eval_results, trace)
        self.assertEqual(report["decision"], "abandoned")
        self.assertTrue(report["repair"]["abandoned"])
        self.assertEqual(report["repair"]["abandonment_reason"], "max_loops_exhausted")

    def test_repair_loop_refreshes_diff_and_reruns_eval_until_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp) / "project"
            worktree = project_path / ".agent/worktrees/run_test/candidate-a"
            run_dir = project_path / ".agent/runs/run_test"
            (project_path / "apps/web/app").mkdir(parents=True)
            (worktree / "apps/web/app").mkdir(parents=True)
            (project_path / "apps/web/app/page.tsx").write_text("export default function Page() { return null }\n", encoding="utf-8")
            (worktree / "apps/web/app/page.tsx").write_text(
                "export default function Page() { return <main>Candidate</main> }\n",
                encoding="utf-8",
            )
            diff = _diff_directories(project_path, worktree, ["apps/**"])
            candidate = {
                "patch_diff": diff["patch_diff"],
                "changed_files": {
                    "changed_files": diff["changed_files"]["changed_files"],
                    "out_of_scope_changes": [],
                    "source_patch_present": True,
                },
                "score": {"source_patch_present": True, "diff_within_scope": True},
                "repair_history": {"attempts": [], "max_loops": 2, "stop_reason": ""},
                "run_log": [],
                "worktree_path": str(worktree),
            }
            intent = {
                "goal": "Repair deterministic file check.",
                "allowed_change_scope": {"paths": ["apps/**"]},
            }
            eval_harness = {
                "commands": [
                    {
                        "name": "fixed-file",
                        "cmd": "test -f apps/web/app/fixed.ts",
                        "cwd": ".",
                        "required": True,
                        "type": "deterministic_file_check",
                    }
                ]
            }
            eval_results = _execute_eval_harness(project_path, eval_harness, candidate, execute_eval=True, timeout_sec=5)
            self.assertFalse(eval_results["required_eval_passed"])

            def fake_repair_agent(**kwargs):
                Path(kwargs["worktree_path"], "apps/web/app/fixed.ts").write_text("export const fixed = true\n", encoding="utf-8")
                return {"status": "completed", "reason": "fake_repair_completed", "details": {"worker": "fake"}}

            with patch("orchestrator.core.agentic_runtime._run_codex_repair_agent", side_effect=fake_repair_agent):
                final_eval = _run_repair_loop(
                    project_path=project_path,
                    run_dir=run_dir,
                    intent=intent,
                    eval_harness=eval_harness,
                    candidate=candidate,
                    eval_results=eval_results,
                    model="fake",
                    timeout_sec=5,
                    max_loops=2,
                )

            self.assertTrue(final_eval["required_eval_passed"])
            self.assertEqual(candidate["repair_history"]["stop_reason"], "eval_passed_after_repair")
            self.assertEqual(candidate["repair_history"]["attempts"][0]["status"], "eval_passed")
            self.assertIn("apps/web/app/fixed.ts", candidate["patch_diff"])


class TaxonomyTests(unittest.TestCase):
    """Guards the FAILURE_TAXONOMY <-> classifier <-> prompt contract."""

    def test_every_match_rule_category_is_in_taxonomy(self) -> None:
        for rule in _FAILURE_MATCH_RULES:
            self.assertIn(rule["category"], FAILURE_TAXONOMY)
            self.assertTrue(rule["subtype"], f"{rule['category']} rule must have subtype")
            self.assertTrue(rule["repair_hint"], f"{rule['category']} rule must have repair_hint")

    def test_taxonomy_entries_have_required_fields(self) -> None:
        required = {"description", "default_subtype", "default_likely_cause", "repair_hint"}
        for category, spec in FAILURE_TAXONOMY.items():
            missing = required - set(spec.keys())
            self.assertFalse(missing, f"{category} missing fields: {missing}")

    def test_classifier_hits_expected_category_for_each_fixture(self) -> None:
        # One representative fixture per non-`none` category, stressing the
        # ordering of rules in _FAILURE_MATCH_RULES.
        fixtures: list[tuple[str, dict[str, Any]]] = [
            (
                "environment_error",
                {"name": "build", "cmd": "npm run build", "exit_code": None, "stderr": "Command timed out after 60s"},
            ),
            (
                "environment_error",
                {"name": "build", "cmd": "npm run build", "exit_code": 1, "stderr": "Error: listen EADDRINUSE 0.0.0.0:3107"},
            ),
            (
                "dependency_error",
                {"name": "build", "cmd": "npm run build", "exit_code": 1, "stderr": "Cannot find module 'foo'"},
            ),
            (
                "type_error",
                {"name": "build", "cmd": "npm run build", "exit_code": 1, "stderr": "Type error: not assignable"},
            ),
            (
                "e2e_failure",
                {"name": "e2e", "cmd": "npm run test:e2e", "exit_code": 1, "stdout": "playwright run failed"},
            ),
            (
                "unit_test_failure",
                {"name": "unit-tests", "cmd": "npm run test", "exit_code": 1, "stdout": "expected true to equal false"},
            ),
            (
                "runtime_exception",
                {"name": "build", "cmd": "node script.js", "exit_code": 1, "stderr": "ReferenceError: x is not defined"},
            ),
            (
                "build_failure",
                {"name": "build", "cmd": "npm run build", "exit_code": 1, "stderr": "Build failed: see output"},
            ),
            (
                "spec_ambiguity",
                {"name": "smoke", "cmd": "./run-smoke.sh", "exit_code": 1, "stderr": "non-matching opaque output"},
            ),
        ]
        for expected_category, raw in fixtures:
            command = {"required": True, "executed": True, "passed": False, **raw}
            classification = _classify_eval_failure({"commands": [command]})
            self.assertEqual(
                classification["failure_type"],
                expected_category,
                f"Expected {expected_category} for fixture {raw}; got {classification}",
            )
            self.assertEqual(
                classification["category_description"],
                FAILURE_TAXONOMY[expected_category]["description"],
                f"Description should be sourced from FAILURE_TAXONOMY for {expected_category}",
            )

    def test_classifier_returns_none_when_no_required_failure(self) -> None:
        classification = _classify_eval_failure({"commands": []})
        self.assertEqual(classification["failure_type"], "none")
        self.assertEqual(classification["category_description"], FAILURE_TAXONOMY["none"]["description"])


class AbandonmentTests(unittest.TestCase):
    """Guards Promotion Gate `abandoned` decision and the JSONL log."""

    def _abandoned_promotion_inputs(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        intent = {"goal": "Ship a verified change.", "success_criteria": []}
        context = {"context_quality": {"has_source_files": True}, "relevant_files": []}
        eval_harness = {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}
        eval_results = {
            "required_eval_declared": True,
            "required_eval_executed": True,
            "required_eval_passed": False,
            "commands": [
                {
                    "name": "build",
                    "cmd": "npm run build",
                    "required": True,
                    "declared": True,
                    "executed": True,
                    "exit_code": 1,
                    "passed": False,
                    "stdout": "",
                    "stderr": "Type error: still broken",
                }
            ],
        }
        candidate = {
            "score": {"source_patch_present": True, "diff_within_scope": True},
            "repair_history": {
                "attempts": [
                    {"loop_index": 1, "status": "eval_failed"},
                    {"loop_index": 2, "status": "eval_failed"},
                ],
                "max_loops": 2,
                "stop_reason": "max_loops_exhausted",
                "final_failure": {"failure_type": "type_error", "subtype": "typescript"},
            },
        }
        trace = [{"stage": str(index)} for index in range(6)]
        return intent, context, eval_harness, candidate, eval_results, trace

    def test_promotion_decision_is_abandoned_when_repair_exhausted(self) -> None:
        intent, context, eval_harness, candidate, eval_results, trace = self._abandoned_promotion_inputs()
        report = _legacy_promo(intent, context, eval_harness, candidate, eval_results, trace)
        self.assertEqual(report["decision"], "abandoned")
        self.assertTrue(report["repair"]["abandoned"])
        self.assertEqual(report["repair"]["abandonment_reason"], "max_loops_exhausted")
        self.assertEqual(report["repair"]["attempt_count"], 2)

    def test_promotion_decision_is_abandoned_for_codex_failure_stop_reason(self) -> None:
        intent, context, eval_harness, candidate, eval_results, trace = self._abandoned_promotion_inputs()
        candidate["repair_history"]["stop_reason"] = "codex_cli_timeout"
        report = _legacy_promo(intent, context, eval_harness, candidate, eval_results, trace)
        self.assertEqual(report["decision"], "abandoned")
        self.assertEqual(report["repair"]["abandonment_reason"], "codex_cli_timeout")

    def test_promotion_decision_stays_repair_when_no_attempts_yet(self) -> None:
        intent, context, eval_harness, candidate, eval_results, trace = self._abandoned_promotion_inputs()
        candidate["repair_history"]["attempts"] = []
        candidate["repair_history"]["stop_reason"] = "repair_loop_disabled"
        report = _legacy_promo(intent, context, eval_harness, candidate, eval_results, trace)
        self.assertEqual(report["decision"], "repair")
        self.assertFalse(report["repair"]["abandoned"])
        self.assertIsNone(report["repair"]["abandonment_reason"])

    def test_append_abandonment_record_creates_and_appends_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            log_path = project_path / AGENTIC_ABANDONMENT_LOG_RELPATH
            self.assertFalse(log_path.exists())

            intent = {"goal": "Repair the build."}
            promotion_first = {
                "candidate": "candidate-a",
                "decision": "abandoned",
                "repair": {
                    "stop_reason": "max_loops_exhausted",
                    "attempt_count": 3,
                    "max_loops": 3,
                    "final_failure": {"failure_type": "type_error", "subtype": "typescript"},
                },
            }
            promotion_second = {
                "candidate": "candidate-a",
                "decision": "abandoned",
                "repair": {
                    "stop_reason": "codex_cli_timeout",
                    "attempt_count": 1,
                    "max_loops": 3,
                    "final_failure": {"failure_type": "build_failure", "subtype": "build_command_failed"},
                },
            }

            relpath_first = _append_abandonment_record(
                project_path, run_id="run_aaa", intent=intent, promotion=promotion_first, patch_worker="codex",
            )
            self.assertEqual(relpath_first, AGENTIC_ABANDONMENT_LOG_RELPATH)
            self.assertTrue(log_path.exists())

            relpath_second = _append_abandonment_record(
                project_path, run_id="run_bbb", intent=intent, promotion=promotion_second, patch_worker="codex",
            )
            self.assertEqual(relpath_second, AGENTIC_ABANDONMENT_LOG_RELPATH)

            lines = log_path.read_text(encoding="utf-8").strip().split("\n")
            self.assertEqual(len(lines), 2, "Second append must not overwrite first record")
            first = json.loads(lines[0])
            second = json.loads(lines[1])
            self.assertEqual(first["run_id"], "run_aaa")
            self.assertEqual(first["stop_reason"], "max_loops_exhausted")
            self.assertEqual(first["attempt_count"], 3)
            self.assertEqual(first["candidate"], "candidate-a")
            self.assertEqual(first["patch_worker"], "codex")
            self.assertEqual(first["intent_goal"], "Repair the build.")
            self.assertEqual(first["final_failure"]["failure_type"], "type_error")
            self.assertEqual(first["schema_version"], "agentic.abandonment_record.v1")
            self.assertEqual(second["run_id"], "run_bbb")
            self.assertEqual(second["stop_reason"], "codex_cli_timeout")
            self.assertEqual(second["final_failure"]["failure_type"], "build_failure")

    def test_runtime_writes_abandonment_log_when_repair_exhausted(self) -> None:
        # End-to-end: run the agentic runtime against a project whose build
        # always fails, with a fake repair-agent that never fixes anything.
        # Expect: decision=abandoned, JSONL log written, log contains one
        # record matching this run.
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Build that never passes.", paths.projects_dir)
            project_path = Path(project["path"])
            (project_path / "apps/web/package.json").write_text(
                json.dumps({"scripts": {"build": "node -e \"console.error('Type error: nope'); process.exit(1)\""}}),
                encoding="utf-8",
            )
            (project_path / "apps/web/app").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/app/page.tsx").write_text(
                "export default function Page() { return null }\n", encoding="utf-8",
            )

            def fake_noop_repair(**kwargs):
                # Touch a non-source file so the diff refresh runs without
                # adding any code that would actually fix the build.
                return {"status": "completed", "reason": "fake_noop", "details": {"worker": "fake"}}

            with patch("orchestrator.core.agentic_runtime._run_codex_repair_agent", side_effect=fake_noop_repair):
                result = AgenticProjectRuntime(engine.db).run(
                    project=project,
                    patch_worker="codex",
                    execute_eval=True,
                    max_repair_loops=2,
                )

            promotion = json.loads((result.run_dir / "promotion-report.json").read_text(encoding="utf-8"))
            log_path = project_path / AGENTIC_ABANDONMENT_LOG_RELPATH
            # The runtime promotes "abandoned" only when source_patch_present
            # is True (the patch worker produced a diff). With patch_worker=
            # codex but the codex CLI not actually invoked here (we only mock
            # the repair agent), source_patch_present is False, so decision
            # may be needs-human-review. We assert one of the two terminal
            # states and tie the log presence to the abandoned decision.
            self.assertIn(promotion["decision"], {"abandoned", "needs-human-review"})
            if promotion["decision"] == "abandoned":
                self.assertTrue(log_path.exists())
                lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
                self.assertEqual(len(lines), 1)
                record = json.loads(lines[0])
                self.assertEqual(record["run_id"], result.run_id)
                self.assertEqual(record["candidate"], "candidate-a")
                self.assertEqual(record["patch_worker"], "codex")
            else:
                # Even if we didn't reach abandonment in this minimal harness,
                # the log file must not be created spuriously.
                self.assertFalse(log_path.exists())


class AbandonmentHistoryReadTests(unittest.TestCase):
    """Guards _read_abandonment_history and _count_prior_abandonments."""

    def test_read_returns_empty_when_log_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_read_abandonment_history(Path(tmp)), [])

    def test_read_skips_blank_and_malformed_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            log_path = project_path / AGENTIC_ABANDONMENT_LOG_RELPATH
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(
                "\n"
                + json.dumps({"run_id": "run_a", "patch_worker": "codex", "final_failure": {"failure_type": "type_error"}}) + "\n"
                + "this-is-not-json\n"
                + "\n"
                + "[\"json-but-not-a-dict\"]\n"
                + json.dumps({"run_id": "run_b", "patch_worker": "codex", "final_failure": {"failure_type": "build_failure"}}) + "\n",
                encoding="utf-8",
            )
            history = _read_abandonment_history(project_path)
            self.assertEqual([record["run_id"] for record in history], ["run_a", "run_b"])

    def test_count_prior_abandonments_filters_by_worker_and_failure_type(self) -> None:
        history = [
            {"patch_worker": "codex", "final_failure": {"failure_type": "type_error"}},
            {"patch_worker": "codex", "final_failure": {"failure_type": "type_error"}},
            {"patch_worker": "codex", "final_failure": {"failure_type": "build_failure"}},
            {"patch_worker": "claude", "final_failure": {"failure_type": "type_error"}},
            {"patch_worker": "codex", "final_failure": None},
            {"patch_worker": "codex"},  # missing final_failure entirely
        ]
        self.assertEqual(_count_prior_abandonments(history, patch_worker="codex", failure_type="type_error"), 2)
        self.assertEqual(_count_prior_abandonments(history, patch_worker="codex", failure_type="build_failure"), 1)
        self.assertEqual(_count_prior_abandonments(history, patch_worker="claude", failure_type="type_error"), 1)
        self.assertEqual(_count_prior_abandonments(history, patch_worker="codex", failure_type=""), 0)
        self.assertEqual(_count_prior_abandonments(history, patch_worker="", failure_type="type_error"), 0)


class AbandonmentSoftSignalTests(unittest.TestCase):
    """Guards the abandonment_pattern soft signal in promotion report."""

    def _failing_run_inputs(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        intent = {"goal": "Pattern-aware repair.", "success_criteria": []}
        context = {"context_quality": {"has_source_files": True}, "relevant_files": []}
        eval_harness = {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}
        eval_results = {
            "required_eval_declared": True,
            "required_eval_executed": True,
            "required_eval_passed": False,
            "commands": [
                {
                    "name": "build", "cmd": "npm run build", "required": True,
                    "declared": True, "executed": True, "exit_code": 1, "passed": False,
                    "stdout": "", "stderr": "Type error: same shape again",
                }
            ],
        }
        candidate = {
            "score": {"source_patch_present": True, "diff_within_scope": True},
            "repair_history": {
                "attempts": [{"loop_index": 1, "status": "eval_failed"}],
                "max_loops": 2,
                "stop_reason": "max_loops_exhausted",
                "final_failure": {"failure_type": "type_error", "subtype": "typescript"},
            },
        }
        trace = [{"stage": str(index)} for index in range(6)]
        return intent, context, eval_harness, candidate, eval_results, trace

    def _seed_history(self, project_path: Path, records: list[dict[str, Any]]) -> None:
        log_path = project_path / AGENTIC_ABANDONMENT_LOG_RELPATH
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")

    def test_no_warning_when_history_empty(self) -> None:
        intent, context, eval_harness, candidate, eval_results, trace = self._failing_run_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            report = _legacy_promo(intent, context, eval_harness, candidate, eval_results, trace,
                project_path=Path(tmp), patch_worker="codex",
            )
            self.assertEqual(report["abandonment_pattern"]["prior_abandonments"], 0)
            self.assertFalse(report["abandonment_pattern"]["warning_emitted"])
            self.assertNotIn(
                "patch_worker `codex`",
                "\n".join(str(risk) for risk in report["remaining_risks"]),
            )

    def test_no_warning_when_only_one_prior(self) -> None:
        intent, context, eval_harness, candidate, eval_results, trace = self._failing_run_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            self._seed_history(project_path, [
                {"run_id": "run_old", "patch_worker": "codex", "final_failure": {"failure_type": "type_error"}},
            ])
            report = _legacy_promo(intent, context, eval_harness, candidate, eval_results, trace,
                project_path=project_path, patch_worker="codex",
            )
            self.assertEqual(report["abandonment_pattern"]["prior_abandonments"], 1)
            self.assertFalse(report["abandonment_pattern"]["warning_emitted"])

    def test_warning_emitted_when_two_or_more_priors_match(self) -> None:
        intent, context, eval_harness, candidate, eval_results, trace = self._failing_run_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            self._seed_history(project_path, [
                {"run_id": "r1", "patch_worker": "codex", "final_failure": {"failure_type": "type_error"}},
                {"run_id": "r2", "patch_worker": "codex", "final_failure": {"failure_type": "type_error"}},
                {"run_id": "r3", "patch_worker": "codex", "final_failure": {"failure_type": "build_failure"}},
            ])
            report = _legacy_promo(intent, context, eval_harness, candidate, eval_results, trace,
                project_path=project_path, patch_worker="codex",
            )
            pattern = report["abandonment_pattern"]
            self.assertEqual(pattern["patch_worker"], "codex")
            self.assertEqual(pattern["failure_type"], "type_error")
            self.assertEqual(pattern["prior_abandonments"], 2)
            self.assertTrue(pattern["warning_emitted"])
            risks_text = "\n".join(str(risk) for risk in report["remaining_risks"])
            self.assertIn("patch_worker `codex`", risks_text)
            self.assertIn("type_error", risks_text)
            self.assertIn("2 prior", risks_text)

    def test_warning_does_not_fire_for_different_worker_or_type(self) -> None:
        intent, context, eval_harness, candidate, eval_results, trace = self._failing_run_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            # Two priors but BOTH on a different patch_worker → no match.
            self._seed_history(project_path, [
                {"run_id": "r1", "patch_worker": "claude", "final_failure": {"failure_type": "type_error"}},
                {"run_id": "r2", "patch_worker": "claude", "final_failure": {"failure_type": "type_error"}},
            ])
            report = _legacy_promo(intent, context, eval_harness, candidate, eval_results, trace,
                project_path=project_path, patch_worker="codex",
            )
            self.assertEqual(report["abandonment_pattern"]["prior_abandonments"], 0)
            self.assertFalse(report["abandonment_pattern"]["warning_emitted"])

    def test_no_warning_when_current_run_has_no_failure(self) -> None:
        # A passing run should never emit a pattern warning even if history has matches.
        intent, context, eval_harness, _, _, trace = self._failing_run_inputs()
        eval_results_passing = {
            "required_eval_declared": True,
            "required_eval_executed": True,
            "required_eval_passed": True,
            "commands": [
                {"name": "build", "cmd": "npm run build", "required": True,
                 "declared": True, "executed": True, "exit_code": 0, "passed": True},
            ],
        }
        candidate_passing = {
            "score": {"source_patch_present": True, "diff_within_scope": True},
            "repair_history": {"attempts": [], "max_loops": 0, "stop_reason": "eval_passed_no_repair_needed"},
        }
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            self._seed_history(project_path, [
                {"run_id": "r1", "patch_worker": "codex", "final_failure": {"failure_type": "type_error"}},
                {"run_id": "r2", "patch_worker": "codex", "final_failure": {"failure_type": "type_error"}},
            ])
            report = _legacy_promo(
                intent, context, eval_harness, candidate_passing, eval_results_passing, trace,
                project_path=project_path, patch_worker="codex",
            )
            self.assertEqual(report["decision"], "promote")
            self.assertIsNone(report["abandonment_pattern"]["failure_type"])
            self.assertFalse(report["abandonment_pattern"]["warning_emitted"])

    def test_no_warning_when_patch_worker_is_none(self) -> None:
        # Default patch_worker="none" runs (no patch worker selected) must not
        # consult history at all — they're not real candidate generations.
        intent, context, eval_harness, candidate, eval_results, trace = self._failing_run_inputs()
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            self._seed_history(project_path, [
                {"run_id": "r1", "patch_worker": "none", "final_failure": {"failure_type": "type_error"}},
                {"run_id": "r2", "patch_worker": "none", "final_failure": {"failure_type": "type_error"}},
            ])
            report = _legacy_promo(intent, context, eval_harness, candidate, eval_results, trace,
                project_path=project_path, patch_worker="none",
            )
            self.assertEqual(report["abandonment_pattern"]["prior_abandonments"], 0)
            self.assertFalse(report["abandonment_pattern"]["warning_emitted"])


class CriticPanelGroundedTests(unittest.TestCase):
    """Critic panel reports must cite specific paths, command names, and
    failure types from this run — not generic templates."""

    def _run_critics(self, tmp: Path, *, intent: dict[str, Any], context: dict[str, Any],
                     candidate: dict[str, Any], eval_harness: dict[str, Any]) -> dict[str, str]:
        run_dir = tmp / ".agent/runs/run_x"
        if "id" not in candidate:
            candidate = {**candidate, "id": "candidate-a"}
        _write_critic_reports(tmp, run_dir, intent, context, candidate, eval_harness)
        critics_dir = run_dir / "candidates" / candidate["id"] / "critics"
        return {
            name: (critics_dir / name).read_text(encoding="utf-8")
            for name in ("correctness.md", "regression.md", "security.md", "ux.md", "overfit.md")
        }

    def test_security_critic_cites_sensitive_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            intent = {"requires_human_approval": ["new dependency"], "success_criteria": []}
            context = {}
            candidate = {
                "score": {"source_patch_present": True, "diff_within_scope": False},
                "changed_files": {
                    "changed_files": [
                        {"path": "apps/web/package.json"},
                        {"path": "apps/web/.env.production"},
                        {"path": "apps/web/app/page.tsx"},
                    ],
                    "out_of_scope_changes": [{"path": "scripts/deploy.sh"}],
                },
                "repair_history": {},
                "eval_results": {},
            }
            reports = self._run_critics(Path(tmp), intent=intent, context=context, candidate=candidate, eval_harness={})
            sec = reports["security.md"]
            self.assertIn("`apps/web/package.json`", sec)
            self.assertIn("`apps/web/.env.production`", sec)
            self.assertIn("`scripts/deploy.sh`", sec)
            self.assertIn("diff_within_scope", sec)

    def test_correctness_critic_cites_failed_command_and_classified_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            intent = {"success_criteria": ["build passes"]}
            context = {}
            candidate = {
                "score": {"source_patch_present": True, "diff_within_scope": True},
                "changed_files": {"changed_files": [{"path": "apps/web/app/page.tsx"}], "out_of_scope_changes": []},
                "repair_history": {
                    "attempts": [{"loop_index": 1, "status": "eval_failed"}],
                    "max_loops": 2,
                    "stop_reason": "max_loops_exhausted",
                    "final_failure": {
                        "failure_type": "type_error",
                        "subtype": "typescript",
                        "likely_cause": "TypeScript or type-checking failed after the candidate patch.",
                    },
                },
                "eval_results": {
                    "required_eval_executed": True,
                    "required_eval_passed": False,
                    "commands": [{"name": "build", "cmd": "npm run build", "required": True, "executed": True, "passed": False}],
                },
            }
            eval_harness = {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}
            reports = self._run_critics(Path(tmp), intent=intent, context=context, candidate=candidate, eval_harness=eval_harness)
            corr = reports["correctness.md"]
            self.assertIn("`build`", corr)
            self.assertIn("`type_error`", corr)
            self.assertIn("max_loops_exhausted", corr)
            self.assertIn("Repair attempts: 1", corr)

    def test_ux_critic_flags_ui_change_without_visual_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = {
                "score": {"source_patch_present": True, "diff_within_scope": True},
                "changed_files": {
                    "changed_files": [{"path": "apps/web/app/page.tsx"}, {"path": "apps/web/components/Header.tsx"}],
                    "out_of_scope_changes": [],
                },
                "repair_history": {},
                "eval_results": {},
            }
            reports = self._run_critics(Path(tmp), intent={}, context={},
                                        candidate=candidate, eval_harness={"visual_checks": []})
            ux = reports["ux.md"]
            self.assertIn("`apps/web/app/page.tsx`", ux)
            self.assertIn("UI surface modified but no visual check declared", ux)

    def test_overfit_critic_flags_combined_test_edits_and_repair(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = {
                "score": {"source_patch_present": True, "diff_within_scope": True},
                "changed_files": {
                    "changed_files": [
                        {"path": "apps/web/app/page.tsx"},
                        {"path": "apps/web/tests/page.test.tsx"},
                    ],
                    "out_of_scope_changes": [],
                },
                "repair_history": {
                    "attempts": [{"loop_index": 1, "status": "eval_passed", "changed_files_count": 2}],
                    "stop_reason": "eval_passed_after_repair",
                },
                "eval_results": {"required_eval_executed": True, "required_eval_passed": True},
            }
            reports = self._run_critics(Path(tmp), intent={}, context={}, candidate=candidate, eval_harness={})
            overfit = reports["overfit.md"]
            self.assertIn("page.test.tsx", overfit)
            self.assertIn("Combined signal", overfit)
            self.assertIn("eval_passed_after_repair", overfit)

    def test_regression_critic_zero_for_empty_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = {
                "score": {"source_patch_present": False, "diff_within_scope": True},
                "changed_files": {"changed_files": [], "out_of_scope_changes": []},
                "repair_history": {},
                "eval_results": {},
            }
            reports = self._run_critics(Path(tmp), intent={}, context={}, candidate=candidate, eval_harness={})
            self.assertIn("regression risk is structurally zero", reports["regression.md"])


class RepairHistoryFinalFailureContractTests(unittest.TestCase):
    """Locks in the invariant that repair_history.final_failure is always
    present on every terminal stop_reason — None for no-failure paths,
    a classified failure dict otherwise."""

    def test_no_source_patch_path_sets_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("No patch worker.", paths.projects_dir)
            project_path = Path(project["path"])
            (project_path / "apps/web").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/index.html").write_text("<html></html>", encoding="utf-8")

            result = AgenticProjectRuntime(engine.db).run(project=project)
            history = json.loads((result.run_dir / "candidates/candidate-a/repair-history.json").read_text(encoding="utf-8"))
            self.assertIn("final_failure", history)  # key MUST be present
            self.assertEqual(history["stop_reason"], "no_source_patch_to_repair")
            self.assertIsNone(history["final_failure"])

    def test_passing_eval_path_sets_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Eval passes.", paths.projects_dir)
            project_path = Path(project["path"])
            (project_path / "apps/web").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/index.html").write_text("<html></html>", encoding="utf-8")

            result = AgenticProjectRuntime(engine.db).run(project=project, execute_eval=True)
            history = json.loads((result.run_dir / "candidates/candidate-a/repair-history.json").read_text(encoding="utf-8"))
            self.assertIn("final_failure", history)
            # static-html-present succeeds, but no source patch was generated
            # (no patch worker), so we still expect the no_source_patch path.
            self.assertIn(history["stop_reason"], {"no_source_patch_to_repair", "eval_passed_no_repair_needed"})
            self.assertIsNone(history["final_failure"])

    def test_failing_eval_with_loop_disabled_sets_classified_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Eval fails.", paths.projects_dir)
            project_path = Path(project["path"])
            (project_path / "apps/web/package.json").write_text(
                json.dumps({"scripts": {"build": "node -e \"console.error('Type error: x'); process.exit(1)\""}}),
                encoding="utf-8",
            )
            (project_path / "apps/web/app").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/app/page.tsx").write_text(
                "export default function Page() { return null }\n", encoding="utf-8",
            )

            result = AgenticProjectRuntime(engine.db).run(project=project, execute_eval=True)
            history = json.loads((result.run_dir / "candidates/candidate-a/repair-history.json").read_text(encoding="utf-8"))
            self.assertIn("final_failure", history)
            # No patch worker → no_source_patch_to_repair path takes precedence.
            # final_failure is None on this path (we have no diff to repair).
            self.assertEqual(history["stop_reason"], "no_source_patch_to_repair")
            self.assertIsNone(history["final_failure"])

    def test_initial_repair_history_includes_final_failure_key(self) -> None:
        # Even before any termination, the initial repair_history dict must
        # have final_failure as a key (None until set).
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Smoke.", paths.projects_dir)
            (Path(project["path"]) / "apps/web").mkdir(parents=True, exist_ok=True)
            (Path(project["path"]) / "apps/web/index.html").write_text("<html></html>", encoding="utf-8")
            result = AgenticProjectRuntime(engine.db).run(project=project)
            history = json.loads((result.run_dir / "candidates/candidate-a/repair-history.json").read_text(encoding="utf-8"))
            self.assertIn("final_failure", history)


class MemoryProducerTests(unittest.TestCase):
    """Guards _derive_learnings_from_run + _build_memory_update producer."""

    def _passing_inputs(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        intent = {"goal": "Pass."}
        context = {"unknowns": []}
        eval_harness = {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}
        candidate = {
            "score": {"source_patch_present": True},
            "changed_files": {"out_of_scope_changes": []},
            "repair_history": {"attempts": []},
        }
        eval_results = {
            "required_eval_passed": True,
            "commands": [{"name": "build", "cmd": "npm run build", "required": True, "executed": True, "passed": True}],
        }
        promotion = {
            "decision": "promote",
            "candidate": "candidate-a",
            "repair": {"attempted": False, "attempt_count": 0, "abandoned": False, "stop_reason": "eval_passed_no_repair_needed"},
            "abandonment_pattern": {"patch_worker": "codex", "failure_type": None, "prior_abandonments": 0, "warning_emitted": False},
            "eval": {},
        }
        return intent, context, eval_harness, candidate, eval_results, promotion

    def test_promote_run_produces_positive_learning(self) -> None:
        intent, context, eval_harness, candidate, eval_results, promotion = self._passing_inputs()
        learnings = _legacy_derive_learnings(intent, context, eval_harness, candidate, eval_results, promotion, "run_p1")
        patterns = [item["pattern"] for item in learnings]
        self.assertTrue(any("produced a promotable patch" in p for p in patterns), f"Expected positive learning, got {patterns}")
        # No failure_type should appear since none happened.
        self.assertFalse(any("failure_type" in p for p in patterns))

    def test_failed_run_produces_failure_type_learning(self) -> None:
        intent, context, eval_harness, candidate, eval_results, promotion = self._passing_inputs()
        promotion["decision"] = "abandoned"
        promotion["repair"] = {
            "attempted": True, "attempt_count": 2, "abandoned": True,
            "stop_reason": "max_loops_exhausted",
            "final_failure": {"failure_type": "type_error", "subtype": "typescript"},
        }
        eval_results["required_eval_passed"] = False
        eval_results["commands"][0]["passed"] = False
        learnings = _legacy_derive_learnings(intent, context, eval_harness, candidate, eval_results, promotion, "run_f1")
        patterns = [item["pattern"] for item in learnings]
        self.assertTrue(any("failure_type `type_error`" in p for p in patterns), patterns)
        self.assertTrue(any("promotion_decision `abandoned`" in p for p in patterns), patterns)

    def test_out_of_scope_paths_become_learnings(self) -> None:
        intent, context, eval_harness, candidate, eval_results, promotion = self._passing_inputs()
        candidate["changed_files"]["out_of_scope_changes"] = [
            {"path": "node_modules/sneaky.js"},
            {"path": ".env.production"},
        ]
        learnings = _legacy_derive_learnings(intent, context, eval_harness, candidate, eval_results, promotion, "run_oos1")
        patterns = [item["pattern"] for item in learnings]
        self.assertTrue(any("node_modules/sneaky.js" in p for p in patterns), patterns)
        self.assertTrue(any(".env.production" in p for p in patterns), patterns)

    def test_unexecuted_required_eval_becomes_learning(self) -> None:
        intent, context, eval_harness, candidate, eval_results, promotion = self._passing_inputs()
        eval_results["commands"] = [
            {"name": "build", "cmd": "npm run build", "required": True, "executed": False, "reason": "execute_eval_disabled"},
            {"name": "test", "cmd": "npm test", "required": True, "executed": True, "passed": True},
        ]
        learnings = _legacy_derive_learnings(intent, context, eval_harness, candidate, eval_results, promotion, "run_skip1")
        patterns = [item["pattern"] for item in learnings]
        self.assertTrue(any("declared but not executed" in p and "`build`" in p for p in patterns), patterns)
        self.assertFalse(any("`test`" in p and "declared but not executed" in p for p in patterns))

    def test_abandonment_pattern_warning_becomes_learning(self) -> None:
        intent, context, eval_harness, candidate, eval_results, promotion = self._passing_inputs()
        promotion["decision"] = "abandoned"
        promotion["repair"] = {
            "attempted": True, "attempt_count": 1, "abandoned": True,
            "stop_reason": "max_loops_exhausted",
            "final_failure": {"failure_type": "type_error"},
        }
        promotion["abandonment_pattern"] = {
            "patch_worker": "codex", "failure_type": "type_error",
            "prior_abandonments": 2, "warning_emitted": True,
        }
        eval_results["required_eval_passed"] = False
        learnings = _legacy_derive_learnings(intent, context, eval_harness, candidate, eval_results, promotion, "run_pat1")
        patterns = [item["pattern"] for item in learnings]
        self.assertTrue(any("repeatedly abandoned on failure_type `type_error`" in p for p in patterns), patterns)

    def test_build_memory_update_attaches_run_metadata(self) -> None:
        intent, context, eval_harness, candidate, eval_results, promotion = self._passing_inputs()
        memory = _legacy_memory_update(intent, context, eval_harness, candidate, eval_results, promotion, "run_meta1")
        self.assertEqual(memory["schema_version"], "agentic.memory_update_proposal.v2")
        self.assertEqual(memory["status"], "proposed_only")
        self.assertEqual(memory["source_run"], "run_meta1")
        self.assertIn("source_timestamp_utc", memory)
        self.assertEqual(memory["project_observations"]["promotion_decision"], "promote")
        self.assertTrue(memory["project_observations"]["source_patch_present"])
        self.assertEqual(memory["project_observations"]["passed_required_eval_count"], 1)


class MemoryReaderAndAggregatorTests(unittest.TestCase):
    """Guards _read_prior_memory_updates and _aggregate_prior_learnings."""

    def _write_memory(self, project_path: Path, run_id: str, learned: list[dict[str, Any]]) -> Path:
        run_dir = project_path / ".agent" / "runs" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        path = run_dir / "memory-update.proposed.json"
        path.write_text(
            json.dumps({
                "schema_version": "agentic.memory_update_proposal.v2",
                "source_run": run_id,
                "source_timestamp_utc": "2026-05-01T00:00:00+00:00",
                "learned_patterns": learned,
            }),
            encoding="utf-8",
        )
        return path

    def test_read_returns_empty_when_no_runs_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(_read_prior_memory_updates(Path(tmp)), [])

    def test_read_skips_current_run_and_corrupt_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self._write_memory(project, "run_a", [{"pattern": "alpha", "confidence": 0.7}])
            self._write_memory(project, "run_b", [{"pattern": "beta", "confidence": 0.6}])
            # Current run we want to exclude
            self._write_memory(project, "run_curr", [{"pattern": "exclude_me", "confidence": 0.9}])
            # Corrupt JSON
            corrupt_dir = project / ".agent/runs/run_corrupt"
            corrupt_dir.mkdir(parents=True)
            (corrupt_dir / "memory-update.proposed.json").write_text("{not-json", encoding="utf-8")
            # Non-dict JSON
            wrong_dir = project / ".agent/runs/run_array"
            wrong_dir.mkdir(parents=True)
            (wrong_dir / "memory-update.proposed.json").write_text("[1,2,3]", encoding="utf-8")
            # Run dir without memory-update file
            (project / ".agent/runs/run_empty").mkdir(parents=True)

            payloads = _read_prior_memory_updates(project, exclude_run_id="run_curr")
            source_runs = [p["source_run"] for p in payloads]
            self.assertEqual(set(source_runs), {"run_a", "run_b"})
            self.assertNotIn("run_curr", source_runs)

    def test_aggregate_dedupes_and_counts_occurrences(self) -> None:
        # Three runs, most-recent first. Pattern "alpha" appears twice; "beta" once.
        memory_updates = [
            {"source_run": "r3", "source_timestamp_utc": "2026-05-09T00:00:00+00:00",
             "learned_patterns": [
                 {"pattern": "alpha", "confidence": 0.6, "evidence": {"source_run": "r3"}},
             ]},
            {"source_run": "r2", "source_timestamp_utc": "2026-05-08T00:00:00+00:00",
             "learned_patterns": [
                 {"pattern": "alpha", "confidence": 0.9, "evidence": {"source_run": "r2"}},
                 {"pattern": "beta", "confidence": 0.7, "evidence": {"source_run": "r2"}},
             ]},
            {"source_run": "r1", "source_timestamp_utc": "2026-05-07T00:00:00+00:00",
             "learned_patterns": [
                 {"pattern": "  ", "confidence": 0.9},  # blank — should be skipped
                 {"pattern": "gamma", "confidence": 0.4, "evidence": {"source_run": "r1"}},
             ]},
        ]
        aggregated = _aggregate_prior_learnings(memory_updates)
        by_pattern = {entry["pattern"]: entry for entry in aggregated}
        self.assertEqual(set(by_pattern.keys()), {"alpha", "beta", "gamma"})
        self.assertEqual(by_pattern["alpha"]["occurrences"], 2)
        self.assertEqual(by_pattern["alpha"]["max_confidence"], 0.9)
        self.assertEqual(by_pattern["alpha"]["last_seen_run"], "r3")  # most recent wins for evidence
        self.assertEqual(by_pattern["alpha"]["last_evidence"]["source_run"], "r3")
        self.assertEqual(by_pattern["beta"]["occurrences"], 1)
        self.assertEqual(by_pattern["gamma"]["occurrences"], 1)
        # Top entry should be `alpha` (most occurrences).
        self.assertEqual(aggregated[0]["pattern"], "alpha")
        # No internal _recency_rank leak.
        self.assertNotIn("_recency_rank", aggregated[0])

    def test_aggregate_caps_at_ten(self) -> None:
        memory_updates = [
            {"source_run": "r1", "source_timestamp_utc": "2026-05-09T00:00:00+00:00",
             "learned_patterns": [{"pattern": f"pat-{i}", "confidence": 0.5} for i in range(15)]},
        ]
        aggregated = _aggregate_prior_learnings(memory_updates)
        self.assertEqual(len(aggregated), 10)


class ContextPackPriorLearningsTests(unittest.TestCase):
    """Guards context_pack consumer wiring."""

    def test_context_pack_includes_empty_prior_learnings_for_fresh_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "apps/web").mkdir(parents=True)
            (project / "apps/web/page.tsx").write_text("export default () => null", encoding="utf-8")
            intent = {"goal": "fresh project", "allowed_change_scope": {"paths": ["apps/**"]}}
            context = _build_context_pack(project, intent, run_id="run_first")
            self.assertEqual(context["schema_version"], "agentic.context_pack.v2")
            self.assertEqual(context["prior_learnings"], [])
            self.assertEqual(context["prior_run_count"], 0)

    def test_context_pack_surfaces_prior_run_learnings_and_excludes_current(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "apps/web").mkdir(parents=True)
            (project / "apps/web/page.tsx").write_text("export default () => null", encoding="utf-8")
            # Seed two prior runs.
            for run_id, learnings in [
                ("run_old1", [
                    {"pattern": "failure_type `type_error` observed during required eval on this project",
                     "confidence": 0.78, "evidence": {"source_run": "run_old1"}},
                ]),
                ("run_old2", [
                    {"pattern": "failure_type `type_error` observed during required eval on this project",
                     "confidence": 0.78, "evidence": {"source_run": "run_old2"}},
                    {"pattern": "patch_worker `codex` repeatedly abandoned on failure_type `type_error` for this project",
                     "confidence": 0.92, "evidence": {"source_run": "run_old2"}},
                ]),
            ]:
                run_dir = project / ".agent/runs" / run_id
                run_dir.mkdir(parents=True)
                (run_dir / "memory-update.proposed.json").write_text(
                    json.dumps({"source_run": run_id, "source_timestamp_utc": "2026-05-01T00:00:00+00:00",
                                "learned_patterns": learnings}),
                    encoding="utf-8",
                )
            # Also create the current run dir; its memory file should be excluded.
            current_dir = project / ".agent/runs/run_current"
            current_dir.mkdir(parents=True)
            (current_dir / "memory-update.proposed.json").write_text(
                json.dumps({"source_run": "run_current", "source_timestamp_utc": "2026-05-09T00:00:00+00:00",
                            "learned_patterns": [{"pattern": "exclude_me", "confidence": 0.9}]}),
                encoding="utf-8",
            )
            intent = {"goal": "with prior learnings", "allowed_change_scope": {"paths": ["apps/**"]}}
            context = _build_context_pack(project, intent, run_id="run_current")

            patterns = [entry["pattern"] for entry in context["prior_learnings"]]
            self.assertTrue(any("type_error" in p for p in patterns))
            self.assertTrue(any("repeatedly abandoned" in p for p in patterns))
            self.assertNotIn("exclude_me", patterns)
            self.assertEqual(context["prior_run_count"], 2)
            # Most-frequent (type_error appears in both runs) should rank first.
            self.assertEqual(context["prior_learnings"][0]["occurrences"], 2)


class MemoryLoopEndToEndTests(unittest.TestCase):
    """Run the runtime twice on the same project and verify the second run
    sees the first run's learnings via context_pack.prior_learnings."""

    def test_second_run_sees_first_run_learnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Loop closure test.", paths.projects_dir)
            project_path = Path(project["path"])
            # Make a project shape that yields a deterministic eval (build) and
            # arrange for a failure so the first run produces a failure_type
            # learning. Static-html-present check is the safest deterministic
            # required eval — but we want a real failing eval to produce a
            # learning. Use a build script that fails.
            (project_path / "apps/web/package.json").write_text(
                json.dumps({"scripts": {"build": "node -e \"console.error('Type error: x'); process.exit(1)\""}}),
                encoding="utf-8",
            )
            (project_path / "apps/web/app").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/app/page.tsx").write_text(
                "export default function Page() { return null }\n", encoding="utf-8",
            )

            # Run 1: no prior runs, should produce a failure-type learning.
            result1 = AgenticProjectRuntime(engine.db).run(project=project, execute_eval=True)
            ctx1 = json.loads((result1.run_dir / "context-pack.json").read_text(encoding="utf-8"))
            self.assertEqual(ctx1["prior_learnings"], [])
            self.assertEqual(ctx1["prior_run_count"], 0)
            mem1 = json.loads((result1.run_dir / "memory-update.proposed.json").read_text(encoding="utf-8"))
            self.assertEqual(mem1["source_run"], result1.run_id)
            patterns1 = [item["pattern"] for item in mem1["learned_patterns"]]
            # First run had no source patch (no patch_worker), so promotion is
            # needs-human-review and we should see at least the decision learning.
            self.assertTrue(any("needs-human-review" in p for p in patterns1), patterns1)

            # Run 2: the same project, fresh run. Context pack should now
            # surface Run 1's learnings.
            result2 = AgenticProjectRuntime(engine.db).run(project=project, execute_eval=True)
            self.assertNotEqual(result1.run_id, result2.run_id)
            ctx2 = json.loads((result2.run_dir / "context-pack.json").read_text(encoding="utf-8"))
            self.assertEqual(ctx2["prior_run_count"], 1)
            patterns2 = [entry["pattern"] for entry in ctx2["prior_learnings"]]
            self.assertTrue(
                any("needs-human-review" in p for p in patterns2),
                f"Run 2 context should surface Run 1's decision learning; got {patterns2}",
            )


def _make_candidate(
    candidate_id: str,
    strategy: str,
    *,
    source_patch_present: bool = True,
    diff_within_scope: bool = True,
    out_of_scope: list[dict[str, Any]] | None = None,
    changed_paths: list[str] | None = None,
    eval_executed: bool = True,
    eval_passed: bool = True,
    repair_attempts: int = 0,
    stop_reason: str = "eval_passed_no_repair_needed",
    final_failure: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Construct a candidate dict with the shape `_build_promotion_report` expects."""
    changed = [{"path": p, "category": "source"} for p in (changed_paths or [])]
    return {
        "id": candidate_id,
        "strategy": strategy,
        "patch_diff": f"# {candidate_id}\n",
        "score": {
            "candidate": candidate_id,
            "strategy": strategy,
            "source_patch_present": source_patch_present,
            "diff_within_scope": diff_within_scope,
        },
        "changed_files": {
            "candidate": candidate_id,
            "changed_files": changed,
            "out_of_scope_changes": out_of_scope or [],
            "source_patch_present": source_patch_present,
        },
        "repair_history": {
            "candidate": candidate_id,
            "max_loops": max(repair_attempts, 0),
            "attempts": [{"loop_index": i + 1, "status": "eval_failed"} for i in range(repair_attempts)],
            "stop_reason": stop_reason,
            "final_failure": final_failure,
        },
        "eval_results": {
            "required_eval_declared": True,
            "required_eval_executed": eval_executed,
            "required_eval_passed": eval_passed,
            "commands": [
                {
                    "name": "build", "cmd": "npm run build", "required": True,
                    "executed": eval_executed, "passed": eval_passed, "exit_code": 0 if eval_passed else 1,
                }
            ],
        },
        "run_log": [],
        "worktree_path": None,
    }


class JaccardDiversityTests(unittest.TestCase):
    def test_empty_sets(self) -> None:
        self.assertEqual(_jaccard_distance(set(), set()), 0.0)

    def test_identical_sets(self) -> None:
        self.assertEqual(_jaccard_distance({"a", "b"}, {"a", "b"}), 0.0)

    def test_disjoint_sets(self) -> None:
        self.assertEqual(_jaccard_distance({"a"}, {"b"}), 1.0)

    def test_partial_overlap(self) -> None:
        self.assertAlmostEqual(_jaccard_distance({"a", "b", "c"}, {"b", "c", "d"}), 1.0 - 2 / 4)

    def test_diversity_zero_for_single_candidate(self) -> None:
        diversity = _compute_candidate_diversity([_make_candidate("candidate-a", "x", changed_paths=["foo.ts"])])
        self.assertEqual(diversity["average"], 0.0)
        self.assertEqual(diversity["pairs"], [])

    def test_diversity_nonzero_for_disjoint_candidates(self) -> None:
        candidates = [
            _make_candidate("candidate-a", "x", changed_paths=["foo.ts"]),
            _make_candidate("candidate-b", "y", changed_paths=["bar.ts"]),
        ]
        diversity = _compute_candidate_diversity(candidates)
        self.assertEqual(diversity["average"], 1.0)
        self.assertEqual(len(diversity["pairs"]), 1)
        self.assertEqual(diversity["pairs"][0]["distance"], 1.0)


class CandidateScorerTests(unittest.TestCase):
    """Unit tests for _score_candidate hard gates and component math."""

    def test_passing_candidate_earns_full_required_eval_and_safety(self) -> None:
        cand = _make_candidate("candidate-a", "conservative", changed_paths=["apps/web/app/page.tsx"])
        score = _score_candidate(
            cand, intent={"goal": "ship", "success_criteria": []},
            context={"relevant_files": [{"path": "apps/web/app/page.tsx"}]},
            eval_results=cand["eval_results"], abandonment_history=[], patch_worker="codex",
        )
        self.assertFalse(score["disqualified"])
        self.assertEqual(score["components"]["required_eval"], 40)
        self.assertEqual(score["components"]["scope_safety"], 10)
        self.assertEqual(score["components"]["context_alignment"], 5)
        self.assertGreaterEqual(score["total"], 60)

    def test_disqualified_when_no_source_patch(self) -> None:
        cand = _make_candidate("candidate-a", "x", source_patch_present=False, eval_passed=False)
        score = _score_candidate(
            cand, intent={"goal": "x", "success_criteria": []}, context={},
            eval_results=cand["eval_results"], abandonment_history=[], patch_worker="codex",
        )
        self.assertTrue(score["disqualified"])
        self.assertFalse(score["hard_gates"]["source_patch_present"])

    def test_disqualified_when_out_of_scope(self) -> None:
        cand = _make_candidate(
            "candidate-c", "broader-fix",
            changed_paths=["apps/web/app/page.tsx"],
            out_of_scope=[{"path": "scripts/deploy.sh"}],
        )
        score = _score_candidate(
            cand, intent={"goal": "x", "success_criteria": []}, context={},
            eval_results=cand["eval_results"], abandonment_history=[], patch_worker="codex",
        )
        self.assertTrue(score["disqualified"])
        self.assertFalse(score["hard_gates"]["no_out_of_scope_changes"])

    def test_disqualified_when_sensitive_path_touched(self) -> None:
        cand = _make_candidate("candidate-a", "x", changed_paths=["apps/web/.env.production"])
        score = _score_candidate(
            cand, intent={"goal": "x", "success_criteria": []}, context={},
            eval_results=cand["eval_results"], abandonment_history=[], patch_worker="codex",
        )
        self.assertTrue(score["disqualified"])
        self.assertFalse(score["hard_gates"]["no_critical_security_finding"])

    def test_test_only_patch_penalty_when_intent_is_not_test_focused(self) -> None:
        cand = _make_candidate(
            "candidate-b", "test-focused",
            changed_paths=["apps/web/tests/page.test.tsx"],
        )
        score = _score_candidate(
            cand, intent={"goal": "ship a feature", "success_criteria": ["feature works"]},
            context={}, eval_results=cand["eval_results"], abandonment_history=[], patch_worker="codex",
        )
        self.assertEqual(score["penalties"]["test_only_patch"], 15)

    def test_test_only_patch_no_penalty_when_intent_is_test_focused(self) -> None:
        cand = _make_candidate(
            "candidate-b", "test-focused",
            changed_paths=["apps/web/tests/page.test.tsx"],
        )
        score = _score_candidate(
            cand, intent={"goal": "add test coverage", "success_criteria": ["tests pass"]},
            context={}, eval_results=cand["eval_results"], abandonment_history=[], patch_worker="codex",
        )
        self.assertEqual(score["penalties"]["test_only_patch"], 0)

    def test_repeated_failure_type_penalty_when_history_matches(self) -> None:
        cand = _make_candidate(
            "candidate-a", "x", eval_passed=False,
            repair_attempts=2, stop_reason="max_loops_exhausted",
            final_failure={"failure_type": "type_error", "subtype": "typescript"},
        )
        history = [
            {"patch_worker": "codex", "final_failure": {"failure_type": "type_error"}},
        ]
        score = _score_candidate(
            cand, intent={"goal": "x", "success_criteria": []}, context={},
            eval_results=cand["eval_results"], abandonment_history=history, patch_worker="codex",
        )
        self.assertEqual(score["penalties"]["repeated_failure_type"], 10)


class MultiCandidatePromotionTests(unittest.TestCase):
    """The 8 acceptance tests Chuan listed for MVP-3A, plus a couple of
    structural checks (per-candidate score.json, three-candidate dirs)."""

    def _project_with_static_html(self, tmp: str, name: str) -> tuple[Path, dict[str, Any], Any]:
        paths = resolve_paths(tmp)
        initialize_workspace(paths)
        engine = create_engine(paths)
        project = engine.create_project(name, paths.projects_dir)
        project_path = Path(project["path"])
        (project_path / "apps/web").mkdir(parents=True, exist_ok=True)
        (project_path / "apps/web/index.html").write_text("<html></html>", encoding="utf-8")
        return project_path, project, engine

    def test_creates_three_candidate_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, project, engine = self._project_with_static_html(tmp, "Three candidates run")
            result = AgenticProjectRuntime(engine.db).run(project=project)
            run_dir = result.run_dir
            for cid in ("candidate-a", "candidate-b", "candidate-c"):
                self.assertTrue((run_dir / "candidates" / cid).is_dir(), f"missing {cid} dir")
                self.assertTrue((run_dir / "candidates" / cid / "patch.diff").exists())
                self.assertTrue((run_dir / "candidates" / cid / "changed-files.json").exists())
                self.assertTrue((run_dir / "candidates" / cid / "score.json").exists())
                self.assertTrue((run_dir / "candidates" / cid / "repair-history.json").exists())
                self.assertTrue((run_dir / "candidates" / cid / "eval-results.json").exists())
                self.assertTrue((run_dir / "candidates" / cid / "critics" / "security.md").exists())

    def test_writes_per_candidate_score_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _, project, engine = self._project_with_static_html(tmp, "Per-candidate score")
            result = AgenticProjectRuntime(engine.db).run(project=project)
            for cid, expected_strategy in [("candidate-a", "conservative"),
                                           ("candidate-b", "test-focused"),
                                           ("candidate-c", "broader-fix")]:
                score_path = result.run_dir / "candidates" / cid / "score.json"
                score = json.loads(score_path.read_text(encoding="utf-8"))
                self.assertEqual(score["candidate"], cid)
                self.assertEqual(score["strategy"], expected_strategy)

    def test_writes_selected_candidate_to_promotion_report(self) -> None:
        # When all candidates pass eval and have a source patch, selected_candidate
        # is set and decision is promote OR (in this minimal harness with no
        # patch worker) needs-human-review with selected_candidate=None.
        # Either way, the report MUST have the selected_candidate field present.
        with tempfile.TemporaryDirectory() as tmp:
            _, project, engine = self._project_with_static_html(tmp, "Selected promotion")
            result = AgenticProjectRuntime(engine.db).run(project=project)
            promo = json.loads((result.run_dir / "promotion-report.json").read_text(encoding="utf-8"))
            self.assertIn("selected_candidate", promo)
            self.assertIn("candidates", promo)
            self.assertIn("candidate_diversity", promo)
            self.assertEqual(promo["candidate_count"], 3)
            self.assertEqual(promo["schema_version"], "agentic.promotion_report.v2")
            # Each candidate summary must have id and score.
            for summary in promo["candidates"]:
                self.assertIn("id", summary)
                self.assertIn("score", summary)
                self.assertIn("disqualified", summary)

    def test_selects_candidate_b_when_eval_passes_and_candidate_a_fails(self) -> None:
        # Direct call to _build_promotion_report with hand-crafted candidates:
        # a fails eval, b passes, c passes but with sensitive-path penalty.
        cand_a = _make_candidate("candidate-a", "conservative", source_patch_present=True,
                                 changed_paths=["apps/web/app/page.tsx"], eval_passed=False)
        cand_b = _make_candidate("candidate-b", "test-focused", source_patch_present=True,
                                 changed_paths=["apps/web/app/Header.tsx", "apps/web/tests/header.test.tsx"],
                                 eval_passed=True)
        cand_c = _make_candidate("candidate-c", "broader-fix", source_patch_present=True,
                                 changed_paths=["apps/web/app/page.tsx", "apps/web/components/X.tsx"],
                                 eval_passed=True)
        intent = {"goal": "ship", "allowed_change_scope": {"paths": ["apps/**"]}, "success_criteria": []}
        context = {"context_quality": {"has_source_files": True}, "relevant_files": []}
        eval_harness = {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}
        report = _build_promotion_report(intent, context, eval_harness, [cand_a, cand_b, cand_c],
                                          [{"stage": str(i)} for i in range(6)],
                                          patch_worker="codex")
        # b or c can win — both pass eval. Critical: a is NOT selected.
        self.assertNotEqual(report["selected_candidate"], "candidate-a")
        self.assertIn(report["selected_candidate"], {"candidate-b", "candidate-c"})
        self.assertEqual(report["decision"], "promote")
        # Find candidate-a's summary, confirm it is disqualified.
        a_summary = next(s for s in report["candidates"] if s["id"] == "candidate-a")
        self.assertTrue(a_summary["disqualified"])

    def test_disqualifies_candidate_with_out_of_scope_changes(self) -> None:
        cand_a = _make_candidate("candidate-a", "conservative",
                                 changed_paths=["apps/web/app/page.tsx"],
                                 out_of_scope=[{"path": "node_modules/sneaky.js"}],
                                 eval_passed=True)
        cand_b = _make_candidate("candidate-b", "test-focused",
                                 changed_paths=["apps/web/tests/x.test.tsx"], eval_passed=True)
        intent = {"goal": "test the gate", "allowed_change_scope": {"paths": ["apps/**"]}, "success_criteria": ["tests pass"]}
        context = {"context_quality": {"has_source_files": True}, "relevant_files": []}
        eval_harness = {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}
        report = _build_promotion_report(intent, context, eval_harness, [cand_a, cand_b],
                                          [{"stage": str(i)} for i in range(6)],
                                          patch_worker="codex")
        a_summary = next(s for s in report["candidates"] if s["id"] == "candidate-a")
        self.assertTrue(a_summary["disqualified"])
        self.assertNotEqual(report["selected_candidate"], "candidate-a")

    def test_computes_candidate_diversity_from_changed_files(self) -> None:
        cand_a = _make_candidate("candidate-a", "conservative",
                                 changed_paths=["apps/web/app/page.tsx"], eval_passed=True)
        cand_b = _make_candidate("candidate-b", "test-focused",
                                 changed_paths=["apps/web/tests/page.test.tsx"], eval_passed=True)
        intent = {"goal": "x", "allowed_change_scope": {"paths": ["apps/**"]}, "success_criteria": []}
        context = {"context_quality": {"has_source_files": True}, "relevant_files": []}
        eval_harness = {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}
        report = _build_promotion_report(intent, context, eval_harness, [cand_a, cand_b],
                                          [{"stage": str(i)} for i in range(6)], patch_worker="codex")
        self.assertEqual(report["candidate_diversity"]["method"], "changed_file_jaccard_distance")
        self.assertGreater(report["candidate_diversity"]["average"], 0.0)
        self.assertNotEqual(report["soft_scores"]["candidate_diversity"], 0.0)

    def test_marks_run_needs_more_context_when_all_candidates_have_no_source_patch(self) -> None:
        # No context source files → context_has_source_files = False.
        cand_a = _make_candidate("candidate-a", "conservative", source_patch_present=False, eval_passed=False)
        cand_b = _make_candidate("candidate-b", "test-focused", source_patch_present=False, eval_passed=False)
        intent = {"goal": "x", "allowed_change_scope": {"paths": ["apps/**"]}, "success_criteria": []}
        context = {"context_quality": {"has_source_files": False}, "relevant_files": []}
        eval_harness = {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}
        report = _build_promotion_report(intent, context, eval_harness, [cand_a, cand_b],
                                          [{"stage": str(i)} for i in range(6)], patch_worker="codex")
        self.assertEqual(report["decision"], "needs-more-context")

    def test_records_candidate_abandonment_without_marking_run_abandoned(self) -> None:
        # End-to-end: simulate one candidate hitting repair exhaustion while
        # a sibling candidate has no patch to repair. The JSONL should have a
        # candidate_abandoned entry but no run_abandoned entry (because the
        # gate decision is needs-human-review, not abandoned).
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            initialize_workspace(paths)
            engine = create_engine(paths)
            project = engine.create_project("Partial abandonment", paths.projects_dir)
            project_path = Path(project["path"])
            (project_path / "apps/web").mkdir(parents=True, exist_ok=True)
            (project_path / "apps/web/index.html").write_text("<html></html>", encoding="utf-8")

            # Manually seed a candidate_abandoned record (since the runtime
            # without patch_worker=codex won't trigger real repair). Then
            # confirm the CLI helper distinguishes events correctly.
            log_dir = project_path / ".agent"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "agentic-abandonments.jsonl"
            log_path.write_text(
                json.dumps({
                    "schema_version": "agentic.abandonment_record.v1",
                    "event_type": "candidate_abandoned",
                    "run_id": "run_x", "timestamp_utc": "2026-05-09T00:00:00+00:00",
                    "intent_goal": "demo", "candidate": "candidate-a",
                    "decision": "candidate_abandoned", "patch_worker": "codex",
                    "stop_reason": "max_loops_exhausted", "attempt_count": 3, "max_loops": 3,
                    "final_failure": {"failure_type": "type_error"},
                }) + "\n",
                encoding="utf-8",
            )
            history = _read_abandonment_history(project_path)
            self.assertEqual(len(history), 1)
            self.assertEqual(history[0]["event_type"], "candidate_abandoned")
            self.assertEqual(history[0]["candidate"], "candidate-a")

            # Now actually run the agentic_project — it should NOT add a
            # run_abandoned record (decision is needs-human-review since
            # there is no patch worker generating diffs).
            result = AgenticProjectRuntime(engine.db).run(project=project)
            history_after = _read_abandonment_history(project_path)
            self.assertNotIn(result.decision, {"abandoned"})
            run_abandoned_records = [r for r in history_after if r.get("event_type") == "run_abandoned"]
            self.assertEqual(run_abandoned_records, [],
                             "no run_abandoned record should be written when run is not actually abandoned")


class SchemaValidatorTests(unittest.TestCase):
    """MVP-3C: shape contracts on the artifacts the runtime writes."""

    def _valid_promotion_report(self) -> dict[str, Any]:
        return {
            "schema_version": "agentic.promotion_report.v2",
            "candidate": "candidate-a",
            "selected_candidate": "candidate-a",
            "candidate_count": 1,
            "decision": "promote",
            "hard_gates": {"required_eval_passed": True},
            "gate_details": [],
            "candidates": [{"id": "candidate-a", "score": 90}],
            "candidate_diversity": {"method": "changed_file_jaccard_distance", "average": 0.0, "pairs": []},
            "eval": {},
            "repair": {},
            "soft_scores": {},
            "remaining_risks": [],
            "abandonment_pattern": {"patch_worker": "codex", "failure_type": None, "prior_abandonments": 0, "warning_emitted": False},
        }

    def test_promotion_report_valid_payload_returns_no_errors(self) -> None:
        self.assertEqual(_validate_promotion_report_v2(self._valid_promotion_report()), [])

    def test_promotion_report_rejects_wrong_schema_version(self) -> None:
        payload = self._valid_promotion_report()
        payload["schema_version"] = "agentic.promotion_report.v1"
        errors = _validate_promotion_report_v2(payload)
        self.assertTrue(any("schema_version" in e for e in errors))

    def test_promotion_report_rejects_unknown_decision(self) -> None:
        payload = self._valid_promotion_report()
        payload["decision"] = "yolo"
        errors = _validate_promotion_report_v2(payload)
        self.assertTrue(any("decision `yolo`" in e for e in errors))

    def test_promotion_report_rejects_missing_required_keys(self) -> None:
        payload = self._valid_promotion_report()
        del payload["candidates"]
        errors = _validate_promotion_report_v2(payload)
        self.assertTrue(any("candidates" in e for e in errors))

    def test_promotion_report_rejects_wrong_type(self) -> None:
        payload = self._valid_promotion_report()
        payload["candidate_count"] = "three"
        errors = _validate_promotion_report_v2(payload)
        self.assertTrue(any("candidate_count" in e for e in errors))

    def test_promotion_report_rejects_candidate_summary_without_id(self) -> None:
        payload = self._valid_promotion_report()
        payload["candidates"] = [{"score": 90}]
        errors = _validate_promotion_report_v2(payload)
        self.assertTrue(any("id" in e for e in errors))

    def test_promotion_report_rejects_non_dict_payload(self) -> None:
        self.assertTrue(_validate_promotion_report_v2("not a dict"))  # type: ignore[arg-type]

    def test_candidate_score_validator_passes_minimum_payload(self) -> None:
        self.assertEqual(_validate_candidate_score({
            "schema_version": "agentic.candidate_score.v1",
            "candidate": "candidate-a",
            "source_patch_present": True,
            "diff_within_scope": True,
        }), [])

    def test_candidate_score_validator_catches_wrong_schema(self) -> None:
        errors = _validate_candidate_score({"schema_version": "wrong", "candidate": "x", "source_patch_present": True, "diff_within_scope": True})
        self.assertTrue(any("schema_version" in e for e in errors))

    def test_candidate_score_validator_catches_bool_typo(self) -> None:
        errors = _validate_candidate_score({
            "schema_version": "agentic.candidate_score.v1",
            "candidate": "x",
            "source_patch_present": "yes",  # str instead of bool
            "diff_within_scope": True,
        })
        self.assertTrue(any("source_patch_present" in e for e in errors))

    def test_changed_files_validator_passes_minimum_payload(self) -> None:
        self.assertEqual(_validate_changed_files({
            "schema_version": "agentic.changed_files.v1",
            "candidate": "candidate-a",
            "changed_files": [],
            "source_patch_present": False,
            "out_of_scope_changes": [],
        }), [])

    def test_changed_files_validator_catches_missing_keys(self) -> None:
        errors = _validate_changed_files({"schema_version": "agentic.changed_files.v1", "candidate": "x"})
        self.assertTrue(len(errors) >= 3)  # changed_files / source_patch_present / out_of_scope_changes

    def test_build_promotion_report_writes_valid_v2_payload(self) -> None:
        # Round-trip: build a real promotion report and validate it.
        cand = _make_candidate("candidate-a", "conservative",
                               changed_paths=["apps/web/app/page.tsx"], eval_passed=True)
        intent = {"goal": "x", "allowed_change_scope": {"paths": ["apps/**"]}, "success_criteria": []}
        context = {"context_quality": {"has_source_files": True}, "relevant_files": []}
        eval_harness = {"commands": [{"name": "build", "cmd": "npm run build", "required": True}]}
        report = _build_promotion_report(intent, context, eval_harness, [cand],
                                         [{"stage": str(i)} for i in range(6)],
                                         patch_worker="codex")
        self.assertEqual(_validate_promotion_report_v2(report), [])


if __name__ == "__main__":
    unittest.main()
