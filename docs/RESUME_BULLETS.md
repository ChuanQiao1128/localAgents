# Resume Bullets — Local Agent Dev Studio

Each bullet is **evidence-grounded** — every claim points back to an artifact in the repo, a commit hash, or a documented test. Pick the variant that matches the role; mix-and-match individual lines as needed.

中文导读: 每条 bullet 都对应仓库里的真实证据 (commit hash / test 文件 / 文档)。下面分了 3-bullet / 5-bullet 两种长度,以及 AI Engineer 和 Full-stack AI 两个方向的口径。

---

## 3-bullet version (concise — for résumé summary line, LinkedIn headline supplement)

- **Built Local Agent Dev Studio**, a local-first AI-native software delivery runtime in Python that drives Codex through a deterministic pipeline (decompose → patch → eval → 12-rule Promotion Gate → 10-rule Apply Gate → real `git commit` with provenance trailers + schema-validated `applied-change.json` and `delivery-report.md`).
- **Verified end-to-end on a 3-demo matrix** (AI Writing Quality Editor, AI Usage & Cost Planner, Agent Review Queue Console) — three different Next.js + TypeScript verticals, **3 of 3 green**, 12 real-Codex commits across the matrix, zero open review items, every change `npm run build` clean.
- **Hardened by 8 documented real failure cases**, every one fixed and locked behind a regression test (337 tests pass) — including a corrupt patch generator (`difflib` → real `git diff`) and a parser bug surfaced by the first real-Codex run that a 100%-passing unit suite missed.

## 5-bullet version (full — for the project section)

- **Designed and built Local Agent Dev Studio**, a local-first AI-native software delivery runtime (~10k lines Python; SQLite persistence; Codex CLI patch worker). Two entry points share one orchestration: greenfield (`requirements.md` → autonomous task graph → real Codex per task → committed code) and Change Request Mode (`change-request.md` → 1-task synthesis → patch → commit on `agentic/change/<id>` with `Change-Id` git trailer).
- **Engineered a two-gate safe-apply pipeline.** A 12-rule **Promotion Gate** (`source_patch_present`, `diff_within_scope`, `patch_apply_check_passed`, `required_eval_passed`, etc.) scores the candidate from the run-package side; a 10-rule **Apply Gate** re-checks safety from the live-git side (HEAD = base_commit, worktree clean, `git apply --check` exits 0, no out-of-scope mutations) before any real `git apply`. Two gates because they measure different things; documented in `docs/ARCHITECTURE.md`.
- **Verified the system across 3 distinct Next.js + TypeScript verticals.** AI Writing Quality Editor (deterministic analyzer + clarity score), AI Usage & Cost Planner (token-budget calc + budget warning), Agent Review Queue Console (severity badges + SLA risk). Same machinery, no per-demo special-case code. **3/3 green, 12 real-Codex commits, 0 open review items**, every change passes `npm run build` and `tsc --noEmit`. Evidence: `docs/EVALUATION.md` + `docs/rc4c-demo-suite-report.md`.
- **Authored 8 schema validators** for every load-bearing artifact (`agentic.change_contract.v1`, `agentic.applied_change.v1`, `agentic.promotion_report.v2`, `agentic.applied_candidate.v1`, plus autonomous-session / task-graph / review-item / integration-failure / deployment / smoke-check / rollback / final-run-status). Two CLIs (`agent-studio change validate` and `agent-studio autonomous validate-artifacts`) walk a directory and return `ok=true/false` per artifact — every change run in the matrix passed.
- **Surfaced and fixed 8 distinct failure modes** in real-Codex dogfood runs, each locked behind a regression test (337 tests pass). Cases include: a `tsconfig.tsbuildinfo` build by-product captured as an out-of-scope edit (Promotion Gate refused; fix: discover-files filter); a corrupt `difflib`-generated `patch.diff` that passed `git apply --check` but failed real apply (fix: replace difflib with real `git diff --binary` from an ephemeral repo, plus a 12th hard gate `patch_apply_check_passed`); a parser that captured wrapping backticks literally and broke `fnmatch`-based scope checks (fix: defensive backtick stripping + multi-line bullet form; 6 regression tests). Documented in `docs/interview/03-failure-cases.md`.

---

## AI Engineer slant (focus: agent loop, gates, evals, real-LLM integration)

- **Built an evidence-grounded autonomous coding agent runtime** (Python + SQLite + Codex CLI). Multi-candidate inner loop runs Codex in `workspace-write` sandbox with `on-request` approval, then scores each candidate against a deterministic 12-rule Promotion Gate before a 10-rule Apply Gate enforces safe `git apply`. Real eval harness runs `npm run build` / `tsc --noEmit` / project-defined commands per candidate in an ephemeral worktree.
- **Closed-loop memory + repair.** Per-run `memory-update.proposed.json` is aggregated across runs into a `prior_learnings` block in the next task's context pack, so the agent doesn't repeat solved bugs. The repair loop re-prompts Codex with the failed eval output, capped by `max_repair_attempts_per_candidate`. Abandonment history is a soft signal in the Promotion Gate's score (recent abandonments lower a candidate).
- **Hardened the patch path against real failures.** RC-3E surfaced that `difflib.unified_diff` produces output that `git apply --check` accepts but real `git apply` can't reliably handle; replaced with real `git diff --binary --cached HEAD` from an ephemeral git repo, plus added `patch_apply_check_passed` as a 12th deterministic gate so corrupt patches are caught before they reach Apply.
- **Verified the agent loop on 3 different product surfaces.** Three Next.js + TypeScript demos (text quality / cost calc / governance dashboard); 12 real-Codex commits across the matrix; every Promotion Gate fired with `hard_gates=6/6 passed` on single-candidate runs; every change `npm run build` clean. Documentation + per-demo evidence in `docs/EVALUATION.md`.
- **Designed the human-in-the-loop layer** (`review_queue.py`). 5 source types (`task_run`, `apply_failure`, `needs_more_context`, `corrective_limit`, `deployment_failure`); blocking/warning/info severity; CLI `agent-studio autonomous reviews list / show / approve --yes / reject --reason / resolve --note`. The approve-with-override path re-runs the Apply Gate with `human_override=True` (only the "decision must be promote" rule is bypassed; every safety gate still runs) and records the override as a commit trailer for audit.

