# RC-3D Success Report — Style Guide RAG Shape Probe End-to-End

Date: 2026-05-11.
Status: **Result A** — first content/data-flow probe (deterministic
Style Guide RAG pipeline) passed end-to-end on real services; no
runtime changes required.

## Outcome

```
project:           project_a841c64d1f (AI Writing Humanizer — RC-3D Style Guide RAG)
session:           session_227f6040e7 in /tmp/rc3d-real
session.status:    completed
deployment:        ready
deployment URL:    https://rc3d-style-guide-rag-probe-av1toyb7e-pianxing11281128s-projects.vercel.app
deployment id:     deployment_b505be9a4d
smoke:             passed (smoke_4b98d2c24e)
review queue:      0 open
validate-artifacts: ok=true
Vercel project:    rc3d-style-guide-rag-probe (new, in pianxing11281128s-projects)
```

**Per-task git history:**

| Task | Commit | Run ID | Decision | Candidate |
|---|---|---|---|---|
| task-001 Add local style guide ingestion and chunking | `5cc3769` | `run_4a8ef4ba50` | promote | candidate-a |
| task-002 Add deterministic style rule retrieval | `e438c7d` | `run_6d8282fe18` | promote | candidate-a |
| task-003 Use retrieved style rules in /rewrite + add frontend Style Guide RAG section | `2a3c5db` | `run_715ad257ea` | promote | candidate-a |

**Integration:** 4 runs, 4 passed. Each run executed `npm run build`
which expanded to `npm run backend:test && prisma generate && next
build`. All three new tasks landed without backend test regression
across all 4 integration cycles.

## Validates

This is the first run of the runtime against a project that involves
**content/data flow** (vs RC-3A/B/C which were structural). Specifically:

- **Codex implemented a deterministic RAG pipeline end-to-end** —
  ingestion (`load_style_guides`), chunking (`chunk_style_guides`),
  retrieval (`retrieve_style_rules`), and `/rewrite` integration —
  using only Python stdlib + the existing fastapi/pydantic deps.
  No new pip dependencies were added.
- **Codex extended an existing endpoint without regression** —
  `POST /rewrite`'s response shape gained `applied_style_rules: list[str]`
  alongside all RC-3C-inherited fields (`rewritten_text`,
  `change_summary`, `meaning_preservation_score`, `clarity_score`,
  `style_match_score`, `warnings`). The pre-existing
  `assert set(body) == {...}` test was correctly updated rather than
  broken.
- **Codex preserved an existing module under multi-task pressure** —
  `processor.py`'s `evaluate_text`, `LEXICAL_REPLACEMENTS`, and tone
  helpers (RC-3C output) remained intact through 3 sequential edits.
- **Codex authored a Server-Component frontend section** without
  introducing client-side fetch — Vercel preview smoke `/` 200 OK.
- **The "no new pip deps" prompt constraint held** under repeated
  pressure (3 Python tasks could each have been "an excuse" to add a
  vector library; none did — `requirements.txt` is byte-identical to
  the RC-3C end state).

## Predictions vs reality

From `docs/rc3d-prep-report.md`:

| ID | Prediction | Outcome |
|---|---|---|
| **P1** | Codex imports openai/anthropic/sentence-transformers/numpy/sklearn/langchain | **Wrong.** stdlib only. `requirements.txt` UNMODIFIED. |
| **P2** | Codex stores chunks/index outside `backend/` | Did not fire. Stayed in scope. |
| **P3** | Chunk IDs unstable (uuid/random) | Did not fire. Determinism check passed. |
| **P4** | Retrieval scoring nondeterministic | Did not fire. Same input → same output. |
| **P5** | Codex adds disruptive unrequested endpoint | Did not fire. |
| **P6** | Frontend tries to fetch backend at runtime | Did not fire. Server-component, no fetch. |
| **P7** | context_pack under-ranks `backend/data/style_guides/*.md` | Did not fire. Codex saw the seed files and used them correctly. |
| **P8** | Codex modifies `backend/requirements.txt` to add ML lib | **Wrong.** Codex respected the stdlib-only constraint. |
| **P9** | Vercel `experimentalServices` accidentally activates backend deploy | See observation below — Vercel CLI did re-inject the block during operator's `vercel link`; smoke still passed against the Next.js frontend at `/`. |
| **P10** | backend:test passes locally but fails on Vercel build | Did not fire. Vercel's Python image handled venv + pip + pytest cleanly. |

