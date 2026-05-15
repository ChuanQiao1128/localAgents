# RC-3B Prep Report — Prisma Data Model Probe

Date: 2026-05-11.
Status: **prep complete, NOT yet executed.** Awaiting explicit
"go RC-3B run" signal from operator (with confirmation that a new
`rc3b-prisma-probe` Vercel project exists and Deployment Protection
is disabled — RC-3A run-3 / run-5 lessons).

## Goal

Prove Local Agent Dev Studio can drive a Next.js + **Prisma** project
shape end-to-end (real `requirements.md` → real Codex × 3 schema-edit
tasks → `npx prisma validate` + `npx prisma generate` + `next build`
→ real Vercel preview deploy → real smoke). One new dimension on top
of RC-3A: a real ORM and a generated client.

This is the second probe in the Next.js SaaS Factory ladder (after
RC-3A's shape probe). RC-3B intentionally stays at SQLite + datasource
+ generator + 3 model edits. NO real Postgres, NO migrations
framework, NO auth, NO Stripe, NO RAG. Those are RC-3C through RC-3H.

## Prepared artifacts

```
.dogfood/rc3b-prisma-probe/
  package.json              Next 15.5.18 + React 19 + Prisma 5.22.0 + @prisma/client 5.22.0
  next.config.mjs           reactStrictMode: true (unchanged from rc3a)
  tsconfig.json             strict, App Router compatible (unchanged from rc3a)
  tailwind.config.ts        same content scan (unchanged from rc3a)
  postcss.config.mjs        same (unchanged from rc3a)
  vercel.json               {"framework": "nextjs"} (RC-3A learning)
  .gitignore                rc3a's + prisma/dev.db, prisma/dev.db-journal, prisma/migrations/
  .env                      DATABASE_URL="file:./dev.db" (committed for the dogfood;
                            no real secret. Real projects would gitignore this.)
  app/
    layout.tsx              metadata describes RC-3B
    page.tsx                same baseline placeholder shape as RC-3A
    globals.css             unchanged from rc3a
  prisma/
    schema.prisma           datasource (sqlite, env DATABASE_URL) + generator
                            (prisma-client-js); Codex tasks add models
  requirements.md           3 H2 tasks (RewriteJob → StyleGuide → RewriteResult)

scripts/rc3b.sh             dry-run by default, --run executes
docs/rc3b-prep-report.md    this file
```

## How `scripts/rc3b.sh` differs from `scripts/rc3a.sh`

`scripts/rc3b.sh` mirrors `scripts/rc3a.sh`. Deltas:

1. **Different dogfood + workspace path** — `.dogfood/rc3b-prisma-probe`
   and `/tmp/rc3b-real`.
2. **Seed list adds `prisma/` directory + `.env`** — the schema and the
   DATABASE_URL must reach the worktree.
3. **`build` script is now `prisma generate && next build`** — `next build`
   alone would fail because `@prisma/client` is generated, not pre-shipped.
   This means integration's `npm run build` automatically exercises the
   generated client without an extra eval-harness command.
4. **POST-RUN TRIAGE** explicitly enumerates Prisma-shaped failure modes
   (back-reference forgotten, generated client out-of-scope, missing
   prisma generate in build chain, DATABASE_URL invisible to integration).
5. **Cost note** — npm install adds ~50 MB Prisma deps; total node_modules
   is ~300-350 MB instead of RC-3A's ~250-300 MB.

The agent-studio.yaml is otherwise identical to RC-3A (same budgets,
same integration cadence, same deploy/smoke/rollback config).

## Why models are tasks 1/2/3, in this order

- **task-001 RewriteJob** — independent, self-contained model. First
  ensures basic schema authoring works.
- **task-002 StyleGuide** — also independent. Confirms Codex can edit
  an existing schema without breaking the prior model.
- **task-003 RewriteResult** — has a relation to RewriteJob, **and**
  requires the RewriteJob model to gain a back-reference field
  (`results RewriteResult[]`). This is the "real" Prisma test: a
  multi-line patch touching two models in one schema, with a relation
  consistency requirement.

Putting RewriteResult last makes `Promotion Gate handles multi-file
patch?` a clean test against task-003 specifically. (Note: it's still
single-file — `schema.prisma` — but multi-block within that file.)

## Validation done in prep

- `bash -n scripts/rc3b.sh` → SYNTAX OK
- Full dry-run with mocked codex/vercel → all expected commands print,
  agent-studio.yaml renders correctly with the integration block
  matching RC-3A (no Prisma-specific commands needed because `npm
  run build` now wraps `prisma generate && next build`)
- `prisma/schema.prisma` syntactically valid (datasource + generator
  blocks present; no models yet — Codex will add them per task)
- `requirements.md` parser shape: 3 H2 sections with `Scope:` /
  `Risk:` / `Depends:` lines (same convention RC-3A's parser handled)

## Predictions — what RC-3B is most likely to surface

These are hypotheses to falsify/confirm against the real run. Same
discipline as RC-3A's P1-P6: predictions are **technical-shape**, not
"will it succeed."

### P1 — `prisma generate` output ends up out-of-scope

Highest-likelihood real failure, and the most analogous to RC-3A.7's
`tsbuildinfo` gap. `npm install` runs `prisma generate` on postinstall,
which writes to `node_modules/@prisma/client/` and (depending on
configuration) to `node_modules/.prisma/client/`. Both should be inside
`node_modules/` which `_discover_files` already ignores via
`ignored_dirs`. **But:** if Codex generates a schema that puts
generated output anywhere else (custom `output = "..."` in the
generator block), or if a `.prisma` directory or `*.d.ts` file leaks,
the same `diff_within_scope=False` failure as RC-3A.7 will fire.

**Triage:** if needs-human-review hits with `out_of_scope_changes`
listing anything Prisma-related, fix at `_discover_files` (add filter)
or in `_change_category` / `_context_bucket` (classify as
`generated_or_low_value`). The runtime fix is narrow.

### P2 — Codex forgets the back-reference on task-003

The success criteria for task-003 explicitly require RewriteJob to
gain `results RewriteResult[]`. If Codex only adds the RewriteResult
model and leaves RewriteJob untouched, `npx prisma validate` will
fail with "missing relation back-reference."

**Triage:** if integration fails on task-003 with a Prisma validation
error mentioning back-references, that's prompt-side — `_render_patch_worker_prompt`
should be checked for whether it surfaces multi-model edit
expectations clearly. Don't add a Prisma-specific prompt template;
do tighten the "scope can include adjacent files within scope" wording.

### P3 — `npm install` + `prisma generate` postinstall flakiness

`prisma generate` runs as a postinstall hook. On cold install with
no network cache, Prisma downloads its query engine binary
(~30-50 MB platform-specific). On macOS arm64 + node v24, this is
usually fast and reliable, but if any of: corporate proxy, npm
mirror, Prisma engine version mismatch with Prisma version → install
fails before integration even runs.

**Triage:** if `npm install` exits non-zero in the script (NOT in
integration), the script's `do_or_print` will surface it before
agent-studio starts. NOT a runtime bug. Operator action: check
network / proxy / `prisma --version`.

### P4 — Vercel build needs `prisma generate` in the build chain

We've already pre-baked this into the `build` script:
`"build": "prisma generate && next build"`. This was lifted
straight from Prisma's standard Next.js + Vercel deployment guide.
**Expectation:** Vercel build should pass first try.

**Triage:** if Vercel build fails with `Cannot find module '@prisma/client'`,
the `build` script wasn't picked up — likely a vercel.json overrides
issue. Look at the deploy.json stderr for the actual `Running ...`
line.

### P5 — DATABASE_URL not visible to Vercel build

`prisma generate` does not need DATABASE_URL (it only reads the
schema). `prisma validate` doesn't either. So the Vercel build
should not need any env var. **Expectation:** no DATABASE_URL
env vars need to be set in Vercel project settings for RC-3B.

**Triage:** if Vercel build fails complaining about DATABASE_URL,
it means we accidentally introduced a runtime call (e.g. Codex
imported `@prisma/client` and instantiated `new PrismaClient()`
in a server component). Check the changed files. Should NOT happen
because the requirements.md only asks for schema edits.

### P6 — Eval harness wiring: `prisma:validate` script declared but not invoked

The agent-studio.yaml does NOT add `prisma:validate` to the
integration command list — instead, we rely on `prisma generate &&
next build` (which transitively validates). This is a deliberate
choice: minimum eval surface, max signal. **Expectation:** if
something's wrong with the schema, `prisma generate` will fail before
`next build` even tries. We should not need a separate `prisma
validate` step.

**Triage:** if a syntactically-broken schema slips past integration
because `prisma generate` emits a permissive warning instead of an
error, add `npx prisma validate` to the integration command list.
NOT pre-emptively.

### P7 — `.env` file leaks DATABASE_URL into changed-files

The dogfood `.env` is committed at baseline. If Codex tries to modify
it (e.g. to add `SHADOW_DATABASE_URL`), the change would land outside
the `prisma/**` scope. **Expectation:** Codex won't touch `.env`
because the requirements.md only mentions `prisma/**, schema.prisma`.

**Triage:** if a needs-human-review fires because `.env` was modified,
that's a Codex prompt issue, not a runtime issue.

## Out of scope for RC-3B — explicitly deferred

- Real Postgres / RDS / Neon / Supabase
- Migrations framework (`prisma migrate dev/deploy`)
- Seed scripts (`prisma db seed`)
- Auth (User / Account / Session models, NextAuth, Clerk, etc.)
- Stripe / billing models
- Subscription / Credits / Usage tracking
- Vector chunks / pgvector / RAG
- File uploads
- API routes that consume Prisma client
- E2E tests against the database
- Database connection pooling / serverless adapters
- Vercel Postgres / Vercel KV
- Prisma Edge runtime
- Prisma Studio
- Schema linting tools beyond `prisma validate`

If RC-3B's actual run surfaces any of these as a side effect, log
it and **resist the urge to harden** — wait for RC-3C/D/E/F/G/H to
justify any new product code.

## What success looks like (Result A)

- `autonomous start` completes (status=completed, no pause).
- 3 commits on `agent-studio/session-*` branch with evidence trailers
  + `Patch-Worker: codex`.
- Each task selected `candidate-a` only (budget cap honored).
- Integration ran 4 times (3 periodic + 1 session_end), each
  passing `prisma generate && next build` cleanly.
- `prisma/schema.prisma` ends with all 3 models present + correct
  RewriteJob ↔ RewriteResult relation.
- `deployment.json` status=`ready`, deployment URL printed.
- `smoke-check.json` status=`passed`, home `/` returned 200.
- `validate-artifacts --json` ok=true.
- 0 open review items.

If we land Result A, RC-3B is verified and the next milestone is
**RC-3C** (FastAPI / LLM pipeline). If anything in P1-P7 surfaces,
the spec for "real failures only" applies: fix the prompt /
context_pack / repair-loop / eval-wiring / changed-files
classification as narrowly as the failure justifies.

## Operator pre-checklist (do BEFORE `--run`)

Same shape as RC-3A run-2 onwards, with one new item:

1. **Create a NEW Vercel project** named `rc3b-prisma-probe` in the
   `pianxing11281128s-projects` scope. (You can do this via dashboard,
   then copy the project ID.)
2. **Disable Vercel Authentication** for the new project: Settings →
   Deployment Protection → Vercel Authentication → Disabled. (RC-3A
   run-5 lesson.)
3. **Update `~/.local-agent-vercel.env`** to set `VERCEL_PROJECT_ID`
   to the new project's ID. The same `sed -i ''` one-liner pattern
   from RC-3A applies.
4. **Verify env file** with the same grep-only-PROJECT_ID one-liner
   (does not echo TOKEN or ORG_ID).
5. Run `./scripts/rc3b.sh --run 2>&1 | tee /tmp/rc3b-run.log`.

## Hold state

Prep is complete and waiting. No runtime / dogfood / script changes
beyond what's listed above. I will not start the run, edit runtime
code, or pre-fix any P1-P7 prediction without first seeing the run
produce real artifacts.
