# Studio Console — Locked Spec (RC-5A)

This is the **locked spec** for `apps/studio-console/`. Every implementation decision below was confirmed during RC-5A scoping. If something here turns out wrong during implementation, **stop and re-discuss with the operator before changing it** — don't quietly drift.

中文导读: 这是 Studio Console 的合同 — 实现过程中如果发现哪一条不对,先停下来和操作者讨论,不要悄悄改。

---

## 0. Purpose

A **local-only** Studio Console that visualizes and controls the existing `agent-studio` CLI runtime. It is NOT a SaaS, NOT a platform, NOT a chat client.

> **The frontend is the operator's eyes and a finger to press the button. It does not own state, does not run business logic, and does not replace the CLI.**

---

## 1. The 6 design principles (immutable)

1. **Local-first** — localhost only, single user, no auth, no cloud, no SaaS hosting.
2. **Artifact-first** — Console reads `.agent/` directly; never reinvents state.
3. **Human-in-the-loop** — every state transition (Lock, Run, Approve, Resume, Start Change) requires an explicit click; nothing auto-fires on page load.
4. **Evidence-driven** — every "completed" claim links to the artifact that proves it.
5. **Preview + Local Run** — Preview mode (copy command) is default; Live mode (whitelisted shell-out) is opt-in with safety modal.
6. **Function over visual** — ship the 7-page workflow first; visual polish is RC-5A.11.

---

## 2. Hard out-of-scope (NO list)

Not in v1. Each is documented as a future-milestone option, not a defect.

- No auth / multi-user / multi-tenant.
- No cloud deployment / hosting / Vercel target for the Console itself.
- No real-time streaming / SSE / websockets (polling only).
- No GitHub PR creation / push / remote git.
- No deploy buttons / Vercel triggers / Studio-side smoke checks.
- No multi-agent orchestration UI.
- No rich markdown editor (Monaco / CodeMirror) — plain `<textarea>` + side preview only.
- No settings page / preferences UI (env vars at startup only).
- No light/dark toggle (light mode pinned).
- No file upload (paste-into-textarea only).
- No drag-drop task reordering.
- No inline patch editing (read-only display).
- No trace replay / animation / progress bar.
- No cost / token tracking dashboard (warning banner only).
- No notification system / toast queue.
- No Naturalizer-specific UI.
- No live-execute approve / reject for review items in v1 (copy command only).

---

## 3. Stack (pinned)

| Choice | Decision |
|--------|----------|
| Framework | Next.js **15.5.18** + React **19.0.0** + TypeScript **5.7.3** (matches RC-4B demos exactly; NOT the canary version `apps/dashboard-legacy/` had) |
| Routing | App Router with one route per page (7 pages) |
| State | React `useState` + small custom hooks (`useProject`, etc.) — no Redux / Zustand / Jotai |
| Styling | CSS variables in `app/globals.css` + per-component className references — no Tailwind, no CSS-in-JS, no UI framework |
| Markdown editing | Plain `<textarea>` + side preview (`marked` or equivalent) |
| Filesystem access | Next.js API routes (Node `fs/promises`) |
| CLI shell-out (Live mode only) | `child_process.spawn` in `app/api/cli/route.ts` with strict whitelist + process manager |
| Polling | `setInterval` 3s on Run tab when status === running |

---

## 4. Repo location

```
apps/studio-console/
  package.json
  next.config.mjs
  tsconfig.json
  .gitignore
  README.md
  app/
    layout.tsx              ← AppShell + SidebarNav + TopBar
    page.tsx                ← redirect("/dashboard")
    globals.css             ← CSS variables + base shell
    dashboard/page.tsx
    design/page.tsx
    plan/page.tsx
    run/page.tsx
    review/page.tsx
    evidence/page.tsx
    change-request/page.tsx
    api/
      projects/route.ts
      projects/[id]/route.ts
      contracts/route.ts
      contracts/[id]/route.ts
      artifact/route.ts
      cli/route.ts          ← RC-5A.10 only
  components/
    SidebarNav.tsx
    TopBar.tsx
    StatusBadge.tsx
    CommandBlock.tsx
    CostWarningModal.tsx
    ArtifactViewerModal.tsx
    MarkdownEditor.tsx
    TaskGraphTable.tsx
    EvidenceCard.tsx
    ReviewItemCard.tsx
    DemoMatrixTable.tsx
  lib/
    paths.ts                ← path allowlist (load-bearing, RC-5A.1)
    artifacts.ts
    contracts.ts
    commands.ts
    preflight.ts            ← scope/non-goals scanner (RC-5A.5)
    safety.ts
```

