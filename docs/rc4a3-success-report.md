# RC-4A.3 — Real Codex Change-Run Tiny Probe — Success Report

**Status:** RC-4A.3 core ✅ SUCCESS · RC-4A.3.1 cleanup ✅ SHIPPED · ready for RC-4B.

## Core result — RC-4A.3

The real Codex change run against the `.dogfood/rc4a3-change-run-tiny` seed produced a clean delivery against the "Add status filter" change request:

| Field | Value |
| --- | --- |
| change_id | `change_198713d499` |
| run_id | `run_87fe64f49f` |
| commit | `a7ef695` |
| branch | `agentic/change/change_198713d499` |
| changed file | `app/page.tsx` |
| change run result | `completed` |
| real Codex | yes (`patch_worker=codex`, sandbox=workspace-write) |
| Promotion Gate | `promote` |
| Apply Gate | succeeded |
| Change-Id trailer | present |
| Source-Change-Request trailer | present |
| `applied-change.json` | exists |
| `delivery-report.md` | exists |
| `change validate latest --json` | `ok=true` |
| `npm run build` | passed |
| review queue | 0 open |

This is the first end-to-end real-Codex run through Change Request Mode (RC-4A.2). The probe confirms every load-bearing seam — parser, contract, repo onboarding, 1-task task-graph swap, AutonomousController, real `AgenticProjectRuntime`, 12-rule Promotion Gate, 10-rule Apply Gate, Change-Id git trailers, applied-change.json, delivery-report.md, review queue — works on a non-trivial real-world tiny project.

## What the real run surfaced

Despite the core success, three cleanup-grade quality issues fired:

### Issue 1 — worktree left dirty (`D task-graph.json`)

`git status --short` showed `D task-graph.json` after a successful run. Cause: the autonomous controller's `commit_task` runs `git add -A -- ':!.agent'`, which captures the ephemeral 1-task `task-graph.json` change-mode wrote into the project root. On the post-run restore (no prior task-graph existed for this project) the change_runner unlinked the file, but the commit had already added it — so git tracked it while the file was gone, producing the `D` row. For a project that already had a `task-graph.json` (autonomous + change mixed), the symmetric breakage was `M task-graph.json` after restoring the original content. Either way the next `change run` preflight refused to start.

### Issue 2 — delivery-report Validation section empty

`delivery-report.md` rendered `## Validation` as `- (no validation results recorded)` even though Codex's eval pass had passed real commands. Cause: a producer/consumer schema mismatch — `agentic_runtime` writes the per-candidate `eval-results.json` with key `commands`, but the RC-4A.2 first cut of `change_runner._read_eval_validation` read `commands_run`. Every real-Codex change run looked validation-empty.

### Issue 3 — `change validate` didn't validate `applied-change.json`

`agent-studio change validate latest --json` only included `change-contract.json` and `delivery-report.md` in its report. `applied-change.json` was silently skipped — so the operator's "did this change actually deliver something coherent?" check covered the human-facing markdown but NOT the schema-bearing JSON.

## RC-4A.3.1 cleanup fixes shipped

### A. Task-graph restore hygiene

Added `change_runner._purge_task_graph_from_change_commit` which runs in the `finally` block after `advance_one_task`. It resets `task-graph.json` to its pre-change state (untracked + removed if no prior, restored to `backup_payload` content if prior) and amends the change commit so its tree matches. The amend also updates the commit SHA, which `task_state["commit"]` and downstream `applied-change.json` pick up automatically.

- File: `orchestrator/core/change_runner.py` — `_purge_task_graph_from_change_commit` helper + updated `run_change` finally block.
- Tests: `test_change_run_happy_path_completed` now asserts `git status --porcelain` is empty AND `HEAD` does not contain `task-graph.json`. `test_change_run_restores_prior_task_graph` asserts the prior content's git blob SHA is identical at HEAD post-run.

### B. Delivery-report Validation section populated

Replaced `_read_eval_validation` with a multi-source builder that surfaces:

- **`eval.<name>`** rows from `eval-results.json`'s `commands` array (correct field name).
- **`eval.required`** roll-up showing required-eval declared/executed/passed.
- **`promotion`** row from `promotion-report.json` — decision + `gate_details` pass count (`hard_gates=3/3 passed`).
- **`apply`** row from `applied-change.json` — applied-to commit + branch.

Non-completed paths (needs-human-review / failed) also surface eval + promotion rows now, so the report can explain WHY the change paused. Helper `_peek_candidate_id_from_promotion` derives the right candidate id from `promotion-report.json` when there's no `applied-change.json` to read from.

