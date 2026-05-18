# LEGACY — `apps/dashboard/` (archived)

This directory was renamed from `apps/dashboard/` to `apps/dashboard-legacy/` on 2026-05-14 as part of RC-5A.0.

## What it was

An early prototype of a project dashboard, built before Local Agent Dev Studio's runtime had its current shape. The hardcoded mock data inside `app/page.tsx` references workflow phases (`Intake / Research / PRD / Design / Architecture / Implementation / QA / Review / Merge`) that belong to the **pre-MVP-3 workflow_engine** model. The current AI-native runtime — task graph + Promotion Gate + Apply Gate + Change Request Mode — does not use those phases. The skeleton was never wired to live data and was never used in any RC milestone.

## What replaced it

The current Studio frontend is **`apps/studio-console/`** (RC-5A onward). It is artifact-driven (reads `.agent/` directly), filesystem-coupled (writes contract drafts to `.studio-console/contracts/`), and matches the runtime concepts the Studio actually uses.

If you were looking for the working frontend, see:

- `apps/studio-console/` — the live console
- `apps/studio-console/README.md` — run instructions
- `docs/STUDIO_CONSOLE_SPEC.md` — locked spec
- `docs/ARCHITECTURE.md` — runtime architecture (so you can see why the legacy phase model no longer fits)

## Why it's archived, not deleted

Two reasons:

1. **Git history.** Renaming preserves the git log of the early UI exploration in case anyone wants to see how the project's UI thinking evolved.
2. **Reviewer clarity.** A reviewer who sees `apps/dashboard/` in the repo and finds it unused would assume the project's UI is broken. Renaming to `apps/dashboard-legacy/` plus this README explicitly tells them: it's intentional archive, the live UI is elsewhere.

## Do not use

- Do not run `npm install` or `npm run dev` here — the hardcoded mock data won't reflect anything real.
- Do not reference these components from the live console.
- Do not extract patterns from here as "the right way" — the layout is fine but the data model is wrong.

## Safe to delete?

Yes, eventually. Once the project has shipped enough downstream milestones that the early prototype's git history is no longer interesting, this directory can be deleted entirely. For now it's preserved as archive.
