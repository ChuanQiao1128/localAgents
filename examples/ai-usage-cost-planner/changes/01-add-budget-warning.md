# Add budget warning and break-even comparison

## Goal

Add a monthly budget input. When the total monthly cost across scenarios
exceeds the budget, show a warning badge. Surface each scenario's share of
the total as a percentage column. Identify and highlight the cheapest and
most expensive scenarios in the table.

## Scope

- app/**
- components/**

## Non-goals

- Do not call any external API. No fetch, no network, no LLM client.
- Do not add any new dependency. Do not modify package.json or package-lock.json.
- Do not change tsconfig.json, next.config.mjs, or .gitignore.
- Do not introduce a CSS framework. Inline styles only.
- Do not change `MODEL_OPTIONS` or the `Scenario` type beyond what the
  budget feature requires (the budget itself can be a separate top-level
  state field, not a per-scenario field).
- Do not add tests, build scripts, deploy config, or auth.
- Do not add a chart library. Render percentages and badges as text.

## Acceptance

- A `monthly-budget` number input is rendered above the scenarios table.
- The input persists in `localStorage` under the same key as scenarios
  (e.g. nested under `ai-usage-cost-planner.v1.budget`) — must round-trip
  on page reload.
- When `total > budget AND budget > 0`, a warning badge with text "Over budget"
  appears next to the summary card total. Otherwise the badge is hidden.
- Each scenario row shows a "Share" column with that scenario's
  `monthlyCost / total * 100` rounded to 1 decimal place, suffixed with `%`.
  When `total === 0`, the share column shows `0.0%` for every row.
- The cheapest scenario row gets an inline "cheapest" tag (e.g. a small
  green text span). The most expensive scenario row gets an inline
  "most expensive" tag. With one scenario, BOTH tags appear on that row.
- The empty-state and existing "Remove" actions still work unchanged.
- `npm run build` passes.
- `npm run typecheck` passes.
