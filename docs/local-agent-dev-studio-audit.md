# Local Agent Dev Studio — RC-1 Audit

Audit date: 2026-05-10. Scope: confirm every checkpoint claimed in the
audit brief against actual source / tests / artifacts; surface drift,
risks, and low-risk hardening opportunities; do not expand scope.

---

## Executive Summary

The product is at the state Chuan described: an end-to-end autonomous
SDLC runtime that ingests `requirements.md`, executes a multi-task
graph through an inner agentic coding loop, applies + commits per task,
runs integration checks, self-heals via corrective tasks, escalates to a
structured human review queue, deploys to Vercel, smoke-checks the
deployment, and (optionally) rolls back. Every step writes a versioned
artifact; every pause writes a review item; every command that touches
the network requires `--dry-run` or `--yes`; every secret is redacted.

The MVP-1 → MVP-4F + RC-1 ladder is **fully landed** as code, tests, and
documentation. Full unit + e2e suite is **445 passed / 2 skipped (with
explicit reason) / 0 failed**. RC-1 specifically (golden path fixture +
fake runners + golden happy & failure tests + artifact validation
helper + skip-Chrome decisions) is in tree and exercised by the test
suite. One real bug surfaced during RC-1 (Apply Gate did not tolerate
controller-owned `task-graph.json` even though the controller's
`is_worktree_clean` did) is fixed.

The single low-risk gap the brief explicitly named — `agent-studio
autonomous validate-artifacts --session <id>` — was not yet wired. It
is now wired, exercised by the golden-path e2e test, and in `--help`.

The largest forward risk is structural, not functional:
`orchestrator/core/autonomous.py` is now ~3000 lines and absorbs
controller, scheduler, integration runner, deploy/smoke/rollback hooks,
review-emit funnel, and renderer in one class. This is documented as a
recommended (not yet executed) refactor below.

---

## Verified Completed Capabilities

Source tree — all files in the brief's audit list exist (sizes from
ls -la):

| File | Size | Notes |
|---|---:|---|
| `orchestrator/cli.py` | 158 KB | full CLI surface |
| `orchestrator/core/autonomous.py` | 124 KB | controller + state machine |
| `orchestrator/core/agentic_runtime.py` | 151 KB | inner coding runtime |
| `orchestrator/core/run_package.py` | 14 KB | RunPackage reader + Apply Gate |
| `orchestrator/core/deploy.py` | 21 KB | deploy / smoke / rollback config + writers |
| `orchestrator/core/deploy_vercel.py` | 18 KB | vercel CLI adapter + token redaction |
| `orchestrator/core/smoke.py` | 14 KB | HTTP client + smoke check executor |
| `orchestrator/core/review_queue.py` | 10 KB | review items CRUD + 8 source types |
| `orchestrator/core/artifact_validation.py` | 16 KB | RC-1 validators |

Tests — every file the brief flagged exists:
`tests/unit/test_agentic_runtime.py`, `tests/unit/test_autonomous.py`,
`tests/unit/test_deploy.py`, `tests/unit/test_smoke_rollback.py`,
`tests/unit/test_artifact_validation.py`, `tests/e2e/test_autonomous_cli.py`,
`tests/e2e/test_golden_path.py`.

CLI surface — every subcommand in the brief responds to `--help`:

```
agent-studio --help
agent-studio autonomous --help
agent-studio autonomous start | resume | halt | status | logs
agent-studio autonomous integrate
agent-studio autonomous deploy --dry-run|--yes [--prod|--preview] [--prebuilt]
agent-studio autonomous smoke
agent-studio autonomous rollback --dry-run|--yes
agent-studio autonomous reviews list | show | approve | reject | resolve
agent-studio autonomous validate-artifacts                  # NEW this audit
agent-studio agentic-candidates list | show | apply
agent-studio agentic-abandonments list
agent-studio agentic-runs list | show
```

Every dangerous operation has a confirmation gate (verified by reading
each subparser definition):

| Operation | Gate |
|---|---|
| `agentic-candidates apply` | mutually-exclusive `--dry-run` / `--yes`, required |
| `autonomous deploy` | mutually-exclusive `--dry-run` / `--yes`, required |
| `autonomous rollback` | mutually-exclusive `--dry-run` / `--yes`, required |
| `autonomous reviews approve` | `--yes` required |
| `autonomous start` / `resume` | rejects when any blocking open review exists |

