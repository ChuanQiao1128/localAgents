# Studio Console

Local-first frontend for `agent-studio`. RC-5A scaffold — the 7 pages render but most are stubs that get fleshed out in subtasks RC-5A.2 through RC-5A.11. See [`docs/STUDIO_CONSOLE_SPEC.md`](../../docs/STUDIO_CONSOLE_SPEC.md) for the full spec.

## Run

From the repo root:

```bash
cd apps/studio-console
npm install
npm run dev
# open http://localhost:3000
```

The Console reads `.agent-studio/projects/` and `examples/` from the **two parents up** by default (i.e. the repo root). Override with the `LOCALAGENTS_ROOT` env var if you launch from elsewhere:

```bash
LOCALAGENTS_ROOT=/path/to/clone npm run dev
```

## Validate

```bash
npm run build       # production build
npm run typecheck   # tsc --noEmit
```

Both must exit 0 before any commit.

## Repo touchpoints

The Console touches **two filesystem locations**:

- **Reads** `.agent-studio/projects/**`, `.studio-console/contracts/**` (its own writes), `examples/**`, plus a small allowlist of files under `docs/`. Path allowlist enforced by `lib/paths.ts`.
- **Writes** `.studio-console/contracts/<contract_id>/{raw-requirements,discussion,product-contract,mvp-requirements,open-questions}.md` + `lock.json`. Live mode (RC-5A.10) additionally writes `.studio-console/runs/<run_id>/`.

The Console **never** touches `.git/`, `node_modules/`, anywhere outside the workspace root, or anywhere the path allowlist refuses.

## Pages

In execution order (sidebar order matches):

1. **Dashboard** — 3/3 demo matrix from committed evidence (RC-5A.2)
2. **Design Workspace** — contract editor + lock state machine (RC-5A.4)
3. **Plan Workspace** — pre-flight scope scanner + generated `agent-studio` command (RC-5A.5)
4. **Run Monitor** — task graph + session status + polling (RC-5A.6)
5. **Review Queue** — human-in-the-loop checkpoints (RC-5A.8)
6. **Evidence Center** — per-change schema-validated artifact viewer (RC-5A.7)
7. **Change Request Workspace** — write change-request.md + generate change run command (RC-5A.9)

RC-5A.1 ships the shell (sidebar, top bar, footer, all 7 routes wired) plus the design system (CSS variables in `app/globals.css`) and the path-allowlist primitives (`lib/paths.ts`).

## Out of scope (NO list)

See [`docs/STUDIO_CONSOLE_SPEC.md`](../../docs/STUDIO_CONSOLE_SPEC.md) § 2. Short version: no auth, no cloud, no streaming, no PR automation, no deploy buttons, no rich markdown editor, no settings page.
