from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import json
from pathlib import Path

from tests.unit.test_prd_manual_agent import _valid_payload


class CliFlowTests(unittest.TestCase):
    def test_cli_new_run_approve_flow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            init = _cli(root, "init")
            new = _cli(root, "new", "Build a todo app")
            run = _cli(root, "run", "software_project")
            status = _cli(root, "status")
            approve = _cli(root, "approve", "prd")
            final = _cli(root, "status")

            self.assertIn("Initialized Local Agent Dev Studio", init.stdout)
            self.assertIn("Created project", new.stdout)
            self.assertIn("Status: needs_approval", run.stdout)
            self.assertIn("Pending approvals:", status.stdout)
            self.assertIn("Status: completed", approve.stdout)
            self.assertIn("Run status: completed", final.stdout)

    def test_cli_manual_codex_prd_import_can_approve_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            response = root / "prd-response.json"
            response.write_text(json.dumps(_valid_payload(), ensure_ascii=False), encoding="utf-8")

            _cli(root, "init")
            _cli(root, "new", "Build a personal expense tracker")
            run = _cli(root, "run", "software_project")
            prepare = _cli(root, "prd", "prepare")
            imported = _cli(root, "prd", "import", str(response))
            final = _cli(root, "status")

            self.assertIn("Status: needs_approval", run.stdout)
            self.assertIn("Prepared manual Codex PRD prompt pack", prepare.stdout)
            self.assertIn("PRD validation: ok", imported.stdout)
            self.assertIn("PRD gate approved", imported.stdout)
            self.assertIn("Run status: completed", final.stdout)

    def test_cli_prd_research_mock_then_prepare(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            _cli(root, "init")
            _cli(root, "new", "Build a personal expense tracker")
            _cli(root, "run", "software_project")
            research = _cli(root, "prd", "research", "--mock", "--max-queries", "2", "--results-per-query", "2")
            research_v2 = _cli(root, "prd", "research-v2")
            prepare = _cli(root, "prd", "prepare")

            self.assertIn("Research provider: MockSearchProvider", research.stdout)
            self.assertIn("Sources: 4", research.stdout)
            self.assertIn("Reference products:", research.stdout)
            self.assertIn("Generated PRD Research v2 artifacts", research_v2.stdout)
            self.assertIn("Prepared manual Codex PRD prompt pack", prepare.stdout)

    def test_cli_prd_draft_import_can_complete_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            _cli(root, "init")
            _cli(root, "new", "Build a freelance invoice tracker")
            _cli(root, "run", "software_project")
            _cli(root, "prd", "research", "--mock", "--max-queries", "2", "--results-per-query", "2")
            draft = _cli(root, "prd", "draft", "--import")
            score = _cli(root, "prd", "score")
            critique = _cli(root, "prd", "critique")
            product_fit = _cli(root, "prd", "product-fit")
            team_review = _cli(root, "prd", "team-review")
            design_draft = _cli(root, "design", "draft")
            design_critique = _cli(root, "design", "critique")
            # `design directions` was removed when the v0 paid API was dropped.
            architecture = _cli(root, "architecture", "draft")
            implementation = _cli(root, "implementation", "draft")
            build_review = _cli(root, "prd", "build-review")
            teams_plan = _cli(root, "teams", "plan")
            design_team = _cli(root, "design", "team")
            developer_team = _cli(root, "implementation", "team")
            teams_review = _cli(root, "teams", "review")
            final = _cli(root, "status")

            self.assertIn("PRD validation: ok", draft.stdout)
            self.assertIn("PRD gate approved from valid generated draft", draft.stdout)
            self.assertIn("PRD score:", score.stdout)
            self.assertIn("Status: pass", score.stdout)
            self.assertIn("Generated PRD critique", critique.stdout)
            self.assertIn("Status: pass", critique.stdout)
            self.assertIn("Product-fit score:", product_fit.stdout)
            self.assertIn("Status: pass", product_fit.stdout)
            self.assertIn("Generated PRD agent team review", team_review.stdout)
            self.assertIn("Generated design draft", design_draft.stdout)
            self.assertIn("Design score:", design_critique.stdout)
            self.assertIn("Status: pass", design_critique.stdout)
            self.assertIn("Generated architecture draft", architecture.stdout)
            self.assertIn("Generated implementation draft", implementation.stdout)
            self.assertIn("Status: completed", implementation.stdout)
            self.assertIn("Post-build product review:", build_review.stdout)
            self.assertIn("Downstream team plan:", build_review.stdout)
            self.assertIn("Generated downstream team plans", teams_plan.stdout)
            self.assertIn("Generated UI Product Team package", design_team.stdout)
            self.assertIn("Design contract JSON:", design_team.stdout)
            self.assertIn("Generated Developer Team package", developer_team.stdout)
            self.assertIn("Implementation contract JSON:", developer_team.stdout)
            self.assertIn("Generated team system review", teams_review.stdout)
            self.assertIn("Maturity JSON:", teams_review.stdout)
            self.assertIn("Run status: completed", final.stdout)

    def test_cli_prd_options_select_then_draft_import(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            _cli(root, "init")
            _cli(root, "new", "Build a freelance invoice tracker")
            _cli(root, "run", "software_project")
            _cli(root, "prd", "research", "--mock", "--max-queries", "2", "--results-per-query", "2")
            options = _cli(root, "prd", "options")
            selected = _cli(root, "prd", "select", "option-b", "--notes", "Use invoice-ready workflow.")
            prepared = _cli(root, "prd", "council", "--prepare")
            council = _cli(root, "prd", "council")
            draft = _cli(root, "prd", "draft", "--import")
            final = _cli(root, "status")

            self.assertIn("Generated PRD options", options.stdout)
            self.assertIn("Recommended: option-b", options.stdout)
            self.assertIn("Selected PRD option: option-b", selected.stdout)
            self.assertIn("Prepared manual PRD council prompt pack", prepared.stdout)
            self.assertIn("Generated PRD council outputs", council.stdout)
            self.assertIn("Roles: 6", council.stdout)
            self.assertIn("PRD validation: ok", draft.stdout)
            self.assertIn("Run status: completed", final.stdout)

    def test_cli_agentic_project_run_writes_runtime_package(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            _cli(root, "init")
            _cli(root, "new", "Build a verified portfolio builder")
            run = _cli(root, "run", "agentic_project")
            final = _cli(root, "status")

            self.assertIn("Workflow: agentic_project", run.stdout)
            self.assertIn("Decision: needs-more-context", run.stdout)
            self.assertIn("Run package:", run.stdout)
            self.assertIn("Run status: completed", final.stdout)

    def test_agentic_abandonments_list_empty_and_seeded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _cli(root, "init")
            new = _cli(root, "new", "Test abandonments CLI")
            project_id = _extract_project_id(new.stdout)

            empty = _cli(root, "agentic-abandonments", "list", "--project", project_id)
            self.assertIn("No abandonment records yet", empty.stdout)

            # Seed the JSONL log directly to test the read/format path.
            from orchestrator.config import resolve_paths
            paths = resolve_paths(root)
            db = paths.db_path  # ensure paths is materialized
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(paths)
            project_path = Path(engine.require_project(project_id)["path"])
            log_dir = project_path / ".agent"
            log_dir.mkdir(parents=True, exist_ok=True)
            log_path = log_dir / "agentic-abandonments.jsonl"
            records = [
                {"schema_version": "agentic.abandonment_record.v1",
                 "run_id": "run_a", "timestamp_utc": "2026-05-09T10:00:00+00:00",
                 "intent_goal": "demo", "candidate": "candidate-a", "decision": "abandoned",
                 "patch_worker": "codex", "stop_reason": "max_loops_exhausted",
                 "attempt_count": 3, "max_loops": 3,
                 "final_failure": {"failure_type": "type_error", "subtype": "typescript"}},
                {"schema_version": "agentic.abandonment_record.v1",
                 "run_id": "run_b", "timestamp_utc": "2026-05-09T11:00:00+00:00",
                 "intent_goal": "demo", "candidate": "candidate-a", "decision": "abandoned",
                 "patch_worker": "codex", "stop_reason": "max_loops_exhausted",
                 "attempt_count": 3, "max_loops": 3,
                 "final_failure": {"failure_type": "type_error", "subtype": "typescript"}},
            ]
            log_path.write_text(
                "\n".join(json.dumps(record) for record in records) + "\n",
                encoding="utf-8",
            )

            seeded = _cli(root, "agentic-abandonments", "list", "--project", project_id)
            self.assertIn("Total: 2 abandonment record(s)", seeded.stdout)
            self.assertIn("type_error", seeded.stdout)
            self.assertIn("max_loops_exhausted", seeded.stdout)
            self.assertIn("codex / type_error: 2", seeded.stdout)
            self.assertIn("[pattern: gate would warn]", seeded.stdout)

            # JSON mode round-trips the records faithfully.
            seeded_json = _cli(root, "agentic-abandonments", "list", "--project", project_id, "--json")
            parsed = json.loads(seeded_json.stdout)
            self.assertEqual(len(parsed), 2)
            self.assertEqual(parsed[0]["run_id"], "run_a")


class AgenticCandidatesCliTests(unittest.TestCase):
    """MVP-3B: 12 acceptance tests for agentic-candidates list/show/apply."""

    def _seed_promotion(self, project_path: Path, *, decision: str = "promote",
                        selected: str | None = "candidate-b",
                        base_commit: str | None = None,
                        out_of_scope: list[dict] | None = None,
                        patch_diff: str | None = None,
                        candidates: list[str] | None = None,
                        ) -> Path:
        """Seed a complete .agent/runs/run_t/* package shaped like an MVP-3A run."""
        run_dir = project_path / ".agent/runs/run_t"
        run_dir.mkdir(parents=True, exist_ok=True)
        candidates = candidates or ["candidate-a", "candidate-b", "candidate-c"]

        candidate_summaries = []
        for i, cid in enumerate(candidates):
            disqualified = (cid == "candidate-a")  # a is disqualified to make b the winner
            candidate_summaries.append({
                "id": cid,
                "strategy": ["conservative", "test-focused", "broader-fix"][i % 3],
                "source_patch_present": True,
                "required_eval_executed": True,
                "required_eval_passed": (cid != "candidate-a"),
                "diff_within_scope": (cid != "candidate-a") if not out_of_scope else True,
                "no_critical_security_finding": True,
                "disqualified": disqualified,
                "score": 90 if cid == "candidate-b" else (70 if cid == "candidate-c" else 35),
                "stop_reason": "eval_passed_no_repair_needed",
                "repair_attempts": 0,
                "final_failure": None,
            })

        promo = {
            "schema_version": "agentic.promotion_report.v2",
            "candidate": selected or "candidate-a",
            "selected_candidate": selected,
            "candidate_count": len(candidates),
            "decision": decision,
            "candidates": candidate_summaries,
            "candidate_diversity": {"method": "changed_file_jaccard_distance", "average": 0.5, "pairs": []},
            "hard_gates": {"required_eval_passed": True},
            "eval": {},
            "repair": {},
            "soft_scores": {"candidate_diversity": 0.5},
            "remaining_risks": [],
            "abandonment_pattern": {"patch_worker": "codex", "failure_type": None, "prior_abandonments": 0, "warning_emitted": False},
        }
        (run_dir / "promotion-report.json").write_text(json.dumps(promo), encoding="utf-8")

        for cid in candidates:
            cdir = run_dir / "candidates" / cid
            cdir.mkdir(parents=True, exist_ok=True)
            cdiff = patch_diff if (patch_diff is not None and cid == (selected or "candidate-a")) else f"# {cid} patch\n"
            (cdir / "patch.diff").write_text(cdiff, encoding="utf-8")
            (cdir / "score.json").write_text(json.dumps({
                "candidate": cid, "strategy": "conservative", "total": 70,
                "source_patch_present": True,
                "diff_within_scope": True,
                "components": {"required_eval": 40, "scope_safety": 10, "critic_risk": 10},
                "penalties": {"repeated_failure_type": 0},
            }), encoding="utf-8")
            (cdir / "changed-files.json").write_text(json.dumps({
                "candidate": cid,
                "base_commit": base_commit or "abc123",
                "changed_files": [{"path": f"apps/web/{cid}.ts", "category": "source", "change_type": "added"}],
                "out_of_scope_changes": (out_of_scope or []) if cid == (selected or "candidate-a") else [],
                "source_patch_present": True,
            }), encoding="utf-8")
            (cdir / "repair-history.json").write_text(json.dumps({
                "candidate": cid, "max_loops": 0, "attempts": [],
                "stop_reason": "eval_passed_no_repair_needed", "final_failure": None,
            }), encoding="utf-8")
            (cdir / "eval-results.json").write_text(json.dumps({
                "required_eval_executed": True, "required_eval_passed": True,
                "commands": [{"name": "build", "required": True, "executed": True, "passed": True, "exit_code": 0}],
            }), encoding="utf-8")
            (cdir / "critics").mkdir(exist_ok=True)
            for cname in ("correctness", "regression", "security", "ux", "overfit"):
                (cdir / "critics" / f"{cname}.md").write_text(f"# {cname.title()} Critic\n\n## Findings\n- baseline\n", encoding="utf-8")
        return run_dir

    def _make_git_repo(self, project_path: Path) -> str:
        """Initialize a git repo at project_path with one committed file. Returns short HEAD."""
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project_path, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=project_path, check=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=project_path, check=True)
        (project_path / "README.md").write_text("hello\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=project_path, check=True)
        subprocess.run(["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", "init"], cwd=project_path, check=True)
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=project_path, capture_output=True, text=True, check=True)
        return head.stdout.strip()

    def _setup_project(self, root: Path, name: str = "MVP-3B test project") -> str:
        _cli(root, "init")
        new = _cli(root, "new", name)
        return _extract_project_id(new.stdout)

    # ------------------------------------------------------------------
    # list
    # ------------------------------------------------------------------
    def test_list_candidates_shows_all_candidates_and_selected_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            self._seed_promotion(project_path)
            out = _cli(root, "agentic-candidates", "list", "--project", project_id)
            self.assertIn("candidate-a", out.stdout)
            self.assertIn("candidate-b", out.stdout)
            self.assertIn("candidate-c", out.stdout)
            # candidate-b is the only `selected` row; the trailing column is
            # padded so use word-boundary matching rather than endswith.
            import re as _re
            yes_lines = [ln for ln in out.stdout.splitlines() if "candidate-b" in ln and _re.search(r"\byes\b", ln)]
            no_b_lines = [ln for ln in out.stdout.splitlines() if "candidate-a" in ln and _re.search(r"\bno\b", ln)]
            self.assertEqual(len(yes_lines), 1, out.stdout)
            self.assertEqual(len(no_b_lines), 1, out.stdout)

    def test_list_candidates_json_includes_scores_strategies_and_selected_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            self._seed_promotion(project_path)
            out = _cli(root, "agentic-candidates", "list", "--project", project_id, "--json")
            payload = json.loads(out.stdout)
            self.assertEqual(payload["selected_candidate"], "candidate-b")
            self.assertEqual(payload["candidate_count"], 3)
            ids = [c["id"] for c in payload["candidates"]]
            self.assertEqual(ids, ["candidate-a", "candidate-b", "candidate-c"])
            self.assertEqual([c["strategy"] for c in payload["candidates"]],
                             ["conservative", "test-focused", "broader-fix"])
            selected_flags = {c["id"]: c["selected"] for c in payload["candidates"]}
            self.assertTrue(selected_flags["candidate-b"])
            self.assertFalse(selected_flags["candidate-a"])

    # ------------------------------------------------------------------
    # show
    # ------------------------------------------------------------------
    def test_show_candidate_selected_resolves_to_promotion_selected_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            self._seed_promotion(project_path, selected="candidate-b")
            out = _cli(root, "agentic-candidates", "show", "--project", project_id, "--candidate", "selected")
            self.assertIn("Candidate: candidate-b", out.stdout)
            self.assertIn("Selected: yes", out.stdout)

    def test_show_candidate_prints_patch_path_eval_repair_and_critic_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            self._seed_promotion(project_path)
            out = _cli(root, "agentic-candidates", "show", "--project", project_id, "--candidate", "candidate-b")
            self.assertIn("== Patch ==", out.stdout)
            self.assertIn("patch.diff", out.stdout)
            self.assertIn("== Eval ==", out.stdout)
            self.assertIn("build: passed", out.stdout)
            self.assertIn("== Repair ==", out.stdout)
            self.assertIn("Stop reason: eval_passed_no_repair_needed", out.stdout)
            self.assertIn("== Hard gates ==", out.stdout)

    # ------------------------------------------------------------------
    # apply --dry-run
    # ------------------------------------------------------------------
    def test_apply_dry_run_selected_candidate_passes_when_patch_is_clean(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            head_short = self._make_git_repo(project_path)
            # Patch that creates a new file.
            patch = (
                "diff --git a/new_file.txt b/new_file.txt\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/new_file.txt\n"
                "@@ -0,0 +1 @@\n"
                "+hello world\n"
            )
            self._seed_promotion(project_path, base_commit=head_short, patch_diff=patch)
            out = _cli(root, "agentic-candidates", "apply", "--project", project_id, "--candidate", "selected", "--dry-run")
            self.assertIn("Apply Gate passed all 10 checks", out.stdout)
            self.assertIn("Dry-run only", out.stdout)
            # No applied-candidate.json should be written.
            self.assertFalse((project_path / ".agent/runs/run_t/applied-candidate.json").exists())

    def test_apply_yes_applies_patch_to_clean_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            head_short = self._make_git_repo(project_path)
            patch = (
                "diff --git a/new_file.txt b/new_file.txt\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/new_file.txt\n"
                "@@ -0,0 +1 @@\n"
                "+hello world\n"
            )
            self._seed_promotion(project_path, base_commit=head_short, patch_diff=patch)
            out = _cli(root, "agentic-candidates", "apply", "--project", project_id, "--candidate", "selected", "--yes")
            self.assertIn("Patch applied", out.stdout)
            self.assertTrue((project_path / "new_file.txt").exists())
            self.assertEqual((project_path / "new_file.txt").read_text(encoding="utf-8"), "hello world\n")

    # ------------------------------------------------------------------
    # apply rejection cases
    # ------------------------------------------------------------------
    def _expect_apply_failure(self, root: Path, project_id: str, *, contains: str) -> None:
        with self.assertRaises(subprocess.CalledProcessError) as cm:
            _cli(root, "agentic-candidates", "apply", "--project", project_id, "--candidate", "selected", "--dry-run")
        out = (cm.exception.stdout or "") + (cm.exception.stderr or "")
        self.assertIn("Apply Gate REJECTED", out)
        self.assertIn(contains, out)

    def test_apply_rejects_when_promotion_decision_is_not_promote(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            head_short = self._make_git_repo(project_path)
            self._seed_promotion(project_path, decision="needs-human-review", base_commit=head_short)
            self._expect_apply_failure(root, project_id, contains="decision is `needs-human-review`")

    def test_apply_rejects_when_patch_diff_is_missing_or_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            head_short = self._make_git_repo(project_path)
            self._seed_promotion(project_path, base_commit=head_short, patch_diff="")
            self._expect_apply_failure(root, project_id, contains="empty")

    def test_apply_rejects_when_current_head_does_not_match_base_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            self._make_git_repo(project_path)
            self._seed_promotion(project_path, base_commit="deadbee")  # arbitrary, won't match
            self._expect_apply_failure(root, project_id, contains="does not match candidate base_commit")

    def test_apply_rejects_when_worktree_is_dirty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            head_short = self._make_git_repo(project_path)
            (project_path / "uncommitted.txt").write_text("dirty", encoding="utf-8")
            self._seed_promotion(project_path, base_commit=head_short)
            self._expect_apply_failure(root, project_id, contains="working tree is not clean")

    def test_apply_rejects_out_of_scope_candidate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            head_short = self._make_git_repo(project_path)
            self._seed_promotion(
                project_path, base_commit=head_short,
                out_of_scope=[{"path": "scripts/deploy.sh"}],
            )
            self._expect_apply_failure(root, project_id, contains="out_of_scope_changes")

    def test_apply_yes_refuses_when_run_already_applied(self) -> None:
        """MVP-3C re-apply guard: a run with applied-candidate.json on disk
        cannot be applied again. Dry-run is still allowed."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            head_short = self._make_git_repo(project_path)
            patch = (
                "diff --git a/new_file.txt b/new_file.txt\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/new_file.txt\n"
                "@@ -0,0 +1 @@\n"
                "+hello world\n"
            )
            self._seed_promotion(project_path, base_commit=head_short, patch_diff=patch)
            # First apply succeeds.
            _cli(root, "agentic-candidates", "apply", "--project", project_id, "--candidate", "selected", "--yes")
            self.assertTrue((project_path / ".agent/runs/run_t/applied-candidate.json").exists())
            # Second --yes apply must be refused.
            with self.assertRaises(subprocess.CalledProcessError) as cm:
                _cli(root, "agentic-candidates", "apply", "--project", project_id, "--candidate", "selected", "--yes")
            out = (cm.exception.stdout or "") + (cm.exception.stderr or "")
            self.assertIn("already been applied", out)
            # Dry-run, however, must still be allowed (it's pure inspection).
            # Need to revert the working tree first so the patch can apply
            # cleanly under --check; just remove the file.
            (project_path / "new_file.txt").unlink()
            dry = _cli(root, "agentic-candidates", "apply", "--project", project_id, "--candidate", "selected", "--dry-run")
            self.assertIn("Apply Gate passed", dry.stdout)

    def test_apply_writes_applied_candidate_json_after_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id = self._setup_project(root)
            from orchestrator.config import resolve_paths
            from orchestrator.core.run_manager import create_engine
            engine = create_engine(resolve_paths(root))
            project_path = Path(engine.require_project(project_id)["path"])
            head_short = self._make_git_repo(project_path)
            patch = (
                "diff --git a/new_file.txt b/new_file.txt\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/new_file.txt\n"
                "@@ -0,0 +1 @@\n"
                "+hello world\n"
            )
            self._seed_promotion(project_path, base_commit=head_short, patch_diff=patch)
            _cli(root, "agentic-candidates", "apply", "--project", project_id, "--candidate", "selected", "--yes")
            applied_path = project_path / ".agent/runs/run_t/applied-candidate.json"
            self.assertTrue(applied_path.exists())
            record = json.loads(applied_path.read_text(encoding="utf-8"))
            self.assertEqual(record["candidate"], "candidate-b")
            self.assertEqual(record["decision_at_apply_time"], "promote")
            self.assertEqual(record["base_commit"], head_short)
            self.assertTrue(record["applied"])
            self.assertFalse(record["dry_run"])
            self.assertIn("patch_sha256", record)
            self.assertEqual(record["changed_files"], ["apps/web/candidate-b.ts"])


class AgenticRunsCliTests(unittest.TestCase):
    """MVP-3C: agent-studio agentic-runs list / show."""

    def _setup_project_with_run(self, root: Path) -> tuple[str, Path, str]:
        _cli(root, "init")
        new = _cli(root, "new", "MVP-3C runs cli")
        project_id = _extract_project_id(new.stdout)
        from orchestrator.config import resolve_paths
        from orchestrator.core.run_manager import create_engine
        from orchestrator.core.agentic_runtime import AgenticProjectRuntime
        engine = create_engine(resolve_paths(root))
        project_path = Path(engine.require_project(project_id)["path"])
        (project_path / "apps/web").mkdir(parents=True, exist_ok=True)
        (project_path / "apps/web/index.html").write_text("<html></html>", encoding="utf-8")
        result = AgenticProjectRuntime(engine.db).run(project=engine.require_project(project_id))
        return project_id, project_path, result.run_id

    def test_runs_list_shows_runs_with_no_runs_message_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _cli(root, "init")
            new = _cli(root, "new", "MVP-3C empty runs")
            project_id = _extract_project_id(new.stdout)
            out = _cli(root, "agentic-runs", "list", "--project", project_id)
            self.assertIn("No agentic_project runs yet", out.stdout)

    def test_runs_list_table_includes_run_id_decision_and_applied_marker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path, run_id = self._setup_project_with_run(root)
            out = _cli(root, "agentic-runs", "list", "--project", project_id)
            self.assertIn(run_id[:18], out.stdout)
            self.assertIn("needs-human-revi", out.stdout)  # truncated decision
            self.assertIn("applied", out.stdout)
            # No applied-candidate.json yet → applied=no
            self.assertIn(" no ", out.stdout)

    def test_runs_list_json_contains_runs_array(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path, run_id = self._setup_project_with_run(root)
            out = _cli(root, "agentic-runs", "list", "--project", project_id, "--json")
            payload = json.loads(out.stdout)
            self.assertEqual(payload["project_id"], project_id)
            self.assertGreaterEqual(payload["run_count"], 1)
            run_ids = [r["run_id"] for r in payload["runs"]]
            self.assertIn(run_id, run_ids)
            # Each entry must have decision, selected_candidate, applied bool.
            entry = payload["runs"][0]
            for key in ("run_id", "decision", "selected_candidate", "applied", "candidate_count"):
                self.assertIn(key, entry)
            self.assertIsInstance(entry["applied"], bool)

    def test_runs_show_summarizes_intent_candidates_and_apply_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, project_path, run_id = self._setup_project_with_run(root)
            out = _cli(root, "agentic-runs", "show", "--project", project_id, "--run", run_id)
            self.assertIn(f"Run: {run_id}", out.stdout)
            self.assertIn("Intent goal:", out.stdout)
            self.assertIn("== Candidates ==", out.stdout)
            self.assertIn("candidate-a", out.stdout)
            self.assertIn("candidate-b", out.stdout)
            self.assertIn("candidate-c", out.stdout)
            self.assertIn("== Promotion ==", out.stdout)
            self.assertIn("== Apply state ==", out.stdout)
            self.assertIn("applied: no", out.stdout)

    def test_runs_show_rejects_unknown_run_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            project_id, _, _ = self._setup_project_with_run(root)
            with self.assertRaises(subprocess.CalledProcessError) as cm:
                _cli(root, "agentic-runs", "show", "--project", project_id, "--run", "run_does_not_exist")
            err = (cm.exception.stdout or "") + (cm.exception.stderr or "")
            self.assertIn("Run not found", err)


def _extract_project_id(stdout: str) -> str:
    # `agent-studio new` prints something like: "Created project project_xxxxxxxxxx"
    for token in stdout.split():
        if token.startswith("project_"):
            return token.strip().rstrip(":,;")
    raise AssertionError(f"Could not find project id in: {stdout!r}")


def _cli(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    # These tests verify orchestrator plumbing end-to-end. They must stay fast
    # and deterministic, so we force the stub artifact path rather than the
    # LLM path (LLM calls would hit real CLIs in some test environments).
    env = {**os.environ, "LOCALAGENTS_FORCE_STUB": "1", "LOCALAGENTS_QUIET": "1"}
    return subprocess.run(
        [sys.executable, "-m", "orchestrator.cli", "--root", str(root), *args],
        cwd=Path(__file__).resolve().parents[2],
        text=True,
        capture_output=True,
        check=True,
        env=env,
    )


if __name__ == "__main__":
    unittest.main()
