# RC-3D Prep Report — Style Guide RAG Shape Probe

Date: 2026-05-11.
Status: **prep complete, NOT yet executed.** Awaiting explicit
"go RC-3D run" signal from operator (after creating new
`rc3d-style-guide-rag-probe` Vercel project, disabling Deployment
Protection, and updating env file with the corrected recipe).

## Goal

Verify Local Agent Dev Studio can drive Codex to implement a
**minimum closed-loop Style Guide RAG pipeline** inside the existing
RC-3C FastAPI backend — local markdown style guide ingestion →
chunking → deterministic retrieval → applied style rule IDs surfaced
in `/rewrite` → frontend Style Guide RAG section. NO real embeddings,
NO real LLM, NO new ML pip deps, NO pgvector, NO file upload.

This is the first probe involving content/data flow (vs RC-3A/B/C
which were structural). Same dogfood discipline: one new dimension
only — a deterministic, dependency-free RAG shape on top of the
already-verified two-language stack.

## Locked decisions (from scoping)

| # | Decision | Locked value |
|---|---|---|
| 1 | Vector store | Local JSONL / in-memory index — NO pgvector |
| 2 | Embeddings | Deterministic local (token overlap / bag-of-words / hash-based) — NO real OpenAI |
| 3 | Backend deploy | FastAPI stays local-only, verified via `backend:test`; Vercel deploys Next.js frontend only |
| 4 | RAG data | Local markdown style guide files committed under `backend/data/style_guides/` |
| 5 | Eval | Deterministic retrieval + pipeline tests, NOT semantic LLM quality |

## What RC-3D tests

- Codex authoring Python that reads files, chunks markdown deterministically, and stores in-memory representations
- Codex implementing deterministic retrieval logic (no randomness, no external models)
- Codex extending an EXISTING endpoint (`/rewrite`) with a new response field while preserving all RC-3C contracts
- Codex preserving an EXISTING module (`processor.py`, `evaluate_text`) without regression
- Codex extending an EXISTING frontend page with a new section while preserving the RC-3C "Processing Service" panel
- pytest discovery of new test files alongside existing RC-3C tests
- The "no new pip deps" prompt constraint holding under multiple Python tasks (vs RC-3C which only had two Python tasks; RC-3D has two too — task-001 + task-002)

## What RC-3D deliberately does NOT test

- Real embeddings (OpenAI / Anthropic / Cohere / sentence-transformers / local model)
- Real LLM rewrite (mock stays mock)
- pgvector / Postgres / Qdrant / Chroma / Faiss / any external vector DB
- File upload / style guide CRUD / dynamic style guides
- Versioning, A/B comparison, hybrid search, re-ranking
- numpy / scikit-learn / sentence-transformers / langchain / any ML pip dep
- Style guide quality eval (LLM-as-judge / semantic similarity)
- with-RAG vs without-RAG comparison
- Backend deployment (FastAPI stays local; Vercel `experimentalServices` block is NOT activated)
- Auth / Stripe / billing / dashboard

## Dogfood directory

```
.dogfood/rc3d-style-guide-rag-probe/        15 files / dirs
  package.json                              build chain (UNCHANGED): backend:test && prisma generate && next build
  next.config.mjs / tsconfig.json / tailwind.config.ts / postcss.config.mjs
  vercel.json                               {"framework":"nextjs"} (frontend-only; experimentalServices NOT in seed)
  .gitignore                                inherited from RC-3C
  .env                                      DATABASE_URL="file:./dev.db"
  app/
    layout.tsx                              metadata describes RC-3D
    page.tsx                                INHERITED from RC-3C-completed (Processing Service panel; task-003 adds Style Guide RAG section alongside)
    globals.css                             unchanged
  prisma/
    schema.prisma                           RC-3B 3-model schema (RewriteJob/StyleGuide/RewriteResult — NOT regressed)
  backend/
    requirements.txt                        UNCHANGED from RC-3C (fastapi/pydantic/pytest/httpx) — Codex MUST NOT modify
    app/
      __init__.py
      main.py                               INHERITED from RC-3C-completed (3 endpoints wired)
      processor.py                          INHERITED from RC-3C-completed (Pydantic models + rewrite_text + evaluate_text + LEXICAL_REPLACEMENTS)
    tests/
      __init__.py
      test_health.py                        INHERITED from RC-3C-completed
      test_rewrite.py                       INHERITED from RC-3C-completed (will be EXTENDED by task-003 to assert applied_style_rules)
      test_evaluate.py                      INHERITED from RC-3C-completed
    data/
      style_guides/
        brand-voice.md                      12 short rules; brand voice
        friendly-saas-copy.md               12 short rules; conversational SaaS copy
        professional-email.md               12 short rules; professional email register
  scripts/
    backend-test.sh                         INHERITED from RC-3C (venv + pip install + pytest)
  requirements.md                           3 H2 tasks (below)

scripts/rc3d.sh                             dry-run by default, --run executes
docs/rc3d-prep-report.md                    this file
```

