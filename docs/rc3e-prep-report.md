# RC-3E Prep Report — LLMOps Eval Suite Shape Probe

Date: 2026-05-11. Updated 2026-05-11 with RC-3E.1 seed/scope hygiene fix.
Status: **prep complete (RC-3E.1 fix applied), NOT yet executed.**
The first RC-3E run paused at task-002 due to a dogfood-seed scope gap;
RC-3E.1 fixed the seed (no runtime changes) and the next run uses a
**fresh workspace** (do NOT resume the old session).

## RC-3E.1 seed/scope hygiene fix

**What happened on the first RC-3E run** (paused at task-002,
`run_ef5e319611`, `review_aee2c2f94d`):
- Codex generated **good code** — 26 backend tests passed, `npm run
  build` passed, source patch present, required eval executed.
- Promotion Gate correctly blocked promotion because:
  - `diff_within_scope=False` — Codex's patch modified `package.json`
    (added `npm run eval`) and created `backend/scripts/eval.sh`,
    both outside the task-002 declared scope `backend/**`.
  - `no_critical_security_finding=False` — security critic flagged
    the `package.json` edit (`scripts` is a security-sensitive surface
    by the critic's heuristic, alongside `env`, `deps`, `migrations`,
    `auth`, `session`, `Dockerfile`).
- Plus the patch contained two leaked runtime artifacts:
  - `backend/.pytest_cache/v/cache/nodeids` — pytest's cache leaked
    into changed-files because `_discover_files` ignores
    `__pycache__/` but NOT `.pytest_cache/`. (Analogous to the
    RC-3A.7 `*.tsbuildinfo` gap; a runtime-layer fix exists but is
    out of scope for RC-3E.1 per discipline.)
  - `backend/data/eval/runs/eval_<hash>/eval-result.json` — Codex's
    pytest tests wrote a real eval-result file at the production path
    instead of using `tmp_path`, leaking the artifact into the diff.

**Fix at the dogfood/seed layer (no runtime changes):**