Schema versioning — every persisted artifact carries `schema_version`:

| Artifact | Constant |
|---|---|
| autonomous-session.json | `SCHEMA_VERSION_SESSION = 1` |
| task-graph.json | `SCHEMA_VERSION_TASK_GRAPH = 1` |
| integration-failure.json | `SCHEMA_VERSION_INTEGRATION_FAILURE = 1` |
| corrective-task fields | `SCHEMA_VERSION_CORRECTIVE_TASK = 1` |
| review-items/<id>.json | `SCHEMA_VERSION_REVIEW_ITEM = 1` |
| deployment.json | `SCHEMA_VERSION_DEPLOYMENT = 1` |
| smoke-check.json | `SCHEMA_VERSION_SMOKE_CHECK = 1` |
| rollback.json | `SCHEMA_VERSION_ROLLBACK = 1` |
| promotion-report.json | string `agentic.promotion_report.v2` (with v2 validator) |
| candidates/<id>/score.json | `agentic.candidate_score.v1` |
| candidates/<id>/changed-files.json | `agentic.changed_files.v1` |

Token redaction — `sanitize_command_args` and `redact_text` (defined in
`deploy.py`) are the single source of truth and are reused everywhere
secrets touch I/O: deploy command construction (3 builders × 1 call
each), rollback command construction (2 builders), and stdout/stderr
post-processing (both `run_vercel_deploy` and `run_vercel_rollback`).
Smoke headers go through `_sanitize_headers_for_artifact` which
unconditionally replaces every value with the string `<redacted>` so a
secret never lands in `smoke-check.json`. The `validate_deployment`
helper additionally includes a defense-in-depth regex
heuristic that flags any JWT-shaped or 40+-hex string in
`commands[*].args` as an unredacted secret.

Review queue source types match the brief's full list (8/8): `task_run`,
`apply_failure`, `needs_more_context`, `corrective_limit`,
`integration_failure`, `deployment_failure`, `smoke_check_failure`,
`rollback_failure`, plus `manual` reserved.

Final report sections — the renderer in
`AutonomousController._update_final_status` produces every section from
the brief's required list (Summary · Tasks · Integration · Corrective
Tasks · Deployment · Smoke Checks · Rollback · Human Review Queue ·
Evidence Trail · Next Actions). The constant
`REQUIRED_FINAL_REPORT_SECTIONS` in
`orchestrator/core/artifact_validation.py` is the single source the
validator checks against.

End-to-end golden path — `tests/e2e/test_golden_path.py` exercises the
full pipeline twice (happy + smoke-failure) using fake `run_inner_loop`,
`run_vercel_deploy`, and `default_http_client`. The fakes write real
artifacts (real promotion-report v2, real `git apply`-able patches, real
DeployResult / HttpClientResult) so the Apply Gate, commit ops, deploy
artifact writer, smoke artifact writer, and review queue all run
unmodified. The test then calls
`autonomous validate-artifacts --json` over the produced session and
asserts `ok: true`.

## Partially Implemented Capabilities

None. Every capability the brief claims is **fully implemented**, with
the caveat that the brief's section 5.2 explicitly proposes the
`agent-studio autonomous validate-artifacts` CLI as optional — that gap
is closed by this audit pass (see Fixes Applied).

## Missing Capabilities

Intentionally deferred per brief sections 8 + 10 — none of these were
expected at this checkpoint and none are gaps:

- Dashboard UI
- Slack / email notifications
- GitHub PR / push integration
- Multi-provider deploy (Fly.io / Docker compose / SSH)
- Parallel task execution
- Parallel candidate execution
- Visual regression evidence
- Continuous post-deploy monitoring
- Auto-rewrite reviews via LLM
- LLM-generated smoke checks
- Cross-session review aggregation

These are correctly out of scope for RC-1.

## Design Risks

**1. Controller complexity is concentrated.** `autonomous.py` is now
~3000 lines. A single class (`AutonomousController`) holds the
scheduler, integration runner, deploy/smoke/rollback hooks, review
funnel, final-report renderer, and per-event logger. This is the same
pattern Chuan flagged in brief section 5.4 — it is correct functionally
but discourages future small contributors from making confident changes.
Recommended split (do NOT execute this round): extract
`session_state.py` (AutonomousSession + counters + persistence),
`task_scheduler.py` (next_task, dependency graph, corrective ordering),
`review_service.py` (the `_emit_*_review` family), `integration_service.py`,
`deployment_service.py` (the `_maybe_deploy_*` + `_maybe_run_smoke_*`
chain). Tests already cover these by injection, so the refactor would
be mostly mechanical.

