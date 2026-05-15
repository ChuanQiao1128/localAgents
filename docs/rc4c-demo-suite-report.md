# RC-4C — Demo Suite Real-Codex Run Report

**Status:** RC-4C ✅ 3/3 GREEN. Local Agent Dev Studio drove three distinct Next.js + TS projects from `requirements.md` to a verified change-applied delivery — all with real Codex, real git commits, real eval gates, and zero open review items.

This doc is the run-by-run evidence record. The matrix-level evaluation (why these three demos, what the suite proves, limitations) is in `docs/EVALUATION.md`.

---

## Suite-level summary

| # | Demo | Greenfield | Change | Files Touched | Build | Validate | Reviews |
|---|------|------------|--------|---------------|-------|----------|---------|
| 1 | ai-writing-quality-editor | 3/3 commits | `15864c9` on `agentic/change/change_c19add9a71` | `app/page.tsx`, `components/analyzer.ts` | ✅ | ✅ ok=true | 0 |
| 2 | ai-usage-cost-planner | 3/3 commits | `1f15c39` on `agentic/change/change_9bdae25130` | `app/page.tsx` | ✅ | ✅ ok=true | 0 |
| 3 | agent-review-queue-console | 3/3 commits | `63979f5` on `agentic/change/change_e8525afae2` | `app/page.tsx`, `components/reviews.ts` | ✅ | ✅ ok=true | 0 |

Across all three demos: **9 greenfield task commits + 3 change-mode commits = 12 real-Codex commits**, every one of which carries the full evidence trailer set (`Agent-Task-ID`, `Agent-Run-ID`, `Selected-Candidate`, `Candidate-Strategy`, `Promotion-Decision`, `Promotion-Report`, plus `Change-Id` + `Source-Change-Request` on the change commits). Every Promotion Gate fired with `hard_gates=6/6 passed`. Every change-mode delivery report's Validation section listed `eval.build / eval.typecheck / eval.required / promotion / apply` rows — none rendered the "(no validation results recorded)" pre-RC-4A.3.1 placeholder.

---

## Demo 1 — AI Writing Quality Editor

**Project id:** `project_ab6ae7e5d3` · **Workspace:** `/tmp/rc4b-ai-writing-quality-editor`

### Greenfield (`agent-studio autonomous start`)

Session `session_333bccc6df` reached `status=completed`, `pause_reason=null`. Three tasks, three real-Codex inner runs, four integration checks (3 per-task + 1 session-end), all passed.

| Task | Commit | Run id | Status | Scope |
|------|--------|--------|--------|-------|
| `task-001` Editor page shell | `4efd1d8` | `run_37cea9e129` | completed | `app/**` |
| `task-002` Deterministic writing analyzer | `7ca9304` | `run_a54d617b65` | completed | `app/**`, `components/**` |
| `task-003` Wire analyzer + persistence | `bca2ec4` | `run_b8e65a0545` | completed | `app/**`, `components/**` |

Final greenfield HEAD: `bca2ec4` on `agentic/autonomous/session_333bccc6df`. `npm run build` + `npm run typecheck` passed at every checkpoint.

### Change request — "Add deterministic clarity score 0-100"

| Field | Value |
|-------|-------|
| change_id | `change_c19add9a71` |
| run_id | `run_70dc791814` |
| candidate | `candidate-a` (strategy: `conservative`) |
| base_commit | `bca2ec4` (= last greenfield HEAD — no drift) |
| applied_to_commit | `bca2ec4` |
| commit sha | `15864c9` |
| commit branch | `agentic/change/change_c19add9a71` |
| files_touched | `app/page.tsx`, `components/analyzer.ts` |
| promotion_decision | `promote` |
| applied_at | 2026-05-13T23:55:55+00:00 |
| elapsed | 399.447 sec |

Delivery report's Validation section:

```
- **apply**: passed — `applied to 15864c9 on `agentic/change/change_c19add9a71``
- **eval.build**: passed — `npm run build`
- **eval.required**: passed — `required eval declared=True, executed=True`
- **eval.typecheck**: passed — `npm run typecheck`
- **promotion**: passed — `decision=promote, hard_gates=6/6 passed`
```

