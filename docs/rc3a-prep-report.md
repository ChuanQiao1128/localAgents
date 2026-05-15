# RC-3A Prep Report — Next.js + Tailwind Shape Probe

Date: 2026-05-11.
Status: **prep complete, NOT yet executed.** Awaiting explicit
"go RC-3A run" signal from operator.

## Goal

Prove Local Agent Dev Studio can drive a Next.js 15 + Tailwind +
TypeScript project shape end-to-end (real `requirements.md` →
real Codex × 3 small UI tasks → `npm run build` → real Vercel
preview deploy → real smoke check) with **NO new product code in
the runtime** — only the dogfood project + a runner script.

This is the first vertical-adapter probe of the new "Next.js SaaS
Factory" framing. It is intentionally tiny (3 client-side UI tasks,
no auth, no DB, no API routes, no real AI) so that any failure
surfaces at the *project shape* layer — TypeScript, App Router,
Tailwind, npm, Next.js build — and not at the product complexity
layer. RC-3B (Prisma), RC-3C (external services), RC-3D (auth
+ billing) are deliberately deferred.

## Prepared artifacts

All files committed to `LocalAgents/`:

```
.dogfood/rc3a-saas-shape/
  package.json              Next 15.1.6 + React 19.0.0 + TS 5.7.3 + Tailwind 3.4.17
  next.config.mjs           reactStrictMode: true (minimal)
  tsconfig.json             strict, App Router compatible, "@/*" path alias
  tailwind.config.ts        content: app/**/*.{ts,tsx}, components/**/*.{ts,tsx}
  postcss.config.mjs        tailwindcss + autoprefixer
  .gitignore                node_modules, .next, out, dist, .vercel, .env*.local
  app/
    layout.tsx              <html><body>{children}</body></html> + metadata
    page.tsx                placeholder hero (Codex replaces in task-001)
    globals.css             @tailwind base/components/utilities + body bg-slate-50
  requirements.md           3 H2 tasks; scope: app/page.tsx, app/**, components/**

scripts/rc3a.sh             dry-run by default, --run executes
docs/rc3a-prep-report.md    this file
```

The dogfood project is a real Next.js 15 App Router app with a
working `npm run build` — verified by manual `npm install && npm
run build` to produce a clean `.next/` (NOT committed; only
`package.json` + `package-lock.json` are committed via the
baseline so `npm ci` deterministically installs the same tree
inside the dogfood workspace).

## Runner script — how it diverges from `scripts/rc2c.sh`

`scripts/rc3a.sh` mirrors `scripts/rc2c.sh` (same dry-run-by-default
discipline, same belt-and-suspenders refusal of `production` /
`prod: true`, same `~/.local-agent-vercel.env` sourcing, same
budget caps). Three deltas:

1. **`npm ci` step (NEW)** — between agent-studio seeding and
   `autonomous start`. Static-HTML RC-2C didn't need `node_modules`
   on disk; Next.js does, because the integration step runs `npm
   run build` inside the seeded project dir. Without `npm ci`,
   integration would fail at the very first task with
   `MODULE_NOT_FOUND: 'next'`.
2. **Integration timeout bumped to 900s** (default 600s). A cold
   first Next.js build is meaningfully slower than the static
   dogfood (`next build` does TS compile + bundle + RSC payload
   generation). 900s gives one slow build comfortable headroom.
3. **No `vercel.json` is written** — Vercel auto-detects Next.js
   from `package.json`. `vercel.json` was a workaround for the
   static dogfood needing `outputDirectory: dist`.

Budgets are unchanged from the RC-2C.2 clean pass:
`max_tasks_per_session=3`, `max_candidates_per_task=1`,
`max_total_inner_runs=3`, `max_repair=1`, `max_abandoned=1`,
`max_corrective=1`. If any task abandons, the session pauses
immediately — exactly what we want for a probe.

## Validation done in prep

- `bash -n scripts/rc3a.sh` → SYNTAX OK.
- Full dry-run with mocked `codex`/`vercel`/Vercel env →
  prints all expected commands; no command shape regressed
  vs `scripts/rc2c.sh`; rendered `agent-studio.yaml` is the
  exact RC-3A target shape (deploy.enabled=true preview, no
  rollback, smoke `/` GET 200).
- `requirements.md` parser shape: 3 H2 sections with explicit
  `Scope:`, `Risk:`, and (for tasks 2/3) `Depends:` lines —
  same convention the RC-2C deterministic parser handled
  without adapter changes.

## Predictions — what RC-3A is most likely to surface

These are the hypotheses to triage against once the run completes.
Predictions are **technical-shape**, not "will it succeed" — the
goal is to know what to look at first if anything trips, and to
have a falsifiable record of where current intuition was wrong.

### P1 — `"use client"` directive (task-002)

Highest-likelihood real failure. The textarea task explicitly
requires `"use client"` + `useState`, but Codex has no project
memory that Next.js 15 App Router defaults to **server**
components. The success criteria spell it out, but Codex may
still:

- forget the directive entirely → `useState is not defined`
  build error, OR
- put the directive on the wrong file (e.g. `layout.tsx`
  instead of `page.tsx`), OR
- introduce a separate client component but not import it.