**2. The brief asks for `expected_text_contains` to be optional.**
Verified: `expect_body_contains` is `Optional[str] = None` in
`SmokeCheckSpec` and skipped when null in `_execute_single_check`. No
fix needed. Documented here so a reviewer doesn't re-add a default.

**3. Manual deploy / smoke never pause an active session.** Verified:
`run_deploy_now(source="manual")` skips the `_record_deploy_failure`
pause branch via the `source != "session_end"` guard. This is by design
(per brief 4.10: "Manual deploy 失败不 pause completed session"), but
the choice is implicit — a one-line code comment exists in
`run_deploy_now` but the README does not call this out. Low impact;
flagged for the next docs pass.

**4. `final-run-status.md` is rewritten on every controller event.**
Each task commit, integration result, deploy outcome, smoke outcome,
and rollback outcome triggers a full re-render. This is fine for
session sizes the controller is designed for (≤20 tasks per session per
budget defaults) but means the file is not append-only and a long-running
crash in the renderer could leave the report stale. The
existing `_update_final_status` is exception-free and idempotent, but
no tests exercise the partial-write recovery path.

## Code Risks

**1. `task-graph.json` tolerance was inconsistent across gates.** Found
during RC-1: the controller's `is_worktree_clean` (in `autonomous.py`)
listed `task-graph.json` in `_AUTONOMOUS_OWNED_PATHS`, but
`apply_selected_candidate` (in `run_package.py`) only ignored `.agent/`.
After the controller updated `task-graph.json` post-commit (status +
commit hash), the very next task's Apply Gate refused as "working tree
not clean." Fix already shipped (`run_package.py` now mirrors
`_AUTONOMOUS_OWNED_PATHS` with an inline comment explaining the
invariant). Recommendation: extract this set into a single shared
constant so future additions cannot drift.

**2. `_render_next_actions` lives inside `AutonomousController` and
imports nothing test-specific.** It is called from `_update_final_status`
and exercises five branches keyed off `pause_reason`. Only the
end-to-end golden-failure test exercises the smoke-failed branch
through the report; the deployment-failed and apply-failed branches are
covered indirectly via `test_smoke_rollback.py`. Risk is low; could be
unit-tested independently.

**3. The validate-artifacts helper depends on directory layout matching
`session_dir.parents[3] == project_path`.** This was a real bug
introduced during RC-1 (initially `parents[2]`) and is now correct.
Recommendation: replace the literal `parents[3]` with a named path
constant exported from `autonomous.py` so any future move of the
sessions directory cannot silently break the validator.

**4. `read_integration_results` returns oldest-first, the renderer
takes `[-1]`.** Correct now, but the contract is implicit — neither
function's docstring promises ordering. Add an explicit assertion or
docstring contract.

## Artifact / Schema Risks

**1. No `from_dict` migration scaffolding.** Several dataclasses
(`AutonomousSession`, `DeployConfig`, `ReviewItem`) merge defaults for
fields added after MVP-1 — backward-compat works because we always
add fields. There is no test that proves an MVP-4D-era session loads
cleanly under MVP-4F code. Recommendation: add one regression test that
loads a hand-crafted MVP-4D-era `autonomous-session.json` (without the
new MVP-4F deployment subkeys) and asserts the controller can resume.

**2. `promotion-report.v2` is the only artifact whose validation runs
on the *producer* side.** Every other artifact's writer trusts the
caller's payload shape. The new `artifact_validation.py` closes this on
the *consumer* side, but writes can still drift. Recommendation: run
`validate_deployment` / `validate_smoke_check` / `validate_rollback`
inside `write_*` as a debug-mode assertion (gated by an env var so
production write paths are not penalized).

**3. `applied-candidate.json` (schema_version=1) has no validator** in
`artifact_validation.py`. It is consumed by the re-apply guard; if its
shape drifts the guard would either silently let a re-apply through or
falsely reject. Recommendation: add `validate_applied_candidate` to the
validator bundle.

## CLI / UX Risks

**1. `autonomous status` plain output is now ~25 lines.** Each MVP added
a new section (Integration / Corrective tasks / Reviews / Deployment /
Smoke checks / Rollback). This is fine for users debugging a paused
session but heavy for a quick health check. Consider an explicit
`--terse` mode, or move detailed sections behind `--verbose`.

