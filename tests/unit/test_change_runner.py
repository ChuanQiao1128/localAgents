"""RC-4A.2: unit tests for orchestrator.core.change_runner.

These cover the deterministic seams of change-mode runtime:
- `build_change_task_graph` turns a contract into a 1-task graph
- `validate_applied_change` enforces the agentic.applied_change.v1 shape
- `_finalize_change_outputs` (driven via `_finalize_change_outputs_test_harness`)
  produces the right delivery-report content for each operational outcome

E2E coverage (real autonomous controller + git + commit trailers + fake
patch worker) lives in `tests/e2e/test_change_run_e2e.py` so the unit
suite stays fast and isolated.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.core.artifact_validation import (
    validate_applied_change,
    validate_change_contract,
    validate_delivery_report_text,
)
from orchestrator.core.change_runner import (
    APPLIED_CHANGE_SCHEMA_VERSION,
    MISSING_SCOPE_REASON,
    build_change_task_graph,
    _emit_missing_scope_failure,
    _read_eval_validation,
    _swap_task_graph,
    _restore_task_graph,
)


_VALID_CONTRACT = {
    "schema_version": "agentic.change_contract.v1",
    "change_id": "change_abc123def456",
    "source_change_request_path": "/tmp/change-request.md",
    "goal": "Add a side-by-side diff view between original and rewritten text on the home page.",
    "scope_paths": ["app/page.tsx", "components/**"],
    "scope_missing": False,
    "non_goals": ["Do not change the rewrite API."],
    "acceptance": [
        "Original on the left, rewritten on the right.",
        "npm run build passes.",
    ],
    "created_at": "2026-05-12T00:00:00+00:00",
}


class BuildChangeTaskGraphTests(unittest.TestCase):
    def test_builds_single_task_with_metadata(self) -> None:
        graph = build_change_task_graph(
            change_id="change_abc",
            contract=_VALID_CONTRACT,
            change_dir_relpath=".agent/changes/change_abc",
        )
        self.assertEqual(len(graph["tasks"]), 1)
        task = graph["tasks"][0]
        self.assertEqual(task["id"], "change-change_abc")
        self.assertEqual(task["status"], "pending")
        self.assertEqual(task["dependencies"], [])
        # change-mode metadata commit_task reads
        self.assertEqual(task["change_id"], "change_abc")
        self.assertEqual(
            task["source_change_request"],
            ".agent/changes/change_abc/change-request.md",
        )

    def test_scope_paths_acceptance_propagate(self) -> None:
        graph = build_change_task_graph(
            change_id="change_abc",
            contract=_VALID_CONTRACT,
            change_dir_relpath=".agent/changes/change_abc",
        )
        task = graph["tasks"][0]
        self.assertEqual(task["scope_paths"], ["app/page.tsx", "components/**"])
        self.assertEqual(
            task["acceptance_criteria"],
            [
                "Original on the left, rewritten on the right.",
                "npm run build passes.",
            ],
        )
        self.assertIn("side-by-side diff", task["intent"])
        self.assertIn("npm run build passes", task["intent"])
        self.assertIn("Do not change the rewrite API", task["intent"])

    def test_empty_goal_raises(self) -> None:
        bad = dict(_VALID_CONTRACT)
        bad["goal"] = "   "
        with self.assertRaises(ValueError):
            build_change_task_graph(
                change_id="change_x", contract=bad,
                change_dir_relpath=".agent/changes/change_x",
            )

    def test_empty_acceptance_raises(self) -> None:
        bad = dict(_VALID_CONTRACT)
        bad["acceptance"] = []
        with self.assertRaises(ValueError):
            build_change_task_graph(
                change_id="change_x", contract=bad,
                change_dir_relpath=".agent/changes/change_x",
            )

    def test_non_dict_contract_raises(self) -> None:
        with self.assertRaises(TypeError):
            build_change_task_graph(
                change_id="change_x", contract=[1, 2, 3],  # type: ignore[arg-type]
                change_dir_relpath=".agent/changes/change_x",
            )

    def test_contract_validation_still_passes_for_input(self) -> None:
        # Sanity: the unit-test fixture is itself a valid change-contract.
        self.assertEqual(validate_change_contract(_VALID_CONTRACT), [])


class SwapAndRestoreTaskGraphTests(unittest.TestCase):
    def test_swap_when_no_prior_graph_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            new_graph = build_change_task_graph(
                change_id="change_a", contract=_VALID_CONTRACT,
                change_dir_relpath=".agent/changes/change_a",
            )
            backup = _swap_task_graph(project, new_graph)
            self.assertIsNone(backup)
            self.assertTrue((project / "task-graph.json").exists())
            _restore_task_graph(project, backup)
            self.assertFalse(
                (project / "task-graph.json").exists(),
                msg="restore must remove the swapped-in file when there was no original",
            )

    def test_swap_round_trip_preserves_prior_graph(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            prior = {
                "schema_version": 1,
                "project_title": "existing",
                "overview": "existing autonomous graph",
                "tasks": [{
                    "id": "task-001", "title": "existing", "intent": "existing",
                    "acceptance_criteria": [], "scope_paths": [], "dependencies": [],
                    "status": "pending", "risk": "low", "run_ids": [], "commit": None,
                }],
            }
            (project / "task-graph.json").write_text(json.dumps(prior), encoding="utf-8")
            new_graph = build_change_task_graph(
                change_id="change_a", contract=_VALID_CONTRACT,
                change_dir_relpath=".agent/changes/change_a",
            )
            backup = _swap_task_graph(project, new_graph)
            self.assertIsNotNone(backup)
            self.assertEqual(backup["project_title"], "existing")  # type: ignore[index]
            self.assertEqual(
                json.loads((project / "task-graph.json").read_text(encoding="utf-8"))["tasks"][0]["id"],
                "change-change_a",
                msg="task-graph.json should now hold the change-mode graph",
            )
            _restore_task_graph(project, backup)
            restored = json.loads((project / "task-graph.json").read_text(encoding="utf-8"))
            self.assertEqual(restored["project_title"], "existing")
            self.assertEqual(restored["tasks"][0]["id"], "task-001")


class ValidateAppliedChangeTests(unittest.TestCase):
    def _baseline(self) -> dict:
        return {
            "schema_version": APPLIED_CHANGE_SCHEMA_VERSION,
            "change_id": "change_abc",
            "candidate": "candidate-b",
            "run_id": "run_xyz",
            "base_commit": "abc1234",
            "applied_to_commit": "def5678",
            "files_touched": ["app/page.tsx", "components/Diff.tsx"],
            "applied_at": "2026-05-12T00:01:30+00:00",
            "commit": {
                "branch": "agentic/change/change_abc",
                "sha": "def5678",
                "message": "Add side-by-side diff view\n\nChange-Id: change_abc\n",
            },
            "promotion_decision": "promote",
            "source_change_request": ".agent/changes/change_abc/change-request.md",
        }

    def test_baseline_is_valid(self) -> None:
        self.assertEqual(validate_applied_change(self._baseline()), [])

    def test_wrong_schema_version_flagged(self) -> None:
        bad = self._baseline()
        bad["schema_version"] = "wrong"
        errors = validate_applied_change(bad)
        self.assertTrue(any("schema_version" in e for e in errors))

    def test_missing_required_keys_flagged(self) -> None:
        bad = self._baseline()
        del bad["commit"]
        errors = validate_applied_change(bad)
        self.assertTrue(any("commit" in e for e in errors))

    def test_commit_subkeys_required(self) -> None:
        bad = self._baseline()
        bad["commit"] = {"branch": "agentic/change/change_abc", "sha": "def5678"}
        errors = validate_applied_change(bad)
        self.assertTrue(any("commit.message" in e for e in errors))

    def test_files_touched_must_be_list_of_str(self) -> None:
        bad = self._baseline()
        bad["files_touched"] = ["ok.tsx", 12345]  # type: ignore[list-item]
        errors = validate_applied_change(bad)
        self.assertTrue(any("files_touched[1]" in e for e in errors))

    def test_empty_change_id_flagged(self) -> None:
        bad = self._baseline()
        bad["change_id"] = "   "
        errors = validate_applied_change(bad)
        self.assertTrue(any("change_id" in e for e in errors))

    def test_non_dict_payload_returns_error(self) -> None:
        self.assertEqual(validate_applied_change([]), ["applied-change payload is not a dict"])  # type: ignore[arg-type]


class ReadEvalValidationTests(unittest.TestCase):
    """RC-4A.3.1.B regression tests — the delivery-report Validation
    section must surface eval + promotion + apply signals on a successful
    change run, not the "(no validation results recorded)" placeholder
    the pre-fix path produced because it read the wrong field name
    (`commands_run` vs `commands`)."""

    def _seed_run_dir(self, project_path: Path, run_id: str, candidate_id: str) -> Path:
        run_dir = project_path / ".agent" / "runs" / run_id
        cand_dir = run_dir / "candidates" / candidate_id
        cand_dir.mkdir(parents=True, exist_ok=True)
        # eval-results.json — producer schema uses `commands` (NOT
        # `commands_run`). RC-4A.3.1.B fix.
        (cand_dir / "eval-results.json").write_text(json.dumps({
            "schema_version": "agentic.eval_results.v1",
            "required_eval_declared": True,
            "required_eval_executed": True,
            "required_eval_passed": True,
            "commands": [
                {"name": "build", "cmd": "npm run build", "passed": True, "required": True, "executed": True, "exit_code": 0},
                {"name": "typecheck", "cmd": "tsc --noEmit", "passed": True, "required": True, "executed": True, "exit_code": 0},
            ],
        }), encoding="utf-8")
        # promotion-report.json so we can also surface decision + gate count.
        (run_dir / "promotion-report.json").write_text(json.dumps({
            "schema_version": "agentic.promotion_report.v2",
            "decision": "promote",
            "selected_candidate": candidate_id,
            "hard_gates": {"all_pass": True},
            "gate_details": [
                {"id": "source_patch_present", "passed": True},
                {"id": "diff_within_scope", "passed": True},
                {"id": "patch_apply_check_passed", "passed": True},
            ],
        }), encoding="utf-8")
        return run_dir

    def test_eval_promotion_apply_rows_all_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            self._seed_run_dir(project, "run_x", "candidate-b")
            applied_change = {
                "commit": {"branch": "agentic/change/change_x", "sha": "deadbee"},
                "applied_to_commit": "deadbee",
            }
            block = _read_eval_validation(
                project, "run_x", "candidate-b",
                promotion_decision="promote",
                applied_change=applied_change,
            )
            self.assertIn("eval.build", block)
            self.assertIn("eval.typecheck", block)
            self.assertTrue(block["eval.build"]["passed"])
            self.assertEqual(block["eval.build"]["command"], "npm run build")
            self.assertIn("promotion", block)
            self.assertTrue(block["promotion"]["passed"])
            self.assertIn("decision=promote", block["promotion"]["command"])
            self.assertIn("hard_gates=3/3 passed", block["promotion"]["command"])
            self.assertIn("apply", block)
            self.assertTrue(block["apply"]["passed"])
            self.assertIn("deadbee", block["apply"]["command"])

    def test_no_eval_results_still_returns_promotion_block(self) -> None:
        """When eval-results.json is missing (e.g. paused early), we still
        want the promotion + apply rows so the report says SOMETHING."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            run_dir = project / ".agent" / "runs" / "run_y"
            run_dir.mkdir(parents=True)
            (run_dir / "promotion-report.json").write_text(json.dumps({
                "schema_version": "agentic.promotion_report.v2",
                "decision": "needs-human-review",
                "selected_candidate": None,
                "hard_gates": {"all_pass": False},
                "gate_details": [
                    {"id": "source_patch_present", "passed": True},
                    {"id": "diff_within_scope", "passed": False},
                ],
            }), encoding="utf-8")
            block = _read_eval_validation(
                project, "run_y", "candidate-a",
                promotion_decision="needs-human-review",
                applied_change=None,
            )
            self.assertIn("promotion", block)
            self.assertFalse(block["promotion"]["passed"])
            self.assertIn("decision=needs-human-review", block["promotion"]["command"])
            self.assertIn("hard_gates=1/2 passed", block["promotion"]["command"])
            self.assertNotIn("apply", block)

    def test_no_run_id_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            block = _read_eval_validation(
                Path(tmp), None, None,
                promotion_decision=None, applied_change=None,
            )
            self.assertEqual(block, {})


