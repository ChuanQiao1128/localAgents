# RC-3A Success Report — Next.js + Tailwind Shape Probe End-to-End

Date: 2026-05-11.
Status: **Result A** — full ladder pass on real services.

## Outcome

```
session:           session_67125a42ed in /tmp/rc3a-real
project:           project_0002de7d73 (AI Writing Humanizer — RC-3A Shape Probe)
session.status:    completed
deployment:        verified
deployment URL:    https://rc3a-saas-shape-f3r3fq4wz-pianxing11281128s-projects.vercel.app
smoke:             passed (smoke_3465c8d13d, GET / → 200)
review queue:      0 open
validate-artifacts: ok=true
```

**Per-task git history (clean):**

| Task | Commit | Decision | Candidate | Patch worker |
|---|---|---|---|---|
| baseline | `0a1a2b7` | — | — | — |
| task-001 Add a landing-page hero with project intro | `9db124c` | promote | candidate-a | codex |
| task-002 Add a textarea input section | `0c9793e` | promote | candidate-a | codex |
| task-003 Add a mock process button and result panel | `07b8879` | promote | candidate-a | codex |

Codex inner-run wall-clock: 1m 43s + 1m 30s + 1m 29s = ~5 min.
4/4 integrations passed (`npm run typecheck` + `npm run build`), each ~7s.
Single Vercel deploy: 47s (upload + build + verify).

## Validates

This is the first run of the runtime against a real **Next.js 15 + Tailwind + TypeScript** project shape. RC-2C.2 had validated the same ladder against a static dogfood. RC-3A confirms:

- The runtime drives a Next.js project shape with no Next.js-specific code anywhere in the runtime.
- `next build` + `tsc --noEmit` integrate cleanly.
- Vercel detects + deploys Next.js when `vercel.json: {"framework":"nextjs"}` is present.
- Smoke check works against a real Vercel preview URL after Deployment Protection is disabled at project level.
- `tsbuildinfo` runtime-classification fix lets TypeScript incremental builds coexist with the Promotion Gate's `diff_within_scope` rule.
- Codex 0.130.0 produces correct `"use client"` + `useState` patches without prompt-side reinforcement.

## What changed during this run (6 observed failure branches across 5 recovery cycles)

| # | Failure surfaced | Root cause | Fix layer |
|---|---|---|---|
| 1 | `npm ci: no package-lock.json` | dogfood seed had no lockfile; `npm ci` requires one | `scripts/rc3a.sh` — fall back to `npm install` if no lockfile |
| 2 | task-001 needs-human-review (clean Codex patch refused by Promotion Gate) | `tsconfig.tsbuildinfo` (TS incremental output) classified as `category=source`, tripping `diff_within_scope=False` | runtime: `_discover_files` ignores `*.tsbuildinfo` (alongside existing `next-env.d.ts`); + targeted unit test |
| 3 | `vercel deploy` exit 1: `outputDirectory "public" not found` (rc2-creator-tracker URL) | `VERCEL_PROJECT_ID` still pointed at old static rc2 project | operator: created new `rc3a-saas-shape` Vercel project, updated env file |
| 4 | `vercel deploy` exit 1: same error, new URL `rc3a-saas-shape-*` | new Vercel project was created without git-import, defaulted to framework=Other | dogfood: `.dogfood/rc3a-saas-shape/vercel.json = {"framework":"nextjs"}` (overrides project setting) |
| 5 | `vercel deploy` exit 1: `Vulnerable version of Next.js detected` | Next 15.1.6 has CVE-2025-66478; Vercel hard-blocks vulnerable versions in deploy | dogfood: bump `next` to `15.5.18` (npm dist-tag `backport`) |
| 6 | smoke check `GET /` → 401 | Vercel Deployment Protection / Vercel Authentication on by default for new project | operator: dashboard → Settings → Deployment Protection → Vercel Authentication: Disabled |

Of the six failure branches (across 5 recovery cycles): #1 and #2 became permanent **runtime/dogfood code changes** in the repo; #3, #4, #6 are **operator state** (not in repo); #5 is a **dogfood dependency bump**.

## Predictions vs reality

From `docs/rc3a-prep-report.md`:

| Prediction | Outcome |
|---|---|
| **P1** Codex forgets `"use client"` directive | **Wrong.** Codex got it right autonomously across 3 separate sessions — no prompt change needed. |
| **P2** Tailwind purge / content path mismatch | Never fired. |
| **P3** `npm ci` timeout | Wrong reason but adjacent — actual failure was `npm ci` without a lockfile, not timeout. Caught by script fallback. |
| **P4** `agent-studio new --from` parser issue with TS | Never fired. Parser was language-agnostic as predicted. |
| **P5** Codex `.tsx` shape (whole-file rewrite, JSX-as-string, etc) | Never fired. Codex emitted clean diffs against the existing `app/page.tsx`. |
| **P6** Vercel preview deploy: project link + build runtime | **Confirmed**, in four sub-modes: stale PROJECT_ID, new project framework default, CVE hard-block, SSO/Auth default-on. Each was a separate operator/dogfood fix. |

The unpredicted real failure was #2 (`tsbuildinfo` classification) — pure runtime gap that surfaced only because RC-3A is the first TypeScript project the runtime has seen. RC-2C used static HTML; the gap could not have surfaced earlier.

## Discipline observations

- 5 reruns to land Result A. Each rerun was justified by a real, distinct failure with a real artifact trail. No speculative fixes were applied between runs.
- Total Codex tokens spent across the 5 runs: ~5 × ~75-120k = ~400-600k. Within sane bounds for a milestone validation.
- The only product-code change to the runtime was the `*.tsbuildinfo` filter (~2 lines + 1 test). All other changes were dogfood seed or operator state.
- The runtime correctly emitted blocking review items at every failure point and persisted full evidence (sanitized stderr, classification, URLs). No 'best-effort retries' or 'auto-resolve' attempts were made by the runtime, which is the correct behavior — a deploy failure should pause and demand human attention rather than burn tokens repeating.

## Followups deliberately deferred

- `.dogfood/rc3a-saas-shape/package-lock.json` — would let the script use deterministic `npm ci`. Hygiene only; the `npm install` fallback works. Defer until needed.
- `next-env.d.ts` is already filtered at runtime (`_discover_files`); the dogfood `.gitignore` adds it for defense in depth, not because the runtime needs it.
- POST-RUN TRIAGE block in `scripts/rc3a.sh` does not enumerate the smoke-401 case explicitly; can add when the cost of writing it < the cost of operators rediscovering it. Not now.

## Status lock + next milestone

State: **RC-3A SUCCEEDED, holding.**

Next milestone: **RC-3B — Prisma data model probe.** NOT started, awaiting explicit go signal. RC-3B will be the first probe involving:
- a database (sqlite for dev, postgres for prod)
- a schema (`prisma/schema.prisma`)
- migrations (`prisma migrate`)
- generated client output (`@prisma/client`)

Predicted-but-unverified RC-3B failure surfaces (write later in `docs/rc3b-prep-report.md` once go signal arrives):
- Prisma `generate` output paths likely classified the same way `tsbuildinfo` was — needs the same kind of runtime filter
- Vercel build needs `prisma generate` in build command
- Migration files need to be committed but not under product `Scope:` — gate may flag as out-of-scope source

DO NOT pre-build any of this. Wait for the dogfood to surface real failures first.
