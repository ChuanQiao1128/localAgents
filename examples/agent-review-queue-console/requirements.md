# Agent Review Queue Console

A deterministic, client-only console for triaging items that an AI agent
left in its review queue. Each item has a severity (`blocking` / `warning` /
`info`), a reason code (`failed-apply` / `needs-human-review` /
`deployment-failed`), a title, an evidence summary, and an action set
(approve / reject / resolve). The console shows summary counts at the top,
a status filter, and a table of items.

This demo intentionally mirrors Local Agent Studio's own review queue
concept — the point is to show the human-in-the-loop layer agentic systems
need, rendered as a calm SaaS dashboard. No backend, no fetch, no LLM call —
seed data is hardcoded in TypeScript. Visual style is calm SaaS: light
background, dark text, simple cards/tables, inline styles only.

## task-001 — Console page shell + summary + filter

Build the console page shell at `app/page.tsx`:

- Page title "Agent Review Queue Console" and a one-line subtitle.
- A summary section with four cards (id `summary-cards`):
  `summary-open`, `summary-approved`, `summary-rejected`, `summary-resolved`
  — each shows the count for that status. All four start at 0 (real values
  wire in task-003).
- A status filter `<select>` with id `status-filter` and exactly five options:
  `all` (default), `open`, `approved`, `rejected`, `resolved`.
- An empty `reviews-table` area with the placeholder
  "No review items in this view."
- The page must be a client component (`"use client";`).

Scope:
- app/**

Acceptance:
- Page renders the title, four summary cards (all showing 0), the filter
  selector, and the empty table area.
- Filter default is `all`; all five options present in DOM.
- `npm run build` passes.
- `npm run typecheck` passes.

Risk: low

## task-002 — Seed review items

Implement the seed data + types at `components/reviews.ts`:

- Export a `Severity` type: `"blocking" | "warning" | "info"`.
- Export a `ReasonCode` type: `"failed-apply" | "needs-human-review" | "deployment-failed"`.
- Export a `ReviewStatus` type: `"open" | "approved" | "rejected" | "resolved"`.
- Export a `ReviewItem` type:
  `{ id: string; severity: Severity; reason: ReasonCode; title: string; evidence: string; status: ReviewStatus; createdAt: string }`
  — `createdAt` is an ISO 8601 string (e.g. `"2026-05-12T03:00:00Z"`).
- Export `SEED_REVIEWS: ReviewItem[]` containing EXACTLY six items:
  - `review_001` blocking / failed-apply / open / "Patch failed apply check on api/users.ts" / createdAt = `"2026-05-11T08:00:00Z"`
  - `review_002` blocking / deployment-failed / open / "Vercel preview returned 500 on /api/health" / createdAt = `"2026-05-12T01:00:00Z"`
  - `review_003` warning / needs-human-review / open / "Codex repair loop exhausted on task-002" / createdAt = `"2026-05-12T11:00:00Z"`
  - `review_004` warning / needs-human-review / approved / "Out-of-scope edit to package.json" / createdAt = `"2026-05-10T14:00:00Z"`
  - `review_005` info / needs-human-review / resolved / "Style critic flagged 3 minor issues" / createdAt = `"2026-05-09T20:00:00Z"`
  - `review_006` info / failed-apply / rejected / "Stale base_commit, fixed manually" / createdAt = `"2026-05-08T07:00:00Z"`

Scope:
- app/**
- components/**

Acceptance:
- `SEED_REVIEWS` has exactly 6 items with the IDs `review_001`..`review_006`.
- Every field on every item matches the spec above (deterministic).
- The `createdAt` strings parse with `new Date()` (no `Date.now()` calls).
- `npm run build` passes.
- `npm run typecheck` passes.

Risk: low

Depends: task-001

## task-003 — Wire actions, badges, summary counts, filter

Wire the console into stateful review management:

- Render the filtered review items in a `<table>` with columns: ID,
  Severity (with a colored badge — blocking=red text/border, warning=orange,
  info=gray), Reason, Title, Evidence, Status, Actions.
- Each row's Actions cell renders three buttons: "Approve", "Reject",
  "Resolve". Clicking a button updates that item's status accordingly.
- `localStorage` persistence under the key `agent-review-queue-console.v1`:
  hydrate on mount (fallback to `SEED_REVIEWS`), persist on every status
  change.
- Summary cards update live based on the FULL list (not the filtered view):
  count of `open`, `approved`, `rejected`, `resolved`.
- The filter selector hides rows that don't match the chosen status. `all`
  shows everything.
- Empty-state placeholder appears when the filtered list is empty (e.g.
  filter = `rejected` with no rejected items).

Scope:
- app/**
- components/**

Acceptance:
- Initial render shows 6 rows with summary `open=3, approved=1, rejected=1, resolved=1`.
- Clicking "Approve" on `review_001` flips its status to `approved` and updates the summary to `open=2, approved=2, rejected=1, resolved=1`.
- Reloading the page restores the modified statuses from `localStorage`.
- Filter `open` shows only items with status `open`.
- Severity badges are rendered as inline spans with the right color per severity.
- The empty-state placeholder appears when the filter would render zero rows.
- `npm run build` passes.
- `npm run typecheck` passes.

Risk: medium

Depends: task-002