| Change | Purpose |
|---|---|
| `package.json` adds `"eval": "bash backend/scripts/eval.sh"` | Codex no longer needs to touch `package.json`; eliminates the out-of-scope `package.json` edit AND the security critic's `scripts` flag |
| `backend/scripts/eval.sh` pre-seeded | Codex no longer needs to create the runner; fails clearly if `backend/app/eval/cli.py` doesn't exist yet |
| `backend/pytest.ini` pre-seeded with `cache_dir = .venv/.pytest_cache` | Pytest cache writes inside `.venv/` which IS in `_discover_files`'s `ignored_dirs` set; cache no longer leaks into changed-files |
| `requirements.md` task-002 wording explicitly forbids editing `package.json`, `backend/scripts/eval.sh`, `backend/pytest.ini`, `backend/requirements.txt` | Codex sees the constraints up front rather than discovering them via Promotion Gate failure |
| `requirements.md` task-002 pins eval module path to `backend/app/eval/*` (single package, NOT a parallel `backend/eval/*`) | Removes the path ambiguity that produced both `backend/app/eval/*` AND `backend/eval/*` in the first run; pre-seeded `backend/scripts/eval.sh` calls `python3 -m app.eval.cli` so the path is locked in shell |
| `requirements.md` task-002 requires pytest tests to use `tmp_path` for any artifact write; CLI must support `--output-root` | Eliminates the `eval-result.json` leak from candidate tests; the actual production artifact under `backend/data/eval/runs/` is owned by the operator's post-run `npm run eval`, not by tests |
| `.gitignore` confirmed includes `backend/.pytest_cache/`, `backend/**/__pycache__/`, `backend/data/eval/runs/eval_*/` | Defense in depth (note: `_discover_files` doesn't read `.gitignore`, so this only helps `git status` hygiene; the pytest.ini redirect is the load-bearing fix for cache leak, and `tmp_path` in tests is the load-bearing fix for eval artifact leak) |

**What did NOT change:** Local Agent Dev Studio runtime, Promotion
Gate, security critic, artifact validator, agent-studio.yaml, eval
schema, the 12 golden cases, any RC-3D/C/B/A code. The current
`review_aee2c2f94d` is left open and will be discarded along with the
old session state when the next run starts in a fresh workspace.

**Next-run discipline:** the next RC-3E run uses
`/tmp/rc3e-real` wiped fresh by `scripts/rc3e.sh --run`. Do NOT
resume `session_*` from the paused first run; the seed has changed
underneath it. The pre-seeded `npm run eval` will fail clearly until
task-002's `backend/app/eval/cli.py` lands.

---

## Original prep report (below) — preserved for context

## Goal

Verify Local Agent Dev Studio can drive Codex to scaffold a
**deterministic, dependency-free LLMOps eval shape** on top of the
RC-3D Style Guide RAG pipeline:
- Typed loader for committed golden dataset (12 cases).
- Three deterministic per-case scorers (structural / containment /
  retrieval-hit).
- Batch CLI that runs each case twice (with-RAG vs without-RAG via
  optional `disable_retrieval` flag), times each call, writes a
  structured artifact with `cost_usd: 0.0` and latency summaries.
- Frontend "Eval Suite" section documenting the contract.

This is the first probe involving **post-run artifact consumption**
(not just structural / data-flow). It deliberately uses NO real LLM,
NO LLM-as-judge, NO embeddings, NO new pip deps. The eval is
**observe-only**: NOT in `npm run build`, NOT in agent-studio
integration commands, failure does not block deploy.

## Why deterministic-only

The constraints lock RC-3E to zero-LLM by design. Two reasons:

1. **Token + cost discipline.** Real LLM-as-judge would compound Codex
   token spend with eval-time API spend on every probe iteration. RC-3E
   is the *plumbing* milestone; the actual quality-eval comes later.
2. **Deterministic baseline first.** Without a stable per-case pass/fail
   signal computed from rules, there's no foundation to add an LLM-judge
   metric on top of. The artifact schema (`cost_usd: 0.0` field present)
   is forward-compatible — a future RC-4+ milestone can swap in real
   LLM scoring without re-architecting.

## Locked decisions (from scoping)

| # | Decision | Locked value |
|---|---|---|
| 1 | Golden dataset | committed JSONL seed (`backend/data/eval/rewrite_golden.jsonl`); 12 cases; Codex must NOT modify it |
| 2 | Batch eval | `npm run eval` → Python CLI under `backend/eval/`; NOT a new `agent-studio` CLI subcommand |
| 3 | Metrics | deterministic only — structural + containment + retrieval-hit; NO LLM-as-judge, NO model call |
| 4 | Prompt comparison | NOT in RC-3E. single-config single-pass eval only |
| 5 | with-RAG vs without-RAG | execute both, record both in artifact; NOT a hard gate; add optional `disable_retrieval: bool = False` to `RewriteRequest` (default False preserves existing behavior) |
| 6 | Cost / latency artifact | latency recorded; `cost_usd: 0.0` present (forward-compat); per-eval-run dir at `backend/data/eval/runs/<eval_run_id>/eval-result.json` |
| 7 | Regression gate | observe-only by default; CLI exits 0; `--strict` may exit non-zero; eval is NOT in `npm run build` and NOT in integration; `scripts/rc3e.sh` runs `npm run eval` once post-autonomous as verification (failure surfaced, not hidden) |

## Dogfood directory

```
.dogfood/rc3e-llmops-eval-suite-probe/         15 files / dirs at baseline
  package.json                                  renamed rc3d→rc3e; build chain UNCHANGED (npm run backend:test && prisma generate && next build)
  next.config.mjs / tsconfig.json / tailwind.config.ts / postcss.config.mjs
  vercel.json                                   FRONTEND-ONLY override {"framework":"nextjs"} (RC-3D's was 228-byte version with Vercel auto-injected experimentalServices; rc3e drops it per locked decision #3)
  .gitignore                                    inherited + new line: backend/data/eval/runs/eval_*/ (per-run dirs gitignored; runs/ tracked via .gitkeep)
  .env                                          DATABASE_URL="file:./dev.db"
  app/
    layout.tsx                                  metadata describes RC-3E
    page.tsx                                    INHERITED from RC-3D-completed (Processing Service + Style Guide RAG sections both present)
    globals.css                                 unchanged
  prisma/
    schema.prisma                               RC-3B 3-model schema (UNCHANGED)
  backend/
    requirements.txt                            UNCHANGED from RC-3C/3D (fastapi/pydantic/pytest/httpx)
    app/
      __init__.py
      main.py                                   3 endpoints (RC-3D-completed)
      processor.py                              applied_style_rules wired (RC-3D-completed)
      style_guides.py                           load + chunk (RC-3D Codex-generated)
      retriever.py                              deterministic retrieve_style_rules (RC-3D Codex-generated)
    tests/
      __init__.py
      test_health.py + test_rewrite.py + test_evaluate.py + test_style_guides.py + test_retriever.py
                                                ALL inherited from RC-3D-completed (12 tests pass)
    data/
      style_guides/
        brand-voice.md / friendly-saas-copy.md / professional-email.md
                                                RC-3D seed (UNCHANGED)
      eval/
        rewrite_golden.jsonl                    NEW — 12 hand-curated cases (committed at baseline; Codex MUST NOT modify)
        runs/.gitkeep                           directory tracked, per-run subdirs gitignored
  scripts/
    backend-test.sh                             INHERITED from RC-3D
  requirements.md                               3 H2 tasks (loader → metrics+CLI+disable_retrieval → frontend Eval Suite section)

scripts/rc3e.sh                                 dry-run by default, --run executes; NEW post-run step runs `npm run eval` (observe-only)
docs/rc3e-prep-report.md                        this file
```

## Baseline source

The seed inherits from the **completed RC-3D workspace**
(project_a841c64d1f, session_227f6040e7) staged at
`.dogfood/_rc3e_baseline_from_rc3d/`. Verified before copying:
- `backend/app/main.py` — 3 endpoints
- `backend/app/processor.py` — `applied_style_rules` referenced 4× (RC-3D's task-003 wiring intact)
- `backend/app/style_guides.py` — RC-3D Codex-generated module present
- `backend/app/retriever.py` — RC-3D Codex-generated module present
- `backend/tests/` — all 5 test files (health/rewrite/evaluate/style_guides/retriever) present
- `backend/data/style_guides/*.md` — 3 RC-3D seed files unchanged
- `backend/data/eval/` — does NOT exist in baseline (RC-3E adds it as new seed dir)
- `app/page.tsx` — "Style Guide RAG" appears 3× (RC-3D's task-003 panel intact)
- `prisma/schema.prisma` — 1121 bytes (RC-3B 3-model schema)
- `backend/requirements.txt` — fastapi/pydantic/pytest/httpx only, byte-identical to RC-3C end state (no LLM lib added across 6 prior Python tasks)

Seed deltas from baseline:
- `package.json` name + description renamed rc3d→rc3e
- `app/layout.tsx` metadata description updated for RC-3E
- `vercel.json` overridden to frontend-only (RC-3D's had Vercel-auto-injected `experimentalServices`; per RC-3E locked decision #3 backend stays local-only)
- `.gitignore` adds `backend/data/eval/runs/eval_*/`
- `backend/data/eval/rewrite_golden.jsonl` NEW (12 cases)
- `backend/data/eval/runs/.gitkeep` NEW (directory marker)

## Golden dataset schema

Each line is a JSON object:

```json
{
  "id": "case_001",
  "input_text": "...",
  "tone": "concise" | "friendly" | "professional",
  "expected_applied_style_rules_min": 0 | 1 | ...,
  "must_contain": [],
  "must_not_contain": [],
  "expected_style_guide_prefix": "professional-email" | "brand-voice" | "friendly-saas-copy" | null
}
```

12 cases in seed:
- 3 tones represented: `concise`, `friendly`, `professional`
- 4 distinct expected style guide prefixes: `professional-email`, `friendly-saas-copy`, `brand-voice`, `null` (1 edge case)
- 4 cases include `must_not_contain` constraints (test that regex/lexical replacements actually drop hype words / robotic transitions / hedging)
- 1 case (`case_010`) uses near-empty input ("Hello.") with `expected_applied_style_rules_min: 0` to exercise the empty-input branch
- All cases small (<200 chars input_text) for fast eval cycles
- No real customer data, no secrets

## Requirements task list (3 H2 tasks)

### Task 1 — Add eval golden dataset loader
- New module `backend/eval/cases.py` exposes `load_golden_cases(path)` → `list[GoldenCase]`
- `GoldenCase` Pydantic model with all 7 fields above
- Order matches JSONL file order
- pytest covers: 12 records loaded, fields parsed, two consecutive loads identical
- `backend/requirements.txt` UNCHANGED; stdlib + existing pydantic only

### Task 2 — Add deterministic eval metrics, CLI, and disable_retrieval flag
- Add optional `disable_retrieval: bool = False` to `RewriteRequest` (default False preserves existing behavior)
- Processor short-circuits: when True, `applied_style_rules = []` and retriever is NOT called
- New module `backend/eval/metrics.py` with three scorers (structural, containment, retrieval_hit)
- New CLI module `backend/eval/cli.py` runnable via `python3 -m backend.eval.cli`:
  - Loads golden cases
  - Calls processor twice per case (with-RAG, without-RAG)
  - Writes artifact at `backend/data/eval/runs/eval_<hash>/eval-result.json`
  - `<hash>` deterministic (NOT timestamp)
  - Exits 0 by default; `--strict` exits non-zero on failed cases
- New `npm run eval` script
- pytest for metrics + disable_retrieval + CLI artifact schema (incl. assertion that at least one case has different applied_style_rules count between with-RAG and without-RAG — proves the flag actually changed behavior)
- Eval is **NOT** added to `npm run build` chain
- Eval is **NOT** added to agent-studio integration commands
- `backend/requirements.txt` UNCHANGED

### Task 3 — Add frontend Eval Suite section + preserve existing sections
- Server-Component "Eval Suite" section on `app/page.tsx`
- Documents golden dataset path, `npm run eval`, three metrics, with-RAG vs without-RAG observational signal, `cost_usd: 0.0` semantics, observe-only gate semantics
- RC-3C "Processing Service" section preserved
- RC-3D "Style Guide RAG" section preserved
- All three sections coexist
- `backend/data/eval/rewrite_golden.jsonl` UNCHANGED
- `backend/requirements.txt` UNCHANGED
- Vercel preview smoke `/` 200

## What RC-3E tests

- Codex authoring a JSONL loader + Pydantic model from a committed seed
- Codex implementing deterministic per-case scorers (no randomness, no model calls)
- Codex extending a Pydantic model with a new optional field WITHOUT breaking existing default behavior
- Codex authoring a CLI that writes a structured artifact with a forward-compatible schema (incl. `cost_usd: 0.0` field even though no real LLM)
- Codex correctly wiring a new `npm run eval` script that lives OUTSIDE the existing build chain
- Codex preserving TWO previously-built frontend sections (Processing Service from RC-3C, Style Guide RAG from RC-3D) while adding a third (Eval Suite)
- The "no new pip deps" constraint holding under maximum pressure (RC-3D had 0/10 predictions fire, 6 prior Python tasks; RC-3E adds 2 more Python tasks where "eval" / "metrics" / "scoring" wording strongly tempts pretrained models toward `numpy` / `ragas` / `evals` libraries)
- Post-run artifact discoverability: the script's post-run `npm run eval` step finds the artifact at the expected `backend/data/eval/runs/eval_*/` path

## What RC-3E deliberately does NOT test

- Real OpenAI / Anthropic / any LLM API call
- LLM-as-judge / GPT-evaluator
- BLEU / ROUGE / METEOR / embedding-similarity / perplexity / any model-based metric
- numpy / pandas / sklearn / sentence-transformers / langchain / ragas / deepeval / evals
- Real cost reporting (>0)
- Prompt comparison / A/B / multi-config sweeps
- Cross-run baseline persistence / regression delta computation
- Strict-mode default-on
- Eval inside `npm run build` chain (would block deploy on eval failures)
- Eval inside agent-studio integration command list
- Eval as deployment gate
- New `agent-studio eval` CLI subcommand (would be runtime change)
- Eval dashboard / web UI
- Auth / Stripe / billing
- Real backend deployment (Vercel `experimentalServices` block stays inert; seed forces frontend-only)

## Local validation (done in prep)

- `bash -n scripts/rc3e.sh` → SYNTAX OK
- `chmod +x scripts/rc3e.sh` and `chmod +x backend-test.sh` → both executable
- Full dry-run with mocked codex/vercel/env → all expected commands print, agent-studio.yaml renders cleanly, post-run npm run eval block previews
- `npm install` → 114 packages in 19s, clean
- `bash scripts/backend-test.sh` → venv created, pip install, **12 passed in 0.19s** (1 health + 2 rewrite + 3 evaluate + 3 style_guides + 3 retriever — full RC-3D end-state inherited)
- `npm run typecheck` → clean (`tsc --noEmit`)
- Golden JSONL parses cleanly: 12 cases, 3 tones, 4 distinct expected prefixes (incl. None edge case), 4 cases with `must_not_contain` constraints, 7 schema fields per case
- `npm run build` not run in sandbox: prisma generate would 403 against `binaries.prisma.sh` (known sandbox-only restriction; RC-3B/C/D all proved Prisma 5.22.0 works on Mac and Vercel)
- `npm run eval` not run: that script doesn't exist until Codex creates it in task-002. Per spec "do not invent eval code during prep beyond golden dataset seed."

## Failure predictions (P1-P10)

| ID | Prediction | Most likely fix layer |
|---|---|---|
| **P1** | Codex imports `openai` / `anthropic` / `langchain` / `ragas` / `deepeval` / `evals` despite stdlib-only | prompt + requirements.md (do NOT accept dep) |
| **P2** | Codex adds `numpy` / `pandas` for batch tabulation | prompt tightening (do NOT accept dep) |
| **P3** | Codex modifies `rewrite_golden.jsonl` (treats it as fixture to extend) | scope wording in task-001 + task-002 |
| **P4** | Eval CLI nondeterministic (eval_run_id derived from timestamp / random; dict iteration order without sort) | task-002 acceptance + prompt tightening |
| **P5** | Codex adds eval to `npm run build` chain or to agent-studio integration commands | task-002 acceptance explicit "NOT in build / NOT in integration" |
| **P6** | `disable_retrieval` flag exists but processor still calls retriever / `applied_style_rules` not actually empty when flag is True | task-002 pytest must assert difference on at least one case |
| **P7** | Frontend tries to fetch eval artifacts at runtime | task-003 acceptance: Server Component, no fetch |
| **P8** | Eval artifact `cost_usd` field renamed (`cost`, `total_cost_usd`, etc.) → forward-compat broken | scoping doc fixes the schema; task-002 acceptance pins it |
| **P9** | RC-3D Style Guide RAG section removed by frontend task | task-003 explicit "do NOT remove RC-3D section" wording |
| **P10** | `npm run eval` works locally but the script's post-run verification can't find the artifact | check the output path the CLI actually writes to vs what `scripts/rc3e.sh` expects (`backend/data/eval/runs/eval_*/eval-result.json`) |

Plus standing checks: "Codex tries to be smart and adds an LLM call as a quality scorer" — do NOT accept; tighten the prompt.

## Operator pre-checklist (do BEFORE `--run`)

1. **Review the seeded `rewrite_golden.jsonl`** — open and skim the 12 cases. They ARE the eval input data, so their quality IS the eval quality. If you want different cases, edit the file BEFORE the run.

2. **Create new Vercel project** `rc3e-llmops-eval-suite-probe`:
   ```bash
   cd ~/Documents/LocalAgents/.dogfood/rc3e-llmops-eval-suite-probe
   vercel link
   ```
   Suggested answers: yes / pianxing11281128's projects / no / `rc3e-llmops-eval-suite-probe` / `./` / no.

3. **(Observation only)** `vercel link` may again auto-detect the `backend/` dir and offer multi-service config. Accept or decline; the seed's `vercel.json` is frontend-only and RC-3E doesn't activate backend deploy regardless.

4. **Disable Vercel Authentication**:
   ```bash
   open "https://vercel.com/pianxing11281128s-projects/rc3e-llmops-eval-suite-probe/settings/deployment-protection"
   ```
   Settings → Deployment Protection → **Vercel Authentication: Disabled**. **Save.**

5. **Update `~/.local-agent-vercel.env` — corrected recipe** (do NOT `source` old env file before `cat >`-ing the new one — that's the standing RC-3B/C gotcha):
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

6. **Verify env**:
   ```bash
   source ~/.local-agent-vercel.env
   python3 - <<'PY'
   import os
   for k in ["VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID"]:
       v = os.environ.get(k)
       print(f"{k}: {'present' if v else 'missing'}" + (f" ({len(v)} chars)" if v else ""))
   PY
   ```

7. **Verify deployment URL after the run** starts with `rc3e-llmops-eval-suite-probe-...` (NOT rc3a/b/c/d).

8. **Dry-run first**:
   ```bash
   cd ~/Documents/LocalAgents
   ./scripts/rc3e.sh
   ```

9. **Then real run**:
   ```bash
   ./scripts/rc3e.sh --run 2>&1 | tee /tmp/rc3e-run.log
   ```
   The script will execute `npm run eval` once after the autonomous run completes, print the artifact path + a summary of key fields, and exit. Eval failure does NOT block this script (observe-only) but the warning is surfaced.

10. **After run, verify locally**: cat the eval artifact to inspect per-case results:
    ```bash
    PROJ=$(ls -d /tmp/rc3e-real/.agent-studio/projects/*/ | head -1 | sed 's:/$::')
    cat "$PROJ"/backend/data/eval/runs/eval_*/eval-result.json | python3 -m json.tool | head -80
    ```

## Out of scope — do NOT build (explicit)

Per the locked spec:
- `scripts/rc3e.sh --run` (operator action only, after pre-checklist)
- Calling Codex / OpenAI / Anthropic / any LLM API
- LLM-as-judge / GPT-evaluator
- numpy / pandas / sklearn / sentence-transformers / langchain / ragas / deepeval / evals
- BLEU / ROUGE / embedding-similarity / perplexity
- Real cost values (>0)
- Prompt comparison / A/B / multi-config (RC-3F+)
- Cross-run baseline persistence / regression delta computation (RC-3F+)
- Strict-mode default-on
- Eval inside `npm run build` / integration command list
- New `agent-studio eval` CLI subcommand
- Eval dashboard / web UI
- Auth / Stripe / billing / dashboard
- Real backend deployment / activate Vercel `experimentalServices`
- General eval framework abstraction
- `autonomous.py` refactor
- Runtime changes (UNLESS prep is blocked by a real Local Agent Dev Studio bug — none surfaced)
- Starting RC-3F

## Status lock + next milestone

State: **RC-3E prep complete, holding for "go RC-3E run" signal.**

Next milestone: **RC-3E run** (operator executes), then RC-3F
(Observability — separate scoping conversation, NOT auto-triggered
by RC-3E success).