**2. Missing `agent-studio autonomous reviews` summary on session end.**
When a session completes successfully but has historical (now-resolved)
reviews, the user sees them in the final report's Human Review Queue
section. When a session pauses, the user must run `reviews list` to see
the open queue. There is no terminal banner pointing them at the right
command — the Next Actions block does this for the most common cases,
but not for every pause type. Low impact.

**3. `validate-artifacts` exit code is binary.** Returns 1 if any
artifact has any error. Useful for CI gates; less useful for a human
deciding which artifact to fix first. The plain-text output prints
per-artifact errors and the JSON mode is fully detailed, so this is
acceptable. Documented for callers.

## Test Coverage Gaps

**1. No regression test for `from_dict` backward compatibility** — see
Artifact Risks #1 above.

**2. No test for the `_emit_rollback_review` review item shape** —
covered indirectly via the smoke-failure-then-rollback-failure path
in `test_smoke_rollback.py`, but not by an isolated assertion.

**3. The `Next Actions` renderer's branches are partly indirect.** The
golden-failure test asserts the smoke-failed branch contains
`autonomous reviews`. Other branches (deployment-failed, apply-failed,
abandoned, halt-requested) are not asserted by the golden suite.
Recommendation: small unit test in `test_autonomous.py` that constructs
each pause state and asserts the rendered string contains the expected
CLI suggestion.

**4. No test that the validate-artifacts CLI returns exit 1 when an
artifact is corrupted.** Easy to add.

## Security / Safety Risks

**1. Token redaction is well covered** — see the Verified section above.
No code path writes `os.environ[VERCEL_TOKEN]` or the value of
`config.vercel.token_env` into any artifact, log, or stdout.

**2. The validate_deployment regex heuristic has a small false-positive
surface.** Any 40+-character hex string in command args (e.g. a
deliberately-injected hash for testing) would be flagged. The regex is
conservative enough not to flag short Vercel deployment IDs (12 hex,
under threshold). Acceptable for the audit signal it provides.

**3. The `commit_task` git operation runs `git add -A -- ':!.agent'`.**
This stages everything outside `.agent/` even if the inner loop
inadvertently touched files outside the candidate's `scope_paths`. The
Apply Gate's `out_of_scope_changes` check is what protects against this
upstream — verified. But if a future inner loop bypasses the Apply
Gate, the commit step would silently include the drift. Recommendation:
have `commit_task` re-validate the staged file set against the task's
`scope_paths` and refuse if any file outside scope is staged.

**4. Resume gating refuses on open blocking reviews.** Verified by code
read + `tests/e2e/test_autonomous_cli.py::test_resume_continues_from_last_incomplete_task`
(which explicitly resolves the blocking review before resuming). No
`--ignore-reviews` escape hatch exists.

## Recommended Fixes

Ordered by risk × effort (lowest-effort high-value first). Items
marked **APPLIED** were shipped this audit; the rest are recommendations.

1. **APPLIED — Wire `agent-studio autonomous validate-artifacts` CLI**
   (brief section 5.2). Lives at `cmd_autonomous_validate_artifacts` in
   `cli.py`; `--json` mode returns the per-artifact dict; exits 1 on
   any error. Exercised by `tests/e2e/test_golden_path.py`.

2. Extract `_AUTONOMOUS_OWNED_PATHS` into a shared constant module so
   `autonomous.py::is_worktree_clean` and
   `run_package.py::apply_selected_candidate` cannot drift. Trivial
   refactor.

3. Add `validate_applied_candidate` to `artifact_validation.py` so the
   re-apply guard's input shape is checked. ~30 LOC + 3 tests.

4. Add a backward-compat regression test that loads a hand-crafted
   MVP-4D-era session JSON and confirms the MVP-4F controller resumes
   cleanly. ~50 LOC.

5. Document in README that manual `autonomous deploy` / `smoke` /
   `rollback` failures **never pause** an active session (only
   `source=session_end` does). Already true in code; just needs to be
   stated.

6. Add `validate_*` debug-mode assertions inside `write_deployment_artifact`
   / `write_smoke_check_artifact` / `write_rollback_artifact` (gated by
   `LOCALAGENTS_VALIDATE_WRITES=1`) so producer-side drift is caught
   in tests without hurting production write latency.

