# Local Agent Dev Studio

Local Agent Dev Studio is a local-first AI-native development runtime.

It is not primarily a simulated software company. The human-facing PM, Design, Development, QA, and Review views remain useful for communication, but the execution core is the traceable relationship between intent, context, executable evaluations, candidate patches, runtime feedback, critic findings, promotion decisions, and proposed memory updates.

The new `agentic_project` workflow is a traceable, single-candidate MVP of that runtime:

```text
Intent Contract
→ Context Pack
→ Executable Spec / Eval Harness
→ Task Slices
→ Candidate Patch Record
→ Run-Observe-Repair Envelope
→ Critic Panel
→ Promotion Gate
→ Memory Update Proposal
```

The current MVP focuses on the deterministic foundation:

- CLI project creation
- SQLite persistence
- workflow phase state machine
- task board records
- artifact and event logs
- approval gates
- retry from blocked phases
- stub model router and agent runtime
- cost tracking
- path locks, worktree helper, safe file/shell/git tools
- deterministic PM, Architect, Developer, QA, and Reviewer agent shells
- generated-task driven static web MVP output for supported domains
- local markdown memory store
- Next.js dashboard shell

Run locally:

```bash
./agent-studio init
./agent-studio new "做一个个人记账 web app，支持收入支出、分类、月度统计"
./agent-studio run software_project
./agent-studio run agentic_project
./agent-studio status
./agent-studio approve prd
python3 -m unittest discover -s tests
```

## Quickstart — autonomous SDLC happy path

For end-to-end driven runs, the canonical entry is `requirements.md → autonomous start → walk away → come back to a final report`. Every persisted artifact along the way is validated by the RC-1 golden-path test suite (`tests/e2e/test_golden_path.py`), so this flow is reproducible without real Codex / Vercel / HTTP.

