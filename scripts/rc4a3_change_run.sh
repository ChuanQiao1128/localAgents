#!/usr/bin/env bash
# RC-4A.3: Real Codex Change-Run Tiny Probe.
#
# Goal: Run ONE real Codex change request end-to-end through `agent-studio
# change run` against a tiny Next.js project. Proves Change Request Mode
# (RC-4A.2) works with the real patch worker (codex), not just the fake
# patch worker the unit/e2e suite uses.
#
# NO Vercel deploy. NO smoke check. NO real LLM other than Codex itself.
# NO LLM-as-judge. NO new pip/npm deps. NO demo matrix yet (RC-4B).
# NO RC-3F detector work touched.
#
# DEFAULT IS DRY-RUN. Pass --run to actually execute.
# --run will:
#   - npm install in the tiny project (~30-60s)
#   - burn ~25-50k Codex tokens (1 task × 1 candidate cap)
#   - leave a real git commit on `agentic/change/<change_id>` carrying the
#     `Change-Id` + `Source-Change-Request` trailers.
#
# Required binaries on PATH:
#   $CODEX_BIN (default /opt/homebrew/bin/codex), npm, node, git, python3
#
# Hard NO-list (same shape as rc3f.sh):
#   - NOT production deploy / NOT rollback / NOT --yolo
#   - tokens never echoed; only token_present=true persists in artifacts

set -euo pipefail

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOGFOOD_REPO="$REPO_ROOT/.dogfood/rc4a3-change-run-tiny"
WORKSPACE="${WORKSPACE:-/tmp/rc4a3-change-real}"
CODEX_BIN="${CODEX_BIN:-/opt/homebrew/bin/codex}"

# Change mode is single-task by definition. Keep budgets tight so a runaway
# Codex run still has a hard ceiling.
BUDGET_MAX_TASKS=1
BUDGET_MAX_INNER_RUNS=2
BUDGET_MAX_CANDIDATES=1
BUDGET_MAX_REPAIR=1
INTEGRATION_TIMEOUT_SEC=600

# -----------------------------------------------------------------------------
# Argv
# -----------------------------------------------------------------------------
RUN_MODE="dry-run"
for arg in "$@"; do
  case "$arg" in
    --run) RUN_MODE="run" ;;
    --dry-run) RUN_MODE="dry-run" ;;
    -h|--help)
      cat <<'HELP'
rc4a3_change_run.sh — RC-4A.3 Real Codex Change-Run Tiny Probe

Usage:
  scripts/rc4a3_change_run.sh                       # dry-run: print commands only
  scripts/rc4a3_change_run.sh --run                 # execute (real Codex)
  CODEX_BIN=/path/to/codex scripts/rc4a3_change_run.sh --run

Env:
  CODEX_BIN          path to codex binary (default: /opt/homebrew/bin/codex)
  WORKSPACE          agent-studio workspace (default: /tmp/rc4a3-change-real)

Defaults to dry-run. --run will:
  - run `npm install` in the tiny dogfood project (~30-60s)
  - consume ~25-50k Codex tokens (1 task × 1 candidate cap)
  - leave a real git commit on `agentic/change/<change_id>` with
    `Change-Id:` + `Source-Change-Request:` trailers
  - write applied-change.json + delivery-report.md under
    <project>/.agent/changes/<change_id>/
HELP
      exit 0
      ;;
    *) echo "rc4a3_change_run.sh: unknown arg '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
say()  { printf '\n[rc4a3] %s\n' "$*"; }
warn() { printf '\n[rc4a3 WARN] %s\n' "$*" >&2; }
do_or_print() {
  if [[ "$RUN_MODE" == "run" ]]; then
    printf '\n$ %s\n' "$*"
    eval "$*"
  else
    printf '\n[dry-run] $ %s\n' "$*"
  fi
}

# -----------------------------------------------------------------------------
# Pre-flight
# -----------------------------------------------------------------------------
say "RC-4A.3 Real Codex Change-Run Tiny Probe — mode: $RUN_MODE"
say "repo:        $REPO_ROOT"
say "dogfood:     $DOGFOOD_REPO"
say "workspace:   $WORKSPACE"
say "codex bin:   $CODEX_BIN"

