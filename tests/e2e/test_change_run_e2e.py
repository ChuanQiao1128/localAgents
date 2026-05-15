"""RC-4A.2: end-to-end test for `agent-studio change run` driven by a
fake patch worker.

No real Codex. No real Vercel. Confirms the full change-mode pipeline
end-to-end through the real AutonomousController + real Apply Gate +
real git ops:

    change new      → change-contract + 4 sibling artifacts
    change run      → 1-task graph swapped in, advance_one_task,
                      apply_selected_candidate, real git commit with
                      Change-Id + Source-Change-Request trailers
    [post-run]      → applied-change.json + delivery-report.md exist
                      under .agent/changes/<change_id>/
    change validate → all artifacts validate clean
"""
from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from orchestrator.core.autonomous import AutonomousController
from orchestrator.core.change_contract import (
    change_dir,
    create_change,
)
from orchestrator.core.change_runner import (
    APPLIED_CHANGE_SCHEMA_VERSION,
    run_change,
)
from orchestrator.core.ids import now_iso
from orchestrator.core.run_package import apply_selected_candidate


_VALID_CHANGE_REQUEST = (
    "## Goal\n"
    "Add a side-by-side diff view between original and rewritten text on the home page.\n"
    "\n"
    "## Scope\n"
    "- app/page.tsx\n"
    "- components/**\n"
    "\n"
    "## Non-goals\n"
    "- Do not change the rewrite API.\n"
    "\n"
    "## Acceptance\n"
    "- Original text appears on the left, rewritten text on the right.\n"
    "- npm run build passes.\n"
)


def _git(*args: str, cwd: Path, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=cwd, text=True, capture_output=True, check=check,
    )


def _git_short_head(project_path: Path) -> str:
    return _git("rev-parse", "--short", "HEAD", cwd=project_path).stdout.strip()


def _slugify(title: str) -> str:
    out = []
    for ch in title.lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "task"


def _build_new_file_diff(rel_path: str, body_lines: list[str]) -> str:
    body = "".join(f"+{line}\n" for line in body_lines)
    return (
        f"diff --git a/{rel_path} b/{rel_path}\n"
        "new file mode 100644\n"
        "--- /dev/null\n"
        f"+++ b/{rel_path}\n"
        f"@@ -0,0 +1,{len(body_lines)} @@\n"
        f"{body}"
    )


def _materialize_run_package(project_path: Path, *, run_id: str, task: dict[str, Any]) -> Path:
    """Write a run package the Apply Gate will accept, including a patch
    that creates a brand-new file (so it always applies cleanly)."""
    candidate_id = "candidate-b"
    run_dir = project_path / ".agent" / "runs" / run_id
    cand_dir = run_dir / "candidates" / candidate_id
    cand_dir.mkdir(parents=True, exist_ok=True)

    rel_path = f"components/{_slugify(task['title'])}.tsx"
    body_lines = [
        "// fake change-mode patch — RC-4A.2 e2e test",
        f"// task: {task['id']}",
        "export default function Component() {",
        f"  return <div>{task['title']}</div>;",
        "}",
    ]
    (cand_dir / "patch.diff").write_text(_build_new_file_diff(rel_path, body_lines), encoding="utf-8")

    base_commit = _git_short_head(project_path)
    (cand_dir / "changed-files.json").write_text(json.dumps({
        "schema_version": "agentic.changed_files.v1",
        "candidate": candidate_id,
        "base_commit": base_commit,
        "changed_files": [{"path": rel_path, "status": "added"}],
        "source_patch_present": True,
        "out_of_scope_changes": [],
    }, indent=2), encoding="utf-8")
    (cand_dir / "score.json").write_text(json.dumps({
        "schema_version": "agentic.candidate_score.v1",
        "candidate": candidate_id,
        "strategy": "test-focused",
        "source_patch_present": True,
        "diff_within_scope": True,
        "score": 0.95,
        "components": {"hard_gates": True},
        "penalties": {},
    }, indent=2), encoding="utf-8")
    # eval-results.json so the delivery-report's validation section has
    # something to display. Schema mirrors the real producer's shape
    # (`commands`, NOT `commands_run` — this was the field-name mismatch
    # RC-4A.3.1.B fixed).
    (cand_dir / "eval-results.json").write_text(json.dumps({
        "schema_version": "agentic.eval_results.v1",
        "candidate": candidate_id,
        "required_eval_declared": True,
        "required_eval_executed": True,
        "required_eval_passed": True,
        "commands": [
            {"name": "build", "cmd": "npm run build", "passed": True, "required": True, "executed": True, "exit_code": 0},
            {"name": "test", "cmd": "npm test", "passed": True, "required": True, "executed": True, "exit_code": 0},
        ],
    }, indent=2), encoding="utf-8")

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
            {"id": "patch_apply_check_passed", "passed": True},
        ],
        "eval": {"required_pass": True, "failed_required": []},
        "repair": {"loops": 0, "exhausted": False},
        "soft_scores": {"score": 0.95},
        "remaining_risks": [],
        "abandonment_pattern": {"prior_runs": 0, "abandoned_count": 0},
        "candidates": [
            {
                "id": candidate_id,
                "strategy": "test-focused",
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
    # Lightweight envelope artifacts
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
            {"id": candidate_id, "label": "test-focused", "prompt_hint": "fake"},
        ],
    }, indent=2), encoding="utf-8")
    (run_dir / "trace.jsonl").write_text(
        json.dumps({"ts": now_iso(), "stage": "fake", "message": "synthetic"}) + "\n",
        encoding="utf-8",
    )
    return run_dir


