# RC-4B — 3-Project Demo Matrix

**Status:** RC-4B prep complete + RC-4C.1 cleanup applied — holding for `go RC-4C single-demo rerun`.

## RC-4C.1 cleanup (2026-05-13)

The first real-Codex single-demo run (`scripts/run_demo_suite.sh --run --demo=ai-writing-quality-editor`) surfaced three orchestration bugs the dry-run + unit suite had not caught:

1. **Scope formatting bug.** Every example's `Scope:` line was ``Scope: `app/**`, `components/**` `` — the parser captured the backticks literally, fnmatch never matched any patched file, Promotion Gate blocked on `diff_within_scope=false` even though Codex produced a real, building patch.
   - **Fix:** Parser now strips wrapping backticks defensively AND supports a multi-line bullet form (`Scope:\n- app/**\n- components/**`). All 3 examples rewritten to the bullet form.
   - **Tests:** 6 new parser regression tests in `test_autonomous.py` (`scope_strips_wrapping_backticks`, `scope_multiline_bullet_form`, `scope_multiline_block_closes_on_next_meta_line`, etc.).
2. **Runner gate bug.** When `agent-studio autonomous start` paused (e.g. greenfield needs-human-review), the runner kept going and ran `change new + change run` against an unfinished scaffold, producing meaningless evidence.
   - **Fix:** `scripts/run_demo_suite.sh` now reads `autonomous-session.json` after each demo's greenfield run. If `status != "completed"`, it skips change-mode entirely, marks the demo `greenfield_paused`, prints the review-queue hint, and exits non-zero in single-demo mode.
3. **`change status` misreporting `delivered`.** When a change run failed (no `applied-change.json`) but the runner had already written `delivery-report.md`, `change_status_summary` returned `state="delivered"` — actively wrong.
   - **Fix:** `delivered` now requires BOTH `applied-change.json` AND `delivery-report.md`. Delivery-without-apply maps to the actual `## Result` token from the report (`needs_human_review` / `failed`). 4 new regression tests added.

The demo matrix itself is unchanged — same 3 demos, same change archetypes, same stack. Only orchestration got hardened.

## Why this matrix exists

Local Agent Dev Studio has now proven both core capabilities on a single tiny project (RC-4A.3 + RC-4A.3.1):

- **Greenfield generation** — `requirements.md` → autonomous → committed code, build passes.
- **Change Request Mode** — `change-request.md` → real Codex → applied-change + delivery-report + clean worktree.

The demo matrix's job is to prove **this works across more than one project shape**. Three demos, each picking a distinct vertical, each running the same machinery, each producing the same evidence shape.

## The matrix

| # | Demo | Vertical | Greenfield (3 tasks) | First change request |
| --- | --- | --- | --- | --- |
| 1 | `ai-writing-quality-editor` | AI text-quality product | Editor shell + tone selector → deterministic analyzer (long sentence / repeated word / templated opener / overly formal) → wire + localStorage persistence | **Add clarity score 0-100** computed from analyzer findings (kind-weighted deductions, clamped) |
| 2 | `ai-usage-cost-planner` | AI SaaS cost / token budget logic | Planner shell + scenario form → pricing constants (3 fixed model tiers) + `monthlyCost` calculator → scenarios table + total summary + localStorage | **Add budget warning + break-even comparison** (monthly budget input, over-budget badge, share %, cheapest/most-expensive tags) |
| 3 | `agent-review-queue-console` | Agent workflow / human-in-the-loop governance | Console shell + summary cards + status filter → 6 seed review items (3 severity × 3 reason codes × 4 statuses) → wire actions + badges + counts + localStorage | **Add SLA risk badges** (`urgent` for blocking >24h, `review-soon` for warning >8h, urgent count card) |

Each demo is a small Next.js + TypeScript App Router app. No backend, no DB, no fetch, no auth, no Vercel deploy, no test framework, no CSS framework. Inline styles only. Pure deterministic TS for all logic — every demo's "AI angle" is in its product framing, not in calling a real LLM.

## Stack (uniform across all 3 demos)

- Next.js 15.5.18 + React 19.0.0 + TypeScript 5.7.3 (matches RC-4A.3 baseline)
- App Router, single client-side page per demo
- Local state + `localStorage` only
- Inline styles (no Tailwind)
- npm scripts: `build`, `dev`, `start`, `typecheck`. **No** test script.
- No external API, no environment variables, no `.env`

## File layout

