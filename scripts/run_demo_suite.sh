#!/usr/bin/env bash
# RC-4B: 3-project demo matrix runner.
#
# Drives Local Agent Studio across 3 distinct small Next.js + TS demos to
# prove BOTH greenfield generation AND change-mode delivery work end-to-end
# with real Codex on more than one project shape:
#
#   1. ai-writing-quality-editor    deterministic writing analyzer
#   2. ai-usage-cost-planner        AI cost estimator with localStorage
#   3. agent-review-queue-console   agent workflow / human-in-the-loop dashboard
#
# Each demo's flow:
#   agent-studio init
#   agent-studio new --from <demo>/requirements.md
#   cp seed files into project dir + write agent-studio.yaml
#   npm install + git baseline commit
#   agent-studio autonomous start                      (~3 task commits)
#   agent-studio change new --from <demo>/changes/01-*.md
#   agent-studio change run latest
#   agent-studio change validate latest --json + autonomous validate-artifacts --json
#
# NO Vercel deploy. NO real LLM other than Codex itself. NO new pip/npm deps.
# NO demo-suite-side scripted edits to the codebase. NO LLM-as-judge.
# NO RC-3F detector work.
#
# DEFAULT IS DRY-RUN. Pass --run to actually execute.
# --run will:
#   - npm install in each of 3 tiny projects (~30-60s each)
#   - burn ~150-250k Codex tokens total (4 inner runs per demo × 3 demos)
#   - leave per-demo evidence at /tmp/rc4b-<demo>/.agent-studio/projects/<id>/
#
# Required binaries on PATH: $CODEX_BIN, npm, node, git, python3.
#
# Hard NO-list:
#   - NOT production deploy / NOT rollback / NOT --yolo / NOT prod env
#   - tokens never echoed; only token_present=true persists in artifacts

set -euo pipefail

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
EXAMPLES_ROOT="$REPO_ROOT/examples"
WORKSPACE_BASE="${WORKSPACE_BASE:-/tmp/rc4b}"
CODEX_BIN="${CODEX_BIN:-/opt/homebrew/bin/codex}"

# Per-demo budgets. 3 greenfield tasks + 1 change run = 4 inner runs max.
BUDGET_MAX_TASKS=3
BUDGET_MAX_INNER_RUNS=5
BUDGET_MAX_CANDIDATES=1
BUDGET_MAX_REPAIR=1
INTEGRATION_TIMEOUT_SEC=600

DEMOS=(
  "ai-writing-quality-editor"
  "ai-usage-cost-planner"
  "agent-review-queue-console"
)

# -----------------------------------------------------------------------------
# Argv
# -----------------------------------------------------------------------------
RUN_MODE="dry-run"
SINGLE_DEMO=""
for arg in "$@"; do
  case "$arg" in
    --run) RUN_MODE="run" ;;
    --dry-run) RUN_MODE="dry-run" ;;
    --demo=*) SINGLE_DEMO="${arg#--demo=}" ;;
    --demo)
      echo "run_demo_suite.sh: --demo expects --demo=<name>" >&2
      exit 2
      ;;
    -h|--help)
      cat <<'HELP'
run_demo_suite.sh — RC-4B 3-project demo matrix runner

Usage:
  scripts/run_demo_suite.sh                              # dry-run all 3
  scripts/run_demo_suite.sh --run                        # execute all 3 (real Codex)
  scripts/run_demo_suite.sh --demo=ai-writing-quality-editor --run
  CODEX_BIN=/path/to/codex scripts/run_demo_suite.sh --run

Env:
  CODEX_BIN          path to codex binary (default: /opt/homebrew/bin/codex)
  WORKSPACE_BASE     prefix for per-demo workspaces (default: /tmp/rc4b)

Defaults to dry-run. --run will:
  - npm install in each demo project (~30-60s each)
  - consume ~150-250k Codex tokens total (4 inner runs × 3 demos)
  - leave per-demo evidence under <WORKSPACE_BASE>-<demo>/
