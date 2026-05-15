# Local Agent Dev Studio — Interview Story

Three time budgets (30 sec / 1 min / 5 min) plus the most likely follow-up questions with concise answers. Pair this with [`docs/EVALUATION.md`](EVALUATION.md) (the dry evidence) and [`docs/interview/04-demo-matrix-story.md`](interview/04-demo-matrix-story.md) (the longer 30-minute walkthrough).

中文导读: 这一篇是面试逐字稿 — 30 秒 / 1 分钟 / 5 分钟三种节奏,常见问题对应的简短答案,以及怎么讲失败案例。

---

## The 30-second pitch

> "I built **Local Agent Dev Studio**, a local-first AI-native software delivery runtime. You write a `requirements.md` for a new project or a `change-request.md` for an existing one; it drives a real Codex agent through a deterministic pipeline — decompose, generate patches, score against twelve hard rules, apply through ten more, real `git commit`, schema-validated evidence trail. To prove it isn't a fixed template, I ran three different Next.js demos — an AI Writing Quality Editor, an AI Usage & Cost Planner, and an Agent Review Queue Console — each with both greenfield generation and a follow-up change request. **Three of three green.** Twelve real-Codex commits. Zero open review items. Full evidence is in `docs/EVALUATION.md`."

中文 30 秒版:

> "我做了一个本地优先的 AI 原生软件交付系统 Local Agent Dev Studio。你写一个 `requirements.md` 或 `change-request.md`,系统调真实 Codex agent 跑一条确定性流水线 — 分解任务、出补丁、用 12 条死规则打分、再用 10 条规则做安全应用、生成真实 git commit + 完整证据。为了证明它不是固定模板,我用三个不同方向的 Next.js demo 验证了从零生成 + 修改请求两条链路。**3/3 绿。** 总共 12 次真实 Codex commit,零 review 阻塞。完整证据在 `docs/EVALUATION.md`。"

Key beats: (1) what it is, (2) why it's not just a copilot, (3) the matrix result, (4) where the proof lives.

---

## The 1-minute pitch

> "Most AI coding agents today are at one of two extremes — IDE copilots like Cursor that leave you to accept every suggestion, or end-to-end auto-shipping bots like Devin that produce something live but un-reviewable. **Local Agent Dev Studio sits in the middle.** Autonomous enough to drive multi-task delivery without a human in the loop, but every decision is gated by deterministic Python rules and every artifact is schema-validated.
>
> Two entry points share the same interior: greenfield (`requirements.md` → autonomous run → per-task git commits) and Change Request Mode (`change-request.md` → 1-task run against an existing project → commit on `agentic/change/<id>` with a `Change-Id` trailer). Same machinery in both — same 12-rule Promotion Gate, same 10-rule Apply Gate, same artifact contract.
>
> I verified it with **three demos in different verticals** — an AI Writing Quality Editor (text quality + analyzer), an AI Usage & Cost Planner (token budget logic), and an Agent Review Queue Console (governance dashboard). Three real-Codex greenfield runs (9 task commits) plus three real change runs (3 change commits). Three of three green; zero blocking review items.
>
> The strongest evidence the system works isn't the happy path — it's the failure cases. Eight distinct failure modes have surfaced across the project, every one fixed and locked behind a regression test. The first real-Codex run on the demo matrix paused at task-001 because the parser captured backticks literally — the Promotion Gate refused the patch because `fnmatch('app/page.tsx', '\`app/**\`')` returned False. The fix was small (strip wrapping backticks defensively) plus a 6-test regression suite. **Real Codex behaved correctly; the bug was on the parser side.** That's the system working."

This version covers: positioning, the two-modes pattern, the three-demo proof, and the strongest concrete failure-case anecdote.

---

## The 5-minute pitch

