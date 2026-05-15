# AI Usage & Cost Planner

A deterministic, client-only planner that estimates monthly AI usage cost
across user-defined scenarios. The user picks a model, enters input/output
token estimates and monthly request count, and the planner shows the
estimated monthly cost per scenario plus a roll-up across all scenarios.

Pricing comes from a small, hardcoded constant table — no external API,
no fetch. Scenarios persist in `localStorage`. Visual style is calm SaaS:
light background, dark text, simple cards and tables, inline styles only —
no Tailwind, no CSS framework.

## task-001 — Planner page shell + scenario form

Build the planner page shell at `app/page.tsx`:

- Page title "AI Usage & Cost Planner" and a one-line subtitle.
- A scenario form with these inputs (each has a stable `id` for testing):
  - `scenario-name` text input (required)
  - `scenario-model` `<select>` whose options come from `MODEL_OPTIONS`
    (see task-002) — display the model `name` and an inline `$X / $Y per 1M`
    hint.
  - `scenario-input-tokens` number input (estimated input tokens per request)
  - `scenario-output-tokens` number input (estimated output tokens per request)
  - `scenario-monthly-requests` number input
  - "Add scenario" submit button
- An empty `scenarios-table` area that says "No scenarios yet — add one above."
- The page must be a client component (`"use client";`).

Scope:
- app/**

Acceptance:
- Page renders the title, the form (5 fields + button), and the empty table area.
- All inputs have the stable IDs above.
- `npm run build` passes.
- `npm run typecheck` passes.

Risk: low

## task-002 — Pricing constants + cost calculator

Implement the pricing module at `components/pricing.ts`:

- Export `MODEL_OPTIONS`, an array with EXACTLY these three entries (do NOT
  add more, do NOT change names — downstream tasks depend on the IDs):
  - `{ id: "fast-small", name: "fast-small", inputCostPerMTokens: 0.15, outputCostPerMTokens: 0.6 }`
  - `{ id: "balanced", name: "balanced", inputCostPerMTokens: 1.0, outputCostPerMTokens: 4.0 }`
  - `{ id: "reasoning", name: "reasoning", inputCostPerMTokens: 5.0, outputCostPerMTokens: 15.0 }`
- Export a `Scenario` type:
  `{ id: string; name: string; modelId: string; inputTokens: number; outputTokens: number; monthlyRequests: number }`.
- Export `monthlyCost(scenario: Scenario): number` that returns
  `((inputTokens * inputCost + outputTokens * outputCost) / 1_000_000) * monthlyRequests`,
  using the model's prices from `MODEL_OPTIONS`. Return `0` if the modelId
  is not found. Round to 4 decimal places (so the table doesn't render
  noisy floats but tests can still assert exact values).

Scope:
- app/**
- components/**

Acceptance:
- `monthlyCost` is a pure function — no `Math.random`, no `Date.now`, no network.
- `monthlyCost({ ..., modelId: "balanced", inputTokens: 1000, outputTokens: 500, monthlyRequests: 1000 })` returns `3.0`.
- `monthlyCost({ ..., modelId: "missing" })` returns `0`.
- Total of the rounding makes 4 decimal places: e.g. `2.3456`, never `2.3456000000001`.
- `npm run build` passes.
- `npm run typecheck` passes.

Risk: medium

Depends: task-001

## task-003 — Wire form + scenarios table + localStorage

Wire the form into stateful scenario management:

- Submitting the form appends a new `Scenario` (with a generated string id —
  use `crypto.randomUUID()` in the browser; fall back to `${Date.now()}-${count}`
  if `crypto.randomUUID` is unavailable; fallback must be deterministic across
  re-render).
- Render scenarios in a `<table>` with columns: Name, Model, Input tokens,
  Output tokens, Monthly requests, Monthly cost (formatted as `$X.XX`).
- Below the table, show a summary card: "Total monthly cost across N
  scenario(s): $X.XX".
- A "Remove" button per row deletes that scenario.
- Persist scenarios in `localStorage` under the key
  `ai-usage-cost-planner.v1`. On mount, hydrate from localStorage if present.
- The empty-state placeholder remains when there are zero scenarios.

Scope:
- app/**
- components/**

Acceptance:
- Adding a scenario appends a new row and updates the summary card total.
- Reloading the page restores scenarios from `localStorage`.
- Removing a scenario shrinks the table + updates the total.
- The summary card's total equals the sum of `monthlyCost` over all scenarios (rounded display, exact internal sum).
- The empty-state message renders when there are zero scenarios.
- `npm run build` passes.
- `npm run typecheck` passes.

Risk: medium

Depends: task-002
