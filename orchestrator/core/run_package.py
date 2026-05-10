"""RunPackageReader — read-side helper over the agentic_project run package.

MVP-3B introduces multi-place artifact reading (CLI list/show/apply, future
dashboard). This module is the single point that knows the on-disk shape:

    <project>/.agent/runs/<run_id>/
        intent-contract.json
        context-pack.json
        eval-harness.json
        task-slices.json
        promotion-report.json
        memory-update.proposed.json
        applied-candidate.json          (only after a successful apply)
        candidates/<candidate_id>/
            patch.diff
            changed-files.json
            score.json
            repair-history.json
            eval-results.json
            run-log.jsonl
            critics/{correctness,regression,security,ux,overfit}.md

All readers handle missing / malformed files defensively — never raise on a
corrupt artifact, return None or {} as appropriate. CLIs that need stricter
behavior (apply gate) check explicitly.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


_CRITIC_FILES = ("correctness.md", "regression.md", "security.md", "ux.md", "overfit.md")


def _safe_load_json(path: Path) -> dict[str, Any] | list[Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _safe_read_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


@dataclass(frozen=True)
class CandidateReport:
    """Read-only view over one candidate's evidence package."""
    run_dir: Path
    candidate_id: str

    @property
    def candidate_dir(self) -> Path:
        return self.run_dir / "candidates" / self.candidate_id

    @property
    def patch_diff_path(self) -> Path:
        return self.candidate_dir / "patch.diff"

    def patch_diff(self) -> str:
        return _safe_read_text(self.patch_diff_path)

    def changed_files(self) -> dict[str, Any]:
        data = _safe_load_json(self.candidate_dir / "changed-files.json")
        return data if isinstance(data, dict) else {}

    def score(self) -> dict[str, Any]:
        data = _safe_load_json(self.candidate_dir / "score.json")
        return data if isinstance(data, dict) else {}

    def repair_history(self) -> dict[str, Any]:
        data = _safe_load_json(self.candidate_dir / "repair-history.json")
        return data if isinstance(data, dict) else {}

    def eval_results(self) -> dict[str, Any]:
        data = _safe_load_json(self.candidate_dir / "eval-results.json")
        return data if isinstance(data, dict) else {}

    def critic_summary(self) -> dict[str, str]:
        """Return {critic_name: markdown_text} for each critic file present.
        Empty string if a particular critic file is missing."""
        critics_dir = self.candidate_dir / "critics"
        return {name: _safe_read_text(critics_dir / name) for name in _CRITIC_FILES}

    @property
    def strategy(self) -> str:
        return str(self.score().get("strategy") or "")

    def exists(self) -> bool:
        return self.candidate_dir.is_dir()


@dataclass(frozen=True)
class RunPackage:
    """Read-only view over one agentic_project run package."""
    project_path: Path
    run_dir: Path

    @property
    def run_id(self) -> str:
        return self.run_dir.name

    def promotion_report(self) -> dict[str, Any]:
        data = _safe_load_json(self.run_dir / "promotion-report.json")
        return data if isinstance(data, dict) else {}

    def intent_contract(self) -> dict[str, Any]:
        data = _safe_load_json(self.run_dir / "intent-contract.json")
        return data if isinstance(data, dict) else {}

    def applied_candidate(self) -> dict[str, Any] | None:
        data = _safe_load_json(self.run_dir / "applied-candidate.json")
        return data if isinstance(data, dict) else None

    def candidate_ids(self) -> list[str]:
        candidates_root = self.run_dir / "candidates"
        if not candidates_root.is_dir():
            return []
        return sorted(p.name for p in candidates_root.iterdir() if p.is_dir())

    def candidates(self) -> list[CandidateReport]:
        return [CandidateReport(run_dir=self.run_dir, candidate_id=cid) for cid in self.candidate_ids()]

    def candidate(self, candidate_id: str) -> CandidateReport | None:
        report = CandidateReport(run_dir=self.run_dir, candidate_id=candidate_id)
        return report if report.exists() else None

    def selected_candidate(self) -> CandidateReport | None:
        promo = self.promotion_report()
        selected_id = promo.get("selected_candidate")
        if not selected_id:
            return None
        return self.candidate(str(selected_id))

    def resolve_candidate(self, requested: str) -> CandidateReport | None:
        """Resolve `--candidate <id|selected>` semantics.

        Returns None if the requested candidate cannot be resolved (e.g.
        "selected" but no selected_candidate, or unknown id).
        """
        if requested == "selected":
            return self.selected_candidate()
        return self.candidate(requested)

    def exists(self) -> bool:
        return self.run_dir.is_dir()