```bash
# 1. one-time setup
./agent-studio init

# 2. write your requirements.md (H1 = project title, each H2 = one task,
#    bullets = acceptance criteria, optional `Depends: <task title>`,
#    `Scope: src/api/**`, `Risk: low|medium|high`)
cp tests/fixtures/autonomous_golden_project/requirements.md .

# 3. ingest — produces prd.md, task-graph.json, acceptance-criteria.json,
#    architecture.md (deterministic parser, no LLM)
./agent-studio new --from requirements.md

# 4. enable deploy + smoke (optional — both default to off / opt-in
#    rollback). Add a deploy block to <project>/agent-studio.yaml; see the
#    MVP-4E / MVP-4F sections lower in this README for the full schema.

# 5. drive the controller
./agent-studio autonomous start

# 6. inspect at any point
./agent-studio autonomous status
./agent-studio autonomous logs --tail 100
./agent-studio autonomous preflight                # cheap pre-flight check (RC-2B)
./agent-studio autonomous validate-artifacts       # cross-walk every persisted artifact (RC-1)
```

Optional `agent-studio.yaml` knobs the controller reads at session creation (RC-2C; defaults are conservative — `autonomous:` budgets default to `DEFAULT_BUDGETS`, `integration:` to `every_n_tasks=3 / run_at_session_end=true / timeout_sec=600`). Existing on-disk sessions are NOT migrated — the override merge only happens when `start_or_resume` creates a fresh session:

```yaml
autonomous:
  budgets:
    max_tasks_per_session: 5
    max_total_inner_runs: 8
    max_corrective_tasks: 2
    max_abandoned_tasks: 1
    max_needs_human_review_tasks: 1

integration:
  every_n_tasks: 1                # 1 = run after every task; 3 = every 3rd; 0 = disable
  run_at_session_end: true
  timeout_sec: 120
```

When the run finishes, every artifact lives under `<project>/.agent/autonomous/sessions/<session_id>/`:

- `final-run-status.md` — the page to open first. Sections: `Summary · Tasks · Integration · Corrective Tasks · Deployment · Smoke Checks · Rollback · Human Review Queue · Evidence Trail · Next Actions`. The Next Actions block prints the exact CLI command to run next based on session state.
- `autonomous-session.json` — session counters, deployment state, pause reason
- `controller-log.jsonl` — every controller event (task_started, candidate_selected, deployment_started, smoke_check_completed, etc.)
- `review-items/<id>.json` — every paused-for-human decision (one file per pause)
- `deployments/<id>/deployment.json` — Vercel deploy artifact (token redacted)
- `smoke-checks/<id>/smoke-check.json` — HTTP check results (headers redacted)
- `rollbacks/<id>/rollback.json` — only present if rollback ran
- `integration-failures/<id>/integration-failure.json` — structured integration failure used to inject corrective tasks

If something paused, the queue tells you what to do:

```bash
./agent-studio autonomous reviews list
./agent-studio autonomous reviews show <review_id>
./agent-studio autonomous reviews approve <review_id> --yes      # human override apply
./agent-studio autonomous reviews reject <review_id> --reason "..."
./agent-studio autonomous reviews resolve <review_id> --note "manually fixed"
./agent-studio autonomous resume                                  # blocked while any review is open
```

### Opting into the Codex patch worker (RC-2B)

The autonomous controller defaults to `patch_worker: none` for safety — every task pauses on `needs-human-review` until you wire a real coding worker. To use the OpenAI Codex CLI as the inner patch worker, install it (`npm i -g @openai/codex`) and add an `agentic:` block to your project's `agent-studio.yaml`:

```yaml
agentic:
  patch_worker: codex
  codex:
    command: codex                  # or absolute path
    sandbox: workspace-write        # also allowed: read-only, read
    ask_for_approval: on-request    # also allowed: untrusted, never
    timeout_sec: 600
    max_prompt_chars: 60000
```

Safety invariants the autonomous patch worker enforces — these cannot be configured around:

- `--yolo` / `--dangerously-bypass-approvals-and-sandbox` / `danger-full-access` are on a hard forbid-list. The pure command builder (`build_codex_patch_worker_command`) raises `ValueError` BEFORE any subprocess fork; the runtime maps that to a `codex_command_refused` failure record.
- The sandbox value must be one of `{workspace-write, read-only, read}` — anything else raises before fork.
- The approval value must be one of `{on-request, untrusted, never}` — anything else raises before fork.
- A preflight runs `shutil.which(<codex_command>)` BEFORE the first subprocess so missing-CLI shows up as a clean `codex_cli_not_found` review item with `details.looked_for: <name>` instead of an opaque "no diff produced".

Verify the wiring:

```bash
agent-studio autonomous preflight              # see RC-2B.11 below
agent-studio autonomous status --json          # `agentic.patch_worker` will appear
agent-studio autonomous validate-artifacts     # ok=true after a clean run
```

For deploy / smoke / rollback the manual entry points are:

```bash
./agent-studio autonomous deploy --dry-run                        # print sanitized vercel cmds
./agent-studio autonomous deploy --yes [--prod | --preview] [--prebuilt]
./agent-studio autonomous smoke --url https://app.vercel.app/     # one-shot
./agent-studio autonomous smoke --rollback-on-failure --yes       # smoke + auto-rollback (prod only)
./agent-studio autonomous rollback --dry-run
./agent-studio autonomous rollback --yes
```

Programmatic artifact validation lives in `orchestrator/core/artifact_validation.py` (`validate_session_directory(sess_dir)` returns `dict[path → list[error_str]]`; empty values mean valid). The golden-path e2e test calls this after every run as a drift detector.

`software_project` is the human-readable SDLC workflow. `agentic_project` is the AI-native runtime workflow. It writes a complete evidence package under `.agent/runs/<run-id>/`, including `intent-contract.json`, `context-pack.json`, `eval-harness.json`, `task-slices.json`, `candidates/candidate-a/*`, `critics/*.md`, `promotion-report.json`, `trace.jsonl`, and `memory-update.proposed.json`.

MVP-1.5 patch/eval mode is explicit:

```bash
./agent-studio run agentic_project \
  --agentic-patch-worker codex \
  --agentic-execute-eval
```

Without `--agentic-patch-worker codex`, the run is classified as a successful runtime-only evidence run. Promotion Gate will keep `source_patch_present=false` and return `needs-human-review`; it will not pretend implementation happened.

MVP-2 repair mode is also explicit. It keeps the same candidate workspace, classifies required eval failures, asks a repair-agent to patch, reruns eval, and records each loop in `candidates/candidate-a/repair-history.json`:

```bash
./agent-studio run agentic_project \
  --agentic-patch-worker codex \
  --agentic-execute-eval \
  --agentic-repair-loops 3 \
  --agentic-candidate-count 3
```

If repair loops are exhausted (max loops reached, same failure type 3x in a row, or the repair worker itself errored), Promotion Gate returns the dedicated `abandoned` decision instead of `needs-human-review`. The reason — drawn from the canonical taxonomy in `orchestrator/core/agentic_runtime.py::FAILURE_TAXONOMY` — is exposed two ways:

- Per-run: `promotion-report.json -> repair.abandoned` (`true`/`false`) and `repair.abandonment_reason` (verbatim `stop_reason`).
- Cross-run: a JSONL line is appended to `<project>/.agent/agentic-abandonments.jsonl` with `{run_id, timestamp_utc, intent_goal, candidate, decision, patch_worker, stop_reason, attempt_count, max_loops, final_failure}`. Future worker-selection logic can read this log to bias against repeatedly-failing categories on a given project.

`abandoned` is reserved for "we tried to repair and gave up"; `needs-human-review` is still used when the run never had a patch to repair (no `source_patch_present`, eval not declared, etc.).

The Promotion Gate also reads the abandonment log as a soft signal. Every run reports `promotion-report.json -> abandonment_pattern` with `{patch_worker, failure_type, prior_abandonments, warning_emitted}`. When the current run hits a failure type that the same `patch_worker` has already been abandoned on for this project ≥2 times, a warning is appended to `remaining_risks` ("`codex` has been abandoned N prior time(s) on this project for failure_type `type_error`; consider switching workers, expanding eval coverage, or revisiting intent scope before another repair attempt."). The signal does not gate the decision — it is meant to surface a pattern before another repair cycle is spent on it.

Memory loop closure: as of schema `agentic.memory_update_proposal.v2`, `memory-update.proposed.json` is derived from this run's evidence (failure types observed, promotion decision, out-of-scope writes, declared-but-unexecuted eval commands, abandonment-pattern echoes, promote successes) instead of static MVP-1 meta-text. The next agentic run on the same project reads every prior `memory-update.proposed.json` (excluding the current run, defensive against corrupt JSON), dedupes learnings by pattern text, and surfaces the top 10 in `context_pack.prior_learnings` (`{pattern, occurrences, max_confidence, last_seen_run, last_seen_at, last_evidence}`) with a `prior_run_count` field. Codex sees these naturally when it reads the context pack. Status remains `proposed_only` — nothing is written to long-term memory; the loop is a per-project working-memory channel.

Critic Panel reports under `critics/*.md` are derived from this run's evidence rather than fixed templates: each critic reads `candidate.patch_diff`, `candidate.changed_files`, `candidate.score`, `repair_history`, and `eval_results`, and cites specific paths, command names, failure types, and repair outcomes. The Security Critic flags sensitive-path heuristics (env, deps, migrations, auth, scripts, Dockerfile) on touched files. The Overfit Critic combines test-file edits with repair history to highlight test-gaming risk. No LLM call is made; critics are pure derivations of the artifact envelope.

`repair_history.final_failure` is now guaranteed to be present on every terminal `stop_reason` — `None` when there is no failure to record (no source patch, eval skipped, eval passed) or a classified failure dict otherwise. Downstream consumers can read it unconditionally.

MVP-3A (Sequential Multi-Candidate Selection): each `agentic_project` run now generates `--agentic-candidate-count N` candidates sequentially (default N=3). The strategies are fixed (`candidate-a: conservative`, `candidate-b: test-focused`, `candidate-c: broader-fix`) and embedded in patch-worker prompts; the deterministic Promotion Gate scorer does not trust strategy labels — selection is purely on observed evidence. Each candidate gets an isolated worktree, its own `candidates/<id>/{patch.diff, changed-files.json, score.json, repair-history.json, eval-results.json, critics/*.md}`, and a single-candidate failure does not abort sibling candidates. Promotion-report schema bumps to v2 and gains `candidates[]`, `selected_candidate`, `candidate_count`, and a real `candidate_diversity` block (Jaccard distance over changed-file sets, with per-pair detail). Selection: hard gates (source_patch_present / required_eval_executed / required_eval_passed / no_out_of_scope_changes / no_critical_security_finding) disqualify candidates; eligible candidates earn deterministic soft scores (required_eval +40, optional_eval +15, repair_stability +15, scope_safety +10, critic_risk +10, test_relevance +5, context_alignment +5) minus penalties (repeated_failure_type −10, docs_only_patch −10, test_only_patch −15 unless intent is test-focused); `max(score.total)` wins. Abandonment recording now distinguishes `event_type: "candidate_abandoned"` (one candidate's repair exhausted) from `event_type: "run_abandoned"` (every candidate failed); the CLI list output includes both `event` and `candidate` columns. Memory updates derive winner/loser comparison patterns when ≥2 candidates run.

MVP-3B (Selected Candidate Handoff & Safe Apply): once a run picks a winner, the CLI lets a human inspect, dry-run, and safely apply the patch.

```bash
./agent-studio agentic-candidates list   [--project <id>] [--run <run-id>] [--json]
./agent-studio agentic-candidates show   [--project <id>] [--run <run-id>] --candidate <id|selected> [--json]
./agent-studio agentic-candidates apply  [--project <id>] [--run <run-id>] --candidate <id|selected> (--dry-run | --yes)
```

`apply` is gated by 10 hard rules (any failure refuses the apply): (1) `promotion-report.schema_version == agentic.promotion_report.v2`; (2) `selected_candidate` present; (3) candidate `patch.diff` exists and is non-empty; (4) `changed-files.json` present; (5) `score.source_patch_present == true`; (6) candidate has no `out_of_scope_changes`; (7) current repo HEAD short-hash equals `changed-files.base_commit`; (8) working tree is clean (changes under `.agent/` are ignored); (9) `git apply --check` passes; (10) `promotion-report.decision == "promote"`. `--dry-run` runs every gate but never writes to the working tree; `--yes` runs every gate and then `git apply`s the patch. On successful `--yes` apply the runtime writes `<run_dir>/applied-candidate.json` (`{schema_version: 1, run_id, candidate, strategy, decision_at_apply_time, project_id, base_commit, applied_to_commit, patch_sha256, dry_run: false, applied: true, changed_files: [...], timestamp_utc}`). The CLI never auto-commits — the user runs `git status` / `git diff` / `git commit` themselves. `decision != "promote"` runs (e.g. `needs-human-review`, `repair`, `abandoned`) are inspectable via `list` and `show` but cannot be applied; this keeps the `apply` command a strict promotion-gate executor rather than an override tool.

Read-side helper: `orchestrator/core/run_package.py` exposes `RunPackage` / `CandidateReport` / `ProjectRunPackages`. CLI commands and future tooling (dashboard, memory replay) read run artifacts through this helper rather than parsing JSON inline.

MVP-3C (Run Inspection & Hardening): two new read-side commands and two safety nets.

```bash
./agent-studio agentic-runs list   [--project <id>] [--json]
./agent-studio agentic-runs show   [--project <id>] --run <run-id> [--json]
```

`agentic-runs list` enumerates every run for a project (most-recent first) with `run_id / created_utc / decision / selected_candidate / applied? / candidate_count`. `agentic-runs show <run-id>` dumps a one-screen run summary: intent goal, context_quality, prior_run_count, prior_learnings count, every candidate's score / eval / repair, abandonment events for the run, decision, and apply state. Pure read, no behavior change.

Schema validation: `_validate_promotion_report_v2` / `_validate_candidate_score` / `_validate_changed_files` are pure functions returning a list of error strings. `_build_promotion_report` calls `_assert_valid_promotion_report_v2` before returning so a malformed report fails loud at the producer rather than silently corrupting the run package.

Re-apply guard (Apply Gate rule 11): when `<run_dir>/applied-candidate.json` already exists, `apply --yes` refuses with the message "this run has already been applied; create a new agentic_project run before re-applying". `--dry-run` is still allowed because it is pure inspection.

MVP-4A (Resumable Autonomous Controller): the outer loop. `agentic_project` becomes the inner execution unit; `autonomous` is the user-facing controller that drives a project from PRD to per-task commits.

```bash
agent-studio new --from requirements.md       # ingest PRD → prd.md / acceptance-criteria.json / architecture.md / task-graph.json
agent-studio autonomous start                 # run the controller loop
agent-studio autonomous status                # session state, task counts, budget usage
agent-studio autonomous logs --tail           # tail controller-log.jsonl
agent-studio autonomous halt                  # cooperative halt (pauses after current task)
agent-studio autonomous resume                # continue a paused session
```

Requirements parsing is deterministic — no LLM. The first H1 becomes the project title, every H2 section becomes a task, and within each section: bullet `- ...` lines become `acceptance_criteria`, `Depends:` and `Scope:` lines (plus `Risk: low|medium|high`) configure the task. `task-graph.json` is the binding contract.

Controller behavior per task:
- pick next dependency-satisfied pending task
- spawn an `agentic_project` inner run with the task's intent / acceptance / scope_paths injected via `intent_overrides`
- on `decision == promote`: run the Apply Gate, `git apply` the winner, commit on the session branch with evidence trailers (`Agent-Task-ID / Agent-Run-ID / Selected-Candidate / Candidate-Strategy / Promotion-Decision / Promotion-Report`), mark task `completed`
- on `decision == needs-human-review`: pause the session
- on `decision == abandoned`: count it; pause when the abandonment threshold is reached
- after every task: refresh `final-run-status.md`

Default budgets (configurable per session): `max_tasks_per_session=20`, `max_abandoned_tasks=2`, `max_needs_human_review_tasks=1`, `max_total_inner_runs=30`. The controller pauses on the first breach.

Git policy:
- A session creates branch `agentic/autonomous/<session_id>` and stays on it.
- One commit per task, with evidence trailers.
- No auto-push. No PR creation. The user pushes / merges to main themselves.

Out of scope for MVP-4A (deferred to MVP-4B/C/D/E/F): integration phase, corrective task injection, human-review queue UX, deploy adapter (Vercel first), smoke check, rollback, parallel task execution. The `architecture.md` produced today is intentionally lightweight (repo / framework / scripts detection, no LLM-generated architectural reasoning).

Resumability is on disk: `.agent/autonomous/sessions/<session_id>/{autonomous-session.json, controller-log.jsonl, final-run-status.md}`. Killing the process and restarting picks up from the last incomplete task. `task-graph.json` lives at project root and is treated as controller-owned (the worktree-clean preflight ignores it).

MVP-4B (Integration Phase): per-task agentic_project runs prove a candidate's required eval passes IN ISOLATION inside its worktree. After multiple commits land on the session branch, the cumulative project state could still be broken (task A passed alone but conflicts with task B). The integration phase catches this.

```bash
agent-studio autonomous integrate          # manual integration check (does not advance tasks)
```

Behavior:
- Periodic: after every `integration_policy.every_n_tasks` (default 3) successful task commits, the controller reuses `_build_eval_harness` to derive integration commands and runs them against the actual project working tree.
- At session end: if `integration_policy.run_at_session_end` (default `true`) AND at least one task was completed since session start, a final integration runs before the session is marked `completed`.
- Pass: `integrations_passed` counter increments; controller continues.
- Fail: `integrations_failed` counter increments, the session is paused with `pause_reason: "integration_failed"`, and a human-readable `integration-failure-summary.md` is written next to `controller-log.jsonl`.
- All results are appended to `<session_dir>/integration-results.jsonl` (one record per check, newest at end).

The controller does NOT yet generate corrective tasks on integration failure (that is MVP-4C). For now, you fix the cumulative state manually (commit the fix on the session branch) and resume.

Configuration in `autonomous-session.json`:

```
"integration_policy": {
  "every_n_tasks": 3,
  "run_at_session_end": true,
  "timeout_sec": 600
}
```

Setting `every_n_tasks: 0` disables periodic integration (only the session-end check runs).

MVP-4C (Corrective Task Injection): integration failure no longer pauses immediately; the controller turns it into a self-healing loop.

```
integration check fails
  → write structured integration-failure.json (per failure_id, under .agent/autonomous/sessions/<sid>/integration-failures/<failure_id>/)
  → classify failure type (build_failure / type_error / unit_test_failure / e2e_failure / unknown) from failed command
  → extract suspected files via regex on stderr/stdout tails (best-effort)
  → check duplicate guard:  identical (failed_command, after_task_id, failure_type) with pending/running corrective?
        if yes → log skipped, do not re-inject, session continues (existing corrective will handle it)
  → check budget (max_corrective_tasks, default 3)
        if exhausted → pause with reason "too-many-corrective-tasks"
  → otherwise build a bounded corrective task (id task-fix-integration-NNN, intent + acceptance referencing the failed command,
        scope_paths from repo detection, dependency on after_task_id, source/source_failure_id/corrective=true) and append to task-graph.json
  → controller continues; scheduler picks the corrective task FIRST (corrective tasks beat normal pending tasks)
  → corrective task runs through inner agentic_project loop; on promote, applies + commits with extra trailers:
        Corrective-Task: true
        Source-Failure-ID: integration_failure_xxx
  → IMMEDIATELY after a corrective commit, integration is re-run (post_corrective trigger, not waiting for the periodic every_n)
        → pass: continue normal task graph
        → fail again: try another corrective (if budget allows) or pause
  → if the corrective task itself goes needs-human-review / abandoned / apply-failed → session pauses; final-run-status.md
        flags it as "blocked by corrective task <id>"
```

Per-session limits + counters live in `autonomous-session.json`:

```
"budgets": { "max_corrective_tasks": 3, ... }
"counters": {
  "corrective_tasks_created": 0,
  "corrective_tasks_completed": 0,
  ...
}
```

Decision deliberately NOT done here: the corrective-task BUILDER is fully deterministic (no LLM call). It records the failed command verbatim and asks the inner agentic_project loop to fix it; the inner loop is where multi-candidate strategies, scoring, critics, and Promotion Gate already do their work. MVP-4C only changes WHO triggers the inner loop, not what the inner loop does.

`autonomous status` now prints a `Corrective tasks:` block with created / completed counts + max budget + pending/running list. `final-run-status.md` gains two sections: `Corrective Tasks` (every corrective with status + source_failure_id) and `Integration Failures` (every recorded failure with trigger / after_task / detected_failure_type).

MVP-4D (Human Review Queue): every autonomous pause that needs a human decision becomes a structured review item — no more "session paused, dig through logs". Five trigger reasons map to review items:

```
needs-human-review              ← inner loop's promotion decision
needs-more-context              ← inner loop's promotion decision
failed-apply                    ← Apply Gate refused selected candidate
corrective-task-needs-review    ← (above three when the task is a corrective task)
too-many-corrective-tasks       ← session-level corrective budget exhausted
```

```bash
agent-studio autonomous reviews list                              # default: only open
agent-studio autonomous reviews list --all --json
agent-studio autonomous reviews show <review_id>                  # full evidence + suggested commands
agent-studio autonomous reviews approve <review_id> --yes         # human override + safe apply + commit trailers
agent-studio autonomous reviews reject <review_id> --reason "..." # task → blocked (human_rejected)
agent-studio autonomous reviews resolve <review_id> --note "..." [--mark-task pending|completed|blocked]
```

Each review item is its own JSON file at `<session>/review-items/<review_id>.json` with: status (open|approved|rejected|resolved), severity (blocking|warning|info), source_type, reason_code, title, summary, task_id / run_id / candidate_id / promotion_decision, source_failure_id, evidence_paths (real file paths to inspect), suggested_commands, allowed_actions, created_at, updated_at, resolution.

Approve is a HUMAN OVERRIDE, not a relaxation of the Promotion Gate. The promotion-report.json keeps decision=needs-human-review/needs-more-context. When approving a review tied to a candidate patch, the Apply Gate runs in `human_override=True` mode, which bypasses ONLY the "decision must be promote" check. Every other safety gate still applies (patch.diff non-empty, source_patch_present, no out_of_scope, HEAD == base_commit, worktree clean, `git apply --check`, re-apply guard). On success the commit gets extra trailers:

```
Human-Review-ID: review_xxx
Human-Review-Decision: approved
Human-Review-Override: true
```

`applied-candidate.json` records `human_override: true` so audit can distinguish gate-promote-applies from human-override-applies.

Resume gating: `autonomous start` / `autonomous resume` refuse to advance when the session has any blocking open review item. The CLI lists each blocking review with its `review_id` and points at the four resolution commands. There is intentionally no `--ignore-reviews` flag — the whole point of the queue is that the controller doesn't silently retry states that need human judgment.

`autonomous status` adds a `Review queue:` block (open / blocking counts + latest items). `final-run-status.md` gains a `## Human Review Queue` section grouped by status (Open / Approved / Rejected / Resolved). New controller-log events: `review_item_created / review_item_approved / review_item_rejected / review_item_resolved / resume_blocked_by_open_reviews`.

MVP-4E (Vercel Deploy Adapter): once a session reaches "no more eligible tasks + final integration passed", an optional deploy step runs and produces a structured deployment artifact. Failures route through the same Human Review Queue as every other pause.

```bash
agent-studio autonomous deploy --dry-run                  # print sanitized commands
agent-studio autonomous deploy --yes                      # real deploy + write deployment.json
agent-studio autonomous deploy --yes --prod               # production override
agent-studio autonomous deploy --yes --preview            # preview override
agent-studio autonomous deploy --yes --prebuilt           # build then deploy --prebuilt
agent-studio autonomous deploy --yes --json
```

Config lives in `<project>/agent-studio.yaml`; defaults are deploy disabled.

```yaml
deploy:
  enabled: false              # MUST opt in
  target: vercel
  environment: preview        # preview | production | <custom>
  project_path: "."
  vercel:
    mode: source              # source | prebuilt
    prod: false
    prebuilt: false
    build_before_deploy: false
    inspect: true
    inspect_timeout: "5m"
    skip_domain: false
    token_env: "VERCEL_TOKEN"
    org_id_env: "VERCEL_ORG_ID"
    project_id_env: "VERCEL_PROJECT_ID"
    scope: null
    project: null
```

Auto-deploy semantics: after every task completes and final integration passes, the controller calls the configured deploy adapter. On success the session enters `status: completed` with `session.deployment.status = "deployed"` and `latest_deployment_url` populated. On failure the session is paused with `pause_reason: deployment-failed` and a review item (`source_type: deployment_failure`, `reason_code: deployment-failed`, `allowed_actions: [show, reject, resolve]`) is created — the user inspects the deployment artifact, fixes the underlying problem, and either retries with `agent-studio autonomous deploy --yes` or resolves the review.

Manual deploy semantics: `--dry-run` prints sanitized commands without invoking Vercel; `--yes` runs the real CLI and writes `deployment.json`; one of the two is REQUIRED (no default), preventing accidental real deploys. Manual deploys do NOT pause an already-completed session — the user invoked the command knowing it might fail. Pause is reserved for the auto session-end deploy hook (`source=session_end`); manual `agent-studio autonomous deploy` always writes the artifact and (on failure) the review item, then returns control to the operator.

Token redaction is enforced everywhere: `--token <value>` is added to the actual subprocess args at runtime but never appears in `deployment.json`, `controller-log.jsonl`, `final-run-status.md`, the dry-run output, or any CLI stdout. The sanitized command list (with `<redacted>` in place of the token) is what gets persisted. `deployment.json` records `token_env: VERCEL_TOKEN` + `token_present: true|false` so a debugger can verify the env var was set without ever seeing the value.

Deployment artifacts: each attempt writes `<session>/deployments/<deployment_id>/deployment.json` (schema 1) with status (ready | failed | unknown), deployment_url, git branch + commit, full sanitized command results (args + exit_code + stdout/stderr tails truncated to 3KB), the resolved vercel config snapshot, the source linkage (session_status / task_graph_path / final_run_status_path), and a failure block (failure_type ∈ {vercel_cli_missing, vercel_auth_missing, vercel_deploy_failed, vercel_inspect_failed, deployment_url_missing, unknown}, message, failed_command). `final-run-status.md` gains a `## Deployment` section.

Out of scope for MVP-4E: smoke check (curl the deployed URL), rollback, production health monitoring, dashboard, GitHub PR deployment integration, Slack/email notification, multi-provider deploy (Fly.io / Docker compose / SSH), domain alias management, Vercel env management, login flow. Smoke + rollback ship in MVP-4F.

MVP-4F (Smoke Check + Rollback + Complete Final Report): every successful deploy is verified with HTTP smoke checks. Production smoke failures can optionally trigger a `vercel rollback`. Failures route to the same Human Review Queue. The final report becomes a complete, evidence-backed status page.

```bash
agent-studio autonomous smoke --url https://app.vercel.app/         # one-shot manual smoke
agent-studio autonomous smoke                                       # smoke against latest deployment
agent-studio autonomous smoke --rollback-on-failure --yes           # smoke + auto-rollback (prod only)
agent-studio autonomous rollback --dry-run                          # print sanitized vercel rollback args
agent-studio autonomous rollback --yes                              # execute rollback + write rollback.json
```

Smoke config (extends the deploy block; smoke defaults to enabled with a single GET / → 200 check):

```yaml
deploy:
  # ... fields above ...
  smoke_checks:
    enabled: true
    timeout_sec: 10
    retries: 0
    checks:
      - name: home
        method: GET
        path: /
        expect_status: [200]
        expect_body_contains: null
        headers: {}
  rollback:
    enabled: false                         # MUST opt in
    production_only: true                  # preview smoke failures NEVER auto-rollback
    trigger_on_smoke_failure: true
    timeout: "30s"
    status_timeout: "30s"
```

Auto-smoke + rollback semantics: after a deploy reaches `ready`, the controller runs `run_smoke_checks` against `deployment_url`. Pass → session completes (`smoke_status: passed`). Fail → emit a `smoke_check_failure` review item; if env=production AND `rollback.enabled` AND `production_only` allows, run `vercel rollback [<url>]` then `vercel rollback status` to confirm; success ⇒ pause with reason `smoke-check-failed-rolled-back`, failure ⇒ also emit a `rollback_failure` review item and pause with `rollback-failed`. Preview environments NEVER auto-rollback even when `enabled: true` — they write a `skipped` rollback artifact and let the user act manually.

Manual smoke / rollback semantics — same pause discipline as manual deploy: `autonomous smoke` resolves URL from `--url` > `--deployment <id>` > latest deployment; failures still write `smoke-check.json` and emit a `smoke_check_failure` review item but do NOT pause the session, and rollback never runs from manual smoke unless BOTH `--rollback-on-failure` AND `--yes` are passed (and even then, `rollback.production_only` still gates auto-rollback for non-prod environments). `autonomous rollback` REQUIRES `--dry-run` or `--yes` (no default) — `--dry-run` prints the sanitized command and writes nothing; `--yes` executes the real CLI and writes `rollback.json`. Manual rollback failure writes the artifact but does NOT pause an active session — the operator chose to run it.

Pause discipline summary (the rule the controller never breaks):
- Auto deploy / smoke / rollback at `source=session_end` → on failure: write artifact + emit review item + pause session with the matching `pause_reason`.
- Any manual `autonomous deploy`/`smoke`/`rollback` invocation → on failure: write artifact + emit review item, but the session's `status` is unchanged. Pause is reserved for the unattended path so that a failed manual command never silently locks an already-completed session.

Smoke / rollback artifacts: `<session>/smoke-checks/<smoke_check_id>/smoke-check.json` (schema 1) records every check's status / status_code / latency_ms / body tail (truncated to 3KB) plus `headers_redacted: {...}` (every header value is `<redacted>` — never persists secrets). Failure types: `status_code_mismatch | body_assertion_failed | timeout | connection_error | dns_error | tls_error | deployment_url_missing | unknown`. `<session>/rollbacks/<rollback_id>/rollback.json` (schema 1) records target / environment / sanitized commands / status (`completed | failed | skipped`) and on failure: `vercel_rollback_failed | vercel_rollback_status_failed | unknown`. Token redaction is end-to-end — `--token <value>` exists only in the live subprocess args; sanitized variants land in every artifact, log line, and CLI output.

Final report (`final-run-status.md`) becomes a complete evidence-backed page with sections: Summary · Tasks · Integration · Corrective Tasks · Deployment · Smoke Checks · Rollback · Human Review Queue · Evidence Trail · Next Actions. The Evidence Trail block lists every persisted artifact with relative paths so a reviewer can `cat` straight from the report. Next Actions is rendered deterministically from current session + open review items + latest deployment state — e.g. on `pause_reason: smoke-check-failed-rolled-back` it prints `agent-studio autonomous reviews show <id>` and `agent-studio autonomous deploy --dry-run` so the user has the exact next CLI to run.

Out of scope for MVP-4F: continuous health monitoring (smoke runs once after deploy), multi-provider rollback, domain re-aliasing, Vercel env-var rotation, traffic shifting / canary, automatic re-deploy after rollback, Slack/email notifications, dashboard. The repair loop's job is to give the user a complete picture and a clear next step — not to keep rolling forward without them.

Useful follow-on commands:

```bash
./agent-studio agents
./agent-studio workflows
./agent-studio logs
./agent-studio run-agent product_manager --materialize
./agent-studio run-agent architect --materialize
./agent-studio run-agent developer --materialize
./agent-studio costs
./agent-studio retry prd
./agent-studio agentic-abandonments list [--project <id>] [--json]
./agent-studio agentic-candidates list [--project <id>] [--run <run-id>] [--json]
./agent-studio agentic-candidates show --candidate <id|selected> [--project <id>] [--run <run-id>] [--json]
./agent-studio agentic-candidates apply --candidate <id|selected> (--dry-run | --yes) [--project <id>] [--run <run-id>]
./agent-studio agentic-runs list [--project <id>] [--json]
./agent-studio agentic-runs show --run <run-id> [--project <id>] [--json]
./agent-studio new --from <requirements.md>
./agent-studio autonomous start [--project <id>] [--max-steps N]
./agent-studio autonomous status [--project <id>] [--json]
./agent-studio autonomous logs [--project <id>] [--tail N]
./agent-studio autonomous halt [--project <id>]
./agent-studio autonomous resume [--project <id>] [--max-steps N]
./agent-studio autonomous integrate [--project <id>] [--json]
./agent-studio autonomous reviews list [--all] [--session <sid>] [--json]
./agent-studio autonomous reviews show <review_id>
./agent-studio autonomous reviews approve <review_id> --yes
./agent-studio autonomous reviews reject <review_id> --reason "..."
./agent-studio autonomous reviews resolve <review_id> --note "..." [--mark-task pending|completed|blocked]
./agent-studio autonomous deploy (--dry-run | --yes) [--prod | --preview] [--prebuilt] [--json]
```

Manual Codex PRD flow:

```bash
./agent-studio new "做一个个人记账 web app，支持收入支出、分类、月度统计"
./agent-studio run software_project
./agent-studio prd research
./agent-studio prd options
./agent-studio prd select option-b
./agent-studio prd prepare
# Paste the generated prompt into Codex/ChatGPT, save the JSON response, then:
./agent-studio prd import examples/manual-codex-prd-response.json
./agent-studio prd validate
```

`prd research` uses `TAVILY_API_KEY` when set. Without a key, use deterministic mock data:

```bash
./agent-studio prd research --mock
```

`prd research` also writes Research v2 artifacts. You can refresh them from existing sources without another Tavily call:

```bash
./agent-studio prd research-v2
```

You can also generate a local benchmark library without any external API calls:

```bash
./agent-studio prd benchmark
```

Research v2 artifacts:

```text
docs/product/research-plan.md
docs/product/source-quality-report.md
docs/product/reference-products/index.md
docs/product/reference-products/reference-products.json
docs/product/feature-patterns.md
docs/product/ux-patterns.md
docs/product/product-management-benchmarks.md
docs/product/evidence-chain.md
docs/product/reference-screenshots/README.md
docs/product/benchmark-library/index.md
docs/product/benchmark-library/<domain>-template.md
docs/product/benchmark-library/quality-gates.md
docs/product/benchmark-library/decision-playbook.md
docs/product/benchmark-library/development-handoff.md
docs/product/benchmark-library/benchmark-library.json
```

`product-management-benchmarks.md` turns mature product-management and AI-development products into operating standards for the PRD Agent: Aha!-style lifecycle discipline, Dovetail-style evidence synthesis, Productboard-style insight-to-feature traceability, Jira Product Discovery-style option selection, v0/Replit-style prototype handoff, and Claude Code-style execution gates. `evidence-chain.md` maps source evidence or assumptions to PRD decisions, MVP/non-goal implications, and downstream QA/review gates.

`benchmark-library/` is the token-free local knowledge base for PRD quality. It encodes mature-product patterns, domain-specific PRD questions, quality gates, decision playbooks, and handoff contracts for UI, architecture, development, QA, and review agents. `prd draft` refreshes this library automatically.

`prd import` validates the imported artifacts and approves the pending PRD gate when validation passes.

Fully local PRD draft flow:

```bash
./agent-studio prd research
./agent-studio prd options
./agent-studio prd select option-b
./agent-studio prd council
./agent-studio prd draft --import
./agent-studio prd product-fit
./agent-studio prd score
./agent-studio prd critique
./agent-studio prd team-review
./agent-studio design draft
./agent-studio design critique
./agent-studio design directions
./agent-studio architecture draft
./agent-studio implementation draft
./agent-studio run-agent qa --materialize
./agent-studio run-agent reviewer --materialize
./agent-studio prd build-review
./agent-studio teams plan
./agent-studio design team
./agent-studio implementation team
./agent-studio teams review
```

`prd options` creates three PM strategy options plus a Lead PM recommendation. `prd select` writes the product decision. `prd council` writes separate council role artifacts under `docs/product/council/`. `prd draft` automatically refreshes the council outputs and then uses the selected strategy to create an importable PRD JSON draft. The local draft is deterministic and does not call ChatGPT; use it as a fast baseline, then refine with Codex/ChatGPT when you want higher product judgment.

`prd score` writes an independent 80-point quality score to `docs/product/prd-score.md` and `docs/product/prd-score.json`. It checks research depth, evidence chain, differentiation, UX specificity, MVP scope discipline, testability, handoff readiness, and anti-generic quality. `prd critique` writes `docs/product/prd-critique.md` with Market PM, UX Researcher, Technical PM, QA Lead, Reviewer, Critic, and Lead PM perspectives. `prd validate` uses the same hard gates, so a PRD cannot pass by giving itself a high `prd-quality-score.md`.

`prd product-fit` writes `docs/product/product-fit.md` and `docs/product/product-fit.json`. It judges whether the product is worth building across user pain, target user, alternatives, differentiation, core workflow, valuable artifact, repeat use, and MVP boundary. `prd validate` also uses this gate.

`prd team-review` writes `docs/product/prd-agent-team-review.md`, `docs/product/prd-agent-team-optimized-workflow.md`, and `docs/product/prd-agent-team-contracts.json`. It documents the PRD agent team responsibilities, optimized order, and handoff contracts.

`design draft` writes `docs/design/user-flow.md`, `docs/design/design-system.md`, and `docs/design/component-spec.md` from PRD/product-fit/benchmark context. `design critique` writes `docs/design/design-critique.md` and `docs/design/design-critique.json`, scoring information architecture, first-screen value, visual hierarchy, workflow efficiency, state completeness, asset integrity, responsiveness, and domain fit.

`design directions` is the P0 external visual-direction bridge. It generates three explicitly opposed v0-ready variants: `minimalist-editorial`, `bold-marketing`, and `dense-dashboard`. With `V0_API_KEY` set, `--provider auto` calls the v0 Platform API and captures screenshots from the returned demo URL. Without a key, it falls back to deterministic mock variants so the rest of the pipeline can be tested. It writes `docs/design/visual-directions.md`, `docs/design/visual-direction-pairwise.md`, `docs/design/selected-visual-direction.md`, and `.agent/artifacts/visual_directions/variants.json`. The critic uses rubric dimensions plus pairwise comparisons instead of relying on one unstable absolute visual score. v0 defaults to `--prompt-mode concise` and omits `modelId` unless `V0_MODEL_ID` or `--v0-model` is set; use `--prompt-mode full` for maximum PRD/design context, and `--v0-request-timeout 300 --v0-timeout 900` or `.env.local` overrides for slow generations. `design v0-smoke` sends a tiny prompt directly to v0 so key, billing, model, and request latency can be diagnosed before running product visual directions.

`architecture draft` regenerates `docs/architecture/*` and `.agent/tasks/generated-tasks.json` from PRD and design gate artifacts, including `product-fit`, `prd-score`, `prd-critique`, `design-critique`, and `component-spec`.

`implementation draft` reads `.agent/tasks/generated-tasks.json` and writes deterministic code artifacts under `apps/web/` plus a smoke-test checklist. Portfolio builder projects generate a runnable static MVP with profile editing, avatar upload validation, project gallery editing, theme switching, live preview, local save, project ordering, and self-contained HTML export. Open `apps/web/index.html` directly in a browser.

`run-agent qa --materialize` detects generated static web apps and writes `docs/qa/test-plan.md`, `docs/qa/test-results.md`, and `docs/qa/bugs.md` with checks for file completeness, profile/project editors, image validation, live preview, local persistence, export behavior, escaping, and responsiveness. When local Chrome/Chromium is available, it also captures `.agent/artifacts/qa/desktop-screenshot.png` and `.agent/artifacts/qa/mobile-screenshot.png` as browser evidence.

`run-agent reviewer --materialize` detects generated static web apps and reviews task coverage, implementation files, QA status, escaping, and static export behavior before approving or requesting changes in `docs/review/review-report.md`.

`prd build-review` runs the PRD/product team after implementation. It writes `docs/product/post-build-product-review.md`, `docs/product/post-build-product-review.json`, and `docs/product/downstream-agent-team-plan.md`. This gate is stricter than QA: a generated app can pass static QA and still fail product review if it is only a runnable demo rather than a strong, differentiated product.

`teams plan` turns a failed or needs-revision post-build review into downstream team plans for UI, Developer, QA, and Review. It writes `docs/design/ui-team-plan.md`, `docs/implementation/developer-team-plan.md`, `docs/qa/qa-team-plan.md`, `docs/review/review-team-plan.md`, `.agent/teams/downstream-agent-contracts.json`, and `.agent/tasks/downstream-remediation-tasks.json`.

`design team` runs the full UI Product Team. It writes role outputs for UX Flow Lead, Visual Design Lead, Asset Strategy Lead, Visual QA Lead, Design Critic, Lead UI Product synthesis, reference-to-design traceability, screen-level spec, template spec, Dev handoff, visual QA checklist, design contract JSON, contracts JSON, and a UI team score under `docs/design/` and `docs/design/ui-team/`. This is the handoff gate for remediation development, not final product approval. If browser screenshots are missing, the status remains evidence-pending even when the Dev Team handoff is ready.

`implementation team` runs the Developer Team. It consumes `docs/design/design-contract.json` and `docs/design/ui-team-dev-handoff.md`, then writes Editor Workflow, Preview/Export, Asset Handling, Browser Test, and Integration Lead plans plus `docs/implementation/implementation-contract.json`, `docs/implementation/developer-team-task-plan.json`, and `docs/implementation/acceptance-matrix.md`. This is the remediation implementation planning gate; final product approval still requires QA, Review, and post-build product review.

`teams review` reviews and optimizes all teams. It writes `docs/team-review/team-system-review.md`, per-team review docs, `.agent/teams/team-maturity.json`, `.agent/tasks/team-optimization-tasks.json`, and missing contracts for Architecture, QA, Review, and Lead/Orchestrator teams.

Manual council role flow:

```bash
./agent-studio prd council --prepare
# Run each role prompt in Codex/ChatGPT and save each answer as roles/<role-id>/response.json.
./agent-studio prd council --import-dir "<prompt-pack-directory-from-prepare>"
```

The import command expects `response.json` files inside the generated prompt-pack directory, under `roles/<role-id>/`.

PRD Agent v2 also requires research-team artifacts:

```text
docs/product/council/*.md
docs/product/competitor-matrix.md
docs/product/pm-debate.md
docs/product/prd-quality-score.md
```

`prd validate` fails if these artifacts are missing or if `prd-quality-score.md` reports a final score below `42/60`.

Portfolio builder ideas are recognized as a first-class PRD domain:

```bash
./agent-studio new "做一个个人 portfolio builder web app，用户可以上传头像和作品截图，填写个人简介、技能、项目描述、项目链接和联系方式，选择主题，预览并导出静态 HTML。"
./agent-studio run software_project
./agent-studio prd research
./agent-studio prd options
./agent-studio prd select option-b --notes "重点测试上传图片、项目描述、主题选择、预览和静态导出。"
./agent-studio prd council
./agent-studio prd draft --import
./agent-studio design draft
./agent-studio design critique
./agent-studio design directions
./agent-studio architecture draft
./agent-studio implementation draft
./agent-studio prd build-review
./agent-studio teams plan
./agent-studio design team
./agent-studio implementation team
./agent-studio teams review
```

Dashboard source is in `apps/dashboard`. Dependencies are declared but not installed by the MVP bootstrap.