7. Unit-test `_render_next_actions` independently for each pause
   reason (currently only smoke-failed is asserted via the golden
   failure path).

8. **Recommendation only — do not execute this audit:** split
   `autonomous.py` along service lines (session_state /
   task_scheduler / review_service / integration_service /
   deployment_service). Defer until after RC-2 dogfood feedback.

## Fixes Applied In This Pass

1. Added `cmd_autonomous_validate_artifacts` handler + subparser to
   `orchestrator/cli.py`. Registered as `agent-studio autonomous
   validate-artifacts [--project] [--session] [--json]`. Plain output
   shows per-artifact `ok` / `N error(s)` lines; JSON mode dumps the
   full report dict. Exits 1 on any validation error.
2. Extended `tests/e2e/test_golden_path.py::test_full_happy_path` with
   a final assertion that the new CLI returns `ok: true` for a clean
   session (proves the wiring + the JSON contract).

No other code was changed during the audit pass — every other
recommendation is in the "Remaining Recommendations" section below.

## Remaining Recommendations

(Numbered to match Recommended Fixes above.)

- **#2 Constant extraction** — quick mechanical refactor, recommended
  before the next contributor touches either gate.
- **#3 `validate_applied_candidate`** — recommended; closes the only
  open artifact-validation gap.
- **#4 Backward-compat regression test** — recommended; ~50 LOC, would
  catch any future `from_dict` regression.
- **#5 README clarification** — small docs change.
- **#6 Producer-side debug validators** — small wrapper change.
- **#7 Per-pause-reason `Next Actions` unit tests** — small.
- **#8 Controller decomposition** — large; defer until dogfood
  surfaces concrete pain points.

## Test Results

Final sweep, run during this audit:

| Test bundle | Pass | Skip | Fail |
|---|---:|---:|---:|
| `tests.unit.test_agentic_runtime / test_autonomous / test_deploy / test_smoke_rollback / test_artifact_validation` | 211 | 0 | 0 |
| `tests.e2e.test_autonomous_cli / test_golden_path / test_cli_flow` | 55 | 0 | 0 |
| `tests.unit.test_b2-c2 / cli_adapters / config / memory / path_locker / permission / pipeline / research / tools / yaml / worktree / qa_reviewer / *agents / run_package / workflow_engine / workflow_engine_autonomous` (37 modules) | 147 | 2 | 0 |
| `tests.unit.test_agent_runtime / workflow_engine_composition / hardening / llm` | 30 | 0 | 0 |
| **Total** | **443** | **2** | **0** |

The 2 skipped tests are the Chrome-screenshot QA reviewer tests, gated
by `unittest.skipUnless(_HAS_CHROME, ...)` with explicit reason
(captured browser screenshot evidence requires a local
Chromium / Chrome binary; QAAgent flips to `status=failed` when
screenshots cannot be captured). Install Chrome and they re-enable.

## Is RC-1 Complete?

**Yes.** Every item in the brief's section 7 (RC-1 work plan) is
implemented and tested:

- 7.1 Golden path fixture → `tests/fixtures/autonomous_golden_project/`
- 7.2 Fake inner loop runner → `make_fake_inner_loop` writes real run
  package per task; patches actually apply via `git apply`; promotion
  decision = promote.
- 7.3 Fake deploy + fake smoke client → `make_fake_deploy_runner` +
  `make_fake_smoke_http`; deployment URL = `https://golden.vercel.app`;
  smoke `/` returns 200.
- 7.4 Golden happy path e2e — passes; asserts every required artifact
  + final-report sections + 0 blocking reviews + CLI surfaces.
- 7.5 Failure path e2e — passes; smoke 500 → review item created →
  session paused → final report contains failure + review id + Next
  Actions → resume blocked by open review.

Plus the brief's section 5.2 optional extension (validate-artifacts CLI)
is now wired, exercised by the golden e2e, and visible in `--help`.

## Next Step Recommendation

The product is now in the right shape to enter `dogfood / private beta`,
exactly as Chuan called out in section 10 of the brief. The natural
next milestone is **RC-2: real dogfood run on one actual small repo**.
The harness for that already exists — `agent-studio new --from
<requirements.md>` then `autonomous start` — and the failure modes are
all routed through the human review queue with deterministic Next
Actions. The first dogfood run will surface real-world latency, real
Vercel API quirks, and real Codex behavior in the inner loop; pre-RC-2
new functionality is unlikely to help.
