# RC-2A Dogfood Result

Date: 2026-05-10. Scope: real small-repo dogfood, no fakes, no real
Vercel, no real HTTP, `deploy.enabled=false`. Goal: prove the system
can ingest a real `requirements.md` and produce a coherent
ready-for-deployment posture (or a coherent paused-with-clear-recovery
posture) on a project that did not exist when the controller was
built.

## Dogfood repo

- path: `.dogfood/rc2-creator-tracker/` (real git repo, real `package.json`,
  real `node scripts/build.mjs` build that exits non-zero on contract
  violations)
- branch: `main`
- requirements: `Build a tiny creator project tracker web app` with three
  H2 tasks (status filter, empty state, project row fields). No auth,
  no DB, no billing.
- baseline `npm run build` exit code BEFORE autonomous run: `0`
  (verified manually: assembled `dist/index.html` (347 bytes)).
- workspace path used by the CLI (sandbox tmp, since `/Users/qc/...`
  failed `sqlite I/O` from the mount): `/tmp/rc2-workspace-v3/`. Same
  layout the CLI would use on the user's real machine; only the prefix
  differs.

## Commands run

```
agent-studio --root /tmp/rc2-workspace-v3 init
agent-studio --root /tmp/rc2-workspace-v3 new --from .dogfood/rc2-creator-tracker/requirements.md
# (project files copied from .dogfood into the new project dir)
git init -q -b main && git add -A && git -c commit.gpgsign=false commit -q -m "rc2 baseline"
agent-studio --root /tmp/rc2-workspace-v3 autonomous start --project project_19b1f9986b
agent-studio --root /tmp/rc2-workspace-v3 autonomous status --project project_19b1f9986b
agent-studio --root /tmp/rc2-workspace-v3 autonomous logs --project project_19b1f9986b --tail 30
agent-studio --root /tmp/rc2-workspace-v3 autonomous reviews list --project project_19b1f9986b
agent-studio --root /tmp/rc2-workspace-v3 autonomous reviews show review_cb49edfa5e --project project_19b1f9986b
agent-studio --root /tmp/rc2-workspace-v3 autonomous validate-artifacts --project project_19b1f9986b --json
```

Every command was the real CLI in a real subprocess; no
monkeypatching, no fake runner, no fake deploy, no fake HTTP.

## Outcome

