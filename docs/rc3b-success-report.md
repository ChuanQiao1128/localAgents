# RC-3B Success Report — Prisma Data Model Probe End-to-End First Try

Date: 2026-05-11.
Status: **Result A** — full ladder pass on real services, no reruns,
all 7 P1-P7 predictions falsified.

## Outcome

```
session:           session_c9f96e897d in /tmp/rc3b-real
project:           project_51d8b78782 (AI Writing Humanizer — RC-3B Prisma Data)
session.status:    completed
deployment:        verified
deployment URL:    https://rc3a-saas-shape-6qlupnoyg-pianxing11281128s-projects.vercel.app
                   (NB: URL hostname says rc3a-saas-shape — see "Operator gotcha" below;
                   the runtime + Prisma + deploy chain still all worked end-to-end)
smoke:             passed (smoke_c42efde695, GET / → 200)
review queue:      0 open
validate-artifacts: ok=true
```

**Per-task git history (clean):**

| Task | Commit | Decision | Candidate | Wall-clock |
|---|---|---|---|---|
| baseline | `bcbc1b8` | — | — | — |
| task-001 Add the RewriteJob model | `2b9fb01` | promote | candidate-a | 1m 22s |
| task-002 Add the StyleGuide model | `7c10014` | promote | candidate-a | 1m 28s |
| task-003 Add the RewriteResult model with relation to RewriteJob | `39a997f` | promote | candidate-a | 1m 17s |

