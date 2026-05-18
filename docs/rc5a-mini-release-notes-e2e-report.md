# RC-5A · Mini Release Notes Builder — End-to-End Dogfood Test

**Date:** 2026-05-16
**Operator:** Chuan Qiao
**Scope:** First end-to-end dogfood of Studio's full delivery loop —
greenfield generation → feature change request → bug-fix change request —
against a project that didn't exist in any seed before this test.

---

## Why this test matters

Three things become claimable only when this test passes:

1. **Studio is not "demo-rigged."** The three RC-4C demos
   (ai-writing-quality-editor / ai-usage-cost-planner /
   agent-review-queue-console) live under `examples/` and were sourced as
   seeds. Mini Release Notes Builder was authored fresh inside the
   Studio Console flow — same orchestration, no special setup.
2. **Change Request Mode actually works on a real project**, not just
   the rc4a3 tiny e2e test. Both a feature change and a bug-fix change
   land on top of the generated MVP via `agent-studio change run`, with
   Promotion Gate + Apply Gate enforced on the live tree.
3. **Two real Studio runtime bugs were caught by dogfooding** (not by
   tests we wrote up front), then fixed and regression-tested. That's
   the delivery loop critiquing itself — exactly the loop Studio is
   supposed to be.
4. **A generated-product bug was found and fixed through the same loop.**
   The localStorage restore bug was discovered by manual browser QA,
   described as a Change Request, fixed by Studio, promoted from
   multiple candidates, and verified in the browser.

---

## Project

| Field | Value |
|---|---|
| Name | **Mini Release Notes Builder** |
| Stack | Next.js 15.5.18 + TypeScript + localStorage; no backend |
| Studio Console project id | (not persisted in v1) |
| Runtime project id | `project_d03fc7c129` |
| Runtime project path | `.agent-studio/projects/mini-release-notes-builder-mvp-require-c7c129` |
| Patch worker | `codex` (real Codex CLI) |

---

## Phase 1 · Greenfield generation

Locked MVP contract → `agent-studio new --from …` → manual scaffold copy
+ `npm install` + `git init baseline` → `agent-studio autonomous start`.

**Session:** `session_a1acb370d9`

**Task graph (3 tasks, all completed):**

| Task | Title | Commit |
|---|---|---|
| task-001 | Page shell and release item model | `cced934` |
| task-002 | Add item form and list | `9f72902` |
| task-003 | Markdown preview and localStorage | `83b2b51` |
| — | Record completed task graph | `23bdd10` |

**Validation evidence:**

- Session status: **completed**
- Tasks: **3 / 3 completed**
- Integration: **4 / 4 passed** (eval / build / typecheck per task)
- Review queue: **0 open**
- `agent-studio autonomous validate-artifacts --json` → `ok=true`
- `npm run build` (post-session, on the generated app): **passes**
- Local app verified at `localhost:3001` — the editor / list / markdown
  preview / localStorage persistence all behave as the contract
  prescribed.

This proves the whole greenfield loop: PRD → task graph → autonomous
loop → multi-candidate generation → Promotion Gate → Apply Gate →
commit-with-trailers → integration check → final report.

---

## Phase 2 · Change Request iteration

Goal of the change: *"Add a button that copies the generated Markdown
release notes to the clipboard."*

The change took **three attempts** —
v1 and v2 surfaced two real runtime bugs; v3 succeeded after the
hardening fixes landed. v3 evidence is the headline.

### v3 (the successful run)

| Field | Value |
|---|---|
| change_id | `change_d198aa653c` |
| session | `session_1ec8b3012f` (fresh, not reused from v1/v2) |
| commit | `e5f262e` "Add a button that copies the generated Markdown release notes to the clipboard." |
| state | **delivered** |
| `agent-studio change validate latest --json` | `ok=true` |
| review queue | **0 open** |
| `npm run build` | **passes** |

The button works in the running app: clicking it copies the rendered
markdown release notes to the system clipboard.

This is the demonstrable evidence that **Studio can take an existing
project and apply a directed change** with the same gate machinery and
commit provenance as the greenfield loop.

---

## Phase 3 · Bug fix via Change Request

Goal of the bug fix: *"Fix the Mini Release Notes Builder so release
items saved to localStorage are restored after a page refresh."*

This was a real product defect discovered by manual browser testing
after the MVP and Copy Markdown change were delivered. Adding release
items worked and the Markdown preview updated, but refreshing the page
lost the visible release items. The fix was intentionally submitted as
a Change Request instead of a direct manual code edit.

