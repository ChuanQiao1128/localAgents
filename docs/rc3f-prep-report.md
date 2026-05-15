# RC-3F Prep Report — Third-party Detector Adapter Probe

Date: 2026-05-12.
Status: **prep complete, NOT yet executed.** Awaiting explicit
"go RC-3F run" signal from operator (after creating new
`rc3f-detector-adapter-probe` Vercel project, disabling Deployment
Protection, and updating env file with the corrected recipe — and
optionally adding `SAPLING_API_KEY` for post-run real-mode test).

## Goal

Add the AI Writing Naturalizer's first **real third-party AI
detector adapter** (Sapling), with mock-default + real-opt-in
routing, on top of the RC-3E LLMOps eval suite. The DetectorAdapter
abstraction + DetectorResult schema + redaction + endpoint shape +
CLI artifact pattern + frontend Detector Report section all land in
one milestone — but without any rewrite-loop logic and without any
detector-as-deployment-gate behavior. Those are RC-3G+.

## Dual-purpose framing (per `project_dual_purpose_pivot.md`)

RC-3F is a **two-output milestone**:

- **Product output (Naturalizer MVP step):** real third-party AI
  detection capability lands in production-like paths
  (`backend/data/detector/runs/detect_<hash>/detector-result.json`),
  with a Server-Component frontend section that documents the
  contract using the locked compliance text. This is where the
  Naturalizer starts being measurable against ChatGPT-only
  workflows.
- **Studio output (capability dimension validated):** Local Agent
  Dev Studio can drive Codex through a real third-party SaaS API
  integration end-to-end — including secret handling, redaction,
  failure-mode discipline, env-gated routing, observe-only artifact
  persistence, and a frontend section that ships product-shaped
  copy (not probe narration).

## Product strategy lock (from `project_product_strategy.md`)

| | LOCKED |
|---|---|
| Positioning | "AI-likeness risk reduction + text naturalization workflow" |
| NOT positioning | "guaranteed bypass," "undetectable AI," "guaranteed detector evasion" |
| Detector outputs | Reference signal, not absolute truth. Frontend MUST contain the verbatim compliance text |
| Workflow | Max 2-3 rounds of detector-informed rewrite (RC-3G+); RC-3F is **adapter shape only — NO loop logic anywhere** |
| Selection | Composite score (detector risk + meaning preservation + naturalness + factual consistency + no oddities); never detector-score-alone |
| Cost | Free tier: mock only (no API call). Real-mode opt-in via env. RC-3F: cost_usd always 0.0 |
| Compliance boundary | What Claude WILL and WILL NOT help with documented in `project_product_strategy.md` |

## Why Sapling first

Compared to GPTZero (the runner-up):
- API documentation is more idiomatic (clear Python client examples
  via `aidetect`) — lower onboarding friction for a SHAPE probe
- Free tier headroom is generous enough for adapter dev without
  triggering rate-limit handling
- Sapling's score is already 0.0-1.0 with 1.0 = AI-likely (no
  inversion needed in the adapter)
- Future stack synergy: Sapling also offers rewrite/grammar APIs
  (deferred to later milestones)

GPTZero comes second under the same `DetectorAdapter` ABC in RC-3F.x
or RC-3G. The user-recognition advantage matters at marketing time,
not at adapter-shape probe time.

## Mock-default + real-opt-in design

| Mode | When active | Network? | Cost? |
|---|---|---|---|
| Mock | Default. Whenever `DETECTOR_PROVIDER` is missing or `=mock`. Always used by integration / pytest. | No | $0.0 |
| Real | Opt-in: `DETECTOR_PROVIDER=sapling` AND `SAPLING_API_KEY` set. Operator's post-run `npm run detect:real` only. | Yes (1 call) | $0.0 (RC-3F doesn't track real cost yet) |
| Real with no key | `DETECTOR_PROVIDER=sapling` but `SAPLING_API_KEY` missing. **Falls back to mock**, sets `error.code="missing_api_key"` in result. NEVER raises. | No | $0.0 |

## DetectorResult schema (LOCKED)