@dataclass(frozen=True)
class _FakeRunResult:
    run_id: str
    status: str
    decision: str
    candidate: str
    run_dir: Path


def make_fake_inner_loop(project_path: Path) -> Callable[..., _FakeRunResult]:
    counter = {"i": 0}

    def runner(**kwargs: Any) -> _FakeRunResult:
        intent = kwargs.get("intent_overrides") or {}
        title = (intent.get("goal") or "Change task").splitlines()[0][:80]
        counter["i"] += 1
        run_id = f"run_change_e2e_{counter['i']:03d}"
        synthetic_task = {
            "id": f"change-task-{counter['i']:03d}",
            "title": title,
            "intent": intent.get("goal") or "",
            "acceptance_criteria": list(intent.get("success_criteria") or []),
            "scope_paths": list(((intent.get("allowed_change_scope") or {}).get("paths")) or []),
        }
        run_dir = _materialize_run_package(project_path, run_id=run_id, task=synthetic_task)
        return _FakeRunResult(
            run_id=run_id, status="ready", decision="promote",
            candidate="candidate-b", run_dir=run_dir,
        )
    return runner


def _setup_git_project(tmp: Path) -> Path:
    """Create a tiny git project ready for `change new`/`change run`."""
    project = tmp / "project"
    project.mkdir()
    (project / "package.json").write_text(json.dumps({
        "name": "rc4a2-demo",
        "scripts": {"build": "next build", "test": "jest"},
        "dependencies": {"next": "15.5.18", "react": "19.0.0"},
    }, indent=2), encoding="utf-8")
    (project / "app").mkdir()
    (project / "app" / "page.tsx").write_text("export default function Page() { return null; }\n", encoding="utf-8")
    (project / "components").mkdir()
    (project / "components" / ".gitkeep").write_text("", encoding="utf-8")
    (project / ".gitignore").write_text(".agent/\nnode_modules/\n", encoding="utf-8")

    _git("init", "-q", "-b", "main", cwd=project)
    _git("config", "user.email", "rc4a2@change", cwd=project)
    _git("config", "user.name", "rc4a2", cwd=project)
    _git("add", "-A", cwd=project)
    _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "init", cwd=project)
    return project


