# Add SLA risk badges

## Goal

Surface time-based SLA risk on review items. Blocking items older than 24
hours show an "urgent" badge; warning items older than 8 hours show a
"review soon" badge. Add an "Urgent" count to the summary section. Existing
filters and actions continue to work unchanged.

For deterministic test behavior the SLA "now" reference must come from a
single helper (e.g. `slaNow(): Date`) so a test or an inline comment
can pin a fixed time. Default `slaNow()` returns `new Date()`.

## Scope

- app/**
- components/**

## Non-goals

- Do not call any external API. No fetch, no network, no LLM client.
- Do not add any new dependency. Do not import a date library (no `date-fns`,
  no `dayjs`, no `luxon`). Use the built-in `Date` only.
- Do not modify package.json or package-lock.json.
- Do not change tsconfig.json, next.config.mjs, or .gitignore.
- Do not introduce a CSS framework. Inline styles only.
- Do not add tests, build scripts, deploy config, or auth.
- Do not change `SEED_REVIEWS` shape or `ReviewItem` field set beyond what
  the SLA feature requires (the SLA badge is derived state, not stored).
- Do not modify resolved or rejected items' SLA — only `open` items can
  carry an SLA badge.

## Acceptance

- A pure helper `slaRisk(item: ReviewItem, now: Date): "urgent" | "review-soon" | null` is exported from `components/reviews.ts` (or a sibling file).
  - Returns `"urgent"` when `item.status === "open"` AND `item.severity === "blocking"` AND `now - createdAt > 24 hours`.
  - Returns `"review-soon"` when `item.status === "open"` AND `item.severity === "warning"` AND `now - createdAt > 8 hours`.
  - Returns `null` otherwise (including for non-open items and `info` severity).
- The console renders the badge inline in each affected row's Severity column (e.g. next to the severity badge).
- A new `summary-urgent` card is rendered in the summary section, showing the number of items currently flagged `"urgent"` over the FULL list.
- The status filter still works: filtering to `open` still shows rows with badges; filtering to `resolved` shows no badges.
- Approving an `urgent` blocking item flips its status to `approved`, the badge disappears, and the urgent summary count drops by 1.
- `npm run build` passes.
- `npm run typecheck` passes.
