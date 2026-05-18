from __future__ import annotations

import fnmatch
import difflib
import json
import os
import re
import shutil
import signal
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore, _artifact_kind
from orchestrator.core.event_bus import EventBus
from orchestrator.core.ids import now_iso, short_id
from orchestrator.db import Database


@dataclass(frozen=True)
class AgenticRunResult:
    run_id: str
    status: str
    decision: str
    candidate: str
    run_dir: Path
    artifacts: list[str]
    promotion_report_path: Path


class AgenticProjectRuntime:
    """AI-native evidence runtime for verified patch generation.

    MVP-1 deliberately focuses on the observable execution envelope:
    intent contract, context pack, eval harness, candidate record,
    repair history, critic reports, promotion gate, trace, and proposed
    memory update. The actual patch worker can be swapped in later without
    changing the artifact contract.
    """

    def __init__(self, db: Database):
        self.db = db
        self.events = EventBus(db)
        self.artifacts = ArtifactStore(db)

    def run(
        self,
        *,
        project: dict[str, Any],
        patch_worker: str = "none",
        execute_eval: bool = False,
        model: str | None = None,
        timeout_sec: int = 900,
        max_repair_loops: int = 0,
        candidate_count: int = 3,
        intent_overrides: dict[str, Any] | None = None,
        codex_sandbox: str = "workspace-write",
        codex_ask_for_approval: str = "on-request",
        codex_command: str = "codex",
        candidate_strategy_order: list[str] | None = None,
    ) -> AgenticRunResult:
        project_path = Path(str(project["path"]))
        project_path.mkdir(parents=True, exist_ok=True)
        run_id = short_id("run")
        run_dir = project_path / ".agent" / "runs" / run_id
        trace: list[dict[str, Any]] = []
        written: list[str] = []

        now = now_iso()
        self.db.execute(
            """
            INSERT INTO runs (id, project_id, workflow_id, status, current_phase, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (run_id, project["id"], "agentic_project", "running", "intent-contract", now, now),
        )
        self.db.execute(
            "UPDATE projects SET status = 'running', updated_at = ? WHERE id = ?",
            (now, project["id"]),
        )
        self.events.emit(
            event_type="agentic.run.created",
            project_id=project["id"],
            run_id=run_id,
            message=f"Created agentic runtime run {run_id}.",
            payload={"run_dir": str(run_dir)},
        )

        def record(stage: str, message: str, payload: dict[str, Any] | None = None) -> None:
            event = {
                "ts": now_iso(),
                "stage": stage,
                "message": message,
                "payload": payload or {},
            }
            trace.append(event)
            self.events.emit(
                event_type=f"agentic.{stage}",
                project_id=project["id"],
                run_id=run_id,
                phase_id=stage,
                message=message,
                payload=payload or {},
            )

        try:
            record("intent-contract", "Compiled user intent into bounded change contract.")
            intent = _build_intent_contract(project, project_path)
            if intent_overrides:
                # MVP-4A: autonomous controller injects per-task intent
                # (goal / success_criteria / allowed_change_scope). Overrides
                # are merged shallow — list/dict values replace wholesale.
                for key, value in intent_overrides.items():
                    intent[key] = value
            written.append(_write_json(project_path, run_dir / "intent-contract.json", intent))

            record("context-pack", "Collected repository context and explicit unknowns.")
            context = _build_context_pack(project_path, intent, run_id=run_id)
            written.append(_write_json(project_path, run_dir / "context-pack.json", context))

            record("eval-harness", "Compiled executable and deterministic evaluation signals.")
            eval_harness = _build_eval_harness(project_path, context)
            written.append(_write_json(project_path, run_dir / "eval-harness.json", eval_harness))

            # MVP-3A: pick the strategies for this run. candidate_count is
            # clamped between 1 and len(CANDIDATE_STRATEGIES). Callers may
            # provide a preferred strategy order for workflow-specific cases
            # such as change mode, where a test-focused candidate is usually
            # a better first spend than a minimal conservative patch.
            strategies = _select_candidate_strategies(
                candidate_count,
                candidate_strategy_order=candidate_strategy_order,
            )

            record("task-slicing", "Defined permission-bounded agentic task slices.")
            task_slices = _build_task_slices(intent, eval_harness)
            task_slices["candidate_strategies"] = [
                {"id": s["id"], "label": s["label"], "prompt_hint": s["prompt_hint"]}
                for s in strategies
            ]
            written.append(_write_json(project_path, run_dir / "task-slices.json", task_slices))

            # Build, eval, repair, and write artifacts for each candidate.
            # Sequential by design (MVP-3A): no parallel execution. A single
            # candidate's failure must not abort the run — we capture it as
            # a candidate-level event and continue to the next strategy.
            candidates: list[dict[str, Any]] = []
            for strategy in strategies:
                candidate_id = strategy["id"]
                record(
                    "candidate-patches",
                    f"Building {candidate_id} (strategy={strategy['label']}, patch_worker={patch_worker}).",
                )
                try:
                    candidate = _build_candidate(
                        project_path,
                        run_dir,
                        intent,
                        context,
                        eval_harness,
                        patch_worker=patch_worker,
                        model=model or "gpt-5.5",
                        timeout_sec=timeout_sec,
                        candidate_id=candidate_id,
                        strategy=strategy,
                        codex_sandbox=codex_sandbox,
                        codex_ask_for_approval=codex_ask_for_approval,
                        codex_command=codex_command,
                    )
                    candidate["repair_history"]["max_loops"] = max_repair_loops
                    eval_results = _execute_eval_harness(
                        project_path,
                        eval_harness,
                        candidate,
                        execute_eval=execute_eval,
                        timeout_sec=timeout_sec,
                    )
                    candidate["eval_results"] = eval_results
                    candidate["run_log"].extend(eval_results.get("events", []))
                    if execute_eval and max_repair_loops > 0:
                        record(
                            "repair-loop",
                            f"Running repair loop for {candidate_id} with max_loops={max_repair_loops}.",
                        )
                        eval_results = _run_repair_loop(
                            project_path=project_path,
                            run_dir=run_dir,
                            intent=intent,
                            eval_harness=eval_harness,
                            candidate=candidate,
                            eval_results=eval_results,
                            model=model or "gpt-5.5",
                            timeout_sec=timeout_sec,
                            max_loops=max_repair_loops,
                        )
                        candidate["eval_results"] = eval_results
                    else:
                        _finalize_repair_history_without_loop(candidate, eval_results, execute_eval)
                except Exception as exc:  # noqa: BLE001
                    # Single-candidate failure must not crash the run. Record
                    # the failure as a synthetic candidate so the gate sees it.
                    record(
                        "candidate-error",
                        f"{candidate_id} failed during build/eval/repair: {exc}",
                        {"error": str(exc)},
                    )
                    candidate = {
                        "id": candidate_id,
                        "strategy": strategy["label"],
                        "patch_diff": f"# {candidate_id} build failed: {exc}\n",
                        "changed_files": {
                            "schema_version": "agentic.changed_files.v1",
                            "candidate": candidate_id,
                            "patch_status": "not_generated",
                            "reason": "candidate_build_exception",
                            "details": {"error": str(exc)},
                            "changed_files": [],
                            "source_patch_present": False,
                            "out_of_scope_changes": [],
                        },
                        "score": {
                            "schema_version": "agentic.candidate_score.v1",
                            "candidate": candidate_id,
                            "strategy": strategy["label"],
                            "source_patch_present": False,
                            "diff_within_scope": True,
                            "patch_class": "build_exception",
                            "patch_status": "not_generated",
                            "patch_reason": "candidate_build_exception",
                        },
                        "repair_history": {
                            "schema_version": "agentic.repair_history.v1",
                            "candidate": candidate_id,
                            "max_loops": max_repair_loops,
                            "attempts": [],
                            "stop_reason": "candidate_build_exception",
                            "final_failure": {
                                "failure_type": "spec_ambiguity",
                                "subtype": "candidate_build_exception",
                                "likely_cause": str(exc),
                                "repair_action": "Investigate orchestrator state and re-run.",
                            },
                            "failure_taxonomy": [c for c in FAILURE_TAXONOMY.keys() if c != "none"],
                        },
                        "run_log": [],
                        "eval_results": {"required_eval_declared": False, "required_eval_executed": False, "required_eval_passed": False, "commands": []},
                        "worktree_path": None,
                    }

                candidate_dir = run_dir / "candidates" / candidate_id
                written.append(_write_text(project_path, candidate_dir / "patch.diff", candidate["patch_diff"]))
                written.append(_write_json(project_path, candidate_dir / "changed-files.json", candidate["changed_files"]))
                written.append(_write_jsonl(project_path, candidate_dir / "run-log.jsonl", candidate["run_log"]))
                written.append(_write_json(project_path, candidate_dir / "score.json", candidate["score"]))
                written.append(_write_json(project_path, candidate_dir / "repair-history.json", candidate["repair_history"]))
                written.append(_write_json(project_path, candidate_dir / "eval-results.json", candidate.get("eval_results") or {}))

                record(
                    "critic-panel",
                    f"Wrote read-only critic panel findings for {candidate_id}.",
                )
                critic_paths = _write_critic_reports(project_path, run_dir, intent, context, candidate, eval_harness)
                written.extend(critic_paths)

                # If this candidate's repair loop exhausted (attempts > 0
                # but eval still failing), emit a candidate_abandoned record
                # immediately. The run-level abandoned event is decided
                # later by the Promotion Gate.
                ch = candidate.get("repair_history") or {}
                if ch.get("attempts") and not (candidate.get("eval_results") or {}).get("required_eval_passed"):
                    rel = _append_abandonment_record(
                        project_path,
                        run_id=run_id,
                        intent=intent,
                        promotion={"candidate": candidate_id, "decision": "candidate_abandoned"},
                        patch_worker=patch_worker,
                        event_type="candidate_abandoned",
                        candidate_id=candidate_id,
                        candidate=candidate,
                    )
                    record(
                        "candidate-abandoned",
                        f"{candidate_id} repair exhausted; recorded to {rel}.",
                        {"candidate": candidate_id, "abandonment_log": rel},
                    )
                    if rel not in written:
                        written.append(rel)

                candidates.append(candidate)

            record("promotion-gate", "Evaluated deterministic promotion gate over all candidates.")
            promotion = _build_promotion_report(
                intent,
                context,
                eval_harness,
                candidates,
                trace,
                project_path=project_path,
                patch_worker=patch_worker,
            )
            promotion_report_path = run_dir / "promotion-report.json"
            written.append(_write_json(project_path, promotion_report_path, promotion))

            if str(promotion.get("decision")) == "abandoned":
                abandonment_relpath = _append_abandonment_record(
                    project_path,
                    run_id=run_id,
                    intent=intent,
                    promotion=promotion,
                    patch_worker=patch_worker,
                    event_type="run_abandoned",
                )
                record(
                    "abandonment-recorded",
                    f"All candidates exhausted; appended run-level record to {abandonment_relpath}.",
                    {
                        "stop_reason": promotion["repair"].get("stop_reason"),
                        "abandonment_log": abandonment_relpath,
                    },
                )
                if abandonment_relpath not in written:
                    written.append(abandonment_relpath)

            record("memory-update", "Proposed evidence-backed memory update; did not write long-term memory.")
            memory_update = _build_memory_update(intent, context, eval_harness, candidates, promotion, run_id)
            written.append(_write_json(project_path, run_dir / "memory-update.proposed.json", memory_update))

            run_yaml = _render_run_yaml(run_id, project, promotion, run_dir)
            written.append(_write_text(project_path, run_dir / "run.yaml", run_yaml))
            human_summary = _render_human_summary(project, intent, context, eval_harness, promotion)
            written.append(_write_text(project_path, run_dir / "summary.md", human_summary))

            record("trace", "Finalized agentic trace.")
            written.append(_write_jsonl(project_path, run_dir / "trace.jsonl", trace))

            for relative in written:
                self.artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="agentic-runtime",
                    path=relative,
                    kind=_artifact_kind(relative),
                    summary=f"Agentic runtime artifact: {relative}.",
                    source_type="extra",
                    trust_level="high",
                    validation_status="passed",
                    validation_score=90,
                )

            completed = now_iso()
            self.db.execute(
                """
                UPDATE runs
                SET status = 'completed', current_phase = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                ("promotion-gate", completed, completed, run_id),
            )
            self.db.execute(
                "UPDATE projects SET status = 'completed', updated_at = ? WHERE id = ?",
                (completed, project["id"]),
            )
            self.events.emit(
                event_type="agentic.run.completed",
                project_id=project["id"],
                run_id=run_id,
                message=f"Agentic runtime completed with decision {promotion['decision']}.",
                payload={"decision": promotion["decision"], "candidate": promotion["candidate"]},
            )
            return AgenticRunResult(
                run_id=run_id,
                status="completed",
                decision=str(promotion["decision"]),
                candidate=str(promotion["candidate"]),
                run_dir=run_dir,
                artifacts=written,
                promotion_report_path=promotion_report_path,
            )
        except Exception:
            failed = now_iso()
            self.db.execute(
                """
                UPDATE runs
                SET status = 'failed', current_phase = ?, completed_at = ?, updated_at = ?
                WHERE id = ?
                """,
                ("agentic-runtime", failed, failed, run_id),
            )
            self.db.execute(
                "UPDATE projects SET status = 'failed', updated_at = ? WHERE id = ?",
                (failed, project["id"]),
            )
            raise


def _build_intent_contract(project: dict[str, Any], project_path: Path) -> dict[str, Any]:
    allowed_paths = _default_allowed_paths(project_path)
    return {
        "schema_version": "agentic.intent_contract.v1",
        "goal": str(project.get("idea") or project.get("name") or "Deliver a verified patch."),
        "non_goals": [
            "Do not redesign unrelated product areas.",
            "Do not change dependency or database providers without explicit approval.",
            "Do not write outside the project workspace.",
        ],
        "success_criteria": [
            "A bounded change contract exists.",
            "Relevant context and unknowns are explicit.",
            "Executable or deterministic evaluations are declared before patch promotion.",
            "Candidate evidence is traceable through promotion gate output.",
        ],
        "allowed_change_scope": {
            "paths": allowed_paths,
            "max_files": 12,
            "allow_dependency_changes": False,
        },
        "risk_level": "medium",
        "requires_human_approval": [
            "database migration",
            "new dependency",
            "production secret change",
            "write outside allowed_change_scope.paths",
        ],
    }


def _build_context_pack(
    project_path: Path,
    intent: dict[str, Any],
    *,
    run_id: str | None = None,
) -> dict[str, Any]:
    files = _discover_files(project_path)
    relevant_files = _rank_relevant_files(files, intent)
    existing_tests = [path for path in files if _looks_like_test(path)]
    commands = _git_context(project_path)
    constraints = _detect_constraints(project_path, files)
    selected = relevant_files[:20]
    context_quality = _context_quality(files, selected)
    unknowns = _detect_unknowns(project_path, existing_tests, constraints, context_quality)
    # Read prior memory-update proposals (excluding the current run) and
    # surface them as a deduped, capped `prior_learnings` block. Codex sees
    # this naturally when it reads context-pack.json.
    prior_memory = _read_prior_memory_updates(project_path, exclude_run_id=run_id)
    prior_learnings = _aggregate_prior_learnings(prior_memory)
    return {
        "schema_version": "agentic.context_pack.v2",
        "repo": commands,
        "context_quality": context_quality,
        "ranking_summary": _ranking_summary(files, selected),
        "must_include_files": _must_include_files(files),
        "relevant_files": selected,
        "symbols": _extract_symbols(project_path, [item["path"] for item in selected[:12]]),
        "existing_tests": existing_tests[:20],
        "constraints": constraints,
        "unknowns": unknowns,
        "prior_learnings": prior_learnings,
        "prior_run_count": len(prior_memory),
    }


def _build_eval_harness(project_path: Path, context: dict[str, Any]) -> dict[str, Any]:
    commands: list[dict[str, Any]] = []
    # RC-2A bug fix surfaced by dogfood: prior versions ONLY recognized
    # `apps/web/package.json`, which silently makes the eval harness
    # empty for any project with a flat layout (Vite default, Next.js
    # default `pages/` at root, plain Node). When the eval harness has
    # no required commands, the promotion gate's
    # `required_eval_declared` fails and every task returns
    # `needs-human-review`. We now probe `apps/web/package.json` first
    # (preserves all existing behavior for monorepo layouts) AND fall
    # back to project-root `package.json` so flat-layout projects work.
    web_dir = project_path / "apps" / "web"
    web_package_json = web_dir / "package.json"
    root_package_json = project_path / "package.json"
    if web_package_json.exists():
        package_cwd = "apps/web"
        scripts = _read_package_scripts(web_package_json)
    elif root_package_json.exists():
        package_cwd = "."
        scripts = _read_package_scripts(root_package_json)
    else:
        package_cwd = None
        scripts = {}
    if package_cwd is not None:
        if "typecheck" in scripts:
            commands.append(_command("typecheck", "npm run typecheck", required=True, cwd=package_cwd, timeout=120))
        if "build" in scripts:
            commands.append(_command("build", "npm run build", required=True, cwd=package_cwd, timeout=180))
        if "test" in scripts:
            commands.append(_command("unit-tests", "npm run test", required=False, cwd=package_cwd, timeout=180))
        if "test:e2e" in scripts:
            commands.append(_command("e2e", "npm run test:e2e", required=False, cwd=package_cwd, timeout=300))
    elif (project_path / "apps" / "web" / "index.html").exists():
        commands.append(
            {
                "name": "static-html-present",
                "cmd": "test -f apps/web/index.html",
                "required": True,
                "cwd": ".",
                "timeout_sec": 10,
                "type": "deterministic_file_check",
            }
        )
    return {
        "schema_version": "agentic.eval_harness.v1",
        "commands": commands,
        "api_contracts": _detect_api_contracts(project_path),
        "visual_checks": _detect_visual_checks(project_path),
        "manual_review_required": not any(command.get("required") for command in commands),
        "execution_policy": {
            "mode": "declared_here_executed_in_candidate_eval_results",
            "promotion_requires_required_eval_execution": True,
        },
        "context_unknowns": context.get("unknowns", []),
    }


