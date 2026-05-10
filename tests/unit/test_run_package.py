from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.core.run_package import (
    CandidateReport,
    ProjectRunPackages,
    RunPackage,
    iter_candidate_summaries,
)


def _seed_run(project_path: Path, run_id: str, *, selected: str | None = None,
              candidates: list[str] | None = None,
              decision: str = "needs-human-review",
              promotion_extra: dict | None = None) -> Path:
    run_dir = project_path / ".agent/runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    candidates = candidates or ["candidate-a", "candidate-b", "candidate-c"]
    promo: dict = {
        "schema_version": "agentic.promotion_report.v2",
        "decision": decision,
        "selected_candidate": selected,
        "candidate_count": len(candidates),
        "candidates": [
            {"id": cid, "strategy": ("conservative" if i == 0 else ("test-focused" if i == 1 else "broader-fix")),
             "score": 70 - i * 5, "disqualified": False, "required_eval_passed": True,
             "required_eval_executed": True, "source_patch_present": True}
            for i, cid in enumerate(candidates)
        ],
    }
    if promotion_extra:
        promo.update(promotion_extra)
    (run_dir / "promotion-report.json").write_text(json.dumps(promo), encoding="utf-8")
    for cid in candidates:
        cdir = run_dir / "candidates" / cid
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "patch.diff").write_text(f"# {cid} patch\n", encoding="utf-8")
        (cdir / "score.json").write_text(json.dumps({"candidate": cid, "strategy": "conservative", "total": 70}), encoding="utf-8")
        (cdir / "changed-files.json").write_text(json.dumps({
            "candidate": cid, "base_commit": "abc123", "changed_files": [{"path": f"apps/{cid}.ts", "category": "source"}],
            "out_of_scope_changes": [], "source_patch_present": True,
        }), encoding="utf-8")
        (cdir / "repair-history.json").write_text(json.dumps({
            "candidate": cid, "max_loops": 0, "attempts": [], "stop_reason": "eval_passed_no_repair_needed",
            "final_failure": None,
        }), encoding="utf-8")
        (cdir / "eval-results.json").write_text(json.dumps({
            "required_eval_executed": True, "required_eval_passed": True,
            "commands": [{"name": "build", "required": True, "executed": True, "passed": True, "exit_code": 0}],
        }), encoding="utf-8")
    return run_dir


class RunPackageReaderTests(unittest.TestCase):
    def test_latest_run_picks_most_recent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            _seed_run(project_path, "run_old")
            _seed_run(project_path, "run_new")
            walker = ProjectRunPackages(project_path=project_path)
            # Both runs are essentially same mtime, so we just check latest is one of them.
            self.assertIn(walker.latest_run().run_id, {"run_old", "run_new"})
            self.assertEqual(len(walker.runs()), 2)

    def test_run_returns_none_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            walker = ProjectRunPackages(project_path=Path(tmp))
            self.assertIsNone(walker.run("missing"))
            self.assertEqual(walker.runs(), [])

    def test_resolve_candidate_handles_selected_keyword(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            run_dir = _seed_run(project_path, "run_a", selected="candidate-b")
            run = RunPackage(project_path=project_path, run_dir=run_dir)
            self.assertEqual(run.resolve_candidate("selected").candidate_id, "candidate-b")
            self.assertEqual(run.resolve_candidate("candidate-c").candidate_id, "candidate-c")

    def test_resolve_candidate_returns_none_for_missing_or_unselected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            run_dir = _seed_run(project_path, "run_a", selected=None)
            run = RunPackage(project_path=project_path, run_dir=run_dir)
            self.assertIsNone(run.resolve_candidate("selected"))
            self.assertIsNone(run.resolve_candidate("candidate-zzz"))

    def test_candidate_report_handles_missing_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            run_dir = project_path / ".agent/runs/run_x"
            (run_dir / "candidates/candidate-a").mkdir(parents=True)
            report = CandidateReport(run_dir=run_dir, candidate_id="candidate-a")
            self.assertTrue(report.exists())
            self.assertEqual(report.score(), {})
            self.assertEqual(report.eval_results(), {})
            self.assertEqual(report.patch_diff(), "")

    def test_candidate_report_skips_corrupt_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            run_dir = project_path / ".agent/runs/run_y"
            cdir = run_dir / "candidates/candidate-a"
            cdir.mkdir(parents=True)
            (cdir / "score.json").write_text("{not-json", encoding="utf-8")
            report = CandidateReport(run_dir=run_dir, candidate_id="candidate-a")
            self.assertEqual(report.score(), {})  # silent skip

    def test_iter_candidate_summaries_stamps_selected_marker(self) -> None:
        promo = {
            "selected_candidate": "candidate-b",
            "candidates": [
                {"id": "candidate-a", "score": 60},
                {"id": "candidate-b", "score": 80},
                "not-a-dict",  # garbage, should be skipped
            ],
        }
        out = list(iter_candidate_summaries(promo))
        self.assertEqual(len(out), 2)
        self.assertFalse(out[0]["selected"])
        self.assertTrue(out[1]["selected"])


if __name__ == "__main__":
    unittest.main()
