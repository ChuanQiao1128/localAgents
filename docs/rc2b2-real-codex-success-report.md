# RC-2B.2 Result — Real Codex Patch Worker, 3 Tasks

Date: 2026-05-10. Goal: in `.dogfood/rc2-creator-tracker/`, run real
Codex patch worker against ALL 3 dogfood tasks (RC-2B.1 had verified
just task-001). Same repo, same `agent-studio.yaml` shape, just
`max_tasks_per_session=3` and `max_total_inner_runs=3`. Run executed
on Chuan's real Mac via `scripts/rc2b2.sh --run`.

**Outcome: Result A — RC-2B.2 verified.** All three tasks completed
end-to-end with real Codex generating real source patches, the
Promotion Gate accepting each, the Apply Gate committing each on the
session branch, integration passing both periodically and at session
end, and `validate-artifacts` returning `ok=true`. Zero open review
items.

This is the milestone the project has been building toward since
MVP-1: every layer of the autonomous SDLC ladder has now been
exercised end-to-end with a real LLM-backed patch worker. The
architecture is no longer just an auditable controller — it is a
working local AI coding studio.

## Environment

- workspace: `/tmp/rc2b2-real`
- project id: `project_75a4aadf8c`
- session id: `session_ac4ae81746`
- Codex version: `codex-cli 0.130.0`
- Codex binary: `/opt/homebrew/bin/codex`
- patch_worker: `codex` (sandbox=`workspace-write`,
  ask_for_approval=`on-request`)
- deploy.enabled: `false` (preserved — RC-2C is a separate milestone)
- runner: `scripts/rc2b2.sh --run`

## Outcome

- session status: `completed`
- tasks completed: **3 / 3**
- commits: **3** (each on session branch
  `agentic/autonomous/session_ac4ae81746`)
- review queue: **0 open**
- validate-artifacts: `ok: true`
- ready-for-deployment: **yes** (deploy disabled by config — RC-2C is
  the next milestone to actually ship anything)

## Per-task results

| Task | Run id | Selected candidate | Commit | Result |
|---|---|---|---|---|
| task-001 — Add a status filter UI | `run_c4f5cfa2a5` | `candidate-b` (test-focused) | `8b020c0` | promote → apply → commit |
| task-002 — Add an empty state for zero projects | `run_3907450635` | `candidate-a` (conservative) | `0d49290` | promote → apply → commit |
| task-003 — Show project name, status, and due date in each row | `run_db87a2a5ce` | `candidate-a` (conservative) | `ba67705` | promote → apply → commit |

Per-task acceptance: every task's promotion-report.json had
`decision=promote`, every Apply Gate ran without raising, every
commit_task call produced a real git commit on the session branch,
every commit carried the full evidence trailer set
(`Agent-Task-ID / Agent-Run-ID / Selected-Candidate /
Candidate-Strategy / Promotion-Decision / Promotion-Report`).

## Integration

- **periodic**: passed (triggered after the per-N completed-tasks
  threshold during the session)
- **session_end**: passed (triggered before `_complete`, with all 3
  tasks committed)

Both runs of `npm run build` exited 0 against the cumulative working
tree, which is the load-bearing signal that Codex's patches actually
compose — not just that each patch passes its own per-task eval, but
that the three together still build coherently.

## Validate-artifacts

`agent-studio --root /tmp/rc2b2-real autonomous validate-artifacts
--json` returned:

```
ok: true
report:
  autonomous-session.json: []
  final-run-status.md: []
  task-graph.json: []
  .agent/runs/run_c4f5cfa2a5/applied-candidate.json: []
  .agent/runs/run_3907450635/applied-candidate.json: []
  .agent/runs/run_db87a2a5ce/applied-candidate.json: []
```

(Plus per-task changed-files / score / promotion-report walkthrough,
all `[]` = no errors.)

## Observations (recorded — NOT fixing now)

These are real surface-area gaps surfaced by the run. Per RC dogfood
discipline, they are recorded here for later inspection and are
explicitly NOT being fixed in this pass — RC-2B.2 succeeded, drift is
not justified.

### Observation A: task-001 selected `candidate-b` even though YAML had `max_candidates_per_task=1`

The `agent-studio.yaml` written by `scripts/rc2b2.sh` includes:

```yaml
autonomous:
  budgets:
    max_candidates_per_task: 1
    max_repair_attempts_per_candidate: 1
```

But task-001's selected candidate was `candidate-b` (test-focused
strategy) — meaning at least 2 candidates (a + b) actually ran for
that task; otherwise the scorer wouldn't have had `candidate-b` to
pick. (For task-002 and task-003 the conservative `candidate-a` was
picked, which is consistent with either "1 candidate" or "3 candidates
where conservative scored highest".)

Most likely cause: the `autonomous.budgets.max_candidates_per_task`
key is not actually wired into `AgenticProjectRuntime.run`'s
`candidate_count` argument. The candidate count is controlled
elsewhere (CLI flag `--agentic-candidate-count` or env
`LOCALAGENTS_AGENTIC_CANDIDATE_COUNT`, defaulting to 3 per
`CANDIDATE_STRATEGIES`). `RC-2C.x` config-block work added the
`AutonomousOverrides` loader but only wired `budgets` into
`session.budgets` for the controller's own loop — it does not
propagate down into the inner runtime's candidate_count.

This is **a real wiring gap, not a behavioral bug** — the dogfood
still succeeded because the scorer correctly picked the best
candidate. But operators who set `max_candidates_per_task=1` to
control token spend would in fact see ~3× the spend they expected.

### Observation B: `autonomous status` shows `?` for several budget counters

The `Budget:` block in plain `autonomous status` output shows:

```
max_corrective_tasks: ? / 1 *
max_candidates_per_task: ? / 1 *
max_repair_attempts_per_candidate: ? / 1 *
```

The `?` means there's no usage counter for these in
`AutonomousSession.counters` — only the limit side is displayed.
For `max_corrective_tasks` there IS a counter
(`corrective_tasks_created`) but the renderer's `used_key` lookup
table doesn't map it. The other two have no counter at all (related
to Observation A — they aren't actually consumed).

Cosmetic; not blocking; recorded for the next status-renderer
touchup if/when that becomes worth doing.

## Conclusion

**RC-2B.2 verified. The private-beta blocker — "real patch_worker
missing" — is now removed.**

The project state lock advances to:

```
RC-2A:                             completed
RC-2B (Codex adapter):             completed
RC-2B hardening (.8 → .E):         completed
RC-2B.1 sandbox env probe:         completed
RC-2B.1 real Codex (1 task):       completed (commit 787c428)
RC-2B.2 real Codex (3 tasks):      completed (commits 8b020c0, 0d49290, ba67705)
Next milestone:                    RC-2C real Vercel preview dogfood
```

## Next milestone

**RC-2C — real Vercel preview deploy on the same dogfood repo.** Same
3 tasks already completed by RC-2B.2 (so no new Codex spend for the
patch generation), `deploy.enabled=true`, `environment=preview`,
production rollback OFF. Goal: validate the
`vercel deploy → smoke check → final report` ladder against a real
preview URL.

Success criteria for RC-2C:

```
session completed
deployment.json present (status=ready)
smoke-check.json present (status=passed)
final-run-status.md includes deployed URL
validate-artifacts ok=true
0 open reviews
```

Failure modes to expect (per prior MVP-4E/F design):

- env/auth: `VERCEL_TOKEN` / `VERCEL_ORG_ID` / `VERCEL_PROJECT_ID`
  missing → `vercel_auth_missing` failure type, review item, no fake
  success
- vercel CLI missing → `vercel_cli_missing` failure type
- deploy succeeds but smoke fails → `smoke-check-failed` review item;
  rollback NOT triggered (preview env)

These are all already wired and tested with fake runners; RC-2C is
the first time they'll be exercised against the real Vercel CLI.

## Net production state after RC-2B.2

- Initial git commit: `a6d9f2c` (full LocalAgents history)
- Dogfood-repo commits (in `/tmp/rc2b2-real/.agent-studio/projects/...`,
  not in LocalAgents): `8b020c0`, `0d49290`, `ba67705`
- Test suite: 552 passed / 2 skipped / 0 failed (unchanged — RC-2B.2
  didn't add tests; it consumed the existing system)
- Codex tokens consumed this run: ≈ 3× RC-2B.1 spend (estimated
  75-120k based on RC-2B.1's 25-40k baseline; verify in your
  `codex /status`)
- No product code changed in this RC-2B.2 pass.
