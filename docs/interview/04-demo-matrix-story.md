# Demo Matrix — How to Tell This Story

This doc is the narrative companion to `docs/EVALUATION.md` (the dry evidence) and `docs/rc4c-demo-suite-report.md` (the per-demo run details). It's structured for three time budgets: **5 minutes** (elevator), **10 minutes** (whiteboard), **30 minutes** (full walkthrough). Use whichever fits the audience.

中文导读: 这一篇是 demo matrix 的"怎么讲"版本 — 给三种不同时间预算 (5 / 10 / 30 分钟) 配好讲法,你直接念就行。

---

## The 5-minute version (elevator)

> "I built Local Agent Dev Studio — an AI-native software delivery runtime. You write a `requirements.md` for a new project or a `change-request.md` for an existing one, and the Studio drives a real Codex agent through a deterministic pipeline: deterministic decompose, real Codex patch generation, 12-rule Promotion Gate, 10-rule Apply Gate, real `git apply`, real commit with provenance trailers, schema-validated `applied-change.json` and `delivery-report.md`. To prove it's not a single-template generator, I ran three demos: an AI Writing Quality Editor, an AI Usage & Cost Planner, and an Agent Review Queue Console — three different verticals, three different state shapes, three different change archetypes. **Same machinery drove all three from `requirements.md` to a verified change commit. 3 of 3 green.** Real Codex, real npm run build, zero open review items. The complete evidence is in `docs/EVALUATION.md`."

What to show on screen if they ask for proof:

- `cat docs/EVALUATION.md` — show the per-demo evidence table.
- `git log --oneline --all -20` in any of the three workspaces — show the 3 greenfield commits + 1 change commit per demo.
- `cat .agent/changes/<change_id>/applied-change.json` — show the schema-validated artifact.
- `cat .agent/changes/<change_id>/delivery-report.md` — show the operator-facing summary.

---

## The 10-minute version (whiteboard)

```
Greenfield:
  requirements.md  → autonomous start  → 3 commits         → npm run build ✓
                       (real Codex)

Brownfield:
  change-request.md → change run       → 1 commit on       → npm run build ✓
                       (real Codex)        agentic/change/<id>   + delivery-report.md
                                                              + applied-change.json

Repeat across 3 distinct verticals:
  AI Writing Editor      ✓
  AI Usage Cost Planner  ✓
  Agent Review Queue     ✓

Same machinery in the middle.
Same 12-rule Promotion Gate.
Same 10-rule Apply Gate.
Same artifact contract.
Same regression suite (337 tests pass).
```

Key talking points if they probe deeper:

1. **"Why two gates?"** Promotion Gate measures *what Codex produced* (the patch). Apply Gate measures *what the repo can safely accept right now* (live git state). A candidate that passed Promotion at time T can fail Apply at T+1 if HEAD moved. They measure different things.

2. **"Why deterministic Python in the gates instead of LLM judges?"** If the gate were "ask an LLM whether the patch is in scope," the audit trail would be circular — you'd need another LLM to check the first LLM. Hard rules in real Python mean a human reviewer can read the rule + read the artifact + verify the decision matches.

3. **"What happens when Codex does something wrong?"** Three layers catch it: Promotion Gate, Apply Gate, post-apply integration (real `npm run build`). If anything fails, a review item lands in the human-in-the-loop queue with the gate breakdown + suggested commands. Across the RC-4C matrix, zero review items were emitted.

4. **"Show me a real failure that surfaced."** Pull up `docs/interview/03-failure-cases.md`. The first real-Codex run on the demo matrix surfaced the **scope-parser-captures-backticks** bug (Case 7). The Promotion Gate correctly refused the patch because `fnmatch("app/page.tsx", "\`app/**\`")` returned False. The fix was strip-wrapping-backticks-in-the-parser + a 6-test regression suite. Real Codex behaved correctly; the bug was on the parser side. That's the system working.

---

## The 30-minute version (full walkthrough)

### Minutes 0-5: framing

Open with the elevator pitch. Then:

> "Most AI coding agents today land at one of two extremes. On one end you have **in-IDE copilots** like Cursor — great for one-off help, but they leave you to decide whether each suggestion is worth taking. On the other end you have **end-to-end auto-shipping bots** like Devin — they promise to build whole apps, but you can't review what happened because there's no audit trail. Local Agent Dev Studio sits in the middle: autonomous enough to drive multi-task delivery without a human in the loop, but **every decision is gated by deterministic rules and every artifact is schema-validated**. A reviewer can `git log --grep "Change-Id:"` and find every commit a specific change session produced, then read the `delivery-report.md`, then drill into `applied-change.json`, then `promotion-report.json`, then the `patch.diff`. Reproducible. Auditable. No black box."

