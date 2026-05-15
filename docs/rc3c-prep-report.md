# RC-3C Prep Report — FastAPI + Mock LLM Processing Pipeline Probe

Date: 2026-05-11.
Status: **prep complete, NOT yet executed.** Awaiting explicit
"go RC-3C run" signal from operator (after creating a new
`rc3c-fastapi-processing-probe` Vercel project, disabling Deployment
Protection, and updating env file with the corrected recipe — see
operator pre-checklist below).

## Why RC-3C exists

After RC-3A (Next.js shape) and RC-3B (Prisma data model), RC-3C is
the **first second-language / second-service probe**. The runtime
has only seen TypeScript edits driven by Codex; RC-3C asks Codex to
write Python (FastAPI + Pydantic + pytest) alongside continuing
TypeScript / Next.js work.

The "LLM pipeline" framing is **deliberate but not real**. The
backend is structured as if it eventually wraps a real LLM — request
shapes look like rewrite/evaluate APIs, response shapes have
meaning_preservation/clarity/factual_consistency scores — but the
implementation is a deterministic mock. NO `openai`, NO `anthropic`,
NO `litellm`, NO real network calls. Real LLM is RC-3D+ work.

## How RC-3C extends RC-3B

RC-3B's verified end state was preserved:
- Same Next.js 15.5.18 + React 19 + Tailwind + TS 5.7.3 baseline
- Same Prisma 5.22.0 + `@prisma/client` 5.22.0
- **Same 3 verified Prisma models** (`RewriteJob` / `StyleGuide` / `RewriteResult`
  with the back-reference + relation) — copied from `docs/rc3b-success-report.md`
  rather than the empty pre-RC-3B schema. RC-3C must NOT regress these.
- Same `vercel.json: {"framework":"nextjs"}`
- Same `.env DATABASE_URL="file:./dev.db"`
- Same script-fallback discipline (`npm install` if no lockfile)

NEW in RC-3C:
- `backend/` subtree (FastAPI app + pytest)
- `scripts/backend-test.sh` (venv + pip install + pytest runner)
- `package.json` `build` script wraps backend test:
  `"build": "npm run backend:test && prisma generate && next build"`

## Why `build` wraps backend:test (and not a YAML `required_commands` knob)

I checked the runtime: `_build_eval_harness` derives integration commands
from `package.json` script names — only `typecheck` / `build` / `test` /
`test:e2e` are recognized; only `typecheck` and `build` are required by
default. There's no YAML knob for arbitrary "required commands" the way
the user spec implied (`integration.required_commands` in
agent-studio.yaml is not a real key).

Two honest options to gate backend tests:
- **A. Bundle backend:test inside `build`** — failure fails build, fails
  integration, fails Vercel deploy. Vercel build env has python3, so
  pytest runs there too (~30-60s extra deploy time, cached after first).
- **B. Add `required_commands` runtime feature** — speculative product
  code, violates the locked NO-list discipline.