class DeliveryReportFromControllerStateTests(unittest.TestCase):
    """Render-driven tests: feed `render_delivery_report` the shape
    `_finalize_change_outputs` produces and confirm the validator agrees.
    Keeps us honest about the contract between the runner and the
    renderer without spinning up the controller."""

    def _render_via_runner_shape(self, *, result: str, commit_sha: str | None,
                                  files: list[str], validation: dict, risks: list[str],
                                  open_reviews: list[dict]) -> str:
        from orchestrator.core.change_delivery_report import render_delivery_report
        return render_delivery_report({
            "change_id": "change_abc123",
            "result": result,
            "goal": "Add side-by-side diff view.",
            "files_touched": files,
            "validation": validation,
            "risks": risks,
            "commit": (
                {"branch": "agentic/change/change_abc123", "sha": commit_sha, "message": "Add diff"}
                if commit_sha else {}
            ),
            "review_queue": {"open_count": len(open_reviews), "items": open_reviews},
            "elapsed_sec": 12.3,
            "created_at": "2026-05-12T00:00:00+00:00",
            "completed_at": "2026-05-12T00:00:12+00:00",
        })

    def test_completed_path_renders_and_validates(self) -> None:
        md = self._render_via_runner_shape(
            result="completed",
            commit_sha="deadbee",
            files=["app/page.tsx", "components/Diff.tsx"],
            validation={"build": {"passed": True, "command": "npm run build", "duration_sec": 12.3}},
            risks=[],
            open_reviews=[],
        )
        self.assertIn("**completed**", md)
        self.assertIn("`app/page.tsx`", md)
        self.assertIn("`deadbee`", md)
        self.assertEqual(validate_delivery_report_text(md), [])

    def test_needs_review_path_renders_and_validates(self) -> None:
        md = self._render_via_runner_shape(
            result="needs-human-review",
            commit_sha=None,
            files=[],
            validation={},
            risks=["Session paused: needs_human_review"],
            open_reviews=[{"review_id": "review_42", "title": "Diff layout needs human review"}],
        )
        self.assertIn("**needs-human-review**", md)
        self.assertIn("`review_42`", md)
        self.assertIn("(no commit recorded — change was not applied)", md)
        self.assertEqual(validate_delivery_report_text(md), [])

    def test_failed_path_renders_and_validates(self) -> None:
        md = self._render_via_runner_shape(
            result="failed",
            commit_sha=None,
            files=[],
            validation={"build": {"passed": False, "command": "npm run build", "duration_sec": 9.0}},
            risks=["Codex patch refused to apply."],
            open_reviews=[],
        )
        self.assertIn("**failed**", md)
        self.assertIn("**build**: failed", md)
        self.assertEqual(validate_delivery_report_text(md), [])


