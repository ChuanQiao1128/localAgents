# RC-5F Studio Product Review & Auto Change Planner

## Status

Completed locally.

## What changed

Studio Console can now review a generated product and turn product findings into scoped Change Request drafts.

The flow is:

1. Read the Studio project contract and linked runtime project.
2. Inspect runtime source under `app/**`, `components/**`, `lib/**`, and `docs/**`.
3. Produce a deterministic product review.
4. Write review artifacts under `.studio-console/projects/<project>/product-reviews/<review_id>/`.
5. Generate project-level Change Request drafts under `.studio-console/projects/<project>/changes/`.
6. Let the normal Change Request runner execute each draft through existing gates.

## Naturalizer review result

Project: `ai-writing-naturalizer`

Runtime project: `project_9b14ce39d7`

Runtime path: `.agent-studio/projects/mvp-requirements-ce39d7`

Latest review:

- Score: `66/100`
- Verdict: `needs_change_plan`
- Latest delivered change: `change_f1b526a311`
- Provider mode: `Codex CLI rewrite / configured detector`

Generated drafts:

- `Product Review CR-B — Add structured writing context`
- `Product Review CR-C — Add anti-fabrication guardrails`

## Automated execution result

After the first product review, Studio ran its generated Change Requests through the normal background Change runner.

1. `Product Review CR-C — Add anti-fabrication guardrails`
   - Runtime change: `change_830f2d5f9f`
   - Commit: `d31baea`
   - Selected candidate: `candidate-a`
   - Strategy: `conservative`
   - Repair attempts: `1`
   - Promotion decision: `promote`
   - Validation: `change validate` OK, runtime `npm run typecheck` OK, runtime `npm run build` OK

2. `Product Review CR-B — Add structured writing context`
   - Runtime change: `change_32a7ff98d9`
   - Commit: `5a15c97`
   - Selected candidate: `candidate-a`
   - Strategy: `conservative`
   - Repair attempts: `0`
   - Promotion decision: `promote`
   - Validation: `change validate` OK, runtime `npm run typecheck` OK, runtime `npm run build` OK

Final product review after generated changes:

- Score: `100/100`
- Verdict: `pass`
- Latest delivered change: `change_caa13f9df3`
- Latest commit: `a01996d`
- Recommended generated changes: none

## RC-5F.2 hardening

The Product Review runner now writes schema v2 artifacts and tracks resolved/open status per finding:

```text
.studio-console/projects/<project>/product-reviews/<review_id>/
  product-review.md
  product-review.json
  prioritized-change-plan.md
```

The v2 JSON includes:

- `schema_version`
- `studio_project_id`
- `runtime_project_id`
- `verdict`
- `score`
- `findings[]`
- `recommended_changes[]`
- `inputs_read[]`
- `created_at`

Naturalizer-specific rubric IDs are now stable:

- `NAT-001` detector score is not treated as success metric
- `NAT-002` detector output is framed as reference signal
- `NAT-003` score increase has a clear warning
- `NAT-004` bypass/evasion framing is avoided
- `NAT-005` anti-fabrication guardrail is present
- `NAT-006` user context capture supports specificity without invention
- `NAT-007` provider mode is visible without exposing secrets
- `NAT-008` next action is clear when reference signals remain high

When open findings exist, Studio can generate scoped drafts:

- `CR-D` Reframe detector as reference signal
- `CR-E` Improve rewrite result verification and claim warnings
- `CR-F` Improve guidance when reference signal increases

The latest Naturalizer review resolved all eight Naturalizer checks:

- Review: `product_review_20260517T022854`
- Score: `100/100`
- Verdict: `pass`
- Open findings: `0`
- Inputs read: Studio contract files, preview status, latest delivery evidence, and runtime source files

Preview URL observed after restart:

- `http://127.0.0.1:4960`

Smoke test evidence:

- `/api/rewrite` returned `mode=real_codex`
- Detector reference modes were `real_provider` for original and rewritten text
- Response included `verificationReport.requiresUserVerification`
- Response included `verificationReport.safeGenericPhraseRemovals`
- Response included `verificationReport.nextSuggestions`

## Why this matters

Before RC-5F, the human operator or Codex coworker had to decide the next product direction and hand-write the Change Request.

After RC-5F, Studio performs the product-review step itself and produces runnable drafts. Codex still writes candidate patches, but Studio decides what product problems should become scoped changes and still routes those changes through the normal quality gates.

This supports the core interview claim:

> The model is not the system. The validation and promotion loop is the system.

## Safety boundaries

- No `.env.local` values are read or displayed.
- No external LLM or detector calls are made by product review.
- Generated changes still require normal Change Request gates.
- Product review does not auto-approve reviews.
- Product review does not deploy, push, or create PRs.

## Validation

- `cd apps/studio-console && npm run typecheck`
- `cd apps/studio-console && npm run build`
- `python3 -m unittest tests.unit.test_product_review_core -v`
- `./agent-studio product-review --project project_9b14ce39d7 --json`
- `POST /api/studio-projects/ai-writing-naturalizer/product-review`
- Browser check: Develop tab shows `Studio Product Review`, v2 finding status, inputs read, and generated CR actions when applicable.
