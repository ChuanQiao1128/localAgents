# RC Status — Local Agent Dev Studio

A 5-minute scan of where this project is.

## State lock (2026-05-10)

```
RC-2A:                              completed
RC-2B (Codex adapter wired):        completed
RC-2B hardening (.8 → .13):         completed
RC-2C (autonomous + integration cfg): completed
RC-2D (defensive config + UX):      completed
RC-2E (contract pinning):           completed
RC-2B.1 sandbox env probe:          completed (real codex argv bug found + fixed)
RC-2B.1 real Codex dogfood:         SUCCEEDED on real Mac (Result A)
                                      → commit 787c428 in dogfood repo
RC-2B.2 (3 tasks, real Codex):      not yet run
```

## Test suite

**552 passed / 2 skipped (Chrome-screenshot-dependent) / 0 failed.**

Layout: `tests/unit/test_*.py` (~317 tests across ~25 modules) +
`tests/e2e/` (61 tests across 4 modules). All run without real Codex
or real Vercel — the test suite is deterministic and uses fake
runners.

## Initial git commit

`a6d9f2c` — "Initial commit: Local Agent Dev Studio (MVP-1 → RC-2B.1
verified)" — 192 files / 56,223 insertions. Full project history
captured. `.dogfood/rc2-creator-tracker` was committed as a submodule
pointer (mode 160000); local works fine, but if pushing to a remote
later, inline it with `git rm --cached .dogfood/rc2-creator-tracker
&& rm -rf .dogfood/rc2-creator-tracker/.git && git add .dogfood/...
&& commit`.

## What works end-to-end (proven 2026-05-10 with real Codex)

```
requirements.md
  → deterministic PRD parser
  → task-graph.json (3 bounded tasks for the dogfood repo)
  → AutonomousController.start_or_resume
  → AgenticProjectRuntime.run with patch_worker=codex
  → real codex exec (codex-cli 0.130.0, ~5m37s for task-001)
  → candidate worktree under .agent/worktrees/<run>/candidate-a/
  → real source patch (6 lines added to src/index.html)
  → _diff_directories computes patch.diff + changed-files.json
  → Promotion Gate accepts (decision=promote)
  → apply_selected_candidate runs Apply Gate (11 hard rules)
  → git apply + commit on session branch (commit 787c428)
  → final-run-status.md updated
  → validate-artifacts ok=true
  → 0 blocking review items
```

Evidence: [`docs/rc2b-real-codex-dogfood-report.md`](rc2b-real-codex-dogfood-report.md).

## Current blocker (resolved)

Was: Codex CLI not installed in Cowork sandbox + no auth + no
network access to `api.openai.com`.

Now: Codex installed + authenticated on Chuan's Mac
(`/opt/homebrew/bin/codex`, codex-cli 0.130.0). Read-only sanity
check passed. The Cowork sandbox itself still cannot reach
`api.openai.com` (HTTP 403 from sandbox proxy) — that's expected and
unrelated; all Codex work happens on the Mac.

## Next milestone

**RC-2B.2** — let all 3 dogfood tasks complete on the same repo.
Same `.dogfood/rc2-creator-tracker/`, same `agent-studio.yaml`
shape, just bumps `max_tasks_per_session` and
`max_total_inner_runs` to 3. Estimated cost: 3× RC-2B.1 ≈
75-120k tokens, ~15-20 min wall-clock.

Run via `scripts/rc2b2.sh` (added in this pass — see Operations below).

### Three-bucket triage for RC-2B.2 results

| Bucket | What you see | Next action |
|---|---|---|
| **A success** | 3 tasks completed + 3 commits + `validate-artifacts ok=true` + 0 open reviews | RC-2C (real Vercel preview) |
| **B partial** | Codex generates patches but build / promotion / repair fails on some task | Inspect review evidence; fix only `prompt / context-pack / repair-loop / eval-wiring / changed-files classification` per spec |
| **C env** | Auth / rate limit / network failure | No product code change; fix env, re-run `scripts/rc2b2.sh` |

## Out of scope (do NOT add)

- Dashboard
- Slack / email notifications
- GitHub PR integration
- Real Vercel / smoke / rollback (until RC-2C is explicitly opened)
- MCP / A2A
- Parallel tasks
- Parallel candidates
- `autonomous.py` decomposition refactor
- More validators / status fields / README polish — the audit's
  Remaining Recommendations are all closed
- New product features without a real RC-2B.2 failure surfacing the need

## Key evidence paths