---

## 5. The 7 pages

In execution order. Each page renders a stub in RC-5A.1 and gets fleshed out in its named subtask.

| # | Page | Subtask | Reads from | Writes to |
|---|------|---------|-----------|-----------|
| 1 | **Dashboard** | RC-5A.2 | `docs/EVALUATION.md`, `docs/rc4c-demo-suite-report.md`, `examples/`, optional `/tmp/rc4b-*` | (read-only) |
| 2 | **Design Workspace** | RC-5A.4 | `.studio-console/contracts/<id>/*` | same |
| 3 | **Plan Workspace** | RC-5A.5 | locked contract + `lib/preflight.ts` | (read-only) |
| 4 | **Run Monitor** | RC-5A.6 | `<project>/task-graph.json`, `<project>/.agent/autonomous/sessions/*/`, `<project>/.agent/autonomous/review-items/*` | (Live mode only via `/api/cli`) |
| 5 | **Review Queue** | RC-5A.8 | `<project>/.agent/autonomous/review-items/*` | (read-only; copy commands only in v1) |
| 6 | **Evidence Center** | RC-5A.7 | `<project>/.agent/changes/*/`, `<project>/.agent/runs/*/`, `<project>/final-run-status.md` | (read-only) |
| 7 | **Change Request Workspace** | RC-5A.9 | `<project>/.agent/changes/*/` | `.studio-console/contracts/<id>/change-request.md` (drafts) |

Sidebar order matches execution order; default route `/` redirects to `/dashboard`.

---

## 6. Contract storage layout

`.studio-console/contracts/<contract_id>/` (one dir per contract draft):

```
raw-requirements.md      ← what the operator pasted
discussion.md            ← scratch / decisions / rationale
product-contract.md      ← the editable rendered contract
mvp-requirements.md      ← carved-out MVP slice (this is what gets fed to agent-studio new --from)
open-questions.md        ← checkbox list (see § 7)
lock.json                ← {locked: bool, locked_at: iso8601, locked_by: "operator"}
```

Contract id format: `cr_<10-char-hex>` (matches existing `change_*` / `run_*` / `session_*` short-id pattern).

`.studio-console/` is **not** added to .gitignore by default — operators may commit contract drafts as part of their requirements work. Operators who don't want this can add `.studio-console/` to their project's `.gitignore` themselves.

---

## 7. Open questions format (load-bearing)

`open-questions.md` uses standard markdown checkboxes:

```
- [ ] Detector provider: Sapling or GPTZero?
- [x] Tone options: natural / professional / concise / academic
- [ ] Localized vs global storage for drafts?
```

**Lock rule:** count of `^- \[ \]` (unresolved) lines must be **0** before the "Lock MVP Contract" button enables. The Console renders unresolved counts inline next to the lock button; resolving a question = checking the box (Console does this via the editor).

---

## 8. Lock state machine

| State | UI affordance |
|-------|---------------|
| `DRAFT` | All editor panes editable; Lock button disabled (with reason) |
| `READY_TO_LOCK` | All preconditions met; Lock button enabled and prominent |
| `LOCKED` | Editor panes read-only; "Unlock for revisions" button visible (requires confirmation modal) |
| `UNLOCKED_FOR_REVISION` | Same as DRAFT, but lock.json.unlocked_at recorded |

**Lock preconditions (all must be true):**

1. `product-contract.md` is non-empty (≥ 50 chars stripped of whitespace)
2. `mvp-requirements.md` is non-empty (≥ 50 chars stripped of whitespace)
3. `open-questions.md` has 0 unresolved (`- [ ]`) items
4. `mvp-requirements.md` has at least one `## task` H2 section (sanity check that it parses as a requirements doc)

If any precondition fails, the Lock button is disabled with a tooltip explaining the missing precondition. Same set of preconditions are evaluated server-side on the actual lock POST so the UI can't lie.

---

## 9. Pre-flight scope scanner (`lib/preflight.ts`, RC-5A.5)