# ===========================================================================
# RC-5A.13: pre-Codex scope guard
# ===========================================================================
class MissingScopeGuardTests(unittest.TestCase):
    """A change with no scope_paths must fail BEFORE Codex / autonomous
    is involved, write a deterministic delivery-report.md, and never
    consume the project's needs_human_review budget."""

    def _setup_cdir(self, tmp: str, change_id: str) -> tuple[Path, Path]:
        project = Path(tmp)
        cdir = project / ".agent" / "changes" / change_id
        cdir.mkdir(parents=True)
        return project, cdir

    def test_emits_failed_delivery_report_with_missing_scope_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project, cdir = self._setup_cdir(tmp, "change_zzz")
            contract = {
                **_VALID_CONTRACT,
                "change_id": "change_zzz",
                "scope_paths": [],
                "scope_missing": True,
            }

            result = _emit_missing_scope_failure(
                project_path=project,
                cdir=cdir,
                change_id="change_zzz",
                contract=contract,
                change_dir_relpath=".agent/changes/change_zzz",
                started_iso="2026-05-15T00:00:00+00:00",
                started_ts=1747000000.0,
            )

            self.assertEqual(result.result, "failed")
            self.assertEqual(result.change_id, "change_zzz")
            # No session was created.
            self.assertIsNone(result.session_id)
            # No commit because Codex was never called.
            self.assertIsNone(result.commit_sha)
            self.assertIsNone(result.applied_change_path)

            # delivery-report.md exists, displays the failure reason, and
            # validates clean against agentic.delivery_report.v1.
            self.assertTrue(result.delivery_report_path.exists())
            md = result.delivery_report_path.read_text(encoding="utf-8")
            self.assertIn("**failed**", md)
            self.assertIn(MISSING_SCOPE_REASON, md)
            self.assertEqual(validate_delivery_report_text(md), [])

    def test_pre_codex_guard_does_not_invoke_inner_loop(self) -> None:
        """Drive run_change with a real (but tiny) git repo + scope_missing
        contract. Verify the injected `run_inner_loop` is NEVER called and
        the controller never creates a session."""
        import subprocess as sp

        from orchestrator.core.change_contract import create_change
        from orchestrator.core.change_runner import run_change

        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            sp.run(["git", "init", "-q", "-b", "main"], cwd=project_path, check=True)
            sp.run(["git", "config", "user.email", "test@example.com"], cwd=project_path, check=True)
            sp.run(["git", "config", "user.name", "Test"], cwd=project_path, check=True)
            (project_path / "README.md").write_text("# test\n", encoding="utf-8")
            sp.run(["git", "add", "."], cwd=project_path, check=True)
            sp.run(["git", "commit", "-q", "-m", "init"], cwd=project_path, check=True)

            # Write change-request.md with NO scope section. Parser flags
            # scope_missing=True; create_change persists that into the
            # contract.
            cr_path = project_path / "change-request.md"
            cr_path.write_text(
                "## Goal\nAdd footer.\n\n## Acceptance\n- Footer renders.\n",
                encoding="utf-8",
            )
            created = create_change(
                project_path=project_path,
                change_request_path=cr_path,
            )
            change_id = created.change_id
            # The change dir + change-request.md are now untracked → the
            # change_runner's worktree-clean preflight would refuse. Commit
            # so we exercise ONLY the pre-Codex guard, not preflight.
            sp.run(["git", "add", "."], cwd=project_path, check=True)
            sp.run(["git", "commit", "-q", "-m", "seed change"], cwd=project_path, check=True)

            inner_loop_calls: list[Any] = []

            def fake_inner_loop(*args: Any, **kwargs: Any) -> Any:
                inner_loop_calls.append((args, kwargs))
                raise AssertionError(
                    "pre-Codex guard failure: inner loop was called even though "
                    "scope_paths is empty"
                )

            project = {"id": "test-project", "path": str(project_path)}
            result = run_change(
                project=project,
                change_id=change_id,
                run_inner_loop=fake_inner_loop,
            )

            self.assertEqual(result.result, "failed")
            self.assertEqual(inner_loop_calls, [], "inner loop must not be called")
            self.assertIsNone(result.session_id)
            self.assertIsNone(result.commit_sha)
            # Sessions dir should not have been created.
            sessions_dir = project_path / ".agent" / "autonomous" / "sessions"
            if sessions_dir.exists():
                self.assertEqual(
                    list(sessions_dir.iterdir()),
                    [],
                    "no session should have been created",
                )
            md = result.delivery_report_path.read_text(encoding="utf-8")
            self.assertIn(MISSING_SCOPE_REASON, md)


if __name__ == "__main__":
    unittest.main()