class ChangeRunE2ETests(unittest.TestCase):
    def test_change_run_happy_path_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = _setup_git_project(Path(tmp))

            # 1. Create the change session via the real `create_change`.
            cr_path = project_path.parent / "change-request.md"
            cr_path.write_text(_VALID_CHANGE_REQUEST, encoding="utf-8")
            created = create_change(project_path, cr_path)
            change_id = created.change_id
            cdir = change_dir(project_path, change_id)

            # 2. Drive change-mode runtime with a fake patch worker that
            # writes a real run package and returns decision=promote.
            project = {"id": "project_rc4a2", "name": "rc4a2-demo", "path": str(project_path)}
            result = run_change(
                project=project,
                change_id=change_id,
                run_inner_loop=make_fake_inner_loop(project_path),
                apply_candidate=apply_selected_candidate,
            )

            # 3. Result is completed and produced both artifacts.
            self.assertEqual(result.result, "completed", msg=str(result))
            self.assertIsNotNone(result.applied_change_path)
            self.assertTrue(Path(result.applied_change_path).exists())  # type: ignore[arg-type]
            self.assertTrue(Path(result.delivery_report_path).exists())
            self.assertIsNotNone(result.commit_sha)

            # 4. applied-change.json shape matches agentic.applied_change.v1.
            from orchestrator.core.artifact_validation import validate_applied_change
            applied = json.loads(Path(result.applied_change_path).read_text(encoding="utf-8"))  # type: ignore[arg-type]
            self.assertEqual(applied["schema_version"], APPLIED_CHANGE_SCHEMA_VERSION)
            self.assertEqual(applied["change_id"], change_id)
            self.assertTrue(applied["commit"]["sha"])
            self.assertEqual(applied["commit"]["branch"], f"agentic/change/{change_id}")
            self.assertEqual(validate_applied_change(applied), [])
            # files_touched must contain the patched path
            self.assertTrue(any(p.endswith(".tsx") for p in applied["files_touched"]))

            # 5. delivery-report.md validator agrees.
            from orchestrator.core.artifact_validation import validate_delivery_report_text
            md = Path(result.delivery_report_path).read_text(encoding="utf-8")
            self.assertIn(f"# Change Delivery Report — {change_id}", md)
            self.assertIn("**completed**", md)
            self.assertEqual(validate_delivery_report_text(md), [])

            # 6. Real git commit carries Change-Id + Source-Change-Request trailers.
            head_msg = _git("log", "-1", "--pretty=%B", cwd=project_path).stdout
            self.assertIn(f"Change-Id: {change_id}", head_msg)
            self.assertIn(f"Source-Change-Request: .agent/changes/{change_id}/change-request.md", head_msg)
            # Branch is agentic/change/<change_id>
            current_branch = _git("rev-parse", "--abbrev-ref", "HEAD", cwd=project_path).stdout.strip()
            self.assertEqual(current_branch, f"agentic/change/{change_id}")

            # 7. Status reflects "delivered" (delivery-report.md present).
            from orchestrator.core.change_contract import change_status_summary
            summary = change_status_summary(project_path, change_id)
            self.assertEqual(summary["state"], "delivered")
            self.assertIsNotNone(summary["artifacts"]["applied_change_json"])
            self.assertIsNotNone(summary["artifacts"]["delivery_report_md"])

            # 8. Validator CLI surface: every artifact under change_dir validates.
            from orchestrator.core.artifact_validation import (
                validate_change_contract,
            )
            contract = json.loads(
                (cdir / "change-contract.json").read_text(encoding="utf-8")
            )
            self.assertEqual(validate_change_contract(contract), [])

            # 9. RC-4A.3.1.A: worktree must be CLEAN after a successful change
            # run. Pre-fix the change commit captured the ephemeral 1-task
            # task-graph.json and the post-run on-disk restore left
            # `D task-graph.json` in `git status -s`, which broke the next
            # change run's worktree-clean preflight.
            status = _git("status", "--porcelain", cwd=project_path).stdout
            self.assertNotIn(
                "task-graph.json", status,
                msg=f"worktree must be clean after change run; got:\n{status}",
            )
            self.assertEqual(
                status.strip(), "",
                msg=f"worktree must have no uncommitted changes; got:\n{status}",
            )

            # 10. RC-4A.3.1.A: HEAD commit's tree must NOT contain task-graph.json
            # (this project had no prior task-graph.json, so the change commit
            # must have been amended to drop it).
            head_files = _git(
                "show", "--name-only", "--pretty=", "HEAD", cwd=project_path,
            ).stdout.strip().splitlines()
            self.assertNotIn(
                "task-graph.json", head_files,
                msg=f"change commit must not include task-graph.json; got: {head_files}",
            )

            # 11. RC-4A.3.1.B: delivery-report Validation section must include
            # eval + promotion + apply rows derived from real artifacts.
            self.assertIn("**eval.build**: passed", md)
            self.assertIn("**eval.test**: passed", md)
            self.assertIn("**promotion**: passed", md)
            self.assertIn("decision=promote", md)
            self.assertIn("**apply**: passed", md)
            self.assertNotIn("(no validation results recorded)", md)

            # 12. RC-4A.3.1.C: validate_applied_change runs over the post-run
            # artifact and returns clean. (CLI surface for this is covered by
            # the cmd_change_validate update; here we exercise the validator
            # directly against the on-disk file the runner just wrote.)
            self.assertEqual(validate_applied_change(applied), [])

    def test_change_run_refuses_dirty_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = _setup_git_project(Path(tmp))
            cr_path = project_path.parent / "change-request.md"
            cr_path.write_text(_VALID_CHANGE_REQUEST, encoding="utf-8")
            created = create_change(project_path, cr_path)

            # Dirty the worktree with a user-owned file
            (project_path / "uncommitted.txt").write_text("dirty\n", encoding="utf-8")

            project = {"id": "project_rc4a2", "name": "rc4a2-demo", "path": str(project_path)}
            with self.assertRaises(RuntimeError) as ctx:
                run_change(
                    project=project,
                    change_id=created.change_id,
                    run_inner_loop=make_fake_inner_loop(project_path),
                )
            self.assertIn("working tree not clean", str(ctx.exception))

    def test_change_run_restores_prior_task_graph(self) -> None:
        """If the project already has a task-graph.json (autonomous mode),
        change-mode must restore it byte-identical after the run AND the
        worktree + HEAD commit tree must reflect the restoration."""
        with tempfile.TemporaryDirectory() as tmp:
            project_path = _setup_git_project(Path(tmp))
            prior_graph = {
                "schema_version": 1,
                "project_title": "prior autonomous graph",
                "overview": "existing",
                "tasks": [{
                    "id": "task-001", "title": "prior", "intent": "prior",
                    "acceptance_criteria": [], "scope_paths": [], "dependencies": [],
                    "status": "completed", "risk": "low", "run_ids": [], "commit": "abc",
                }],
            }
            prior_text = json.dumps(prior_graph, ensure_ascii=False, indent=2) + "\n"
            (project_path / "task-graph.json").write_text(prior_text, encoding="utf-8")
            _git("add", "task-graph.json", cwd=project_path)
            _git("-c", "commit.gpgsign=false", "commit", "-q", "-m", "prior graph", cwd=project_path)

            # SHA of HEAD that owns the prior task-graph.json — what the
            # change commit's tree must agree with for this file.
            prior_blob = _git("rev-parse", "HEAD:task-graph.json", cwd=project_path).stdout.strip()

            cr_path = project_path.parent / "change-request.md"
            cr_path.write_text(_VALID_CHANGE_REQUEST, encoding="utf-8")
            created = create_change(project_path, cr_path)

            project = {"id": "project_rc4a2", "name": "rc4a2-demo", "path": str(project_path)}
            run_change(
                project=project,
                change_id=created.change_id,
                run_inner_loop=make_fake_inner_loop(project_path),
                apply_candidate=apply_selected_candidate,
            )

            restored = json.loads((project_path / "task-graph.json").read_text(encoding="utf-8"))
            self.assertEqual(restored["project_title"], "prior autonomous graph")
            self.assertEqual(restored["tasks"][0]["id"], "task-001")

            # RC-4A.3.1.A: worktree clean post-run.
            status = _git("status", "--porcelain", cwd=project_path).stdout
            self.assertEqual(
                status.strip(), "",
                msg=f"worktree must be clean after change run; got:\n{status}",
            )

            # RC-4A.3.1.A: HEAD commit's task-graph.json blob must match the
            # prior content's blob (same SHA), proving the amend restored it
            # rather than leaving the ephemeral 1-task graph in the change
            # commit.
            new_blob = _git("rev-parse", "HEAD:task-graph.json", cwd=project_path).stdout.strip()
            self.assertEqual(
                new_blob, prior_blob,
                msg="change commit's task-graph.json blob must match prior content's blob",
            )


if __name__ == "__main__":
    unittest.main()