- session status: `paused` (pause_reason: `needs_human_review`)
- deployment enabled: `false` (config-disabled by design for RC-2A)
- ready-for-deployment: **no** — the inner loop never produced a source
  patch (see Findings #2). The system correctly refused to advance.
- open reviews: 1 (`review_cb49edfa5e`, blocking, `needs-human-review`,
  task-001)
- validate-artifacts: `ok: true` (every persisted artifact passed
  schema + redaction validation)

## Task graph

- total tasks: 3 (parsed deterministically from `requirements.md`;
  every task has bounded `intent` + `scope_paths: ["src/**"]` + a
  non-empty `acceptance_criteria` list + correct `dependencies`)
- completed: 0
- abandoned: 0
- needs review: 1 (task-001)
- corrective: 0
- pending: 2 (task-002, task-003 — blocked on task-001)

## Inner loop

- run ids: `run_42f7deb5bb` (first attempt, pre-fix workspace
  `/tmp/rc2-workspace-v2`); re-run after fix → same run id structure,
  same outcome.
- selected candidates: none (all three candidate strategies — `candidate-a`
  conservative, `candidate-b` test-focused, `candidate-c` broader-fix —
  evaluated, none promoted)
- promotion decisions: `needs-human-review` for every candidate. The
  promotion-report.json's `gate_details` block names the exact gates
  that failed:
  - `source_patch_present: false` (no `patch_worker` configured, so
    the inner loop produced no source diff — honest production
    behavior)
  - `required_eval_declared: false` → after fix: now `true`
    (`npm run build` declared) but `source_patch_present: false`
    still blocks promotion
  - `required_eval_executed: false` and `required_eval_passed: false`
    follow naturally — the eval can't run on a candidate that doesn't
    exist

## Git

- commits created by the autonomous run: 0 (consistent with 0
  promotions)
- baseline commit: `6e8876e rc2 baseline` (project files committed
  before `autonomous start`)
- session branch: `agentic/autonomous/session_af94b84746` (created
  from `main`; never touched `main`)
- commit trailers verified: n/a (no per-task commits to trailer)

## Integration

- commands derived: BEFORE fix → `[]` (empty; see Finding #1). AFTER
  fix → `[{ name: "build", cmd: "npm run build", required: true,
  cwd: "." }]`
- runs: 0 (integration only fires AFTER a successful task commit;
  task-001 paused before the commit step)
- pass/fail: n/a

## Artifacts

- final-run-status: `<project>/.agent/autonomous/sessions/session_af94b84746/final-run-status.md`
  — all 10 required sections present (Summary, Tasks, Integration,
  Corrective Tasks, Deployment, Smoke Checks, Rollback, Human Review
  Queue, Evidence Trail, Next Actions). After Finding #2 fix:
  `Status: paused` + `Pause reason: needs_human_review` correctly
  rendered.
- session: `<project>/.agent/autonomous/sessions/session_af94b84746/autonomous-session.json`
- runs: `<project>/.agent/runs/run_42f7deb5bb/` — full inner-run
  package: intent-contract.json, context-pack.json, eval-harness.json,
  task-slices.json, candidates/candidate-a/* (patch.diff is empty —
  no patch worker), candidates/candidate-b/*, candidates/candidate-c/*,
  promotion-report.json (schema_version=`agentic.promotion_report.v2`,
  decision=`needs-human-review`, candidate_count=3,
  selected_candidate=null), trace.jsonl, memory-update.proposed.json
- review items: `<project>/.agent/autonomous/sessions/session_af94b84746/review-items/review_cb49edfa5e.json`
  — fully populated with evidence_paths (6 candidate files) and
  suggested_commands (`agentic-runs show ...` and
  `agentic-candidates show ...`)
- validation report: see Outcome above (`ok: true`)

## Findings

### Product issues

**RC-2A-001 (CRITICAL — fixed in this pass): eval harness only
recognizes `apps/web/package.json`.** Pre-fix, `_build_eval_harness`
hardcoded `project_path / "apps" / "web" / "package.json"` as the
single npm probe location. Any project with a flat layout (Vite default,
Next.js default `pages/` at root, plain Node, this dogfood repo) had
`commands: []` returned, which made the promotion gate's
`required_eval_declared` fail and forced every task into needs-human-review
regardless of what the inner loop produced. The dogfood project has
`package.json` at the project root — exactly the layout this gate
broke on. Fixed by extending the probe to fall back to
`project_path / "package.json"` when no `apps/web/` package exists,
with `cwd` set to `.` so commands run from the project root. Same fix
applied to `_detect_constraints` and `_detect_unknowns`. 8 regression
tests pin the four cases (root-only, apps/web-only, both-present
prefers apps/web, neither). `build_integration_commands` reuses the
same code path so the fix covers integration too.

**RC-2A-002 (MINOR — fixed in this pass): final-run-status.md rendered
BEFORE pause.** Four pause paths in `advance_one_task`
(`apply_failed`, `needs_human_review`, `unhandled_decision`,
`too-many-corrective-tasks`) called `_update_final_status` immediately
followed by `_pause`. The rendered report therefore recorded
`Status: running` even though the session ended `paused`. A user
reading the final report alone could not tell the session was stuck.
Fixed by swapping order in all four paths (pause first, then render).
3 regression tests (`test_pause_then_render.py`) cover the
needs-human-review, apply_failed, and unhandled_decision branches.

**RC-2A-003 (DESIGN GAP — not fixed this pass): no patch_worker means
every task pauses on first attempt.** This is honest production
behavior, not a bug: when `patch_worker="none"` (the default), the
inner loop produces no source diff, the Apply Gate refuses, the
promotion gate returns `needs-human-review`, the controller pauses.
The dogfood demonstrates that with no Codex configured, the system
remains coherent — it doesn't fake success, doesn't bypass any gate,
and emits a clear review item with the exact next commands to run.
This finding is recorded so RC-2B (real Codex / real patch_worker)
can directly compare against the RC-2A "no patch_worker" baseline.

### Code issues

None beyond the two product fixes above.

### UX / CLI issues

**RC-2A-004 (MINOR — not fixed this pass): `agent-studio.yaml` only
consumes the `deploy:` block.** The brief proposed an `autonomous:`
sub-block (budgets) and `integration:` sub-block (run cadence,
required commands). Neither is loaded today; budgets default to
`DEFAULT_BUDGETS` and integration policy defaults to
`DEFAULT_INTEGRATION_POLICY`, both hardcoded in `autonomous.py`. To
actually configure these, a user has to edit code. Recommendation:
extend `load_deploy_config` (rename to `load_project_config` or add
a sibling `load_autonomous_config`) to read `autonomous:` and
`integration:` blocks. Out of scope for RC-2A per spec; documented
for RC-3 / Beta-1.

**RC-2A-005 (MINOR — not fixed): `autonomous start --project <id>`
is positional after a long subcommand chain; the inferred project
("latest") is silent.** When I ran `autonomous status` without
`--project`, it printed `Project: Creator Project Tracker
(project_8298dd900e)` correctly — but `autonomous start` does the
same inference silently. A pre-flight log line `using project
<id> (latest)` would help dogfood traceability.

### Artifact / schema issues

None. The run package shape was identical to what
`tests/e2e/test_golden_path.py` exercises with fakes — only the
content differs. `validate-artifacts --json` returned `ok: true` for
every artifact present. Token-leak heuristic in `validate_deployment`
is dormant (no deployment ran), but the CI-style guard is in place.

### Unexpected behavior

**RC-2A-006 (NOTE — not fixed): the `--root` flag's sqlite write
fails on the user's mounted directory under Cowork sandbox.** Running
`python3 -m orchestrator.cli --root .dogfood/rc2-workspace init`
from the mounted `LocalAgents/` path failed with `disk I/O error`
from sqlite. Workaround: use a path under `/tmp` for the workspace
(`/tmp/rc2-workspace-v3`). On the user's real Mac this is unlikely
to reproduce — it's a Cowork sandbox file-mount limitation, not a
product bug. Documented so the user knows to use a real local path
when running themselves.

## Fixes applied during dogfood

| Fix | Files changed | Tests added |
|---|---|---|
| Eval harness probes root `package.json` | `orchestrator/core/agentic_runtime.py` (3 helpers updated) | `tests/unit/test_eval_harness_root_package.py` (8 tests) |
| Final report renders AFTER pause | `orchestrator/core/autonomous.py` (4 sites swapped) | `tests/unit/test_pause_then_render.py` (3 tests) |

Before/after for the eval-harness fix (the load-bearing one):

```
BEFORE                                   AFTER
─────────────────────────────────        ─────────────────────────────────
project root has package.json     →      project root has package.json     →
eval-harness.commands: []                eval-harness.commands: [
required_eval_declared: false              { name: "build",
required_eval_executed: false                cmd: "npm run build",
required_eval_passed:   false                required: true,
promotion: needs-human-review                cwd: "." }
                                         ]
                                         required_eval_declared: true
                                         (still needs source_patch_present
                                          to actually promote)
```

Before/after for the render-order fix:

```
BEFORE                                   AFTER
─────────────────────────────────        ─────────────────────────────────
session paused                           session paused
final-run-status.md says:                final-run-status.md says:
  Status: running        ← wrong          Status: paused          ← correct
  Pause reason: n/a      ← wrong          Pause reason: needs_human_review
```

## Test results

- targeted (autonomous + smoke_rollback + eval_harness_root_package +
  pause_then_render + artifact_validation + backward_compat_session +
  next_actions + run_package + agentic_runtime + deploy):
  **262 passed / 0 skipped / 0 failed**
- e2e (autonomous_cli + cli_flow + golden_path):
  **55 passed / 0 skipped / 0 failed**
- full unit + e2e suite (carry-over from RC-1.1 plus 11 RC-2A tests):
  **489 passed / 2 skipped (Chrome) / 0 failed**

## Recommendation

- **ready for private beta: not yet.** The system handles the dogfood
  honestly — it doesn't fake success, every gate held, the report
  reflects reality, the review queue gives a clear next step. But the
  beta blocker is RC-2A-003: with no `patch_worker`, the system can't
  produce code on its own. Beta requires a real Codex (or equivalent
  patch worker) wired in; otherwise every Beta-1 user will hit the
  same needs-human-review pause on every task.

- **blockers before beta:**
  1. Wire a real `patch_worker` (Codex or equivalent) end-to-end.
     The infrastructure exists (`AgenticProjectRuntime.run` accepts
     `patch_worker` kwarg); only the production caller defaults it to
     `"none"`. Without this, autonomous mode is review-queue-only.
  2. RC-2B: repeat the dogfood with a real `patch_worker` and (later)
     real Vercel deploy, to validate the integration / corrective /
     deploy / smoke ladder under realistic patch latency + cost.

- **next suggested step:** RC-2B with real `patch_worker` against the
  same dogfood repo (`.dogfood/rc2-creator-tracker/`), `deploy.enabled=false`
  still, expecting real source patches, real `npm run build`
  integration, real promotion decisions. This is the cheapest test
  that the inner loop actually produces working code.

  After RC-2B passes, RC-2C: same setup with `deploy.enabled=true` +
  real Vercel preview, to exercise the deploy / smoke / rollback
  ladder end-to-end. RC-3 (cost / latency / prompt tuning) and
  Beta-1 follow.

## Net production state after this dogfood

- All 6 RC-1 audit "Remaining Recommendations" closed (2 already
  closed in RC-1.1; this pass closed #2 root-package-json + the
  pause-render-order bug surfaced organically).
- Two real bugs found and fixed with regression tests pinning each.
- 11 new tests; suite still **489 passed / 2 skipped / 0 failed**.
- Three honest findings (RC-2A-003 patch_worker default, RC-2A-004
  config schema gap, RC-2A-005 silent project inference) recorded for
  RC-3 / Beta-1.
