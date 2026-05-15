"""RC-4A.2: change-mode runner — drives one change end-to-end.

Bridges the change-mode foundation (RC-4A.1: change-request parser,
repo onboarding, change-contract, delivery-report renderer, CLI) into
the existing autonomous machinery (AutonomousController, Promotion
Gate, Apply Gate, commit_task, review queue).

Design constraints
------------------
- 1 task per change. We build a single-task task-graph from the change
  contract and hand it to AutonomousController.advance_one_task. The
  controller's existing branch / commit / apply / review-queue paths
  do everything else.
- No refactor of autonomous.py beyond the additive Change-Id trailer
  fields on commit_task (RC-4A.2.3).
- The project's existing task-graph.json (autonomous mode) is preserved
  on disk: we back it up, swap our 1-task graph in for the controller
  call, and restore the original (or remove it if there wasn't one)
  unconditionally afterward.
- applied-change.json (schema `agentic.applied_change.v1`) and
  delivery-report.md are written under the change dir
  (`.agent/changes/<change_id>/`), NOT under the session dir, so a
  change session's deliverables stay co-located with its contract.

What this module deliberately does NOT do
-----------------------------------------
- It does NOT call Codex itself. The injected `run_inner_loop` callable
  is what decides whether real Codex runs, mock-runs, or a fake patch
  worker (the e2e test injects a fake; production CLI plugs in the
  real `AgenticProjectRuntime.run`).
- It does NOT run Vercel / deploys / smoke checks. Change mode stops
  at "applied + committed + delivery report written" — RC-4A.3+ may
  add a post-apply integration hook but RC-4A.2 holds the line.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from orchestrator.core.autonomous import (
    AutonomousController,
    SCHEMA_VERSION_TASK_GRAPH,
    find_active_session,
    is_git_repo,
    is_worktree_clean,
    task_graph_file,
    write_task_graph,
)
from orchestrator.core.change_contract import change_dir, read_change_contract
from orchestrator.core.change_delivery_report import render_delivery_report
from orchestrator.core.ids import now_iso
from orchestrator.core.review_queue import list_review_items
from orchestrator.core.run_package import apply_selected_candidate


APPLIED_CHANGE_SCHEMA_VERSION = "agentic.applied_change.v1"
CHANGE_BRANCH_PREFIX = "agentic/change"


# ---------------------------------------------------------------------------
# Return shape
# ---------------------------------------------------------------------------
@dataclass
class ChangeRunResult:
    """What `run_change` returns after a single change session.

    `result` is one of {completed, needs-human-review, failed} and is the
    same value the delivery-report.md will display.
    """
    change_id: str
    result: str
    delivery_report_path: Path
    applied_change_path: Path | None
    session_id: str | None
    task_id: str
    commit_sha: str | None
    review_open_count: int


# ---------------------------------------------------------------------------
# Task-graph builder
# ---------------------------------------------------------------------------
def build_change_task_graph(
    *,
    change_id: str,
    contract: dict[str, Any],
    change_dir_relpath: str,
) -> dict[str, Any]:
    """Construct a single-task task-graph from a change-contract.

    The synthesized task carries two extra fields read by `commit_task`:
      - `change_id` → emits a `Change-Id:` git trailer
      - `source_change_request` → emits a `Source-Change-Request:` trailer

    Both fields are no-ops for normal (autonomous-mode) tasks, so this
    same task-graph can flow through the controller untouched.
    """
    if not isinstance(contract, dict):
        raise TypeError(f"contract must be a dict, got {type(contract).__name__}")
    goal = str(contract.get("goal") or "").strip()
    acceptance = list(contract.get("acceptance") or [])
    scope_paths = list(contract.get("scope_paths") or [])
    non_goals = list(contract.get("non_goals") or [])

    if not goal:
        raise ValueError("change-contract.goal is empty; refusing to build task-graph")
    if not acceptance:
        raise ValueError("change-contract.acceptance is empty; refusing to build task-graph")

    title_line = goal.splitlines()[0][:80] if goal else "Change task"
    title = title_line.strip() or "Change task"
    task_id = f"change-{change_id}"

    intent_parts: list[str] = [f"Change request: {goal}"]
    if non_goals:
        intent_parts.append("Non-goals:\n" + "\n".join(f"- {ng}" for ng in non_goals))
    intent_parts.append(
        "Acceptance criteria:\n" + "\n".join(f"- {c}" for c in acceptance)
    )
    intent = "\n\n".join(intent_parts)

    return {
        "schema_version": SCHEMA_VERSION_TASK_GRAPH,
        "project_title": title,
        "overview": goal,
        "tasks": [{
            "id": task_id,
            "title": title,
            "intent": intent,
            "acceptance_criteria": acceptance,
            "scope_paths": scope_paths,
            "dependencies": [],
            "status": "pending",
            "risk": "medium",
            "run_ids": [],
            "commit": None,
            # RC-4A.2: change-mode metadata; commit_task reads these.
            "change_id": change_id,
            "source_change_request": str(
                (Path(change_dir_relpath) / "change-request.md").as_posix()
            ),
        }],
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------
def run_change(
    *,
    project: dict[str, Any],
    change_id: str,
    run_inner_loop: Callable[..., Any],
    apply_candidate: Callable[..., Any] | None = None,
    now: datetime | None = None,
    allow_dirty_worktree: bool = False,
) -> ChangeRunResult:
    """Run a change end-to-end against a project.

    Caller injects `run_inner_loop` (and optionally `apply_candidate`).
    The injection point is the same one autonomous mode uses, so the
    production CLI can plug in `AgenticProjectRuntime.run` while tests
    plug in a deterministic fake.
    """
    project_path = Path(project["path"])
    if not is_git_repo(project_path):
        raise RuntimeError(
            f"{project_path} is not a git repository. "
            "change run requires git for safe apply + commit."
        )
    if not allow_dirty_worktree:
        clean, reason = is_worktree_clean(project_path)
        if not clean:
            raise RuntimeError(f"working tree not clean: {reason}")

    contract = read_change_contract(project_path, change_id)
    cdir = change_dir(project_path, change_id)
    if not cdir.exists():
        raise FileNotFoundError(f"change dir not found: {cdir}")

    change_dir_relpath = str(cdir.relative_to(project_path).as_posix())

    started_dt = (now or datetime.now(timezone.utc))
    started_iso = started_dt.isoformat(timespec="seconds")
    started_ts = started_dt.timestamp()

    # Build single-task task-graph and swap it in (preserve existing graph).
    new_graph = build_change_task_graph(
        change_id=change_id, contract=contract,
        change_dir_relpath=change_dir_relpath,
    )
    backup_payload = _swap_task_graph(project_path, new_graph)

    controller = AutonomousController(
        project=project,
        run_inner_loop=run_inner_loop,
        apply_candidate=apply_candidate or apply_selected_candidate,
    )

    try:
        session = controller.start_or_resume()

        # Override branch to `agentic/change/<change_id>` for evidence trail.
        branch_name = f"{CHANGE_BRANCH_PREFIX}/{change_id}"
        session.branch = branch_name
        controller._save_session(session)
        _create_or_checkout_branch(project_path, branch_name)

        outcome = controller.advance_one_task(session, new_graph)
    finally:
        # RC-4A.3.1.A: cleanup hygiene. The autonomous controller's
        # `commit_task` runs `git add -A -- ':!.agent'`, which captures the
        # ephemeral 1-task `task-graph.json` into the change commit. After
        # the commit lands we MUST:
        #   1. Reset task-graph.json (on disk + in the change commit) to its
        #      pre-change state — backup_payload contents if there was one,
        #      untracked + removed if there wasn't.
        #   2. Amend the change commit so its tree matches that pre-change
        #      state (otherwise `git status` shows ` M task-graph.json` or
        #      `D task-graph.json` after the run, breaking the next change
        #      run's worktree-clean preflight).
        # The old _restore_task_graph helper only handled the on-disk file,
        # not the git-side reset, which is the bug RC-4A.3 surfaced.
        task_state_for_cleanup = _read_task_state(new_graph)
        commit_sha_for_cleanup = task_state_for_cleanup.get("commit") if task_state_for_cleanup else None
        amended_sha = _purge_task_graph_from_change_commit(
            project_path,
            commit_sha=commit_sha_for_cleanup,
            backup_payload=backup_payload,
        )
        if amended_sha and task_state_for_cleanup is not None:
            # commit_sha changes after `git commit --amend`; keep task_state
            # in sync so applied-change.json gets the new SHA.
            task_state_for_cleanup["commit"] = amended_sha
        # Belt-and-suspenders: ensure the on-disk file matches the
        # backup state regardless of whether amend ran (e.g. when no
        # commit_sha was produced because the run paused before commit).
        _restore_task_graph(project_path, backup_payload)

    completed_dt = datetime.now(timezone.utc)
    completed_iso = completed_dt.isoformat(timespec="seconds")
    elapsed_sec = round(completed_dt.timestamp() - started_ts, 3)

    task_state = _read_task_state(new_graph)
    return _finalize_change_outputs(
        project_path=project_path,
        cdir=cdir,
        change_id=change_id,
        contract=contract,
        outcome=outcome,
        session=session,
        task_state=task_state,
        change_dir_relpath=change_dir_relpath,
        started_at=started_iso,
        completed_at=completed_iso,
        elapsed_sec=elapsed_sec,
    )


# ---------------------------------------------------------------------------
# applied-change.json + delivery-report.md derivation
# ---------------------------------------------------------------------------
def _finalize_change_outputs(
    *,
    project_path: Path,
    cdir: Path,
    change_id: str,
    contract: dict[str, Any],
    outcome: Any,
    session: Any,
    task_state: dict[str, Any],
    change_dir_relpath: str,
    started_at: str,
    completed_at: str,
    elapsed_sec: float,
) -> ChangeRunResult:
    """Translate controller state into the two operator-facing artifacts.

    Three operational outcomes:
      * task completed (commit hash set)          → result = completed
      * task needs-human-review                   → result = needs-human-review
      * task abandoned / error / nothing applied  → result = failed
    """
    decision = getattr(outcome, "decision", None) if outcome else None
    new_status = getattr(outcome, "new_status", None) if outcome else None
    commit_sha = task_state.get("commit")
    run_id = (task_state.get("run_ids") or [None])[-1] if task_state.get("run_ids") else None

    if new_status == "completed" and commit_sha:
        result = "completed"
    elif new_status == "needs-human-review":
        result = "needs-human-review"
    else:
        result = "failed"

    # applied-change.json — only written on `completed`. needs-review and
    # failed do not produce one (the source of truth is the review queue
    # or the controller log).
    applied_change_path: Path | None = None
    files_touched: list[str] = []
    validation: dict[str, Any] = {}
    base_commit: str | None = None
    applied_to_commit: str | None = None
    candidate_id: str | None = None
    promotion_decision: str | None = decision
    if result == "completed":
        applied_candidate = _read_applied_candidate(project_path, run_id)
        if applied_candidate:
            files_touched = list(applied_candidate.get("changed_files") or [])
            base_commit = str(applied_candidate.get("base_commit") or "")
            applied_to_commit = str(applied_candidate.get("applied_to_commit") or "")
            candidate_id = str(applied_candidate.get("candidate") or "")
            promotion_decision = str(applied_candidate.get("decision_at_apply_time") or decision or "")

        applied_change_payload = {
            "schema_version": APPLIED_CHANGE_SCHEMA_VERSION,
            "change_id": change_id,
            "candidate": candidate_id or "",
            "run_id": run_id or "",
            "base_commit": base_commit or "",
            "applied_to_commit": applied_to_commit or "",
            "files_touched": files_touched,
            "applied_at": completed_at,
            "commit": {
                "branch": getattr(session, "branch", "") or "",
                "sha": commit_sha or "",
                "message": _read_commit_message(project_path, commit_sha) if commit_sha else "",
            },
            "promotion_decision": promotion_decision or "",
            "source_change_request": str(
                (Path(change_dir_relpath) / "change-request.md").as_posix()
            ),
        }
        applied_change_path = cdir / "applied-change.json"
        applied_change_path.write_text(
            json.dumps(applied_change_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        # Build validation block AFTER applied_change_payload is in scope so
        # the renderer surfaces eval rows + promotion decision + apply target.
        validation = _read_eval_validation(
            project_path, run_id, candidate_id,
            promotion_decision=promotion_decision,
            applied_change=applied_change_payload,
        )
    else:
        # needs-human-review / failed: still surface whatever eval +
        # promotion signals the inner loop wrote so the operator knows
        # WHY this change paused, not just that it did. No applied-change
        # row (the change wasn't applied). Best-effort candidate id from
        # the latest run package.
        candidate_id = _peek_candidate_id_from_promotion(project_path, run_id)
        validation = _read_eval_validation(
            project_path, run_id, candidate_id,
            promotion_decision=promotion_decision,
            applied_change=None,
        )

    review_items = []
    if session is not None:
        try:
            review_items = [
                r.to_dict() for r in list_review_items(project_path, session.session_id)
            ]
        except Exception:  # noqa: BLE001 — review queue is best-effort context
            review_items = []
    open_reviews = [r for r in review_items if r.get("status") == "open"]

    commit_block = {}
    if commit_sha:
        commit_block = {
            "branch": getattr(session, "branch", "") or "",
            "sha": commit_sha,
            "message": _read_commit_message(project_path, commit_sha) or "",
        }

    delivery_result = {
        "change_id": change_id,
        "result": result,
        "goal": contract.get("goal"),
        "files_touched": files_touched,
        "validation": validation,
        "risks": _extract_risks(outcome, session),
        "commit": commit_block,
        "review_queue": {
            "open_count": len(open_reviews),
            "items": [
                {"review_id": r.get("review_id"), "title": r.get("title")}
                for r in open_reviews
            ],
        },
        "elapsed_sec": elapsed_sec,
        "created_at": started_at,
        "completed_at": completed_at,
    }
    delivery_report_path = cdir / "delivery-report.md"
    delivery_report_path.write_text(
        render_delivery_report(delivery_result), encoding="utf-8"
    )

    return ChangeRunResult(
        change_id=change_id,
        result=result,
        delivery_report_path=delivery_report_path,
        applied_change_path=applied_change_path,
        session_id=getattr(session, "session_id", None),
        task_id=task_state.get("id") or f"change-{change_id}",
        commit_sha=commit_sha,
        review_open_count=len(open_reviews),
    )


# ---------------------------------------------------------------------------
# Internals — task-graph swap
# ---------------------------------------------------------------------------
def _swap_task_graph(project_path: Path, new_graph: dict[str, Any]) -> dict[str, Any] | None:
    """Stash the existing task-graph.json (if any) and write `new_graph`
    in its place. Returns the prior payload (or None) for `_restore_task_graph`."""
    path = task_graph_file(project_path)
    backup: dict[str, Any] | None = None
    if path.exists():
        try:
            backup = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = None
    write_task_graph(project_path, new_graph)
    return backup


def _restore_task_graph(project_path: Path, backup_payload: dict[str, Any] | None) -> None:
    """Restore the prior task-graph.json or delete it if there wasn't one."""
    path = task_graph_file(project_path)
    if backup_payload is None:
        try:
            if path.exists():
                path.unlink()
        except OSError:
            pass
        return
    try:
        write_task_graph(project_path, backup_payload)
    except OSError:
        pass


