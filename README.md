# Local Agent Dev Studio

> **AI-native software delivery runtime.** You write a `requirements.md` (or a `change-request.md` for an existing project). It drives a real Codex agent through a deterministic pipeline — decompose, generate patches, score against 12 hard rules, apply through 10 more, real `git commit`, schema-validated evidence trail. **3 of 3 demos green** end-to-end on real Codex. No black box.

## Why this exists (vs. just using Claude Code or Codex directly)

Codex (and Claude Code) can write code. They're real coding agents. **Local Agent Dev Studio uses Codex as its patch worker** — the model is what types. Studio is the **runtime around it**: requirements → task graph → context pack → eval harness → Promotion Gate → Apply Gate → real `git commit` → schema-validated `applied-change.json` + `delivery-report.md`.

The line that explains this in one sentence:

> **The model is not the system. The delivery loop is the system.**

If you let a coding agent run unattended for hours, you usually get "a pile of code changes plus a model summary." That's fine for one-off help; it's not enough for delivery. Studio answers the questions a reviewer (or a future you) actually needs answered:

- Which files changed? (`changed-files.json`)
- Were any of them outside scope? (Promotion Gate `diff_within_scope`)
- Did the build and typecheck pass on the patched tree? (`eval-results.json`)
- Did the patch apply cleanly against the right base commit? (Apply Gate `git apply --check`)
- Did the agent silently edit `package.json` or add a dependency? (out-of-scope refusal)
- Which commit corresponds to which task / change? (`Agent-Task-ID` / `Change-Id` git trailers)
- If something breaks later, how do I trace which task introduced it? (`promotion-report.json` per run)
- For a follow-up change request, where's the contract / scope / acceptance? (`change-contract.json`)
- For an interview or audit, where's the evidence? (`docs/EVALUATION.md` + the on-disk artifact trail)

Studio's value is **controlled autonomy with full evidence**, not "AI writes more code." `Claude/Codex = patch worker; Studio = delivery runtime.`

## What this is

Most "AI coding agents" today are either in-IDE copilots that leave the operator to accept every diff, or end-to-end auto-shippers that produce something live but un-reviewable. Local Agent Dev Studio sits in the middle: **autonomous enough to drive multi-task delivery without a human in the loop, but every decision is gated by deterministic Python rules and every artifact is schema-validated** — a reviewer can `git log --grep "Change-Id:"` and reconstruct exactly what happened and why.

Two entry points, identical interior:

- **Greenfield** — `agent-studio autonomous start` reads `requirements.md`, decomposes it into a task graph, drives Codex through each task with eval / Promotion Gate / Apply Gate / commit. Per-task git commits with `Agent-Task-ID` trailers.
- **Change Request Mode** — `agent-studio change run latest` reads a `change-request.md` against an existing project, builds a 1-task task graph, drives the same machinery, leaves a real commit on `agentic/change/<change_id>` with `Change-Id` + `Source-Change-Request` trailers.

Both write `applied-change.json` (`agentic.applied_change.v1`) + `delivery-report.md` + a per-run package containing `promotion-report.json`, `patch.diff`, `score.json`, `changed-files.json`, `eval-results.json`, and per-critic findings.

## Demo matrix — 3 / 3 green

Three distinct Next.js + TypeScript projects. Same orchestration. Real Codex on every task. Full evidence in [`docs/EVALUATION.md`](docs/EVALUATION.md) and [`docs/rc4c-demo-suite-report.md`](docs/rc4c-demo-suite-report.md).

| Demo | Greenfield | Change Request | Build | Artifacts |
|------|-----------|----------------|-------|-----------|
| AI Writing Quality Editor | ✅ 3 commits | ✅ Add clarity score 0-100 (`15864c9`) | ✅ pass | ✅ ok=true |
| AI Usage & Cost Planner | ✅ 3 commits | ✅ Add budget warning + break-even (`1f15c39`) | ✅ pass | ✅ ok=true |
| Agent Review Queue Console | ✅ 3 commits | ✅ Add SLA risk badges (`63979f5`) | ✅ pass | ✅ ok=true |

12 real-Codex commits across the matrix. Every change run: `hard_gates=6/6 passed`, 0 open review items, `npm run build` + `npm run typecheck` clean.

## Core flow