@dataclass(frozen=True)
class ProjectRunPackages:
    """Walks `.agent/runs/*` for one project."""
    project_path: Path

    def _runs_root(self) -> Path:
        return self.project_path / ".agent" / "runs"

    def runs(self) -> list[RunPackage]:
        root = self._runs_root()
        if not root.is_dir():
            return []
        # Most-recent first by directory mtime.
        run_dirs = [p for p in root.iterdir() if p.is_dir()]
        run_dirs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return [RunPackage(project_path=self.project_path, run_dir=run_dir) for run_dir in run_dirs]

    def latest_run(self) -> RunPackage | None:
        runs = self.runs()
        return runs[0] if runs else None

    def run(self, run_id: str) -> RunPackage | None:
        candidate = self._runs_root() / run_id
        if not candidate.is_dir():
            return None
        return RunPackage(project_path=self.project_path, run_dir=candidate)


class ApplyGateRefused(Exception):
    """Raised by `apply_selected_candidate` when one of the Apply Gate rules
    fails. The message lists every failing rule, one per line."""


def apply_selected_candidate(
    *,
    project_path: Path,
    run_dir: Path,
    selected_candidate: str | None = None,
    human_override: bool = False,
) -> dict[str, Any]:
    """Programmatic Apply Gate (mirrors the CLI semantics, no sys.exit).

    Used by the autonomous controller to apply a winner without going
    through the CLI subprocess. Returns the `applied-candidate.json` dict
    on success, raises `ApplyGateRefused` with a multi-line message
    otherwise. Writes `applied-candidate.json` on success.

    MVP-4D: when `human_override=True`, the "decision must be promote"
    gate is bypassed (a Human Review Queue approval explicitly chose to
    apply a needs-human-review / needs-more-context candidate). EVERY
    other safety gate still runs — a human override does NOT skip patch.diff
    presence, source_patch_present, no_out_of_scope, HEAD/base match,
    worktree clean, `git apply --check`, or the re-apply guard.
    """
    import hashlib
    import subprocess
    import json as _json

    run = RunPackage(project_path=project_path, run_dir=run_dir)
    promotion = run.promotion_report()
    candidate_arg = selected_candidate or "selected"
    candidate = run.resolve_candidate(candidate_arg)
    if candidate is None:
        raise ApplyGateRefused(f"Candidate not resolvable: {candidate_arg}")

    failures: list[str] = []
    if str(promotion.get("schema_version") or "") != "agentic.promotion_report.v2":
        failures.append("promotion-report.schema_version is not v2")
    if not promotion.get("selected_candidate"):
        failures.append("promotion-report.selected_candidate is null")
    patch_path = candidate.patch_diff_path
    patch_text = candidate.patch_diff()
    if not patch_path.exists():
        failures.append(f"patch.diff missing: {patch_path}")
    elif not patch_text.strip():
        failures.append(f"patch.diff empty: {patch_path}")
    changed_files = candidate.changed_files()
    if not changed_files:
        failures.append("changed-files.json missing or unreadable")
    score = candidate.score()
    if not bool(score.get("source_patch_present")):
        failures.append("score.source_patch_present is false")
    out_of_scope = changed_files.get("out_of_scope_changes") or []
    if out_of_scope:
        failures.append(f"candidate has {len(out_of_scope)} out_of_scope_changes")
    base_commit = str(changed_files.get("base_commit") or "")
    if not base_commit or base_commit == "unknown":
        failures.append("changed-files has no base_commit")
    else:
        try:
            head = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                cwd=project_path, capture_output=True, text=True, check=False,
            )
            head_short = head.stdout.strip() if head.returncode == 0 else ""
        except FileNotFoundError:
            head_short = ""
        if not head_short:
            failures.append("project is not a git repository")
        elif head_short != base_commit:
            failures.append(f"HEAD `{head_short}` != base_commit `{base_commit}`")

    # Worktree clean (ignoring `.agent/` and controller-owned files at the
    # project root). The autonomous controller mutates `task-graph.json`
    # AFTER each task commit (to record commit hash + status); from the
    # next task's apply gate, that update LOOKS like a dirty worktree but
    # is actually expected runtime bookkeeping. We import the canonical
    # set from `autonomous.py` so manual `agent-studio agentic-candidates
    # apply` and the controller's `apply_candidate` cannot drift apart
    # (RC-1.1 cleanup of audit Code Risks #1).
    from orchestrator.core.autonomous import AUTONOMOUS_OWNED_PATHS
    try:
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_path, capture_output=True, text=True, check=False,
        )
        if status_proc.returncode == 0:
            for line in status_proc.stdout.splitlines():
                if not line:
                    continue
                path = line[3:]
                if " -> " in path:
                    path = path.split(" -> ", 1)[1]
                path = path.strip().strip('"')
                if path.startswith(".agent/") or path == ".agent":
                    continue
                if path in AUTONOMOUS_OWNED_PATHS:
                    continue
                failures.append(f"working tree not clean: `{path}` is uncommitted")
                break
    except FileNotFoundError:
        pass

    if patch_path.exists() and patch_text.strip():
        check = subprocess.run(
            ["git", "apply", "--check", str(patch_path)],
            cwd=project_path, capture_output=True, text=True, check=False,
        )
        if check.returncode != 0:
            failures.append(f"git apply --check failed: {check.stderr.strip() or check.stdout.strip()}")

    if str(promotion.get("decision") or "") != "promote" and not human_override:
        failures.append(f"promotion-report.decision is `{promotion.get('decision')}` (require promote, or use human override via reviews approve)")

    applied_record = run.applied_candidate()
    if applied_record:
        failures.append("this run has already been applied (re-apply guard)")

    if failures:
        raise ApplyGateRefused("Apply Gate refused:\n  - " + "\n  - ".join(failures))

    apply = subprocess.run(
        ["git", "apply", str(patch_path)],
        cwd=project_path, capture_output=True, text=True, check=False,
    )
    if apply.returncode != 0:
        raise ApplyGateRefused(f"git apply failed: {apply.stderr.strip() or apply.stdout.strip()}")

    head_after = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=project_path, capture_output=True, text=True, check=False,
    )
    applied_to_commit = head_after.stdout.strip() or base_commit

    from datetime import datetime, timezone
    record = {
        "schema_version": 1,
        "run_id": run.run_id,
        "candidate": candidate.candidate_id,
        "strategy": candidate.strategy,
        "decision_at_apply_time": promotion.get("decision"),
        "human_override": bool(human_override),
        "project_id": None,  # filled in by caller if needed
        "base_commit": base_commit,
        "applied_to_commit": applied_to_commit,
        "patch_sha256": hashlib.sha256(patch_text.encode("utf-8")).hexdigest(),
        "dry_run": False,
        "applied": True,
        "changed_files": [
            str(entry.get("path"))
            for entry in (changed_files.get("changed_files") or [])
            if isinstance(entry, dict) and entry.get("path")
        ],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    record_path = run_dir / "applied-candidate.json"
    record_path.write_text(_json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return record


def iter_candidate_summaries(promotion: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Yield each `candidates[]` entry from a promotion-report dict, with
    a `selected` boolean stamped on. Tolerates a missing `candidates` key."""
    selected_id = promotion.get("selected_candidate")
    for summary in promotion.get("candidates") or []:
        if not isinstance(summary, dict):
            continue
        out = dict(summary)
        out["selected"] = (out.get("id") == selected_id) if selected_id else False
        yield out