| Field | Value |
|---|---|
| change_id | `change_96f8a953c7` |
| state | **delivered** |
| commit | `83b9cd9` "Fix the Mini Release Notes Builder so release items saved to localStorage are re" |
| selected candidate | `candidate-b` |
| promotion | `promote` |
| review queue | **0 open** |
| preview URL | `http://127.0.0.1:4151` |

**Files changed:**

- `app/page.tsx`
- `lib/release-items.ts`
- `lib/release-items.test.ts`

**Validation evidence:**

- `agent-studio change validate latest --json` → `ok=true`
- `npm run typecheck` → **passes**
- `npm run build` → **passes**
- `node --test --experimental-transform-types lib/release-items.test.ts`
  → **4 tests passed**
- Browser manual verification → **passes**
  - add feature + fix release items
  - Markdown preview updates
  - refresh the browser
  - restored release items remain visible
  - restored Markdown preview remains visible
  - Copy Markdown remains enabled and copies restored content

The selected patch was **not** the first candidate. Studio evaluated
three candidates and promoted `candidate-b`:

| Candidate | Strategy | Score | Result |
|---|---|---:|---|
| candidate-a | conservative | 80 | passed gates |
| candidate-b | test-focused | 85 | **promoted** |
| candidate-c | broader-fix | 80 | passed gates |

This is the strongest evidence from this dogfood run that Studio is
doing evidence-based apply instead of blindly accepting the first patch.

---

## Failures dogfood surfaced (and the fixes)

### Bug 1 — change-request parser missed `## Scope paths` heading

**Symptom (v1):** the change request used the heading style:

```markdown
## Scope paths

- app/**
- components/**
- lib/**
```

The parser produced `scope_paths=[]` and `scope_missing=true`. Codex
generated a perfectly fine patch, but the Apply Gate's `diff_within_scope`
check refused every changed file because the contract claimed the
change had no allowed scope. A `needs-human-review` review item was
created. The operator burned Codex tokens for a patch that was structurally
correct but blocked by a parser mismatch.

**Root cause:** `_extract_list(sections, "scope")` only looked at the
lowercase key `"scope"`. The `## Scope paths` heading became
`sections["scope paths"]` — never read.

**Fix (RC-5A.13):** parser now accepts three section aliases —
`## Scope`, `## Scope paths`, `## Files to change` — plus matching
inline forms (`Scope paths: a, b`, `Files to change: a, b`). It also
strips wrapping backticks on each scope path entry, mirroring the
defensive cleanup the autonomous parser added in RC-4C.1. 5 new tests
in `tests/unit/test_change_request_parser.py` lock the behavior.

The Studio Console's `lib/changeRequestQuality.ts` was aligned to use
the same alias set so the on-screen preview never disagrees with the
backend.

### Bug 2 — failed-apply review correctly refused human override

**Symptom:** after v1 left a `needs-human-review` queue item, attempting
`agent-studio autonomous reviews approve` was refused with
`selected_candidate=null` and `out_of_scope_changes=1`.

**Diagnosis:** this is **not a bug** — it's the safe-apply contract
working as designed. The Apply Gate checked the prerequisite that a
candidate was actually selected by the Promotion Gate. Because the
parser bug had caused diff_within_scope to fail, no candidate was
selected (`selected_candidate=null`), so there's nothing safe to apply
even with a human override. The override path requires a real candidate
on disk; it doesn't let humans paper over a missing diff.

This is recorded as **PASS — gate refused exactly the right way.**

### Bug 3 — `change run` reused a budget-exhausted session from a different change_id

**Symptom (v2):** after rejecting v1's review, the operator wrote a
fresh change request with corrected scope and ran `change run latest`
for the new `change_10f6306b6` change_id. Run **failed in 0.016 s** with:

```
Pause reason: budget:max_needs_human_review_tasks
```

No Codex was invoked. No validation evidence. The new change had
silently inherited the prior change's session (`session_c1de07dab7`,
created for `change_a2f...`), whose `needs_review_tasks` counter was
already at the budget cap from v1's rejected review.

**Root cause:** `controller.start_or_resume()` blindly resumed the
most-recently-updated active session. It had no notion of which
change_id a session belonged to, so a new change inherited the budget
counters of the old change — a footgun specific to change mode.

**Fix (RC-5A.13):** `AutonomousSession` gains a `change_id: str | None`
field (with `to_dict` / `from_dict` round-trip + backward-compat).
`start_or_resume()` now takes `change_id=` kwarg with two new rules:

- **change run with different `existing.change_id` → fresh session.**
  Prevents v2's exact bug.
- **change run when prior session paused with `pause_reason` starting
  `"budget:"` → fresh session.** Prevents the same change being silently
  re-paused on a stale budget after the operator rejected its first run.
- **Plain autonomous resume (no change_id) → unchanged.** Keeps the
  CLI's `autonomous resume` behavior identical for back-compat.

6 new tests in `tests/unit/test_autonomous.py` (`StartOrResumeChangeIdTests`)
lock all four code paths plus session-record persistence.

**Bonus pre-Codex guard.** Even with the parser fixed, RC-5A.13 added a
pre-Codex scope guard in `change_runner.run_change()`: if `scope_missing`
or `scope_paths` is empty, write a deterministic `delivery-report.md`
with `result=failed` + reason `missing_scope_paths` and return in
milliseconds. **No Codex call. No agentic run. No session created. No
candidate budget consumed.** A future scope mistake becomes a sub-second
operator-readable failure instead of a silent token burn.

### v3 result confirms both fixes

After the hardening landed, v3 used the same `## Scope paths` heading
that v1 mis-parsed:

```
v3 scope:
Scope paths: 3 (missing=False) ✅

v3 session:
session_1ec8b3012f      ← brand-new, not session_c1de07dab7
state: completed
commit: e5f262e
```

Both the parser fix and the fresh-session rule fired correctly under
real load.

---

## Validation summary

| Phase | What was checked | Result |
|---|---|---|
| Greenfield | 3-task autonomous loop completes cleanly | ✅ |
| Greenfield | Promotion Gate + Apply Gate enforced per task | ✅ |
| Greenfield | `validate-artifacts --json` → ok=true | ✅ |
| Greenfield | Generated app `npm run build` passes | ✅ |
| Greenfield | Generated app runs at localhost:3001 | ✅ |
| Greenfield | Review queue empty | ✅ (0) |
| Change v1 | parser detected scope mismatch (post-fix verification) | regression covered by `test_parses_scope_paths_heading` |
| Change v2 | session reuse blocked (post-fix verification) | regression covered by `test_creates_new_session_for_different_change_id` |
| Change v3 | end-to-end successful change | ✅ |
| Change v3 | `change validate latest --json` → ok=true | ✅ |
| Change v3 | new commit `e5f262e` lands on change branch | ✅ |
| Change v3 | post-change `npm run build` passes | ✅ |
| Change v3 | review queue empty | ✅ (0) |
| Change v3 | clipboard button works in running app | ✅ |
| Bug fix change | localStorage restore change delivered | ✅ |
| Bug fix change | selected candidate was `candidate-b`, not first candidate | ✅ |
| Bug fix change | `change validate latest --json` → ok=true | ✅ |
| Bug fix change | `npm run typecheck` + `npm run build` pass | ✅ |
| Bug fix change | `lib/release-items.test.ts` → 4 tests passed | ✅ |
| Bug fix change | browser refresh restores items + preview + Copy Markdown | ✅ |

---

## Remaining issues (not blocking; logged for follow-up)

### 1. Next.js workspace root warning

The generated app emits during build:

```
Next.js inferred your workspace root...
Detected additional lockfiles...
```

It doesn't fail the build — npm just probes upward and finds the
monorepo's outer lockfile. Future template work (RC-5A.12.5 Start
Development Automation) should ship a `next.config.mjs` with an
explicit `outputFileTracingRoot` so the generated app is hermetic:

```js
import path from "node:path";

export default {
  outputFileTracingRoot: path.resolve("."),
};
```

### 2. Start Development is still operator-driven

The greenfield + change loops above were stitched together with manual
terminal commands — exactly what Studio Console's Develop tab currently
shows as copy-only blocks. To make this a one-button experience, the
Console needs:

- a **template system** under `apps/studio-console/templates/` (at
  minimum a `nextjs-app/` scaffold with `package.json` + `next.config.mjs`
  + `tsconfig.json` + `app/layout.tsx` + `app/page.tsx`)
- a **run manager** API (`POST /api/studio-projects/[id]/start` /
  `POST /api/studio-projects/[id]/stop` / `GET …/run`) that writes
  `command.json` / `stdout.log` / `stderr.log` / `status.json` /
  `pid.json` under `.studio-console/projects/<id>/runs/<run_id>/`
