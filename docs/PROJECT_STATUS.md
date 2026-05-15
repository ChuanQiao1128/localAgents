# Local Agent Dev Studio — Project Status

Snapshot as of **2026-05-14**. All milestones below are evidence-anchored: each row links to the artifact, test, or memory note that proves it landed.

中文导读: 这一篇是项目里程碑全景图 — 已完成的、推迟的、下一步要做的都在这里;每一项都对应仓库里的具体证据。

---

## TL;DR

```
Local Agent Dev Studio runtime: ✅ verified end-to-end
  Greenfield:                   ✅ verified (multiple RC-3 dogfoods + RC-4C matrix)
  Change Request Mode:          ✅ verified (RC-4A.3 + RC-4C matrix)
  Demo matrix (3 verticals):    ✅ 3 of 3 green
  Real-Codex orchestration:     ✅ verified
  Schema-validated artifacts:   ✅ 11 validators, 100% coverage of load-bearing files
  Failure-case catalog:         ✅ 8 documented + fixed + regression-tested
  Tests:                        ✅ 337 pass

Portfolio packaging (RC-4D):    🟡 in progress (you are reading it)
AI Writing Naturalizer MVP:     ⏸ deferred to RC-5A+
GitHub PR automation:           ⏸ deferred to RC-4E or later
Studio frontend dashboard:      ⏸ deferred (CLI-only today)
RC-3F detector adapter probe:   ⏸ paused (post-pivot)
```

---

## Completed milestones

### MVP-1 → MVP-3 — runtime foundation

The deterministic runtime that everything else builds on. CLI surface, SQLite persistence, workflow phase state machine, task board, artifact + event logs, approval gates.

- **MVP-1 / MVP-2 (closed memory loop, abandonment tracking, critic panel).** Per-run `memory-update.proposed.json` aggregated into the next task's `prior_learnings`. Failure taxonomy (9 categories), `abandoned` decision path, project-level `agentic-abandonments.jsonl`.
- **MVP-3A (multi-candidate inner loop).** `CANDIDATE_STRATEGIES = [fast, conservative, test-focused]`, deterministic `_score_candidate`, Jaccard candidate diversity, per-candidate critic panels.
- **MVP-3B (Apply Gate).** 10 hard rules, `applied-candidate.json` (`agentic.applied_candidate.v1`), re-apply guard, programmatic `apply_selected_candidate` for the autonomous controller.
- **MVP-3C (read-side helpers).** `RunPackageReader` / `CandidateReport`, schema validation for promotion-report v2 / candidate score / changed-files.

### MVP-4A → MVP-4F — autonomous controller + integration + review queue + deploy

The outer SDLC loop and everything around it.

- **MVP-4A (controller core).** PRD-to-task-graph parser (deterministic), `AutonomousController.start_or_resume / advance_one_task`, session lifecycle, per-task git commits with provenance trailers, `agent-studio autonomous start/status/logs/halt/resume`.
- **MVP-4B (integration runner).** Per-task + session-end `npm run build` / `npm run typecheck` against the cumulative tree; `integration-results.jsonl`.
- **MVP-4C (corrective tasks).** Integration failure → `integration-failure.json` → auto-generated corrective task → re-run integration. Bounded by `max_corrective_tasks`.
- **MVP-4D (human-in-the-loop review queue).** `review_queue.py` schema + CRUD + resume gating; CLI `autonomous reviews list / show / approve --yes / reject / resolve`; `Human-Review-*` commit trailers on override commits.
- **MVP-4E (Vercel deploy adapter).** `deploy.py` + `deploy_vercel.py`; `deployment.json` artifact; CLI `autonomous deploy [--dry-run|--yes] [--prod|--preview]`.
- **MVP-4F (smoke check + rollback).** Post-deploy HTTP smoke checks; `smoke-check.json`; rollback adapter; `rollback.json`; final-run-status.md complete report.

### RC-1 → RC-2E — release-candidate hardening + dogfood loop

End-to-end e2e tests, golden fixtures, real-Codex patch worker dogfood, artifact-validation hardening, multi-block agent-studio.yaml support.