HELP
      exit 0
      ;;
    *) echo "run_demo_suite.sh: unknown arg '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

# Filter demos if --demo specified.
if [[ -n "$SINGLE_DEMO" ]]; then
  found="no"
  for d in "${DEMOS[@]}"; do
    if [[ "$d" == "$SINGLE_DEMO" ]]; then
      found="yes"
      break
    fi
  done
  if [[ "$found" != "yes" ]]; then
    echo "run_demo_suite.sh: unknown demo '$SINGLE_DEMO'. Known: ${DEMOS[*]}" >&2
    exit 2
  fi
  DEMOS=("$SINGLE_DEMO")
fi

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
say()  { printf '\n[rc4b] %s\n' "$*"; }
warn() { printf '\n[rc4b WARN] %s\n' "$*" >&2; }
do_or_print() {
  if [[ "$RUN_MODE" == "run" ]]; then
    printf '\n$ %s\n' "$*"
    eval "$*"
  else
    printf '\n[dry-run] $ %s\n' "$*"
  fi
}

# Per-demo evidence the post-run summary collates.
declare -a SUMMARY_LINES=()
record_summary() { SUMMARY_LINES+=("$*"); }

# -----------------------------------------------------------------------------
# Pre-flight
# -----------------------------------------------------------------------------
say "RC-4B 3-project demo matrix runner — mode: $RUN_MODE"
say "repo:        $REPO_ROOT"
say "examples:    $EXAMPLES_ROOT"
say "workspaces:  ${WORKSPACE_BASE}-<demo>"
say "codex bin:   $CODEX_BIN"
say "demos:       ${DEMOS[*]}"

if [[ ! -x "$CODEX_BIN" ]]; then
  warn "CODEX_BIN '$CODEX_BIN' is not executable. Try: which codex"
  exit 1
fi
say "codex --version: $("$CODEX_BIN" --version 2>&1 | head -1)"

for cmd in npm node git python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    warn "$cmd not on PATH"
    exit 1
  fi
done
say "npm --version: $(npm --version 2>&1 | head -1)"
say "node --version: $(node --version 2>&1 | head -1)"

for demo in "${DEMOS[@]}"; do
  if [[ ! -d "$EXAMPLES_ROOT/$demo" ]]; then
    warn "demo seed missing at $EXAMPLES_ROOT/$demo"
    exit 1
  fi
  for required in package.json tsconfig.json next.config.mjs requirements.md; do
    if [[ ! -f "$EXAMPLES_ROOT/$demo/$required" ]]; then
      warn "$EXAMPLES_ROOT/$demo missing $required"
      exit 1
    fi
  done
  if [[ -z "$(ls -A "$EXAMPLES_ROOT/$demo/changes/" 2>/dev/null)" ]]; then
    warn "$EXAMPLES_ROOT/$demo/changes/ is empty (need at least one change request)"
    exit 1
  fi
done

# -----------------------------------------------------------------------------
# Cost / blast radius warning
# -----------------------------------------------------------------------------
cat <<WARN