```
examples/
  ai-writing-quality-editor/
    package.json
    tsconfig.json
    next.config.mjs
    .gitignore
    app/
      layout.tsx
      page.tsx          # baseline placeholder; the autonomous run replaces it
    requirements.md     # 3 tasks — greenfield input
    changes/
      01-add-clarity-score.md
  ai-usage-cost-planner/
    (same file shape; requirements.md + changes/01-add-budget-warning.md)
  agent-review-queue-console/
    (same file shape; requirements.md + changes/01-add-sla-risk-badges.md)

scripts/
  run_demo_suite.sh    # dry-run by default; --run to execute; --demo=<name> for single demo

docs/
  demo-matrix.md       # this file
```

## How a single demo flows

For each demo `<name>`, `scripts/run_demo_suite.sh` runs:

1. `rm -rf /tmp/rc4b-<name>` (per-demo isolated workspace)
2. `agent-studio --root /tmp/rc4b-<name> init`
3. `agent-studio --root /tmp/rc4b-<name> new --from examples/<name>/requirements.md`
4. Copy seed files (`package.json`, `tsconfig.json`, `next.config.mjs`, `.gitignore`, `app/`) into the project dir
5. Write per-demo `agent-studio.yaml` inline (interpolates `$CODEX_BIN`)
6. `npm install --no-audit --no-fund`
7. `npm run build` — confirms the baseline scaffold compiles BEFORE Codex touches anything
8. `git init` + baseline commit
9. `agent-studio autonomous preflight`
10. `agent-studio autonomous start` — drives the 3 greenfield tasks (real Codex)
11. `agent-studio change new --from examples/<name>/changes/01-*.md`
12. `agent-studio change run latest`
13. `agent-studio change status latest --json`
14. `agent-studio change validate latest --json`
15. `agent-studio autonomous validate-artifacts --json`
16. Final `npm run build` — confirms the change branch still builds
17. Per-demo summary line emitted into the cross-demo roll-up

After all demos run, the script prints a single roll-up summary with each demo's `change_run_exit`, commit SHA, branch, build status, and artifact paths.

## Per-demo budgets

Each demo runs with the same tight budgets so a runaway Codex run still has a hard ceiling:

```yaml
autonomous:
  budgets:
    max_tasks_per_session: 3              # 3 greenfield tasks
    max_total_inner_runs: 5               # cap retries
    max_candidates_per_task: 1            # one candidate per task
    max_repair_attempts_per_candidate: 1
    max_abandoned_tasks: 1
    max_corrective_tasks: 1
```

Change Request Mode is single-task by definition — the same agentic config governs the change run.

## Success criteria for the matrix

A demo is **green** when ALL of:

1. Greenfield run completes; all 3 task commits land on `agentic/autonomous/<session_id>` with `Agent-Task-ID` trailers.
2. `npm run build` passes after the greenfield run.
3. `agent-studio change run latest` returns `result=completed` (exit 0).
4. The change commit lands on `agentic/change/<change_id>` with `Change-Id` AND `Source-Change-Request` trailers.
5. `applied-change.json` validates clean (`agentic.applied_change.v1`).
6. `delivery-report.md` validates clean and the **Validation** section shows real `eval.*` + `promotion` + `apply` rows (not "(no validation results recorded)").
7. `git status -s` is clean post-change-run (RC-4A.3.1 hygiene).
8. `npm run build` still passes after the change.
9. `agent-studio change validate latest --json` returns `ok=true` over all 3 artifacts (`change-contract.json`, `delivery-report.md`, `applied-change.json`).
10. No blocking review items.

The matrix is **green** when **3/3 demos are green**. Partial pass (1/3 or 2/3) is still useful evidence and gets honestly recorded in RC-4C's `EVALUATION.md`.

## Failure predictions per demo

Each vertical has its own drift surface; the change-request non-goals are written to catch the predicted misbehavior.

### `ai-writing-quality-editor`

- Codex tries to import an LLM client (`openai`, `anthropic`) or a text-analysis dep (`compromise`, `natural`, `franc`, `wink-nlp`). Hard non-goal: "Do not call any external API. Do not import any LLM client. Do not add any new dependency."
- Codex makes the analyzer non-deterministic (calls `Date.now()` for some scoring noise). Hard test: `analyze("")` returns `[]`; the four kind-specific tests all assert exact shapes.
- Codex changes the `Finding` / `FindingKind` shape mid-task. Acceptance pins the kinds explicitly.

### `ai-usage-cost-planner`

- Codex adds a date library (`date-fns`, `dayjs`, `luxon`) for nicer formatting. Non-goal: "Do not import a date library."
- Codex re-orders or renames `MODEL_OPTIONS` entries (downstream task-003 references them). Acceptance: "EXACTLY these three entries… do NOT add more, do NOT change names."
- Codex adds a charting library for the budget warning. Non-goal: "Do not add a chart library. Render percentages and badges as text."