def _read_task_state(graph: dict[str, Any]) -> dict[str, Any]:
    tasks = graph.get("tasks") or []
    if not tasks:
        return {}
    return tasks[0]


# ---------------------------------------------------------------------------
# Internals — git
# ---------------------------------------------------------------------------
def _create_or_checkout_branch(project_path: Path, branch: str) -> None:
    """Check out (or create + check out) the change branch."""
    existing = subprocess.run(
        ["git", "branch", "--list", branch],
        cwd=project_path, capture_output=True, text=True, check=False,
    )
    if (existing.stdout or "").strip():
        subprocess.run(["git", "checkout", branch], cwd=project_path, check=True, capture_output=True)
    else:
        subprocess.run(["git", "checkout", "-b", branch], cwd=project_path, check=True, capture_output=True)


def _purge_task_graph_from_change_commit(
    project_path: Path,
    *,
    commit_sha: str | None,
    backup_payload: dict[str, Any] | None,
) -> str | None:
    """Amend the change commit so task-graph.json is reset to pre-change state.

    Returns the new short SHA after amend, or None if no amend ran.

    The autonomous controller's commit_task does `git add -A -- ':!.agent'`,
    which means the ephemeral 1-task task-graph.json change-mode wrote into
    the project root gets captured in the commit. That's correct for
    autonomous mode (where task-graph.json is the source of truth and
    should be tracked), but wrong for change mode (where it's a temporary
    swap). Without this purge, a successful change run leaves the worktree
    dirty after we restore task-graph.json on disk:
      - prior task-graph existed → ` M task-graph.json` (file content
        differs from HEAD, which now has our 1-task graph)
      - no prior task-graph existed → `D task-graph.json` (file gone but
        git tracks it because the change commit added it)
    Either way the next `change run` preflight refuses to start.

    This helper resets task-graph.json (on disk + index) to backup state
    and amends HEAD. Defensive: if no commit_sha (run paused pre-commit)
    or git operations fail, returns None and leaves the worktree to be
    cleaned by `_restore_task_graph`.
    """
    if not commit_sha:
        return None
    path = task_graph_file(project_path)
    if backup_payload is None:
        # Pre-change state: file did not exist, file was not tracked.
        # Untrack from index (ignore errors if it wasn't actually staged
        # — e.g. when commit_task captured no file change).
        subprocess.run(
            ["git", "rm", "--cached", "-f", "--ignore-unmatch", "task-graph.json"],
            cwd=project_path, capture_output=True, check=False,
        )
        if path.exists():
            try:
                path.unlink()
            except OSError:
                pass
    else:
        # Pre-change state: file existed with backup_payload content.
        write_task_graph(project_path, backup_payload)
        subprocess.run(
            ["git", "add", "task-graph.json"],
            cwd=project_path, capture_output=True, check=False,
        )
    amend = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "commit", "--amend", "--no-edit", "--no-verify"],
        cwd=project_path, capture_output=True, text=True, check=False,
    )
    if amend.returncode != 0:
        # Defensive: amend can fail if the index ends up with nothing to
        # commit (e.g. commit_task wrote nothing, or backup_payload exactly
        # matched what got committed). Surface nothing — caller will fall
        # back to plain on-disk restore.
        return None
    new_head = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        cwd=project_path, capture_output=True, text=True, check=False,
    )
    return (new_head.stdout or "").strip() or None