- **RC-1 (golden e2e).** `tests/e2e/test_golden_path.py`, fake patch-worker fixtures, `validate_session_directory` walks every load-bearing artifact.
- **RC-2A → RC-2E (real Codex dogfood + hardening).** First real-Codex dogfood (`tiny-creator-tracker`); fixed eval harness root probe; `CodexPatchWorker` adapter + workspace-write sandbox + on-request approval; producer-side validation hooks for `write_review_item`; `agent-studio autonomous preflight`; surfaced `patch_worker` in status / final report; tested resume-after-codex-becomes-available.
- **RC-2C.1.4 (patch worker prompt hardening).** Success criteria + previous tasks + scope-driven wording included in the Codex prompt template.

### RC-3A → RC-3E — vertical-adapter ladder (greenfield dogfoods across stacks)

Five real-Codex greenfield dogfoods, each adding a new dimension. The hard NO-list (no production deploy, no rollback, no `--yolo`, no token leaks) held across all five.

| RC | Stack added | Surfaced bug → runtime fix |
|----|-------------|----------------------------|
| RC-3A | Next.js + Tailwind shape | `*.tsbuildinfo` captured as out-of-scope (Promotion Gate refused); fix: `_discover_files` filter + `.gitignore` |
| RC-3B | + Prisma data model | (clean) |
| RC-3C | + FastAPI backend | (clean) |
| RC-3D | + Style Guide RAG | (clean) |
| RC-3E | + LLMOps eval suite | difflib-generated `patch.diff` was corrupt; fix: real `git diff --binary` from ephemeral repo + `patch_apply_check_passed` as 12th hard gate |

### RC-3F — paused

Detector adapter probe (Sapling integration with mock-default + real-opt-in routing). Seed + script + prep-report landed, but the probe was deferred when the strategic pivot moved focus to multi-project + change requests. Paused, not abandoned — could resume after RC-5+ if the AI Writing Naturalizer needs detector integration.

### RC-4A — Change Request Mode (the second entry point)

The biggest single feature beyond the autonomous loop. Three sub-milestones:

- **RC-4A.1 — foundation.** Parser (`change_request_parser.py`), repo onboarding (`change_repo_onboarding.py`), change-contract module + 5 artifacts under `.agent/changes/<change_id>/`, delivery-report renderer, CLI plumbing (`change new / list / show / status / validate / run` — run is a stub at this stage).
- **RC-4A.2 — wired.** `change_runner.py` builds a 1-task task-graph from the contract, drives the same `AutonomousController` greenfield uses, applies via Apply Gate, commits with `Change-Id` + `Source-Change-Request` trailers, writes `applied-change.json` (`agentic.applied_change.v1`) + `delivery-report.md`. Schema validator added.
- **RC-4A.3 — real Codex dogfood + RC-4A.3.1 cleanup.** First real-Codex change run (`change_198713d499` on the tiny notes app) shipped — proved end-to-end real Codex works through Change Request Mode. RC-4A.3.1 cleaned 3 issues the real run surfaced: task-graph hygiene (`_purge_task_graph_from_change_commit` amends the change commit), delivery-report Validation section sourcing fix (`commands` not `commands_run`), `change validate` now includes `applied-change.json`.

### RC-4B → RC-4C — 3-project demo matrix

The "not a single-template generator" proof.

- **RC-4B (matrix prep).** Three example seeds (`ai-writing-quality-editor`, `ai-usage-cost-planner`, `agent-review-queue-console`) + `scripts/run_demo_suite.sh` + `docs/demo-matrix.md`. Local seed validation per demo (npm install + build + typecheck clean).
- **RC-4C.1 (orchestration cleanup surfaced by first single-demo run).** Three orchestration bugs fixed:
  - Scope parser captured backticks literally → defensive backtick stripping + multi-line bullet form.
  - Runner ran change-mode against incomplete greenfield → greenfield-completed gate in `run_demo_suite.sh`.
  - `change_status_summary` reported `delivered` without `applied-change.json` → both files now required for `delivered`; delivery-without-apply maps to the report's actual `## Result` token.