| What | Path |
|---|---|
| RC-2B.1 success report | `docs/rc2b-real-codex-dogfood-report.md` |
| RC-2A real-repo dogfood (no Codex baseline) | `docs/rc2-dogfood-report.md` |
| RC-2B (Codex adapter wired) report | `docs/rc2b-dogfood-report.md` |
| Audit baseline | `docs/local-agent-dev-studio-audit.md` |
| Dogfood test repo | `.dogfood/rc2-creator-tracker/` |
| Dogfood agent-studio.yaml (current) | `.dogfood/rc2-creator-tracker/agent-studio.yaml` |
| RC-2B.1 actual Codex commit | `787c428` (in the dogfood project's git, on session branch `agentic/autonomous/session_00fdc84305`) |
| RC-1 golden path test | `tests/e2e/test_golden_path.py` |
| Codex patch worker tests | `tests/unit/test_codex_patch_worker.py` (26 tests) |

## Operations

### Quick status check
```bash
cd /Users/qc/Documents/LocalAgents
which codex
codex --version          # expect: codex-cli 0.130.0+
git log --oneline -1     # expect: a6d9f2c
```

### Run RC-2B.2 (real Codex on 3 tasks)
```bash
cd /Users/qc/Documents/LocalAgents

# Dry-run first — prints every command + the agent-studio.yaml
# that will be written. NO Codex tokens consumed.
./scripts/rc2b2.sh

# When ready to actually consume tokens (~75-120k):
./scripts/rc2b2.sh --run

# If codex isn't at the default /opt/homebrew/bin/codex:
CODEX_BIN=$(which codex) ./scripts/rc2b2.sh --run
```

### Inspect a session after a run
```bash
WS=/tmp/rc2b2-real
./agent-studio --root $WS autonomous status
./agent-studio --root $WS autonomous logs --tail 80
./agent-studio --root $WS autonomous reviews list
./agent-studio --root $WS autonomous validate-artifacts --json

# See what Codex wrote, per task:
PROJECT=$(ls -d $WS/.agent-studio/projects/*/ | head -1)
git -C $PROJECT log --oneline
git -C $PROJECT show <task_commit_hash>
```

## Prompt risks before RC-2B.2

A read-only review of `_render_patch_worker_prompt` +
`AutonomousController.advance_one_task::intent_overrides` (no code
changes made — per RC-2B.2 prep spec) found **3 real gaps** that
could surface when RC-2B.2 runs task-002 + task-003 (both have
`Depends: task-001`, building on top of the previous task's commit).

### Gap 1: `success_criteria` is silently dropped from the prompt

The controller builds `intent_overrides = { goal, success_criteria,
allowed_change_scope }` from each task. `_render_patch_worker_prompt`
inlines `intent.goal` and `intent.allowed_change_scope.paths` — but
**never references `intent.success_criteria`**. So the explicit
acceptance criteria the task-graph parser extracted from the
requirements never reach Codex.

For RC-2B.1's task-001, this didn't bite because the task title
("Add a status filter UI") and goal text made the bar obvious. But
task-002's success criteria are more specific: "The page contains the
text 'No projects yet' or equivalent friendly copy" + "The empty
state is hidden when at least one project exists". If Codex never
sees these, it's free to render an empty state that doesn't carry
those exact words, which would still build but might fail the
spec's intent.

**Fix shape (NOT applied):** add `Success criteria:` block to the
prompt template, listing `intent.success_criteria` as a JSON list.
~5 lines of `_render_patch_worker_prompt`.

### Gap 2: no signal that prior tasks already committed in this session

`intent_overrides` for task-002 contains the same 3 keys it does for
task-001 — there's no `previous_tasks_completed` /
`previous_commits` / `predecessor_summary` field. The patch worker
prompt similarly never mentions that `src/index.html` was just
modified by an earlier autonomous commit.

In practice this is partially mitigated because the `context_pack`
gets re-built per inner run — so the SECOND run reads `src/index.html`
in its post-task-001 state, and Codex sees the filter UI it added
last time. But if Codex over-aggressively restructures (e.g.
"refactor index.html to a cleaner layout"), it could remove the
filter and still satisfy task-002's intent — which would be
out-of-spec because task-002 `Depends: task-001`.

**Fix shape (NOT applied):** add a `Predecessor commits in this
session:` block listing recently completed tasks' titles + commit
short hashes from the task-graph. Pull from
`task_graph['tasks']` filtered by `status == 'completed'`. ~10 lines.

### Gap 3: prompt encourages "apps/web source" specifically

The prompt's last paragraph says: *"Prefer touching existing
apps/web source and tests over writing documentation."* This is a
holdover from the pre-RC-2A `apps/web/` layout assumption (which
RC-2A bug RC-2A-001 already partially fixed in the eval harness). For
the dogfood repo (which uses flat `src/` layout), this hint is
slightly misdirecting — though Codex on RC-2B.1 ignored it and did
the right thing.

**Fix shape (NOT applied):** change to "Prefer touching existing
source and tests over writing documentation." (drop `apps/web`).
1 line. Lowest-risk of the three — could ship even without a
RC-2B.2 failure.

### Risk assessment

If RC-2B.2 task-002 or task-003 fails and the failure looks like
"Codex wrote semantically wrong code that still builds" or "Codex
overwrote previous task's work", **gap #1 or #2 is likely the
cause.** Fix targeted (only the prompt template — does not require
controller / autonomous.py changes). All three fixes are in scope per
the RC-2B.1 spec ("Codex command construction bug / prompt /
context-pack").

If RC-2B.2 succeeds without intervention, none of the three are
worth touching speculatively — no drift.

---

*Last updated: 2026-05-10. If you're reading this and the timestamps
in `git log` are weeks newer, the test suite tally and milestone
markers above are stale; refer to `git log` and the most recent
`docs/rc2*` report instead.*