- a polling Develop tab (every 1–3 s) that surfaces `current command /
  elapsed / latest stdout / latest stderr` instead of copy-only commands
- **explicit refusal** of any cloud action (no `git push`, no `vercel
  deploy`, no `npm publish`) — Live mode stays local-only

### 3. Studio Project ↔ runtime project mapping

The current convention is "id matches" — i.e. when the operator runs
`agent-studio new --id <studioProjectId>`, the runtime dir matches the
Studio Console dir name and `loadProjectDetail()` finds it. This works
but isn't enforced; Studio Console should persist the resolved
`agentProjectId` + `agentProjectPath` into `project.json` after Start
Development runs, so subsequent reads are direct lookups instead of
naming convention.

### 4. Console does not yet show this run

The Mini Release Notes Builder lives at
`.agent-studio/projects/mini-release-notes-builder-mvp-require-c7c129`,
with no corresponding `.studio-console/projects/<id>/`. The new
project-centric workspace (RC-5A.12.1) only lists Studio Console
projects, so this run isn't visible in `/projects` even though all the
runtime evidence is on disk. The cleanest fix is to make Start
Development create the studio-console mirror dir at the same time it
runs `agent-studio new`. Until then, operators inspecting this run
have to use the legacy Evidence / Run pages (still reachable as
deprecation stubs that link to /projects, but with the underlying data
visible via `/api/projects/[id]` and `/api/artifact?path=`).

---

## What this proves for the interview narrative

| Claim | Evidence here |
|---|---|
| "Studio is a delivery runtime, not a demo." | Mini Release Notes Builder isn't an `examples/*` seed. It was authored fresh through the Studio flow on 2026-05-16. |
| "Same orchestration handles greenfield AND iteration." | Same `AutonomousController` / Promotion Gate / Apply Gate / commit_task / review_queue ran both phases. The change phase reused everything from the greenfield phase except the task-graph (1 synthesized task instead of 3). |
| "The model is not the system. The delivery loop is the system." | The two failures we caught had nothing to do with model output quality — Codex generated correct patches in both v1 and v2. The bugs were in the delivery loop's parser and session-binding logic. The loop critiqued itself. |
| "Controlled autonomy, not unbounded autonomy." | v1's failure was caught by the Apply Gate refusing an out-of-scope diff. The human-override path correctly refused to bypass that refusal because no real candidate existed to apply. The system stopped exactly where it should have stopped. |
| "Real Codex calls, not mocked." | Patch worker was the real Codex CLI; commits are real (`cced934`, `9f72902`, `83b2b51`, `e5f262e`); each commit carries `Agent-Task-ID` / `Agent-Run-ID` / `Selected-Candidate` / `Promotion-Decision` trailers (and `Change-Id` / `Source-Change-Request` on the change commit). |
| "Two unrelated dogfood bugs found and fixed in one cycle." | RC-5A.13 parser fix + session-binding fix are landed, regression-locked, and proven by v3's success. |

---

## Files of record (on disk)

- `.agent-studio/projects/mini-release-notes-builder-mvp-require-c7c129/`
  - `task-graph.json` — final state with 3 tasks completed
  - `.agent/autonomous/sessions/session_a1acb370d9/` — greenfield session + final-run-status.md
  - `.agent/autonomous/sessions/session_1ec8b3012f/` — change v3 session
  - `.agent/autonomous/sessions/session_baa34eb396/` — bug-fix change session
  - `.agent/changes/change_d198aa653c/` — applied-change.json + delivery-report.md for v3
  - `.agent/changes/change_96f8a953c7/` — applied-change.json + delivery-report.md for the localStorage restore fix
  - `.agent/runs/<run_id>/` — per-task promotion-report / candidates / eval-results

These exist on the operator's Mac; they aren't checked into the
portfolio repo.

---

## Status

**Mini Release Notes Builder end-to-end test: PASS** ✅

- Greenfield MVP generation: PASS
- Change Request iteration (v3): PASS
- Bug fix via Change Request: PASS
- Multi-candidate promotion selected `candidate-b`: PASS
- Build validation: PASS
- Added tests: PASS
- Browser refresh verification: PASS
- Review queue: 0
- Artifacts validation: ok=true
- Two hardening bugs found, fixed, regression-locked

**Mini Release Notes Builder dogfood: FULL PASS** ✅

Next milestone: use the same flow on **AI Writing Naturalizer**. Stop
expanding Mini Release Notes; its job as a proof case is complete.