if [[ ! -x "$CODEX_BIN" ]]; then
  warn "CODEX_BIN '$CODEX_BIN' is not executable. Try: which codex"
  exit 1
fi
say "codex --version: $("$CODEX_BIN" --version 2>&1 | head -1)"

if ! command -v npm >/dev/null 2>&1; then
  warn "npm not on PATH (required for npm install + npm run build)"
  exit 1
fi
say "npm --version: $(npm --version 2>&1 | head -1)"
say "node --version: $(node --version 2>&1 | head -1)"

if ! command -v git >/dev/null 2>&1; then
  warn "git not on PATH (change run requires git for safe apply + commit)"
  exit 1
fi

if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not on PATH (required for parsing artifact JSON in summary)"
  exit 1
fi

if [[ ! -d "$DOGFOOD_REPO" ]]; then
  warn "dogfood repo missing at $DOGFOOD_REPO"
  exit 1
fi
if [[ ! -f "$DOGFOOD_REPO/change-request.md" ]]; then
  warn "change-request.md missing at $DOGFOOD_REPO/change-request.md"
  exit 1
fi

# -----------------------------------------------------------------------------
# Cost / blast radius warning
# -----------------------------------------------------------------------------
cat <<'WARN'

------------------------------------------------------------------------------
RC-4A.3 COST + BLAST-RADIUS WARNING

  - npm install:     ~30-60s; ~250-300 MB node_modules in the tiny project.
  - Codex tokens:    ~25-50k (1 task × 1 candidate cap, 1 repair).
  - Wall clock:      ~3-7 min Codex + ~60s setup.
  - Vercel:          NOT touched. Change Request Mode in RC-4A.3 stops at
                     "applied + committed + delivery-report written."
  - Real LLM:        NOT called other than Codex itself.
  - LLM-as-judge:    NOT used.
  - Production:      NOT touched. NOT rollback. NOT smoke check.
  - Demo matrix:     NOT started (that's RC-4B).
  - Detector:        NOT touched (RC-3F is paused).

Dry-run prints commands only. 0 tokens, 0 commits, 0 npm installs.
------------------------------------------------------------------------------
WARN

# -----------------------------------------------------------------------------
# Plan
# -----------------------------------------------------------------------------
say "Plan:"
echo "  1. Wipe workspace at $WORKSPACE"
echo "  2. agent-studio init"
echo "  3. agent-studio new \"creator notes\""
echo "  4. Seed project dir with tiny Next.js app + agent-studio.yaml"
echo "  5. npm install in the project"
echo "  6. git init + baseline commit"
echo "  7. agent-studio change new --from <project>/change-request.md"
echo "  8. agent-studio change run latest"
echo "  9. agent-studio change status latest --json"
echo " 10. agent-studio change show latest --json"
echo " 11. agent-studio change validate latest --json"
echo " 12. Print: commit hash + Change-Id trailer + delivery-report path + applied-change path"
echo " 13. Confirm: npm run build still passes on the change branch"

# -----------------------------------------------------------------------------
# Step 1-3: workspace + project setup
# -----------------------------------------------------------------------------
do_or_print "rm -rf '$WORKSPACE'"
do_or_print "mkdir -p '$WORKSPACE'"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' init"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' new 'creator notes'"

if [[ "$RUN_MODE" == "run" ]]; then
  PROJECT_DIR="$(ls -d "$WORKSPACE/.agent-studio/projects/"*/ 2>/dev/null | head -1 | sed 's:/$::')"
  if [[ -z "${PROJECT_DIR:-}" ]]; then
    warn "no project dir created"
    exit 1
  fi
  say "project dir: $PROJECT_DIR"
else
  PROJECT_DIR="<workspace>/.agent-studio/projects/creator-notes-XXXXXX"
  say "project dir (dry-run placeholder): $PROJECT_DIR"
fi

# -----------------------------------------------------------------------------
# Step 4: copy seed files
# -----------------------------------------------------------------------------
for item in package.json next.config.mjs tsconfig.json .gitignore change-request.md; do
  do_or_print "cp '$DOGFOOD_REPO/$item' '$PROJECT_DIR/'"
done
do_or_print "cp -r '$DOGFOOD_REPO/app' '$PROJECT_DIR/'"
do_or_print "cp -r '$DOGFOOD_REPO/components' '$PROJECT_DIR/'"

# -----------------------------------------------------------------------------
# Step 4 (continued): RC-4A.3 agent-studio.yaml
# Same shape as RC-3F minus deploy/smoke (Change Request Mode does not
# touch deploy in RC-4A.3). The integration command list is derived by the
# runtime from package.json scripts (typecheck/build).
# -----------------------------------------------------------------------------
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
    max_corrective_tasks: 0

integration:
  every_n_tasks: 1
  run_at_session_end: true
  timeout_sec: $INTEGRATION_TIMEOUT_SEC

deploy:
  enabled: false
YAML
)"