- Files: `orchestrator/core/change_runner.py`.
- Tests: 3 new unit tests in `test_change_runner.py::ReadEvalValidationTests` (eval+promotion+apply all present, no-eval-still-returns-promotion-block, no-run-id-empty). e2e `test_change_run_happy_path_completed` asserts `**eval.build**: passed`, `**eval.test**: passed`, `**promotion**: passed — decision=promote, hard_gates=3/3 passed`, `**apply**: passed`, NO `(no validation results recorded)`.

### C. `change validate` now validates `applied-change.json` when present

`cmd_change_validate` reads `applied-change.json` if it exists and passes it through `validate_applied_change`. When the change is still `ready_for_run` (file not yet produced) the validator does NOT require it, so the pre-run validation flow stays unchanged.

- File: `orchestrator/cli.py`.
- Tests: `test_change_new_show_status_validate_round_trip` asserts the pre-run report has neither `applied-change.json` nor `delivery-report.md` in it (ready_for_run case). `test_change_run_happy_path_completed` asserts `validate_applied_change(applied)` returns `[]` against the on-disk artifact.

## Test status

| Suite | Tests | Status |
| --- | --- | --- |
| `tests/unit/test_change_runner.py` | 21 | ✅ pass |
| `tests/e2e/test_change_run_e2e.py` | 3 | ✅ pass |
| `tests/e2e/test_change_cli_flow.py` | 2 | ✅ pass |
| `tests/unit/test_change_contract.py` | 16 | ✅ pass |
| `tests/unit/test_change_delivery_report.py` | 8 | ✅ pass |
| `tests/unit/test_change_request_parser.py` | 10 | ✅ pass |
| `tests/unit/test_change_repo_onboarding.py` | 9 | ✅ pass |
| `tests/unit/test_artifact_validation.py` | — | ✅ pass |
| `tests/unit/test_autonomous.py` | 58 | ✅ pass (commit_task trailers untouched) |
| `tests/unit/test_agentic_runtime.py` | 86 | ✅ pass |
| `tests/unit/test_run_package.py` | 7 | ✅ pass |
| `tests/unit/test_codex_patch_worker.py` | 26 | ✅ pass |
| `tests/unit/test_rc2c1_fixes.py` | 15 | ✅ pass |
| `tests/e2e/test_golden_path.py` | 2 | ✅ pass |
| `tests/unit/test_pause_then_render.py` | 3 | ✅ pass |
| `tests/unit/test_backward_compat_session.py` | 10 | ✅ pass |
| `tests/unit/test_next_actions.py` | 14 | ✅ pass |

Targeted RC-4A + autonomous + agentic + run_package + artifact_validation = **298 tests pass** in 7.35s. Adding golden-path + pause-then-render + backward-compat + next-actions = **327 tests pass**.

## Files changed in RC-4A.3.1

| File | Change |
| --- | --- |
| `orchestrator/core/change_runner.py` | `_purge_task_graph_from_change_commit` helper; multi-source `_read_eval_validation`; `_peek_candidate_id_from_promotion` helper; updated `run_change` finally block + non-completed validation surface |
| `orchestrator/cli.py` | `cmd_change_validate` now includes `applied-change.json` when present |
| `tests/unit/test_change_runner.py` | New `ReadEvalValidationTests` suite (3 tests) |
| `tests/e2e/test_change_run_e2e.py` | Happy-path asserts worktree clean + HEAD tree clean + delivery-report Validation rows + validate_applied_change. Prior-graph test asserts git-blob equality. Fake fixture aligned to producer `commands` shape. |
| `tests/e2e/test_change_cli_flow.py` | Pre-run validate asserts `applied-change.json` and `delivery-report.md` are NOT in the report (ready_for_run state) |

## Conclusion

RC-4A.3 demonstrated that Change Request Mode works against real Codex on a real-world tiny project. RC-4A.3.1 cleaned the three quality issues the real run surfaced. Change Request Mode is now full-clean: a `change run` leaves a clean worktree, a `delivery-report.md` that reflects the real eval/promotion/apply outcomes, and a `change validate` that covers every schema-bearing artifact in the change dir.

## Next milestone

**RC-4B — 3-project demo matrix.** Three distinct project shapes, each with one change. Use the RC-4A.3 recipe (tiny Next.js + simple change-request.md + small budgets + no Vercel) as the template. Holding for explicit `go RC-4B scoping`.