def _build_task_slices(intent: dict[str, Any], eval_harness: dict[str, Any]) -> dict[str, Any]:
    allowed = intent["allowed_change_scope"]["paths"]
    return {
        "schema_version": "agentic.task_slices.v1",
        "slices": [
            {
                "id": "context-explorer",
                "mode": "read_only",
                "can_write": ["context-pack.json"],
                "can_run": ["rg", "git status", "git grep"],
            },
            {
                "id": "spec-compiler",
                "mode": "read_only_plus_eval_write",
                "can_write": ["eval-harness.json"],
                "can_run": [command["cmd"] for command in eval_harness.get("commands", [])],
            },
            {
                "id": "patch-worker",
                "mode": "isolated_worktree_write",
                "candidate_count": 1,
                "allowed_paths": allowed,
            },
            {
                "id": "repair-agent",
                "mode": "candidate_scoped_write",
                "max_loops": 5,
                "stop_on_repeated_failure_type": True,
            },
            {
                "id": "critic-panel",
                "mode": "read_only",
                "critics": ["correctness", "regression", "security", "ux", "overfit"],
            },
            {
                "id": "integration-lead",
                "mode": "promotion_gate_only",
                "can_merge": False,
            },
            {
                "id": "memory-curator",
                "mode": "proposed_only",
                "can_write_long_term_memory": False,
            },
        ],
    }


# MVP-3A: ordered list of candidate strategies. Each agentic run produces
# one candidate per strategy (sequentially). The strategy label is recorded
# in candidate metadata and surfaced in patch_worker prompts; deterministic
# scoring does not trust the label — selection is purely on observed evidence.
CANDIDATE_STRATEGIES: list[dict[str, str]] = [
    {
        "id": "candidate-a",
        "label": "conservative",
        "prompt_hint": "Prefer the smallest in-place change. Do not refactor adjacent code or rename symbols.",
    },
    {
        "id": "candidate-b",
        "label": "test-focused",
        "prompt_hint": "Add or extend tests that pin the intent's success criteria first, then make the smallest implementation change to satisfy them.",
    },
    {
        "id": "candidate-c",
        "label": "broader-fix",
        "prompt_hint": "If a wider but still in-scope change yields a more robust fix (handle adjacent edge cases, fix related code paths), make it — but stay within allowed_change_scope.",
    },
]


def _select_candidate_strategies(
    candidate_count: int,
    *,
    candidate_strategy_order: list[str] | None = None,
) -> list[dict[str, str]]:
    """Return the candidate strategies for a run, honoring an optional order.

    The runtime's default order stays conservative for greenfield/autonomous
    runs. Change runs may pass a different order so a constrained single
    candidate budget can spend the first attempt on a more useful strategy.
    """
    ordered: list[dict[str, str]] = []
    seen: set[str] = set()

    for requested in candidate_strategy_order or []:
        key = str(requested).strip().lower()
        if not key:
            continue
        match = next(
            (
                strategy
                for strategy in CANDIDATE_STRATEGIES
                if strategy["id"].lower() == key or strategy["label"].lower() == key
            ),
            None,
        )
        if match and match["id"] not in seen:
            ordered.append(match)
            seen.add(match["id"])

    for strategy in CANDIDATE_STRATEGIES:
        if strategy["id"] not in seen:
            ordered.append(strategy)
            seen.add(strategy["id"])

    limit = max(1, min(int(candidate_count), len(ordered)))
    return ordered[:limit]


def _build_candidate(
    project_path: Path,
    run_dir: Path,
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    *,
    patch_worker: str,
    model: str,
    timeout_sec: int,
    candidate_id: str = "candidate-a",
    strategy: dict[str, str] | None = None,
    codex_sandbox: str = "workspace-write",
    codex_ask_for_approval: str = "on-request",
    codex_command: str = "codex",
) -> dict[str, Any]:
    """Build one candidate's evidence package.

    `candidate_id` and `strategy` parameterize this function so the same
    builder can produce candidate-a/b/c sequentially. The strategy label is
    embedded in candidate metadata and passed to the patch worker prompt
    when patch_worker == "codex".
    """
    if strategy is None:
        # Default to the first strategy when caller didn't specify one
        # (preserves single-candidate fallback for tests calling directly).
        strategy = next((s for s in CANDIDATE_STRATEGIES if s["id"] == candidate_id), CANDIDATE_STRATEGIES[0])
    strategy_label = str(strategy.get("label") or "")
    patch_status = "not_generated"
    patch_reason = "patch_worker_disabled"
    patch_details: dict[str, Any] = {
        "worker": patch_worker,
        "recommended_next_action": "rerun_with_agentic_patch_worker_codex",
    }
    worktree_path: str | None = None
    changed_files: list[dict[str, Any]] = []
    patch_diff = (
        f"# {candidate_id} runtime-only evidence package (strategy: {strategy_label})\n"
        "# No product source patch was generated.\n"
    )

    if not context.get("context_quality", {}).get("has_source_files"):
        patch_reason = "insufficient_source_context"
        patch_details = {
            "source_files_in_context": context.get("context_quality", {}).get("checks", {}).get("source_files_selected", 0),
            "docs_files_in_context": context.get("ranking_summary", {}).get("selected_doc_files", 0),
            "tests_in_context": context.get("ranking_summary", {}).get("selected_test_files", 0),
            "recommended_next_action": "rerun_context_pack_with_source_weighting",
        }
    elif patch_worker == "codex":
        patch_result = _run_codex_patch_worker(
            project_path=project_path,
            run_dir=run_dir,
            intent=intent,
            context=context,
            eval_harness=eval_harness,
            model=model,
            timeout_sec=timeout_sec,
            candidate_id=candidate_id,
            strategy=strategy,
            sandbox=codex_sandbox,
            ask_for_approval=codex_ask_for_approval,
            codex_command=codex_command,
        )
        patch_status = str(patch_result["patch_status"])
        patch_reason = str(patch_result["reason"])
        patch_details = dict(patch_result.get("details") or {})
        worktree_path = patch_result.get("worktree_path")
        changed_files = list(patch_result["changed_files"]["changed_files"])
        patch_diff = str(patch_result["patch_diff"])
    else:
        patch_diff = (
            f"# {candidate_id} runtime-only evidence package (strategy: {strategy_label})\n"
            "# No product source patch was requested. Re-run with --agentic-patch-worker codex to generate one.\n"
        )

    if patch_worker == "codex" and not patch_diff.strip():
        changed_files = []
        patch_diff = (
            f"# {candidate_id} patch worker did not produce a source diff\n"
            f"# reason: {patch_reason}\n"
        )

    allowed = intent["allowed_change_scope"]["paths"]
    out_of_scope = [item for item in changed_files if not bool(item.get("within_scope", False))]
    source_patch_present = bool(patch_diff.strip()) and any(
        item.get("category") in {"source", "test", "config"} for item in changed_files
    )
    diff_within_scope = all(_path_allowed(item["path"], allowed) for item in changed_files)
    changed_files_payload = {
        "schema_version": "agentic.changed_files.v1",
        "candidate": candidate_id,
        "base_commit": context.get("repo", {}).get("commit") or "unknown",
        "head_commit": "working-tree" if changed_files else None,
        "patch_status": patch_status,
        "reason": patch_reason,
        "details": patch_details,
        "worktree_path": worktree_path,
        "changed_files": changed_files,
        "source_patch_present": source_patch_present,
        "out_of_scope_changes": out_of_scope,
    }
    score = {
        "schema_version": "agentic.candidate_score.v1",
        "candidate": candidate_id,
        "strategy": strategy_label,
        "patch_class": "source_patch" if source_patch_present else "runtime_only_evidence_capture",
        "patch_status": patch_status,
        "patch_reason": patch_reason,
        "source_changes": len(changed_files),
        "source_patch_present": source_patch_present,
        "diff_within_scope": diff_within_scope,
        "context_files_considered": len(context.get("relevant_files", [])),
        "hard_gate_ready": True,
        "notes": [
            "Promotion requires a non-empty source/test/config diff plus executed required evals.",
            "Runtime-only artifacts are never counted as product-source patches.",
        ],
    }
    run_log = [
        {
            "ts": now_iso(),
            "candidate": candidate_id,
            "event": "candidate.created",
            "message": f"Created candidate package with patch_status={patch_status}, strategy={strategy_label}.",
        },
        {
            "ts": now_iso(),
            "candidate": candidate_id,
            "event": "patch.status",
            "message": patch_reason,
            "details": patch_details,
        },
    ]
    repair_history = {
        "schema_version": "agentic.repair_history.v1",
        "candidate": candidate_id,
        "max_loops": 5,
        "attempts": [],
        "stop_reason": "repair_loop_not_connected" if patch_status != "generated" else "no_failure_observed_before_eval",
        # Always present so downstream consumers can read it unconditionally.
        # Set to a classified failure dict on terminal repair-failure paths,
        # None otherwise (no failure observed, or eval passed).
        "final_failure": None,
        # Sourced from FAILURE_TAXONOMY so this list cannot drift from the
        # classifier. "none" is omitted because it represents the no-failure
        # state and is never a stop_reason.failure_type.
        "failure_taxonomy": [category for category in FAILURE_TAXONOMY.keys() if category != "none"],
    }
    return {
        "id": candidate_id,
        "strategy": strategy_label,
        "patch_diff": patch_diff,
        "changed_files": changed_files_payload,
        "run_log": run_log,
        "score": score,
        "repair_history": repair_history,
        "worktree_path": worktree_path,
    }


# RC-2B: explicitly enumerate the only sandbox / approval values the
# patch worker accepts. The brief is non-negotiable on this: Codex must
# run under workspace-write with on-request approval; the dangerous
# bypass flags (`--yolo`, `--dangerously-bypass-approvals-and-sandbox`,
# `danger-full-access`) are NEVER allowed for the autonomous patch worker.
#
# RC-2B.1 env-probe correction: real `codex exec` (codex-cli 0.130.0)
# enumerates sandbox values as exactly `read-only / workspace-write /
# danger-full-access`. Our prior allow-list also listed "read" which is
# NOT a real codex value — removed. `danger-full-access` IS a real
# value but stays on the forbid-list (policy choice, not codex limit).
_CODEX_ALLOWED_SANDBOXES: frozenset[str] = frozenset({
    "workspace-write", "read-only",
})
# Real codex approval_policy values (per codex 0.130.0): on-request,
# untrusted, never. These are NOT a CLI flag; codex consumes them via
# the config-override surface `-c approval_policy=<value>`.
_CODEX_ALLOWED_APPROVALS: frozenset[str] = frozenset({
    "on-request", "untrusted", "never",
})
_CODEX_FORBIDDEN_TOKENS: frozenset[str] = frozenset({
    "--yolo",
    "--dangerously-bypass-approvals-and-sandbox",
    "danger-full-access",
})


def codex_cli_available(*, command: str = "codex") -> bool:
    """Preflight: is the codex CLI on PATH? Cheap to call from CLIs that
    want to print a clear error before kicking off a long autonomous run."""
    return shutil.which(command) is not None


def _default_codex_runner(command: list[str], cwd: Path, timeout_sec: int) -> Any:
    """Subprocess.run wrapper. Extracted so unit tests can swap a fake
    runner that returns a synthetic completed-process without forking."""
    return _run_command_with_process_group_timeout(command, cwd, timeout_sec)


def _run_command_with_process_group_timeout(command: list[str], cwd: Path, timeout_sec: int) -> subprocess.CompletedProcess[str]:
    """Run a command in a new process group and kill the whole group on timeout.

    Codex can spawn nested tool commands such as `npm run build`. Killing only
    the Codex parent leaves those children writing into the candidate worktree,
    which can corrupt the next eval run. A process group timeout keeps the
    candidate workspace deterministic.
    """
    process = subprocess.Popen(
        command,
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout_sec)
    except subprocess.TimeoutExpired as exc:
        _terminate_process_group(process)
        stdout, stderr = process.communicate()
        raise subprocess.TimeoutExpired(
            command,
            timeout_sec,
            output=stdout if isinstance(stdout, str) else exc.output,
            stderr=stderr if isinstance(stderr, str) else exc.stderr,
        ) from exc
    _terminate_process_group_members(process.pid)
    return subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout or "",
        stderr or "",
    )