```
requirements.md / change-request.md
        │
        ▼
deterministic decomposer (pure Python regex, no LLM)
        │
        ▼
task graph (JSON; reproducible)
        │
        ▼
AutonomousController.advance_one_task
        │
        ▼
AgenticProjectRuntime  ← real Codex (sandbox=workspace-write, approval=on-request)
        │
        ▼
per-candidate {patch.diff, score.json, eval-results.json, critics/*.md}
        │
        ▼
Promotion Gate (12 deterministic hard rules)
        │   decision ∈ { promote, needs-human-review, abandoned }
        ▼
Apply Gate (10 more rules; git apply --check then real git apply)
        │
        ▼
real git commit on agentic/{autonomous,change}/<id> with provenance trailers
        │
        ▼
applied-change.json + delivery-report.md + change validate ok=true
```

### Promotion Gate (12 hard rules, in `agentic_runtime.py`)

`source_patch_present` · `diff_within_scope` · `patch_apply_check_passed` · `required_eval_declared` · `required_eval_executed` · `required_eval_passed` · `no_critical_security_finding` · `no_critical_regression_finding` · `no_overfit_to_evals` · `out_of_scope_change_count == 0` · `patch_size_within_budget` · `abandonment_history_clear`

Output: `promotion-report.json` (schema `agentic.promotion_report.v2`) with the per-rule pass/fail breakdown. Decision `promote` requires every gate pass. Anything else routes to the human-in-the-loop review queue.

### Apply Gate (10 rules, in `run_package.py::apply_selected_candidate`)

Re-checks safety from the live-git side at apply time: schema match, base commit equality with current HEAD, worktree clean (modulo `.agent/` + `task-graph.json`), `git apply --check` exits 0, no out-of-scope mutations, re-apply guard. A candidate that scored "promote" yesterday can fail Apply today if HEAD moved — measured separately on purpose.

## Quickstart

```bash
# 1. Initialize the workspace (one time)
./agent-studio init

# 2. Greenfield: write requirements.md, ingest it
cp examples/ai-writing-quality-editor/requirements.md ./requirements.md
./agent-studio new --from requirements.md
./agent-studio autonomous start          # real Codex required

# 3. Change Request Mode: write change-request.md, run it
cp examples/ai-writing-quality-editor/changes/01-add-clarity-score.md ./cr.md
./agent-studio change new --from ./cr.md
./agent-studio change run latest         # real Codex required

# 4. Verify: every artifact validates
./agent-studio change validate latest --json
./agent-studio autonomous validate-artifacts --json

# 5. Run the full 3-demo matrix (real Codex; ~150-250k tokens; ~45-55 min)
scripts/run_demo_suite.sh                # dry-run by default
scripts/run_demo_suite.sh --run          # execute
scripts/run_demo_suite.sh --demo=ai-writing-quality-editor --run
```

The runner (`scripts/run_demo_suite.sh`) gates change-mode on greenfield completion: if `agent-studio autonomous start` doesn't reach `status="completed"`, it skips `change new` / `change run` and exits non-zero in single-demo mode. Worktree-clean preflight + `agent-studio.yaml` deploy-disabled grep-assert.

## Tests

```bash
python -m pytest tests/unit/test_change_runner.py tests/unit/test_change_contract.py \
                 tests/unit/test_change_request_parser.py tests/unit/test_change_repo_onboarding.py \
                 tests/unit/test_change_delivery_report.py tests/unit/test_artifact_validation.py \
                 tests/unit/test_autonomous.py tests/unit/test_agentic_runtime.py \
                 tests/unit/test_run_package.py tests/unit/test_codex_patch_worker.py \
                 tests/e2e/test_change_run_e2e.py tests/e2e/test_change_cli_flow.py \
                 tests/e2e/test_golden_path.py
```

**337 tests pass** (RC-4A focused + autonomous + agentic + run_package + artifact_validation + codex_patch_worker + e2e golden + RC-4C.1 cleanup regression).

## Repo layout

