#!/usr/bin/env bash
# RC-2B.2 dogfood runner.
#
# Goal: in `.dogfood/rc2-creator-tracker/`, run real Codex patch worker
# against ALL 3 dogfood tasks (RC-2B.1 verified just task-001). Same repo,
# same agent-studio.yaml, just bumps the budget cap to 3 tasks.
#
# DEFAULT IS DRY-RUN. Pass --run to actually execute.
# Dry-run prints every command + the agent-studio.yaml that will be used.
# --run does NOT auto-confirm; it still requires you to be sitting at the
# terminal, since `autonomous start` will burn real Codex tokens.
#
# Required env: CODEX_BIN (default: /opt/homebrew/bin/codex). Override
# if your codex install lives elsewhere; we'll print `which codex` if
# CODEX_BIN doesn't exist on disk so you can fix the path.
#
# Hard NO-list:
#   - does NOT install codex (manual step on your part)
#   - does NOT save or echo any auth tokens
#   - does NOT run Vercel deploy / smoke / rollback (deploy.enabled=false enforced)
#   - does NOT consume Codex tokens by default (dry-run is the default)
#   - does NOT touch any repo other than .dogfood/rc2-creator-tracker/

set -euo pipefail

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOGFOOD_REPO="$REPO_ROOT/.dogfood/rc2-creator-tracker"
WORKSPACE="${WORKSPACE:-/tmp/rc2b2-real}"
CODEX_BIN="${CODEX_BIN:-/opt/homebrew/bin/codex}"

# Budget for RC-2B.2: ALL 3 dogfood tasks, conservative repair caps.
BUDGET_MAX_TASKS=3
BUDGET_MAX_INNER_RUNS=3
BUDGET_MAX_CANDIDATES=1
BUDGET_MAX_REPAIR=1
BUDGET_MAX_ABANDONED=1
BUDGET_MAX_CORRECTIVE=1

# -----------------------------------------------------------------------------
# Argv parsing
# -----------------------------------------------------------------------------
RUN_MODE="dry-run"
for arg in "$@"; do
  case "$arg" in
    --run) RUN_MODE="run" ;;
    --dry-run) RUN_MODE="dry-run" ;;
    -h|--help)
      cat <<'HELP'
rc2b2.sh — RC-2B.2 dogfood runner

Usage:
  scripts/rc2b2.sh                       # dry-run: print commands only
  scripts/rc2b2.sh --run                 # execute (will burn Codex tokens)
  CODEX_BIN=/path/to/codex scripts/rc2b2.sh --run

Env:
  CODEX_BIN      path to codex binary (default: /opt/homebrew/bin/codex)
  WORKSPACE      agent-studio workspace path (default: /tmp/rc2b2-real)

Defaults to dry-run. --run will actually consume Codex tokens.
HELP
      exit 0
      ;;
    *) echo "rc2b2.sh: unknown arg '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

# -----------------------------------------------------------------------------
# Pretty printers (no color codes — works in any terminal / log capture)
# -----------------------------------------------------------------------------
say()   { printf '\n[rc2b2] %s\n' "$*"; }
warn()  { printf '\n[rc2b2 WARN] %s\n' "$*" >&2; }
do_or_print() {
  if [[ "$RUN_MODE" == "run" ]]; then
    printf '\n$ %s\n' "$*"
    eval "$*"
  else
    printf '\n[dry-run] $ %s\n' "$*"
  fi
}

# -----------------------------------------------------------------------------
# Pre-flight: codex binary visible
# -----------------------------------------------------------------------------
say "RC-2B.2 dogfood — mode: $RUN_MODE"
say "repo:        $REPO_ROOT"
say "dogfood:     $DOGFOOD_REPO"
say "workspace:   $WORKSPACE"
say "codex bin:   $CODEX_BIN"

if [[ ! -x "$CODEX_BIN" ]]; then
  warn "CODEX_BIN '$CODEX_BIN' is not executable."
  warn "  Try: which codex"
  warn "  Then: CODEX_BIN=\$(which codex) $0 $RUN_MODE"
  exit 1
fi
codex_version="$("$CODEX_BIN" --version 2>&1 | head -1)"
say "codex --version: $codex_version"