```json
{
  "schema_version": "agentic.detector_result.v1",
  "detector_run_id": "detect_<12-char-hex>",
  "provider": "sapling" | "mock",
  "mode": "mock" | "real",
  "input_text_sha256_prefix": "<first 16 hex chars>",
  "score": 0.0,
  "label": "human_likely" | "mixed" | "ai_likely" | "unknown",
  "confidence": 0.0,
  "sentence_scores": [{"text": "...", "score": 0.0}],
  "latency_ms": 0.0,
  "cost_usd": 0.0,
  "raw": { },
  "error": null,
  "created_at": "<iso8601>"
}
```

Normalization rules:
- `score` is 0.0-1.0 where 1.0 = AI-likely (regardless of provider's native scale)
- `score` is `null` if `error` is non-null
- `label` is the small enum above (consistent across providers)
- `input_text_sha256_prefix` is first 16 hex chars of SHA-256 — privacy-friendly dedupe key
- `raw` is the provider response with auth headers stripped via the redaction helper
- `error` mutually exclusive with successful `score`

## Env strategy

```
DETECTOR_PROVIDER=mock|sapling      default mock
SAPLING_API_KEY=...                 only required for real-mode opt-in
DETECTOR_TIMEOUT_SEC=10             default 10s
```

Pre-seeded `.env.example` at the dogfood baseline documents these.
The actual `.env` ships with `DETECTOR_PROVIDER=mock` only.

API key handling:
- env-only at runtime (NEVER hardcoded)
- NEVER appears in logs / error messages / artifact JSON / exception traces / git history
- Stripped from `raw` field before persistence by the adapter (case-insensitive
  match on auth-shaped header keys: `authorization`, `x-api-key`, `*-token`,
  `*-key`, `*-secret`)

## Endpoint shape

```
POST /detect
Body: {"text": str, "provider": str | None}
Response: 200 with DetectorResult JSON (always; failure cases use error field)
```

Plus internal Python module `backend/app/detectors/{base.py, mock.py,
sapling.py, cli.py, __init__.py}` for the rewrite engine to consume
in future milestones (RC-3G).

## Artifact shape

```
backend/data/detector/runs/detect_<12-char-hash>/detector-result.json
```

- `<hash>` = `sha256(text + provider + mode)[:12]` — deterministic
- Per-call dir gitignored; `runs/.gitkeep` keeps the parent dir tracked
- `raw` field stored REDACTED

## Failure behavior

| Trigger | Response | Block deploy? |
|---|---|---|
| `DETECTOR_PROVIDER=sapling` + `SAPLING_API_KEY` missing | `mode=mock`, `error.code=missing_api_key` (silent fallback) | No |
| Sapling 401/403 | `mode=real`, `score=null`, `error.code=auth_failed` | No |
| Sapling 429 | `mode=real`, `score=null`, `error.code=rate_limited` | No |
| Sapling timeout (>`DETECTOR_TIMEOUT_SEC`) | `mode=real`, `score=null`, `error.code=timeout` | No |
| Sapling 5xx | `mode=real`, `score=null`, `error.code=provider_unavailable` | No |
| Sapling unparseable JSON / wrong schema | `mode=real`, `score=null`, `error.code=bad_response` | No |

**ALL detector failures are observe-only in RC-3F.** Never block
`npm run build` / integration / Vercel deploy. Detector is signal,
not gate.

## What RC-3F tests

- Codex authoring a typed schema + ABC + redaction helper using only
  stdlib + existing pydantic
- Codex implementing a deterministic mock provider (no random, no time)
- Codex calling a real third-party SaaS API via `httpx` (already in deps,
  NO new HTTP libs)
- Codex correctly handling secret env vars (read-only, never persisted)
- Codex implementing all 6 failure modes with non-blocking semantics
- Codex normalizing a real provider's response into the unified schema
- Codex preserving THREE previously-built frontend sections while
  adding a fourth (Processing Service / Style Guide RAG / Eval Suite /
  Detector Report)
- Codex shipping the locked compliance text VERBATIM in product copy
- Cross-RC discipline: `requirements.txt` byte-identical across 8+
  Python tasks now (RC-3C/D/E/F)

## What RC-3F deliberately does NOT test

- Real OpenAI / Anthropic / any LLM API
- LLM-as-judge / GPT-evaluator
- GPTZero / Originality.ai / Copyleaks / Pangram / Winston / ZeroGPT
  / Turnitin (RC-3G+)
- Multi-detector ensemble / comparison report (RC-3G+)
- Detector-informed rewrite loop (RC-3G — capped 2-3 rounds)
- Real cost values (>0)
- numpy / pandas / sklearn / sentence-transformers / langchain /
  ragas / deepeval / evals / requests / aiohttp / urllib3
- Detector score as deployment gate
- Detector inside `npm run build` chain
- New `agent-studio detect` CLI subcommand
- Auth / Stripe / billing / dashboard
- File upload for detection
- Browser-side `fetch('/detect')` calls
- Real backend deployment (FastAPI stays local-only; Vercel
  `experimentalServices` block stays inert)

## Local validation (done in prep)

| Gate | Result |
|---|---|
| `bash -n scripts/rc3f.sh` | ✅ SCRIPT SYNTAX OK |
| `chmod +x scripts/rc3f.sh + backend/scripts/detect.sh` | ✅ both executable |
| `bash scripts/backend-test.sh` (in cloned validation dir) | ✅ **24 passed in 0.20s** (full RC-3E end-state inherited: 1 health + 2 rewrite + 3 evaluate + 3 style_guides + 3 retriever + 3 eval_cases + 4 eval_cli + 5 eval_metrics — exact split varies by what RC-3E task-002 emitted) |
| `npm install` | ✅ 114 packages in 25s |
| `npm run typecheck` | ✅ clean |
| `npm run detect:real` (pre-task-003) | ✅ fails clearly with `[backend-detect] backend/app/detectors/cli.py not found. RC-3F task-003 must add it; this baseline ships only the runner script + .env.example.` — **exactly the designed pre-task-003 behavior** |
| pytest cache landing | ✅ `backend/.venv/.pytest_cache/` (RC-3E.1 redirect intact); `backend/.pytest_cache/` does NOT exist |

`npm run build` is not run in sandbox: known `binaries.prisma.sh`
403 (RC-3B/C/D/E all proved Prisma 5.22.0 works on Mac and Vercel).

## Failure predictions (P1-P12)

| ID | Prediction | Most likely fix layer |
|---|---|---|
| P1 | Codex imports `requests` / `aiohttp` / `urllib3` instead of `httpx` | prompt + requirements.md (do NOT accept dep) |
| P2 | Sapling URL/API key hardcoded in `sapling.py` | task-002 acceptance + prompt tightening |
| P3 | `raw` response persisted with `Authorization: Bearer ...` value visible (redaction helper bug or not invoked) | task-001 redaction-helper test + task-002 raw-store path |
| P4 | Mock detector nondeterministic (`random` / `time.time()`) | task-001 acceptance pins determinism test |
| P5 | Missing `SAPLING_API_KEY` raises HTTP 500 instead of falling back to mock | task-002 explicit "always 200" + endpoint test |
| P6 | Codex adds `npm run detect:real` to `npm run build` chain → blocks deploy | task-003 explicit "NOT in build" + `build` script invariant |
| P7 | Frontend tries to fetch `/detect` at runtime | task-003 acceptance: Server Component, no fetch |
| P8 | Codex modifies `rewrite_golden.jsonl` / eval modules / RC-3D RAG modules | scope wording (`backend/app/detectors/**` + `app/**` only) |
| P9 | Detector artifact path mismatch with `scripts/rc3f.sh` post-run grep | task-003 pins exact `backend/data/detector/runs/detect_*/detector-result.json` path |
| P10 | pytest accidentally hits real Sapling network (forgot to mock `httpx.Client.post`) | task-002 acceptance: every Sapling test must mock httpx; CI/sandbox proves no outbound network |
| P11 | Codex helpfully adds GPTZero / Originality / Copyleaks adapters too | task-001/002 explicit "one provider per milestone" wording |
| P12 | Codex adds rewrite loop based on detector score (`while score > X: rewrite`) | **COMPLIANCE BOUNDARY VIOLATION**. Reject and tighten requirements.md. RC-3F is shape-only; loop logic is RC-3G with capped 2-3 rounds + composite scoring per `project_product_strategy.md` |

## Operator pre-checklist (do BEFORE `--run`)

1. **Decide if you want real-mode test post-run**: optionally sign up
   for Sapling free tier at https://sapling.ai → developer console →
   generate API key. Skip if you want mock-only RC-3F (still fully
   validates the adapter shape).

2. **Create new Vercel project** `rc3f-detector-adapter-probe` via
   `vercel link`:
   ```bash
   cd ~/Documents/LocalAgents/.dogfood/rc3f-detector-adapter-probe
   vercel link
   ```
   Suggested answers: yes / pianxing11281128's projects / no /
   `rc3f-detector-adapter-probe` / `./` / no.

3. **(Observation only)** `vercel link` may again auto-detect `backend/`
   and offer multi-service config. Accept or decline; the seed's
   `vercel.json` is frontend-only and RC-3F doesn't activate backend
   deploy regardless.

4. **Disable Vercel Authentication**:
   ```bash
   open "https://vercel.com/pianxing11281128s-projects/rc3f-detector-adapter-probe/settings/deployment-protection"
   ```
   Settings → Deployment Protection → Vercel Authentication: Disabled.
   Save.

5. **Update `~/.local-agent-vercel.env` — corrected recipe** (do NOT
   `source` old env file before `cat >`-ing the new one):
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

6. **(Optional)** Add `SAPLING_API_KEY` for real-mode test post-run:
   ```bash
   echo "export SAPLING_API_KEY='sk_...'" >> ~/.local-agent-vercel.env
   echo "export DETECTOR_PROVIDER=sapling" >> ~/.local-agent-vercel.env
   ```
   Skip this step to keep RC-3F purely mock-mode.

7. **Verify env**:
   ```bash
   source ~/.local-agent-vercel.env
   python3 - <<'PY'
   import os
   for k in ["VERCEL_TOKEN", "VERCEL_ORG_ID", "VERCEL_PROJECT_ID", "SAPLING_API_KEY", "DETECTOR_PROVIDER"]:
       v = os.environ.get(k)
       print(f"{k}: {'present' if v else 'missing'}")
   PY
   ```

8. **Verify deployment URL after run** starts with
   `rc3f-detector-adapter-probe-...` (NOT rc3a/b/c/d/e).

9. **Dry-run first**:
   ```bash
   cd ~/Documents/LocalAgents
   ./scripts/rc3f.sh
   ```

10. **Then real run**:
    ```bash
    ./scripts/rc3f.sh --run 2>&1 | tee /tmp/rc3f-run.log
    ```
    The script will execute `npm run detect:real` once after the
    autonomous run completes, print the detector artifact path + a
    summary of key fields (provider, mode, score, label, error if
    any), and exit. Detector failure does NOT block this script
    (observe-only) but the warning is surfaced.

11. **After run, verify locally**: cat the detector artifact:
    ```bash
    PROJ=$(ls -d /tmp/rc3f-real/.agent-studio/projects/*/ | head -1 | sed 's:/$::')
    cat "$PROJ"/backend/data/detector/runs/detect_*/detector-result.json | python3 -m json.tool | head -50
    ```

## What NOT to build yet (explicit)

Per the locked spec:
- `scripts/rc3f.sh --run` (operator action only, after pre-checklist)
- Calling Codex / OpenAI / Anthropic / any LLM API
- LLM-as-judge / GPT-evaluator
- GPTZero / Originality.ai / Copyleaks / Pangram / Winston / ZeroGPT
  adapters (RC-3F.x or RC-3G)
- Multi-detector ensemble / comparison report (RC-3G+)
- Detector-informed rewrite loop (RC-3G; capped 2-3 rounds with
  composite scoring; **NEVER** `while score > threshold: rewrite`)
- numpy / pandas / sklearn / sentence-transformers / langchain /
  ragas / deepeval / evals
- requests / aiohttp / urllib3 (httpx is already available)
- Real cost values (>0)
- Detector score as deployment gate
- Eval / detector inside `npm run build` chain
- New `agent-studio detect` CLI subcommand (Studio runtime change)
- Detector dashboard / web UI
- Auth / Stripe / billing
- Real backend deployment / activate Vercel `experimentalServices`
- File upload for detection
- Per-user metering / quota
- General "DetectorPipeline" abstraction with strategy pattern
  (premature; needs 2+ real adapters first)
- Starting RC-3G

## Status lock + next milestone

State: **RC-3F prep complete, holding for "go RC-3F run" signal.**

Next milestone after RC-3F run lands: **RC-3G — Detector-Informed
Rewrite Flow** (capped 2-3 rounds, composite scoring; the actual
"detector signal feeds rewrite candidate selection" behavior). NOT
started; needs scoping conversation BEFORE prep.
