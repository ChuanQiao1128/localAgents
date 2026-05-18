# RC-5B · AI Writing Naturalizer — Generation Report

**Date:** 2026-05-16
**Operator:** Chuan Qiao
**Scope:** Studio Console dogfood of the AI Writing Naturalizer MVP:
locked contract -> Prepare Runtime Project -> Start Development ->
generated website preview -> browser verification.

---

## Why this test matters

This run proves that Studio Console is no longer only an artifact viewer
or command generator. It can drive the local development loop from the
frontend and produce a working generated product:

1. **Prepare Runtime Project** created and linked a real `agent-studio`
   runtime project from the locked Studio contract.
2. **Start Development** launched the controlled autonomous development
   run from the Develop page.
3. **Codex patch-worker** completed the generated task graph and produced
   committed source changes in the runtime project.
4. **Deliver** exposed a generated website preview with a direct
   **Open Website** action.
5. **Browser verification** confirmed that the generated Naturalizer is
   usable, not just buildable.

This is the first RC-5B evidence that Studio can take a new product
contract and generate an inspectable, runnable web app through the
frontend-controlled workflow.

---

## Project

| Field | Value |
|---|---|
| Studio project | `ai-writing-naturalizer` |
| Product name | **AI Writing Naturalizer** |
| Runtime project id | `project_c2690269a6` |
| Runtime project path | `.agent-studio/projects/ai-writing-naturalizer-mvp-requirement-0269a6` |
| Runtime session | `session_9ade0b8ba7` |
| Patch worker | `codex` |
| Preview URL | `http://127.0.0.1:4957` |

---

## Studio flow result

| Step | Result |
|---|---|
| Naturalizer contract locked | PASS |
| Prepare Runtime Project | PASS |
| Runtime mapping persisted | PASS |
| Start Development from Develop page | PASS |
| Runtime session completed | PASS |
| Review queue | `0 open`, `0 blocking` |
| Final integration | `2 passed`, `0 failed` |
| `validate-artifacts` | `ok=true` |
| Deliver page Generated Website | PASS |
| Deliver page Open Website | PASS |

The Studio development run completed with:

- `runId`: `studio_run_4323174aa7`
- `status`: `completed`
- `runtimeSessionStatus`: `completed`
- `taskCounts.completed`: `4`
- `taskCounts.pending`: `0`
- `taskCounts.needs-human-review`: `0`
- `preflightExitCode`: `0`
- `validateArtifactsExitCode`: `0`
- `exitCode`: `0`

---

## Runtime task graph

| Task | Title | Commit |
|---|---|---|
| task-001 | Editor shell and before/after layout | `25f0f72` |
| task-002 | Rewrite API and provider adapter | `046bb83` |
| task-003 | Detector adapter and risk report | `ff2c37c` |
| task-004 | History, report polish, and copy actions | `d6ba339` |
| - | Record completed task graph | `c7d7ebb` |

All task candidates were selected and applied with Promotion Gate
decision `promote`.

Applied candidate artifacts were validated for:

- `.agent/runs/run_1614623df5/applied-candidate.json`
- `.agent/runs/run_a459a03158/applied-candidate.json`
- `.agent/runs/run_88d27bc20a/applied-candidate.json`
- `.agent/runs/run_e8f9702fa7/applied-candidate.json`

---

## Validation evidence

### Generated Naturalizer

- `npm run build` in generated Naturalizer: PASS
- Runtime project git status after post-run hygiene: clean
- Generated preview returned HTTP 200 at `http://127.0.0.1:4957`
- Browser opened generated product successfully

### Studio Console

- `npm run build` in `apps/studio-console`: PASS
- `npm run typecheck` in `apps/studio-console`: PASS
- Studio Console available at `http://127.0.0.1:3015`
- Deliver page shows:
  - `Generated Website`
  - `Open Website`
  - preview URL `http://127.0.0.1:4957`
  - runtime project id `project_c2690269a6`
  - runtime project path `.agent-studio/projects/ai-writing-naturalizer-mvp-requirement-0269a6`

### Preview manager hardening

During verification, the generated website initially returned HTTP 200
but showed a Next.js dev runtime overlay caused by `next dev` and
`.next` build artifacts sharing the same output directory. Studio's
Preview Manager was updated to launch generated previews with
production `next start` instead of `next dev`.

After the change:

- Preview starts at `http://127.0.0.1:4957`
- Preview status persists as `running`
- `Open Website` opens the generated product
- No dev overlay appears in the verified preview

---

## Browser verification

The generated Naturalizer was tested in the browser with a sample input
containing formulaic AI-style phrases.

Verified features:

- textarea exists
- tone selector exists
- `Naturalize` button exists
- deterministic rewrite output appears
- original text remains visible
- rewritten text appears in the after panel
- detector-style score appears before rewrite
- detector-style score appears after rewrite
- risk report appears
- disclaimer is visible
- `Copy text` works
- `Copy report` works
- localStorage history is written
- refresh restores the recent rewrite run
- restored state includes rewritten text, history, and report

Example browser result:

- Original detector-style score: `100/100`
- Rewritten detector-style score: `0/100`
- Score movement: `-100`
- Tone: `Direct`

Screenshot evidence:

- `.agent-studio/projects/ai-writing-naturalizer-mvp-requirement-0269a6/.agent/artifacts-naturalizer-official-preview-tested.png`
- `.agent-studio/projects/ai-writing-naturalizer-mvp-requirement-0269a6/.agent/artifacts-naturalizer-deliver-page.png`

---

## Current product behavior

The MVP currently provides a complete local workflow:

1. User pastes draft text.
2. User chooses a tone: concise, warm, professional, or direct.
3. User clicks Naturalize.
4. The app produces a rewritten result.
5. The app computes before/after detector-style heuristic scores.
6. The app shows a risk report and disclaimer.
7. The user can copy rewritten text or a short report.
8. Recent runs are saved in localStorage and restored after refresh.

This is a working MVP skeleton for the intended product.

---

## Current limitations

The generated MVP is intentionally not yet a real API-backed product.
Current limitations:

- Rewrite is local deterministic / mock-style.
- Detector is local heuristic / mock-style.
- There is no real external LLM call yet.
- There is no real third-party detector API yet.
- No provider API key is required or used.
- The detector-style score is not authoritative and must remain labeled
  as a reference signal.

These limitations are acceptable for RC-5B generation, but they are the
next blockers for turning the Naturalizer into a credible demo product.

---

## Next planned changes

### Change Request 1: real LLM rewrite provider

Goal: add a server-side real LLM rewrite provider while keeping the
existing deterministic rewrite as a mock fallback.

Expected behavior:

- `POST /api/rewrite` accepts original text and tone.
- If a configured server-side API key exists, the API uses the real LLM.
- If the key is missing or the provider fails, the deterministic fallback
  is used.
- Response includes mode: `real` or `mock`.
- Frontend shows a clear provider/mode badge.
- API keys never appear in client code, logs, artifacts, or UI.

### Change Request 2: real detector provider

Goal: add one real third-party detector provider while keeping the local
heuristic detector as fallback.

Expected behavior:

- `POST /api/detect` accepts text.
- If a configured detector key exists, the API uses the real provider.
- If the key is missing or provider fails, local heuristic fallback is
  used.
- Response includes mode, provider, score, label, and error.
- UI continues to show the disclaimer and must not claim detector bypass.

---

## RC-5B status

**AI Writing Naturalizer MVP generation: PASS**

Studio Console successfully generated and previewed a working web app
from a locked Naturalizer contract. The next phase should not expand
Studio features. It should use Change Request mode to add API realism:
real LLM rewrite first, then one real detector provider.

---

## Studio Console status after RC-5C polish

RC-5C keeps Naturalizer as a generated local MVP and improves the Studio
Console around it for demo and workplace use.

Console improvements:

- Dashboard now includes an Interview Demo Path linking directly to the
  Mini Release Notes dogfood case, Naturalizer workspace, and evidence docs.
- Dashboard includes a Workplace value card explaining why Studio exists
  around Codex: scoped change requests, deterministic gates, human review,
  and delivery reports for pre-PR handoff.
- Project workspaces now expose a compact inspector with contract status,
  runtime mapping, preview URL when running, delivered change evidence, and
  review queue counts.
- Naturalizer workspace shows provider readiness as planned / not connected,
  with current mode clearly labeled as local deterministic / heuristic.
- Deliver tab includes a Pre-PR Handoff card with a copyable PR-description
  style summary, without creating a GitHub PR or pushing code.

Still intentionally not implemented in RC-5C:

- Real LLM rewrite provider.
- Real detector provider.
- GitHub PR creation.
- Production deployment.
- Multi-user or cloud operation.
