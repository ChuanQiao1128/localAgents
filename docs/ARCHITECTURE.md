# Local Agent Dev Studio — Architecture

This is the **top-level architecture** doc. It traces the data flow from operator input to a verified git commit, names every component, and points to the code. For deeper component-by-component walkthroughs use [`interview/02-architecture-walkthrough.md`](interview/02-architecture-walkthrough.md).

中文导读: 一篇看完就理解 Studio 是怎么从一个 markdown 文件变成 git commit 的。

---

## 1. Inputs

```
requirements.md           change-request.md
   │                            │
   │  for greenfield            │  for change request mode
   ▼                            ▼
```

### `requirements.md` (greenfield)

Free-form markdown. The first H1 is the project title. Each H2 (`## task ...`) is a discrete task with:
- a free-text intent paragraph,
- a `Scope:` line (inline `Scope: app/**, components/**` OR multi-line bullet block — both supported),
- an `Acceptance:` block of bullet criteria,
- optional `Risk: low|medium|high`,
- optional `Depends: <task title or task-id>`.

### `change-request.md` (Change Request Mode)

Same shape, different intent. Sections: `## Goal`, `## Scope`, `## Non-goals`, `## Acceptance`. The non-goals section is load-bearing: it tells the Promotion Gate what kinds of drift to refuse (e.g. "Do not modify package.json", "Do not add new dependencies").

---

## 2. Deterministic decomposer (no LLM in the parsing step)

```
requirements.md  → parse_requirements_md()  → task-graph.json
change-request.md → parse_change_request_text() → change-contract.json
```

**Pure Python regex + line-state machine.** Same input always produces the same output. This is load-bearing for reproducibility — a reviewer can run the decomposer locally and verify the agent's task list matches what the markdown actually said.

Code: `orchestrator/core/autonomous.py::parse_requirements_md` and `orchestrator/core/change_request_parser.py`.

### task-graph.json

```json
{
  "schema_version": 1,
  "project_title": "...",
  "overview": "...",
  "tasks": [
    {
      "id": "task-001",
      "title": "...",
      "intent": "...",
      "acceptance_criteria": [...],
      "scope_paths": ["app/**", "components/**"],
      "dependencies": [],
      "status": "pending|completed|needs-human-review|abandoned",
      "risk": "low|medium|high",
      "run_ids": [],
      "commit": null
    },
    ...
  ]
}
```

### change-contract.json (`agentic.change_contract.v1`)

```json
{
  "schema_version": "agentic.change_contract.v1",
  "change_id": "change_198713d499",
  "source_change_request_path": "...",
  "goal": "...",
  "scope_paths": ["app/**", "components/**"],
  "scope_missing": false,
  "non_goals": [...],
  "acceptance": [...],
  "created_at": "..."
}
```

For change mode, `change_contract.create_change()` writes 5 artifacts under `<project>/.agent/changes/<change_id>/`: change-request.md (immutable copy), change-contract.json, repo-onboarding.md (deterministic project snapshot), implementation-plan.md, acceptance-criteria.json (shape-compatible with autonomous mode).

---

## 3. AutonomousController — the outer SDLC loop

```
controller.advance_one_task(session, task_graph)
   │
   ├── pick_next_eligible_task (respects dependencies, corrective-task priority)
   ├── build context_pack (intent + scope + previous_completed_tasks + repo metadata)
   ├── runtime.run(intent_overrides=...)         ← inner agent loop
   ├── if decision == "promote":
   │      apply_candidate(...)                    ← Apply Gate
   │      commit_task(...)                        ← real git commit
   │      maybe_run_integration(...)              ← post-commit npm run build / typecheck
   ├── else (needs-human-review / abandoned):
   │      emit_review_item(); session.pause()
   └── save autonomous-session.json + final-run-status.md
```

Code: `orchestrator/core/autonomous.py::AutonomousController`.

Per-task budgets cap runaway runs:

| Knob | RC-4C matrix value |
|------|---|
| `max_tasks_per_session` | 3 |
| `max_total_inner_runs` | 5 |
| `max_candidates_per_task` | 1 (cost control) |
| `max_repair_attempts_per_candidate` | 1 |
| `max_abandoned_tasks` | 1 |
| `max_corrective_tasks` | 1 |

For change mode, `change_runner.run_change()` builds a 1-task task-graph from the change contract, swaps it in for the project's `task-graph.json`, calls `controller.advance_one_task` exactly once, then amends the commit to purge the ephemeral graph (RC-4A.3.1.A).

