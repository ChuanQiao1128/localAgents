# Local Agent Dev Studio — Architecture Walkthrough

This doc traces what happens when you type `agent-studio change run latest` (the most representative single command) — every component that fires, what it reads, what it writes. Pair with `01-project-summary.md` for term definitions and `03-failure-cases.md` for the bugs each component has been hardened against.

中文导读: 这一篇按 `agent-studio change run latest` 这条命令的执行路径,把每个组件、每个读写的文件、每个闸门按顺序讲一遍,配 ASCII 图。

---

## High-level diagram

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                                                                              │
│   Operator writes:                                                           │
│     requirements.md           change-request.md                              │
│         │                            │                                       │
│         ▼                            ▼                                       │
│   ┌──────────────┐           ┌──────────────────┐                            │
│   │  Greenfield  │           │  Change Request  │  ← two entry points,       │
│   │     Mode     │           │      Mode        │    same machinery          │
│   └──────┬───────┘           └─────────┬────────┘                            │
│          │                              │                                    │
│          │  parse_requirements_md()     │  parse_change_request_text()       │
│          │  (deterministic Python,      │  + scan_repo() + create_change()   │
│          │   no LLM)                    │                                    │
│          ▼                              ▼                                    │
│     task-graph.json              .agent/changes/<change_id>/                 │
│     (N tasks)                       change-contract.json                     │
│                                     (1-task task-graph built in memory)      │
│          │                              │                                    │
│          └──────────────┬───────────────┘                                    │
│                         │                                                    │
│                         ▼                                                    │
│              ┌──────────────────────┐                                        │
│              │ AutonomousController │  drives the outer SDLC loop            │
│              │  .advance_one_task() │                                        │
│              └──────────┬───────────┘                                        │
│                         │                                                    │
│                         ▼                                                    │
│        ┌──────────────────────────────┐                                      │
│        │ AgenticProjectRuntime.run()  │  inner agent loop per task           │
│        │  (multi-candidate Codex)     │                                      │
│        └────────┬───────────┬─────────┘                                      │
│                 │           │                                                │
│       per-candidate         per-candidate                                    │
│         patch.diff          eval-results.json                                │
│                 │           │                                                │
│                 └─────┬─────┘                                                │
│                       ▼                                                      │
│             ┌──────────────────┐                                             │
│             │  Promotion Gate  │  12 deterministic rules                     │
│             │  → decision      │  → promote / needs-human-review / abandoned │
│             └────────┬─────────┘                                             │
│                      │ (if promote)                                          │
│                      ▼                                                       │
│             ┌──────────────────┐                                             │
│             │   Apply Gate     │  10 deterministic rules                     │
│             │   → git apply    │  + applied-candidate.json                   │
│             └────────┬─────────┘                                             │
│                      │                                                       │
│                      ▼                                                       │
│             ┌──────────────────┐                                             │
│             │   commit_task()  │  real git commit on a session/change       │
│             │                  │  branch with provenance trailers            │
│             └────────┬─────────┘                                             │
│                      │                                                       │
│      ┌───────────────┴────────────────┐                                      │
│      ▼                                ▼                                      │
│  Greenfield: next task             Change Mode:                              │
│  in task-graph                     write applied-change.json +               │
│  (advance_one_task                 delivery-report.md                        │
│  again)                                                                      │
│                                                                              │
└──────────────────────────────────────────────────────────────────────────────┘
```

---

## Component-by-component

### 1. Two entry points, identical interior

Greenfield (`agent-studio autonomous start`) and Change Request Mode (`agent-studio change run`) both land in `AutonomousController.advance_one_task`. The only differences are:

- Greenfield reads `task-graph.json` from disk (the deterministic decomposer wrote it from `requirements.md`).
- Change Mode synthesizes a 1-task task-graph in memory from `change-contract.json` and **temporarily swaps** the project's `task-graph.json` for it (RC-4A.3.1.A's task-graph hygiene fix amends the change commit so the swap is invisible in git).

This unification is load-bearing: every hardening of the autonomous controller (Promotion Gate, Apply Gate, review queue, integration runner) automatically also hardens Change Request Mode. No code is duplicated between the two paths.

### 2. Deterministic decomposer (parser)

`orchestrator/core/autonomous.py::parse_requirements_md()` is pure Python regex + line scanning — no LLM. Same input → same output. This is what makes the task-graph reproducible: a reviewer can run the decomposer locally and check that the agent's task list matches what `requirements.md` actually said.

After RC-4C.1.A:
- Strips wrapping backticks from any meta-line value (`Scope:`, `Depends:`) — writers naturally write ``Scope: `app/**` `` and pre-fix the parser captured the backticks literally, breaking the gate.
- Supports both inline form (`Scope: app/**, components/**`) AND multi-line bullet form (`Scope:\n- app/**\n- components/**`) per metadata field.

### 3. Repo onboarding (change mode only)

`orchestrator/core/change_repo_onboarding.py::scan_repo()` reads the project's `package.json` scripts, top-level dirs, last 5 commits, and a README excerpt to produce a deterministic project snapshot. Codex sees this in its context pack so it can write a patch that fits the existing project shape (e.g. extends an existing `analyze()` function rather than inventing a new one).

### 4. Change contract

For change mode, `orchestrator/core/change_contract.py::create_change()` writes 5 artifacts under `<project>/.agent/changes/<change_id>/`:

```
change-request.md          ← immutable copy of operator input
change-contract.json       ← schema agentic.change_contract.v1
                              {goal, scope_paths, non_goals, acceptance,
                               scope_missing, source_change_request_path, created_at}
repo-onboarding.md         ← deterministic project snapshot
implementation-plan.md     ← derived from contract + onboarding (Codex sees this)
acceptance-criteria.json   ← shape-compatible with autonomous mode
```

`change_id` looks like `change_198713d499` (`<prefix>_<10-char-hex>` from `orchestrator.core.ids.short_id`). Same pattern as `session_*`, `run_*`, `deployment_*`, etc.

### 5. AutonomousController

`orchestrator/core/autonomous.py::AutonomousController.advance_one_task()` is the outer loop:

```
while next_task = pick_next_eligible_task(task_graph):
    1. emit "task_started"
    2. build context_pack from intent + scope + previous_completed_tasks
    3. result = run_inner_loop(project, intent_overrides=...)         ← AgenticProjectRuntime
    4. if result.decision == "promote":
         applied = apply_candidate(...)                                ← Apply Gate
         commit_sha = commit_task(...)                                 ← real git commit
         task.status = "completed"; task.commit = commit_sha
         maybe_run_integration(...)                                    ← npm run build + typecheck
       elif result.decision == "needs-human-review":
         emit_review_item(); session.pause()
       elif result.decision == "abandoned":
         task.status = "abandoned"; maybe_continue_or_complete()
    5. save session + task graph + final-run-status.md
```

Per-task budgets cap runaway runs: `max_tasks_per_session`, `max_total_inner_runs`, `max_candidates_per_task`, `max_repair_attempts_per_candidate`, `max_abandoned_tasks`, `max_corrective_tasks`. The RC-4C demo matrix used `3 / 5 / 1 / 1 / 1 / 1` — tight ceiling so a runaway Codex still costs <100k tokens per demo.

### 6. AgenticProjectRuntime (inner agent loop)

`orchestrator/core/agentic_runtime.py::AgenticProjectRuntime.run()` per-task:

```
1. build intent-contract.json (goal + success_criteria + scope)
2. build context-pack.json (repo metadata + previous task commits +
                            prior_learnings from memory-update.proposed.json
                            of previous runs)
3. build eval-harness.json (the npm scripts + test commands to run)
4. for candidate_strategy in CANDIDATE_STRATEGIES[:candidate_count]:
     a. run patch_worker (codex)                                        ← real LLM call
     b. score against hard gates (source_patch_present, diff_within_scope,
        patch_apply_check_passed, etc.)
     c. write candidates/<id>/{patch.diff, score.json,
        changed-files.json, eval-results.json,
        critics/{correctness,regression,security,ux,overfit}.md}
     d. run repair loop if eval failed (capped by max_repair_attempts)
5. _build_promotion_report(candidates, ...) → promotion-report.json
6. return AgenticRunResult(run_id, decision, candidate, run_dir, ...)
```

Codex itself is invoked via `orchestrator/core/codex_patch_worker.py` (sandbox `workspace-write`, approval `on-request`). Codex writes its diff into an ephemeral worktree under `.agent/runs/<run_id>/candidates/<candidate>/worktree/`; the runtime then computes a real `git diff --binary --cached HEAD` against that worktree (RC-3E.2 fix — replaces difflib which produced corrupt patches).

### 7. Promotion Gate (12 deterministic rules)

Lives in `_build_promotion_report` in `agentic_runtime.py`. Reads each candidate's `score.json` + `eval-results.json` + `changed-files.json`. The 12 hard gates include:

```
source_patch_present              candidate produced a non-empty patch
diff_within_scope                 every changed file matches scope_paths
patch_apply_check_passed          `git apply --check` against base_commit clean
required_eval_declared            eval-harness has at least one required command
required_eval_executed            all required commands actually ran
required_eval_passed              all required commands exited 0
no_critical_security_finding      security critic didn't flag a high-severity issue
no_critical_regression_finding    regression critic didn't flag breakage
no_overfit_to_evals               diff doesn't only mutate test files
out_of_scope_change_count == 0    files outside scope_paths are zero
                                  (matches the same diff_within_scope intent
                                  but at the changed-files.json layer)
patch_size_within_budget          size cap per candidate
abandonment_history_clear         soft signal — recent abandonments lower the score
```

Decision logic: `all_pass=True` → `promote`. Some pass + some fail → `needs-human-review` (the operator's queue gets an item with the gate breakdown). All fail → `abandoned`.

Output: `promotion-report.json` (schema `agentic.promotion_report.v2`) with `decision`, `selected_candidate`, `hard_gates`, `gate_details`, `eval`, `repair`, `soft_scores`, `candidates`, `candidate_diversity`.

### 8. Apply Gate (10 deterministic rules)

Lives in `orchestrator/core/run_package.py::apply_selected_candidate()`. Runs only if Promotion Gate said `promote`. Re-checks safety from a different angle:

```
promotion-report v2 schema present
selected_candidate not null
patch.diff exists and is non-empty
changed-files.json present
score.source_patch_present == True
no out_of_scope_changes (re-checked)
base_commit matches current short HEAD
worktree clean (modulo `.agent/` + `task-graph.json`)
git apply --check exits 0
re-apply guard: applied-candidate.json doesn't already exist
```

Two gates instead of one because they measure different things. Promotion Gate asks "is this candidate good enough?" against the run package. Apply Gate asks "is the repo safe to accept this right now?" against live git state. A candidate can pass Promotion at time T and fail Apply at time T+1 if HEAD moved or another commit landed. Caught in test_run_package.py.

On success: `git apply` real, then write `applied-candidate.json` (schema agentic.applied_candidate.v1) under the run dir.

### 9. commit_task() with provenance trailers

`autonomous.py::commit_task()` runs `git add -A -- ':!.agent'` (everything except `.agent/`), then commits with a body including:

```
Agent-Task-ID: task-001
Agent-Run-ID: run_70dc791814
Selected-Candidate: candidate-a
Candidate-Strategy: conservative
Promotion-Decision: promote
Promotion-Report: .agent/runs/run_70dc791814/promotion-report.json
[Change-Id: change_c19add9a71]                  ← only on change-mode commits
[Source-Change-Request: .agent/changes/.../change-request.md]
```

Greppable forever via `git log --grep "Agent-Task-ID"` / `git log --grep "Change-Id"`. This is the most operator-friendly part of the audit trail — you don't need to open `.agent/` to see what an agent commit was.

### 10. Change-mode-only post-commit hygiene

After `advance_one_task` returns (change mode), `change_runner.py::_purge_task_graph_from_change_commit()` runs in a `finally` block to:

1. Reset `task-graph.json` to its pre-change state (untrack + remove if no prior, restore content + stage if prior).
2. `git commit --amend --no-edit --no-verify` so the change commit's tree no longer contains the ephemeral 1-task graph.
3. Update `task_state["commit"]` to the new amended SHA so `applied-change.json` records the right hash.

Without this, `git status --short` after a change run showed `D task-graph.json` (no-prior case) or ` M task-graph.json` (prior-existed case), breaking the next `change run`'s worktree-clean preflight. RC-4A.3.1.A.

### 11. Delivery report + applied-change.json (change mode)

`change_runner.py::_finalize_change_outputs()` writes:

```
.agent/changes/<change_id>/
  applied-change.json       ← agentic.applied_change.v1
                              {change_id, candidate, run_id, base_commit,
                               applied_to_commit, files_touched, applied_at,
                               commit:{branch,sha,message},
                               promotion_decision, source_change_request}
  delivery-report.md        ← operator-facing summary
                              # Change Delivery Report — change_<id>
                              ## Goal
                              ## Result   (**completed** / **needs-human-review** / **failed**)
                              ## What was changed   (file list)
                              ## Validation         (eval.* / promotion / apply rows)
                              ## Risks
                              ## Commit             (branch / SHA / message)
                              ## Review queue       (open count + items)
                              ## Timing
```

The Validation section's `eval.*` rows come from per-candidate `eval-results.json` (RC-4A.3.1.B fix uses the producer's `commands` key, not the wrong `commands_run`). `promotion` row comes from `promotion-report.json`. `apply` row comes from `applied-change.json` we just wrote. All three sources, one section. If there's no real eval data (e.g. failed run), the section still surfaces `promotion` so the operator knows WHY it failed.

### 12. Schema validation

`orchestrator/core/artifact_validation.py` exports validators for every load-bearing artifact:

```
validate_change_contract             agentic.change_contract.v1
validate_applied_change              agentic.applied_change.v1
validate_delivery_report_text        markdown section markers
validate_applied_candidate           agentic.applied_candidate.v1
validate_promotion_report            agentic.promotion_report.v2 (delegates to runtime)
validate_autonomous_session          autonomous-session.json
validate_task_graph                  task-graph.json
validate_review_item                 review-items/<id>.json
validate_integration_failure         integration-failure.json
validate_deployment                  deployment.json
validate_smoke_check                 smoke-check.json
validate_rollback                    rollback.json
validate_final_run_status_md         final-run-status.md section markers
```

`agent-studio change validate` and `agent-studio autonomous validate-artifacts` walk a directory and apply every relevant validator; `ok=true/false` + per-artifact error list. Token-leak heuristics built into a few validators flag JWT-shaped or 40+-hex strings appearing in unexpected fields (defense against env-var leaks into commit metadata).

### 13. Review queue (human-in-the-loop)

When a gate refuses or the inner loop returns `needs-human-review`, `review_queue.py::create_review_item()` writes a `review-items/<id>.json` (schema validated) with `severity` (`blocking` / `warning` / `info`), `reason_code` (`failed-apply`, `needs-human-review`, `deployment-failed`, etc.), `title`, `summary`, `evidence_paths`, `suggested_commands`, `allowed_actions`. The CLI exposes `agent-studio autonomous reviews list / show / approve --yes / reject --reason / resolve --note`. `approve --yes` is a human override that re-runs the Apply Gate (with `human_override=True` permitting promotion-decision-mismatch only, not the safety gates).

The **resume guard** in `agent-studio autonomous start` refuses to advance if blocking review items are open — the controller will not silently keep retrying state that needs human judgment. RC-4D MVP includes the resume guard test.

---

## Data flow summary (one trace)

For Demo 3 (`agent-review-queue-console`), change request "Add SLA risk badges":

```
1. cmd_change_run reads change-contract.json (change_e8525afae2)
   → goal="Surface time-based SLA risk on review items..."
   → scope_paths=["app/**", "components/**"]
   → acceptance=[6 items]

2. change_runner.run_change()
   ├── builds 1-task task-graph in memory
   ├── _swap_task_graph(): backs up project task-graph.json (it had 3 prior
   │   greenfield tasks), writes the 1-task graph for the controller
   ├── creates session_ae046c386f
   ├── overrides session.branch = "agentic/change/change_e8525afae2"
   ├── _create_or_checkout_branch (real git checkout -b)
   └── controller.advance_one_task(session, new_graph)
       │
       ├── runtime.run(patch_worker="codex", execute_eval=True, ...)
       │   ├── Codex sees: intent + scope + 3 prior commits' hashes/titles
       │   │   + project shape + pricing module + reviews seed data
       │   ├── Codex writes patch.diff (touches app/page.tsx + components/reviews.ts)
       │   ├── eval harness runs `npm run build` + `npm run typecheck` in
       │   │   ephemeral worktree → both pass
       │   └── promotion-report.json: decision="promote", hard_gates=6/6 passed
       │
       ├── apply_candidate (Apply Gate)
       │   ├── 10 hard rules pass
       │   ├── git apply patch.diff (real)
       │   └── writes applied-candidate.json
       │
       ├── commit_task
       │   ├── git add -A -- ':!.agent' (stages app/page.tsx + components/reviews.ts
       │   │   AND the ephemeral task-graph.json)
       │   └── git commit with body: "Surface time-based SLA risk on review items..."
       │       + 8 trailer lines including Change-Id + Source-Change-Request
       │       → SHA 63979f5
       │
       └── maybe_run_integration (per-task) + (session-end) → both pass

3. _purge_task_graph_from_change_commit
   ├── restores task-graph.json content to backup_payload
   ├── git add task-graph.json
   ├── git commit --amend → new SHA (also 63979f5 — git's stable on small amends
   │   when the tree settles)
   └── task_state["commit"] = 63979f5

4. _finalize_change_outputs
   ├── reads applied-candidate.json (run_1b3d513d29 / candidate-a)
   ├── builds applied_change_payload, writes applied-change.json
   ├── builds validation block from eval-results.json + promotion-report.json
   │   + applied_change_payload
   └── renders delivery-report.md (Validation section: 5 rows)

5. change validate latest --json
   ├── validate_change_contract(change-contract.json)        → []
   ├── validate_delivery_report_text(delivery-report.md)     → []
   └── validate_applied_change(applied-change.json)          → []
   → ok=true

6. autonomous validate-artifacts --json
   walks .agent/, validates every artifact present → all ok=true
```

End state: `git log --oneline -5` on `agentic/change/change_e8525afae2`:
```
63979f5 Surface time-based SLA risk on review items...   ← change commit
5e1c3ce task-003 — Wire actions, badges, summary counts, filter
7e7f113 task-002 — Seed review items
45ac00a task-001 — Console page shell + summary + filter
<root>  rc4b agent-review-queue-console baseline
```

---

## Why this layout

### Why two gates instead of one

Promotion Gate scores **what the candidate produced**. Apply Gate scores **what the repo can safely accept right now**. They measure different things. A candidate that scored "promote" yesterday could fail Apply today if HEAD moved.

### Why deterministic Python in the gates

If the gate logic itself were an LLM call ("does this patch look in scope?"), the audit trail would be circular. Hard rules in real Python mean a reviewer can read the rule, read the artifact, and verify the decision matches. This is the difference between "AI did it" and "AI did the keystrokes; deterministic logic decided what's safe."

### Why everything is JSON + markdown on disk

Two reasons. (a) git-grep-able forever — six months from now you can `git log --grep` to find every commit a specific change session produced. (b) Schema validation catches half-rendered, hand-edited, or framework-mutated artifacts. The artifact contract IS the product.

### Why same controller for greenfield and change mode

Every hardening of the autonomous controller is automatically also a hardening of Change Request Mode. No code is duplicated; no behavior drifts. RC-4A.3.1's task-graph hygiene fix lives in `change_runner.py` because it's change-mode-specific (autonomous mode WANTS task-graph.json tracked), but the inner controller / Apply Gate / commit_task / review queue are all unmodified.

---

## Where to read the code

| File | What it owns |
|------|--------------|
| `orchestrator/core/autonomous.py` | parse_requirements_md, AutonomousController, commit_task, integration runner, review queue plumbing, deploy hooks |
| `orchestrator/core/agentic_runtime.py` | AgenticProjectRuntime.run, multi-candidate loop, Promotion Gate, eval harness execution, score / scorer / diversity, repair loop |
| `orchestrator/core/run_package.py` | apply_selected_candidate (Apply Gate), RunPackage / CandidateReport readers |
| `orchestrator/core/change_runner.py` | run_change (change-mode entry), task-graph swap/restore + amend, applied-change.json / delivery-report.md |
| `orchestrator/core/change_contract.py` | parse + write change-mode artifacts, change_status_summary state machine |
| `orchestrator/core/change_request_parser.py` | parse change-request.md (deterministic) |
| `orchestrator/core/change_repo_onboarding.py` | scan_repo, render_repo_onboarding |
| `orchestrator/core/change_delivery_report.py` | render_delivery_report markdown |
| `orchestrator/core/artifact_validation.py` | every schema validator |
| `orchestrator/core/codex_patch_worker.py` | Codex CLI adapter (sandbox + approval policy) |
| `orchestrator/core/review_queue.py` | review-items/<id>.json schema + CRUD + resume gating |
| `orchestrator/cli.py` | every `agent-studio <subcommand>` handler |

For tests: `tests/unit/test_autonomous.py`, `tests/unit/test_agentic_runtime.py`, `tests/unit/test_change_*`, `tests/unit/test_run_package.py`, `tests/e2e/test_change_run_e2e.py`, `tests/e2e/test_golden_path.py`.
