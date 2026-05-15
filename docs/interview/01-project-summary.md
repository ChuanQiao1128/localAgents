# Local Agent Dev Studio — Project Summary

## Elevator pitch (one paragraph)

Local Agent Dev Studio (本地智能开发工作室) is an **AI-native software delivery runtime**. You write a `requirements.md` (or a `change-request.md` for an existing project), and the Studio drives a real Codex agent through deterministic **decompose → context → patch → eval → promote → safe-apply → commit → deliver** loop, leaving behind a complete schema-validated evidence trail (per-task git commits with provenance trailers, an `applied-change.json`, a `delivery-report.md`, a Promotion Gate report, and a per-candidate run package). Across three different small Next.js + TypeScript demos — an AI Writing Quality Editor, an AI Usage & Cost Planner, and an Agent Review Queue Console — the same machinery handled both **greenfield generation** (build a project from `requirements.md`) and **change requests** (add a new feature to an existing project) end-to-end with no manual intervention. Real Codex. Real `git apply --check`. Real `npm run build`. Three demos, 3/3 green.

中文一句话: Local Agent Dev Studio 是一个 AI 原生软件交付系统 — 你写 `requirements.md` 或 `change-request.md`,系统调真实 Codex agent 完成"分解 → 取上下文 → 出补丁 → 跑评估 → 投票闸门 → 安全应用 → git commit → 出交付报告"全流程,并留下可被 schema 校验的完整证据。三个不同方向的 demo (AI 写作编辑器/AI 成本规划器/Agent 人审队列控制台) 端到端跑通,**绿色 3/3**。

---

## What problem this solves

Most "AI coding agents" today are either:

1. **In-IDE assistants** that produce a diff but leave the operator to decide whether to accept it. Good for one-off help. Doesn't scale to "drive 3 tasks unattended overnight."
2. **End-to-end auto-shipping bots** that promise "build me a SaaS" and produce something that looks live but cannot be reviewed because there's no audit trail of what the agent did or why.

Local Agent Dev Studio sits in the middle: **autonomous enough to drive multi-task delivery without a human in the loop, but every decision is gated by deterministic rules and every artifact is schema-validated.** A reviewer can read `delivery-report.md`, then `git log --grep "Change-Id:"`, then `applied-change.json`, then `promotion-report.json`, then the `patch.diff`, and reconstruct exactly what happened and why. No black box.

---

## What's been proven (RC-4C, 3/3 green)

| # | Demo | Greenfield | Change | Build | Reviews |
|---|------|------------|--------|-------|---------|
| 1 | AI Writing Quality Editor | 3 commits (real Codex) | Add deterministic clarity score 0-100 | ✅ | 0 |
| 2 | AI Usage & Cost Planner | 3 commits (real Codex) | Add budget warning + break-even comparison | ✅ | 0 |
| 3 | Agent Review Queue Console | 3 commits (real Codex) | Add SLA risk badges (urgent / review-soon) | ✅ | 0 |

12 real-Codex commits across 3 distinct AI-product verticals. Same machinery, no per-demo special-case code. Detailed evidence in `docs/rc4c-demo-suite-report.md` and `docs/EVALUATION.md`.

---

## Glossary (terms a reader of this repo needs)

### AI-native SDLC

A software development lifecycle where **AI is the executor, not the assistant**. The human writes the intent (a `requirements.md` or `change-request.md`); the agent decomposes, plans, codes, tests, scores, applies, and commits. The human reviews artifacts, not keystrokes.

中文: AI 原生软件开发生命周期 — 人写意图,AI 负责分解、写代码、跑测试、应用补丁、提交。人审产出物,不审打字。

Differs from AI-assisted SDLC where the AI is a copilot inside the human's editor session.

### Task graph

A JSON structure (`task-graph.json` at the project root) that decomposes a `requirements.md` into discrete tasks. Each task has an id (`task-001`...), a title, an `intent` paragraph, an `acceptance_criteria` list, a `scope_paths` list (which files the task may modify), `dependencies`, a `status` (`pending` / `running` / `completed` / `needs-human-review` / `abandoned`), and — once executed — a `commit` SHA and `run_ids`.

中文: 任务图 — 一个 JSON 文件,把需求拆成独立任务,每个任务有 id、标题、意图、验收标准、可改动的文件范围、依赖、状态。