## Baseline source

The seed inherits from the **completed RC-3C workspace** (project_2073109d64, session_60a9b731f6) — NOT the rc3c-fastapi-processing-probe prep seed. Verified before copying:

- `backend/app/main.py` — 3 endpoints (`@app.get("/health")`, `@app.post("/rewrite")`, `@app.post("/evaluate")`) ✓
- `backend/app/processor.py` — Pydantic v2 models for both Rewrite and Evaluate, plus `rewrite_text` and `evaluate_text` deterministic implementations ✓
- `backend/tests/test_health.py` + `test_rewrite.py` + `test_evaluate.py` ✓
- `prisma/schema.prisma` — RC-3B 3-model schema with relation + back-reference ✓
- `app/page.tsx` — RC-3C "Processing Service" panel ✓
- `vercel.json` — `{"framework": "nextjs"}` only (the source dogfood's experimentalServices auto-injection happened AFTER the workspace was copied to /tmp; the workspace itself has the original frontend-only config) ✓
- `requirements.txt` — UNMODIFIED across all 3 RC-3C tasks (Codex never added LLM libs — P2 falsified there; we re-test the constraint here) ✓

## Requirements task list (3 H2 tasks)

### Task 1 — Add local style guide ingestion and chunking
- New module exposes `load_style_guides(root) -> list[StyleGuideDoc]` and `chunk_style_guides(docs) -> list[StyleGuideChunk]`
- Chunk `id` MUST be deterministic across runs (e.g. `<style_guide_name>::<position>` or `<name>:<sha256(text)[:12]>`)
- pytest covers: 3 docs loaded, ≥ 8 chunks total, IDs stable across two consecutive calls, no empty chunk text
- `backend/requirements.txt` MUST NOT change; stdlib only

### Task 2 — Add deterministic style rule retrieval
- New module exposes `retrieve_style_rules(query, chunks, k=3)`
- Allowed scoring: token overlap / bag-of-words / Jaccard / character n-gram / simple TF
- NO randomness, NO time-based seeding, NO new deps (no numpy / sklearn / sentence-transformers)
- Ties broken by stable secondary key (e.g. `(score, chunk.id)`)
- pytest covers: query → professional-email returns chunks from that file; empty query doesn't error; same `(query, chunks, k)` returns same output across two calls

### Task 3 — Use retrieved style rules in /rewrite + add frontend Style Guide RAG section
- `POST /rewrite` response gains `applied_style_rules: list[str]` (chunk IDs)
- All existing `RewriteResponse` fields preserved
- pytest extended (and the existing field-set assertion updated to include `applied_style_rules`)
- Frontend home page adds Style Guide RAG section (Server Component only, no fetch)
- `/health` and `/evaluate` continue to work; their tests intact

## Integration commands

The runtime derives integration commands from `package.json` scripts. Currently:
- `npm run typecheck` — required (`tsc --noEmit`)
- `npm run build` — required, expands to `npm run backend:test && prisma generate && next build`
  - `backend:test` runs `bash scripts/backend-test.sh` (venv + pip install + pytest)

So a failed pytest fails the build, fails integration, fails Vercel deploy. Same gating pattern as RC-3C — no `required_commands` YAML knob added.

## Failure predictions (P1-P10)

| ID | Prediction | Most likely fix layer |
|---|---|---|
| **P1** | Codex imports `openai` / `anthropic` / `sentence_transformers` / `numpy` / `sklearn` / `langchain` despite stdlib-only instruction | prompt + requirements.md wording; do NOT accept the dep |
| **P2** | Codex stores chunks/index outside `backend/` (e.g. project root `data/`) → out_of_scope | scope wording in requirements.md |
| **P3** | Chunk IDs not stable across runs (uuid / random / dict iteration order without sorting) → pytest determinism check fails | task-001 acceptance + prompt tightening |
| **P4** | Retrieval scoring not deterministic (random tie-break, set iteration) → pytest fails | task-002 acceptance + prompt tightening |
| **P5** | Codex adds an unrequested endpoint (e.g. `POST /retrieve`, `GET /style-guides`) that's disruptive | requirements wording (currently neither required nor explicitly forbidden — only flag if Codex adds something out-of-scope) |
| **P6** | Frontend task tries to fetch backend at runtime → Vercel build fails | task-003 acceptance + prompt tightening |
| **P7** | Context pack under-ranks `backend/data/style_guides/*.md` because `_context_bucket` doesn't recognize that path | narrow runtime extension to `_context_bucket` ONLY if real failure |
| **P8** | Codex modifies `backend/requirements.txt` to add a heavy ML library | prompt tightening; do NOT accept the dep |
| **P9** | Vercel deploy accidentally activates backend service via `experimentalServices` block (if `vercel link` re-injects it during operator setup) | Vercel config / operator setup; not a runtime bug |
| **P10** | `backend:test` passes locally but fails on Vercel build (Python image mismatch, pip install slow / cached differently) | inspect deployment.json; do NOT deploy backend yet |

## Local validation (done in prep)

Validated in the sandbox before handoff:
- `bash -n scripts/rc3d.sh` → SYNTAX OK
- `chmod +x scripts/rc3d.sh` and `chmod +x backend-test.sh` → both executable
- Full dry-run with mocked codex/vercel/env → all expected commands print, agent-studio.yaml renders cleanly
- `npm install` → 114 packages in 22s, clean
- `bash scripts/backend-test.sh` → venv created, pip install, **6 passed in 0.17s** (inherits 1 health + 2 rewrite + 3 evaluate tests from RC-3C)
- `npm run typecheck` → clean (`tsc --noEmit`)
- `npm run build` → backend:test ✅, prisma generate ❌ — `binaries.prisma.sh` 403 in this Linux sandbox (same network restriction every prior probe hit; RC-3B and RC-3C both verified Prisma 5.22.0 works on Mac and on Vercel build env). NOT a seed bug.

## Operator pre-checklist (do BEFORE `--run`)

1. **Create new Vercel project** `rc3d-style-guide-rag-probe` via `vercel link`:

   ```bash
   cd ~/Documents/LocalAgents/.dogfood/rc3d-style-guide-rag-probe
   vercel link
   ```

   Suggested answers:
   - Set up this directory? **yes**
   - Scope: **pianxing11281128's projects**
   - Link to existing project? **no**
   - Project name: **rc3d-style-guide-rag-probe**
   - Directory: **./**
   - Modify settings? **no**

2. **(Observation only — not blocking)**: `vercel link` will likely auto-detect `backend/` as a FastAPI service and offer to add an `experimentalServices` block to `vercel.json`, same as RC-3C. **Either accept or decline** — RC-3D doesn't activate backend deploy regardless. If you accept, just record it as an observation; smoke still tests `GET /` against the Next.js frontend.

3. **Disable Vercel Authentication** for the new project:

   ```bash
   open "https://vercel.com/pianxing11281128s-projects/rc3d-style-guide-rag-probe/settings/deployment-protection"
   ```

   Settings → Deployment Protection → **Vercel Authentication: Disabled**. **Save.**

4. **Update `~/.local-agent-vercel.env` — corrected recipe** (do NOT `source` old env file before `cat >`-ing the new one — that's the RC-3B/C gotcha):

   ```bash
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

5. **Verify env**:

   ```bash
   source ~/.local-agent-vercel.env
   python3 - <<'PY'
   import os
   for k in ["VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID"]:
       v = os.environ.get(k)
       print(f"{k}: {'present' if v else 'missing'}" + (f" ({len(v)} chars)" if v else ""))
   PY
   ```

6. **Verify deployment URL after the run** starts with `rc3d-style-guide-rag-probe-...` (NOT rc3a/b/c).

7. **Dry-run first**:

   ```bash
   cd ~/Documents/LocalAgents
   ./scripts/rc3d.sh
   ```

8. **Then real run**:

   ```bash
   ./scripts/rc3d.sh --run 2>&1 | tee /tmp/rc3d-run.log
   ```

## Out of scope — do NOT build (explicit)

Per the locked spec:
- `scripts/rc3d.sh --run` (operator action only, after pre-checklist)
- Calling Codex / OpenAI / Anthropic / any LLM API
- Real embeddings provider
- pgvector / Postgres / Qdrant / Chroma / Faiss
- File upload / style guide CRUD / dynamic style guide management
- Real RAG framework (langchain / llamaindex / haystack)
- numpy / scikit-learn / sentence-transformers
- Auth / Stripe / billing / dashboard
- Real FastAPI backend deployment (Vercel `experimentalServices` block stays inert)
- General RAG adapter / general plugin system
- `autonomous.py` refactor
- Runtime changes (UNLESS prep is blocked by a real Local Agent Dev Studio bug — none surfaced)
- Starting RC-3E

## Status lock + next milestone

State: **RC-3D prep complete, holding for "go RC-3D run" signal.**

Next milestone: **RC-3D run** (operator executes), then RC-3E (LLMOps eval suite — separate scoping conversation, NOT auto-triggered by RC-3D success).