def _terminate_process_group(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except OSError:
        return
    try:
        process.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            return


def _terminate_process_group_members(pid: int) -> None:
    """Best-effort cleanup for descendants left behind after parent exit."""
    try:
        os.killpg(pid, signal.SIGTERM)
    except (ProcessLookupError, OSError):
        return


def build_codex_patch_worker_command(
    *,
    worktree: Path,
    output_path: Path,
    prompt: str,
    model: str,
    sandbox: str = "workspace-write",
    ask_for_approval: str = "on-request",
    command: str = "codex",
) -> list[str]:
    """Build the `codex exec` argv for the autonomous patch worker.

    RC-2B safety invariants enforced here, NOT at the call site, so any
    future caller (CLI, test, third-party) cannot accidentally widen the
    blast radius:
      - sandbox MUST be one of `_CODEX_ALLOWED_SANDBOXES`
      - ask_for_approval MUST be one of `_CODEX_ALLOWED_APPROVALS`
      - forbidden tokens (`--yolo`, etc.) raise immediately
    """
    if sandbox not in _CODEX_ALLOWED_SANDBOXES:
        raise ValueError(
            f"codex sandbox `{sandbox}` is not allowed for the autonomous patch worker. "
            f"Allowed: {sorted(_CODEX_ALLOWED_SANDBOXES)}."
        )
    if ask_for_approval not in _CODEX_ALLOWED_APPROVALS:
        raise ValueError(
            f"codex ask-for-approval `{ask_for_approval}` is not allowed. "
            f"Allowed: {sorted(_CODEX_ALLOWED_APPROVALS)}."
        )
    for token in (sandbox, ask_for_approval, command):
        if token in _CODEX_FORBIDDEN_TOKENS:
            raise ValueError(
                f"codex command refused: `{token}` is on the forbidden list "
                f"(`{sorted(_CODEX_FORBIDDEN_TOKENS)}`). The autonomous patch "
                "worker never bypasses sandbox or approval gates."
            )
    # RC-2B.1 env-probe correction: real `codex exec` (codex-cli
    # 0.130.0) does NOT accept `--ask-for-approval` as a subcommand
    # flag — it's a TOP-LEVEL option that must be placed BEFORE the
    # `exec` token. Pre-fix the build_codex_*_command output put
    # `--ask-for-approval` after `exec`, which codex rejected with
    # `error: unexpected argument '--ask-for-approval' found` at the
    # argv parser, BEFORE any model call. The autonomous run therefore
    # would have appeared to fail at a much later, less specific layer
    # (a "no diff produced" promotion-gate failure that would have been
    # extremely hard to diagnose). Verified against codex-cli 0.130.0
    # via the RC-2B.1 env probe.
    return [
        command,
        "--ask-for-approval", ask_for_approval,
        "exec",
        "-C", str(worktree),
        "-m", model,
        "--sandbox", sandbox,
        "--skip-git-repo-check",
        "--output-last-message", str(output_path),
        "--",
        prompt,
    ]


def _run_codex_patch_worker(
    *,
    project_path: Path,
    run_dir: Path,
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    model: str,
    timeout_sec: int,
    candidate_id: str = "candidate-a",
    strategy: dict[str, str] | None = None,
    sandbox: str = "workspace-write",
    ask_for_approval: str = "on-request",
    codex_command: str = "codex",
    command_runner: Any = None,
) -> dict[str, Any]:
    worktree = project_path / ".agent" / "worktrees" / run_dir.name / candidate_id
    try:
        _prepare_candidate_workspace(project_path, worktree)
    except OSError as exc:
        return _patch_worker_failure("worktree_prepare_failed", {"error": str(exc), "worktree_path": str(worktree)})

    # RC-2B: short-circuit with a clear failure when codex CLI is absent.
    # Without this, the run would fork into _diff_directories with no
    # patch and the failure_type would be the less-specific
    # `empty_or_non_source_diff`. A fast preflight gives operators a
    # one-line "install codex" in the review queue.
    if command_runner is None and not codex_cli_available(command=codex_command):
        return _patch_worker_failure("codex_cli_not_found", {
            "worktree_path": str(worktree),
            "looked_for": codex_command,
        })

    output_path = run_dir / "candidates" / candidate_id / "codex-last-message.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = _render_patch_worker_prompt(intent, context, eval_harness, strategy=strategy)
    try:
        command = build_codex_patch_worker_command(
            worktree=worktree, output_path=output_path, prompt=prompt,
            model=model, sandbox=sandbox, ask_for_approval=ask_for_approval,
            command=codex_command,
        )
    except ValueError as exc:
        return _patch_worker_failure("codex_command_refused", {
            "worktree_path": str(worktree),
            "error": str(exc),
        })
    runner = command_runner or _default_codex_runner
    try:
        completed = runner(command, project_path, timeout_sec)
    except FileNotFoundError:
        return _patch_worker_failure("codex_cli_not_found", {"worktree_path": str(worktree)})
    except subprocess.TimeoutExpired as exc:
        diff_result = _diff_directories(
            project_path,
            worktree,
            intent["allowed_change_scope"]["paths"],
            candidate_id=candidate_id,
        )
        if diff_result["source_patch_present"]:
            return {
                "patch_status": "generated",
                "reason": "codex_cli_timeout_with_patch",
                "details": {
                    "worker": "codex",
                    "model": model,
                    "returncode": None,
                    "worktree_path": str(worktree),
                    "timeout_sec": timeout_sec,
                    "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                    "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
                    "last_message_path": str(output_path),
                },
                "worktree_path": str(worktree),
                "patch_diff": diff_result["patch_diff"],
                "changed_files": diff_result["changed_files"],
            }
        return _patch_worker_failure(
            "codex_cli_timeout",
            {
                "worktree_path": str(worktree),
                "timeout_sec": timeout_sec,
                "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
            },
        )

    diff_result = _diff_directories(
        project_path,
        worktree,
        intent["allowed_change_scope"]["paths"],
        candidate_id=candidate_id,
    )
    status = "generated" if diff_result["source_patch_present"] else "not_generated"
    reason = "source_patch_generated" if status == "generated" else "empty_or_non_source_diff"
    if completed.returncode != 0 and status != "generated":
        reason = "codex_cli_failed_without_patch"
    return {
        "patch_status": status,
        "reason": reason,
        "details": {
            "worker": "codex",
            "model": model,
            "returncode": completed.returncode,
            "worktree_path": str(worktree),
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "last_message_path": str(output_path),
        },
        "worktree_path": str(worktree),
        "patch_diff": diff_result["patch_diff"],
        "changed_files": diff_result["changed_files"],
    }


def _run_repair_loop(
    *,
    project_path: Path,
    run_dir: Path,
    intent: dict[str, Any],
    eval_harness: dict[str, Any],
    candidate: dict[str, Any],
    eval_results: dict[str, Any],
    model: str,
    timeout_sec: int,
    max_loops: int,
) -> dict[str, Any]:
    repair_history = candidate["repair_history"]
    repair_history["max_loops"] = max_loops
    attempts = repair_history.setdefault("attempts", [])
    if not candidate.get("score", {}).get("source_patch_present"):
        repair_history["stop_reason"] = "no_source_patch_to_repair"
        return eval_results
    if not eval_results.get("required_eval_executed"):
        repair_history["stop_reason"] = "required_eval_not_executed"
        repair_history["final_failure"] = _classify_eval_failure(eval_results)
        return eval_results
    if eval_results.get("required_eval_passed"):
        repair_history["stop_reason"] = "eval_passed_no_repair_needed"
        repair_history["final_failure"] = None
        return eval_results
    worktree_path = candidate.get("worktree_path")
    if not worktree_path:
        repair_history["stop_reason"] = "missing_candidate_worktree"
        repair_history["final_failure"] = _classify_eval_failure(eval_results)
        return eval_results

    consecutive_failure_type: str | None = None
    consecutive_failure_count = 0
    for loop_index in range(1, max_loops + 1):
        failure = _classify_eval_failure(eval_results)
        failure_type = str(failure.get("failure_type") or "unknown")
        if failure_type == consecutive_failure_type:
            consecutive_failure_count += 1
        else:
            consecutive_failure_type = failure_type
            consecutive_failure_count = 1
        if consecutive_failure_count >= 3:
            repair_history["stop_reason"] = "repeated_failure_type"
            repair_history["final_failure"] = failure
            break

        attempt = {
            "loop_index": loop_index,
            "started_at": now_iso(),
            "failure": failure,
            "repair_action": failure.get("repair_action", "repair candidate and rerun eval"),
            "status": "running",
        }
        attempts.append(attempt)
        candidate["run_log"].append(
            {
                "ts": now_iso(),
                "candidate": candidate.get("id", "candidate-a"),
                "event": "repair.started",
                "message": f"Repair loop {loop_index} started for {failure_type}.",
                "details": failure,
            }
        )
        repair_result = _run_codex_repair_agent(
            project_path=project_path,
            run_dir=run_dir,
            worktree_path=Path(worktree_path),
            intent=intent,
            eval_harness=eval_harness,
            candidate=candidate,
            eval_results=eval_results,
            failure=failure,
            loop_index=loop_index,
            model=model,
            timeout_sec=timeout_sec,
        )
        attempt["repair_result"] = repair_result
        if repair_result.get("status") == "failed":
            attempt["status"] = "repair_failed"
            repair_history["stop_reason"] = str(repair_result.get("reason") or "repair_agent_failed")
            repair_history["final_failure"] = failure
            candidate["run_log"].append(
                {
                    "ts": now_iso(),
                    "candidate": candidate.get("id", "candidate-a"),
                    "event": "repair.failed",
                    "message": repair_history["stop_reason"],
                    "details": repair_result,
                }
            )
            break

        diff_result = _diff_directories(project_path, Path(worktree_path), intent["allowed_change_scope"]["paths"])
        repair_base_commit = (
            _run_git(project_path, ["rev-parse", "--short", "HEAD"])
            or str(candidate.get("changed_files", {}).get("base_commit") or "")
            or "unknown"
        )
        _refresh_candidate_patch(
            candidate,
            context_commit=repair_base_commit,
            diff_result=diff_result,
            patch_status="generated" if diff_result["source_patch_present"] else "not_generated",
            patch_reason="repair_patch_generated" if diff_result["source_patch_present"] else "repair_empty_or_non_source_diff",
            patch_details=repair_result,
        )
        eval_results = _execute_eval_harness(
            project_path,
            eval_harness,
            candidate,
            execute_eval=True,
            timeout_sec=timeout_sec,
        )
        candidate["eval_results"] = eval_results
        candidate["run_log"].extend(eval_results.get("events", []))
        attempt["finished_at"] = now_iso()
        attempt["status"] = "eval_passed" if eval_results.get("required_eval_passed") else "eval_failed"
        attempt["post_repair_eval"] = _eval_summary(eval_results)
        attempt["changed_files_count"] = len(candidate["changed_files"].get("changed_files", []))
        candidate["run_log"].append(
            {
                "ts": now_iso(),
                "candidate": candidate.get("id", "candidate-a"),
                "event": "repair.completed",
                "message": f"Repair loop {loop_index} ended with status {attempt['status']}.",
                "details": attempt["post_repair_eval"],
            }
        )
        if eval_results.get("required_eval_passed"):
            repair_history["stop_reason"] = "eval_passed_after_repair"
            repair_history["final_failure"] = None
            break
    else:
        repair_history["stop_reason"] = "max_loops_exhausted"
        repair_history["final_failure"] = _classify_eval_failure(eval_results)
    return eval_results


def _refresh_candidate_patch(
    candidate: dict[str, Any],
    *,
    context_commit: str,
    diff_result: dict[str, Any],
    patch_status: str,
    patch_reason: str,
    patch_details: dict[str, Any],
) -> None:
    changed_files = list(diff_result["changed_files"].get("changed_files", []))
    out_of_scope = list(diff_result["changed_files"].get("out_of_scope_changes", []))
    source_patch_present = bool(diff_result["source_patch_present"])
    candidate["patch_diff"] = str(diff_result["patch_diff"])
    candidate["changed_files"].update(
        {
            "base_commit": context_commit,
            "head_commit": "working-tree" if changed_files else None,
            "patch_status": patch_status,
            "reason": patch_reason,
            "details": patch_details,
            "changed_files": changed_files,
            "source_patch_present": source_patch_present,
            "out_of_scope_changes": out_of_scope,
        }
    )
    candidate["score"].update(
        {
            "patch_class": "source_patch" if source_patch_present else "runtime_only_evidence_capture",
            "patch_status": patch_status,
            "patch_reason": patch_reason,
            "source_changes": len(changed_files),
            "source_patch_present": source_patch_present,
            "diff_within_scope": not out_of_scope,
        }
    )


def _run_codex_repair_agent(
    *,
    project_path: Path,
    run_dir: Path,
    worktree_path: Path,
    intent: dict[str, Any],
    eval_harness: dict[str, Any],
    candidate: dict[str, Any],
    eval_results: dict[str, Any],
    failure: dict[str, Any],
    loop_index: int,
    model: str,
    timeout_sec: int,
) -> dict[str, Any]:
    candidate_id = str(candidate.get("id") or "candidate-a")
    output_path = run_dir / "candidates" / candidate_id / f"repair-last-message-loop-{loop_index}.md"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = _render_repair_prompt(intent, eval_harness, candidate, eval_results, failure, loop_index)
    command = [
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "-C",
        str(worktree_path),
        "-m",
        model,
        "--sandbox",
        "workspace-write",
        "--skip-git-repo-check",
        "--output-last-message",
        str(output_path),
        "--",
        prompt,
    ]
    try:
        completed = _run_command_with_process_group_timeout(
            command,
            worktree_path,
            timeout_sec,
        )
    except FileNotFoundError:
        return {"status": "failed", "reason": "codex_cli_not_found", "details": {"worktree_path": str(worktree_path)}}
    except subprocess.TimeoutExpired as exc:
        diff_result = _diff_directories(
            project_path,
            worktree_path,
            intent["allowed_change_scope"]["paths"],
            candidate_id=candidate_id,
        )
        if diff_result["source_patch_present"]:
            return {
                "status": "completed",
                "reason": "repair_agent_timeout_with_patch",
                "details": {
                    "worker": "codex",
                    "model": model,
                    "returncode": None,
                    "worktree_path": str(worktree_path),
                    "timeout_sec": timeout_sec,
                    "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                    "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
                    "last_message_path": str(output_path),
                },
            }
        return {
            "status": "failed",
            "reason": "codex_cli_timeout",
            "details": {
                "worktree_path": str(worktree_path),
                "timeout_sec": timeout_sec,
                "stdout_tail": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
                "stderr_tail": (exc.stderr or "")[-4000:] if isinstance(exc.stderr, str) else "",
            },
        }
    return {
        "status": "completed" if completed.returncode == 0 else "failed",
        "reason": "repair_agent_completed" if completed.returncode == 0 else "repair_agent_returned_nonzero",
        "details": {
            "worker": "codex",
            "model": model,
            "returncode": completed.returncode,
            "worktree_path": str(worktree_path),
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
            "last_message_path": str(output_path),
        },
    }


def _render_repair_prompt(
    intent: dict[str, Any],
    eval_harness: dict[str, Any],
    candidate: dict[str, Any],
    eval_results: dict[str, Any],
    failure: dict[str, Any],
    loop_index: int,
) -> str:
    failed_commands = [command for command in eval_results.get("commands", []) if command.get("required") and not command.get("passed")]
    changed_paths = [item.get("path") for item in candidate.get("changed_files", {}).get("changed_files", [])]
    failure_category = str(failure.get("failure_type") or "spec_ambiguity")
    spec = FAILURE_TAXONOMY.get(failure_category, FAILURE_TAXONOMY["spec_ambiguity"])
    return f"""You are the repair-agent for Local Agent Dev Studio's agentic_project runtime.

Loop index: {loop_index}

Goal:
{intent["goal"]}

Repair only the existing candidate in this workspace. Do not redesign unrelated areas.
Do not edit .agent/**. Do not add dependencies. Do not write outside the workspace.
Do not create new route, error, or not-found files unless the success criteria explicitly ask for them.
For copy, first-screen, or UI polish changes, prefer editing existing app/page.tsx and app/layout.tsx only.

Allowed path globs:
{json.dumps(intent["allowed_change_scope"]["paths"], indent=2)}

Current changed files:
{json.dumps(changed_paths, indent=2)}

Failure category: {failure_category}
Category description: {spec["description"]}
Recommended repair posture: {spec["repair_hint"]}

Failure classification:
{json.dumps(failure, ensure_ascii=False, indent=2)}

Failed required eval commands and output tails:
{json.dumps(_failed_command_tails(failed_commands), ensure_ascii=False, indent=2)}

Required eval harness:
{json.dumps([command for command in eval_harness.get("commands", []) if command.get("required")], indent=2)}

Make the smallest code/test/config change needed to fix the failing required eval.
When done, leave files modified in the workspace and reply with a short summary of changed files.
"""


def _failed_command_tails(commands: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "name": command.get("name"),
            "cmd": command.get("cmd"),
            "exit_code": command.get("exit_code"),
            "failure_type": command.get("failure_type"),
            "reason": command.get("reason"),
            "stdout_tail": str(command.get("stdout") or "")[-3000:],
            "stderr_tail": str(command.get("stderr") or "")[-3000:],
        }
        for command in commands
    ]


# ---------------------------------------------------------------------------
# Failure taxonomy
# ---------------------------------------------------------------------------
# Single source of truth for required-eval failure categories. Both the
# deterministic classifier (`_classify_eval_failure`) and the repair-agent
# prompt (`_render_repair_prompt`) read from this dict so that categories,
# human-readable descriptions, and repair guidance never drift apart.
#
# To add a new category:
#   1. Add an entry below.
#   2. Add a matching predicate to `_FAILURE_MATCH_RULES` (or extend an
#      existing one). Order matters — see the comment on that list.
#   3. Add a unit-test fixture exercising the new branch in
#      `tests/unit/test_agentic_runtime.py::TaxonomyTests`.
FAILURE_TAXONOMY: dict[str, dict[str, str]] = {
    "none": {
        "description": "No required eval failure observed.",
        "default_subtype": "required_eval_passed",
        "default_likely_cause": "No failed required eval command was found.",
        "repair_hint": "No repair needed.",
    },
    "build_failure": {
        "description": "A required build/compile/bundle command failed (e.g. `npm run build`).",
        "default_subtype": "build_command_failed",
        "default_likely_cause": "The required build command failed.",
        "repair_hint": "Fix build-breaking source, config, or import issues.",
    },
    "type_error": {
        "description": "Static type checking (TypeScript / tsc) failed after the candidate patch.",
        "default_subtype": "typescript",
        "default_likely_cause": "TypeScript or type-checking failed after the candidate patch.",
        "repair_hint": "Fix type signatures, imports, route types, or invalid JSX/TS syntax.",
    },
    "unit_test_failure": {
        "description": "A unit or integration assertion failed (jest, vitest, generic asserts).",
        "default_subtype": "assertion",
        "default_likely_cause": "A unit or integration assertion failed.",
        "repair_hint": "Fix implementation behavior so existing assertions pass.",
    },
    "e2e_failure": {
        "description": "End-to-end browser flow or API smoke coverage failed (playwright / e2e command).",
        "default_subtype": "browser_or_flow_assertion",
        "default_likely_cause": "End-to-end browser flow or API smoke coverage failed.",
        "repair_hint": "Fix the product flow or test-visible state without weakening the test.",
    },
    "runtime_exception": {
        "description": "Candidate threw a Reference/Syntax/Runtime error during execution.",
        "default_subtype": "runtime_exception",
        "default_likely_cause": "The candidate throws during execution.",
        "repair_hint": "Fix the thrown exception while preserving intended behavior.",
    },
    "dependency_error": {
        "description": "Patch references a missing module, package script, or generated file.",
        "default_subtype": "missing_dependency_or_script",
        "default_likely_cause": "The patch references a missing module, package script, or generated file.",
        "repair_hint": "Use existing dependencies and scripts; do not add new packages without approval.",
    },
    "environment_error": {
        "description": "Eval did not complete deterministically due to the local environment (timeout, ports, permissions, FS).",
        "default_subtype": "local_environment",
        "default_likely_cause": "The eval command failed because of local process, port, or filesystem constraints.",
        "repair_hint": "Avoid relying on unavailable local resources and make the eval command deterministic.",
    },
    "spec_ambiguity": {
        "description": "Failure did not match any deterministic category; treated as unclassified — repair posture is conservative.",
        "default_subtype": "unclassified_eval_failure",
        "default_likely_cause": "The failure did not match a known deterministic category.",
        "repair_hint": "Inspect eval output and repair the smallest likely cause.",
    },
}


def _make_match_rule(
    category: str,
    *,
    subtype: str | None = None,
    likely_cause: str | None = None,
    repair_hint: str | None = None,
    predicate,
) -> dict[str, Any]:
    spec = FAILURE_TAXONOMY[category]
    return {
        "category": category,
        "subtype": subtype or spec["default_subtype"],
        "likely_cause": likely_cause or spec["default_likely_cause"],
        "repair_hint": repair_hint or spec["repair_hint"],
        "predicate": predicate,
    }


# Ordered classification rules. The first matching rule wins, so order is
# significant:
#   * `timeout` must precede the generic `environment_error` branch.
#   * `e2e_failure` must precede `unit_test_failure` (a playwright spec
#     also contains "expected" / "test failed" tokens).
#   * `build_failure` is a low-priority fallback before `spec_ambiguity`
#     because the build command keyword would otherwise swallow more
#     specific failure modes (a build command can produce a type error).
# Each rule may override the default subtype / likely_cause / repair_hint
# from `FAILURE_TAXONOMY`; otherwise the category defaults are used.
_FAILURE_MATCH_RULES: list[dict[str, Any]] = [
    _make_match_rule(
        "environment_error",
        subtype="timeout",
        likely_cause="The eval command did not complete within the allotted time.",
        repair_hint="Reduce hanging behavior or adjust local command assumptions before rerunning eval.",
        predicate=lambda ctx: ctx["exit_code"] is None or "timed out" in ctx["text"] or "timeout" in ctx["text"],
    ),
    _make_match_rule(
        "environment_error",
        subtype="local_environment",
        predicate=lambda ctx: any(token in ctx["text"] for token in ("eperm", "permission denied", "eaddrinuse", "address already in use")),
    ),
    _make_match_rule(
        "dependency_error",
        predicate=lambda ctx: any(token in ctx["text"] for token in ("cannot find module", "module not found", "enoent", "npm err!")),
    ),
    _make_match_rule(
        "type_error",
        predicate=lambda ctx: any(token in ctx["text"] for token in ("type error", "typescript", "tsc")),
    ),
    _make_match_rule(
        "e2e_failure",
        predicate=lambda ctx: "playwright" in ctx["text"] or "e2e" in ctx["command_name"].lower() or "e2e" in ctx["shell_command"].lower(),
    ),
    _make_match_rule(
        "unit_test_failure",
        predicate=lambda ctx: any(token in ctx["text"] for token in ("assert", "expected", "test failed", "jest", "vitest")),
    ),
    _make_match_rule(
        "runtime_exception",
        predicate=lambda ctx: any(token in ctx["text"] for token in ("referenceerror", "syntaxerror", "runtimeerror")),
    ),
    _make_match_rule(
        "build_failure",
        predicate=lambda ctx: "build" in ctx["command_name"].lower() or "build" in ctx["shell_command"].lower(),
    ),
]


def _classify_eval_failure(eval_results: dict[str, Any]) -> dict[str, Any]:
    failed_required = [
        result for result in eval_results.get("commands", []) if result.get("required") and result.get("executed") and not result.get("passed")
    ]
    if not failed_required:
        spec = FAILURE_TAXONOMY["none"]
        return {
            "failure_type": "none",
            "subtype": spec["default_subtype"],
            "likely_cause": spec["default_likely_cause"],
            "repair_action": spec["repair_hint"],
            "category_description": spec["description"],
        }
    command = failed_required[0]
    command_name = str(command.get("name") or "")
    shell_command = str(command.get("cmd") or "")
    text = "\n".join(
        str(part or "")
        for part in (command_name, shell_command, command.get("stdout"), command.get("stderr"), command.get("reason"))
    ).lower()
    ctx = {
        "text": text,
        "command_name": command_name,
        "shell_command": shell_command,
        "exit_code": command.get("exit_code"),
    }
    for rule in _FAILURE_MATCH_RULES:
        if rule["predicate"](ctx):
            spec = FAILURE_TAXONOMY[rule["category"]]
            return {
                "failure_type": rule["category"],
                "subtype": rule["subtype"],
                "command": command_name,
                "cmd": shell_command,
                "exit_code": command.get("exit_code"),
                "likely_cause": rule["likely_cause"],
                "repair_action": rule["repair_hint"],
                "category_description": spec["description"],
            }
    fallback = FAILURE_TAXONOMY["spec_ambiguity"]
    return {
        "failure_type": "spec_ambiguity",
        "subtype": fallback["default_subtype"],
        "command": command_name,
        "cmd": shell_command,
        "exit_code": command.get("exit_code"),
        "likely_cause": fallback["default_likely_cause"],
        "repair_action": fallback["repair_hint"],
        "category_description": fallback["description"],
    }


def _eval_summary(eval_results: dict[str, Any]) -> dict[str, Any]:
    return {
        "required_eval_declared": bool(eval_results.get("required_eval_declared")),
        "required_eval_executed": bool(eval_results.get("required_eval_executed")),
        "required_eval_passed": bool(eval_results.get("required_eval_passed")),
        "commands": [
            {
                "name": command.get("name"),
                "cmd": command.get("cmd"),
                "required": command.get("required"),
                "executed": command.get("executed"),
                "exit_code": command.get("exit_code"),
                "passed": command.get("passed"),
            }
            for command in eval_results.get("commands", [])
        ],
    }