Going with A. Side benefit: a failing backend test ALSO blocks the
Vercel deploy, which is the correct semantic ("don't ship a frontend
documenting a contract the backend can't honor").

`agent-studio.yaml` integration block stays the same shape as RC-3B
(`every_n_tasks: 1`, `run_at_session_end: true`, `timeout_sec: 1200`).
The 1200s (vs RC-3B's 900s) is to absorb the new venv bootstrap on
first run — pip install cold is ~30-60s.

## Dogfood repo path + structure

```
.dogfood/rc3c-fastapi-processing-probe/
  package.json              build wraps backend:test + prisma generate + next build
  next.config.mjs           reactStrictMode (unchanged)
  tsconfig.json             excludes "backend" (Python lives there; tsc shouldn't see it)
  tailwind.config.ts        unchanged
  postcss.config.mjs        unchanged
  vercel.json               {"framework":"nextjs"} (RC-3A learning)
  .gitignore                rc3b's + backend/.venv + backend/__pycache__ + *.pyc + .pytest_cache
  .env                      DATABASE_URL="file:./dev.db" (committed, no real secret)
  app/
    layout.tsx              metadata describes RC-3C
    page.tsx                baseline placeholder; task-003 adds Processing Service panel
    globals.css             unchanged
  prisma/
    schema.prisma           RC-3B's verified 3-model schema (NOT regressed)
  backend/
    requirements.txt        fastapi==0.115.6, pydantic==2.10.4, pytest==8.3.4, httpx==0.28.1
    app/
      __init__.py
      main.py               FastAPI app + GET /health
    tests/
      __init__.py
      test_health.py        TestClient → /health → 200, status=ok
  scripts/
    backend-test.sh         venv bootstrap + pip install + pytest, runs on Mac AND Vercel
  requirements.md           3 H2 tasks (POST /rewrite → POST /evaluate → frontend panel)

scripts/rc3c.sh             dry-run by default, --run executes
docs/rc3c-prep-report.md    this file
```

## FastAPI backend boundary

The backend is **localhost-and-CI only**. RC-3C does NOT deploy it.
The frontend's task-003 documents the contract but does NOT call it
at runtime (no `fetch`, no axios, server-component rendering only).

Choosing a backend deployment target (Render / Fly / Railway / Vercel
serverless functions / Lambda / etc) is **deliberately deferred** to a
later milestone. It's a real decision and should be made once we know
what the LLM-real version (RC-3D+) actually needs (cold-start
sensitivity, streaming, GPU, secrets management, multi-region, etc).
Picking one now would be premature.

## Why LLM is mocked in RC-3C

Two reasons:
1. **Cost discipline.** Real LLM calls during agent-studio runs would
   compound Codex token spend with LLM API spend. RC-3C is about
   verifying the *shape* (cross-language patches, cross-service eval),
   not the model.
2. **Determinism.** A deterministic mock makes integration repeatable.
   A real LLM in the integration command path would create flaky tests.

When RC-3D arrives, the question becomes "where does the real LLM call
live?" — likely in a separate runtime path that's NOT exercised by
integration-time pytest. RC-3C builds the contract; RC-3D fills in
the implementation.

## Local validation (done in prep)

Validated in the sandbox before handing off:
- `bash -n scripts/rc3c.sh` → SYNTAX OK
- Full dry-run with mocked codex/vercel/env → all expected commands print,
  agent-studio.yaml renders cleanly with the corrected schema keys
  (`every_n_tasks`, `expected_status: 200`)
- `npm install` → 114 packages in 31s, clean
- `bash scripts/backend-test.sh` → venv created, pip install, pytest 1
  passed in 0.17s, exit 0
- `npm run typecheck` → clean (tsc --noEmit, backend/ excluded)
- `npm run build` → backend:test ✅, prisma generate ❌ (sandbox network
  blocks `binaries.prisma.sh` → 403; this is a sandbox-only restriction;
  RC-3B proved this exact Prisma version works on Mac and Vercel)

The Prisma engine download issue is reproducible in this Linux sandbox
and was NOT present in RC-3B (which ran on Chuan's Mac). On Mac and
Vercel build env, `prisma generate` works. NOT a seed bug.

## Predictions — what RC-3C is most likely to surface

These are hypotheses to falsify against the real run.

### P1 — `backend:test` may fail due to Python environment / venv script

The backend-test.sh creates a venv and installs requirements. On the
operator's Mac, this should work (python3 + pip3 are standard). On
Vercel build env, pip's cache may not be warm on first deploy →
slower install. If the venv setup fails for any reason (corporate
proxy, pip version too old, fastapi pin incompatible with Python
3.12), the FIRST integration after task-001 will fail at
`backend:test`.

**Triage:** if integration fails with stderr from `bash scripts/backend-test.sh`,
look at venv creation + pip install + pytest output in that order. NOT
a runtime bug.

### P2 — Codex may add real LLM/OpenAI call despite mock-only instruction

The most likely failure. The task wording explicitly says
"deterministic mock processor, NO LLM library imports", but Codex's
training is full of "rewrite" examples that use OpenAI. If Codex
imports `openai` or `anthropic`, the integration will fail with
ImportError (these aren't in `requirements.txt`).

**Triage:** if integration fails with `ModuleNotFoundError: No module
named 'openai'` (or anthropic, etc.), tighten the prompt — push the
"no LLM library imports" language into `_render_patch_worker_prompt`'s
success criteria block. NOT a runtime bug; do not add LLM packages
to requirements.txt as a "fix."

### P3 — Pydantic version mismatch may appear

Pydantic v1 and v2 have substantively different field syntax and
validator patterns. We pinned `pydantic==2.10.4`. Codex's training
spans both — if it generates a `class Config:` instead of
`model_config = ConfigDict(...)`, or `@validator` instead of
`@field_validator`, pytest will fail at import time.

**Triage:** if integration fails with Pydantic-specific syntax errors,
look at the generated `schemas.py` (or wherever Codex put models).
Likely a prompt-side miss; not a seed issue. Could also tighten with
a one-line "Pydantic v2 syntax only" note in the prompt context.

### P4 — Frontend task may try to call backend at runtime

Task-003 explicitly says "documentation only, NO fetch, NO live
calls". But Codex may infer that a "Processing Service panel" should
call the backend. If it does, the frontend will trigger a network
call to `localhost:8000` (or similar) that fails at build time on
Vercel (no backend reachable).

**Triage:** if Vercel build fails with fetch/network errors, check
the frontend task's diff for a fetch() call. Tighten task-003's
success criteria; NOT a runtime bug.

### P5 — Vercel deploy should still pass because frontend-only

Same as RC-3B's success path. The frontend's `next build` should
work; the backend doesn't get deployed; the smoke check tests
`GET /` which is a static page.

**Triage:** if Vercel deploy fails despite frontend-only, look at
Vercel build stderr. Most likely culprit: backend:test failed inside
Vercel's build (P1 family).

### P6 — Context pack may under-rank backend files

`_context_bucket` and `_rank_relevant_files` were tuned for
TypeScript/Next.js project structure. Backend Python files
(`backend/app/main.py`, `backend/tests/test_health.py`) are NOT
recognized as `app_source` / `routes_and_api` / `ui_entrypoints`
buckets — they fall through to `app_source` via the catch-all (line
3375 of agentic_runtime.py). They'd compete with frontend `app/`
files for context_pack slots.

**Triage:** if Codex's task-001 fails because it didn't see
`backend/app/main.py` in context (didn't know FastAPI was already
seeded), this is a context-pack ranking gap. The narrowest fix:
extend `_context_bucket` to recognize `backend/` and similar Python
roots. NOT a "build a Python adapter" justification.

### P7 — Candidate budget should remain one candidate after RC-2C.1

RC-2C.1's fix to `max_candidates_per_task` propagation should still
hold — every task should select `candidate-a` only. If we see
multiple candidates per task in RC-3C, that's a regression from
RC-2C.1.

**Triage:** if status shows multiple candidates per task, check
session.budgets["max_candidates_per_task"] and the candidate budget
propagation path in cli.py / autonomous.py. Should be a no-op since
RC-3A/B both honored the cap.

### P8 — pytest/Next integration may reveal cross-service eval ordering

The `build` script chain is `backend:test && prisma generate &&
next build`. Sequential, single-process. Should be fine. But if
Codex's task-002 introduces a backend test that depends on something
the frontend defines (or vice versa), ordering matters. Unlikely
given the scope splits, but worth flagging.

**Triage:** if integration fails because two tasks' changes interact
unexpectedly across the language boundary, look at the diff of the
last-completed task and the failing eval command. NOT a runtime bug.

## Out of scope for RC-3C — explicitly deferred

- Real OpenAI / Anthropic / any LLM API calls
- Any network call beyond pip install + Vercel deploy
- API keys (no .env entries beyond DATABASE_URL)
- RAG / embeddings / vector store / pgvector
- Stripe / billing
- Auth / user models / sessions
- Database expansion beyond RC-3B's 3 models
- File uploads
- Real FastAPI deployment (Render / Fly / Railway / Vercel functions / Lambda)
- Backend cloud provider selection
- Docker / container deploy
- MCP / A2A
- Dashboard
- `autonomous.py` refactor
- General FastAPI adapter
- General Prisma adapter
- Promotion gate / review queue semantic changes
- RC-3D+ scaffolding

## Operator pre-checklist (do BEFORE `--run`)

Same shape as RC-3B's checklist, with the **corrected env-rewrite recipe**
(per RC-3B operator gotcha — `source` overwrote new exports with old
values, making deploy land in the wrong project).

1. **Create a NEW Vercel project** named `rc3c-fastapi-processing-probe`
   in the `pianxing11281128s-projects` scope. Either via
   `vercel link` from the dogfood directory, or via dashboard.
2. **Disable Vercel Authentication** for the new project: Settings →
   Deployment Protection → Vercel Authentication → Disabled.
3. **Update `~/.local-agent-vercel.env` — corrected recipe (do NOT
   `source` the old file before writing):**

   ```bash
   cd ~/Documents/LocalAgents/.dogfood/rc3c-fastapi-processing-probe
   vercel link  # creates .vercel/project.json with new IDs
   # Read OLD token (it's still valid; only PROJECT_ID + ORG_ID change)
   OLD_TOKEN="$(grep '^export VERCEL_TOKEN=' ~/.local-agent-vercel.env | sed -E "s/.*='([^']+)'/\1/")"
   NEW_PROJECT_ID="$(python3 -c "import json; print(json.load(open('.vercel/project.json'))['projectId'])")"
   NEW_ORG_ID="$(python3 -c "import json; print(json.load(open('.vercel/project.json'))['orgId'])")"
   cat > ~/.local-agent-vercel.env <<EOF
   export VERCEL_TOKEN='$OLD_TOKEN'
   export VERCEL_ORG_ID='$NEW_ORG_ID'
   export VERCEL_PROJECT_ID='$NEW_PROJECT_ID'
   EOF
   chmod 600 ~/.local-agent-vercel.env
   ```

4. **Verify**: deployment URL after the run should start with
   `rc3c-fastapi-processing-probe-...` not `rc3a-...` or `rc3b-...`.
5. Run `cd ~/Documents/LocalAgents && ./scripts/rc3c.sh` (dry-run first to confirm).
6. Then `./scripts/rc3c.sh --run 2>&1 | tee /tmp/rc3c-run.log`.

## What success looks like (Result A)

- `autonomous start` completes (status=completed, no pause).
- 3 commits on `agent-studio/session-*` branch with evidence trailers
  + `Patch-Worker: codex`.
- Each task selected `candidate-a` only (budget cap honored).
- Integration ran 4 times (3 periodic + 1 session_end), each passing
  `npm run backend:test && prisma generate && next build`.
- Final state:
  - `backend/app/main.py` has `/health`, `/rewrite`, `/evaluate`
  - `backend/tests/` has tests for all three endpoints
  - `requirements.txt` UNMODIFIED (Codex didn't add openai etc.)
  - `app/page.tsx` (or new component) has Processing Service panel
  - Prisma schema UNCHANGED (3 RC-3B models intact)
- `deployment.json` status=`ready`, deployment URL printed
  (and starts with `rc3c-fastapi-processing-probe-...`).
- `smoke-check.json` status=`passed`, home `/` returned 200.
- `validate-artifacts --json` ok=true.
- 0 open review items.

If we land Result A, RC-3C is verified and the next milestone is
**RC-3D (Style Guide RAG)**. If anything in P1-P8 surfaces, the
allowed fixes are narrow: prompt / context-pack / repair-loop /
eval-wiring / changed-files classification.

## Hold state

Prep is complete and waiting. No runtime / dogfood / script changes
beyond what's listed above. I will not start the run, edit runtime
code, or pre-fix any P1-P8 prediction without first seeing the run
produce real artifacts.
