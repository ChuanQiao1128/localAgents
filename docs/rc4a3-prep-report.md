# RC-4A.3 — Real Codex Change-Run Tiny Probe — Prep Report

**Status:** PREP COMPLETE — holding for `go RC-4A.3 run`.

## Goal

Run **one real Codex change request** end-to-end through `agent-studio change run` against a tiny Next.js project. The goal is narrow on purpose: prove that **Change Request Mode (RC-4A.2)** works with the real patch worker, not just the fake-patch-worker e2e test that already passes (`tests/e2e/test_change_run_e2e.py`). Nothing else.

This is the smallest dogfood that exercises every load-bearing surface in the change-mode pipeline:

- `change new` reads the operator's `change-request.md` and writes the 5 RC-4A.1 artifacts.
- `change run` builds a 1-task task-graph, calls the **real** `AgenticProjectRuntime.run` with `patch_worker=codex`, runs Codex in workspace-write sandbox, scores candidates through the 12 hard-rule Promotion Gate, applies through the 10 hard-rule Apply Gate, commits on `agentic/change/<change_id>` with `Change-Id:` + `Source-Change-Request:` trailers.
- `change run` derives `applied-change.json` (`agentic.applied_change.v1`) and `delivery-report.md` from the post-run state.
- `change validate` confirms both artifacts pass the schema validators.

## What's in scope

| File | Purpose |
| --- | --- |
| `.dogfood/rc4a3-change-run-tiny/package.json` | Next.js 15.5.18 + React 19 + TS 5.7. Scripts: `build`, `typecheck`, `dev`, `start`. |
| `.dogfood/rc4a3-change-run-tiny/next.config.mjs` | `reactStrictMode: true`. Nothing else. |
| `.dogfood/rc4a3-change-run-tiny/tsconfig.json` | Standard Next.js TS config (strict, bundler resolution). |
| `.dogfood/rc4a3-change-run-tiny/.gitignore` | `node_modules`, `.next`, `.agent`, `*.tsbuildinfo`, `next-env.d.ts`. |
| `.dogfood/rc4a3-change-run-tiny/app/layout.tsx` | Root layout, body wrapper. Title = "Creator Notes". |
| `.dogfood/rc4a3-change-run-tiny/app/page.tsx` | Client component. Renders `<NotesList notes={SEED_NOTES}/>` with three seed notes (2 active + 1 archived). |
| `.dogfood/rc4a3-change-run-tiny/components/notes.ts` | `Note` type, `NoteStatus` union, `SEED_NOTES` array. |
| `.dogfood/rc4a3-change-run-tiny/components/NotesList.tsx` | Pure list renderer. Empty-state path included. |
| `.dogfood/rc4a3-change-run-tiny/change-request.md` | The change request the operator hands to Codex. |
| `scripts/rc4a3_change_run.sh` | Dry-run by default, `--run` for real. |

## Tiny project shape — Creator Notes

A single-page Next.js app. Two seed components and one page. Inline styles only. No backend, no DB, no Tailwind, no auth, no API routes, no tests, no Vercel config. Three seed notes: two `active`, one `archived`. The list renders title + body + status badge for each note. Empty-state message is wired but unreachable on baseline (3 seed notes → list always non-empty).

```
.dogfood/rc4a3-change-run-tiny/
├── package.json            # next 15.5.18, react 19, ts 5.7 — minimal
├── next.config.mjs
├── tsconfig.json
├── .gitignore
├── change-request.md       # the operator intent for Codex
├── app/
│   ├── layout.tsx
│   └── page.tsx
└── components/
    ├── notes.ts            # Note type + SEED_NOTES
    └── NotesList.tsx       # pure list renderer with empty state
```

## Change request — "Add status filter"

The operator asks Codex to add a filter control with three options (All / Active / Archived) that updates the visible list immediately. Selecting Active hides archived notes; selecting Archived hides active notes; selecting All restores the full list. The empty-state message (already wired) must show when the active filter produces zero matches.