### `agent-review-queue-console`

- Codex adds a date library for the SLA "older than 24h" math. Non-goal: "Use the built-in `Date` only."
- Codex mutates `SEED_REVIEWS` shape to add `slaRisk` directly to each item (instead of computing it as derived state). Non-goal: "the SLA badge is derived state, not stored."
- Codex adds a UI/icon framework for the colored severity badges. Non-goal: "Inline styles only."

### Cross-demo

- Out-of-scope `package.json` edit (any demo).
- Out-of-scope `tsconfig.json` / `next.config.mjs` / `.gitignore` edit.
- Build regression after change applies (Apply Gate passes shape, not runtime correctness).
- Codex adds test files (we don't run tests).
- Codex adds `.env.example` / config we don't want.

The Apply Gate's `out_of_scope_changes` rule + the `npm run build` post-check together catch most of the above. Where they don't, the demo simply fails its corresponding success-criteria step and the failure gets recorded honestly in RC-4C.

## Out of scope (RC-4B prep)

- No real Codex run yet (RC-4C runs the suite).
- No Vercel deploy.
- No backend / API routes / DB.
- No tests (Jest / Vitest / Playwright).
- No CSS framework.
- No GitHub PR creation.
- No multi-change chains per demo (one change each — RC-4D portfolio polish can add more).
- No screenshots / video / portfolio packaging (RC-4D).
- No `EVALUATION.md` (RC-4C writes it from real evidence).
- No RC-3F detector work.

## Operator checklist

Run **before** invoking `--run`:

- [ ] `which codex` returns a path; if not, install via `brew install codex` or set `CODEX_BIN`.
- [ ] `codex --version` runs cleanly.
- [ ] `npm --version` and `node --version` (need Node ≥ 18 for Next.js 15).
- [ ] `git --version` available.
- [ ] `python3 --version` available (used by the script's summary block).
- [ ] `bash -n scripts/run_demo_suite.sh` passes (verified during prep).
- [ ] `scripts/run_demo_suite.sh` runs cleanly in dry-run (verified during prep).
- [ ] Per-demo seed validation passed during prep — `npm install` + `npm run build` + `npm run typecheck` clean against each example.

To execute the full matrix:

```bash
cd /path/to/LocalAgents
scripts/run_demo_suite.sh --run
```

Or one demo at a time:

```bash
scripts/run_demo_suite.sh --demo=ai-writing-quality-editor --run
scripts/run_demo_suite.sh --demo=ai-usage-cost-planner --run
scripts/run_demo_suite.sh --demo=agent-review-queue-console --run
```

The script pauses with a "press Ctrl+C to abort" prompt before invoking Codex on each demo (NOTE: the per-demo pause currently lives inside the runner; for now it's a single global cost-warning header, not a per-demo confirm).

## RC-4B / RC-4C / RC-4D split

| Milestone | Scope |
| --- | --- |
| **RC-4B (this prep)** | Three example seeds + suite runner + this matrix doc + local seed validation. **No real Codex.** |
| **RC-4C (next)** | Run the matrix (`scripts/run_demo_suite.sh --run`), capture per-demo evidence under `docs/rc4c-evidence/`, write `docs/EVALUATION.md` with real commit hashes / token counts / drift surfaces. |
| **RC-4D (later)** | Portfolio packaging — `README.md` showcase, `ARCHITECTURE.md`, `INTERVIEW_STORY.md`, optional Vercel previews + screenshots, optional GitHub PR creation hook. |

## Seed validation (done during prep)

Verified locally on 2026-05-13 against each of the three seed dirs:

- `bash -n scripts/run_demo_suite.sh` — clean.
- `scripts/run_demo_suite.sh` (dry-run) — prints every step it will execute for all 3 demos, including the inline `agent-studio.yaml` block.
- For each demo's seed (in a temporary dir): `npm install --no-audit --no-fund` clean, `npm run build` clean (`Compiled successfully`), `npm run typecheck` clean.

## What this proves once RC-4C runs

> Local Agent Dev Studio is not a single-template generator. Across three distinct verticals — AI text quality, AI cost planning, and agent governance — the same machinery (deterministic decomposer, multi-candidate Codex inner loop, 12-rule Promotion Gate, 10-rule Apply Gate, real git commit + Change-Id trailers, applied-change.json + delivery-report.md, schema validators) drives both greenfield generation AND change-mode delivery to a clean, reviewable end state.

This is the claim RC-4C's `EVALUATION.md` will substantiate with real evidence.