------------------------------------------------------------------------------
RC-4B COST + BLAST-RADIUS WARNING

  - npm install:     ~30-60s per demo (~3 demos = ~90-180s); ~250-300 MB
                     node_modules per demo.
  - Codex tokens:    ~50-80k per demo × ${#DEMOS[@]} demo(s) = ~150-250k total.
  - Wall clock:      ~10-20 min Codex per demo (greenfield 3 tasks +
                     1 change run) × ${#DEMOS[@]} demo(s).
  - Vercel:          NOT touched. RC-4B intentionally skips deploy.
  - Real LLM:        NOT called other than Codex itself.
  - LLM-as-judge:    NOT used.
  - Production:      NOT touched. NOT rollback. NOT smoke check.
  - Detector:        NOT touched (RC-3F is paused).

Dry-run prints commands only. 0 tokens, 0 commits, 0 npm installs.
------------------------------------------------------------------------------
WARN

# -----------------------------------------------------------------------------
# Per-demo runner
# -----------------------------------------------------------------------------
run_demo() {
  local demo="$1"
  local seed="$EXAMPLES_ROOT/$demo"
  local workspace="${WORKSPACE_BASE}-${demo}"
  local change_request
  change_request="$(ls "$seed/changes/"*.md 2>/dev/null | head -1)"
  if [[ -z "${change_request:-}" ]]; then
    warn "no change request found in $seed/changes/"
    return 1
  fi

  say "================================================================"
  say "DEMO: $demo"
  say "  seed:           $seed"
  say "  workspace:      $workspace"
  say "  change request: $change_request"
  say "================================================================"

  do_or_print "rm -rf '$workspace'"
  do_or_print "mkdir -p '$workspace'"
  do_or_print "'$REPO_ROOT/agent-studio' --root '$workspace' init"
  do_or_print "'$REPO_ROOT/agent-studio' --root '$workspace' new --from '$seed/requirements.md'"

  local project_dir
  if [[ "$RUN_MODE" == "run" ]]; then
    project_dir="$(ls -d "$workspace/.agent-studio/projects/"*/ 2>/dev/null | head -1 | sed 's:/$::')"
    if [[ -z "${project_dir:-}" ]]; then
      warn "no project dir created for $demo"
      return 1
    fi
    say "project dir: $project_dir"
  else
    project_dir="<workspace>/.agent-studio/projects/${demo}-XXXXXX"
    say "project dir (dry-run placeholder): $project_dir"
  fi

  # Copy seed files (NOT requirements.md / changes/ — those are inputs)
  for item in package.json next.config.mjs tsconfig.json .gitignore; do
    do_or_print "cp '$seed/$item' '$project_dir/'"
  done
  do_or_print "cp -r '$seed/app' '$project_dir/'"

  # Write per-demo agent-studio.yaml inline so CODEX_BIN gets interpolated.
  local YAML_BODY
  YAML_BODY="$(cat <<YAML
agentic:
  patch_worker: codex
  codex:
    command: $CODEX_BIN
    sandbox: workspace-write
    ask_for_approval: on-request
    timeout_sec: $INTEGRATION_TIMEOUT_SEC
    max_prompt_chars: 80000

autonomous:
  budgets:
    max_tasks_per_session: $BUDGET_MAX_TASKS
    max_total_inner_runs: $BUDGET_MAX_INNER_RUNS
    max_candidates_per_task: $BUDGET_MAX_CANDIDATES
    max_repair_attempts_per_candidate: $BUDGET_MAX_REPAIR
    max_abandoned_tasks: 1
    max_corrective_tasks: 1

integration:
  every_n_tasks: 1
  run_at_session_end: true
  timeout_sec: $INTEGRATION_TIMEOUT_SEC

deploy:
  enabled: false
YAML
  )"
  if [[ "$RUN_MODE" == "run" ]]; then
    printf '%s\n' "$YAML_BODY" > "$project_dir/agent-studio.yaml"
    say "wrote $project_dir/agent-studio.yaml"
    # Belt-and-suspenders safety
    if grep -qE '^  enabled: true' "$project_dir/agent-studio.yaml"; then
      warn "deploy enabled detected in RC-4B yaml — refusing"
      return 1
    fi
  else
    say "agent-studio.yaml that will be written:"
    printf '%s\n' "$YAML_BODY" | sed 's/^/    /'
  fi

  do_or_print "(cd '$project_dir' && npm install --no-audit --no-fund)"

  # Confirm baseline build passes BEFORE Codex touches anything.
  do_or_print "(cd '$project_dir' && npm run build)"

  do_or_print "(cd '$project_dir' && git init -q -b main)"
  do_or_print "(cd '$project_dir' && git config user.email rc4b@dogfood)"
  do_or_print "(cd '$project_dir' && git config user.name rc4b)"
  do_or_print "(cd '$project_dir' && git add -A)"
  do_or_print "(cd '$project_dir' && git -c commit.gpgsign=false commit -q -m 'rc4b ${demo} baseline')"

  # ---- Greenfield (autonomous start) ------------------------------------
  do_or_print "'$REPO_ROOT/agent-studio' --root '$workspace' autonomous preflight"

  if [[ "$RUN_MODE" != "run" ]]; then
    say "[dry-run] would run: agent-studio autonomous start (3 tasks)"
    say "[dry-run] would run: agent-studio change new --from $change_request"
    say "[dry-run] would run: agent-studio change run latest"
    say "[dry-run] would run: agent-studio change validate latest --json"
    record_summary "[dry-run] $demo: planned greenfield + 1 change run"
    return 0
  fi

  # Real run (only --run from here on)
  say "STARTING $demo greenfield — Codex tokens ahead."
  set +e
  "$REPO_ROOT/agent-studio" --root "$workspace" autonomous start \
    > "$workspace/autonomous-start.out.log" 2> "$workspace/autonomous-start.err.log"
  local START_EXIT=$?
  set -e
  say "autonomous start exit: $START_EXIT"
  tail -40 "$workspace/autonomous-start.out.log" || true

  # ---- RC-4C.1.D: greenfield-completed gate -----------------------------
  # Refuse to run change-mode against a demo whose greenfield session
  # didn't reach session.status="completed". Otherwise we'd be running
  # the change request against a half-built scaffold (or worse, against
  # the baseline placeholder) and producing meaningless evidence.
  #
  # Read autonomous-session.json directly — the controller writes it
  # atomically per state transition, and `agent-studio autonomous status`
  # is just a thin reader over it.
  local SESSION_JSON
  SESSION_JSON="$(ls -t "$project_dir/.agent/autonomous/sessions/"*/autonomous-session.json 2>/dev/null | head -1 || true)"
  local SESSION_STATUS=""
  if [[ -n "${SESSION_JSON:-}" && -f "$SESSION_JSON" ]]; then
    SESSION_STATUS="$(python3 -c "import json; d=json.load(open('$SESSION_JSON')); print(d.get('status') or '')" 2>/dev/null || true)"
  fi
  say "greenfield session status: ${SESSION_STATUS:-<none>}"
  if [[ "$SESSION_STATUS" != "completed" ]]; then
    local PAUSE_REASON
    if [[ -n "${SESSION_JSON:-}" && -f "$SESSION_JSON" ]]; then
      PAUSE_REASON="$(python3 -c "import json; d=json.load(open('$SESSION_JSON')); print(d.get('pause_reason') or '')" 2>/dev/null || true)"
    fi
    warn "$demo: greenfield did NOT complete (status=${SESSION_STATUS:-unknown}, pause_reason=${PAUSE_REASON:-n/a})"
    warn "$demo: refusing to run change new + change run against an incomplete greenfield"
    warn "$demo: inspect review queue:"
    warn "  agent-studio --root $workspace autonomous reviews list"
    warn "  agent-studio --root $workspace autonomous status"
    record_summary "$demo: greenfield_paused (status=${SESSION_STATUS:-unknown}, pause_reason=${PAUSE_REASON:-n/a}); change run SKIPPED"
    # Exit non-zero in single-demo mode so the caller's `tee` log makes
    # the failure obvious; in full-suite mode we keep going so the other
    # demos still produce evidence.
    if [[ "${#DEMOS[@]}" -eq 1 ]]; then
      return 1
    fi
    return 0
  fi

  # ---- Change request (only when greenfield completed cleanly) ----------
  do_or_print "'$REPO_ROOT/agent-studio' --root '$workspace' change new --from '$change_request'"

  set +e
  "$REPO_ROOT/agent-studio" --root "$workspace" change run latest --json \
    > "$workspace/change-run.out.json" 2> "$workspace/change-run.err.log"
  local CHANGE_RUN_EXIT=$?
  set -e
  say "change run exit: $CHANGE_RUN_EXIT"
  cat "$workspace/change-run.out.json" || true

  do_or_print "'$REPO_ROOT/agent-studio' --root '$workspace' change status latest --json"
  do_or_print "'$REPO_ROOT/agent-studio' --root '$workspace' change validate latest --json"
  do_or_print "'$REPO_ROOT/agent-studio' --root '$workspace' autonomous validate-artifacts --json"

  # ---- Per-demo summary -------------------------------------------------
  local change_dir
  change_dir="$(ls -d "$project_dir/.agent/changes/"*/ 2>/dev/null | head -1 | sed 's:/$::')"
  local applied_change_path=""
  local delivery_report_path=""
  local commit_sha=""
  local branch=""
  if [[ -n "${change_dir:-}" ]]; then
    if [[ -f "$change_dir/applied-change.json" ]]; then
      applied_change_path="$change_dir/applied-change.json"
      commit_sha="$(python3 -c "import json; d=json.load(open('$applied_change_path')); c=d.get('commit') or {}; print(c.get('sha') or '')" 2>/dev/null || true)"
      branch="$(python3 -c "import json; d=json.load(open('$applied_change_path')); c=d.get('commit') or {}; print(c.get('branch') or '')" 2>/dev/null || true)"
    fi
    if [[ -f "$change_dir/delivery-report.md" ]]; then
      delivery_report_path="$change_dir/delivery-report.md"
    fi
  fi

  # Confirm npm run build still passes on the change branch.
  local build_status="?"
  if [[ -d "$project_dir" ]]; then
    set +e
    ( cd "$project_dir" && npm run build ) > "$workspace/post-change-build.log" 2>&1
    local BUILD_EXIT=$?
    set -e
    if [[ "$BUILD_EXIT" -eq 0 ]]; then
      build_status="passed"
    else
      build_status="FAILED (exit $BUILD_EXIT)"
    fi
  fi

  record_summary "$demo: change_run_exit=$CHANGE_RUN_EXIT, commit=${commit_sha:-?}, branch=${branch:-?}, build=$build_status"
  record_summary "  applied:  ${applied_change_path:-(missing)}"
  record_summary "  delivery: ${delivery_report_path:-(missing)}"
}

# -----------------------------------------------------------------------------
# Main loop
# -----------------------------------------------------------------------------
for demo in "${DEMOS[@]}"; do
  run_demo "$demo" || warn "demo $demo did not complete cleanly; continuing"
done

# -----------------------------------------------------------------------------
# Cross-demo summary
# -----------------------------------------------------------------------------
say "================================================================"
say "RC-4B demo matrix summary (${#DEMOS[@]} demo(s))"
say "================================================================"
if [[ ${#SUMMARY_LINES[@]} -eq 0 ]]; then
  say "(no per-demo summaries collected)"
else
  for line in "${SUMMARY_LINES[@]}"; do
    printf '  %s\n' "$line"
  done
fi

cat <<'POST'

------------------------------------------------------------------------------
POST-RUN TRIAGE — RC-4B

  A. All 3 demos green: each greenfield session completed + change run
     completed + commit on agentic/change/<id> + applied-change.json
     valid + delivery-report.md valid + npm run build passed
     → RC-4B verified. Move to RC-4C: write docs/EVALUATION.md from this
       evidence (commit hashes, applied-change SHAs, real run timings).

  B. 1 or 2 demos green: still useful evidence. Capture which demo failed
     and at which surface (greenfield task vs change run vs build),
     record honestly in EVALUATION.md, decide whether to tighten the
     failed demo's requirements.md / change-request.md or accept the
     surface as-is for the portfolio.

  C. 0 demos green: real bug surfaced. Inspect:
       <workspace>/<demo>/.agent/autonomous/sessions/<sid>/controller-log.jsonl
       <workspace>/<demo>/.agent/changes/<cid>/delivery-report.md
       <workspace>/<demo>/post-change-build.log
     Common drift surfaces flagged in docs/demo-matrix.md.
------------------------------------------------------------------------------
POST
