# RC-3E Success Report — Deterministic LLMOps Eval Suite Shape Probe

Date: 2026-05-11.
Status: **Result A** — eval suite scaffolding + post-run artifact pipeline
verified end-to-end on real services. Validates RC-3E.2's patch-generation
runtime fix under live Codex pressure (multi-file patches that previously
broke `git apply --check`).

## Outcome

```
project:           project_dbc2d0c7e3 (AI Writing Humanizer — RC-3E LLMOps Eval)
session:           session_3e00994d99 in /tmp/rc3e-real
session.status:    completed
deployment:        verified
deployment URL:    https://rc3e-llmops-eval-suite-probe-bgrw9mmrf.vercel.app
deployment id:     deployment_ec4bee165f
smoke:             passed (smoke_eaff4341c4)
review queue:      0 open
validate-artifacts: ok=true
Vercel project:    rc3e-llmops-eval-suite-probe (new, in pianxing11281128s-projects)
```

**Per-task git history:**

| Task | Commit | Run ID | Inner-run wall-clock | Decision |
|---|---|---|---|---|
| task-001 Add eval golden dataset loader | `790a8c5` | `run_b880772cf6` | ~5m 14s | promote |
| task-002 Add deterministic eval metrics, CLI, and disable_retrieval flag | `2beab2a` | `run_4e0681aa99` | ~7m 47s | promote |
| task-003 Add frontend Eval Suite section + preserve existing sections | `9472386` | `run_077a712426` | ~4m 3s | promote |

**Integration:** 4 runs, 4 passed. First integration 12.3s (cold `pip install`
for backend venv); subsequent ~7-8s each.

**Post-run `npm run eval` (observe-only verification):**

```
schema_version:     agentic.eval_result.v1
eval_run_id:        eval_2a28b56e9398
case_count:         12
passed / failed:    4 / 8
cost_usd:           0.0
metrics:            structural=12  containment=8  retrieval_hit=8
with_rag_summary:   avg_applied_style_rules=3.0  avg_latency_ms=0.188
without_rag_summary: avg_applied_style_rules=0.0  avg_latency_ms=0.004
```

## Validates

This run validates two distinct things:

### A. RC-3E itself — deterministic LLMOps eval suite shape works end-to-end

- Codex authoring a typed JSONL loader from a committed dataset
  (`backend/app/eval/cases.py`)
- Codex implementing three deterministic per-case scorers
  (`backend/app/eval/metrics.py`)
- Codex extending an existing Pydantic model with an optional field
  (`disable_retrieval: bool = False` on `RewriteRequest`) WITHOUT
  breaking default behavior — the without_rag column literally shows
  `avg_applied=0.0` while the with_rag column shows `avg_applied=3.0`,
  empirically proving the flag short-circuits retrieval as designed
- Codex authoring a CLI that writes a forward-compatible artifact at the
  expected path (`backend/data/eval/runs/eval_<hash>/eval-result.json`)
  with the schema this prep report pinned (incl. `cost_usd: 0.0`)
- Codex preserving TWO previously-built frontend sections (RC-3C
  Processing Service + RC-3D Style Guide RAG) while adding a third
  (Eval Suite)
- The pre-seeded `npm run eval` script + `backend/scripts/eval.sh`
  successfully invoked Codex's `app.eval.cli` module path post-run
- The pre-seeded `backend/pytest.ini` cache redirect (`cache_dir =
  .venv/.pytest_cache`) prevented the `.pytest_cache` leak that
  blocked the original RC-3E run-1
- The "no new pip deps" constraint held — `requirements.txt` is
  byte-identical to RC-3D end state. No `openai`, `numpy`, `pandas`,
  `sklearn`, `langchain`, `ragas`, `deepeval`, `evals`, or
  `sentence-transformers`.

### B. RC-3E.2 patch-generation runtime fix — empirically validates under live load