if [[ "$RUN_MODE" == "run" ]]; then
  printf '%s\n' "$YAML_BODY" > "$PROJECT_DIR/agent-studio.yaml"
  say "wrote $PROJECT_DIR/agent-studio.yaml"
else
  say "agent-studio.yaml that will be written:"
  printf '%s\n' "$YAML_BODY" | sed 's/^/    /'
fi

# Belt-and-suspenders safety: never enable deploy in RC-4A.3.
if [[ "$RUN_MODE" == "run" ]]; then
  if grep -qE '^  enabled: true' "$PROJECT_DIR/agent-studio.yaml"; then
    warn "deploy enabled detected in RC-4A.3 yaml — refusing"
    exit 1
  fi
fi

# -----------------------------------------------------------------------------
# Step 5: install Node deps
# -----------------------------------------------------------------------------
do_or_print "(cd '$PROJECT_DIR' && npm install --no-audit --no-fund)"

# Confirm baseline build passes BEFORE Codex touches anything. If this
# fails, the seed is broken — abort before burning tokens.
do_or_print "(cd '$PROJECT_DIR' && npm run build)"

# -----------------------------------------------------------------------------
# Step 6: git baseline
# -----------------------------------------------------------------------------
do_or_print "(cd '$PROJECT_DIR' && git init -q -b main)"
do_or_print "(cd '$PROJECT_DIR' && git config user.email rc4a3@dogfood)"
do_or_print "(cd '$PROJECT_DIR' && git config user.name rc4a3)"
do_or_print "(cd '$PROJECT_DIR' && git add -A)"
do_or_print "(cd '$PROJECT_DIR' && git -c commit.gpgsign=false commit -q -m 'rc4a3 baseline')"

# -----------------------------------------------------------------------------
# Step 7-11: change new + change run + status + show + validate
# -----------------------------------------------------------------------------
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' change new --from '$PROJECT_DIR/change-request.md'"

if [[ "$RUN_MODE" != "run" ]]; then
  say "DRY-RUN finished. Re-run with --run to actually consume tokens + apply."
  exit 0
fi

# -----------------------------------------------------------------------------
# Real run (only --run)
# -----------------------------------------------------------------------------
say "STARTING change run — Codex tokens ahead. Press Ctrl+C in the next 5s to abort..."
sleep 5

set +e
"$REPO_ROOT/agent-studio" --root "$WORKSPACE" change run latest --json > "$WORKSPACE/change-run.out.json" 2> "$WORKSPACE/change-run.err.log"
CHANGE_RUN_EXIT=$?
set -e
say "change run exit: $CHANGE_RUN_EXIT"
cat "$WORKSPACE/change-run.out.json"
say "change run stderr (tail):"
tail -40 "$WORKSPACE/change-run.err.log" || true

do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' change status latest --json"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' change show latest --json"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' change validate latest --json"

