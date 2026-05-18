# RC-5E Studio + Naturalizer Evaluation

Date: 2026-05-17

## Studio Evaluation

Verdict: usable for controlled local development, with the strongest evidence now in project-scoped Change Request runs.

What improved in this pass:

- Project Inspector now reports Naturalizer provider readiness from runtime `.env.local` key presence.
- Secret values are not returned or rendered; the UI only shows connected/missing state.
- Studio no longer shows Naturalizer providers as "planned / not connected" after they are configured.
- Studio Console build and typecheck pass after the change.

Known remaining gaps:

- Provider readiness is currently specialized for `ai-writing-naturalizer`.
- Provider readiness checks configuration presence, not provider billing/quota health.
- Runtime runs can still take several minutes when Codex CLI is slow, though the UI now exposes candidate/run state.

## Naturalizer Evaluation

Verdict: the generated product is now a real local workflow rather than a mock-only skeleton.

Current runtime:

- Studio project: `ai-writing-naturalizer`
- Runtime project id: `project_9b14ce39d7`
- Runtime path: `.agent-studio/projects/mvp-requirements-ce39d7`
- Preview URL: `http://127.0.0.1:4957`

Validated behavior:

- First screen shows provider readiness before the first rewrite.
- Rewrite provider is shown as Codex CLI when configured.
- Detector provider is shown as configured when endpoint credentials are present.
- Naturalize returns real Codex rewrite mode.
- Detector returns real provider mode in the app UI.
- Result confidence summary shows rewrite mode, detector mode, score delta, and fallback warnings.
- Copy actions, history, risk report, and disclaimer remain present.

Latest Change Request:

- Studio draft: `cr_8eb795c894`
- Runtime change: `change_7aa505b67a`
- Commit: `868aa66`
- Selected candidate: `candidate-a`
- Strategy: `conservative`
- Promotion decision: `promote`
- Validation: `change validate latest` passed.

Files changed in runtime:

- `app/api/provider-status/route.ts`
- `app/page.tsx`

Validation:

- Runtime `npm run typecheck` passed.
- Runtime `npm run build` passed.
- Studio Console `npm run typecheck` passed.
- Studio Console `npm run build` passed.
- Browser verification passed.

## Next Recommended Work

- Add a provider-specific detector adapter once the final third-party detector API contract is confirmed.
- Add a lightweight smoke test for `/api/provider-status`, `/api/rewrite`, and `/api/detect`.
- Add a product-level "sample input" button for faster demos.
- Keep GitHub PR, deploy, auth, billing, database, and upload out of scope until the local dogfood loop is stable.
