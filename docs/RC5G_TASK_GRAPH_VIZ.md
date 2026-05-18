# RC-5G Task Graph DAG + Stage Chevron

## Status

Completed locally. Typecheck clean, unit tests green (4/4 via `npm run test:unit`).
Full `npm run build` not run in sandbox (45s timeout) — verify on local machine.

## What changed

Studio Console Develop tab now visualizes the autonomous task graph as a live
Mermaid DAG, and every project page displays a 7-stage pipeline strip at the
top so users can see at a glance where the project sits in the SDLC.

UI-only change. No backend modifications, no API surface change, no schema
change. Polling cadence unchanged (2 s while a development run is active, 3 s
otherwise).

## Files touched

New:
- `apps/studio-console/lib/buildTaskDag.ts` — pure function, `tasks[]` →
  Mermaid `graph LR` source + status histogram + safeId→rawId reverse map.
- `apps/studio-console/lib/buildTaskDag.test.ts` — 4 unit tests via Node 22
  built-in `node:test` (no test framework dep added).
- `apps/studio-console/components/TaskGraphView.tsx` — client component, lazy
  loads the bundled `mermaid` package via dynamic `import("mermaid")` (lazy so
  it never enters the SSR bundle), source-diffs to avoid flash during live
  polling, attaches click handlers on rendered SVG nodes.
- `apps/studio-console/components/StageChevron.tsx` — derives a 7-stage state
  array (Discuss → Lock → Plan → Run → Promote → Apply → Deliver) from
  `deriveStudioProjectStatus` + change states. Greenfield-only projects render
  Promote/Apply/Deliver as `not-applicable` (diagonally hatched) rather than
  "stuck pending".

Modified:
- `apps/studio-console/app/projects/[projectId]/page.tsx` — replaces the
  vertical task-timeline list with the DAG view; original list collapses into
  a `<details>` element below ("列表视图"). DAG node click → opens the
  details, scrolls the matching `<li>` into view, briefly highlights it.
  Inserts `<StageChevron />` above the per-tab content.
- `apps/studio-console/app/globals.css` — adds the `.task-dag-*`,
  `.stage-chevron-*` rules + `studio-dag-pulse` (running-node pulse) and
  `studio-task-highlight` (list-item flash) keyframes.
- `apps/studio-console/package.json` — adds `test:unit` npm script and
  `mermaid@^10.9.1` as a runtime dependency (replaces the original
  jsDelivr CDN dynamic import; restores the local-first invariant).
- `apps/studio-console/tsconfig.json` — excludes `**/*.test.ts` from the
  Next typecheck so the `.ts`-extension import in the test file (required by
  `--experimental-strip-types`) does not break `tsc --noEmit`.

## How status shows up

| Status               | DAG node color (Mermaid `classDef`) | Stage chevron state         |
| -------------------- | ----------------------------------- | --------------------------- |
| pending              | grey                                | `pending`                   |
| running              | blue, pulsing                       | `active` on the Run cell    |
| completed            | green                               | `done`                      |
| needs_human_review   | amber                               | `needs-human` on active     |
| failed / abandoned   | red                                 | `failed` on active          |
| (n/a for greenfield) | —                                   | `not-applicable` (hatched)  |

## Render strategy

`TaskGraphView` keeps a ref to the last Mermaid source string. The live
polling loop calls the same component with fresh `tasks[]` every 2-3 s; if the
generated source is identical, `mermaid.render()` is skipped entirely and the
SVG stays untouched. Only an actual status change re-renders, which keeps the
DAG from flickering during a long-running session.

## Click-to-evidence

The DAG is intentionally not its own modal. Clicking a node:

1. Force-opens the `<details>` list view below the DAG.
2. Scrolls the matching `<li>` (`id="task-li-{rawId}"`) into the viewport.
3. Applies a 2.4 s `studio-task-highlight` flash so the user can see which row
   the click resolved to.

This avoids reimplementing evidence wiring that already exists in the list
view (and the inspector aside) — the DAG is the navigation layer.

## Safety boundaries

- No `.env.local` values are read or surfaced.
- No external LLM or detector calls; Mermaid is rendered fully client-side.
- No new dependency in `package.json`; Mermaid is fetched at runtime from
  jsDelivr ESM. Trade-off documented below.
- No backend orchestrator changes; no API contract changes.
- Rollback path: delete the 3 new files + revert 4 edits to page.tsx /
  globals.css / package.json. Backend is untouched.

## Known limitations and explicit deferrals

- **Mermaid is now bundled as an npm dep.** The original implementation
  loaded mermaid from jsDelivr CDN; that violated the project's local-first
  invariant, so RC-5G phase 2 added `mermaid@^10.9.1` to package.json and
  switched to a plain `await import("mermaid")` inside the lazy client
  loader. Runs offline. ~107 transitive packages added (mostly d3, dagre,
  cytoscape sub-modules used by mermaid).
- **Build verification incomplete in CI sandbox.** `tsc --noEmit` and
  `node --test` are both green. `next build` exceeds the 45 s sandbox cap
  and was not validated. Run on local machine before demoing.
- **Mermaid pulse selector unverified in browser.** The `.node.running > rect`
  CSS pulse depends on Mermaid 10's classDef-applied class names. If the
  selector is wrong, status colors still display correctly via the inline
  `classDef` fill/stroke — only the pulse animation degrades.
- **No persisted "DAG vs list" preference.** Each tab visit defaults to DAG
  visible, list collapsed. Acceptable for v1.
- **Stage chevron is project-wide.** It does not yet reflect per-change
  pipeline state when the user is mid-change. That's RC-5G-next if needed.

## Validation

- `cd apps/studio-console && npm run typecheck` ✓
- `cd apps/studio-console && npm run test:unit` ✓ (4/4)
- `cd apps/studio-console && npm run build` ⚠ run locally
- Browser check: open `/projects/ai-writing-naturalizer?tab=develop`,
  verify the DAG renders the 8-task graph horizontally, click a node,
  confirm list flash + scroll.