Hard non-goals (so we can detect drift):

- **No new dependencies.** No package.json edits. No package-lock.json edits.
- **No backend / API route / DB.**
- **No tsconfig / next.config / .gitignore edits.**
- **No new build script.** `npm run build` keeps its current shape.
- **No CSS framework** (Tailwind / styled-components / etc.). Inline styles only.
- **No deploy.**

Acceptance criteria (excerpted):

- Filter control with options All / Active / Archived rendered on the page.
- Default = All; all seed notes visible.
- Active hides archived notes; Archived hides active notes; All restores both.
- Empty-state message visible when active filter matches zero.
- `npm run build` passes.
- `npm run typecheck` passes.

Full text in `.dogfood/rc4a3-change-run-tiny/change-request.md`.

## Why no Vercel

Change Request Mode in RC-4A.2 stops at "applied + committed + delivery-report written". Wiring deploy/smoke/rollback is not on the RC-4A.2/4A.3 critical path — RC-4B's demo matrix is the natural place to add a Vercel preview per change because that's where audience-facing artifacts matter. Layering deploy into the RC-4A.3 probe would burn Vercel credits + add another failure surface that has nothing to do with proving the **change runner** works against real Codex.

`agent-studio.yaml` therefore ships with `deploy: { enabled: false }`. The runner script grep-asserts this before continuing.

## Why no demo matrix yet

RC-4B is the demo matrix (3 distinct projects, each with one change). RC-4A.3 is the **single-project sanity check** that real Codex flows through change-mode at all. If RC-4A.3 fails on a load-bearing surface (Apply Gate, commit trailers, applied-change.json, delivery-report.md, branch naming), every project in the demo matrix would inherit the same break. RC-4B starts only after RC-4A.3 has surfaced and resolved any real-Codex bug.

## Success criteria for the real run

The run is a "verified" success if **all** of the following hold:

1. `change new` writes the 5 RC-4A.1 artifacts under `<project>/.agent/changes/<change_id>/`.
2. `change run` invokes real Codex (not the fake patch worker).
3. Promotion Gate selects a candidate (`decision == promote`).
4. Apply Gate succeeds (`git apply` clean, no out-of-scope changes, base/HEAD match, worktree clean modulo `.agent/` + `task-graph.json`).
5. A commit lands on the `agentic/change/<change_id>` branch.
6. The HEAD commit message contains both `Change-Id: change_<id>` and `Source-Change-Request: .agent/changes/<change_id>/change-request.md` trailers.
7. `<project>/.agent/changes/<change_id>/applied-change.json` exists, schema_version is `agentic.applied_change.v1`, and `validate_applied_change` returns `[]`.
8. `<project>/.agent/changes/<change_id>/delivery-report.md` exists and `validate_delivery_report_text` returns `[]`.
9. `npm run build` passes on the change branch (post-Codex).
10. `change validate latest --json` returns `ok=true`.
11. No blocking review items remain open (`autonomous reviews list` is empty).

## Failure predictions

Ranked roughly by expected likelihood:

1. **Codex changes `package.json` despite the non-goal.** The notes app is so small that Codex may decide a state-management library would be cleaner. Apply Gate's `out_of_scope_changes` check should catch this when scope_paths is `app/**, components/**` (package.json is outside scope). Look for `apply_failed` in the controller log + a review item with reason `failed-apply`.
2. **Codex adds a new dependency.** Same shape as #1 — `package.json` mutates. Same gate catches it.
3. **`git apply --check` fails.** RC-3E.2 already replaced the difflib-based patch generator with real `git diff --binary` from an ephemeral repo, and added the `patch_apply_check_passed` hard gate. Risk should be low, but if a binary file (e.g. icon) sneaks in, the patch could fail. Inspect `<run>/candidates/<id>/patch.diff` first.
4. **`npm run build` fails after the change.** Codex passes gates (gates only check shape, not runtime correctness). The patch is applied + committed. Then `npm run build` blows up — usually a missing import, a stale type, or a JSX nesting bug. RC-4A.3 catches this in step 13 of `rc4a3_change_run.sh` (`Confirming npm run build still passes...`); the script surfaces the failure but does NOT roll back the commit. **Highest-value learning** if this happens.
5. **`change run` cannot target the project root.** Current CLI requires the project to be registered under `<root>/.agent-studio/projects/<id>/`. RC-4A.3 sidesteps by creating the project via `agent-studio new "creator notes"` then `cp`-ing the seed in. If the in-place workflow turns out to need attach/import semantics (operator brings their own repo), that's RC-4A.4 work — out of scope here.
6. **Delivery-report missing the validation summary.** `change_runner._read_eval_validation` reads `<run>/candidates/<id>/eval-results.json` and projects each command into `{passed, command, duration_sec}`. If the real-Codex eval harness writes a different shape, the validation table will say "(no validation results recorded)". This is render-tolerant (validator passes), but worth noticing as RC-4A.4 polish.
7. **Codex CLI environment failure.** Wrong `CODEX_BIN`, wrong `sandbox`, missing `ask_for_approval=on-request`. Same operator pattern as `rc2b2.sh`. Rerun `agent-studio autonomous preflight` if confused.
8. **Apply Gate refuses because base_commit ≠ HEAD.** RC-3E.2 fix made base_commit accurate, so the only way this triggers is if something between baseline-commit and `change run` invocation made a commit. The runner script does not do that, so this should not fire.

## Operator checklist

Run **before** invoking `--run`:

- [ ] `which codex` returns a path; if not, install via `brew install codex` or set `CODEX_BIN`.
- [ ] `codex --version` runs cleanly.
- [ ] `npm --version` and `node --version` (need Node ≥ 18 for Next.js 15).
- [ ] `git --version` available.
- [ ] `python3 --version` available (used by the script's summary block).
- [ ] `scripts/rc4a3_change_run.sh` runs cleanly in dry-run (already verified during prep).
- [ ] **Local seed validation passed during prep** — `npm install` + `npm run build` + `npm run typecheck` all clean against the dogfood seed (see "Seed validation" below).

To execute the real run:

```bash
cd /path/to/LocalAgents
scripts/rc4a3_change_run.sh --run
```

The script will pause for 5 seconds with a "press Ctrl+C to abort" prompt before invoking Codex.

## Seed validation (done during prep)

Verified locally on 2026-05-13:

- `bash -n scripts/rc4a3_change_run.sh` — clean.
- `scripts/rc4a3_change_run.sh` (dry-run) — prints every step it will execute, including the agent-studio.yaml block.
- `npm install --no-audit --no-fund` against the dogfood — clean (~250 MB node_modules, Next.js 15.5.18 installed).
- `npm run build` — clean. `Compiled successfully in 2.2s`. Both `/` and `/_not-found` prerender as static.
- `npm run typecheck` (`tsc --noEmit`) — clean. No type errors.

## What this prep does NOT include

- No real Codex run yet (holding for explicit `go RC-4A.3 run`).
- No Vercel deploy / smoke check.
- No demo matrix (RC-4B).
- No RC-3F detector adapter work.
- No new validators or runtime changes — RC-4A.2 ships the implementation; RC-4A.3 is the dogfood.

## Next milestones (after RC-4A.3 verified)

- **RC-4A.4** (optional): operator-attaches-their-own-repo flow if RC-4A.3 surfaces a usability gap.
- **RC-4B**: 3-project demo matrix, each project getting one change.
- **RC-4C**: Demo Suite + EVALUATION.md.
- **RC-4D**: Portfolio packaging (README / ARCHITECTURE.md / INTERVIEW_STORY.md).