The original RC-3E run-2 paused at task-002 with `git apply --check
failed: corrupt patch at line 549`. RC-3E.2 replaced
`difflib.unified_diff` with an ephemeral-repo `git diff --binary`
generator and added `patch_apply_check_passed` as a hard gate. This
run is the live test: task-002 produced a multi-file candidate (4
new modules + 1 modified Pydantic file + 4 new test files = ~9
changed entries — exactly the shape that previously triggered the
corruption) and the Apply Gate accepted it cleanly. The
`patch_apply_check_passed` hard gate stayed True throughout (never
needed to fire) — this is the desired backstop posture.

## Predictions vs reality

From `docs/rc3e-prep-report.md` + RC-3E.1 + RC-3E.2 deltas:

| ID | Prediction | Outcome |
|---|---|---|
| **P1** | Codex imports openai/anthropic/langchain/ragas/deepeval/evals | **Wrong.** Stdlib + existing pydantic only. `requirements.txt` UNMODIFIED. |
| **P2** | Codex adds numpy/pandas | **Wrong.** No new pip deps anywhere. |
| **P3** | Codex modifies `rewrite_golden.jsonl` | **Wrong.** Codex respected the input-only constraint. |
| **P4** | Eval CLI nondeterministic (timestamp/random eval_run_id) | **Wrong.** `eval_2a28b56e9398` is content-derived; deterministic. |
| **P5** | Codex adds eval to npm run build / integration | **Wrong.** `build` script unchanged; eval invoked only via post-run `npm run eval`. |
| **P6** | `disable_retrieval` flag exists but retrieval still runs | **Wrong.** With-RAG `avg_applied=3.0` vs without-RAG `avg_applied=0.0` is direct empirical proof the flag short-circuits the retriever. |
| **P7** | Frontend tries to fetch eval artifacts | **Wrong.** Server Component, no fetch — Vercel preview smoke `/` 200. |
| **P8** | `cost_usd` field renamed | **Wrong.** Literal field `cost_usd: 0.0` in artifact. |
| **P9** | RC-3D Style Guide RAG section removed | **Wrong.** All three sections coexist on the deployed page. |
| **P10** | npm run eval works locally but artifact path mismatch with script | **Wrong.** Script's grep at `backend/data/eval/runs/eval_*/eval-result.json` resolved to `eval_2a28b56e9398/eval-result.json` cleanly. |

**0 of 10 predictions fired.**

Plus an implicit **prediction-by-omission was confirmed**: RC-3E.2's
new `patch_apply_check_passed` hard gate stayed True throughout — the
runtime fix held under a multi-file candidate of exactly the shape
that broke the previous run.

## The 4/12 eval pass rate is observe-only signal, not a failure

The eval CLI exited 0 by design (RC-3E locked decision #7:
observe-only, `--strict` is opt-in). The 8 failed cases break down:

- **structural: 12/12** — every response was well-formed (Pydantic
  validation passes, all required fields present)
- **containment: 8/12** — 4 cases failed because the deterministic mock
  processor's `LEXICAL_REPLACEMENTS` only covers a small set of
  substitutions (`utilize`→`use`, etc.). Cases with
  `must_not_contain: ["I hope this message finds you well", "we
  believe", "we strive", "revolutionary", "game-changing",
  "next-generation"]` still contain those phrases in the rewritten
  output because the mock can't do paraphrasing
- **retrieval_hit: 8/12** — 4 cases asked for a specific style guide
  prefix (`professional-email`, `friendly-saas-copy`, `brand-voice`)
  and the token-overlap retriever picked chunks from a different
  guide. Token overlap is coarse; with a small corpus and short
  queries, ranking ties are common

Both failure modes are **expected behaviors of a deterministic mock
processor + token-overlap retriever**. They're useful signal pointing
at where a future RC-3F+ milestone with real LLM-as-judge or richer
retrieval would add value. They do NOT indicate any RC-3E plumbing
defect — the eval suite correctly:
- ran each case
- computed each metric
- recorded both with-RAG and without-RAG results
- wrote a forward-compatible artifact
- exited 0 (observe-only)

## What changed across RC-3E (run-1 → run-2 → run-3)