**0 of 10 predictions fired in any way that affected the run.** P9 has
a related observation (below) but did not block the success criteria.
This continues the trend: RC-3A (5 reruns / 6 branches), RC-3B
(0 reruns / 0 branches), RC-3C (0 reruns / 0 branches),
RC-3D (0 reruns / 0 branches).

## Observation — Vercel auto-injection of multi-service config (NOT fixed)

During the operator's `vercel link` step for the rc3d-style-guide-rag-probe
project, Vercel CLI again auto-detected both `frontend` (Next.js) and
`backend` (FastAPI) services and added an `experimentalServices` block
to the dogfood `vercel.json`, the same behavior observed in RC-3C.

RC-3D's locked decision was **"FastAPI stays local-only, validated
through `backend:test`"**. The Vercel-side multi-service detection is
out of band from that decision — and **does not invalidate this run's
success**, because:

- Smoke check tested `GET /` only (Next.js frontend route), which
  returned 200.
- All RAG validation happened inside `backend:test` (pytest in the
  local venv inside integration), which passed 4/4.
- Whether Vercel actually deployed the FastAPI service alongside the
  frontend was not asserted by RC-3D. If it did, that's a free
  artifact; if it didn't, nothing changes.

**Action: do NOT fix now.** Record as a deployment-scope observation
for RC-3E / RC-4 discussion. Open questions for that future scoping:
- Does the operator want backend deployed via Vercel `experimentalServices`
  (the simplest path, but couples the project to a Vercel-specific
  preview model)?
- Or does the operator want a separate backend host (Render / Fly /
  Vercel serverless functions / ...) — the original RC-3C "deferred"
  question?

This is not an RC-3E *eval* concern, but it is a runtime-shape concern
that may surface during RC-3E if eval needs to call the real deployed
backend.

## Discipline observations

- **0 of 10 predictions fired.** Same shape as RC-3B and RC-3C — the
  cumulative context built up across RC-3A's shakedown continues to
  pay off.
- **0 runtime changes.** No `_discover_files` tweak, no prompt
  template change, no eval harness adjustment. Only dogfood seed +
  script + reports.
- **First content/data-flow probe in the project.** RC-3A/B/C tested
  *structure* (Next.js shape, Prisma schema, cross-language patches).
  RC-3D tested *content flow* (committed markdown → chunked
  in-memory → retrieved by deterministic scoring → surfaced in API
  response → documented in UI). The runtime treated this exactly the
  same way it treats structural tasks; no special "RAG awareness"
  was needed anywhere.
- **The "no new pip deps" prompt constraint held under multi-task
  pressure.** Two earlier Python tasks (RC-3C tasks 1+2) had no
  reason to add LLM libs; RC-3D's 2 Python tasks specifically asked
  for "RAG" and "retrieval" — exactly the wording that would tempt
  pretrained models toward vector DB + embeddings imports. Codex
  resisted in both. This is a non-trivial signal about the prompt's
  current robustness.

## Followups deliberately deferred

- Vercel `experimentalServices` block — leave inert; let RC-3E or
  RC-4 scoping decide whether to embrace or strip.
- `package-lock.json` for the rc3d dogfood — same deferral as
  RC-3A.6 / RC-3B / RC-3C (npm install fallback works).
- Real embeddings, real LLM, vector DB, RAG framework — RC-4+ work.
- LLM-judged eval / golden dataset / batch eval / regression gate —
  RC-3E scoping.

## Status lock + next milestone

State: **RC-3D SUCCEEDED, holding.**

Next milestone: **RC-3E — LLMOps Eval Suite.** NOT started, awaiting
explicit scoping conversation BEFORE prep. RC-3E introduces meaningful
new complexity:
- golden dataset (where does it live? committed seed? generated?)
- batch eval (how does the runtime invoke it? new agent-studio CLI? in-band integration?)
- quality metrics (LLM-as-judge requires real LLM — would be the first real-LLM stage)
- prompt comparison (does each task produce a candidate evaluated against a baseline?)
- with-RAG vs without-RAG (cross-references RC-3D output)
- cost/latency reporting (new artifact schema)
- regression gate (does eval failure block promotion?)

These cross-cut more than one runtime layer — RC-3E should get its
own scoping pass with Chuan before any prep work begins.