`change validate latest --json` → `ok=true` over `change-contract.json`, `delivery-report.md`, `applied-change.json`. `autonomous validate-artifacts --json` → all artifacts schema-clean. Review queue: 0 open items.

### Artifact paths

```
/tmp/rc4b-ai-writing-quality-editor/.agent-studio/projects/ai-writing-quality-editor-e7e5d3/
  .agent/changes/change_c19add9a71/
    change-request.md
    change-contract.json
    repo-onboarding.md
    implementation-plan.md
    acceptance-criteria.json
    applied-change.json          ← agentic.applied_change.v1
    delivery-report.md
  .agent/runs/run_70dc791814/    ← Codex inner-run package (promotion-report.json + candidates/)
  .agent/autonomous/sessions/session_3aa359c3c5/   ← change-mode session
```

---

## Demo 2 — AI Usage & Cost Planner

**Project id:** `project_8be703bdbb` · **Workspace:** `/tmp/rc4b-ai-usage-cost-planner`

### Greenfield

Session `session_da7dd3cb74` → `status=completed`. Three tasks, three real-Codex runs.

| Task | Commit | Run id | Status | Scope |
|------|--------|--------|--------|-------|
| `task-001` Planner page shell + scenario form | `1e460ab` | `run_c6153fffeb` | completed | `app/**` |
| `task-002` Pricing constants + cost calculator | `ee193b1` | `run_0fd570c756` | completed | `app/**`, `components/**` |
| `task-003` Wire form + scenarios table + localStorage | `49d8afb` | `run_e723c9c4c2` | completed | `app/**`, `components/**` |

Final greenfield HEAD: `49d8afb`.

### Change request — "Add budget warning + break-even comparison"

| Field | Value |
|-------|-------|
| change_id | `change_9bdae25130` |
| run_id | `run_221266bb27` |
| candidate | `candidate-a` (strategy: `conservative`) |
| base_commit | `49d8afb` |
| applied_to_commit | `49d8afb` |
| commit sha | `1f15c39` |
| commit branch | `agentic/change/change_9bdae25130` |
| files_touched | `app/page.tsx` (single-file change — Codex did the entire feature in one file) |
| promotion_decision | `promote` |
| applied_at | 2026-05-14T00:29:34+00:00 |
| elapsed | 565.617 sec |

Validation: same five rows (`apply / eval.build / eval.required / eval.typecheck / promotion`) all passed, `hard_gates=6/6`. `change validate ok=true`. Reviews: 0.

### Artifact paths

```
/tmp/rc4b-ai-usage-cost-planner/.agent-studio/projects/ai-usage-cost-planner-03bdbb/
  .agent/changes/change_9bdae25130/{applied-change.json, delivery-report.md, ...}
  .agent/runs/run_221266bb27/
  .agent/autonomous/sessions/session_bef14f82d1/   ← change-mode session
```

---

## Demo 3 — Agent Review Queue Console

**Project id:** `project_675a9d2c99` · **Workspace:** `/tmp/rc4b-agent-review-queue-console`

### Greenfield

Session `session_accb3028ea` → `status=completed`, `inner_runs=3, integrations_run=4, integrations_passed=4`. Three tasks landed cleanly.

| Task | Commit | Run id | Status | Scope |
|------|--------|--------|--------|-------|
| `task-001` Console page shell + summary + filter | `45ac00a` | `run_2f843bc90d` | completed | `app/**` |
| `task-002` Seed review items | `7e7f113` | `run_bd241e13eb` | completed | `app/**`, `components/**` |
| `task-003` Wire actions, badges, summary counts, filter | `5e1c3ce` | `run_bb2cefd7c8` | completed | `app/**`, `components/**` |

Final greenfield HEAD: `5e1c3ce`.

### Change request — "Add SLA risk badges"