### Minutes 5-10: greenfield walkthrough on Demo 3 (Agent Review Queue Console)

Open `examples/agent-review-queue-console/requirements.md` on screen.

> "Here's what the operator wrote. Three tasks. Each one has an intent paragraph, an acceptance criteria list, and a scope_paths block — the files this task is allowed to modify. The decomposer is **deterministic Python regex** — no LLM in the parsing step. So the task graph is reproducible: you can run the parser locally and check the agent's task list matches what you actually wrote."

Open the workspace's `task-graph.json`:

> "Each task got a stable id (task-001, 002, 003), the parsed intent + acceptance + scope, plus — once executed — a commit SHA and a list of run_ids. Three completed commits: 45ac00a, 7e7f113, 5e1c3ce. Real git history."

Run `git log --oneline -5 5e1c3ce` (or copy-paste the equivalent from the report):

> "Each commit was made by `commit_task` with provenance trailers. Look — `Agent-Task-ID: task-002`, `Agent-Run-ID: run_bd241e13eb`, `Selected-Candidate: candidate-a`, `Promotion-Decision: promote`, plus a pointer to the promotion-report.json. You can grep this forever."

### Minutes 10-15: brownfield (change request) walkthrough on Demo 3

Open `examples/agent-review-queue-console/changes/01-add-sla-risk-badges.md`:

> "Now the change request. Same project, but instead of building from scratch, the operator says: 'Add SLA risk badges. Blocking items older than 24h get an urgent badge; warning items older than 8h get a review-soon badge. Use only built-in `Date`, no date library. No backend, no auth, no deploy.' Hard non-goals are listed explicitly so the gate can refuse drift."

Open the workspace's `applied-change.json`:

> "After change run completed, the runtime wrote this. Schema is `agentic.applied_change.v1`. Look at the fields: change_id, candidate-a chose strategy 'conservative', run_id is run_1b3d513d29, base_commit was 5e1c3ce — that's the LAST greenfield commit, so no drift between greenfield finishing and change run starting. applied_to_commit is also 5e1c3ce. The change commit landed at 63979f5 on `agentic/change/change_e8525afae2`. Files touched: app/page.tsx and components/reviews.ts."

Open the workspace's `delivery-report.md`:

> "And here's the operator-facing summary. Goal verbatim from the change-request. Result: completed. What was changed: two files. Validation section: `apply: passed`, `eval.build: passed — npm run build`, `eval.required: passed`, `eval.typecheck: passed — npm run typecheck`, `promotion: passed — decision=promote, hard_gates=6/6 passed`. Risks: none. Commit branch + SHA + the full message including the Change-Id trailer. Review queue: 0 open items. Elapsed 681 seconds."

### Minutes 15-20: the gates

Open `orchestrator/core/agentic_runtime.py` (or just reference it):

> "The Promotion Gate is here. Twelve hard rules in real Python. `source_patch_present`, `diff_within_scope`, `patch_apply_check_passed`, `required_eval_executed`, `required_eval_passed`, `no_critical_security_finding`, `no_overfit_to_evals`, `out_of_scope_change_count == 0`, etc. If all pass, decision is `promote`. If some pass and some fail, decision is `needs-human-review` and a review item lands in the human queue. If none pass, decision is `abandoned` and the controller decides whether to try another candidate or pause."

Open `orchestrator/core/run_package.py`:

> "Here's the Apply Gate. Ten more hard rules. Re-checks safety from the live-git side: HEAD matches base_commit, worktree is clean modulo `.agent/` and `task-graph.json`, `git apply --check` exits 0, no out-of-scope changes, the re-apply guard hasn't already fired. If all pass, real `git apply` runs. If anything fails, ApplyGateRefused exception, no apply, review item emitted."

> "Two gates instead of one because they measure different things. Promotion = is the candidate good enough? Apply = is the repo ready to accept it?"

### Minutes 20-25: real failures

Pull up `docs/interview/03-failure-cases.md`.

> "The strongest evidence the project works isn't the happy path — it's the failure cases. Eight distinct failure modes have surfaced across RC-3 and RC-4. Every one is fixed, every one is locked behind a regression test."

Walk through Case 7 (the scope-backticks bug) in detail:

> "First real-Codex run on the RC-4B demo matrix. Greenfield paused at task-001. Review queue had `review_2beda9738c` open with reason `failed-apply`. Codex had produced a patch. `npm run build` passed. `tsc --noEmit` passed. `patch_apply_check_passed=true`. `source_patch_present=true`. But Promotion Gate refused with `diff_within_scope=false` — the changed-files report said `app/page.tsx within_scope=false` even though task-001's scope was `app/**`."
> 
> "I traced it to the parser. I'd written `Scope: \`app/**\`, \`components/**\`` because backticks render as code in markdown previewers. The parser captured the backticks literally. Then `fnmatch.fnmatch('app/page.tsx', '\`app/**\`')` returned False, the gate blocked, the patch was rejected. **Real Codex behaved correctly. The bug was on the parser side.**"
>
> "The fix was small — strip wrapping backticks defensively, plus support a multi-line bullet form for `Scope:` so writers don't have to use commas. Six new regression tests lock both forms. The next run was 3/3 green."