def _finalize_repair_history_without_loop(candidate: dict[str, Any], eval_results: dict[str, Any], execute_eval: bool) -> None:
    repair_history = candidate["repair_history"]
    if repair_history.get("attempts"):
        return
    # final_failure is set on every terminal path so downstream consumers
    # can read it unconditionally. None means "no failure to record" (no
    # patch at all, eval skipped, or eval passed); a classified dict means
    # we observed a real failure mode.
    if not candidate.get("score", {}).get("source_patch_present"):
        repair_history["stop_reason"] = "no_source_patch_to_repair"
        repair_history["final_failure"] = None
    elif not execute_eval:
        repair_history["stop_reason"] = "eval_not_executed"
        repair_history["final_failure"] = None
    elif eval_results.get("required_eval_passed"):
        repair_history["stop_reason"] = "eval_passed_no_repair_needed"
        repair_history["final_failure"] = None
    elif eval_results.get("required_eval_executed"):
        repair_history["stop_reason"] = "repair_loop_disabled"
        repair_history["final_failure"] = _classify_eval_failure(eval_results)
    else:
        repair_history["stop_reason"] = "required_eval_not_executed"
        repair_history["final_failure"] = _classify_eval_failure(eval_results)


def _patch_worker_failure(reason: str, details: dict[str, Any]) -> dict[str, Any]:
    return {
        "patch_status": "not_generated",
        "reason": reason,
        "details": details,
        "worktree_path": details.get("worktree_path"),
        "patch_diff": f"# candidate-a patch worker did not produce a source diff\n# reason: {reason}\n",
        "changed_files": {
            "schema_version": "agentic.changed_files.v1",
            "candidate": "candidate-a",
            "base_commit": "unknown",
            "head_commit": None,
            "changed_files": [],
            "source_patch_present": False,
            "out_of_scope_changes": [],
        },
    }


def _prepare_candidate_workspace(project_path: Path, worktree: Path) -> None:
    if worktree.exists():
        shutil.rmtree(worktree)
    worktree.parent.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(
        ".agent",
        ".git",
        ".next",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "test-results",
    )
    shutil.copytree(project_path, worktree, ignore=ignore)
    _link_dependency_dirs(project_path, worktree)


def _link_dependency_dirs(project_path: Path, worktree: Path) -> None:
    for dependency_dir in project_path.rglob("node_modules"):
        relative = dependency_dir.relative_to(project_path)
        if ".agent" in relative.parts:
            continue
        target = worktree / relative
        if target.exists():
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            target.symlink_to(dependency_dir, target_is_directory=True)
        except OSError:
            # If symlinks are unavailable, eval may fail with a dependency
            # error and the repair loop can classify it from stderr.
            continue


def _render_patch_worker_prompt(
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    *,
    strategy: dict[str, str] | None = None,
) -> str:
    context_files = [item["path"] for item in context.get("relevant_files", [])[:20]]
    required_commands = [command for command in eval_harness.get("commands", []) if command.get("required")]
    strategy_block = ""
    if strategy:
        strategy_block = (
            f"\nCandidate strategy: `{strategy.get('label')}`\n"
            f"Strategy guidance: {strategy.get('prompt_hint')}\n"
        )

    # RC-2C.1.4: closing 3 prompt gaps surfaced by the read-only review.
    # (a) success_criteria was being silently dropped — Codex never saw
    # the explicit acceptance bar the task-graph parser extracted from
    # `- bullet` lines under each H2 in requirements.md.
    success_criteria = intent.get("success_criteria") or []
    if success_criteria:
        success_block = (
            "\nSuccess criteria (every item must hold after your patch):\n"
            + "\n".join(f"  - {item}" for item in success_criteria)
            + "\n"
        )
    else:
        success_block = "\nSuccess criteria: none provided.\n"

    # (b) previous_completed_tasks lets task-002 / task-003 know what
    # task-001 already did, so the patch worker doesn't restructure or
    # overwrite committed work. The autonomous controller is responsible
    # for populating this field in intent_overrides; the runtime trusts
    # whatever it receives.
    previous_tasks = intent.get("previous_completed_tasks") or []
    if previous_tasks:
        prev_block = (
            "\nPrevious completed tasks in this session "
            "(their changes are already committed; build on them, do not undo):\n"
            + "\n".join(
                f"  - {t.get('id', '?')} {t.get('title', '')}  "
                f"(commit={t.get('commit') or '?'}, run={t.get('run_id') or '?'})"
                for t in previous_tasks
            )
            + "\n"
        )
    else:
        prev_block = ""

    return f"""You are the patch-worker for Local Agent Dev Studio's agentic_project runtime.

Goal:
{intent["goal"]}
{success_block}{prev_block}{strategy_block}
You must produce a real source/test/config patch in this isolated workspace.
Do not edit .agent/**. Do not add dependencies. Do not run migrations. Do not write outside the workspace.
Do not create new route, error, or not-found files unless the success criteria explicitly ask for them.
For copy, first-screen, or UI polish changes, prefer editing existing app/page.tsx and app/layout.tsx only.

Allowed path globs:
{json.dumps(intent["allowed_change_scope"]["paths"], indent=2)}

Use these context files first:
{json.dumps(context_files, indent=2)}

Required eval commands that the orchestrator will run after your patch:
{json.dumps(required_commands, indent=2)}

Make a small, bounded improvement that advances the goal and can be validated by the declared eval harness.
Prefer touching the source and test files listed above (or under the allowed path globs) over writing documentation.
When done, leave files modified in the workspace and reply with a short summary of changed files.
"""


def _diff_directories(
    base: Path,
    changed: Path,
    allowed_paths: list[str],
    *,
    candidate_id: str = "candidate-a",
) -> dict[str, Any]:
    base_files = set(_discover_files(base))
    changed_files_set = set(_discover_files(changed))
    all_paths = sorted((base_files | changed_files_set) - {".DS_Store"})
    entries: list[dict[str, Any]] = []
    for relative in all_paths:
        if relative.startswith(".agent/"):
            continue
        base_path = base / relative
        changed_path = changed / relative
        base_exists = base_path.exists()
        changed_exists = changed_path.exists()
        if base_exists and changed_exists and _file_bytes(base_path) == _file_bytes(changed_path):
            continue
        category = _change_category(relative)
        entry = {
            "path": relative,
            "change_type": _change_type(base_exists, changed_exists),
            "category": category,
            "within_scope": _path_allowed(relative, allowed_paths),
        }
        entries.append(entry)
    source_patch_present = bool(entries) and any(item["category"] in {"source", "test", "config"} for item in entries)
    # RC-3E.2: build the patch via a real `git diff` in an ephemeral repo
    # rather than hand-serializing unified diffs via `difflib`. The prior
    # difflib path emitted patches without `diff --git` headers / new-file
    # mode markers / hunk-end normalization, which `git apply` rejected as
    # `corrupt patch` once a non-trivial multi-file candidate was produced
    # (RC-3E.2 root cause). The ephemeral-repo path yields canonical
    # output that `git apply --check` accepts deterministically.
    patch_diff, apply_check = _build_git_patch(base, changed, entries)
    return {
        "source_patch_present": source_patch_present,
        "patch_diff": patch_diff,
        "changed_files": {
            "schema_version": "agentic.changed_files.v1",
            "candidate": candidate_id,
            "base_commit": _run_git(base, ["rev-parse", "--short", "HEAD"]) or "unknown",
            "head_commit": "working-tree" if entries else None,
            "changed_files": entries,
            "source_patch_present": source_patch_present,
            "out_of_scope_changes": [item for item in entries if not item["within_scope"]],
            # RC-3E.2: result of `git apply --check patch.diff` against a
            # clean copy of `base`. Promotion Gate consumes this via the
            # `patch_apply_check_passed` hard gate, so a corrupt patch is
            # rejected at promotion time rather than discovered at the
            # Apply Gate (which previously was the first detector and
            # blocked the run with no actionable signal upstream).
            "patch_apply_check_passed": apply_check["passed"],
            "patch_apply_check_stderr": apply_check["stderr"],
        },
    }


def _build_git_patch(
    base: Path,
    changed: Path,
    entries: list[dict[str, Any]],
) -> tuple[str, dict[str, Any]]:
    """Build a canonical `git diff` patch and self-validate it.

    Returns `(patch_text, apply_check_dict)` where apply_check_dict is
    `{"passed": bool, "stderr": str | None}`. An empty entries list
    short-circuits to `("", {"passed": True, "stderr": None})`.

    Strategy: stage `base` + `changed` projections into an ephemeral git
    repo (NOT the real project, NOT the candidate worktree — both of
    which have parent-repo / nested-worktree confounders), then read
    `git diff --binary HEAD` to get a canonical patch, then run
    `git apply --check` on the same patch in the same ephemeral repo
    to verify it is well-formed. If `git` is unavailable or fails, fall
    back to a marker patch and surface the failure via apply_check.

    Notes on robustness:
    - `--binary` keeps the patch deterministic for binary diffs (legacy
      "Binary files differ" line was not git-applyable).
    - Files are staged via `git add -A` so new/deleted files emit the
      proper `new file mode` / `deleted file mode` headers.
    - The candidate worktree is NEVER used as a git repo; we copy the
      file bytes into a fresh tempdir to side-step the `.agent/worktrees/`
      nested-repo footgun (RC-3E.2 finding: running `git diff` inside
      a nested worktree resolved to the parent repo and produced a
      bogus patch listing `task-graph.json`).
    """
    if not entries:
        return "", {"passed": True, "stderr": None}
    try:
        with tempfile.TemporaryDirectory(prefix="agentic-patch-") as tmp:
            repo = Path(tmp)
            # 1. Stage base content. We seed the repo with EVERY file
            #    discovered in `base` (not just the changed ones) so the
            #    base commit is faithful and `git diff HEAD` covers
            #    additions/deletions correctly relative to base.
            for relative in _discover_files(base):
                src = base / relative
                if not src.is_file():
                    continue
                dst = repo / relative
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src.read_bytes())
            _git(repo, ["init", "-q", "-b", "main"])
            _git(repo, ["config", "user.email", "agentic-diff@local"])
            _git(repo, ["config", "user.name", "agentic-diff"])
            _git(repo, ["config", "commit.gpgsign", "false"])
            _git(repo, ["add", "-A"])
            _git(repo, ["commit", "-q", "--allow-empty", "-m", "base"])
            # 2. Apply the changed projection: copy each entry's `changed`
            #    bytes (or remove for deletes). Only touch the entries we
            #    classified — un-changed files stay at base.
            for entry in entries:
                relative = str(entry["path"])
                src_changed = changed / relative
                dst = repo / relative
                if entry["change_type"] == "deleted":
                    if dst.exists():
                        dst.unlink()
                    continue
                if not src_changed.is_file():
                    # Defensive: classification said added/modified but file
                    # missing in changed snapshot. Skip silently rather than
                    # corrupt the patch.
                    continue
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(src_changed.read_bytes())
            _git(repo, ["add", "-A"])
            # 3. Generate canonical patch.
            diff_proc = subprocess.run(
                ["git", "-C", str(repo), "diff", "--binary", "--cached", "HEAD"],
                capture_output=True, text=True, check=False, timeout=30,
            )
            if diff_proc.returncode != 0:
                return (
                    f"# patch generation failed: git diff exit {diff_proc.returncode}\n# stderr: {diff_proc.stderr.strip()}\n",
                    {"passed": False, "stderr": diff_proc.stderr.strip() or "git diff failed"},
                )
            patch_text = diff_proc.stdout
            if not patch_text:
                # Entries existed (binary equality, mode-only, etc.), but
                # diff is empty. Still considered well-formed.
                return "", {"passed": True, "stderr": None}
            # 4. Self-validate with apply --check against the BASE state
            #    (reset to HEAD first so we're not checking against the
            #    already-staged target).
            _git(repo, ["reset", "-q", "--hard", "HEAD"])
            patch_path = repo / ".agentic-patch.diff"
            patch_path.write_bytes(patch_text.encode("utf-8"))
            check_proc = subprocess.run(
                ["git", "-C", str(repo), "apply", "--check", str(patch_path)],
                capture_output=True, text=True, check=False, timeout=30,
            )
            apply_check = {
                "passed": check_proc.returncode == 0,
                "stderr": (check_proc.stderr.strip() or check_proc.stdout.strip() or None) if check_proc.returncode != 0 else None,
            }
            return patch_text, apply_check
    except (OSError, subprocess.TimeoutExpired) as exc:
        return (
            f"# patch generation failed: {exc.__class__.__name__}: {exc}\n",
            {"passed": False, "stderr": f"{exc.__class__.__name__}: {exc}"},
        )


def _git(repo: Path, args: list[str]) -> None:
    """Run a git command in `repo`, raising on nonzero exit. Helper for
    `_build_git_patch` only — _run_git is intentionally lenient for the
    HEAD lookup elsewhere; this one fails loud so patch generation
    surfaces problems via the apply_check dict path."""
    completed = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=False, timeout=30,
    )
    if completed.returncode != 0:
        raise OSError(f"git {' '.join(args)} exited {completed.returncode}: {completed.stderr.strip()}")