if [[ ! -d "$DOGFOOD_REPO" ]]; then
  warn "dogfood repo missing at $DOGFOOD_REPO"
  exit 1
fi
say "dogfood requirements.md: $(wc -l < "$DOGFOOD_REPO/requirements.md") lines"

# -----------------------------------------------------------------------------
# Token-spend warning
# -----------------------------------------------------------------------------
cat <<'WARN'

------------------------------------------------------------------------------
TOKEN-SPEND WARNING

RC-2B.1 (1 task) used ~25-40k tokens over ~5m37s on the user's account.
RC-2B.2 runs 3 tasks in sequence. Realistic estimate: 75-120k tokens total
+ ~15-20 minutes of wall-clock time. Make sure your weekly limit can absorb
this BEFORE you re-run with --run.

The dry-run mode prints what would happen WITHOUT consuming tokens. If you
just want to see the commands or update the YAML, leave the default mode.
------------------------------------------------------------------------------
WARN

# -----------------------------------------------------------------------------
# Plan
# -----------------------------------------------------------------------------
say "Plan:"
echo "  1. Wipe workspace at $WORKSPACE (rm -rf, fresh sqlite)"
echo "  2. agent-studio init under that workspace"
echo "  3. agent-studio new --from $DOGFOOD_REPO/requirements.md"
echo "  4. Seed project dir with dogfood files (package.json / scripts / src / .gitignore)"
echo "  5. Write RC-2B.2 agent-studio.yaml with:"
echo "       agentic.patch_worker = codex"
echo "       agentic.codex.command = $CODEX_BIN"
echo "       agentic.codex.sandbox = workspace-write"
echo "       agentic.codex.ask_for_approval = on-request"
echo "       autonomous.budgets.max_tasks_per_session = $BUDGET_MAX_TASKS"
echo "       autonomous.budgets.max_total_inner_runs  = $BUDGET_MAX_INNER_RUNS"
echo "       autonomous.budgets.max_candidates_per_task = $BUDGET_MAX_CANDIDATES"
echo "       autonomous.budgets.max_repair_attempts_per_candidate = $BUDGET_MAX_REPAIR"
echo "       autonomous.budgets.max_abandoned_tasks    = $BUDGET_MAX_ABANDONED"
echo "       autonomous.budgets.max_corrective_tasks   = $BUDGET_MAX_CORRECTIVE"
echo "       deploy.enabled = false      ← real Vercel stays OFF"
echo "  6. git init + baseline commit in the new project"
echo "  7. autonomous preflight (must report all PASS, including codex_cli_available)"
echo "  8. autonomous start (THIS is where Codex tokens are consumed; ~15-20 min)"
echo "  9. autonomous status / logs --tail 80 / reviews list"
echo " 10. autonomous validate-artifacts --json"
echo " 11. Print final commit hashes (one per completed task)"

# -----------------------------------------------------------------------------
# Step 1-4: workspace + project setup
# -----------------------------------------------------------------------------
do_or_print "rm -rf '$WORKSPACE'"
do_or_print "mkdir -p '$WORKSPACE'"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' init"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' new --from '$DOGFOOD_REPO/requirements.md'"

# After `new`, the project dir is created with a derived suffix. We resolve it
# at run time so the script doesn't hard-code a hash.
if [[ "$RUN_MODE" == "run" ]]; then
  PROJECT_DIR="$(ls -d "$WORKSPACE/.agent-studio/projects/"*/ 2>/dev/null | head -1 | sed 's:/$::')"
  if [[ -z "${PROJECT_DIR:-}" ]]; then
    warn "no project dir created — `new --from` failed?"
    exit 1
  fi
  say "project dir: $PROJECT_DIR"
else
  PROJECT_DIR="<workspace>/.agent-studio/projects/creator-project-tracker-XXXXXX"
  say "project dir (dry-run placeholder): $PROJECT_DIR"
fi

do_or_print "cp '$DOGFOOD_REPO/package.json' '$PROJECT_DIR/'"
do_or_print "cp -r '$DOGFOOD_REPO/scripts' '$PROJECT_DIR/'"
do_or_print "cp -r '$DOGFOOD_REPO/src' '$PROJECT_DIR/'"
do_or_print "cp '$DOGFOOD_REPO/.gitignore' '$PROJECT_DIR/'"