- **RC-4C (full matrix run).** **3 of 3 demos green.** 12 real-Codex commits across the matrix. Every change run: `hard_gates=6/6 passed`, 0 open review items, `npm run build` + `tsc --noEmit` clean. Documentation in `docs/EVALUATION.md` + `docs/rc4c-demo-suite-report.md`.

### RC-4D — portfolio packaging (in progress)

This milestone. README rewrite as portfolio entry point, `ARCHITECTURE.md`, `INTERVIEW_STORY.md`, `RESUME_BULLETS.md`, `PROJECT_STATUS.md`. No new runtime features.

---

## Milestone evidence at a glance

| RC milestone | Status | Where to verify |
|--------------|--------|-----------------|
| MVP-1 → MVP-3 (runtime foundation) | ✅ shipped | `orchestrator/core/agentic_runtime.py` + tests/unit/test_agentic_runtime.py (86 tests) |
| MVP-4A (autonomous controller) | ✅ shipped | `orchestrator/core/autonomous.py` + tests/unit/test_autonomous.py (64 tests) |
| MVP-4B/C/D/E/F (integration / corrective / reviews / deploy / smoke) | ✅ shipped | `orchestrator/core/{review_queue,deploy,deploy_vercel,smoke}.py` |
| RC-1 (golden e2e) | ✅ shipped | tests/e2e/test_golden_path.py (2 tests, real artifacts, fakes for Codex/Vercel/HTTP) |
| RC-2A → RC-2E (real-Codex dogfood + hardening) | ✅ shipped | docs/rc2-dogfood-report.md, docs/rc2b-dogfood-report.md, docs/rc2b2-real-codex-success-report.md |
| RC-3A → RC-3E (vertical adapter ladder) | ✅ shipped | docs/rc3a/3b/3c/3d/3e prep + success reports; .dogfood/rc3a.../rc3e... seeds |
| RC-3F (detector adapter probe) | ⏸ paused | docs/rc3f-prep-report.md (seed + script landed; probe not run) |
| RC-4A.1 (Change Request foundation) | ✅ shipped | orchestrator/core/change_*.py + tests/unit/test_change_*.py |
| RC-4A.2 (change-mode wired) | ✅ shipped | orchestrator/core/change_runner.py + tests/e2e/test_change_run_e2e.py |
| RC-4A.3 (real-Codex change run) + RC-4A.3.1 (cleanup) | ✅ shipped | docs/rc4a3-prep-report.md + docs/rc4a3-success-report.md |
| RC-4B (matrix prep) | ✅ shipped | examples/ai-writing-quality-editor/, ai-usage-cost-planner/, agent-review-queue-console/ + docs/demo-matrix.md + scripts/run_demo_suite.sh |
| RC-4C.1 (orchestration cleanup) | ✅ shipped | orchestrator/core/autonomous.py (parser fix), change_contract.py (state fix), scripts/run_demo_suite.sh (gate fix) |
| RC-4C (full matrix real run) | ✅ 3/3 GREEN | docs/EVALUATION.md + docs/rc4c-demo-suite-report.md (12 commits anchored from /tmp/rc4b-* workspaces) |
| RC-4D (portfolio packaging) | 🟡 in progress | README.md, docs/{ARCHITECTURE,INTERVIEW_STORY,RESUME_BULLETS,PROJECT_STATUS}.md |

---

## Test status

```
tests/unit/                                       (production code coverage)
  test_autonomous.py              64 pass         autonomous controller, parser, commit_task
  test_agentic_runtime.py         86 pass         multi-candidate loop, Promotion Gate, eval harness
  test_run_package.py              7 pass         Apply Gate, RunPackage / CandidateReport readers
  test_codex_patch_worker.py      26 pass         Codex CLI adapter (sandbox + approval policy)
  test_change_runner.py           21 pass         change_runner: task-graph builder, swap/restore, eval validation
  test_change_contract.py         20 pass         change-contract: create_change, status state machine
  test_change_request_parser.py   10 pass
  test_change_repo_onboarding.py   9 pass
  test_change_delivery_report.py   8 pass
  test_artifact_validation.py    (covered via callers)
  test_review_queue.py            (covered via test_autonomous + e2e)
  test_rc2c1_fixes.py             15 pass         RC-2C.1 audit-fix regressions
  test_pause_then_render.py        3 pass
  test_backward_compat_session.py 10 pass
  test_next_actions.py            14 pass
tests/e2e/
  test_change_cli_flow.py          2 pass
  test_change_run_e2e.py           3 pass         change-mode happy path + dirty worktree + prior task-graph restoration
  test_golden_path.py              2 pass         RC-1 full requirements→commit→deploy→smoke ladder w/ fakes

Total: 337 tests pass.
```