> *(0:00 — 0:30)* "I built Local Agent Dev Studio over the last several development cycles. The short version: a local-first AI-native software delivery runtime that turns markdown intents into verified git commits with a complete schema-validated audit trail. Real Codex inside; deterministic Python around it.
>
> *(0:30 — 1:30)* "The positioning matters because the field is bimodal right now. On one end you have IDE copilots — great for one-off help, but they leave you to decide whether each suggestion is worth taking. On the other end you have end-to-end auto-shippers — they promise to build whole apps, but you can't review what happened because there's no audit trail. Local Agent Dev Studio sits in the middle. Autonomous enough to drive multi-task delivery, but every decision is gated by deterministic rules and every artifact is schema-validated. A reviewer can `git log --grep \"Change-Id:\"` and reconstruct exactly what happened and why. No black box.
>
> *(1:30 — 2:30)* "The flow is: operator writes `requirements.md` (or `change-request.md`); deterministic Python parser produces a task graph; the AutonomousController picks the next eligible task; the AgenticProjectRuntime calls real Codex inside a workspace-write sandbox to produce a candidate patch; the runtime computes a real `git diff --binary` and runs `npm run build` and `tsc --noEmit` against the patch in an ephemeral worktree; the **Promotion Gate** scores the candidate against 12 hard rules — things like `source_patch_present`, `diff_within_scope`, `patch_apply_check_passed`, `required_eval_passed`. If everything passes, the **Apply Gate** re-checks safety from the live-git side — base_commit equals current HEAD, worktree clean, `git apply --check` exits 0, no out-of-scope files. Then real `git apply`, real commit with provenance trailers, and `applied-change.json` + `delivery-report.md` get written.
>
> *(2:30 — 3:15)* "Two gates instead of one is intentional. Promotion Gate measures *what Codex produced*; Apply Gate measures *what the repo can safely accept right now*. They're different concerns. A candidate that scored 'promote' yesterday can fail Apply today if HEAD moved.
>
> *(3:15 — 4:00)* "To prove this isn't a fixed template, I built a 3-demo matrix. Three Next.js + TypeScript projects in three different verticals — an AI Writing Quality Editor (deterministic analyzer + suggestion list + clarity score), an AI Usage & Cost Planner (token-budget calculator + monthly summary + budget warning), and an Agent Review Queue Console (severity badges + status filter + SLA risk badges). Each demo got both greenfield generation from `requirements.md` AND a follow-up change request. Same machinery drove all three. **Three of three green** — twelve real-Codex commits across the matrix. Every change run cleared the same 6 of 12 hard gates that fire on a single-candidate change. Every Validation section in the delivery report shows real `eval.build / eval.typecheck / promotion / apply` rows. Zero blocking review items.
>
> *(4:00 — 4:45)* "The strongest evidence the system works is the failure case catalog. Eight distinct failure modes have surfaced across the project. The first real-Codex run on the demo matrix paused at task-001 — the changed-files report said `app/page.tsx` was out of scope even though the task's stated scope was `app/**`. I traced it: I'd written ``Scope: `app/**`, `components/**` `` because backticks render as code in markdown previewers; the parser captured the backticks literally; `fnmatch('app/page.tsx', '\`app/**\`')` returned False; the gate correctly refused the patch. **Real Codex behaved correctly. The bug was on the parser side.** Fix was 30 lines (strip wrapping backticks + support multi-line bullet form for `Scope:`). Six new regression tests. Same pattern for the other seven failure cases — Apply Gate refuses, evidence trail surfaces the why, fix is small, regression test locks it.
>
> *(4:45 — 5:00)* "Next steps: portfolio packaging is wrapping up; after that I'm going to use this Studio to build a real product on top of it — an AI Writing Naturalizer with the AI-likeness risk reduction angle. The Studio is the system; the Naturalizer is what it produces."

---

## Anticipated questions

### Q1. "Why not just use Claude Code / Codex directly? They can run for hours and build whole projects."