| Field | Value |
|-------|-------|
| change_id | `change_e8525afae2` |
| run_id | `run_1b3d513d29` |
| candidate | `candidate-a` (strategy: `conservative`) |
| base_commit | `5e1c3ce` |
| applied_to_commit | `5e1c3ce` |
| commit sha | `63979f5` |
| commit branch | `agentic/change/change_e8525afae2` |
| files_touched | `app/page.tsx`, `components/reviews.ts` |
| promotion_decision | `promote` |
| applied_at | 2026-05-14T09:40:44+00:00 |
| elapsed | 681.559 sec |

Validation: same five rows, `hard_gates=6/6`. `change validate ok=true`. Reviews: 0.

The change carried real product logic — a pure `slaRisk(item, now)` helper plus a new `summary-urgent` count card — and Codex respected the non-goals (no date library imported, no chart library, no `package.json` edit, no schema mutation on `SEED_REVIEWS`). The Apply Gate's `out_of_scope_changes` check would have blocked any of those drift modes.

### Artifact paths

```
/tmp/rc4b-agent-review-queue-console/.agent-studio/projects/agent-review-queue-console-9d2c99/
  .agent/changes/change_e8525afae2/{applied-change.json, delivery-report.md, ...}
  .agent/runs/run_1b3d513d29/
  .agent/autonomous/sessions/session_ae046c386f/   ← change-mode session
```

---

## Cross-demo observations

### Strategy selection

All three change runs picked `conservative` as the winning candidate strategy. With `max_candidates_per_task=1` (the agent-studio.yaml budget the runner writes), Codex generates a single candidate per task and the Promotion Gate either promotes or doesn't — there's no inter-strategy bake-off in this matrix. This is a deliberate budget choice (token spend), not a Studio limitation; the multi-candidate path was already exercised in earlier RC-3 dogfoods.

### Same six hard gates pass across all three demos

Every change run's promotion-report rolled up to `hard_gates=6/6 passed`, and every Apply Gate accepted the patch on the first try. The six gates touched here include `source_patch_present`, `diff_within_scope`, `patch_apply_check_passed` (RC-3E.2 fix), and the eval-side gates (`required_eval_declared`, `required_eval_executed`, `required_eval_passed`).

The fact that every greenfield task + every change ran with the same gate roll-up across three different verticals is the single strongest signal in this report — it means the gate set is stable, vertical-agnostic, and not a special case for any one project shape.

### Change-mode session counter shape

Each change-mode session reports `completed_tasks=1, inner_runs=1, integrations_run=2, integrations_passed=2`. The 2 integrations correspond to the per-task post-commit check + the session-end check (RC-2C `every_n_tasks=1, run_at_session_end=true`). Both passed every time, on every demo.

### Worktree hygiene

After each demo's change run, `git status --short` was empty (no `D task-graph.json`, no ` M task-graph.json`). RC-4A.3.1.A's amend-the-change-commit-to-purge-task-graph.json fix held across all three demos.

### Wall-clock per change run

| Demo | Change-mode elapsed |
|------|---------------------|
| ai-writing-quality-editor | 6m 39s (399s) |
| ai-usage-cost-planner | 9m 26s (566s) |
| agent-review-queue-console | 11m 21s (682s) |

Greenfield wall-clock per demo (from session start to session end) is ~7-8 minutes for 3 tasks (extracted from `started_at` / `updated_at` deltas on each greenfield session). Total Codex wall-clock for the matrix: roughly **45-55 minutes of model work** + npm install + git plumbing.

---

## Where this fits

- **Not blocked:** Local Agent Dev Studio's greenfield + change-mode pipeline is verified across three distinct AI-shaped product verticals. No surfaces remain unaddressed for the chosen product framing (deterministic-TS, no backend, no Vercel).
- **Next milestone:** RC-4D portfolio packaging — turn this evidence into a polished portfolio (README hero, ARCHITECTURE.md, INTERVIEW_STORY.md, optional Vercel previews + screenshots).
- **Out of scope (intentionally):** see `docs/EVALUATION.md` § Limitations.
