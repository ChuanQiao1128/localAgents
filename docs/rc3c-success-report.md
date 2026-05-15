# RC-3C Success Report — FastAPI + Mock LLM Processing Pipeline End-to-End

Date: 2026-05-11.
Status: **Result A** — first cross-language probe (TypeScript + Python)
passed end-to-end; all 8 P1-P8 predictions falsified; ~zero runtime
changes required.

## Outcome

```
project:           project_2073109d64 (AI Writing Humanizer — RC-3C FastAPI)
session:           session_60a9b731f6 in /tmp/rc3c-real
session.status:    completed
deployment:        verified (after operator-side project link correction)
deployment URL:    https://rc3c-fastapi-processing-probe-ozbhttdd7.vercel.app
deployment id:     deployment_f9b1fb3990
smoke:             passed (smoke_0e735586be, GET / → 200)
review queue:      0 open
validate-artifacts: ok=true
Vercel project:    rc3c-fastapi-processing-probe (new, in pianxing11281128s-projects)
```

**Per-task git history:**

| Task | Commit | Run ID | Decision | Candidate |
|---|---|---|---|---|
| task-001 Add rewrite request/response schemas and POST /rewrite endpoint | `159fed3` | `run_18fb21115e` | promote | candidate-a |
| task-002 Add /evaluate endpoint for rewrite output quality scoring | `eb55694` | `run_a820f0ea2f` | promote | candidate-a |
| task-003 Add frontend Processing Service panel for the FastAPI rewrite service | `41724e9` | `run_bbb778a76f` | promote | candidate-a |

**Integration:** 4 runs, 4 passed, `command_count=2`. Each run executed
`npm run build` which expanded to
`npm run backend:test && prisma generate && next build`. Backend pytest
participated in every integration. Final session_end integration also
passed.

## Validates

This is the first run of the runtime against a project that crosses
language boundaries:

- **Codex authored Python** (FastAPI route handlers, Pydantic models,
  pytest tests) on tasks 001 and 002, then **TypeScript** on task 003 —
  same session, same patch-worker, same single integration command.
- **Cross-service eval gate** (pytest + next build) chained inside one
  `npm run build` succeeded across all 3 task boundaries.
- **Backend test participated in deploy chain** — Vercel build executed
  `npm run build` which ran pytest before `next build`, proving the
  Vercel build image has python3 + pip and can host the venv bootstrap.
- **Existing Prisma data model preserved** — RC-3B's `RewriteJob` /
  `StyleGuide` / `RewriteResult` schema remained intact across all 3
  tasks (Codex stayed within `backend/**` for tasks 1 & 2, and within
  `app/**, components/**, backend/**` for task 3 without regressing
  `prisma/`).

## Predictions vs reality

From `docs/rc3c-prep-report.md`:

| ID | Prediction | Outcome |
|---|---|---|
| **P1** | backend:test venv/Python flakiness | Did not fire — venv bootstrap clean on Mac and Vercel build env. |
| **P2** | Codex imports `openai`/`anthropic`/`litellm` despite mock-only | **Wrong.** Codex respected the deterministic-mock instruction across both backend tasks. |
| **P3** | Pydantic v1 syntax mismatch | Did not fire. Codex used Pydantic v2 idioms (consistent with `pydantic==2.10.4` pin). |
| **P4** | Frontend task tries to fetch backend at runtime | Did not fire. Task-003 produced documentation-only panel, no fetch calls. |
| **P5** | Vercel deploy fails on backend:test step | Did not fire. After project-link correction, Vercel build executed the full chain cleanly. |
| **P6** | context_pack under-ranks `backend/` Python files | Did not fire. Codex saw enough backend context to author files in the right places. |
| **P7** | Candidate budget regression | Did not fire. All tasks selected `candidate-a` only — RC-2C.1 cap still working. |
| **P8** | Cross-service eval ordering | Did not fire. Sequential `backend:test && prisma generate && next build` chain held across all 3 tasks. |

**0 of 8 predictions fired.** Same outcome class as RC-3B (also clean
first-pass on the schema corrections after RC-3A's heavier first-time
context-build).

## Operator-side issues (NOT runtime bugs)

### Initial deploy went to wrong Vercel project