**Triage:** if integration fails on task-002 with a "useState
can only be used in Client Components" error, this is what
to fix in the prompt template (App Router awareness in
`_render_patch_worker_prompt`), not in product code.

### P2 — Tailwind class purge / content path mismatch

If Codex creates a new file outside `app/**` or `components/**`
(e.g. `lib/`, `src/`), Tailwind's content scan will purge the
classes and the page will render unstyled. The build will pass
(no error), the smoke will pass (200 OK), but the page will be
visually broken. Smoke check is HTTP-only, so this won't fail
RC-3A, but it would be a real product regression in any human
review.

**Triage:** if the deployed preview looks unstyled, the fix is
either to broaden `tailwind.config.ts` `content` globs in the
dogfood, or to constrain the `Scope:` line in `requirements.md`
more aggressively. Preference is the latter — keep the dogfood
honest.

### P3 — `npm ci` cache cold + integration timeout

First `npm ci` on a clean machine takes 30-90s; cached, ~5s.
We bumped integration timeout to 900s, but if the operator's
machine is slow or `npm` is rate-limited, the **first** task's
integration might still tip over the edge because integration
re-resolves `next build` work each task.

**Triage:** if `integration-failure.json` shows
`failure_class=timeout` on task-001 only (and tasks 2/3 don't
re-trigger it), bump `integration.timeout_sec` further and
re-run. NOT a real bug.

### P4 — `agent-studio new --from` behavior with TypeScript

`requirements.md` does NOT mention `.tsx` or TypeScript
explicitly. The deterministic parser builds `task-graph` from
H2 headers and the `Scope:` / `Depends:` / `Risk:` lines. It's
not language-aware. **Expectation:** parser produces the same
3-task graph it would for any other project, and the seeding
loop just copies `package.json` + `tsconfig.json` over — no
parser change needed. If RC-3A fails *before* task-001 starts,
look at `agent-studio new --from` first.

### P5 — Codex patch shape vs Next.js convention

In RC-2C, Codex generated patches against static `index.html`
files. RC-3A patches will touch `.tsx` files. Two failure
modes specifically possible:

- **Stale `export default function Page()` shape**: Codex may
  rewrite the entire `app/page.tsx` rather than incremental-edit
  it. With `max_candidates_per_task=1`, the single attempt has
  to be right; no fallback. Should still be fine because the
  baseline file is intentionally trivial.
- **JSX in TS string**: if Codex emits HTML/JSX inside a string
  template (not a `.tsx` return) the build will fail. Unlikely
  given Next.js examples are common in pretraining.

**Triage:** if this fires, the fix is in `context_pack`
(Next.js + App Router awareness lines, not file contents), not
the product.

### P6 — Vercel preview deploy: project link + build runtime

This is the one we have the most empirical confidence in
(RC-2C.2 passed it cleanly). RC-3A runs against a NEW Vercel
project name `rc3a-saas-shape` — `.vercel/` link directory does
NOT pre-exist. First `vercel deploy` will use `VERCEL_PROJECT_ID`
env to auto-link. If the operator's `VERCEL_PROJECT_ID` points
at the *old* `rc2-creator-tracker` project, the deploy will
succeed but the preview will be a Next.js build under a static-
configured project, which may 404 on `/`.

**Triage:** if smoke fails 404, **first check the deployment
URL manually** — if it's under `rc2-creator-tracker-*`, the
operator needs a new `VERCEL_PROJECT_ID`. NOT a runtime bug.

## Out of scope for RC-3A — explicitly deferred

- Auth (RC-3D)
- Prisma / database (RC-3B)
- API routes (RC-3B/C)
- Stripe / billing (RC-3D)
- Real AI calls / OpenAI / Anthropic (RC-4 — the actual product
  layer of AI Writing Humanizer)
- Multi-page routing
- Dynamic route segments
- Server actions
- Image optimization
- ESLint / `next lint` enforcement
- TypeScript strict-error gating beyond what `next build` does
- `next build` warning surfacing in eval feedback
- E2E test framework (Playwright / Vitest)

If RC-3A's `next build` happens to surface any of these as a
side effect, log it and **resist the urge to harden** — the
prep discipline is to wait for RC-3B/C/D's natural failure to
justify any new product code.

## What success looks like (Result A)

- `autonomous start` completes (status=completed, no pause).
- 3 commits on `agent-studio/session-*` branch with evidence
  trailers + `Patch-Worker: codex`.
- Each task selected `candidate-a` only (budget cap honored).
- Integration ran at least twice (every_n_tasks=1 +
  run_at_session_end), both passed.
- `deployment.json` status=`ready`, deployment URL printed.
- `smoke-check.json` status=`passed`, home `/` returned 200.
- `validate-artifacts --json` ok=true.
- 0 open review items.

If we land Result A, RC-3A is verified and the next milestone
is **RC-3B** (Prisma data model probe). If anything in the P1-P6
list above surfaces, the spec for "real failures only" applies:
fix the prompt / context_pack / repair-loop / eval-wiring as
narrowly as the failure justifies — no speculative hardening.

## Hold state

Prep is complete and waiting. The next action is the operator
running `scripts/rc3a.sh --run` on their Mac (where the Vercel
env file and Codex CLI live). I will not start the run, edit
runtime code, or pre-emptively patch any of P1-P6 without first
seeing the run produce real artifacts.