> "I do use Codex as the patch worker. The difference is that Local Agent Studio wraps it in a software delivery runtime. It turns requirements into task graphs, builds context packs, runs required validation, applies Promotion and Apply Gates, records artifacts, creates commits, and produces delivery reports. The goal is not to replace coding agents — it's to make their output controlled, auditable, and repeatable.
>
> Concretely: if you let Codex run unattended for hours you get a pile of code changes plus a model summary. With Studio you get a task graph, a per-candidate run package, a `changed-files.json`, an `eval-results.json`, a `promotion-report.json` with the gate breakdown, an `applied-change.json`, a `delivery-report.md`, and real git commits with `Agent-Task-ID` / `Change-Id` trailers you can `git log --grep` forever. **The model is not the system. The delivery loop is the system.**"

中文版:

> "我确实把 Codex 作为 patch worker 使用。区别是 Local Agent Studio 在外层加了一个软件交付运行时:把需求变成任务图,构建上下文,运行验证,通过 Promotion Gate 和 Apply Gate,记录 artifacts,创建 commit,并生成交付报告。我的目标不是替代 coding agent,而是让 coding agent 的输出可控、可审计、可重复。**模型不是系统,交付闭环才是系统。**"

### Q1.5 "Isn't this just Cursor / GitHub Copilot Workspace / Devin?"

> "Different point on the spectrum. Cursor is a copilot — you accept every suggestion. Devin is end-to-end auto — you can't review what happened. Local Agent Dev Studio is **deterministic gates plus complete evidence trail**. Every gate decision is checkable in real Python. Every artifact validates against a schema. The point isn't 'AI does everything' — it's 'AI does the keystrokes; deterministic logic decides what's safe to land.'"

### Q2. "What happens when Codex does something wrong?"

> "Three layers catch it. Promotion Gate refuses with a specific failed rule. Apply Gate refuses with the live-git reason. Post-apply integration (`npm run build` + `tsc --noEmit`) catches anything that compiled in isolation but breaks the cumulative tree. Anything refused emits a review item to the human-in-the-loop queue with the gate breakdown + suggested commands. Across the RC-4C matrix, zero review items were emitted."

### Q3. "Why deterministic Python in the gates instead of an LLM judge?"

> "If the gate logic itself were an LLM call, the audit trail would be circular — you'd need another LLM to check the first LLM. Hard rules in real Python mean a reviewer can read the rule, read the artifact, and verify the decision matches. This is the difference between 'AI did it' and 'AI did the keystrokes; deterministic logic decided what's safe.'"

### Q4. "How do you know it's not just memorizing the demo?"

> "Four reasons. First, the three demos cover three different verticals (text quality / cost calc / governance dashboard) with three different state shapes and three different change archetypes — one trained recipe wouldn't cover all three. Second, the decomposer is deterministic Python — same `requirements.md` always produces the same task-graph, no per-demo learning. Third, the Apply Gate is identical across demos; same 10 hard rules fired on every commit. Fourth, you can run a fourth demo by writing a fourth `requirements.md` plus `change-request.md` without touching any code."

### Q5. "Why did you build your own runtime instead of using LangGraph / CrewAI / etc.?"

> "Two reasons. First, deterministic gates need real code, not LLM-judged 'should we promote this?' calls. A Promotion Gate that says 'this patch is in scope' must use `fnmatch` against the actual diff, not a model rationale. Second, the artifact contract is the product. The Studio's value isn't 'an agent that codes' — every framework can do that. It's 'every decision leaves a schema-validated audit trail,' and that's easier to enforce as a single Python module than as a graph of LLM calls in someone else's framework."

### Q6. "What's NOT in the demo matrix?"