> "That's the system working. The Promotion Gate refused to land a patch it couldn't prove was in scope. The audit trail surfaced the exact reason. Fixing it took an hour. The regression test means it'll never fire again."

Same beat for Case 2 (corrupt difflib patch — surfaced the entire patch generation rewrite + a new hard gate) if there's time.

### Minutes 25-30: limitations + next steps

> "What's deliberately NOT in the demo matrix? See `docs/EVALUATION.md` § Limitations. Briefly: no Vercel deploy in RC-4C — that's a noise/cost surface that doesn't help prove the change-mode works. No backend or DB in the three demos — the Studio supports them, RC-3C and RC-3D ran FastAPI + Prisma greenfield demos, but for the matrix I optimized for portfolio readability. No GitHub PR automation yet — clean future milestone. Single-candidate per task in this matrix — the multi-candidate path was exercised in earlier RC-3 dogfoods, but for cost control the matrix uses single-candidate."
>
> "Next milestone is RC-4D: portfolio packaging. README hero, ARCHITECTURE.md, optional Vercel previews + screenshots, optional GitHub PR creation hook. Then product direction — the AI Writing Naturalizer is the natural product target on top of this runtime."

---

## Anticipated questions + concise answers

### "How long did this take?"

The Studio's runtime came together over multiple RC milestones (RC-1 through RC-4). The 3-demo matrix specifically is RC-4B (matrix prep), RC-4C (real Codex run), RC-4C.1 (cleanup of the bugs the first real run surfaced). Most of the time was hardening — building the gates and the artifact contract took substantially longer than building the agent loop itself. The lesson: **the audit trail is the product, not the agent**.

### "What stack?"

Python 3.10+ for the orchestration layer. SQLite for the project / run / approval database. No external services beyond Codex CLI and (in earlier dogfoods) the Vercel CLI. The demos themselves are Next.js 15.5.18 + React 19 + TypeScript 5.7 with `localStorage` only — no backend in the matrix.

### "Why Codex CLI specifically?"

Codex CLI is the most surface-stable agent runtime I had: workspace-write sandbox, on-request approval, deterministic environment isolation. The patch worker abstraction in `orchestrator/core/codex_patch_worker.py` is replaceable — swapping in another CLI agent is a clean diff against that one file.

### "How does it handle a Codex failure?"

Three escape hatches: (a) Promotion Gate refuses, candidate goes to `abandoned` or `needs-human-review`. (b) Apply Gate refuses, review item lands in the human queue with full context. (c) Repair loop — if eval fails on a candidate, the runtime re-prompts Codex with the failure data, capped by `max_repair_attempts_per_candidate`. If repair exhausts, the candidate is marked `candidate_abandoned` in the abandonment log, and the next candidate (if any) gets a chance.

### "Can it run on real production code?"

The architecture supports it (Apply Gate enforces base_commit + clean worktree + scope checks before any `git apply`). The current dogfoods are intentionally tiny because the goal was proving the orchestration. A production deployment would want: real CI integration (GitHub Actions hook), real PR creation (RC-4E or later), and tighter scope_paths per change. Nothing in the design blocks this; it's just additional surfaces to wire.

### "What's the most surprising thing you learned?"

The first real-Codex run on Demo 1 paused on a parser bug, not a Codex bug. **Real-world dogfood is non-negotiable.** The unit suite was 100% green; the integration was wrong. Every milestone in this project shipped at least one bug that only surfaced once the actual flow ran on a real project. Plan for it; budget time for it; treat the failure-case catalog as the strongest evidence the system works.

### "What would you do differently?"

Build the artifact contract first, the agent loop second. I built them in the other order and had to retrofit the schemas. The artifact-validation layer is what makes the audit trail trustworthy; everything else is in service of that.

---

## What to leave behind after the conversation

1. The repo URL.
2. `docs/EVALUATION.md` — the matrix-level evidence summary.
3. `docs/rc4c-demo-suite-report.md` — per-demo run-by-run details.
4. `docs/interview/03-failure-cases.md` — the failure case catalog.
5. (Optional, if time) one workspace's `.agent/changes/<change_id>/` directory tarball — a tangible "here's the actual evidence trail."

Don't lead with code unless they ask. The artifacts and the evidence are the demo. Code is the implementation detail.