The decomposer is **deterministic Python** (no LLM in the parsing step), so the same `requirements.md` always produces the same task-graph. This is load-bearing for reproducibility.

### Context pack

A per-task snapshot the controller hands to Codex. It includes the task's intent + acceptance criteria + scope_paths + previous task commits (if any) + a deterministic project-shape digest (top-level dirs, `package.json` scripts, last 5 commits, README excerpt). Codex sees only this; it cannot wander the entire repo unless `scope_paths` permits.

中文: 上下文包 — 系统给 Codex 的工作输入。任务意图 + 验收标准 + 可改文件范围 + 之前任务的 commit 摘要 + 项目结构指纹。

### Promotion Gate

A deterministic 12-rule check that runs AFTER Codex produces a candidate patch but BEFORE it can be applied. The gate reads the candidate's `patch.diff`, `changed-files.json`, `score.json`, and `eval-results.json`, then computes a roll-up `decision` ∈ {`promote`, `needs-human-review`, `abandoned`}. Sample rules: `source_patch_present`, `diff_within_scope`, `patch_apply_check_passed` (RC-3E.2 fix), `required_eval_executed`, `required_eval_passed`. The gate output is the `promotion-report.json`.

中文: 投票闸门 — 一组 12 条死规则,在 Codex 出补丁之后、应用之前跑;判断这个补丁是 `promote` (放行) / `needs-human-review` (要人审) / `abandoned` (放弃)。

### Apply Gate

A second deterministic 10-rule check that runs when the controller actually applies a `promote`-decided candidate. Sample rules: `git apply --check` clean, base_commit equals current HEAD, worktree clean (modulo `.agent/` + `task-graph.json`), no out-of-scope file mutations, re-apply guard. Output: `applied-candidate.json` (per-run) and (for change-mode) `applied-change.json` (per-change).

中文: 应用闸门 — 真正 `git apply` 之前的二次安全检查,10 条死规则,任何一条不过就拒绝应用。

The two-gate pattern is intentional: Promotion Gate is about **what Codex produced**; Apply Gate is about **what the repo can safely accept right now**.

### Change Request Mode

A second entry point next to autonomous (greenfield) mode. You write `change-request.md` describing one targeted modification to an EXISTING project (goal / scope_paths / non-goals / acceptance criteria). The Studio:

1. Parses the change-request and writes `change-contract.json` (schema `agentic.change_contract.v1`).
2. Builds a 1-task task-graph from the contract.
3. Drives that one task through the same `AutonomousController` greenfield uses.
4. Applies via the Apply Gate, commits with `Change-Id` + `Source-Change-Request` git trailers on a `agentic/change/<change_id>` branch.
5. Writes `applied-change.json` (schema `agentic.applied_change.v1`) + `delivery-report.md`.
6. `change validate latest --json` schema-validates all three change-dir artifacts.

中文: 修改请求模式 — 第二个入口。对已有项目写一个 change-request.md (目标/范围/不做什么/验收),系统跑同一套机器,完成单任务修改并留完整证据。

### Artifact validation

Every load-bearing artifact has a schema validator in `orchestrator/core/artifact_validation.py`. `agent-studio change validate` and `agent-studio autonomous validate-artifacts` walk the change dir / session dir and return `ok=true/false` with per-artifact error lists. This is what makes the audit trail trustworthy: a half-rendered or hand-edited artifact is caught.

中文: 制品校验 — 每个关键 JSON/MD 文件都有 schema 校验器,可以一键检查整个会话目录里的产物是否齐全且形状正确。

### Delivery report

`delivery-report.md` is the operator-facing one-page summary of a change run. Sections: Goal, Result (`completed` / `needs-human-review` / `failed`), What was changed (file list), Validation (`eval.* / promotion / apply` rows derived from real artifacts), Risks, Commit (branch / SHA / message), Review queue, Timing. Validated by `validate_delivery_report_text`.

中文: 交付报告 — change run 跑完之后给运维/审查人员看的一页 markdown,记录目标、结果、改了哪些文件、验证结果、commit 信息、有没有 review 队列阻塞。

---

## Likely interview questions

### Q1. "Why not just use Claude Code or Codex directly? They can run for hours."