Runs on the locked `mvp-requirements.md`. Each finding is `{severity: "error" | "warning" | "info", message: string, line?: number}`.

| # | Pattern | Severity | Message |
|---|---------|----------|---------|
| 1 | `Scope:` line contains `\`...\`` (backticks) | warning | "Backticks will be stripped — usually fine but verify intent." |
| 2 | Body mentions `package.json` / `pnpm-lock` / `yarn.lock` / `package-lock.json` | error | "Lockfile changes are typically out-of-scope; tighten Scope or Non-goals." |
| 3 | Body mentions `\.env` / `API key` / `secret` / `token` | error | "Secrets in scope = drift risk; explicit Non-goal recommended." |
| 4 | Body mentions `deploy` / `Vercel` / `production` | warning | "Deploy is disabled by default; confirm intent." |
| 5 | Body mentions `prisma migrate` / `db push` / `migration` | warning | "Migrations are out of MVP unless explicitly opted in." |
| 6 | Body mentions `npm install <pkg>` / `add dependency` / `new dep` | warning | "Adding deps is high-drift; consider explicit Non-goal." |
| 7 | Acceptance count < 1 in any task | error | "Lock allowed but task with no acceptance criteria — dangerous; tighten requirements." |

Errors block the "Generate Start Command" button on the Plan page. Warnings show in a yellow banner above the command but don't block.

---

## 10. Generated commands (RC-5A.5 / RC-5A.9)

Plan page generates exactly:

```
agent-studio new --from .studio-console/contracts/<id>/mvp-requirements.md
```

After project exists (Run page):

```
agent-studio autonomous start --project <project_id>
agent-studio autonomous status --project <project_id> --json
agent-studio autonomous reviews list --project <project_id> --json
```

Change Request Workspace generates:

```
agent-studio change new --from .studio-console/contracts/<id>/change-request.md --project <project_id>
agent-studio change run latest --project <project_id>
agent-studio change validate latest --project <project_id> --json
```

All commands rendered via `<CommandBlock>` with copy-to-clipboard button. Live mode (RC-5A.10) replaces "copy" with "run locally".

---

## 11. API routes (6, locked)

| Route | Methods | Purpose |
|-------|---------|---------|
| `/api/projects` | GET | List projects under `<root>/.agent-studio/projects/` |
| `/api/projects/[id]` | GET | Detail: task-graph + sessions + reviews + changes for one project |
| `/api/contracts` | GET, POST | List contracts; create new contract |
| `/api/contracts/[id]` | GET, PUT, DELETE | Read / update single sub-file via `?file=`; lock/unlock via `?file=lock.json` |
| `/api/artifact` | GET | Read any artifact by absolute path; **must pass `lib/paths.ts` allowlist check** |
| `/api/cli` | POST | RC-5A.10 only. Whitelisted shell-out + process manager. |

---

## 12. Path allowlist (`lib/paths.ts`, load-bearing)

Reads are allowed ONLY for paths that resolve inside one of these roots OR are exactly one of the allowed-files entries:

**Allowed roots (recursive):**
```
<workspace>/.agent-studio/projects/**
<workspace>/.studio-console/contracts/**
<workspace>/.studio-console/runs/**            (Live mode only, RC-5A.10)
<workspace>/examples/**
```

**Allowed individual files:**
```
<workspace>/docs/EVALUATION.md
<workspace>/docs/rc4c-demo-suite-report.md
```

**Path resolution rule:** `path.resolve(absPath)` first, then check membership. Reject any `..` traversal, any symlink that resolves outside, any absolute path outside the workspace.

`<workspace>` is determined by `process.env.LOCALAGENTS_ROOT` (override) or `path.resolve(process.cwd(), "..", "..")` (default, assuming Console runs from `apps/studio-console/`).

---

## 13. CLI route whitelist (`/api/cli`, RC-5A.10)

Only these command shapes accepted (after argv parse):

```
agent-studio init
agent-studio new --from <path>                  ← path must be inside allowlist
agent-studio autonomous start [--project <id>]
agent-studio autonomous status [--project <id>] [--json]
agent-studio autonomous reviews list [--project <id>] [--json]
agent-studio change new --from <path> [--project <id>]
agent-studio change run latest [--project <id>]
agent-studio change validate latest [--project <id>] [--json]
agent-studio autonomous validate-artifacts [--project <id>] [--json]
```