## Full-stack AI slant (focus: end-to-end shipping, multi-vertical demos, dev experience)

- **Shipped Local Agent Dev Studio**, a local-first AI delivery runtime that turns markdown intents into verified git commits. Single CLI (`agent-studio`), real Codex inside, deterministic Python around it. Two complementary modes: greenfield generation from `requirements.md` and change requests on existing repos via `change-request.md`.
- **Built and verified a 3-vertical demo matrix end-to-end.** AI Writing Quality Editor (deterministic analyzer + 0-100 clarity score), AI Usage & Cost Planner (token-budget calc + over-budget warning + share %), Agent Review Queue Console (severity badges + status filter + SLA risk badges). Each demo: greenfield `requirements.md` → 3 task commits → change-request.md → 1 change commit on `agentic/change/<id>`. **3/3 green, 12 real-Codex commits, 0 open review items.**
- **Authored every artifact contract** (8 JSON schemas + 3 markdown schemas) and the validator CLI surface. `agent-studio change validate` walks the change-dir; `agent-studio autonomous validate-artifacts` walks the session dir. Every commit carries provenance trailers (`Agent-Task-ID`, `Agent-Run-ID`, `Selected-Candidate`, `Promotion-Decision`, `Change-Id`, `Source-Change-Request`) — `git log --grep` works forever.
- **Designed the developer experience for portfolio review.** `scripts/run_demo_suite.sh` runs the full 3-demo matrix in dry-run-by-default mode; `--demo=<name>` filter for single demos; per-demo workspace isolation under `/tmp/rc4b-<demo>`; cross-demo evidence roll-up at the end. The runner gates change-mode on greenfield completion (no half-built scaffolds get fed to change run). Honest cost / blast-radius warning printed at the top of every run.
- **Catalogued and fixed 8 real failure cases** in `docs/interview/03-failure-cases.md`, each with symptom / root cause / fix / regression test. Patterns include schema-mismatch bugs (consumer read `commands_run`, producer wrote `commands`), parser fragility (backticks captured literally), and orchestration gaps (runner ran change-mode against a paused greenfield). All 337 unit + e2e tests pass after the cleanup.

---

## Single-line variants (for résumé skill list / project tagline)

- **Local Agent Dev Studio** — AI-native software delivery runtime; deterministic gates + real Codex; 3/3 green on a 3-vertical demo matrix.
- **Local Agent Dev Studio** — Built a Codex-driven autonomous coding agent with 12-rule Promotion + 10-rule Apply Gate; verified end-to-end on 3 different Next.js demos.
- **Local Agent Dev Studio** — Local-first AI delivery system that turns `requirements.md` into verified git commits with full schema-validated audit trail.

---

## Numbers you can quote

| Claim | Source |
|-------|--------|
| 12 real-Codex commits across the demo matrix | `docs/EVALUATION.md` per-demo table |
| 3 of 3 demos green | `docs/EVALUATION.md` summary |
| 0 open review items in any demo | `docs/rc4c-demo-suite-report.md` per-demo Validation rows |
| 12-rule Promotion Gate + 10-rule Apply Gate | `docs/ARCHITECTURE.md` §6 + §7 |
| 8 distinct failure modes documented + fixed | `docs/interview/03-failure-cases.md` summary table |
| 337 tests pass (unit + e2e) | `README.md` Tests section |
| 8 JSON schemas + 3 markdown schemas validated | `orchestrator/core/artifact_validation.py` |
| `agentic.change_contract.v1`, `agentic.applied_change.v1`, `agentic.promotion_report.v2` (etc.) | `orchestrator/core/artifact_validation.py` |
| RC-1 → RC-4D milestone history | `docs/PROJECT_STATUS.md` |

---

## What to NOT claim

- Don't say "production-grade SaaS" — it's a local-first runtime, not a hosted product.
- Don't say "fully autonomous, no human needed" — the human-in-the-loop review queue is a load-bearing part of the design.
- Don't say "deterministic AI" — Codex itself is non-deterministic; the orchestration around it is.
- Don't say "no LLM judges" — there are critic-panel markdown reports per candidate that are LLM-generated; the GATES are deterministic but the criticism layer is not.
- Don't claim multi-candidate in the demo matrix — `max_candidates_per_task: 1` for cost control. The multi-candidate path was exercised in earlier RC-3 dogfoods.
- Don't quote token counts beyond ~150-250k total for the matrix (estimated, not measured precisely; the actual numbers are in the per-demo session logs).

---

## Recruiter-friendly elevator (1 sentence)

> "Built Local Agent Dev Studio, a local-first AI-native software delivery runtime — turns markdown intents into verified git commits via deterministic 12-rule Promotion + 10-rule Apply Gates around a real Codex agent; verified end-to-end on a 3-vertical Next.js demo matrix (3 of 3 green, 12 real-Codex commits, 0 open reviews)."