> "Honest list: no Vercel deploy in RC-4C — that's a noise/cost surface that doesn't help prove the change-mode works (the deploy adapter was exercised in earlier RC dogfoods). No backend / API / DB in the three demos — the Studio supports them, RC-3C and RC-3D dogfoods ran FastAPI + Prisma greenfield, but for the matrix I optimized for portfolio readability. No GitHub PR automation yet — clean future milestone; the `delivery-report.md` already has the right shape to become a PR description. Single-candidate per task in this matrix — `max_candidates_per_task: 1` for cost control; the multi-candidate path was exercised in earlier RC-3 dogfoods."

### Q7. "How long did this take?"

> "Multiple development cycles. The Studio runtime came together over RC-1 through RC-4. The 3-demo matrix specifically is RC-4B (matrix prep) → RC-4C (real Codex run) → RC-4C.1 (cleanup of the bugs the first real run surfaced). **Most of the time was hardening — building the gates and the artifact contract took substantially longer than building the agent loop itself.** The lesson: the audit trail is the product, not the agent."

### Q8. "What stack?"

> "Python 3.10+ for the orchestration layer. SQLite for the project / run / approval database. No external services beyond Codex CLI. The demos themselves are Next.js 15.5.18 + React 19 + TypeScript 5.7 with `localStorage` only — no backend in the matrix."

### Q9. "Show me a real failure surfaced in production."

Pull up [`docs/interview/03-failure-cases.md`](interview/03-failure-cases.md). Walk through Case 7 (the scope-backticks bug):

> "First real-Codex run on the matrix. Greenfield paused at task-001. Review queue had `review_2beda9738c` open with reason `failed-apply`. Codex had produced a patch — `npm run build` passed, `tsc --noEmit` passed, `patch_apply_check_passed=true`, `source_patch_present=true`. But the Promotion Gate refused with `diff_within_scope=false`. The changed-files report said `app/page.tsx within_scope=false` — even though task-001's scope was `app/**`. Reproduced in 4 lines of Python: `fnmatch('app/page.tsx', '\`app/**\`')` returns False because of the backticks. Real Codex behaved correctly. The fix was a 30-line `_clean_meta_value` helper that strips wrapping backticks defensively, plus regex relaxation to support a multi-line bullet form for `Scope:`. Six new regression tests. Next run was 3/3 green."

### Q10. "What would you do differently?"

> "Build the artifact contract first, the agent loop second. I built them in the other order and had to retrofit the schemas. The artifact-validation layer is what makes the audit trail trustworthy; everything else is in service of that."

### Q11. "What's the next milestone?"

> "RC-4D portfolio packaging is wrapping up — README hero, ARCHITECTURE.md, INTERVIEW_STORY.md, RESUME_BULLETS.md, PROJECT_STATUS.md. After that, **product direction**: I'm going to use Local Agent Dev Studio to build a real product — an AI Writing Naturalizer with the AI-likeness risk reduction + naturalization workflow angle. The Studio is the system; the Naturalizer is the case study of what the system can produce."

### Q12. "What's the most surprising thing you learned?"

> "The first real-Codex run on Demo 1 paused on a parser bug, not a Codex bug. **Real-world dogfood is non-negotiable.** The unit suite was 100% green; the integration was wrong. Every milestone in this project shipped at least one bug that only surfaced once the actual flow ran on a real project. Plan for it; budget time for it; treat the failure-case catalog as the strongest evidence the system works."

### Q13. "Why not just let Codex run for 24 hours unattended and see what it builds?"

> "Two reasons. First, who decides when it stops? Who decides whether it's done? Who decides if it went out of scope? Who decides if a deploy should happen? Studio answers each of those with a deterministic rule (budget caps, Apply Gate, Promotion Gate, deploy.enabled config). Second, what do you do with what it built? Twenty-four hours of unsupervised Codex usually leaves a tree state nobody can audit — no per-task commits, no scope evidence, no validation log. Studio's design point is **controlled overnight autonomy**: drive multiple tasks unattended, but stop at any of {gate failure, scope violation, security-sensitive change, missing API key, deploy decision, cost budget breach}. That's a more honest answer to 'can the agent run overnight?' than 'yes, just give it time.'"