Codex inner-run wall-clock: ~4m 7s total (faster than RC-3A's ~5m). Each
integration: ~6.5-8s (`prisma generate && next build`). Single Vercel
deploy: 40s.

## Validates

This is the first run of the runtime against a real **Next.js 15 + Tailwind + TypeScript + Prisma 5.22.0** project shape. RC-3A had validated the Next.js shell; RC-3B confirms the runtime can extend that shell with a real ORM and a generated client — without any Prisma-specific code in the runtime. Specifically:

- Codex authors `schema.prisma` correctly across 3 sequential edits.
- Codex correctly handles the multi-block patch case in task-003 (adds RewriteResult model AND adds the back-reference field on the existing RewriteJob model in the same commit, with relation consistency).
- `prisma generate && next build` integrates cleanly — no separate `prisma validate` needed because `generate` validates implicitly.
- Vercel auto-detects the Prisma+Next.js combo; the pre-baked `build` script (`prisma generate && next build`) is sufficient — no additional Vercel project settings required.
- `_discover_files` correctly excludes the generated `node_modules/@prisma/client/**` (already covered by `node_modules/` in `ignored_dirs`); no analogue of the RC-3A.7 `tsbuildinfo` gap surfaced.
- The dogfood `.env` (`DATABASE_URL="file:./dev.db"`) flows through the seed → integration → Vercel build chain unmodified; Codex didn't touch it.

## Final schema content (Codex-generated, verified)

```prisma
generator client {
  provider = "prisma-client-js"
}

datasource db {
  provider = "sqlite"
  url      = env("DATABASE_URL")
}

model RewriteJob {
  id        String   @id @default(cuid())
  inputText String
  tone      String
  status    String
  createdAt DateTime @default(now())
  updatedAt DateTime @updatedAt
  results   RewriteResult[]
}

model StyleGuide {
  id        String   @id @default(cuid())
  name      String
  content   String
  createdAt DateTime @default(now())
}

model RewriteResult {
  id            String     @id @default(cuid())
  rewriteJobId  String
  rewrittenText String
  changeSummary String?
  createdAt     DateTime   @default(now())
  rewriteJob    RewriteJob @relation(fields: [rewriteJobId], references: [id])
}
```

Every field, every default, every relation matches the requirements. The RewriteJob `results RewriteResult[]` back-reference (added in task-003 alongside the new RewriteResult model) is the load-bearing test: if Codex had skipped it, `prisma generate` would have failed.

## Predictions vs reality

From `docs/rc3b-prep-report.md`:

| Prediction | Outcome |
|---|---|
| **P1** generated client out-of-scope | **Wrong.** `node_modules/@prisma/client/**` is already filtered by the existing `node_modules/` rule in `_discover_files` — never reached changed-files. |
| **P2** Codex forgets back-reference on task-003 | **Wrong.** Codex added `results RewriteResult[]` on RewriteJob in the same commit as the new RewriteResult model. `prisma generate` passed. |
| **P3** `npm install` + `prisma generate` postinstall flakiness | Did not fire. 145 packages in 19s, no errors. |
| **P4** Vercel build needs `prisma generate` in build chain | Did not fire because we pre-baked `"build": "prisma generate && next build"` into package.json. |
| **P5** DATABASE_URL not visible to Vercel build | Did not fire. `prisma generate` reads the schema only; no env var needed at build time. |
| **P6** Eval harness wiring: `prisma:validate` not invoked | Did not fire. `prisma generate` validates implicitly; no separate step needed. |
| **P7** `.env` leaks into changed-files | Did not fire. Codex correctly stayed in `prisma/**` scope. |

**0 of 7 predictions fired.** This is the cleanest probe in the project's history. RC-3A surfaced 6 failure branches across 5 cycles; RC-3B surfaced zero.

## Operator gotcha (NOT a runtime bug, log for future probes)

The `vercel link` + env-rewrite recipe used before this run accidentally re-loaded the OLD `VERCEL_PROJECT_ID` because `source ~/.local-agent-vercel.env` ran AFTER the new exports. Net effect: deployment landed in the OLD `rc3a-saas-shape` Vercel project, not the newly-linked `rc3b-prisma-probe`. Functionally invisible (same scope, same Next.js shape, auth already disabled), but worth fixing the recipe.

**Bad pattern (from this run):**
```bash
export VERCEL_ORG_ID="$(...)"        # from new .vercel/project.json
export VERCEL_PROJECT_ID="$(...)"    # rc3b's new ID
source ~/.local-agent-vercel.env     # ← overwrites the new exports with OLD values
cat > ~/.local-agent-vercel.env <<EOF
export VERCEL_PROJECT_ID='$VERCEL_PROJECT_ID'  # ← writes the OLD value back
...
```

**Fix:** for future RC-3C+ probes, write the env file directly from `.vercel/project.json` without `source`-ing first:

```bash
cat > ~/.local-agent-vercel.env <<EOF
export VERCEL_TOKEN='<existing token>'
export VERCEL_ORG_ID='$(python3 -c "import json; print(json.load(open('.vercel/project.json'))['orgId'])")'
export VERCEL_PROJECT_ID='$(python3 -c "import json; print(json.load(open('.vercel/project.json'))['projectId'])")'
EOF
```

The `rc3b-prisma-probe` Vercel project (prj_CV7NRZm5fENjOF6fH4xDI56AlfU2) was created via `vercel link` but never actually received a deployment in this run. It's there for future reuse if RC-3C wants a fresh project.

## Discipline observations

- **First-try clean pass.** No reruns, no runtime changes, no dogfood seed iterations beyond the one-shot prep. 7 predictions written, 7 falsified.
- This is what dogfood discipline produces when the runtime + adapter assumptions are well-calibrated. RC-3A's 5 reruns built up the context (tsbuildinfo filter, vercel.json learning, CVE awareness, SSO operator pattern); RC-3B inherited all of that and found nothing new.
- Total Codex tokens spent: ~75-120k (single run, 3 tasks × 1 candidate cap, schema patches smaller than UI patches).
- The runtime did not require any code changes for RC-3B. Zero. The only "Prisma awareness" anywhere in the system is in the dogfood's `package.json` (`build` script wraps `prisma generate`), the dogfood's `prisma/schema.prisma`, and the dogfood's `.env`. Everything else is generic.

## Followups deliberately deferred

- The RC-3A.6 `package-lock.json` followup remains deferred for both rc3a and rc3b dogfoods. `npm install` fallback continues to work.
- The `rc3b-prisma-probe` Vercel project exists but unused — keep for if RC-3C wants a clean target.
- The env-rewrite recipe gotcha → write the corrected pattern into RC-3C prep doc when that arrives.

## Status lock + next milestone

State: **RC-3B SUCCEEDED, holding.**

Next milestone: **RC-3C — FastAPI / LLM processing pipeline.** NOT started, awaiting explicit go signal. RC-3C will be the first probe involving:
- A second runtime / second language (Python alongside the Next.js front)
- A second deploy target choice (Vercel for Next.js, but where for Python? Render / Fly / Railway / Vercel serverless functions?)
- Cross-service eval (does integration need to spin up the Python service to validate?)
- LLM API contracts (must be FAKED for the probe — no real OpenAI/Anthropic calls)

Predicted RC-3C failure surfaces (write later in `docs/rc3c-prep-report.md` once go signal arrives):
- The deterministic task parser was built for one project; cross-service tasks may not parse cleanly
- Codex prompt may not handle "modify Python file in this dir AND modify Next.js API route in another dir" well
- Eval harness needs to know how to start a Python process and check it
- Integration timeout: cold-start a Python venv + uvicorn could blow past 900s

DO NOT pre-build any of this. Wait for the dogfood to surface real failures first. Same discipline as RC-3A and RC-3B.