# -----------------------------------------------------------------------------
# Step 5: RC-2B.2-specific agent-studio.yaml (NOT the dogfood-repo's; this is
# the per-project config the autonomous controller will read). We write it
# inline so the absolute CODEX_BIN path is baked in.
# -----------------------------------------------------------------------------
YAML_BODY="$(cat <<YAML
agentic:
  patch_worker: codex
  codex:
    command: $CODEX_BIN
    sandbox: workspace-write
    ask_for_approval: on-request
    timeout_sec: 600
    max_prompt_chars: 60000

autonomous:
  budgets:
    max_tasks_per_session: $BUDGET_MAX_TASKS
    max_total_inner_runs: $BUDGET_MAX_INNER_RUNS
    max_candidates_per_task: $BUDGET_MAX_CANDIDATES
    max_repair_attempts_per_candidate: $BUDGET_MAX_REPAIR
    max_abandoned_tasks: $BUDGET_MAX_ABANDONED
    max_corrective_tasks: $BUDGET_MAX_CORRECTIVE

deploy:
  enabled: false
  target: vercel
YAML
)"

if [[ "$RUN_MODE" == "run" ]]; then
  printf '%s\n' "$YAML_BODY" > "$PROJECT_DIR/agent-studio.yaml"
  say "wrote $PROJECT_DIR/agent-studio.yaml"
else
  say "agent-studio.yaml that will be written:"
  printf '%s\n' "$YAML_BODY" | sed 's/^/    /'
fi

# Belt-and-suspenders: deploy.enabled MUST be false at this point.
if [[ "$RUN_MODE" == "run" ]]; then
  if grep -q '^  enabled: true' "$PROJECT_DIR/agent-studio.yaml"; then
    warn "deploy.enabled=true detected after write — refusing to continue"
    exit 1
  fi
fi

# -----------------------------------------------------------------------------
# Step 6: git baseline
# -----------------------------------------------------------------------------
do_or_print "(cd '$PROJECT_DIR' && git init -q -b main)"
do_or_print "(cd '$PROJECT_DIR' && git config user.email rc2b2@dogfood)"
do_or_print "(cd '$PROJECT_DIR' && git config user.name rc2b2)"
do_or_print "(cd '$PROJECT_DIR' && git add -A)"
do_or_print "(cd '$PROJECT_DIR' && git -c commit.gpgsign=false commit -q -m 'rc2b2 baseline')"

# -----------------------------------------------------------------------------
# Step 7: preflight
# -----------------------------------------------------------------------------
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous preflight"

if [[ "$RUN_MODE" != "run" ]]; then
  say "DRY-RUN finished. To actually execute, re-run with --run."
  say "Re-running with --run will consume real Codex tokens (~75-120k estimated)."
  exit 0
fi

# -----------------------------------------------------------------------------
# Step 8-10: the actual run (only reached in --run mode)
# -----------------------------------------------------------------------------
say "STARTING autonomous run — this consumes Codex tokens."
say "Estimated wall-clock: ~15-20 min. Estimated tokens: 75-120k."
say "Press Ctrl+C in the next 5s to abort..."
sleep 5

do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous start"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous status"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous logs --tail 80"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous reviews list"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous validate-artifacts --json"

# -----------------------------------------------------------------------------
# Step 11: post-run summary
# -----------------------------------------------------------------------------
say "Post-run git history (per-task commits on the session branch):"
(cd "$PROJECT_DIR" && git log --oneline -10) || true

cat <<'POST'

------------------------------------------------------------------------------
POST-RUN TRIAGE

  A. All 3 tasks completed + 3 commits + validate-artifacts ok=true
     → RC-2B.2 verified. Next: consider RC-2C (real Vercel preview).

  B. Some tasks completed, others paused (review queue non-empty)
     → Inspect review item evidence; only fix prompt / context /
       repair-loop / eval-wiring issues per spec.

  C. Codex env failure (auth / rate limit / network)
     → No product code change. Fix env, re-run rc2b2.sh.
------------------------------------------------------------------------------
POST