### Q14. "What's the difference between 'using a model' and 'building a system around a model'?"

> "**The model is not the system. The delivery loop is the system.** A model writes code; a system decides what code is allowed to land, records why, and gives a reviewer everything they need to second-guess the decision. Studio is the loop: requirements ingestion, task decomposition, scope binding, candidate generation, eval execution, deterministic gates, real `git apply`, schema-validated artifacts, human-in-the-loop review queue, change-mode delivery report. Codex is one component of that loop — the patch worker. Replacing Codex with another agent (Claude, a future Codex, even a fine-tuned model) is a clean diff against `codex_patch_worker.py`. Replacing the loop with a different orchestration is a different project."

---

## How to talk about failure cases

Most interviewers will probe failure cases. The key reframe:

> "Failure cases are the strongest evidence the system works. A passing test suite proves no test broke. A failure that the system caught and surfaced cleanly proves the gates and the audit trail are doing their job."

Standard structure for any failure-case answer:

1. **Symptom** — what the operator saw (e.g. "review item opened, decision was `failed-apply`").
2. **Root cause** — one sentence on what was actually wrong.
3. **Where the gate caught it** — Promotion / Apply / integration / parser. This is the win.
4. **Fix** — small. With a regression test.
5. **What it proves** — the gate fired correctly even though the bug was real.

Example for Case 2 (corrupt difflib patch):

1. **Symptom.** Apply Gate's `git apply --check` returned 0 but the actual `git apply` failed silently or applied a malformed diff.
2. **Root cause.** Runtime was using Python's `difflib.unified_diff` to generate `patch.diff`. The output looks like a unified diff but is missing the `diff --git a/x b/x` header that `git apply` needs.
3. **Where the gate caught it.** The Apply Gate's `git apply` step. The `--check` was more lenient than the real apply on the same input.
4. **Fix.** Replaced difflib with real `git diff --binary --cached HEAD` from an ephemeral repo. Added a 12th hard rule to the Promotion Gate: `patch_apply_check_passed`, so `git apply --check` runs BEFORE the gate scores the candidate. Two regression tests + one evidence-grounded write-up at `docs/rc3e-corrupt-patch-finding.md`.
5. **What it proves.** The Apply Gate's redundant safety check caught what the simpler `--check` step missed; the fix added a rule that prevents the same class of bug from reaching the Apply Gate at all.

---

## What to leave behind after the conversation

1. **Repo URL.**
2. [`docs/EVALUATION.md`](EVALUATION.md) — matrix-level summary.
3. [`docs/rc4c-demo-suite-report.md`](rc4c-demo-suite-report.md) — per-demo evidence.
4. [`docs/interview/03-failure-cases.md`](interview/03-failure-cases.md) — the failure case catalog.
5. **Optional:** one workspace's `.agent/changes/<change_id>/` directory tarball — a tangible "here's the actual evidence trail."

Don't lead with code unless they ask. The artifacts and the evidence are the demo. Code is the implementation detail.

---

## Common stumbles to avoid

- **Don't claim the matrix used multi-candidate.** It used `max_candidates_per_task=1` for cost control. Be honest; the multi-candidate path is exercised in earlier RC-3 dogfoods, but the matrix is single-candidate.
- **Don't oversell the demos as "production apps."** They're tiny Next.js with `localStorage`. Their job is to be three different shapes for the matrix to span — not to be live products.
- **Don't claim "deterministic" without specifying which layer.** The decomposer is deterministic. The gates are deterministic. The patch generator (Codex itself) is NOT — it's a real LLM. Be precise.
- **Don't say "no LLM" anywhere.** There's a real Codex call per candidate. The deterministic part is the orchestration around it.
- **Don't oversell the limitations as "easy future work."** Be honest that GitHub PR automation, multi-tenant hosting, real-time progress UI, etc. are real work, not afternoon hacks.