def _file_bytes(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError:
        return b""


def _change_type(base_exists: bool, changed_exists: bool) -> str:
    if base_exists and changed_exists:
        return "modified"
    if changed_exists:
        return "added"
    return "deleted"


def _change_category(path: str) -> str:
    lower = path.lower()
    if _looks_like_test(lower):
        return "test"
    if _context_bucket(path) in _SOURCE_BUCKETS:
        return "source"
    if _context_bucket(path) == "repo_manifest":
        return "config"
    if lower.startswith("docs/"):
        return "docs"
    if lower.startswith(".agent/"):
        return "agent_artifact"
    return "other"


def _unified_file_diff(base_path: Path, changed_path: Path, relative: str) -> str:
    if _is_probably_binary(base_path) or _is_probably_binary(changed_path):
        return f"diff --git a/{relative} b/{relative}\nBinary files differ\n"
    base_text = _read_diff_text(base_path) if base_path.exists() else ""
    changed_text = _read_diff_text(changed_path) if changed_path.exists() else ""
    return "".join(
        difflib.unified_diff(
            base_text.splitlines(keepends=True),
            changed_text.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )


def _read_diff_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _is_probably_binary(path: Path) -> bool:
    if not path.exists() or not path.is_file():
        return False
    try:
        chunk = path.read_bytes()[:2048]
    except OSError:
        return False
    return b"\0" in chunk


def _execute_eval_harness(
    project_path: Path,
    eval_harness: dict[str, Any],
    candidate: dict[str, Any],
    *,
    execute_eval: bool,
    timeout_sec: int,
) -> dict[str, Any]:
    commands = list(eval_harness.get("commands") or [])
    required = [command for command in commands if command.get("required")]
    root = Path(candidate["worktree_path"]) if candidate.get("worktree_path") else project_path
    results: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    candidate_id = str(candidate.get("id") or "candidate-a")
    if not execute_eval:
        for command in commands:
            results.append(_not_executed_result(command, "execution_not_requested"))
        events.append(
            {
                "ts": now_iso(),
                "candidate": candidate_id,
                "event": "eval.not_executed",
                "message": "Eval harness declared but execution was not requested.",
            }
        )
    else:
        for command in commands:
            result = _execute_eval_command(root, command, timeout_sec)
            results.append(result)
            events.append(
                {
                    "ts": now_iso(),
                    "candidate": candidate_id,
                    "event": "eval.command",
                    "message": f"{command.get('name')}: {'passed' if result['passed'] else 'failed'}",
                    "details": {
                        "name": result["name"],
                        "exit_code": result["exit_code"],
                        "required": result["required"],
                    },
                }
            )
    required_results = [result for result in results if result.get("required")]
    required_executed = bool(required) and all(result.get("executed") for result in required_results)
    required_passed = bool(required) and all(result.get("passed") for result in required_results)
    payload = {
        "schema_version": "agentic.eval_results.v1",
        "execution_requested": execute_eval,
        "execution_root": str(root),
        "required_eval_declared": bool(required),
        "required_eval_executed": required_executed,
        "required_eval_passed": required_passed,
        "commands": results,
        "events": events,
    }
    if required_executed and not required_passed:
        payload["failure_summary"] = _classify_eval_failure(payload)
    return payload


def _not_executed_result(command: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "name": command.get("name"),
        "cmd": command.get("cmd"),
        "cwd": command.get("cwd", "."),
        "required": bool(command.get("required")),
        "declared": True,
        "executed": False,
        "exit_code": None,
        "passed": False,
        "reason": reason,
        "stdout": "",
        "stderr": "",
    }


def _execute_eval_command(root: Path, command: dict[str, Any], timeout_sec: int) -> dict[str, Any]:
    command_timeout = min(int(command.get("timeout_sec") or timeout_sec), timeout_sec)
    cwd = root / str(command.get("cwd") or ".")
    shell_command = str(command.get("cmd") or "")
    if command.get("type") == "deterministic_file_check":
        target = shell_command.removeprefix("test -f ").strip()
        passed = (cwd / target).exists()
        return {
            "name": command.get("name"),
            "cmd": shell_command,
            "cwd": command.get("cwd", "."),
            "required": bool(command.get("required")),
            "declared": True,
            "executed": True,
            "exit_code": 0 if passed else 1,
            "passed": passed,
            "stdout": "",
            "stderr": "" if passed else f"missing file: {target}",
        }
    try:
        _prepare_eval_command_workspace(cwd, shell_command)
        env = os.environ.copy()
        lower_command = shell_command.lower()
        if "next build" in lower_command or "npm run build" in lower_command:
            env["NODE_ENV"] = "production"
        elif env.get("NODE_ENV") not in {None, "production", "development", "test"}:
            env.pop("NODE_ENV", None)
        completed = subprocess.run(
            shell_command,
            cwd=cwd,
            shell=True,
            text=True,
            capture_output=True,
            timeout=command_timeout,
            check=False,
            env=env,
        )
        return {
            "name": command.get("name"),
            "cmd": shell_command,
            "cwd": command.get("cwd", "."),
            "required": bool(command.get("required")),
            "declared": True,
            "executed": True,
            "exit_code": completed.returncode,
            "passed": completed.returncode == 0,
            "stdout": completed.stdout[-12000:],
            "stderr": completed.stderr[-12000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": command.get("name"),
            "cmd": shell_command,
            "cwd": command.get("cwd", "."),
            "required": bool(command.get("required")),
            "declared": True,
            "executed": True,
            "exit_code": None,
            "passed": False,
            "failure_type": "environment_error",
            "reason": f"timed out after {command_timeout}s",
            "stdout": (exc.stdout or "")[-12000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-12000:] if isinstance(exc.stderr, str) else "",
        }


def _prepare_eval_command_workspace(cwd: Path, shell_command: str) -> None:
    """Remove generated build artifacts that can make isolated eval flaky.

    Next.js build output is intentionally ignored by git, but prior failed
    evals in a candidate copy can still leave `.next` and incremental
    TypeScript artifacts behind. Clean them before build/typecheck commands
    so promotion depends on source state, not stale generated files.
    """
    lower = shell_command.lower()
    if "next build" not in lower and "npm run build" not in lower and "npm run typecheck" not in lower:
        return
    for name in (".next", "tsconfig.tsbuildinfo"):
        target = cwd / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            try:
                target.unlink()
            except OSError:
                pass


def _write_critic_reports(
    project_path: Path,
    run_dir: Path,
    intent: dict[str, Any],
    context: dict[str, Any],
    candidate: dict[str, Any],
    eval_harness: dict[str, Any],
) -> list[str]:
    """Write critic panel reports grounded in this candidate's actual evidence.

    Each critic reads from candidate (patch_diff, changed_files, score,
    repair_history, eval_results) and eval_harness to produce findings that
    cite specific paths, command names, failure types, and repair outcomes —
    not generic templates. No LLM calls; pure derivation from artifacts.

    Output location: `<run_dir>/candidates/<candidate_id>/critics/*.md` so
    multi-candidate runs keep each candidate's critic findings co-located
    with its other artifacts.
    """
    candidate_id = str(candidate.get("id") or "candidate-a")
    eval_results = candidate.get("eval_results") or {}
    changed_files_block = candidate.get("changed_files") or {}
    changed_paths = [
        str(item.get("path") or "")
        for item in (changed_files_block.get("changed_files") or [])
        if isinstance(item, dict) and item.get("path")
    ]
    out_of_scope = [
        str(item.get("path") or "") if isinstance(item, dict) else str(item)
        for item in (changed_files_block.get("out_of_scope_changes") or [])
    ]
    repair_history = candidate.get("repair_history") or {}
    score = candidate.get("score") or {}
    reports = {
        "correctness.md": _critic_correctness(intent, eval_harness, eval_results, score, repair_history),
        "regression.md": _critic_regression(eval_harness, eval_results, score, changed_paths),
        "security.md": _critic_security(intent, score, changed_paths, out_of_scope),
        "ux.md": _critic_ux(eval_harness, changed_paths),
        "overfit.md": _critic_overfit(eval_results, changed_paths, repair_history),
    }
    critics_dir = run_dir / "candidates" / candidate_id / "critics"
    written: list[str] = []
    for filename, content in reports.items():
        written.append(_write_text(project_path, critics_dir / filename, content))
    return written


# Heuristic patterns used by the security and UX critics. Kept here so the
# reasoning is visible and reviewable rather than buried in regex literals.
_SECURITY_SENSITIVE_PATTERNS: tuple[tuple[str, str], ...] = (
    (".env", "environment / secret file touched"),
    ("package.json", "package manifest touched (possible dependency change)"),
    ("package-lock.json", "lock file touched (possible dependency drift)"),
    ("requirements.txt", "Python dependency manifest touched"),
    ("pyproject.toml", "Python project metadata touched"),
    ("Dockerfile", "container image definition touched"),
    ("auth", "auth-surface path touched"),
    ("session", "session-handling path touched"),
    ("/migrations/", "database migration touched"),
    ("/scripts/", "build/operational script touched"),
)
_UI_PATH_HINTS: tuple[str, ...] = ("apps/web/app/", "apps/web/components/", "apps/web/styles", "apps/web/index.html")
_TEST_PATH_HINTS: tuple[str, ...] = ("/tests/", "tests/", "/__tests__/", ".test.", ".spec.")


def _critic_correctness(
    intent: dict[str, Any],
    eval_harness: dict[str, Any],
    eval_results: dict[str, Any],
    score: dict[str, Any],
    repair_history: dict[str, Any],
) -> str:
    findings: list[str] = []
    success_criteria = intent.get("success_criteria") or []
    findings.append(f"Intent contract declares {len(success_criteria)} success criteria.")
    required = [c for c in eval_harness.get("commands", []) if c.get("required")]
    executed = [c for c in eval_results.get("commands", []) if c.get("required") and c.get("executed")]
    passed = [c for c in eval_results.get("commands", []) if c.get("required") and c.get("passed")]
    failed = [c for c in eval_results.get("commands", []) if c.get("required") and c.get("executed") and not c.get("passed")]
    findings.append(f"Required eval commands: declared={len(required)}, executed={len(executed)}, passed={len(passed)}, failed={len(failed)}.")
    if not bool(score.get("source_patch_present")):
        findings.append("No product-source patch was generated by the candidate worker; functional correctness cannot be claimed without a diff to evaluate.")
    if failed:
        names = ", ".join(f"`{c.get('name')}`" for c in failed[:5])
        findings.append(f"Failing required commands: {names}.")
    final_failure = repair_history.get("final_failure")
    if isinstance(final_failure, dict) and final_failure.get("failure_type"):
        findings.append(
            f"Final classified failure: `{final_failure.get('failure_type')}`"
            + (f" (subtype: `{final_failure.get('subtype')}`)" if final_failure.get("subtype") else "")
            + f". Likely cause: {final_failure.get('likely_cause') or 'unspecified'}."
        )
    attempts = repair_history.get("attempts") or []
    if attempts:
        findings.append(f"Repair attempts: {len(attempts)} (max_loops={repair_history.get('max_loops')}, stop_reason=`{repair_history.get('stop_reason')}`).")
    return _critic_report("Correctness Critic", findings)


def _critic_regression(
    eval_harness: dict[str, Any],
    eval_results: dict[str, Any],
    score: dict[str, Any],
    changed_paths: list[str],
) -> str:
    findings: list[str] = []
    findings.append(f"Patch touches {len(changed_paths)} file(s) within scope.")
    if not changed_paths:
        findings.append("No diff to regress: regression risk is structurally zero for this run.")
        return _critic_report("Regression Critic", findings)
    test_paths = [p for p in changed_paths if any(hint in p for hint in _TEST_PATH_HINTS)]
    non_test_paths = [p for p in changed_paths if p not in set(test_paths)]
    if test_paths:
        findings.append(f"Test files modified ({len(test_paths)}): {', '.join(f'`{p}`' for p in test_paths[:5])}{'...' if len(test_paths) > 5 else ''}.")
    if non_test_paths:
        findings.append(f"Non-test source files modified ({len(non_test_paths)}). Sample: {', '.join(f'`{p}`' for p in non_test_paths[:5])}{'...' if len(non_test_paths) > 5 else ''}.")
    findings.append(
        f"Eval coverage on changes: required executed={bool(eval_results.get('required_eval_executed'))}, passed={bool(eval_results.get('required_eval_passed'))}."
    )
    if not eval_results.get("required_eval_executed"):
        findings.append("Without executed required eval, regression risk on these paths cannot be quantified — treat as elevated.")
    return _critic_report("Regression Critic", findings)


def _critic_security(
    intent: dict[str, Any],
    score: dict[str, Any],
    changed_paths: list[str],
    out_of_scope: list[str],
) -> str:
    findings: list[str] = []
    requires_approval = intent.get("requires_human_approval") or []
    findings.append(f"Intent contract names {len(requires_approval)} change(s) that require human approval.")
    sensitive_hits: list[tuple[str, str]] = []
    for path in changed_paths:
        for token, reason in _SECURITY_SENSITIVE_PATTERNS:
            if token in path:
                sensitive_hits.append((path, reason))
                break
    if sensitive_hits:
        findings.append(f"Sensitive in-scope files touched ({len(sensitive_hits)}):")
        for path, reason in sensitive_hits[:5]:
            findings.append(f"  - `{path}` — {reason}.")
    else:
        findings.append("No in-scope changed file matches the security-sensitive heuristic patterns (env, deps, migrations, auth, session, scripts, Dockerfile).")
    if out_of_scope:
        findings.append(f"Patch worker attempted {len(out_of_scope)} out-of-scope write(s):")
        for path in out_of_scope[:5]:
            findings.append(f"  - `{path}` — blocked by allowed_change_scope.")
    if not score.get("diff_within_scope"):
        findings.append("score.diff_within_scope is False — Promotion Gate will fail the diff_within_scope hard gate.")
    return _critic_report("Security Critic", findings)


def _critic_ux(eval_harness: dict[str, Any], changed_paths: list[str]) -> str:
    findings: list[str] = []
    visual_checks = eval_harness.get("visual_checks") or []
    findings.append(f"Visual checks declared in eval harness: {len(visual_checks)}.")
    ui_paths = [p for p in changed_paths if any(p.startswith(hint) or hint in p for hint in _UI_PATH_HINTS)]
    if ui_paths:
        findings.append(f"Patch touches {len(ui_paths)} UI-shaped path(s): {', '.join(f'`{p}`' for p in ui_paths[:5])}{'...' if len(ui_paths) > 5 else ''}.")
        if not visual_checks:
            findings.append("UI surface modified but no visual check declared — UX claim is unverified by this run.")
        else:
            findings.append("Declared visual checks must produce captured screenshot/browser evidence before UX is considered verified.")
    elif visual_checks:
        findings.append("Visual checks are declared but no UI-shaped paths were modified in this run.")
    else:
        findings.append("No UI surface was modified and no visual checks were declared; UX scope is structurally inert for this run.")
    return _critic_report("UX Critic", findings)


def _critic_overfit(
    eval_results: dict[str, Any],
    changed_paths: list[str],
    repair_history: dict[str, Any],
) -> str:
    findings: list[str] = []
    test_paths = [p for p in changed_paths if any(hint in p for hint in _TEST_PATH_HINTS)]
    if test_paths:
        findings.append(f"Tests altered in this candidate ({len(test_paths)}): {', '.join(f'`{p}`' for p in test_paths[:5])}{'...' if len(test_paths) > 5 else ''}.")
        findings.append("Test edits must be reviewed for assertion-weakening (changed expected values, removed cases, skipped tests) — promotion should not rest on relaxed tests.")
    else:
        findings.append("No tests were altered — assertion-weakening is structurally not present in this diff.")
    attempts = repair_history.get("attempts") or []
    if attempts:
        # If repair touched tests across attempts, flag it as suspicious.
        repair_changed_tests = False
        for attempt in attempts:
            count = attempt.get("changed_files_count")
            if count and not test_paths:
                continue
        findings.append(
            f"Repair loop ran {len(attempts)} attempt(s) (stop_reason=`{repair_history.get('stop_reason')}`). "
            "Each attempt must be cross-checked against eval delta, not just exit code, to rule out test-gaming."
        )
        if test_paths:
            findings.append("Combined signal — repair attempted AND tests modified — warrants explicit human review of the test diffs.")
    findings.append(
        f"Required eval signal: executed={bool(eval_results.get('required_eval_executed'))}, "
        f"passed={bool(eval_results.get('required_eval_passed'))}."
    )
    return _critic_report("Overfit Critic", findings)


# MVP-3A: deterministic candidate scoring. The scorer is intentionally
# simple and inspectable — it does not call an LLM. Hard disqualifiers
# remove candidates from selection eligibility; soft components and
# penalties differentiate eligible candidates so the Promotion Gate can
# pick a winner. Weights are fixed constants; tune them in code, not at
# runtime.
_SCORE_HARD_DISQUALIFIERS: tuple[str, ...] = (
    "source_patch_present",        # must be True
    "required_eval_executed",      # must be True
    "required_eval_passed",        # must be True
    "no_out_of_scope_changes",     # must be True
    "no_critical_security_finding", # must be True
)
_SCORE_COMPONENTS: dict[str, int] = {
    "required_eval": 40,
    "optional_eval": 15,
    "repair_stability": 15,
    "scope_safety": 10,
    "critic_risk": 10,
    "test_relevance": 5,
    "context_alignment": 5,
}
_SCORE_PENALTIES: dict[str, int] = {
    "repeated_failure_type": 10,
    "docs_only_patch": 10,
    "test_only_patch": 15,
}


def _evaluate_candidate_hard_gates(
    candidate: dict[str, Any],
    eval_results: dict[str, Any],
) -> dict[str, Any]:
    """Compute the hard-gate dict for one candidate. Each value is True/False.

    A candidate is `disqualified` when ANY hard gate is False. Promotion Gate
    must never select a disqualified candidate.
    """
    score = candidate.get("score") or {}
    changed = candidate.get("changed_files") or {}
    out_of_scope = changed.get("out_of_scope_changes") or []
    changed_paths = [
        str(item.get("path") or "")
        for item in (changed.get("changed_files") or [])
        if isinstance(item, dict) and item.get("path")
    ]
    # Critical security finding heuristic: any in-scope path matching a
    # security-sensitive pattern OR any out-of-scope write at all.
    has_critical_security = bool(out_of_scope)
    if not has_critical_security:
        for path in changed_paths:
            for token, _reason in _SECURITY_SENSITIVE_PATTERNS:
                if token in path:
                    has_critical_security = True
                    break
            if has_critical_security:
                break
    # RC-3E.2: surface the apply-check result as a hard gate. Default to
    # True (do not punish older candidates / migration paths that pre-date
    # this field). The patch generator (`_build_git_patch`) self-checks
    # and stores the result on changed_files; here we just surface it.
    apply_check_present = "patch_apply_check_passed" in changed
    patch_apply_check_passed = bool(changed.get("patch_apply_check_passed", True)) if apply_check_present else True
    return {
        "source_patch_present": bool(score.get("source_patch_present")),
        "required_eval_executed": bool(eval_results.get("required_eval_executed")),
        "required_eval_passed": bool(eval_results.get("required_eval_passed")),
        "no_out_of_scope_changes": not bool(out_of_scope),
        "no_critical_security_finding": not has_critical_security,
        "patch_apply_check_passed": patch_apply_check_passed,
    }


def _score_candidate(
    candidate: dict[str, Any],
    *,
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_results: dict[str, Any],
    abandonment_history: list[dict[str, Any]] | None = None,
    patch_worker: str = "none",
) -> dict[str, Any]:
    """Deterministic scorer. Output shape:

    {
      "candidate": "candidate-b",
      "strategy": "test-focused",
      "disqualified": False,
      "hard_gates": {...},
      "components": {required_eval: 40, optional_eval: 15, ...},
      "penalties": {repeated_failure_type: 10, ...},
      "total": 90,
      "explanation": "..."
    }

    `total` = sum(components) - sum(penalties), clamped to >= 0.
    A disqualified candidate keeps its components/penalties for
    transparency but is never selected by the Promotion Gate.
    """
    hard_gates = _evaluate_candidate_hard_gates(candidate, eval_results)
    disqualified = not all(hard_gates.values())

    score_block = candidate.get("score") or {}
    repair_history = candidate.get("repair_history") or {}
    changed = candidate.get("changed_files") or {}
    changed_paths = [
        str(item.get("path") or "")
        for item in (changed.get("changed_files") or [])
        if isinstance(item, dict) and item.get("path")
    ]

    commands = eval_results.get("commands") or []
    required_passed = all(c.get("passed") for c in commands if c.get("required") and c.get("executed")) and any(
        c.get("required") and c.get("executed") for c in commands
    )
    optional_passed_count = sum(1 for c in commands if not c.get("required") and c.get("executed") and c.get("passed"))
    optional_total_count = sum(1 for c in commands if not c.get("required"))

    attempts = repair_history.get("attempts") or []
    attempt_count = len(attempts)

    components: dict[str, int] = {}
    components["required_eval"] = _SCORE_COMPONENTS["required_eval"] if required_passed else 0
    if optional_total_count > 0:
        # Pro-rate by ratio of passing optional commands.
        ratio = optional_passed_count / optional_total_count
        components["optional_eval"] = int(round(_SCORE_COMPONENTS["optional_eval"] * ratio))
    else:
        components["optional_eval"] = 0
    if attempt_count == 0:
        components["repair_stability"] = _SCORE_COMPONENTS["repair_stability"]
    elif attempt_count == 1 and repair_history.get("stop_reason") == "eval_passed_after_repair":
        components["repair_stability"] = int(round(_SCORE_COMPONENTS["repair_stability"] * 0.6))
    else:
        components["repair_stability"] = 0
    components["scope_safety"] = _SCORE_COMPONENTS["scope_safety"] if hard_gates["no_out_of_scope_changes"] else 0

    # critic_risk: penalize when critical_security_finding is present; full
    # marks otherwise. (Hard gate already disqualifies; this still
    # contributes to differentiating eligible candidates.)
    components["critic_risk"] = _SCORE_COMPONENTS["critic_risk"] if hard_gates["no_critical_security_finding"] else 0

    # test_relevance: candidate touches test files (any).
    test_paths = [p for p in changed_paths if any(hint in p for hint in _TEST_PATH_HINTS)]
    components["test_relevance"] = _SCORE_COMPONENTS["test_relevance"] if test_paths else 0

    # context_alignment: candidate's changed paths overlap with the
    # context_pack's relevant_files (the patch worker actually used the
    # context it was given).
    relevant_paths = {str(item.get("path") or "") for item in (context.get("relevant_files") or []) if isinstance(item, dict)}
    overlap = sum(1 for p in changed_paths if p in relevant_paths)
    components["context_alignment"] = _SCORE_COMPONENTS["context_alignment"] if overlap else 0

    penalties: dict[str, int] = {key: 0 for key in _SCORE_PENALTIES}
    # repeated_failure_type: this candidate's final_failure type matches
    # any prior abandonment for the same patch_worker on this project.
    final_failure = repair_history.get("final_failure")
    if isinstance(final_failure, dict) and final_failure.get("failure_type"):
        ftype = str(final_failure.get("failure_type"))
        if abandonment_history and patch_worker:
            prior = _count_prior_abandonments(abandonment_history, patch_worker=patch_worker, failure_type=ftype)
            if prior >= 1:
                penalties["repeated_failure_type"] = _SCORE_PENALTIES["repeated_failure_type"]
    # docs_only_patch: every changed path is under docs/ (caller wanted
    # source change but got docs only).
    if changed_paths and all(p.startswith("docs/") or "/docs/" in p for p in changed_paths):
        penalties["docs_only_patch"] = _SCORE_PENALTIES["docs_only_patch"]
    # test_only_patch: every changed path is a test file. Skipped when
    # intent itself is test-focused (success criteria mention "test").
    if changed_paths and test_paths and len(test_paths) == len(changed_paths):
        intent_text = " ".join(str(c) for c in (intent.get("success_criteria") or []) + [str(intent.get("goal") or "")]).lower()
        if "test" not in intent_text:
            penalties["test_only_patch"] = _SCORE_PENALTIES["test_only_patch"]

    total = max(0, sum(components.values()) - sum(penalties.values()))
    return {
        "candidate": str(candidate.get("id") or "candidate-a"),
        "strategy": str(candidate.get("strategy") or score_block.get("strategy") or ""),
        "disqualified": disqualified,
        "hard_gates": hard_gates,
        "components": components,
        "penalties": penalties,
        "total": total,
        "explanation": _explain_score(disqualified, hard_gates, components, penalties),
    }


def _explain_score(
    disqualified: bool,
    hard_gates: dict[str, bool],
    components: dict[str, int],
    penalties: dict[str, int],
) -> str:
    if disqualified:
        failed = [name for name, ok in hard_gates.items() if not ok]
        return f"disqualified by hard gate(s): {', '.join(failed)}"
    earned = [f"{k}={v}" for k, v in components.items() if v > 0]
    losses = [f"{k}=-{v}" for k, v in penalties.items() if v > 0]
    parts = []
    if earned:
        parts.append("earned " + ", ".join(earned))
    if losses:
        parts.append("penalized " + ", ".join(losses))
    return "; ".join(parts) or "no scoring signal in either direction"


def _jaccard_distance(left: set[str], right: set[str]) -> float:
    if not left and not right:
        return 0.0
    intersection = len(left & right)
    union = len(left | right)
    if union == 0:
        return 0.0
    return 1.0 - (intersection / union)


def _compute_candidate_diversity(candidates: list[dict[str, Any]]) -> dict[str, Any]:
    """Pairwise Jaccard distance over each candidate's changed file set.

    Returns:
      {
        "method": "changed_file_jaccard_distance",
        "average": 0.42,
        "pairs": [{left, right, distance}, ...]
      }

    With 0 or 1 candidates, average is 0.0 and pairs is empty.
    """
    sets: list[tuple[str, set[str]]] = []
    for candidate in candidates:
        cid = str(candidate.get("id") or "")
        if not cid:
            continue
        changed = candidate.get("changed_files") or {}
        paths = {
            str(item.get("path") or "")
            for item in (changed.get("changed_files") or [])
            if isinstance(item, dict) and item.get("path")
        }
        sets.append((cid, paths))
    pairs: list[dict[str, Any]] = []
    distances: list[float] = []
    for i in range(len(sets)):
        for j in range(i + 1, len(sets)):
            left_id, left_set = sets[i]
            right_id, right_set = sets[j]
            distance = _jaccard_distance(left_set, right_set)
            distances.append(distance)
            pairs.append({"left": left_id, "right": right_id, "distance": round(distance, 3)})
    average = round(sum(distances) / len(distances), 3) if distances else 0.0
    return {
        "method": "changed_file_jaccard_distance",
        "average": average,
        "pairs": pairs,
    }


def _build_promotion_report(
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    candidates: list[dict[str, Any]],
    trace: list[dict[str, Any]],
    *,
    project_path: Path | None = None,
    patch_worker: str = "none",
) -> dict[str, Any]:
    """Build the promotion report from a list of candidates (MVP-3A).

    Each candidate dict must include `id`, `score`, `changed_files`,
    `repair_history`, and an embedded `eval_results` (the runtime stores
    eval_results under candidate['eval_results']).

    Selection logic (per Chuan's MVP-3A spec):
      1. Score every candidate deterministically.
      2. eligible = candidates passing all hard gates.
      3. If eligible: select max(score.total). decision = "promote"
      4. Else if all candidates lack source_patch_present: needs-more-context
      5. Else if any candidate is repairable (failed eval, repair NOT yet
         attempted): "repair"
      6. Else if all candidates with failed eval have already exhausted
         repair: "abandoned"
      7. Else: "needs-human-review"
    """
    context_has_source_files = bool(context.get("context_quality", {}).get("has_source_files"))
    required_commands = [command for command in eval_harness.get("commands", []) if command.get("required")]
    required_eval_declared = bool(required_commands)

    abandonment_history = _read_abandonment_history(project_path) if project_path is not None else []

    # Score every candidate (their score.json is also written separately by
    # run() for per-candidate persistence; this scoring is for the report).
    candidate_scores: list[dict[str, Any]] = []
    for candidate in candidates:
        eval_results_for_candidate = candidate.get("eval_results") or {}
        candidate_scores.append(
            _score_candidate(
                candidate,
                intent=intent,
                context=context,
                eval_results=eval_results_for_candidate,
                abandonment_history=abandonment_history,
                patch_worker=patch_worker,
            )
        )

    # Per-candidate summary table.
    candidate_summaries: list[dict[str, Any]] = []
    for candidate, score in zip(candidates, candidate_scores):
        eval_results_for_candidate = candidate.get("eval_results") or {}
        repair = candidate.get("repair_history") or {}
        attempts = repair.get("attempts") or []
        candidate_summaries.append({
            "id": str(candidate.get("id") or ""),
            "strategy": str(candidate.get("strategy") or ""),
            "source_patch_present": score["hard_gates"]["source_patch_present"],
            "required_eval_executed": score["hard_gates"]["required_eval_executed"],
            "required_eval_passed": score["hard_gates"]["required_eval_passed"],
            "diff_within_scope": score["hard_gates"]["no_out_of_scope_changes"],
            "no_critical_security_finding": score["hard_gates"]["no_critical_security_finding"],
            "patch_apply_check_passed": score["hard_gates"].get("patch_apply_check_passed", True),
            "disqualified": score["disqualified"],
            "score": score["total"],
            "stop_reason": str(repair.get("stop_reason") or ""),
            "repair_attempts": len(attempts),
            "final_failure": repair.get("final_failure"),
        })

    # Selection.
    eligible = [(candidate, score) for candidate, score in zip(candidates, candidate_scores) if not score["disqualified"]]
    selected_candidate_id: str | None = None
    selected_candidate: dict[str, Any] | None = None
    selected_score: dict[str, Any] | None = None
    if eligible:
        # Sort eligible by score.total desc, tiebreak by candidate id (stable).
        eligible.sort(key=lambda pair: (-pair[1]["total"], str(pair[0].get("id") or "")))
        selected_candidate, selected_score = eligible[0]
        selected_candidate_id = str(selected_candidate.get("id") or "")

    # Decision.
    if not context_has_source_files:
        decision = "needs-more-context"
    elif not required_eval_declared:
        decision = "needs-human-review"
    elif eligible:
        decision = "promote"
    else:
        # No eligible candidate. Walk the gate-failure ladder.
        all_no_source_patch = all(not s["hard_gates"]["source_patch_present"] for s in candidate_scores) if candidate_scores else True
        any_repair_pending = any(
            (not s["hard_gates"]["required_eval_passed"])
            and s["hard_gates"]["source_patch_present"]
            and not (c.get("repair_history") or {}).get("attempts")
            for c, s in zip(candidates, candidate_scores)
        )
        all_attempted_repair_and_failed = all(
            (not s["hard_gates"]["required_eval_passed"])
            and bool((c.get("repair_history") or {}).get("attempts"))
            for c, s in zip(candidates, candidate_scores)
            if s["hard_gates"]["source_patch_present"]
        ) and any(s["hard_gates"]["source_patch_present"] for s in candidate_scores)
        if all_no_source_patch:
            decision = "needs-more-context" if not context_has_source_files else "needs-human-review"
            # Distinguish: no source files in context vs patch worker chose
            # not to change anything.
            if context_has_source_files and any(s["hard_gates"]["source_patch_present"] is False for s in candidate_scores):
                decision = "needs-human-review"
        elif any_repair_pending:
            decision = "repair"
        elif all_attempted_repair_and_failed:
            decision = "abandoned"
        else:
            decision = "needs-human-review"

    diversity = _compute_candidate_diversity(candidates)

    # Top-level eval / repair / hard_gates blocks reflect the SELECTED
    # candidate (or the first candidate when none selected, for inspection).
    reference_candidate = selected_candidate or (candidates[0] if candidates else None)
    reference_score = selected_score
    if reference_score is None and candidate_scores:
        reference_score = candidate_scores[0]
    ref_eval = (reference_candidate or {}).get("eval_results") or {}
    ref_repair = (reference_candidate or {}).get("repair_history") or {}
    ref_repair_attempts = list(ref_repair.get("attempts") or [])
    ref_score = (reference_candidate or {}).get("score") or {}
    ref_required_eval_executed = bool(ref_eval.get("required_eval_executed"))
    ref_required_eval_passed = bool(ref_eval.get("required_eval_passed"))
    ref_source_patch_present = bool(ref_score.get("source_patch_present"))
    ref_diff_within_scope = bool(ref_score.get("diff_within_scope")) if ref_score else False
    ref_repair_stop_reason = str(ref_repair.get("stop_reason") or "")
    ref_repair_abandoned = bool(ref_repair_attempts) and ref_required_eval_executed and not ref_required_eval_passed

    # RC-3E.2: surface patch_apply_check at promotion-report level so the
    # report itself records why a candidate was disqualified for a corrupt
    # patch. Default True for older candidates whose changed_files predate
    # the field (backward-compat with archived runs).
    ref_patch_apply_check_passed = (
        bool(reference_score["hard_gates"].get("patch_apply_check_passed", True))
        if reference_score else True
    )
    hard_gates = {
        "intent_contract_present": True,
        "context_pack_present": True,
        "context_has_source_files": context_has_source_files,
        "eval_harness_present": True,
        "candidate_record_present": bool(candidates),
        "trace_complete": len(trace) >= 6,
        "diff_within_scope": ref_diff_within_scope,
        "required_eval_declared": required_eval_declared,
        "required_eval_executed": ref_required_eval_executed,
        "required_eval_passed": ref_required_eval_passed,
        "source_patch_present": ref_source_patch_present,
        "no_critical_security_finding": (reference_score["hard_gates"]["no_critical_security_finding"] if reference_score else True),
        "patch_apply_check_passed": ref_patch_apply_check_passed,
    }
    gate_details = [
        _gate_detail("context_has_source_files", True, context_has_source_files, "Context pack must include source files before patch generation."),
        _gate_detail("source_patch_present", True, ref_source_patch_present, "Selected candidate must contain a non-empty source/test/config diff."),
        _gate_detail("required_eval_declared", True, required_eval_declared, "Required eval command(s) must be declared before promotion."),
        _gate_detail("required_eval_executed", True, ref_required_eval_executed, "Required eval command(s) must execute before promotion."),
        _gate_detail("required_eval_passed", True, ref_required_eval_passed, "Required eval command(s) must pass before promotion."),
        _gate_detail("patch_apply_check_passed", True, ref_patch_apply_check_passed, "Generated patch.diff must pass `git apply --check` against a clean copy of the base before promotion (RC-3E.2)."),
    ]

    # Cross-candidate diversity replaces the prior 0.0 placeholder.
    soft_scores = {
        "context_relevance": _confidence_average(context.get("relevant_files", [])),
        "eval_readiness": _eval_readiness(required_eval_declared, ref_required_eval_executed, ref_required_eval_passed),
        "candidate_diversity": diversity["average"],
        "critic_confidence": 0.62 if not ref_source_patch_present else 0.82,
    }

    # Remaining risks: derive from selected candidate's eval results.
    remaining_risks = _remaining_risks(intent, context, eval_harness, ref_source_patch_present, ref_eval)

    report: dict[str, Any] = {
        "schema_version": "agentic.promotion_report.v2",
        "candidate": selected_candidate_id or (candidates[0].get("id") if candidates else "candidate-a"),
        "selected_candidate": selected_candidate_id,
        "candidate_count": len(candidates),
        "decision": decision,
        "hard_gates": hard_gates,
        "gate_details": gate_details,
        "candidates": candidate_summaries,
        "candidate_diversity": diversity,
        "eval": {
            "required_eval_declared": required_eval_declared,
            "required_eval_executed": ref_required_eval_executed,
            "required_eval_passed": ref_required_eval_passed,
            "failure_summary": ref_eval.get("failure_summary"),
            "required_commands": [
                {
                    "name": result.get("name"),
                    "cmd": result.get("cmd"),
                    "declared": result.get("declared", True),
                    "executed": result.get("executed", False),
                    "exit_code": result.get("exit_code"),
                    "passed": result.get("passed", False),
                }
                for result in ref_eval.get("commands", [])
                if result.get("required")
            ],
        },
        "repair": {
            "attempted": bool(ref_repair_attempts),
            "attempt_count": len(ref_repair_attempts),
            "max_loops": ref_repair.get("max_loops", 0),
            "stop_reason": ref_repair_stop_reason,
            "final_failure": ref_repair.get("final_failure") or ref_eval.get("failure_summary"),
            "abandoned": ref_repair_abandoned,
            "abandonment_reason": ref_repair_stop_reason if ref_repair_abandoned else None,
        },
        "soft_scores": soft_scores,
        "remaining_risks": remaining_risks,
        "abandonment_pattern": {
            "patch_worker": patch_worker,
            "failure_type": None,
            "prior_abandonments": 0,
            "warning_emitted": False,
        },
    }
    if reference_candidate is not None:
        _augment_with_abandonment_pattern(
            report,
            project_path=project_path,
            patch_worker=patch_worker,
            candidate=reference_candidate,
            eval_results=ref_eval,
        )
    # MVP-3C: fail loud on the producer side. If we ever hand a malformed
    # report to writers / downstream consumers, the bug should surface here
    # rather than silently corrupt run packages.
    _assert_valid_promotion_report_v2(report)
    return report


def _augment_with_abandonment_pattern(
    report: dict[str, Any],
    *,
    project_path: Path | None,
    patch_worker: str,
    candidate: dict[str, Any],
    eval_results: dict[str, Any],
) -> None:
    """Read prior abandonments and, if a (patch_worker, failure_type) pattern
    has appeared >= 2 times before on this project, append a structured
    warning to `report['remaining_risks']` and stamp `report['abandonment_pattern']`.

    This is a soft signal only — it does not gate the decision.
    """
    if project_path is None or not patch_worker or patch_worker == "none":
        return
    # Determine the current run's failure_type. Prefer the repair loop's
    # final_failure (set on exhaustion), else the eval-harness failure_summary,
    # else fall back to classifying eval_results directly.
    current_failure: dict[str, Any] | None = None
    final_failure = candidate.get("repair_history", {}).get("final_failure")
    if isinstance(final_failure, dict):
        current_failure = final_failure
    elif isinstance(eval_results.get("failure_summary"), dict):
        current_failure = eval_results["failure_summary"]
    else:
        classified = _classify_eval_failure(eval_results)
        if classified.get("failure_type") and classified["failure_type"] != "none":
            current_failure = classified
    if not current_failure:
        return
    failure_type = str(current_failure.get("failure_type") or "")
    if not failure_type or failure_type == "none":
        return

    history = _read_abandonment_history(project_path)
    prior = _count_prior_abandonments(history, patch_worker=patch_worker, failure_type=failure_type)
    pattern = report["abandonment_pattern"]
    pattern["failure_type"] = failure_type
    pattern["prior_abandonments"] = prior
    if prior >= 2:
        pattern["warning_emitted"] = True
        report["remaining_risks"].append(
            f"patch_worker `{patch_worker}` has been abandoned {prior} prior time(s) on this project for failure_type "
            f"`{failure_type}`; consider switching workers, expanding eval coverage, or revisiting intent scope before another repair attempt."
        )


# Path of the project-level abandonment log. One JSON object per line.
# The file is append-only; each agentic run that ends with decision="abandoned"
# adds exactly one record here. Future worker-selection logic can read this
# log to bias against repeatedly-failing categories on a given project.
AGENTIC_ABANDONMENT_LOG_RELPATH = ".agent/agentic-abandonments.jsonl"


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------
# MVP-3C: explicit shape contracts for the artifacts we write/read most. The
# validators are intentionally lightweight (no jsonschema dependency); they
# check schema_version + required keys + obvious type expectations. Use the
# `_assert_*` form on write side (raises ValueError — fail loud); use the
# `_check_*` form on read side (returns list[str] of issues — fail soft).
_VALID_PROMOTION_DECISIONS: frozenset[str] = frozenset({
    "promote", "repair", "abandoned", "needs-human-review", "needs-more-context",
})


def _validate_promotion_report_v2(payload: dict[str, Any]) -> list[str]:
    """Return a list of validation errors for a promotion-report.v2 payload.

    Empty list means valid. Each error is a human-readable string suitable
    for logging, exception messages, or diagnostic output.
    """
    if not isinstance(payload, dict):
        return ["promotion-report payload is not a dict"]
    errors: list[str] = []
    schema = payload.get("schema_version")
    if schema != "agentic.promotion_report.v2":
        errors.append(f"schema_version is `{schema}`; expected `agentic.promotion_report.v2`")
    decision = payload.get("decision")
    if not isinstance(decision, str):
        errors.append(f"decision is `{type(decision).__name__}`; expected str")
    elif decision not in _VALID_PROMOTION_DECISIONS:
        errors.append(f"decision `{decision}` is not one of {sorted(_VALID_PROMOTION_DECISIONS)}")
    for key, expected_type in (
        ("candidate", str),
        ("candidate_count", int),
        ("hard_gates", dict),
        ("gate_details", list),
        ("eval", dict),
        ("repair", dict),
        ("soft_scores", dict),
        ("remaining_risks", list),
        ("abandonment_pattern", dict),
        ("candidates", list),
        ("candidate_diversity", dict),
    ):
        if key not in payload:
            errors.append(f"missing required key `{key}`")
            continue
        if not isinstance(payload[key], expected_type):
            errors.append(f"`{key}` is `{type(payload[key]).__name__}`; expected `{expected_type.__name__}`")
    # selected_candidate is allowed to be None.
    if "selected_candidate" not in payload:
        errors.append("missing required key `selected_candidate` (may be null)")
    elif payload["selected_candidate"] is not None and not isinstance(payload["selected_candidate"], str):
        errors.append(f"`selected_candidate` is `{type(payload['selected_candidate']).__name__}`; expected str or null")
    # Each candidate summary must at minimum have id + score.
    if isinstance(payload.get("candidates"), list):
        for index, summary in enumerate(payload["candidates"]):
            if not isinstance(summary, dict):
                errors.append(f"candidates[{index}] is not a dict")
                continue
            if not summary.get("id"):
                errors.append(f"candidates[{index}].id is missing or empty")
            if "score" not in summary:
                errors.append(f"candidates[{index}].score is missing")
    # candidate_diversity must have method/average.
    if isinstance(payload.get("candidate_diversity"), dict):
        cd = payload["candidate_diversity"]
        if "method" not in cd:
            errors.append("candidate_diversity.method is missing")
        if "average" not in cd:
            errors.append("candidate_diversity.average is missing")
    return errors


def _validate_candidate_score(payload: dict[str, Any]) -> list[str]:
    """Validate `candidates/<id>/score.json` (agentic.candidate_score.v1)."""
    if not isinstance(payload, dict):
        return ["candidate score payload is not a dict"]
    errors: list[str] = []
    if payload.get("schema_version") != "agentic.candidate_score.v1":
        errors.append(f"schema_version is `{payload.get('schema_version')}`; expected `agentic.candidate_score.v1`")
    for key in ("candidate", "source_patch_present", "diff_within_scope"):
        if key not in payload:
            errors.append(f"missing required key `{key}`")
    if "candidate" in payload and not isinstance(payload["candidate"], str):
        errors.append(f"`candidate` is `{type(payload['candidate']).__name__}`; expected str")
    for bool_key in ("source_patch_present", "diff_within_scope"):
        if bool_key in payload and not isinstance(payload[bool_key], bool):
            errors.append(f"`{bool_key}` is `{type(payload[bool_key]).__name__}`; expected bool")
    return errors


def _validate_changed_files(payload: dict[str, Any]) -> list[str]:
    """Validate `candidates/<id>/changed-files.json` (agentic.changed_files.v1)."""
    if not isinstance(payload, dict):
        return ["changed-files payload is not a dict"]
    errors: list[str] = []
    if payload.get("schema_version") != "agentic.changed_files.v1":
        errors.append(f"schema_version is `{payload.get('schema_version')}`; expected `agentic.changed_files.v1`")
    for key, expected_type in (
        ("candidate", str),
        ("changed_files", list),
        ("source_patch_present", bool),
        ("out_of_scope_changes", list),
    ):
        if key not in payload:
            errors.append(f"missing required key `{key}`")
            continue
        if not isinstance(payload[key], expected_type):
            errors.append(f"`{key}` is `{type(payload[key]).__name__}`; expected `{expected_type.__name__}`")
    return errors


def _assert_valid_promotion_report_v2(payload: dict[str, Any]) -> None:
    errors = _validate_promotion_report_v2(payload)
    if errors:
        raise ValueError("promotion-report.v2 validation failed:\n  - " + "\n  - ".join(errors))


def _append_abandonment_record(
    project_path: Path,
    *,
    run_id: str,
    intent: dict[str, Any],
    promotion: dict[str, Any],
    patch_worker: str,
    event_type: str = "run_abandoned",
    candidate_id: str | None = None,
    candidate: dict[str, Any] | None = None,
) -> str:
    """Append a single abandonment record to the project-level JSONL log.

    `event_type` is either:
      * `"candidate_abandoned"` — one candidate within a multi-candidate run
        was abandoned (its repair loop exhausted). The run as a whole may
        still promote a sibling candidate.
      * `"run_abandoned"` — every candidate in this run failed to produce a
        promotable patch via repair. The run itself has no winner.

    When `event_type == "candidate_abandoned"` the caller passes the
    individual `candidate` dict so the record reflects that candidate's
    repair_history rather than the run-level promotion.repair (which
    refers to the selected/reference candidate).
    """
    if candidate is not None and event_type == "candidate_abandoned":
        repair_history = candidate.get("repair_history") or {}
        record_candidate_id = candidate_id or str(candidate.get("id") or "")
        stop_reason = str(repair_history.get("stop_reason") or "")
        attempt_count = len(repair_history.get("attempts") or [])
        max_loops = int(repair_history.get("max_loops") or 0)
        final_failure = repair_history.get("final_failure")
        decision = "candidate_abandoned"
    else:
        repair = dict(promotion.get("repair") or {})
        record_candidate_id = candidate_id or str(promotion.get("candidate") or "")
        stop_reason = repair.get("stop_reason") or ""
        attempt_count = int(repair.get("attempt_count") or 0)
        max_loops = int(repair.get("max_loops") or 0)
        final_failure = repair.get("final_failure")
        decision = str(promotion.get("decision") or "")
    record = {
        "schema_version": "agentic.abandonment_record.v1",
        "event_type": event_type,
        "run_id": run_id,
        "timestamp_utc": now_iso(),
        "intent_goal": str(intent.get("goal") or ""),
        "candidate": record_candidate_id,
        "decision": decision,
        "patch_worker": patch_worker,
        "stop_reason": stop_reason,
        "attempt_count": attempt_count,
        "max_loops": max_loops,
        "final_failure": final_failure,
    }
    log_path = project_path / AGENTIC_ABANDONMENT_LOG_RELPATH
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    return log_path.relative_to(project_path).as_posix()


def _read_abandonment_history(project_path: Path) -> list[dict[str, Any]]:
    """Read prior abandonment records for this project, oldest first.

    Returns an empty list if the log file does not exist. Lines that fail to
    parse as JSON are skipped silently — we never want the read side to crash
    on a corrupted history line and cause the gate to fail.
    """
    log_path = project_path / AGENTIC_ABANDONMENT_LOG_RELPATH
    if not log_path.exists():
        return []
    records: list[dict[str, Any]] = []
    try:
        text = log_path.read_text(encoding="utf-8")
    except OSError:
        return []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            records.append(parsed)
    return records


def _count_prior_abandonments(
    history: list[dict[str, Any]],
    *,
    patch_worker: str,
    failure_type: str,
) -> int:
    """Count records in history matching the given (patch_worker, failure_type)."""
    if not patch_worker or not failure_type:
        return 0
    count = 0
    for record in history:
        if str(record.get("patch_worker") or "") != patch_worker:
            continue
        final_failure = record.get("final_failure") or {}
        if not isinstance(final_failure, dict):
            continue
        if str(final_failure.get("failure_type") or "") == failure_type:
            count += 1
    return count


# Cap on how many prior_learnings we surface in each context_pack to keep
# the artifact (and any downstream prompt) focused.
_PRIOR_LEARNINGS_CAP = 10


def _read_prior_memory_updates(
    project_path: Path,
    *,
    exclude_run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Load every prior `memory-update.proposed.json` for this project.

    Returns a list of payload dicts ordered most-recent-first by file mtime.
    Skips the current run if `exclude_run_id` matches the run directory name
    or the file's `source_run` field. Corrupt JSON / non-dict payloads are
    silently skipped — the read side never blocks the gate.
    """
    runs_dir = project_path / ".agent" / "runs"
    if not runs_dir.exists():
        return []
    payloads: list[tuple[float, dict[str, Any]]] = []
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        if exclude_run_id and run_dir.name == exclude_run_id:
            continue
        memory_path = run_dir / "memory-update.proposed.json"
        if not memory_path.exists():
            continue
        try:
            payload = json.loads(memory_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        if exclude_run_id and str(payload.get("source_run") or "") == exclude_run_id:
            continue
        try:
            mtime = memory_path.stat().st_mtime
        except OSError:
            mtime = 0.0
        payloads.append((mtime, payload))
    payloads.sort(key=lambda item: item[0], reverse=True)
    return [payload for _, payload in payloads]


def _aggregate_prior_learnings(memory_updates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe learnings across runs by `pattern` text.

    Input is `memory_updates` ordered most-recent-first. Each output entry
    captures occurrences across runs plus the most recent evidence for that
    pattern. The result is capped at `_PRIOR_LEARNINGS_CAP`. Within the cap,
    higher-occurrence patterns win, then higher confidence, then more recent.
    """
    aggregated: dict[str, dict[str, Any]] = {}
    for index, update in enumerate(memory_updates):
        learnings = update.get("learned_patterns") or []
        if not isinstance(learnings, list):
            continue
        for learning in learnings:
            if not isinstance(learning, dict):
                continue
            pattern = str(learning.get("pattern") or "").strip()
            if not pattern:
                continue
            confidence = _coerce_float(learning.get("confidence"), default=0.5)
            entry = aggregated.get(pattern)
            if entry is None:
                aggregated[pattern] = {
                    "pattern": pattern,
                    "occurrences": 1,
                    "max_confidence": confidence,
                    "last_seen_run": update.get("source_run"),
                    "last_seen_at": update.get("source_timestamp_utc"),
                    "last_evidence": learning.get("evidence"),
                    # Most-recent index (lower = more recent because list is sorted desc).
                    "_recency_rank": index,
                }
            else:
                entry["occurrences"] += 1
                if confidence > entry["max_confidence"]:
                    entry["max_confidence"] = confidence
                # Keep the most-recent evidence/run/at — only overwrite if
                # this update is more recent than what we recorded so far.
                if index < entry["_recency_rank"]:
                    entry["last_seen_run"] = update.get("source_run")
                    entry["last_seen_at"] = update.get("source_timestamp_utc")
                    entry["last_evidence"] = learning.get("evidence")
                    entry["_recency_rank"] = index
    # Sort: occurrences desc, max_confidence desc, most-recent first.
    ranked = sorted(
        aggregated.values(),
        key=lambda item: (item["occurrences"], item["max_confidence"], -item["_recency_rank"]),
        reverse=True,
    )
    # Strip the internal recency rank before returning.
    for entry in ranked:
        entry.pop("_recency_rank", None)
    return ranked[:_PRIOR_LEARNINGS_CAP]


def _coerce_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _gate_detail(gate: str, required: Any, actual: Any, reason: str) -> dict[str, Any]:
    return {
        "gate": gate,
        "required": required,
        "actual": actual,
        "passed": required == actual,
        "reason": reason,
    }


def _eval_readiness(declared: bool, executed: bool, passed: bool) -> float:
    if passed:
        return 1.0
    if executed:
        return 0.65
    if declared:
        return 0.45
    return 0.1


def _build_memory_update(
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    candidates: list[dict[str, Any]],
    promotion: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    """Build a per-run memory-update proposal derived from THIS run's evidence.

    Schema v2 intentionally has no hardcoded MVP-1 meta-text; every
    `learned_patterns` entry is derived from facts visible in the current
    run's artifacts (candidate diffs, eval results, promotion report,
    abandonment pattern, winner/loser comparison). The downstream consumer
    (`_aggregate_prior_learnings`) dedupes by `pattern` text across runs.

    Status remains `proposed_only`; nothing here is written to long-term
    memory automatically. Future runs see this file via
    `context_pack.prior_learnings`.
    """
    learned_patterns = list(_derive_learnings_from_run(intent, context, eval_harness, candidates, promotion, run_id))
    repair = promotion.get("repair") or {}
    pattern = promotion.get("abandonment_pattern") or {}
    selected_id = str(promotion.get("selected_candidate") or promotion.get("candidate") or "")
    selected_candidate = next((c for c in candidates if str(c.get("id") or "") == selected_id), None)
    if selected_candidate is None and candidates:
        selected_candidate = candidates[0]
    selected_eval_results = (selected_candidate or {}).get("eval_results") or {}
    commands = selected_eval_results.get("commands", [])
    executed_count = sum(1 for c in commands if c.get("required") and c.get("executed"))
    passed_count = sum(1 for c in commands if c.get("required") and c.get("passed"))
    return {
        "schema_version": "agentic.memory_update_proposal.v2",
        "status": "proposed_only",
        "source_run": run_id,
        "source_timestamp_utc": now_iso(),
        "learned_patterns": learned_patterns,
        "project_observations": {
            "goal": intent.get("goal"),
            "promotion_decision": promotion.get("decision"),
            "candidate": selected_id or (candidates[0].get("id") if candidates else None),
            "selected_candidate": selected_id or None,
            "candidate_count": len(candidates),
            "patch_worker": pattern.get("patch_worker"),
            "unknown_count": len(context.get("unknowns", [])),
            "declared_eval_command_count": len([c for c in commands if c.get("required")]),
            "executed_required_eval_count": executed_count,
            "passed_required_eval_count": passed_count,
            "source_patch_present": bool((selected_candidate or {}).get("score", {}).get("source_patch_present")),
            "repair_attempted": bool(repair.get("attempted")),
            "repair_attempt_count": int(repair.get("attempt_count") or 0),
            "abandoned": bool(repair.get("abandoned")),
            "stop_reason": repair.get("stop_reason"),
        },
        "do_not_remember": [
            "Run-specific ids",
            "Temporary artifact paths",
            "Any local secret or token value",
        ],
    }


def _derive_learnings_from_run(
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    candidates: list[dict[str, Any]],
    promotion: dict[str, Any],
    run_id: str,
) -> list[dict[str, Any]]:
    """Yield run-derived learnings as a list of dicts.

    Each learning has:
      - pattern: stable text that the consumer uses for cross-run dedup.
        Phrase generically enough that multiple runs can produce the same
        pattern when the same condition holds (e.g. "failure_type type_error
        observed during eval"), so the aggregator can count occurrences.
      - evidence: structured detail specific to THIS run, including
        source_run for traceability.
      - confidence: heuristic 0.0-1.0. Higher = more reliable signal.
    """
    learnings: list[dict[str, Any]] = []
    repair = promotion.get("repair") or {}
    pattern = promotion.get("abandonment_pattern") or {}
    decision = str(promotion.get("decision") or "")
    selected_id = str(promotion.get("selected_candidate") or promotion.get("candidate") or "")
    candidate_summaries = list(promotion.get("candidates") or [])

    # Pick the candidate to use for top-level "this run" signals (failure
    # type observed, abandonment_pattern echo, decision learning). Prefer
    # the selected candidate; fall back to the first candidate.
    selected_candidate = next((c for c in candidates if str(c.get("id") or "") == selected_id), None)
    if selected_candidate is None and candidates:
        selected_candidate = candidates[0]
    selected_eval_results = (selected_candidate or {}).get("eval_results") or {}

    failure_type: str | None = None
    final_failure = repair.get("final_failure")
    if isinstance(final_failure, dict):
        failure_type = str(final_failure.get("failure_type") or "") or None
    if not failure_type:
        summary = selected_eval_results.get("failure_summary")
        if isinstance(summary, dict):
            failure_type = str(summary.get("failure_type") or "") or None

    # 1. Failure type observed (only when not "none").
    if failure_type and failure_type != "none":
        learnings.append({
            "pattern": f"failure_type `{failure_type}` observed during required eval on this project",
            "evidence": {"source_run": run_id, "failure_type": failure_type, "decision": decision},
            "confidence": 0.78,
        })

    # 2. Promotion decision is itself a learning (especially abandoned / repair).
    if decision in {"abandoned", "repair", "needs-human-review"}:
        learnings.append({
            "pattern": f"promotion_decision `{decision}` reached on this project",
            "evidence": {
                "source_run": run_id,
                "decision": decision,
                "stop_reason": repair.get("stop_reason"),
                "patch_worker": pattern.get("patch_worker"),
            },
            "confidence": 0.85 if decision == "abandoned" else 0.7,
        })

    # 3. Per-candidate observations: out-of-scope writes attempted, required
    # eval commands declared but not executed. These are recorded for every
    # candidate so multi-candidate runs surface losers' signals too.
    for candidate in candidates:
        c_id = str(candidate.get("id") or "")
        c_eval = candidate.get("eval_results") or {}
        out_of_scope = list((candidate.get("changed_files") or {}).get("out_of_scope_changes") or [])
        for entry in out_of_scope[:5]:  # cap to avoid spam from a runaway worker
            path = entry.get("path") if isinstance(entry, dict) else str(entry)
            if not path:
                continue
            learnings.append({
                "pattern": f"patch worker attempted to write outside allowed_change_scope: `{path}`",
                "evidence": {"source_run": run_id, "candidate": c_id, "path": path},
                "confidence": 0.9,
            })
        for command in c_eval.get("commands", []):
            if command.get("required") and not command.get("executed"):
                name = str(command.get("name") or "")
                if not name:
                    continue
                learnings.append({
                    "pattern": f"required eval command `{name}` was declared but not executed",
                    "evidence": {
                        "source_run": run_id,
                        "candidate": c_id,
                        "name": name,
                        "cmd": command.get("cmd"),
                        "reason": command.get("reason"),
                    },
                    "confidence": 0.72,
                })

    # 4. Abandonment pattern echo: when this run's gate already detected a
    # repeated (worker, failure_type) combo, surface it as a learning so it
    # propagates beyond just the `remaining_risks` of this run.
    if pattern.get("warning_emitted") and pattern.get("failure_type"):
        learnings.append({
            "pattern": (
                f"patch_worker `{pattern.get('patch_worker')}` repeatedly abandoned on failure_type "
                f"`{pattern.get('failure_type')}` for this project"
            ),
            "evidence": {
                "source_run": run_id,
                "prior_abandonments": int(pattern.get("prior_abandonments") or 0),
                "patch_worker": pattern.get("patch_worker"),
                "failure_type": pattern.get("failure_type"),
            },
            "confidence": 0.92,
        })

    # 5. Promote = positive signal worth remembering.
    if decision == "promote":
        learnings.append({
            "pattern": f"patch_worker `{pattern.get('patch_worker') or 'unknown'}` produced a promotable patch on this project",
            "evidence": {
                "source_run": run_id,
                "candidate": selected_id or (candidates[0].get("id") if candidates else None),
                "strategy": str((selected_candidate or {}).get("strategy") or ""),
                "patch_worker": pattern.get("patch_worker"),
            },
            "confidence": 0.8,
        })

    # 6. Multi-candidate winner/loser learnings — only meaningful when
    # there are at least two candidates with comparable score data.
    if len(candidate_summaries) >= 2 and selected_id:
        winner_summary = next((s for s in candidate_summaries if str(s.get("id") or "") == selected_id), None)
        loser_summaries = [s for s in candidate_summaries if str(s.get("id") or "") != selected_id]
        if winner_summary is not None and loser_summaries:
            best_loser = max(loser_summaries, key=lambda s: int(s.get("score") or 0))
            score_delta = int(winner_summary.get("score") or 0) - int(best_loser.get("score") or 0)
            winner_strategy = str(winner_summary.get("strategy") or "unknown")
            loser_strategy = str(best_loser.get("strategy") or "unknown")
            if winner_strategy != loser_strategy:
                learnings.append({
                    "pattern": (
                        f"`{winner_strategy}` candidate outperformed `{loser_strategy}` candidate on this project"
                    ),
                    "evidence": {
                        "source_run": run_id,
                        "winner": winner_summary.get("id"),
                        "winner_strategy": winner_strategy,
                        "loser": best_loser.get("id"),
                        "loser_strategy": loser_strategy,
                        "score_delta": score_delta,
                    },
                    "confidence": 0.74,
                })
        # Surface candidates that were specifically disqualified — useful
        # for noticing systemic issues (e.g. one strategy keeps writing
        # out-of-scope files).
        for summary in candidate_summaries:
            if not summary.get("disqualified"):
                continue
            failed_gates = [
                key for key, value in {
                    "source_patch_present": summary.get("source_patch_present"),
                    "required_eval_executed": summary.get("required_eval_executed"),
                    "required_eval_passed": summary.get("required_eval_passed"),
                    "diff_within_scope": summary.get("diff_within_scope"),
                    "no_critical_security_finding": summary.get("no_critical_security_finding"),
                }.items() if value is False
            ]
            if not failed_gates:
                continue
            learnings.append({
                "pattern": (
                    f"`{summary.get('strategy') or summary.get('id')}` candidate disqualified by hard gate(s) on this project"
                ),
                "evidence": {
                    "source_run": run_id,
                    "candidate": summary.get("id"),
                    "strategy": summary.get("strategy"),
                    "failed_gates": failed_gates,
                },
                "confidence": 0.7,
            })

    return learnings


def _default_allowed_paths(project_path: Path) -> list[str]:
    paths = ["docs/**", "tests/**"]
    if (project_path / "apps").exists():
        paths.insert(0, "apps/**")
    if (project_path / "packages").exists():
        paths.insert(1, "packages/**")
    return paths


def _discover_files(project_path: Path) -> list[str]:
    ignored_dirs = {
        ".agent",
        ".git",
        ".next",
        ".venv",
        ".data",
        "__pycache__",
        "node_modules",
        "dist",
        "build",
        "coverage",
        "test-results",
        "playwright-report",
    }
    ignored_parts: set[str] = set()
    # Tooling-generated files that are never product source. Same nature as
    # `next-env.d.ts` (Next.js codegen): if these leak into changed-files they
    # trip diff_within_scope hard gate even when the actual product patch is
    # clean. *.tsbuildinfo is emitted by `tsc` whenever tsconfig has
    # `incremental: true` (the Next.js default), including under `--noEmit`.
    ignored_file_names = {"next-env.d.ts", ".npmrc", ".pypirc"}
    ignored_file_suffixes = (".tsbuildinfo",)
    files: list[str] = []
    for path in project_path.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(project_path).as_posix()
        if any(part in ignored_dirs for part in path.relative_to(project_path).parts):
            continue
        if _is_secret_context_path(relative):
            continue
        if path.name in ignored_file_names:
            continue
        if path.name.endswith(ignored_file_suffixes):
            continue
        if any(relative.startswith(prefix) for prefix in ignored_parts):
            continue
        files.append(relative)
    return sorted(files)


def _is_secret_context_path(path: str) -> bool:
    """Return true for env/secret files that must never enter agent context."""
    parts = Path(path).parts
    return any(part.startswith(".env") for part in parts)


_CONTEXT_BUDGET = 20
_BUCKET_QUOTAS = {
    "repo_manifest": 2,
    "app_source": 8,
    "routes_and_api": 3,
    "ui_entrypoints": 3,
    "tests": 3,
    "docs_product": 2,
}
_SOURCE_BUCKETS = {"app_source", "routes_and_api", "ui_entrypoints"}


def _rank_relevant_files(files: list[str], intent: dict[str, Any]) -> list[dict[str, Any]]:
    goal_tokens = set(re.findall(r"[a-zA-Z0-9_]{3,}", str(intent.get("goal", "")).lower()))
    ranked: list[dict[str, Any]] = []
    for path in files:
        lower = path.lower()
        bucket = _context_bucket(path)
        score, why = _context_score(path, bucket)
        token_hits = sum(1 for token in goal_tokens if token in lower)
        if token_hits:
            score = min(100, score + token_hits * 3)
            why = f"{why} Path matches {token_hits} intent token(s)."
        ranked.append(
            {
                "path": path,
                "why": why,
                "confidence": round(score / 100, 2),
                "bucket": bucket,
                "_score": score,
            }
        )
    ranked.sort(key=lambda item: (-float(item["_score"]), str(item["path"])))
    selected = _select_ranked_context(ranked, files)
    return [{key: value for key, value in item.items() if key != "_score"} for item in selected]


def _select_ranked_context(ranked: list[dict[str, Any]], files: list[str]) -> list[dict[str, Any]]:
    by_path = {str(item["path"]): item for item in ranked}
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(path: str) -> None:
        item = by_path.get(path)
        if item and path not in seen and len(selected) < _CONTEXT_BUDGET:
            selected.append(item)
            seen.add(path)

    for path in _must_include_files(files):
        add(path)

    for bucket, quota in _BUCKET_QUOTAS.items():
        count = sum(1 for item in selected if item["bucket"] == bucket)
        for item in ranked:
            if len(selected) >= _CONTEXT_BUDGET or count >= quota:
                break
            path = str(item["path"])
            if path in seen or item["bucket"] != bucket:
                continue
            selected.append(item)
            seen.add(path)
            count += 1

    for item in ranked:
        if len(selected) >= _CONTEXT_BUDGET:
            break
        path = str(item["path"])
        bucket = str(item["bucket"])
        if bucket == "docs_product" and sum(1 for existing in selected if existing["bucket"] == "docs_product") >= _BUCKET_QUOTAS["docs_product"]:
            continue
        if path not in seen and bucket != "generated_or_low_value":
            selected.append(item)
            seen.add(path)

    if not selected:
        for item in ranked:
            if len(selected) >= _CONTEXT_BUDGET:
                break
            path = str(item["path"])
            if path not in seen:
                selected.append(item)
                seen.add(path)
    return selected


def _must_include_files(files: list[str]) -> list[str]:
    candidates = [
        "package.json",
        "apps/web/package.json",
        "apps/web/app/page.tsx",
        "apps/web/app/page.jsx",
        "apps/web/app/page.js",
        "apps/web/index.html",
        "apps/web/playwright.config.ts",
        "apps/web/playwright.config.js",
        "apps/web/next.config.ts",
        "apps/web/next.config.js",
        "apps/web/tsconfig.json",
    ]
    present = [path for path in candidates if path in files]
    route = next((path for path in files if _context_bucket(path) == "routes_and_api"), None)
    test = next((path for path in files if _context_bucket(path) == "tests"), None)
    source = next((path for path in files if _context_bucket(path) == "app_source"), None)
    for path in [route, test, source]:
        if path and path not in present:
            present.append(path)
    return present


def _context_bucket(path: str) -> str:
    lower = path.lower()
    name = Path(lower).name
    if "node_modules/" in lower or "/.next/" in lower or lower.startswith(".next/"):
        return "generated_or_low_value"
    if any(part in lower for part in ["/dist/", "/build/", "/coverage/", "/test-results/"]):
        return "generated_or_low_value"
    if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf")):
        return "generated_or_low_value"
    if lower in {"package.json", "pnpm-lock.yaml", "package-lock.json", "yarn.lock", "turbo.json", "tsconfig.json", "pyproject.toml"}:
        return "repo_manifest"
    if lower in {
        "apps/web/package.json",
        "apps/web/pnpm-lock.yaml",
        "apps/web/package-lock.json",
        "apps/web/yarn.lock",
        "apps/web/tsconfig.json",
        "apps/web/next.config.js",
        "apps/web/next.config.ts",
        "apps/web/vite.config.js",
        "apps/web/vite.config.ts",
    }:
        return "repo_manifest"
    if _looks_like_test(lower) or lower.startswith(("tests/", "e2e/", "playwright/", "apps/web/tests/")):
        return "tests"
    if (
        "/app/api/" in lower
        or "/pages/api/" in lower
        or lower.startswith(("src/api/", "server/"))
        or name in {"route.ts", "route.js", "route.tsx", "route.jsx"}
    ):
        return "routes_and_api"
    if (
        lower.endswith(("/page.tsx", "/page.jsx", "/page.ts", "/page.js", "/layout.tsx", "/layout.jsx"))
        or lower.startswith(("apps/web/components/", "apps/web/features/", "components/", "features/"))
    ):
        return "ui_entrypoints"
    if lower.startswith(("apps/web/", "src/", "packages/")):
        return "app_source"
    if lower.startswith("docs/product/") or lower == "readme.md":
        return "docs_product"
    if lower.startswith(("docs/design/", "docs/architecture/")):
        return "docs_product"
    return "generated_or_low_value" if lower.startswith(".agent/") else "app_source"


def _context_score(path: str, bucket: str) -> tuple[int, str]:
    lower = path.lower()
    if bucket == "repo_manifest":
        return 80, "Manifest/config defines commands, dependencies, and project shape."
    if bucket == "routes_and_api":
        return 75, "API route or server entrypoint likely owns executable behavior."
    if bucket == "ui_entrypoints":
        return 70, "UI entrypoint/component is likely relevant for product behavior."
    if bucket == "app_source":
        return 68, "Application source is likely patchable implementation context."
    if bucket == "tests":
        return 55, "Existing tests or test config define verification surface."
    if bucket == "docs_product":
        return 30, "Product/design docs constrain behavior but must not dominate code context."
    if lower.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
        return -50, "Screenshot/image artifact is low-value unless the task is visual review."
    return -40, "Generated or low-value artifact for patch generation."


def _context_quality(all_files: list[str], selected: list[dict[str, Any]]) -> dict[str, Any]:
    selected_count = len(selected)
    selected_source = [item for item in selected if item.get("bucket") in _SOURCE_BUCKETS]
    selected_docs = [item for item in selected if item.get("bucket") == "docs_product"]
    selected_tests = [item for item in selected if item.get("bucket") == "tests"]
    selected_routes = [item for item in selected if item.get("bucket") == "routes_and_api"]
    has_package_scripts = any(item.get("bucket") == "repo_manifest" for item in selected)
    return {
        "has_source_files": bool(selected_source),
        "has_tests": bool(selected_tests),
        "has_package_scripts": has_package_scripts,
        "has_api_routes": bool(selected_routes),
        "docs_dominance_ratio": round(len(selected_docs) / selected_count, 2) if selected_count else 0,
        "source_dominance_ratio": round(len(selected_source) / selected_count, 2) if selected_count else 0,
        "context_gate": "pass" if selected_source else "needs-more-context",
        "checks": {
            "source_files_selected": len(selected_source),
            "tests_selected": len(selected_tests),
            "api_routes_selected": len(selected_routes),
            "package_or_config_selected": sum(1 for item in selected if item.get("bucket") == "repo_manifest"),
            "total_files_scanned": len(all_files),
        },
    }


def _ranking_summary(all_files: list[str], selected: list[dict[str, Any]]) -> dict[str, Any]:
    buckets: dict[str, int] = {}
    for item in selected:
        bucket = str(item.get("bucket", "unknown"))
        buckets[bucket] = buckets.get(bucket, 0) + 1
    return {
        "top_context_budget": _CONTEXT_BUDGET,
        "total_files_scanned": len(all_files),
        "selected_files": len(selected),
        "selected_source_files": sum(1 for item in selected if item.get("bucket") in _SOURCE_BUCKETS),
        "selected_doc_files": buckets.get("docs_product", 0),
        "selected_test_files": buckets.get("tests", 0),
        "bucket_counts": buckets,
        "quotas": dict(_BUCKET_QUOTAS),
    }



def _extract_symbols(project_path: Path, files: list[str]) -> list[dict[str, str]]:
    symbols: list[dict[str, str]] = []
    patterns = [
        re.compile(r"^\s*(?:export\s+)?function\s+([A-Za-z0-9_]+)"),
        re.compile(r"^\s*(?:export\s+)?class\s+([A-Za-z0-9_]+)"),
        re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z0-9_]+)\s*="),
        re.compile(r"^\s*def\s+([A-Za-z0-9_]+)\s*\("),
    ]
    for relative in files:
        path = project_path / relative
        if path.suffix.lower() not in {".js", ".jsx", ".ts", ".tsx", ".py"}:
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()[:250]
        except OSError:
            continue
        for line in lines:
            for pattern in patterns:
                match = pattern.search(line)
                if match:
                    symbols.append({"name": match.group(1), "file": relative, "kind": _symbol_kind(line)})
                    break
            if len(symbols) >= 40:
                return symbols
    return symbols


def _git_context(project_path: Path) -> dict[str, Any]:
    branch = _run_git(project_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    commit = _run_git(project_path, ["rev-parse", "--short", "HEAD"])
    status = _run_git(project_path, ["status", "--short", "--", "."])
    return {
        "branch": branch or "unknown",
        "commit": commit or "unknown",
        "dirty": bool(status),
        "status_summary": status.splitlines()[:20] if status else [],
    }


def _detect_constraints(project_path: Path, files: list[str]) -> list[str]:
    constraints: list[str] = []
    # RC-2A bug fix: also recognize a project-root package.json so flat
    # layouts (Vite/Next default/plain Node) get the same constraint
    # signal that monorepo `apps/web/` layouts get. Mirrors the eval
    # harness's package.json probe order.
    if "apps/web/package.json" in files:
        constraints.append("Contains an apps/web JavaScript package.")
        package = project_path / "apps" / "web" / "package.json"
        scripts = _read_package_scripts(package)
        if "build" in scripts:
            constraints.append("apps/web exposes an npm build command.")
        if "test:e2e" in scripts:
            constraints.append("apps/web exposes a Playwright-style e2e command.")
    elif "package.json" in files:
        constraints.append("Contains a project-root JavaScript package.")
        package = project_path / "package.json"
        scripts = _read_package_scripts(package)
        if "build" in scripts:
            constraints.append("Project root exposes an npm build command.")
        if "test:e2e" in scripts:
            constraints.append("Project root exposes a Playwright-style e2e command.")
    if "apps/web/index.html" in files:
        constraints.append("Contains a static web app entrypoint.")
    elif "src/index.html" in files or "index.html" in files:
        constraints.append("Contains a static web app entrypoint at the project root.")
    if "pyproject.toml" in files:
        constraints.append("Contains a Python project manifest.")
    if any(path.startswith("docs/product/") for path in files):
        constraints.append("Product artifacts exist and should constrain implementation decisions.")
    return constraints or ["No framework-specific constraint detected from project files."]


def _detect_unknowns(
    project_path: Path,
    existing_tests: list[str],
    constraints: list[str],
    context_quality: dict[str, Any],
) -> list[str]:
    unknowns: list[str] = []
    if not context_quality.get("has_source_files"):
        unknowns.append("No source files were selected into the top context pack.")
    if not existing_tests:
        unknowns.append("No existing test files were discovered in the project workspace.")
    if not (project_path / "docs" / "product" / "prd.md").exists():
        unknowns.append("No locked PRD artifact exists at docs/product/prd.md.")
    if not any("npm build" in item or "build command" in item or "exposes an npm build" in item for item in constraints):
        unknowns.append("No required build command was detected.")
    return unknowns


def _detect_api_contracts(project_path: Path) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []
    api_root = project_path / "apps" / "web" / "app" / "api"
    if not api_root.exists():
        return contracts
    for route in sorted(api_root.rglob("route.ts"))[:20]:
        relative = route.relative_to(api_root).parent.as_posix()
        endpoint = "/" if relative == "." else "/" + relative.replace("[", ":").replace("]", "")
        contracts.append({"endpoint": endpoint, "source": route.relative_to(project_path).as_posix()})
    return contracts


def _detect_visual_checks(project_path: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    if (project_path / "apps" / "web" / "index.html").exists() or (project_path / "apps" / "web" / "app").exists():
        checks.append({"name": "desktop-screenshot", "viewport": "1440x1000", "required": False})
        checks.append({"name": "mobile-screenshot", "viewport": "390x844", "required": False})
    return checks


def _read_package_scripts(package_json: Path) -> dict[str, str]:
    try:
        data = json.loads(package_json.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    scripts = data.get("scripts")
    return scripts if isinstance(scripts, dict) else {}


def _command(name: str, cmd: str, *, required: bool, cwd: str, timeout: int) -> dict[str, Any]:
    return {
        "name": name,
        "cmd": cmd,
        "required": required,
        "cwd": cwd,
        "timeout_sec": timeout,
        "type": "shell_command",
    }


def _remaining_risks(
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    source_patch_present: bool,
    eval_results: dict[str, Any],
) -> list[str]:
    risks: list[str] = []
    if not context.get("context_quality", {}).get("has_source_files"):
        risks.append("Context pack did not include source files; patch-worker should not run until source context is present.")
    if not source_patch_present:
        risks.append("No product-source patch was generated; Promotion Gate correctly refused to promote.")
    if eval_results.get("required_eval_declared") and not eval_results.get("required_eval_executed"):
        risks.append("Required eval was declared but not executed.")
    if eval_results.get("required_eval_executed") and not eval_results.get("required_eval_passed"):
        risks.append("Required eval executed but did not pass.")
    if eval_harness.get("manual_review_required"):
        risks.append("No required executable command was detected, so human review is still required.")
    risks.extend(context.get("unknowns", [])[:3])
    return risks


def _confidence_average(files: list[dict[str, Any]]) -> float:
    if not files:
        return 0.0
    values = [float(item.get("confidence", 0.0)) for item in files]
    return round(sum(values) / len(values), 2)


def _looks_like_test(path: str) -> bool:
    lower = path.lower()
    return "test" in lower or lower.endswith(".spec.ts") or lower.endswith(".spec.js")


def _symbol_kind(line: str) -> str:
    stripped = line.strip()
    if stripped.startswith("def "):
        return "function"
    if "class " in stripped:
        return "class"
    if "function " in stripped:
        return "function"
    return "binding"


def _path_allowed(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _run_git(project_path: Path, args: list[str]) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_path), *args],
            text=True,
            capture_output=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return ""
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def _critic_report(title: str, findings: list[str]) -> str:
    lines = [f"# {title}", "", "Status: read-only critic output.", "", "## Findings"]
    lines.extend(f"- {finding}" for finding in findings)
    lines.append("")
    return "\n".join(lines)


def _render_run_yaml(run_id: str, project: dict[str, Any], promotion: dict[str, Any], run_dir: Path) -> str:
    return (
        f"id: {run_id}\n"
        "workflow_id: agentic_project\n"
        f"project_id: {project['id']}\n"
        f"status: completed\n"
        f"decision: {promotion['decision']}\n"
        f"candidate: {promotion['candidate']}\n"
        f"run_dir: {run_dir}\n"
    )


def _render_human_summary(
    project: dict[str, Any],
    intent: dict[str, Any],
    context: dict[str, Any],
    eval_harness: dict[str, Any],
    promotion: dict[str, Any],
) -> str:
    commands = eval_harness.get("commands", [])
    return f"""# Agentic Project Summary

## Goal
{intent["goal"]}

## PRD Summary
The PRD is not treated as the source of truth in this workflow. The bounded intent contract and executable evaluation harness are the control plane.

## Design Summary
Visual and UX checks are represented as declared eval signals. Screenshot-based critique should attach to the candidate before promotion.

## Implementation Summary
The candidate package records whether a real source/test/config patch exists. Runtime-only artifacts do not count as product implementation.

## QA Summary
Declared eval command count: {len(commands)}.
Required eval command count: {sum(1 for command in commands if command.get("required"))}.
Unknown count: {len(context.get("unknowns", []))}.

## Release Notes
Promotion decision: {promotion["decision"]}.
Remaining risks: {", ".join(promotion.get("remaining_risks", [])) or "none"}.
"""


def _write_json(project_path: Path, path: Path, payload: Any) -> str:
    return _write_text(project_path, path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _write_jsonl(project_path: Path, path: Path, events: list[dict[str, Any]]) -> str:
    content = "".join(json.dumps(event, ensure_ascii=False) + "\n" for event in events)
    return _write_text(project_path, path, content)


def _write_text(project_path: Path, path: Path, content: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path.relative_to(project_path).as_posix()
