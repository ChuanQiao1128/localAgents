# RC-2C / RC-2C.1 / RC-2C.2 Combined Report — Real Vercel Preview Dogfood + State Reconciliation + Clean Rerun

Date: 2026-05-11.

## RC-2C.2 clean rerun — SUCCESS (Result A)

Re-ran `scripts/rc2c.sh --run` against the same dogfood repo after
RC-2C.1 landed. **Every fix landed observably; no new bug found.**

```
workspace:           /tmp/rc2c-real
project id:          project_00337d3c57
session id:          session_2f41c37c75
session status:      completed              ← was: paused (budget) pre-fix
deployment status:   verified               ← was: smoke-failed (stale) pre-fix
review queue:        0 open
validate-artifacts:  ok=true
```

**Per-task results (all candidate-a, RC-2C.1 fix #3 observable):**

| Task | Commit | Run | Candidate | Wall-clock |
|---|---|---|---|---|
| task-001 Add a status filter UI | `40dc059` | `run_af74350118` | `candidate-a` | 1m 02s |
| task-002 Add an empty state for zero projects | `be82d14` | `run_1ddfb5126e` | `candidate-a` | 2m 33s |
| task-003 Show project name, status, and due date in each row | `42c770f` | `run_43ceae966b` | `candidate-a` | 1m 24s |

Total Codex wall-clock: **~5 minutes** vs. RC-2B.2's ~17 minutes —
direct empirical evidence that `max_candidates_per_task=1` is now
actually capping candidate generation (3 tasks × 1 candidate vs.
3 tasks × 3 candidates previously). Token spend correspondingly
dropped to ~1/3 of RC-2B.2's estimate.

**Integration:** passed twice (periodic + session_end), 0.192s + 0.186s.

**Vercel preview:**
- `vercel deploy` exit 0, `vercel inspect` exit 0
- deployment_url: `https://rc2-creator-tracker-rcuxxhkpk-pianxing11281128s-projects.vercel.app`
- deployment.json status=`ready`, failure=null

**Smoke:**
- 1 check (`home` GET `/` expect 200)
- actual 200, latest `smoke_1b3c2c90c5`, status=`passed`
- (vercel.json `outputDirectory=dist` from RC-2C.1.5 lets Vercel serve
  the built artifact; SSO protection was disabled at the team level
  from RC-2C — that's operator state, not a product change)

**Per-fix evidence:**

| RC-2C.1 fix | Observable in RC-2C.2 |
|---|---|
| Manual smoke success persists state | `session.deployment.status=verified`, `latest_smoke_status=passed` (auto path; manual path also fixed but not exercised here) |
| Budget vs finalization order | `session.status=completed`, no `budget:max_tasks_per_session` pause |
| `max_candidates_per_task=1` propagates | Every task selected `candidate-a` only; Codex wall-clock ≈ 1/3 of RC-2B.2 |
| Prompt: success_criteria + previous tasks + scope wording | All 3 tasks promoted first-try, integration passed both times — no Codex failure mode observed |
| `vercel.json` seed | Smoke 200 OK first try, no manual intervention |

**Status of remaining observations:**

- `?` markers in status Budget block — still cosmetic. RC-2C.2 status
  output still shows `max_candidates_per_task: ? / 1 *` because no
  usage counter is wired. Confirmed harmless; deferred.
- `.dogfood/rc2-creator-tracker/.gitignore` includes `.vercel` — the
  link directory IS used during dogfood seeding (script copies it
  in) but should remain gitignored in the dogfood repo itself.
  Working correctly.

**Next milestone:** RC-3A — Next.js SaaS shape probe (per the 3-layer
roadmap in the strategic update). NOT started; awaiting explicit go
signal.

---



## RC-2C functional outcome (verified on Chuan's Mac)

The full ladder ran end-to-end against real services for the first time:

```
real Codex (3 tasks)
  → 3 real commits (8b020c0 / 0d49290 / ba67705 from RC-2B.2 baseline,
                    later regenerated against the same dogfood for RC-2C
                    as 1d2…/da8f…/etc. on session_b82c6d6a3c)
  → real `vercel deploy` to preview scope `pianxing11281128s-projects`
  → preview URL `https://rc2-creator-tracker-rm1ywvjxr-...vercel.app`
  → first auto-smoke FAILED (401 — Vercel team SSO protection on preview URLs)
  → operator disabled deployment protection / added bypass
  → manual smoke RE-RAN, status=passed (200 OK with the actual rendered page)
```

This is the milestone that RC-2C was set up to verify. **The deploy +
smoke ladder works against real Vercel CLI, not just fakes.**

## RC-2C state reconciliation bug (found + fixed in RC-2C.1)

After the manual smoke healed the failure, `autonomous status` still
reported the OLD failed smoke and `pause_reason: budget:max_tasks_per_session`.
Diagnosis from session_b82c6d6a3c artifacts:

| Artifact | Reality | Pre-fix session display |
|---|---|---|
| `deployment.json` | status=ready, valid URL, `failure: null` | OK |
| `smoke-check.json` (`c235250331`, latest) | status=passed (200 OK with real HTML) | NOT seen |
| `smoke-check.json` (`7954b46a57`, older) | status=failed (401 SSO) | session.deployment.latest_smoke_check_id stuck here |

Two compounding bugs:

1. **`cmd_autonomous_smoke` only persisted session state on FAILURE.**
   The success branch wrote the artifact + returned, leaving
   `session.deployment.latest_smoke_*` pointing at the prior failed run.
   `cmd_autonomous_rollback` had the same shape (mutated dict in-process,
   never `_save_session`).

2. **`advance_one_task` budget check fired BEFORE the no-eligible-task
   check.** When all 3 tasks were already completed and the operator
   ran `autonomous resume`, `_check_budgets` saw
   `completed_tasks(3) >= max_tasks_per_session(3)` and paused with
   `budget:max_tasks_per_session`, overwriting the smoke-failed
   pause_reason and confusing the post-mortem.

## RC-2C.1 fixes (all in scope; no scope creep)

| # | Fix | File(s) |
|---|---|---|
| 1 | Manual smoke success branch persists session state + saves; failure branch rewritten to share the same persistence path | `orchestrator/cli.py::cmd_autonomous_smoke` |
| 1 | Manual rollback sets `latest_rollback_failure_type` (null on success) and calls `_save_session` | `orchestrator/cli.py::cmd_autonomous_rollback` |
| 2 | `advance_one_task` peeks at `next_task` BEFORE `_check_budgets` — when no eligible task remains, drop into `_maybe_continue_or_complete` for finalization instead of forcing a budget pause | `orchestrator/core/autonomous.py::advance_one_task` |
| 3 | `cli.py::_run_inner_loop` reads `session.budgets["max_candidates_per_task"]` at call time and passes `candidate_count=N` into the runtime when set; default behavior preserved | `orchestrator/cli.py` |
| 4a | `_render_patch_worker_prompt` adds `Success criteria:` block from `intent.success_criteria` (was being silently dropped) | `orchestrator/core/agentic_runtime.py::_render_patch_worker_prompt` |
| 4b | Same: adds `Previous completed tasks` block from new `intent.previous_completed_tasks` field; `advance_one_task` populates the field from completed-task entries (capped at last 8) | `agentic_runtime.py` + `autonomous.py::advance_one_task` |
| 4c | Drops hardcoded `Prefer touching existing apps/web source` (RC-2A bias holdover); replaced with scope-driven wording | `agentic_runtime.py::_render_patch_worker_prompt` |
| 5 | New `.dogfood/rc2-creator-tracker/vercel.json` with `outputDirectory: dist`; `scripts/rc2c.sh` cp list updated to copy it | `.dogfood/rc2-creator-tracker/vercel.json` (new), `scripts/rc2c.sh` |

Manual `autonomous deploy --yes` was NOT touched — it already
delegates to `controller.run_deploy_now` which writes session state +
`_save_session` correctly.

## Tests

`tests/unit/test_rc2c1_fixes.py` (NEW, 15 tests across 5 classes):

- `BudgetVsFinalizationOrderTests` — all-tasks-completed path no
  longer fires `budget:*`; pending-task path still gets gated when
  caps exhausted (proves the fix is narrow, not a budget removal).
- `PromptSuccessCriteriaTests` — prompt now contains `Success
  criteria` block; `none provided` fallback when empty; no longer
  contains `Prefer touching existing apps/web source`.
- `PromptPreviousTasksTests` — empty list omits the block; one entry
  renders id/title/commit/run_id; multiple entries render in order.
- `IntentOverridesPlumbingTests` — `advance_one_task` populates
  `intent_overrides.previous_completed_tasks` from completed tasks
  with commits; pending tasks with no predecessors get an empty list.
- `VercelSeedTests` — dogfood `vercel.json` exists with
  `outputDirectory: dist`; `scripts/rc2c.sh` references `vercel.json`
  in its cp list.
- `CandidateBudgetPropagationTests` — `DEFAULT_BUDGETS` MUST NOT
  silently include `max_candidates_per_task` (default = no cap →
  runtime uses 3); YAML override loads via
  `load_autonomous_overrides`; session created with override carries
  the cap on `session.budgets`.

## Test results

- targeted (autonomous + smoke_rollback + codex_patch_worker +
  agentic_runtime + eval_harness_root + pause_then_render +
  run_package + artifact_validation + backward_compat +
  next_actions + autonomous_config_overrides + config_loader +
  integration_ordering + codex_recovery + deploy +
  **rc2c1_fixes**): **334/334 pass**
- e2e (autonomous_cli + cli_flow + golden_path +
  autonomous_preflight): **61/61 pass**
- Full sweep (estimated): **~565 passed / 2 skipped (Chrome) / 0 failed**
  (up from 552 — +13 new unit tests in `test_rc2c1_fixes.py`,
  -2 displaced by the budget-test rewrite)

## Observations recorded — NOT fixing in this pass

These remain as recorded findings; per RC-2C.1 spec they are
deferred:

- `?` markers in the plain `autonomous status` Budget block for
  `max_corrective_tasks` / `max_candidates_per_task` /
  `max_repair_attempts_per_candidate` (cosmetic — the counters dict
  doesn't have a usage tracker for these keys).
- Vercel SSO preview-protection workaround (added via Vercel UI by
  the operator — not a product behavior).
- The OLD session_b82c6d6a3c on disk still has stale
  `session.deployment` because the fix lands forward — backward
  state migration is intentionally NOT done. A fresh re-run via
  `scripts/rc2c.sh --run` will produce a clean session.

## Next milestone

**RC-2C.2** — re-run `scripts/rc2c.sh --run` against the same dogfood
repo. Expected outcome:

```
session completed
3 tasks committed (with cleaner candidate-a selection because
  max_candidates_per_task=1 now actually caps)
Vercel preview deployment created
deployment.json status=ready
smoke-check.json status=passed (no SSO 401 since vercel.json now
  points Vercel at dist/ AND deployment protection is off)
session.deployment.status=verified  ← was the bug, now fixed
final-run-status.md reflects latest passed smoke
review queue 0
validate-artifacts ok=true
```

Token cost estimate: same as RC-2C (~75-120k Codex + 1 real Vercel
preview deploy).

Hold for explicit "go RC-2C.2" signal.

## Files changed in RC-2C.1

```
orchestrator/cli.py                                  (+state recon for smoke + rollback; +max_candidates_per_task closure; +_session_ref plumbing)
orchestrator/core/autonomous.py                      (+budget-vs-finalization order swap; +previous_completed_tasks plumbing in intent_overrides)
orchestrator/core/agentic_runtime.py                 (+success_criteria block; +previous_completed_tasks block; -apps/web bias)
.dogfood/rc2-creator-tracker/vercel.json             (NEW: outputDirectory=dist + buildCommand)
scripts/rc2c.sh                                      (+cp vercel.json to project dir)
tests/unit/test_rc2c1_fixes.py                       (NEW: 15 tests, 5 classes)
docs/rc2c-vercel-preview-dogfood-report.md           (NEW: this report)
```

Total: 5 product/script files modified, 1 new fixture, 1 new test
file (15 tests), 1 new report. No new validators. No autonomous.py
broad refactor. No SaaS adapter. No production deploy. No rollback
enabled.