---

## 4. Context pack — what Codex actually sees

The controller assembles a per-task `context-pack.json` and hands it to the inner runtime, which then hands the relevant slice to Codex. It contains:

- The task's intent + acceptance criteria + scope paths (verbatim from the task graph).
- Up to 8 most-recent **previous completed tasks** in this session (id / title / commit / run_id) — so Codex knows what the prior tasks shipped and won't undo them.
- Deterministic repo metadata: top-level dirs, `package.json` scripts, last 5 git commits (oneline), README excerpt.
- Aggregated `prior_learnings` from previous runs' `memory-update.proposed.json` files (closed memory loop — RC-2A).

Codex sees this. It does NOT see the entire repo. The Promotion Gate's `diff_within_scope` rule (and the Apply Gate's `out_of_scope_change_count == 0` rule) are what enforce that scope at the gate layer.

Code: `orchestrator/core/agentic_runtime.py::_build_context_pack`.

---

## 5. AgenticProjectRuntime — multi-candidate inner loop

```
runtime.run(project, intent_overrides, patch_worker="codex", execute_eval=True, ...)
   │
   ├── write intent-contract.json + context-pack.json + eval-harness.json + task-slices.json
   ├── for candidate_strategy in CANDIDATE_STRATEGIES[:candidate_count]:
   │     a. patch_worker (codex)                  ← REAL LLM CALL
   │        — workspace-write sandbox, on-request approval
   │        — writes diff into ephemeral worktree under .agent/runs/<run_id>/candidates/<id>/worktree/
   │     b. real `git diff --binary --cached HEAD` against the worktree
   │        → patch.diff (RC-3E.2 fix; replaces difflib which produced corrupt diffs)
   │     c. score against per-candidate hard gates → score.json + changed-files.json
   │     d. execute eval-harness in the worktree → eval-results.json (per-command pass/fail)
   │     e. repair loop if eval failed (capped by max_repair_attempts)
   │     f. critic panel (correctness/regression/security/ux/overfit, evidence-grounded)
   ├── _build_promotion_report(candidates, ...) → promotion-report.json (Promotion Gate)
   └── return AgenticRunResult(run_id, decision, candidate, run_dir, ...)
```

Code: `orchestrator/core/agentic_runtime.py`.

Codex itself: `orchestrator/core/codex_patch_worker.py` adapter — the patch worker abstraction is replaceable; swapping in another CLI agent is a clean diff against that one file.

### Per-candidate run package on disk

```
.agent/runs/<run_id>/
  intent-contract.json
  context-pack.json
  eval-harness.json
  task-slices.json
  promotion-report.json                ← agentic.promotion_report.v2
  memory-update.proposed.json
  trace.jsonl
  applied-candidate.json               ← agentic.applied_candidate.v1 (only post-Apply-Gate)
  candidates/<candidate_id>/
    patch.diff                         ← real git diff
    changed-files.json                 ← agentic.changed_files.v1
    score.json                         ← per-candidate hard gates + soft scores
    eval-results.json                  ← per-command pass/fail; key is `commands` (not `commands_run`)
    repair-history.json
    run-log.jsonl
    critics/{correctness,regression,security,ux,overfit}.md
```

---

## 6. Promotion Gate — 12 deterministic hard rules

```
candidate ──→ Promotion Gate ──→ decision ∈ { promote, needs-human-review, abandoned }
                  │
                  └── output: promotion-report.json (gate_details: per-rule pass/fail)
```

The 12 hard rules:

| Rule | Check |
|------|-------|
| `source_patch_present` | Candidate produced a non-empty `patch.diff` |
| `diff_within_scope` | Every changed file matches some pattern in `scope_paths` (fnmatch) |
| `patch_apply_check_passed` | Real `git apply --check patch.diff` against `base_commit` exits 0 (RC-3E.2.3 added) |
| `required_eval_declared` | Eval harness has at least one `required: true` command |
| `required_eval_executed` | All required commands actually ran (no skipped) |
| `required_eval_passed` | All required commands exited 0 |
| `no_critical_security_finding` | Security critic didn't flag a high-severity issue |
| `no_critical_regression_finding` | Regression critic didn't flag breakage |
| `no_overfit_to_evals` | Diff doesn't mutate ONLY test files |
| `out_of_scope_change_count == 0` | Same intent as `diff_within_scope` but at the changed-files.json layer |
| `patch_size_within_budget` | Per-candidate size cap |
| `abandonment_history_clear` | Soft signal — recent abandonments lower the candidate's score |