Same root cause as the RC-3B gotcha — `VERCEL_PROJECT_ID` in
`~/.local-agent-vercel.env` still pointed at `rc3a-saas-shape` when
the first RC-3C run executed, so the initial automated deploy landed
in that project. After:

1. `vercel link` from
   `.dogfood/rc3c-fastapi-processing-probe/` linking a NEW project
   `rc3c-fastapi-processing-probe`,
2. updating `~/.local-agent-vercel.env` (corrected recipe per RC-3B
   gotcha — no `source` of old file before `cat >`),
3. manual `vercel deploy` to the right project + manual smoke,

the final state is `Deployment status=verified`, `Smoke status=passed`,
deployment URL hosted under the correct `rc3c-fastapi-processing-probe`
project. The runtime did exactly what it was told both times — first
with stale env, then with correct env.

### Vercel auto-modified `vercel.json` during `vercel link`

`vercel link` detected the `backend/` directory as a Python service and
appended an `experimentalServices` block to `vercel.json`:

```json
{
  "framework": "nextjs",
  "experimentalServices": {
    "frontend": { "routePrefix": "/", "framework": "nextjs" },
    "backend":  { "entrypoint": "backend", "routePrefix": "/_/backend" }
  }
}
```

This is Vercel's emerging **multi-service deployments** feature — it
auto-detected the FastAPI structure and offered to route `/_/backend`
to it. RC-3C's design intentionally said "FastAPI is local-only,
backend NOT deployed." Vercel disagreed and tried to deploy it
anyway via this experimental block.

**Decision (per locked spec): NOT reverting now.**
- The current deployed state (with this vercel.json) passed smoke.
- The smoke check only tests `GET /`, which still hits the Next.js
  frontend; the experimental `/_/backend` route is incidental.
- Reverting now would (a) divert into a Vercel CLI behavior debate
  and (b) risk breaking the verified deploy.
- This becomes a real **observation for RC-3D / future RC-3C re-runs**:
  the Vercel platform has effectively answered the "where do we deploy
  the FastAPI backend?" question we'd deferred. RC-3D may want to
  either embrace this (and gain a free hosted backend) or override it
  (and stay local-only). That's an RC-3D scoping decision, not an
  RC-3C cleanup task.

## Discipline observations

- **8 predictions, 0 fired.** Same shape as RC-3B. The cumulative
  context built up across RC-3A's 5-rerun shakedown continues to pay
  off — every subsequent probe surfaces fewer real failures because
  the dogfood-seed + script + runtime calibration is increasingly
  well-tuned.
- **Zero runtime changes.** No `_discover_files` tweak, no prompt
  template change, no eval harness adjustment. The only churn is in
  dogfood seed + script + reports.
- **Cross-language Codex output worked first try.** This is the most
  notable signal in RC-3C: Codex authored idiomatic Pydantic v2 + 
  pytest in tasks 1-2, then idiomatic Next.js Server Component in
  task 3, all within one autonomous session, all promoted by the
  same Promotion Gate against the same eval harness shape.
- The "backend wraps inside `npm run build`" gating pattern (vs a
  speculative `required_commands` runtime feature) is now empirically
  validated end-to-end. Failed pytest WOULD fail integration AND
  Vercel deploy. It just didn't have the chance to here.

## Followups deliberately deferred

- `vercel.json experimentalServices` block — leave as-is; let RC-3D
  decide whether to embrace or strip it.
- `package-lock.json` for the rc3c dogfood — same deferral as RC-3A.6
  / RC-3B (npm install fallback works).
- Backend deployment target choice (Render / Fly / Vercel
  experimentalServices / etc) — RC-3D scoping decision.
- Real LLM library + API keys — RC-3D+ work.
- RAG / embeddings / vector store — RC-3D scoping.

## Status lock + next milestone

State: **RC-3C SUCCEEDED, holding.**

Next milestone: **RC-3D — Style Guide RAG.** NOT started, awaiting
explicit go signal AND a separate scoping conversation. RC-3D
introduces meaningful new complexity — document ingestion, chunking,
embeddings, vector store / pgvector, retrieval, metadata, style rules,
RAG evaluation. That's a step change vs RC-3A → RC-3B → RC-3C, each
of which only added one new dimension. RC-3D should get its own scope
discussion before any prep work begins.