---

## Strategic pivots recorded in memory

The project's direction shifted twice; both pivots are anchored in memory snapshots. Future Claude sessions should respect these:

- **Dual-purpose pivot (RC-3F.0).** Studio is the system; AI Writing Naturalizer is the product. From RC-3F onward every milestone is BOTH a Studio capability AND a real product MVP step. Locked roles: Chuan = product owner; Claude = orchestrator/synthesizer; Codex = patch worker; Studio = system. Drift signals to watch for documented in `project_dual_purpose_pivot.md`.
- **Change Request + Demo Matrix pivot (post-RC-3F).** "Biggest gap is multi-project + change requests, not more AI capabilities." RC-3F paused, RC-3G/H deferred. New path: RC-4A Change Request Mode → RC-4B 3-project Demo Matrix → RC-4C Demo Suite + EVALUATION → RC-4D Portfolio Packaging. Documented in `project_change_request_pivot.md`.

---

## What's next

### Immediate (post RC-4D)

**RC-5A — AI Writing Quality Editor as a real product case study.** Use Local Agent Dev Studio to drive a multi-change product evolution on the writing editor demo. Goal: prove the Studio works on a single project across multiple iterations (5+ changes), not just one demo + one change. The product framing is "AI-likeness risk reduction + naturalization workflow" (per memory: locked product strategy — NOT bypass). Compliance boundary documented in `project_product_strategy.md`.

### Short-term

- **RC-4E — GitHub PR automation.** Wire the `delivery-report.md` shape into PR descriptions; push the change branch; open a PR; attach evidence as comments. Clean future milestone with no code dependency on the Studio internals.
- **Studio frontend dashboard.** A web UI that reads `.agent/` directly (no new backend; just a viewer for sessions + runs + reviews + artifacts). Optional, low priority.

### Long-term / deferred

- **RC-3F — detector adapter probe.** Resume only if the AI Writing Naturalizer needs detector integration (Sapling / GPTZero / Originality.ai mock-default + real-opt-in pattern is documented).
- **RC-3G — detector-informed rewrite flow.** Capped 2-3 rounds with composite scoring. Belongs after RC-3F.
- **Multi-tenant hosting.** The Studio is local-first by design. Hosting is a separate product, not a Studio extension.

### What's NOT going to happen

- "Build the entire SaaS in one shot" — explicitly off-spec; the Studio is a delivery runtime, not a SaaS factory.
- "Replace `commit_task` trailers with a database" — the audit trail's value is being git-greppable forever; databases optimize for the wrong axis.
- "Make the Promotion Gate use an LLM judge" — defeats the deterministic-audit-trail design (documented in INTERVIEW_STORY.md Q3 + Q5).

---

## Where to read the actual evidence

- `docs/EVALUATION.md` — matrix-level summary, per-demo evidence table, RC-4C.1 cleanup narrative, limitations.
- `docs/rc4c-demo-suite-report.md` — per-demo run-by-run details (commits, runs, artifacts, timings).
- `docs/interview/03-failure-cases.md` — the 8 documented failure modes + fixes + regression tests.
- `docs/ARCHITECTURE.md` — top-level architecture (this milestone).
- `docs/INTERVIEW_STORY.md` — interview narration scripts (this milestone).
- `docs/RESUME_BULLETS.md` — résumé bullet variants (this milestone).
- `examples/<demo>/` — the actual greenfield + change-request inputs used in the matrix.

For pre-RC-4 history (MVP-1 → RC-3E success reports, dogfood reports, prep reports), see `docs/rc*-*.md` files.