**Decision logic.** If every gate passes → `promote`. If some pass and some fail → `needs-human-review` (and a review item lands in the human-in-the-loop queue with the gate breakdown). If all fail → `abandoned` (try the next candidate, or pause if none left).

Code: `orchestrator/core/agentic_runtime.py::_build_promotion_report`.

---

## 7. Apply Gate — 10 deterministic hard rules

Runs ONLY if Promotion Gate said `promote`. Re-checks safety from the live-git side at apply time.

| Rule | Check |
|------|-------|
| `promotion-report.schema_version == agentic.promotion_report.v2` | Schema match |
| `selected_candidate is not null` | Promotion actually picked one |
| `patch.diff exists and is non-empty` | Re-checked |
| `changed-files.json present` | Re-checked |
| `score.source_patch_present is True` | Re-checked |
| `out_of_scope_changes count == 0` | Re-checked from changed-files |
| `base_commit == current short HEAD` | No drift between Promotion and Apply |
| Worktree clean (modulo `.agent/` + `task-graph.json`) | Apply onto a clean tree |
| `git apply --check` exits 0 | Real git apply preflight |
| `applied-candidate.json doesn't already exist` | Re-apply guard |

On success: real `git apply patch.diff`, then write `applied-candidate.json` (`agentic.applied_candidate.v1`) under the run dir.

**Two gates on purpose** — Promotion Gate measures *what Codex produced*; Apply Gate measures *what the repo can safely accept right now*. A candidate that passed Promotion at time T can fail Apply at T+1 if HEAD moved.

Code: `orchestrator/core/run_package.py::apply_selected_candidate`.

---

## 8. Real git commit with provenance trailers

```
git add -A -- ':!.agent'              ← stage everything except runtime bookkeeping
git commit -m "<task title>

Agent-Task-ID: task-001
Agent-Run-ID: run_70dc791814
Selected-Candidate: candidate-a
Candidate-Strategy: conservative
Promotion-Decision: promote
Promotion-Report: .agent/runs/run_70dc791814/promotion-report.json
[Change-Id: change_198713d499]                   ← only on change-mode commits
[Source-Change-Request: .agent/changes/.../change-request.md]
[Corrective-Task: true]                          ← only on auto-corrective commits
[Source-Failure-ID: integration_failure_<id>]
[Human-Review-ID: review_<id>]                   ← only on human-override commits
[Human-Review-Decision: approved]
[Human-Review-Override: true]
"
```

Greppable forever via `git log --grep "Agent-Task-ID"` / `git log --grep "Change-Id"`. This is the most operator-friendly slice of the audit trail — you don't need to open `.agent/` to see what an agent commit was.

Code: `orchestrator/core/autonomous.py::commit_task`.

---

## 9. Change-mode-only post-commit hygiene

After `advance_one_task` returns (change mode), `change_runner._purge_task_graph_from_change_commit()` runs in a `finally` block:

1. Restore `task-graph.json` to its pre-change state (untrack + remove if no prior, restore content + stage if prior).
2. `git commit --amend --no-edit --no-verify` so the change commit's tree no longer contains the ephemeral 1-task graph.
3. Update `task_state["commit"]` to the new amended SHA so `applied-change.json` records the right hash.

Without this, `git status --short` after a change run showed `D task-graph.json` (no-prior case) or ` M task-graph.json` (prior-existed case), breaking the next change run's worktree-clean preflight. RC-4A.3.1.A.

---

## 10. Delivery artifacts (Change Request Mode)

```
.agent/changes/<change_id>/
  change-request.md                    ← immutable operator input
  change-contract.json                 ← agentic.change_contract.v1
  repo-onboarding.md                   ← deterministic project snapshot
  implementation-plan.md               ← derived plan
  acceptance-criteria.json
  applied-change.json                  ← agentic.applied_change.v1
                                          {change_id, candidate, run_id,
                                           base_commit, applied_to_commit,
                                           files_touched, applied_at,
                                           commit:{branch,sha,message},
                                           promotion_decision,
                                           source_change_request}
  delivery-report.md                   ← operator-facing summary
                                          # Change Delivery Report — change_<id>
                                          ## Goal | ## Result | ## What was changed
                                          ## Validation (eval.* / promotion / apply rows)
                                          ## Risks | ## Commit | ## Review queue | ## Timing
```

