# Local Agent Dev Studio — Evaluation

**Result: 3 / 3 demos green.** Local Agent Dev Studio drove three distinct Next.js + TypeScript projects from `requirements.md` through autonomous greenfield generation AND a real change request, end-to-end, with real Codex. Every change run produced a schema-validated `applied-change.json`, a clean `delivery-report.md`, a real git commit on a `agentic/change/<id>` branch carrying provenance trailers, and a passing `npm run build`. Zero blocking review items remained open in any demo.

This evaluation explains why the matrix exists, presents the per-demo evidence, documents the cleanup pass that hardened it, and is honest about what's deliberately out of scope.

---

## Why these three demos

The matrix's job is to prove Local Agent Dev Studio is **not a single-template generator**. Three demos, three different verticals, one orchestration:

| # | Demo | Vertical it proves |
|---|------|---------------------|
| 1 | **ai-writing-quality-editor** | AI text-quality product (deterministic analyzer, suggestion list, persistence) |
| 2 | **ai-usage-cost-planner** | AI SaaS cost / token-budget logic (form-driven calc, scenario table, budget control) |
| 3 | **agent-review-queue-console** | Agent workflow / human-in-the-loop governance (severity badges, status filter, approve/reject/resolve) |

These three were chosen over generic "finance tracker" / "creator tracker" because they are recognizable 2026-era AI-product framings — text-quality, AI cost governance, and agent review queues map directly to themes a reader/interviewer is currently seeing in real AI SaaS.

The deeper point: same machinery (deterministic decomposer → multi-candidate Codex inner loop → 12-rule Promotion Gate → 10-rule Apply Gate → real git commit → applied-change/delivery-report → schema validators) drove all three to green without any per-demo special-case code.

---

## Per-demo evidence

### Demo 1 — AI Writing Quality Editor

| Field | Value |
|-------|-------|
| greenfield project | `ai-writing-quality-editor` (Next.js 15.5.18 + React 19 + TS 5.7) |
| greenfield commits | `4efd1d8` task-001 · `7ca9304` task-002 · `bca2ec4` task-003 |
| change request | "Add deterministic clarity score 0-100" |
| change_id | `change_c19add9a71` |
| change run_id | `run_70dc791814` |
| change commit | `15864c9` on `agentic/change/change_c19add9a71` |
| files touched | `app/page.tsx`, `components/analyzer.ts` |
| build status | ✅ `npm run build` passed |
| change validate | ✅ `ok=true` (contract / delivery-report / applied-change all clean) |
| autonomous validate-artifacts | ✅ ok=true |
| review queue count | 0 |
| delivery-report.md | `<workspace>/.agent/changes/change_c19add9a71/delivery-report.md` |
| applied-change.json | `<workspace>/.agent/changes/change_c19add9a71/applied-change.json` |

### Demo 2 — AI Usage & Cost Planner

| Field | Value |
|-------|-------|
| greenfield project | `ai-usage-cost-planner` (same stack) |
| greenfield commits | `1e460ab` task-001 · `ee193b1` task-002 · `49d8afb` task-003 |
| change request | "Add budget warning + break-even comparison" |
| change_id | `change_9bdae25130` |
| change run_id | `run_221266bb27` |
| change commit | `1f15c39` on `agentic/change/change_9bdae25130` |
| files touched | `app/page.tsx` |
| build status | ✅ `npm run build` passed |
| change validate | ✅ `ok=true` |
| autonomous validate-artifacts | ✅ ok=true |
| review queue count | 0 |
| delivery-report.md | `<workspace>/.agent/changes/change_9bdae25130/delivery-report.md` |
| applied-change.json | `<workspace>/.agent/changes/change_9bdae25130/applied-change.json` |

### Demo 3 — Agent Review Queue Console