Anything else → HTTP 400. Any path argument that fails the allowlist check → HTTP 400.

Plus: per-project process lock — if a `running` session exists for that project, refuse to spawn a new one (HTTP 409). Stop button sends SIGTERM and waits for clean exit before clearing the lock.

---

## 14. Live mode evidence layout (`.studio-console/runs/<run_id>/`)

```
command.json    {command, args, project_id, started_at, started_by: "operator"}
stdout.log      tail-able; secrets redacted via lib/safety.ts
stderr.log      same
status.json     {state: "running"|"completed"|"failed"|"stopped", exit_code: int|null, finished_at: iso|null, pid: int}
pid.json        {pid: int, started_at: iso}
```

`run_id` format: `liverun_<10-char-hex>` (distinct prefix from `run_*` to avoid confusion with the inner-loop run package).

---

## 15. Visual spec (locked)

| Element | Spec |
|---------|------|
| Sidebar | 220px wide, fixed, full-height; nav items 40px tall; active item bg `#eef2ff`, text `#2563eb` |
| Top bar | 56px tall, sticky top, white bg, 1px bottom border `#e2e8f0` |
| Main content | max-width 1080px, padded 32px sides, 24px top/bottom |
| Cards | 1px border `#e2e8f0`, 8px radius, 16px padding, white bg, `box-shadow: 0 1px 2px rgba(0,0,0,0.04)` |
| Tables | zebra-striped at `#fafbfc`, header bg `#f8fafc`, cell padding 12px |
| Buttons primary | bg `#2563eb`, text white, 10px×16px padding, 6px radius, hover `#1d4ed8` |
| Buttons ghost | bg transparent, border `#e2e8f0`, text `#0f172a`, hover bg `#f1f5f9` |
| Buttons danger | bg `#dc2626`, text white, hover `#b91c1c` |
| Spacing scale | 4 / 8 / 12 / 16 / 24 / 32 / 48 px (CSS variables `--sp-1` … `--sp-7`) |
| Font UI | system-ui, -apple-system, "Segoe UI", sans-serif |
| Font mono | "SF Mono", Menlo, Consolas, monospace |
| Font sizes | 12 / 14 / 16 / 18 / 24 / 32 px |

| Status badge | Color |
|--------------|-------|
| completed / delivered | `#16a34a` (green) |
| running | `#2563eb` (blue) |
| pending | `#64748b` (gray) |
| needs review | `#d97706` (amber) |
| failed | `#dc2626` (red) |
| locked | `#7c3aed` (purple) |

---

## 16. Footer (every page)

```
Local Agent Studio Console · v0.1 · Reads .agent-studio/ · Writes .studio-console/contracts/
```

---

## 17. Acceptance criteria for RC-5A as a whole

The Console is "done" when an interviewer can do this in front of you, in under 5 minutes, without you typing anything in the terminal:

1. Open `localhost:3000` → land on Dashboard → see "3 / 3 green" matrix from committed evidence.
2. Click "Load demo seeds" → 3 projects appear in project picker.
3. Click into Design Workspace → create new contract → paste a paragraph → save → file lands at `.studio-console/contracts/cr_xxx/raw-requirements.md`.
4. Edit `mvp-requirements.md` → resolve all open questions → click Lock MVP Contract → see status flip to LOCKED.
5. Click into Plan → see pre-flight scanner output → see generated `agent-studio new --from …` command.
6. (Preview mode) Click Copy → paste into terminal → run externally → come back to Run Monitor → click Refresh → see task graph populate.
7. Click into Evidence Center → see latest change card → open `delivery-report.md` modal.
8. Click into Change Request Workspace → create new change → see generated `agent-studio change new --from …` command.
9. (Live mode, optional) Toggle Safety: Live → click Run Locally on Run page → see CostWarningModal → confirm → see live status update via polling → click Stop Run if needed.

If all 9 work, RC-5A ships.

---

## 18. What to do if reality contradicts this spec during implementation

1. **Stop**.
2. Document the contradiction in a comment at the top of the affected file.
3. Surface it in the next subtask completion report.
4. Wait for operator decision before changing the spec or working around it.

This is a hard rule. Quiet drift here is the most expensive class of bug.