def _read_commit_message(project_path: Path, sha: str) -> str:
    """Return the full commit message body for `sha` (or '' on failure)."""
    if not sha:
        return ""
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--pretty=%B", sha],
            cwd=project_path, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or "").strip()


# ---------------------------------------------------------------------------
# Internals — read-side helpers
# ---------------------------------------------------------------------------
def _read_applied_candidate(project_path: Path, run_id: str | None) -> dict[str, Any] | None:
    if not run_id:
        return None
    path = project_path / ".agent" / "runs" / run_id / "applied-candidate.json"
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _peek_candidate_id_from_promotion(project_path: Path, run_id: str | None) -> str | None:
    """For non-completed paths (needs-human-review / failed) we still want
    to surface the candidate's eval results so the operator can see WHY
    the change paused. The Promotion Gate's report carries `selected_candidate`
    (the candidate that was promoted, or the highest-scoring one when nothing
    was promoted), which is the right shape to project the eval table from."""
    if not run_id:
        return None
    promotion_path = project_path / ".agent" / "runs" / run_id / "promotion-report.json"
    if not promotion_path.exists():
        return None
    try:
        payload = json.loads(promotion_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    selected = payload.get("selected_candidate") or payload.get("candidate")
    if isinstance(selected, str) and selected:
        return selected
    # Fall back to the first candidate in the candidates array.
    for candidate in payload.get("candidates") or []:
        if isinstance(candidate, dict) and candidate.get("id"):
            return str(candidate["id"])
    return None


def _read_eval_validation(
    project_path: Path,
    run_id: str | None,
    candidate_id: str | None,
    *,
    promotion_decision: str | None = None,
    applied_change: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the delivery-report `validation` block from multiple sources.

    Sources, in order of preference:
      1. `<run>/candidates/<id>/eval-results.json` — every required eval
         command run by the inner loop. Each becomes a row keyed by
         `eval.<command_name>`. The producer's schema is `commands` (NOT
         `commands_run` — RC-4A.3.1 fix; pre-fix used the wrong key, which
         is why every real-Codex change run rendered "(no validation
         results recorded)" even though commands had passed).
      2. `<run>/promotion-report.json` — surfaces the deterministic
         Promotion Gate's hard-gate roll-up + decision so the operator
         sees that the candidate cleared the 12-rule gate, not just that
         the commands passed.
      3. `applied-change.json` — surfaces the Apply Gate outcome (the
         candidate landed in `applied_to_commit` on `commit.branch`) so
         the report records the safe-apply step too.

    Returns an ordered dict (`validation`) the renderer projects as bullet
    rows. Each entry: `{"passed": bool, "command": str, "duration_sec": ?}`.

    Tolerant: returns at least the promotion + apply rows even when
    eval-results.json is missing or empty, so the section never falls back
    to "(no validation results recorded)" on a real change run.
    """
    out: dict[str, Any] = {}

    # 1. eval-results.json (per-candidate)
    if run_id and candidate_id:
        eval_path = (
            project_path / ".agent" / "runs" / run_id
            / "candidates" / candidate_id / "eval-results.json"
        )
        if eval_path.exists():
            try:
                payload = json.loads(eval_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                payload = {}
            if isinstance(payload, dict):
                for command in payload.get("commands") or []:
                    if not isinstance(command, dict):
                        continue
                    name = str(command.get("name") or "").strip() or "command"
                    key = f"eval.{name}"
                    out[key] = {
                        "passed": bool(command.get("passed")),
                        "command": str(command.get("cmd") or ""),
                        # producer doesn't record duration_sec; renderer
                        # tolerates None as "(not recorded)".
                        "duration_sec": command.get("duration_sec"),
                    }
                # Roll-up signal: required eval declared / executed / passed.
                # Helps the operator see at a glance whether the gate had
                # something to check and whether it was satisfied.
                if payload.get("required_eval_declared") is not None:
                    out["eval.required"] = {
                        "passed": bool(payload.get("required_eval_passed")),
                        "command": (
                            "required eval declared="
                            f"{bool(payload.get('required_eval_declared'))}, "
                            "executed="
                            f"{bool(payload.get('required_eval_executed'))}"
                        ),
                        "duration_sec": None,
                    }

    # 2. promotion-report.json (run-level)
    if run_id:
        promotion_path = project_path / ".agent" / "runs" / run_id / "promotion-report.json"
        if promotion_path.exists():
            try:
                promotion_payload = json.loads(promotion_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                promotion_payload = {}
            if isinstance(promotion_payload, dict):
                decision = str(promotion_payload.get("decision") or "")
                hard = promotion_payload.get("hard_gates") or {}
                gate_details = promotion_payload.get("gate_details") or []
                passed_count = sum(
                    1 for g in gate_details
                    if isinstance(g, dict) and g.get("passed")
                )
                total_count = len(gate_details) if gate_details else 0
                out["promotion"] = {
                    "passed": decision == "promote",
                    "command": (
                        f"decision={decision or 'unknown'}, "
                        f"hard_gates={passed_count}/{total_count} passed"
                        if total_count
                        else f"decision={decision or 'unknown'}, "
                             f"all_pass={bool(hard.get('all_pass'))}"
                    ),
                    "duration_sec": None,
                }

    # 3. applied-change.json (Apply Gate outcome)
    if applied_change:
        commit = applied_change.get("commit") or {}
        sha = str(commit.get("sha") or applied_change.get("applied_to_commit") or "")
        branch = str(commit.get("branch") or "")
        out["apply"] = {
            "passed": True,  # the only way applied-change.json exists is post-apply
            "command": f"applied to {sha} on `{branch}`" if sha else "applied",
            "duration_sec": None,
        }

    return out


def _extract_risks(outcome: Any, session: Any) -> list[str]:
    risks: list[str] = []
    if outcome is None:
        risks.append("Change runner produced no outcome (no eligible task or budget breach).")
    else:
        err = getattr(outcome, "error", None)
        if err:
            risks.append(f"Inner-loop error surfaced: {err}")
    pause_reason = getattr(session, "pause_reason", None) if session is not None else None
    if pause_reason:
        risks.append(f"Session paused: {pause_reason}")
    return risks