| Field | Value |
|-------|-------|
| greenfield project | `agent-review-queue-console` (same stack) |
| greenfield commits | `45ac00a` task-001 · `7e7f113` task-002 · `5e1c3ce` task-003 |
| change request | "Add SLA risk badges" |
| change_id | `change_e8525afae2` |
| change run_id | `run_1b3d513d29` |
| change commit | `63979f5` on `agentic/change/change_e8525afae2` |
| files touched | `app/page.tsx`, `components/reviews.ts` |
| build status | ✅ `npm run build` passed |
| change validate | ✅ `ok=true` |
| autonomous validate-artifacts | ✅ ok=true |
| review queue count | 0 |
| delivery-report.md | `<workspace>/.agent/changes/change_e8525afae2/delivery-report.md` |
| applied-change.json | `<workspace>/.agent/changes/change_e8525afae2/applied-change.json` |

(Workspaces: `/tmp/rc4b-<demo>/.agent-studio/projects/<demo>-<id>/`. See `docs/rc4c-demo-suite-report.md` for the full per-demo run-by-run breakdown including timings, integration counters, and the artifact schema details.)

---

## RC-4C.1 — failures the first run surfaced and how they were fixed

The first real-Codex pass on `ai-writing-quality-editor` *failed* on three orchestration bugs the dry-run + unit suites had not caught. RC-4C.1 fixed all three before the rerun. The fixes are evidence the Studio's hardening loop works on its own bugs too.

### 1. Scope parser captured backticks literally

**Symptom.** `agent-studio autonomous start` paused at task-001 with `review_2beda9738c` open. Codex (`run_0f41f8b7ee`, `candidate-a`) had produced a real, building patch (`patch_apply_check_passed=true`, `source_patch_present=true`, `npm run build` passed locally), but the Promotion Gate refused with `diff_within_scope=false`. The changed-files report said `app/page.tsx within_scope=false` — even though task-001's stated scope was `app/**`.

**Root cause.** Every example's `Scope:` line was written as ``Scope: `app/**`, `components/**` `` because backticks render as code in markdown previewers. The parser captured `\`app/**\`` and `\`components/**\`` literally; `fnmatch.fnmatch("app/page.tsx", "\`app/**\`")` returned False; the gate blocked. Real Codex behaved correctly; the bug was on the parser side.

**Fix.** `_clean_meta_value` helper in `orchestrator/core/autonomous.py` strips wrapping backticks (and double quotes) defensively. `_SCOPE_RE` regex relaxed to allow `Scope:` with no inline value as the opener of a multi-line bullet block. All three example `requirements.md` files rewritten to the cleaner bullet form. Six new parser regression tests in `tests/unit/test_autonomous.py` lock the contract for both inline and bullet forms.

### 2. Runner ran change-mode against an incomplete greenfield

**Symptom.** Even though greenfield paused at task-001, `scripts/run_demo_suite.sh` kept going and ran `change new + change run` against the half-built scaffold. Output was meaningless — `applied_change_json=null`, `delivery_report_md exists`, `state="delivered"`.

**Root cause.** The runner had no gate between "autonomous start finished" and "change new starts". It assumed greenfield always succeeded.

**Fix.** Runner now reads `<project>/.agent/autonomous/sessions/*/autonomous-session.json` after each demo's greenfield. If `status != "completed"`, it prints the pause reason + review-queue inspection commands, records `greenfield_paused` in the cross-demo summary, completely skips the change-mode steps, and exits non-zero in single-demo mode.

### 3. `change status` reported `delivered` without `applied-change.json`

**Symptom.** A failed change run (Promotion Gate refused, no apply) still wrote `delivery-report.md` (with `## Result\n\n**failed**`). Operator ran `agent-studio change status latest --json` → got `state="delivered"`. Actively misleading.

**Root cause.** The pre-fix logic in `change_status_summary` was `if has_delivery: state="delivered"`. It never inspected the report's actual result token, never required `applied-change.json` to be present.

