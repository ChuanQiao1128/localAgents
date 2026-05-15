# AI Writing Quality Editor

A deterministic, client-only writing quality editor. The user pastes a draft,
picks a tone, and the editor surfaces concrete suggestions (long sentences,
repeated words, templated openers, overly formal phrases) computed locally
in TypeScript — no LLM, no third-party detector, no network call.

The first version persists the latest draft + tone choice in `localStorage`.
Visual style is calm SaaS: light background, dark text, bento-style cards,
inline styles only — no Tailwind, no CSS framework.

## task-001 — Editor page shell

Build the editor page shell at `app/page.tsx`:

- A page title "AI Writing Quality Editor" and a one-line subtitle.
- A `<textarea>` with a stable `id="draft-input"` and a placeholder
  ("Paste a draft to analyze...") that fills the working width.
- A tone `<select>` with id `tone-select` and four options exactly:
  `natural`, `professional`, `casual`, `academic`. Default: `natural`.
- An empty results panel area with id `results-panel` that says
  "Run the analyzer to see suggestions." until task-002 + task-003 wire it.
- The page must be a client component (`"use client";`) so future tasks can
  add `useState` without restructuring.

Scope:
- app/**

Acceptance:
- Page renders the title, textarea, tone selector, and empty results panel.
- Tone selector default is `natural`; all four options present in DOM.
- `npm run build` passes.
- `npm run typecheck` passes.

Risk: low

## task-002 — Deterministic writing analyzer

Implement the analyzer module at `components/analyzer.ts` and a
`components/SuggestionList.tsx` renderer:

- Export a `Finding` type: `{ id: string; kind: FindingKind; message: string; excerpt: string }`.
- Export a `FindingKind` union: `"long-sentence" | "repeated-word" | "templated-opener" | "overly-formal"`.
- Export `analyze(text: string): Finding[]` that runs four deterministic checks:
  - **long sentence**: any sentence (split on `.`/`!`/`?`) with > 30 words → one finding per sentence; excerpt is the first 80 chars.
  - **repeated word**: any non-stopword (length ≥ 4, lowercased) appearing > 3 times → one finding per repeated word; excerpt is the word.
  - **templated opener**: sentence starting with one of `In conclusion,`, `Furthermore,`, `Additionally,`, `Moreover,`, `In summary,` (case-insensitive) → one finding per match; excerpt is the opener phrase.
  - **overly formal**: occurrence of any of `henceforth`, `aforementioned`, `heretofore`, `notwithstanding`, `whereupon` (case-insensitive) → one finding per word; excerpt is the word with surrounding context (10 chars each side).
- `analyze("")` returns `[]`.
- `SuggestionList` renders a `<ul>` of findings; each `<li>` shows the kind label, message, and excerpt. Empty list renders the empty-state message "No suggestions — looks clean."

Scope:
- app/**
- components/**

Acceptance:
- `analyze` is a pure function — no `Math.random`, no `Date.now`, no network.
- A draft with one 40-word sentence yields exactly one `long-sentence` finding.
- A draft with the word `however` repeated 5 times yields one `repeated-word` finding for `however`.
- A draft starting with `In conclusion, ...` yields a `templated-opener` finding.
- A draft containing `henceforth` yields an `overly-formal` finding.
- `SuggestionList` renders the empty-state message when given `[]`.
- `npm run build` passes.
- `npm run typecheck` passes.

Risk: medium

Depends: task-001

## task-003 — Wire analyzer + persistence

Wire the analyzer into the page and add `localStorage` persistence:

- On every textarea change, call `analyze(text)` and render results in
  `results-panel` via `<SuggestionList />`.
- Persist `{ draft, tone }` to `localStorage` under the key
  `ai-writing-quality-editor.v1`. On mount, hydrate from `localStorage` if
  present (fallback to empty draft + `natural` tone).
- Show a small "Last saved: just now" indicator when the persistence write
  fires. Use a deterministic indicator — no real timestamp formatting beyond
  `"just now" | "saved earlier"`.
- The empty-results placeholder ("No suggestions — looks clean.") renders
  when `analyze` returns `[]` AND the draft is non-empty. When the draft
  is empty, show the original "Run the analyzer to see suggestions." text.

Scope:
- app/**
- components/**

Acceptance:
- Typing into the textarea updates the suggestion list live.
- Reloading the page restores the previous draft + tone selection from `localStorage`.
- The "Last saved" indicator appears after the first edit.
- The two empty states (no input vs. clean input) render the correct copy.
- `npm run build` passes.
- `npm run typecheck` passes.

Risk: medium

Depends: task-002