| Run | Outcome | What was fixed before next run |
|---|---|---|
| run-1 (`session_6bb2dd41ef`?) | Paused at task-002 — Promotion Gate `diff_within_scope=False` (Codex modified package.json + created backend/scripts/eval.sh out of scope) + `.pytest_cache` and generated eval-result.json leaked into patch | **RC-3E.1 seed/scope hygiene fix** (no runtime changes): pre-seeded `npm run eval` script + `backend/scripts/eval.sh` + `backend/pytest.ini` cache redirect; tightened task-002 wording to forbid those edits + require `tmp_path` for test artifacts; pinned eval module path to `backend/app/eval/*` |
| run-2 (paused, never resumed) | Paused at task-002 — Apply Gate `git apply --check failed: corrupt patch at line 549`. Eval / promotion / scope all green; patch was malformed unified diff from `difflib` | **RC-3E.2 runtime fix**: replaced `difflib.unified_diff` with ephemeral-repo `git diff --binary`; added `patch_apply_check_passed` hard gate as backstop; 5 new tests + 192 regression-sweep passing |
| run-3 (this run) | Result A clean pass | — |

## Discipline observations

- **Two real failure modes surfaced and were fixed at the right layer:**
  - run-1 → seed-layer fix (no runtime changes; the gate fired correctly,
    the dogfood seed was the problem)
  - run-2 → runtime fix (the gate was correct in rejecting the corrupt
    patch, but the runtime was the source of the corrupt patch in the
    first place; fix the producer, not the gate)
- **The cumulative discipline keeps paying off.** RC-3E started against
  the most complex baseline yet (Next.js + Prisma + RC-3D RAG + 5 prior
  Python modules) and Codex still authored 9+ new files cleanly with
  zero new pip deps, zero LLM lib imports, and zero out-of-scope
  writes once the seed was clean.
- **Cumulative-runtime-changes ledger across RC-3:**
  - RC-3A: ~2 lines (`*.tsbuildinfo` filter)
  - RC-3B: 0
  - RC-3C: 0
  - RC-3D: 0
  - RC-3E: ~110 lines patch generator rewrite + ~30 lines hard-gate
    additions + 5 tests
- **The hard-gate addition is forward-compatible.** The new
  `patch_apply_check_passed` defaults True for archived candidates
  predating the field, so existing run packages remain readable and
  validate-artifacts continues to pass on them.

## Followups deliberately deferred

- Removing the now-unreferenced `_unified_file_diff` legacy function
  (left in place for one more probe cycle in case any hidden caller
  surfaces; can be deleted in RC-3F prep)
- LLM-as-judge / real OpenAI / real cost values — RC-4+ work
- Cross-run baseline persistence + regression delta computation — RC-3F+
- Strict-mode default-on for the eval gate
- Eval inside `npm run build` chain
- A second probe specifically exercising the `patch_apply_check_passed`
  failure path against a real Codex (would require manufacturing a
  corrupt patch — burns tokens for negative-path validation; defer until
  there's a reason)

## Status lock + next milestone

State: **RC-3E SUCCEEDED, holding.**

Next milestone: **RC-3F — Observability.** NOT started, awaiting
explicit scoping conversation BEFORE prep. RC-3F-and-beyond are step
changes; same SCOPING-then-PREP-then-RUN cadence as RC-3D and RC-3E.

The deferred RC-3F scoping question shape (placeholder for whenever it
gets unlocked):
- What signals to instrument? (request/response counts? p50/p95 latency?
  retrieval hit-rate? eval pass-rate over time?)
- Where to ship metrics? (file-based JSONL log? OpenTelemetry exporter?
  log-only?)
- Who consumes them? (operator inspecting an artifact? a Vercel
  dashboard? a third-party APM?)
- What's deterministic vs operational? (deterministic in-process counters
  are stdlib-only; OTel introduces a heavy dep — RC-3D-style decision)
- Is this the right time to revisit the Vercel `experimentalServices`
  question — i.e. does observability require deploying the FastAPI
  backend so it can emit metrics from a real environment?

These 5 questions must be answered BEFORE RC-3F prep starts.