**Fix.** `delivered` now requires BOTH files. Delivery-without-apply maps via the new `_state_from_delivery_report` helper to the report's actual result token (`failed` / `needs_human_review` / fallback to `failed` for unparseable or inconsistent input). Four new state-derivation tests in `test_change_contract.py` lock the new semantics.

After RC-4C.1 landed, the rerun on `ai-writing-quality-editor` succeeded cleanly, and the same machinery then carried the other two demos to green without further intervention.

---

## What this proves

### Local Agent Dev Studio is not a fixed template

The 3 demos cover three different product surfaces (AI text-quality / AI cost / agent governance), three different state shapes (suggestion list / scenarios table / status badges + actions), and three different change archetypes (additive scoring derived from existing analysis / new input + warning + derived columns / time-based derived state with badge). The same orchestration drove all three to green without per-demo special-case logic.

### Greenfield AND brownfield work end-to-end

- Greenfield = `requirements.md` → autonomous → 3 task commits per demo (9 total across the matrix).
- Brownfield (Change Request Mode) = `change-request.md` against an EXISTING repo → real Codex generates the patch → Promotion Gate decides → Apply Gate enforces safety → real git commit lands with `Change-Id` + `Source-Change-Request` trailers (3 change commits across the matrix).

12 real-Codex commits total. Every commit grep-able by `Agent-Task-ID` and (for change commits) `Change-Id`.

### Real Codex, real eval gates, real artifacts

- Patch worker = `codex` (sandbox `workspace-write`, approval `on-request`).
- Eval harness = real `npm run build` and `npm run typecheck` executed in an ephemeral worktree per candidate.
- Promotion Gate = 12 deterministic hard rules (this run: 6 of 12 fired per change; the other 6 cover paths irrelevant to single-candidate change runs); roll-up `hard_gates=6/6 passed` on every demo.
- Apply Gate = 10 deterministic hard rules including base-commit equality, worktree clean (modulo `.agent/` + `task-graph.json`), `git apply --check` clean, no out-of-scope file mutations, and a re-apply guard.
- Artifacts validated by schema validators in `orchestrator/core/artifact_validation.py`: `validate_change_contract` (agentic.change_contract.v1), `validate_applied_change` (agentic.applied_change.v1), `validate_delivery_report_text`, `validate_promotion_report` (v2), `validate_applied_candidate`, plus the autonomous-session / task-graph / review-item validators.

### Gated patch application

A change cannot land unless:
1. The Promotion Gate's 12 deterministic hard rules pass (or fail with a logged reason).
2. The Apply Gate's 10 deterministic hard rules pass (`git apply --check` first, then real `git apply`).
3. The post-commit cleanup amends the commit so the ephemeral 1-task `task-graph.json` is excluded from the change branch (RC-4A.3.1.A).
4. `change validate` passes over all three change-dir artifacts.

If any gate refuses, the controller emits a review item to the human-in-the-loop queue and pauses — the change does NOT silently fail. Across the 3 demos, zero review items were emitted.

### Evidence artifacts

For every change there's a permanent, schema-validated audit trail:

```
.agent/changes/<change_id>/
  change-request.md           ← immutable copy of the operator intent
  change-contract.json        ← parsed goal/scope/acceptance/non-goals + provenance
  repo-onboarding.md          ← deterministic project snapshot
  implementation-plan.md      ← derived from contract + onboarding
  acceptance-criteria.json    ← shape-compatible with autonomous mode
  applied-change.json         ← agentic.applied_change.v1; commit/branch/sha + files_touched + promotion_decision
  delivery-report.md          ← operator-facing summary; Validation section shows real eval/promotion/apply rows

.agent/runs/<run_id>/
  promotion-report.json       ← v2 schema; gate_details + decision + selected_candidate
  candidates/<candidate>/
    patch.diff                ← real git diff (RC-3E.2 fix; replaces difflib)
    score.json                ← per-candidate hard gates + soft scores
    changed-files.json        ← agentic.changed_files.v1; base_commit + within_scope flags
    eval-results.json         ← per-command pass/fail
    critics/{correctness,regression,security,ux,overfit}.md
```

