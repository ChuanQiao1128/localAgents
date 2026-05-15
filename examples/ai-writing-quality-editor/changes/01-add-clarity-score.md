# Add clarity score

## Goal

Compute and display a deterministic clarity score (0-100) for the current
draft. The score is derived from the existing analyzer findings — no LLM,
no third-party detector, no network call. A draft with no findings scores
100; each finding lowers the score by a kind-weighted amount, clamped to
the [0, 100] range.

## Scope

- app/**
- components/**

## Non-goals

- Do not call any external API. Do not import any LLM client.
- Do not add any new dependency. Do not modify package.json or package-lock.json.
- Do not change tsconfig.json, next.config.mjs, or .gitignore.
- Do not introduce a CSS framework. Inline styles only.
- Do not add tests, build scripts, deploy config, or auth.
- Do not change the existing `analyze` signature. Add a new exported
  `clarityScore(text: string): number` (or `clarityScore(findings: Finding[])` —
  pick one and stay deterministic).
- Do not regress the existing tone selector, persistence, or suggestion list.

## Acceptance

- `clarityScore("")` returns `100` (an empty draft is trivially clear).
- A draft with zero findings returns `100`.
- The score deducts by kind weight: long-sentence -8, repeated-word -6, templated-opener -4, overly-formal -3. Sum of deductions is clamped to [0, 100].
- The score is rendered in a card above the suggestion list with the format `Clarity score: NN / 100`. The card is visible at all times while there is non-empty input.
- The score updates live as the user types (no extra button click).
- `npm run build` passes.
- `npm run typecheck` passes.