```
orchestrator/
  cli.py                          agent-studio <subcommand> handlers
  core/
    autonomous.py                 task-graph parser + AutonomousController + commit_task
    agentic_runtime.py            multi-candidate Codex inner loop + Promotion Gate
    run_package.py                apply_selected_candidate (Apply Gate) + run-package readers
    change_runner.py              run_change (change-mode entry) + task-graph hygiene + delivery
    change_contract.py            change-mode artifact bootstrap + status state machine
    change_request_parser.py      change-request.md parser
    change_repo_onboarding.py     repo scan (stack, scripts, layout, last 5 commits)
    change_delivery_report.py     delivery-report.md renderer
    artifact_validation.py        every schema validator
    codex_patch_worker.py         Codex CLI adapter
    review_queue.py               human-in-the-loop review queue + resume gating
examples/
  ai-writing-quality-editor/      Demo 1 — greenfield + change request
  ai-usage-cost-planner/          Demo 2 — greenfield + change request
  agent-review-queue-console/     Demo 3 — greenfield + change request
scripts/
  run_demo_suite.sh               full 3-demo runner (dry-run default; --run to execute)
docs/
  EVALUATION.md                   matrix-level evidence (3/3 green, RC-4C.1 fixes, limitations)
  rc4c-demo-suite-report.md       run-by-run evidence (commits, artifacts, timings)
  ARCHITECTURE.md                 top-level architecture walkthrough
  INTERVIEW_STORY.md              30s / 1min / 5min narration scripts
  RESUME_BULLETS.md               3-bullet + 5-bullet + AI-Engineer / Full-stack-AI variants
  PROJECT_STATUS.md               completed RC milestones + next steps
  demo-matrix.md                  RC-4B prep doc + RC-4C.1 cleanup amendment
  interview/
    01-project-summary.md         plain-English summary + glossary + Q&A
    02-architecture-walkthrough.md component-by-component with ASCII diagrams + data flow trace
    03-failure-cases.md           8 real failure modes + symptom/cause/fix/test
    04-demo-matrix-story.md       narrative for telling the demo story
tests/
  unit/, e2e/, fixtures/          337 tests pass
```

## Honest limitations

- **No production SaaS yet.** This is a delivery runtime, not a hosted product. Run it locally; the CLI is the surface.
- **No Studio frontend.** Everything is `agent-studio <subcommand>` plus on-disk artifacts under `.agent/`. A dashboard is plausible future work but not built.
- **No GitHub PR automation.** The runtime leaves a real local commit on `agentic/change/<change_id>`. It does not push, open a PR, or attach the delivery report as a PR comment. The `delivery-report.md` already has the right shape to become a PR description — wiring that to GitHub is a clean future milestone.
- **Demos are local Next.js apps with `localStorage` only.** No backend, no DB, no auth, no fetch. The Studio supports backends (RC-3C / RC-3D dogfoods exercised FastAPI + Prisma greenfield), but the matrix optimized for portfolio readability.
- **Vercel disabled in RC-4C.** The deploy adapter (Vercel preview + smoke check + rollback) was exercised in earlier RC-2 / RC-3 dogfoods and works — but `agent-studio.yaml` ships with `deploy: { enabled: false }` for the demo matrix to keep the surface area focused on greenfield + change-mode, not deploy mechanics. RC-4D portfolio packaging is a natural place to add live preview URLs if useful.
- **Single-candidate per task in the demo matrix.** `max_candidates_per_task: 1` keeps token spend bounded. The multi-candidate path (3 strategies — fast / conservative / test-focused — with Promotion Gate selecting a winner) is exercised in earlier RC-3 dogfoods. Bumping to 3 is a config flip, not a code change.

## Where to read next

| Audience | Start here |
|----------|-----------|
| Reviewer / interviewer | [`docs/EVALUATION.md`](docs/EVALUATION.md) → [`docs/interview/04-demo-matrix-story.md`](docs/interview/04-demo-matrix-story.md) |
| Designing a system on top of it | [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) → [`docs/interview/02-architecture-walkthrough.md`](docs/interview/02-architecture-walkthrough.md) |
| Skeptical "does it actually work?" | [`docs/rc4c-demo-suite-report.md`](docs/rc4c-demo-suite-report.md) → [`docs/interview/03-failure-cases.md`](docs/interview/03-failure-cases.md) |
| Recruiter / hiring manager | [`docs/RESUME_BULLETS.md`](docs/RESUME_BULLETS.md) |
| Project history | [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) |

## Detailed reference

The detailed phase-by-phase MVP history (MVP-1 through MVP-4F, RC-1 through RC-4C.1) was the working journal that produced the runtime. It is preserved verbatim in `docs/PROJECT_STATUS.md` and as memory snapshots under the workspace's persistent memory dir. New readers should not need it; reviewers comparing the design against the implementation may find it useful.

---

**License & contact:** Personal portfolio project by Chuan Qiao (`chuanqiao1128@gmail.com`). Repository is intended for review and discussion; not packaged as an installable library.
