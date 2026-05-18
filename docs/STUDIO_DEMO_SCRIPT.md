# Studio Demo Script

## 5-minute demo

1. Open the dashboard.
   - Click `Interview Demo Path`.
   - Explain: Studio is a local delivery runtime around Codex, not a chat UI.

2. Open Mini Release Notes Builder.
   - Go to `Deliver`.
   - Show `Generated Website` and `Open Website`.
   - Explain that Studio generated a working MVP from locked requirements.

3. Show the bug-fix dogfood evidence.
   - Open the Mini Release E2E report.
   - Point out `change_96f8a953c7`, commit `83b9cd9`, selected `candidate-b`,
     tests added, and browser verification.
   - Message: Studio handled a real bug fix after the MVP, not just one-shot generation.

4. Open AI Writing Naturalizer.
   - Go to `Deliver`.
   - Open the generated website.
   - Show textarea, tone selector, rewrite result, heuristic detector-style report,
     disclaimer, copy actions, and local history.

5. Explain gates and handoff.
   - Show `Pre-PR Handoff`.
   - Explain: Codex writes candidate patches. Studio decides whether they are safe to apply.

## 15-minute technical deep dive

1. Requirements and contract.
   - Open `Discuss & Lock`.
   - Show that work starts from locked local files, not an ad-hoc prompt.

2. Runtime mapping.
   - Open `Develop`.
   - Show `agentProjectId` and `agentProjectPath`.
   - Explain that Studio project and runtime project are explicitly linked.

3. Controlled development.
   - Show task progress, run logs, review queue, and stop controls.
   - Explain that active work runs in the background and the UI polls status.

4. Change Request mode.
   - Switch to `Discuss & Lock -> Change Request`.
   - Show scope examples and quality scan.
   - Explain that missing scope is a hard preflight error because Apply Gate cannot enforce safety without paths.

5. Delivery evidence.
   - Open `Deliver`.
   - Show delivery-report, applied-change, and advanced artifacts collapsed behind the evidence section.
   - Show the copyable Pre-PR Handoff summary.

6. Failure behavior.
   - Explain that timeout, build failure, and blocking review are not hidden.
   - Studio pauses or requires human review instead of pretending the task succeeded.

## What to click

- Dashboard -> `Open Mini Release workspace`.
- Mini Release -> `Deliver` -> `Open Website`.
- Dashboard -> `Open Mini Release E2E report`.
- Dashboard -> `Open Naturalizer workspace`.
- Naturalizer -> `Deliver` -> `Open Website`.
- Deliver -> `Copy handoff summary`.

## What not to click

- Do not start new autonomous development during the interview.
- Do not run real detector or LLM provider calls unless keys are configured and the demo explicitly needs them.
- Do not click deploy, git push, or GitHub PR actions; those are intentionally not part of this local-first demo.
- Do not approve review items live unless the interview is specifically about human override behavior.

## Direct Codex vs Studio

For tiny edits, direct Codex is faster. Studio optimizes for controlled delivery,
not raw typing speed.

Use direct Codex when:

- The change is local and obvious.
- You do not need a run package, gates, or delivery report.

Use Studio when:

- You need scoped change requests.
- You need build/typecheck gates.
- You need human review when blocked.
- You need a pre-PR handoff record.
- You want evidence that a generated patch was safe to apply.

## Mini Release Notes dogfood

Mini Release Notes is the strongest completed evidence case:

- Greenfield MVP was generated.
- A Copy Markdown feature change was delivered.
- A real localStorage restore bug was found by manual browser testing.
- The bug fix was submitted as a Change Request.
- Studio selected `candidate-b`, applied the patch, added tests, and produced delivery evidence.

This shows an actual software iteration loop, not a one-off generated page.

## Naturalizer limitation and next API step

Naturalizer is currently a generated MVP skeleton:

- Rewrite is local deterministic / mock-style.
- Detector is local heuristic / mock-style.
- No real external LLM provider is connected yet.
- No real third-party detector provider is connected yet.

The planned next steps are:

1. Add a real server-side rewrite provider.
2. Add one real detector provider.
3. Keep mock fallback and clearly label real vs local mode.

The product must not claim detector bypass. Detector outputs are reference
signals only and are not authoritative.