The Validation section reads from THREE sources at once: per-command rows from `eval-results.json` (RC-4A.3.1.B fix uses the producer's actual key `commands`), the `promotion` row from `promotion-report.json` (decision + `hard_gates=X/Y passed`), and the `apply` row from `applied-change.json`. If any source is missing the renderer falls back gracefully — but it never says "(no validation results recorded)" on a real change run.

Code: `orchestrator/core/change_runner.py::_finalize_change_outputs` + `change_delivery_report.py::render_delivery_report`.

---

## 11. Schema validators

Every load-bearing artifact has a validator in `orchestrator/core/artifact_validation.py`. `agent-studio change validate latest --json` walks a change dir; `agent-studio autonomous validate-artifacts --json` walks a session dir. Both return `ok=true/false` + per-artifact error list.

| Validator | Schema |
|-----------|--------|
| `validate_change_contract` | `agentic.change_contract.v1` |
| `validate_applied_change` | `agentic.applied_change.v1` |
| `validate_delivery_report_text` | markdown section markers |
| `validate_applied_candidate` | `agentic.applied_candidate.v1` (+ token-leak heuristic) |
| `validate_promotion_report` | `agentic.promotion_report.v2` (delegates to runtime's own validator) |
| `validate_autonomous_session` | autonomous-session.json shape |
| `validate_task_graph` | task-graph.json shape |
| `validate_review_item` | review-items/<id>.json |
| `validate_integration_failure` | integration-failure.json |
| `validate_deployment` | deployment.json (+ token-leak heuristic) |
| `validate_smoke_check` | smoke-check.json (+ header-redaction check) |
| `validate_rollback` | rollback.json |
| `validate_final_run_status_md` | final-run-status.md section markers |

This is what makes the audit trail trustworthy. A half-rendered or hand-edited artifact is caught.

---

## 12. Review queue (human-in-the-loop)

When a gate refuses or the inner loop returns `needs-human-review`, the controller writes a `review-items/<id>.json` (validated by `validate_review_item`) with severity (`blocking` / `warning` / `info`), reason_code (`failed-apply`, `needs-human-review`, `deployment-failed`, etc.), title, summary, evidence_paths, suggested_commands, allowed_actions.

CLI: `agent-studio autonomous reviews list / show / approve --yes / reject --reason / resolve --note`.

`approve --yes` is a human override that re-runs the Apply Gate with `human_override=True` (this only bypasses the "decision must be promote" rule; every safety gate still runs). The override is recorded as commit trailers `Human-Review-ID`, `Human-Review-Decision`, `Human-Review-Override: true` so audit can distinguish "Promotion-Gate-said-yes" from "a-human-said-yes-despite-the-gate".

The **resume guard** in `agent-studio autonomous start` refuses to advance if blocking review items are open — the controller will not silently keep retrying state that needs human judgment.

Code: `orchestrator/core/review_queue.py`.

---

## End-to-end trace (one example)

For the RC-4C real run on Demo 3 (`agent-review-queue-console`), change request "Add SLA risk badges":

```
1. agent-studio change new --from changes/01-add-sla-risk-badges.md
   → parse_change_request_text + scan_repo + create_change
   → writes .agent/changes/change_e8525afae2/{change-request.md, change-contract.json,
                                              repo-onboarding.md, implementation-plan.md,
                                              acceptance-criteria.json}

2. agent-studio change run latest
   ├── change_runner.run_change(project, change_id="change_e8525afae2", ...)
   │     ├── builds 1-task task-graph in memory
   │     ├── _swap_task_graph(): backs up project's task-graph.json
   │     ├── creates session_ae046c386f
   │     ├── overrides session.branch = "agentic/change/change_e8525afae2"
   │     ├── git checkout -b agentic/change/change_e8525afae2
   │     │
   │     └── controller.advance_one_task(session, new_graph)
   │           │
   │           ├── runtime.run(patch_worker="codex", execute_eval=True, ...)
   │           │   ├── Codex sees: intent + scope + 3 prior commits + project shape
   │           │   ├── Codex writes patch.diff (touches app/page.tsx + components/reviews.ts)
   │           │   ├── eval-harness runs `npm run build` + `npm run typecheck` in
   │           │   │   ephemeral worktree → both pass
   │           │   └── promotion-report.json: decision="promote", hard_gates=6/6 passed
   │           │
   │           ├── apply_candidate (Apply Gate: 10 rules, all pass)
   │           │   ├── git apply patch.diff (real)
   │           │   └── writes applied-candidate.json under run dir
   │           │
   │           ├── commit_task
   │           │   └── git commit (real) → SHA 63979f5 with full trailer set
   │           │
   │           └── maybe_run_integration → npm run build + typecheck pass
   │
   ├── _purge_task_graph_from_change_commit
   │   ├── restores task-graph.json content
   │   ├── git add task-graph.json
   │   └── git commit --amend → tree settled
   │
   └── _finalize_change_outputs
       ├── reads applied-candidate.json (run_1b3d513d29 / candidate-a)
       ├── writes applied-change.json (agentic.applied_change.v1)
       ├── builds validation block from eval-results + promotion-report + applied_change
       └── renders delivery-report.md (Validation section: 5 rows)

3. agent-studio change validate latest --json
   ├── validate_change_contract(...)        → []
   ├── validate_delivery_report_text(...)   → []
   └── validate_applied_change(...)         → []
   → ok=true

End state: git log --oneline -5 on agentic/change/change_e8525afae2
   63979f5 Surface time-based SLA risk on review items...   ← change commit
   5e1c3ce task-003 — Wire actions, badges, summary counts, filter
   7e7f113 task-002 — Seed review items
   45ac00a task-001 — Console page shell + summary + filter
   <root>  rc4b agent-review-queue-console baseline
```

---

## Why this layout

### Why two gates instead of one

Promotion Gate scores **what Codex produced** (the candidate). Apply Gate scores **what the repo can safely accept right now** (live git state). They measure different things on purpose. A candidate that scored "promote" at time T can fail Apply at T+1 if HEAD moved.

### Why deterministic Python in the gates instead of LLM judges

If the gate logic itself were an LLM call ("does this patch look in scope?"), the audit trail would be circular — you'd need another LLM to check the first LLM. Hard rules in real Python mean a reviewer can read the rule, read the artifact, and verify the decision matches.

### Why everything is JSON + markdown on disk

Two reasons. (a) `git log --grep "Agent-Task-ID"` / `git log --grep "Change-Id"` works forever — six months from now you can find every commit a specific session produced. (b) Schema validation catches half-rendered, hand-edited, or framework-mutated artifacts. **The artifact contract IS the product.**

### Why the same controller for greenfield and change mode

Every hardening of the autonomous controller (new gate rule, new validator, review-queue source type, etc.) is automatically a hardening of Change Request Mode. No code is duplicated; no behavior drifts. Change-mode-specific concerns (task-graph hygiene, applied-change.json, delivery-report.md) live in `change_runner.py`; everything else is shared.

---

## Key code pointers

| File | Owns |
|------|------|
| `orchestrator/core/autonomous.py` | `parse_requirements_md`, `AutonomousController`, `commit_task`, integration runner, deploy hooks |
| `orchestrator/core/agentic_runtime.py` | `AgenticProjectRuntime.run`, multi-candidate loop, Promotion Gate, eval harness, scorer, repair loop |
| `orchestrator/core/run_package.py` | `apply_selected_candidate` (Apply Gate), `RunPackage` / `CandidateReport` readers |
| `orchestrator/core/change_runner.py` | `run_change` (change-mode entry), task-graph swap/restore + amend, `applied-change.json` + `delivery-report.md` |
| `orchestrator/core/change_contract.py` | parse + write change-mode artifacts, `change_status_summary` state machine |
| `orchestrator/core/change_request_parser.py` | parse `change-request.md` (deterministic) |
| `orchestrator/core/change_repo_onboarding.py` | `scan_repo`, `render_repo_onboarding` |
| `orchestrator/core/change_delivery_report.py` | `render_delivery_report` markdown |
| `orchestrator/core/artifact_validation.py` | every schema validator |
| `orchestrator/core/codex_patch_worker.py` | Codex CLI adapter |
| `orchestrator/core/review_queue.py` | review-items/<id>.json + CRUD + resume gating |
| `orchestrator/cli.py` | every `agent-studio <subcommand>` handler |

For tests: `tests/unit/test_*`, `tests/e2e/test_change_run_e2e.py`, `tests/e2e/test_golden_path.py`. 337 tests pass.