I do use Codex — as the **patch worker**. Studio is the **runtime around it**: requirements → task graph → context pack → eval harness → Promotion Gate → Apply Gate → real `git commit` → schema-validated `applied-change.json` + `delivery-report.md`. The goal isn't to replace coding agents; it's to make their output controlled, auditable, repeatable.

**The model is not the system. The delivery loop is the system.**

If you let Codex run for hours unattended you get a pile of code changes plus a model summary. With Studio you get a task graph, a per-candidate run package, `changed-files.json`, `eval-results.json`, `promotion-report.json` with the gate breakdown, `applied-change.json`, `delivery-report.md`, and real git commits with `Agent-Task-ID` / `Change-Id` trailers `git log --grep`-able forever.

Cursor (copilot — accept every diff) and Devin (end-to-end auto — un-reviewable result) are different points on the spectrum. Studio is **deterministic gates + complete evidence trail**.

### Q2. "What happens when Codex produces something wrong?"

It's caught at one of three layers:

1. The **Promotion Gate** refuses (e.g. `diff_within_scope=false` because Codex tried to edit a file outside `scope_paths`). The candidate becomes `abandoned`; the controller logs why and either tries another candidate or pauses for human review.
2. The **Apply Gate** refuses (e.g. `git apply --check` fails because the base_commit moved). The patch is never applied. A review item is emitted to the human queue.
3. **Post-apply integration** (real `npm run build` + `npm run typecheck`) catches anything that compiled in isolation but breaks the cumulative tree.

Across the RC-4C matrix, every change run cleared all three layers. RC-3A surfaced a real `*.tsbuildinfo` Promotion Gate failure that drove a runtime fix; RC-3E surfaced a corrupt-patch generation bug that drove the difflib → real-`git diff` rewrite. Both fixes shipped with regression tests; both layers have been hardened by real failures.

### Q3. "Why did you build your own runtime instead of using LangGraph / CrewAI / etc.?"

Two reasons. First, **deterministic gates need real code, not LLM-judged 'should we promote this?' calls**. A Promotion Gate that says "this patch is in scope" must use `fnmatch` against the actual diff, not a model rationale. Second, **the artifact contract is the product**. The Studio's value isn't "an agent that codes" — every framework can do that. It's "every decision leaves a schema-validated audit trail," and that's easier to enforce as a single Python module than as a graph of LLM calls in someone else's framework.

### Q4. "How do you know it's not just memorizing the demo?"

(a) The 3 demos are 3 different verticals (text quality / cost calc / governance dashboard), 3 different state shapes, 3 different change archetypes; one trained recipe wouldn't cover all three. (b) The decomposer is deterministic Python — same `requirements.md` always produces the same task-graph, so the agent isn't learning per-demo. (c) The Apply Gate is identical across demos; same 10 hard rules fired on every commit. (d) You can run a 4th demo by writing a 4th `requirements.md` + change-request.md without touching any code.

### Q5. "What's NOT in the matrix?"

See `docs/EVALUATION.md` § Limitations. Short version: no Vercel deploy in RC-4C (deliberate — that's RC-4D portfolio), no backend / API / DB in the demos (the Studio supports them — RC-3C / RC-3D dogfoods exercised FastAPI + Prisma — but the matrix optimizes for portfolio readability), no GitHub PR automation yet (clean future milestone), single-candidate per task in this matrix (multi-candidate path was exercised in earlier RC-3 dogfoods).

### Q6. "What's the next step?"

RC-4D portfolio packaging — README hero, ARCHITECTURE.md, INTERVIEW_STORY.md, optional Vercel previews + screenshots, optional GitHub PR creation hook. Then product direction (the AI Writing Naturalizer is the natural product target; Local Agent Dev Studio is the system that builds + maintains it).

---

## How to read the rest of these docs

- `02-architecture-walkthrough.md` — components and data flow with ASCII diagrams.
- `03-failure-cases.md` — real bugs, what they looked like, and how each got fixed. Best evidence the system has a working hardening loop.
- `04-demo-matrix-story.md` — narrative for telling the demo-matrix story in 5 / 10 / 30 minutes.

For raw evidence: `docs/EVALUATION.md` (matrix-level summary) and `docs/rc4c-demo-suite-report.md` (run-by-run details).