# -----------------------------------------------------------------------------
# Post-run summary
# -----------------------------------------------------------------------------
CHANGE_DIR="$(ls -d "$PROJECT_DIR/.agent/changes/"*/ 2>/dev/null | head -1 | sed 's:/$::')"
if [[ -n "${CHANGE_DIR:-}" ]]; then
  say "change dir: $CHANGE_DIR"
  if [[ -f "$CHANGE_DIR/applied-change.json" ]]; then
    say "applied-change.json:"
    python3 - <<PY || true
import json
with open("$CHANGE_DIR/applied-change.json") as fh:
    d = json.load(fh)
print(f"  schema_version:  {d.get('schema_version')}")
print(f"  change_id:       {d.get('change_id')}")
print(f"  candidate:       {d.get('candidate')}")
print(f"  run_id:          {d.get('run_id')}")
print(f"  base_commit:     {d.get('base_commit')}")
print(f"  applied_to:      {d.get('applied_to_commit')}")
commit = d.get("commit") or {}
print(f"  commit.branch:   {commit.get('branch')}")
print(f"  commit.sha:      {commit.get('sha')}")
print(f"  promotion:       {d.get('promotion_decision')}")
print(f"  files_touched:   {d.get('files_touched')}")
PY
  else
    warn "applied-change.json missing (change probably needs-human-review or failed)"
  fi
  if [[ -f "$CHANGE_DIR/delivery-report.md" ]]; then
    say "delivery-report.md (head):"
    head -40 "$CHANGE_DIR/delivery-report.md" || true
    say "(full path: $CHANGE_DIR/delivery-report.md)"
  else
    warn "delivery-report.md missing"
  fi
fi

# Confirm Change-Id trailer is present on HEAD of the change branch.
say "git log on change branch (HEAD commit message):"
(cd "$PROJECT_DIR" && git log -1 --pretty=fuller HEAD) || true

# Final confirmation: the change branch still builds.
say "Confirming `npm run build` still passes on the change branch..."
BUILD_EXIT=0
( cd "$PROJECT_DIR" && npm run build ) || BUILD_EXIT=$?
if [[ "$BUILD_EXIT" -eq 0 ]]; then
  say "npm run build: exit 0 — change branch still builds."
else
  warn "npm run build: exit $BUILD_EXIT — change introduced a build regression."
fi

cat <<'POST'

------------------------------------------------------------------------------
POST-RUN TRIAGE — RC-4A.3

  A. change run completed + applied-change.json exists + delivery-report.md
     exists + Change-Id trailer on HEAD + npm run build exit 0
     → RC-4A.3 verified. Change Request Mode works with real Codex.
     → Next: write docs/rc4a3-success-report.md, then RC-4B (3-project
       demo matrix).

  B. change run = needs-human-review → expected first failure surface.
     Inspect the review queue for what stopped Codex:
       agent-studio --root <workspace> autonomous reviews list
       agent-studio --root <workspace> autonomous reviews show <id>
     Likely culprits:
       - Codex changed package.json or added a dep (out_of_scope_changes)
         → tighten change-request.md non-goals
       - Codex changed tsconfig / next.config / .gitignore
         → tighten change-request.md non-goals
       - Codex's patch failed `git apply --check`
         → log shows the conflict; usually a base_commit mismatch
       - Codex added a new build script
         → tighten "Do not introduce a new build script" wording

  C. change run = failed (Apply Gate refused) → real Codex output but
     it didn't pass a hard gate. Inspect:
       <project>/.agent/runs/<run_id>/promotion-report.json
       <project>/.agent/runs/<run_id>/candidates/<id>/{patch.diff,score.json}
     Common causes documented in docs/rc4a3-prep-report.md.

  D. Codex CLI failure (timeout, sandbox refusal, env) → no product change
     yet. Confirm CODEX_BIN, sandbox=workspace-write, ask_for_approval=
     on-request. Same operator pattern as rc2b2.sh / rc3a-3f.

  E. npm run build fails after the change → Codex passed gates but
     introduced a runtime build error. This is the most interesting
     failure mode — file it as an evidence-grounded learning before
     starting the demo matrix.
------------------------------------------------------------------------------
POST