Plus the per-task git commits with full evidence trailers (`Agent-Task-ID`, `Agent-Run-ID`, `Selected-Candidate`, `Candidate-Strategy`, `Promotion-Decision`, `Promotion-Report`, `Change-Id`, `Source-Change-Request`).

---

## Limitations

These are deliberate scope choices for RC-4C, not project-wide ceilings. Each one has a known landing point in a future milestone.

### No Vercel deploy in RC-4C

`agent-studio.yaml` ships with `deploy: { enabled: false }` (the runner grep-asserts this). The reason is risk surface: Vercel adds env credential complexity + flake surface that has nothing to do with proving change-mode works across 3 verticals. Earlier RC-2/RC-3 dogfoods proved the Vercel + smoke-check + rollback ladder works against single-project shapes. RC-4D portfolio packaging is the natural place to add live preview URLs if useful.

### No backend / API / DB in the demo matrix

All 3 demos are client-only Next.js with `localStorage`. No FastAPI, no Prisma, no fetch. This is a portfolio-readability choice — each demo stays under 10 source files including the change. The Studio itself supports backends (RC-3C / RC-3D ran FastAPI + Prisma greenfield demos with the same machinery), but the multi-stack matrix would dilute what RC-4B/C is trying to prove.

### No tests beyond build + typecheck in the demos

The `agent-studio.yaml` integration command list for each demo runs `npm run build` and `npm run typecheck` only. There's no demo-level unit test framework (Jest / Vitest) wired in. Acceptance criteria in `requirements.md` are deterministic and Codex-checkable by reading the built code, but they are NOT executed as test assertions. RC-4D could add per-demo tests without changing the orchestration.

### No GitHub PR automation yet

The Studio leaves a real local git commit on the `agentic/change/<change_id>` branch. It does not push, open a PR, request reviewers, or attach the delivery report as a PR comment. That's a clean future milestone (call it RC-4E or RC-5A): the `delivery-report.md` already has the right shape to become a PR description.

### Single-candidate per task in the matrix

`max_candidates_per_task: 1` in the per-demo `agent-studio.yaml`. The multi-candidate path (3 strategies — fast, conservative, test-focused — running in parallel with a Promotion Gate selecting the winner) was exercised in earlier RC-3 dogfoods, but for cost control the matrix uses single-candidate. This means each change run picked `conservative` because it was the only candidate, not because a bake-off chose it. The Studio's multi-candidate orchestration is unchanged — only the budget setting differs.

---

## File pointers

| File | Purpose |
|------|---------|
| `docs/rc4c-demo-suite-report.md` | Run-by-run evidence (commits, artifact paths, validation rows, timings) |
| `docs/demo-matrix.md` | Original RC-4B prep doc + the RC-4C.1 cleanup amendment |
| `docs/interview/01-project-summary.md` | Plain-English elevator pitch + glossary |
| `docs/interview/02-architecture-walkthrough.md` | Component-by-component architecture with text diagrams |
| `docs/interview/03-failure-cases.md` | Real bugs surfaced + how each was fixed |
| `docs/interview/04-demo-matrix-story.md` | Narrative for telling this story to an interviewer |
| `examples/<demo>/requirements.md` | Each demo's greenfield input |
| `examples/<demo>/changes/01-*.md` | Each demo's first change request |
| `scripts/run_demo_suite.sh` | The runner that produced this evidence |

---

**Bottom line.** Local Agent Dev Studio handles **both greenfield project generation AND change requests on existing projects** for at least three distinct AI-shaped product verticals — verified end-to-end with real Codex, real git commits, real eval gates, and a complete schema-validated evidence trail. The two failure surfaces the first run uncovered (RC-4C.1) were fixed before this report was written, so the matrix shipped 3/3 green on the second pass.
