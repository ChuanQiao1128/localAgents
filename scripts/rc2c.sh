#!/usr/bin/env bash
# RC-2C dogfood runner — real Vercel preview deploy + smoke check.
#
# Goal: same `.dogfood/rc2-creator-tracker/` repo, run all 3 tasks
# (real Codex), then auto-deploy to Vercel preview and run smoke
# checks. RC-2C is the first time the deploy → smoke ladder runs
# against real Vercel CLI — the test suite already exercised it with
# fakes.
#
# DEFAULT IS DRY-RUN. Pass --run to actually execute.
# Dry-run prints commands + the agent-studio.yaml that will be used.
# --run will burn Codex tokens AND create a real Vercel preview
# deployment in your `pianxing11281128s-projects` scope.
#
# Required env (loaded from ~/.local-agent-vercel.env):
#   VERCEL_TOKEN      Vercel personal token
#   VERCEL_ORG_ID     team / org id
#   VERCEL_PROJECT_ID linked project id
#
# Required binary:
#   CODEX_BIN         default /opt/homebrew/bin/codex
#   vercel            must be on PATH
#
# Hard NO-list:
#   - does NOT do production deploy (deploy.environment=preview, prod=false)
#   - does NOT enable rollback (deploy.rollback.enabled=false)
#   - does NOT echo or persist any token value (only token_present: bool
#     lands in artifacts; --token is stripped via existing redact path)
#   - does NOT install codex or vercel
#   - does NOT use --dangerously-bypass-approvals-and-sandbox / --yolo
#   - does NOT touch any repo other than .dogfood/rc2-creator-tracker/

set -euo pipefail

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOGFOOD_REPO="$REPO_ROOT/.dogfood/rc2-creator-tracker"
WORKSPACE="${WORKSPACE:-/tmp/rc2c-real}"
CODEX_BIN="${CODEX_BIN:-/opt/homebrew/bin/codex}"
VERCEL_ENV_FILE="${VERCEL_ENV_FILE:-$HOME/.local-agent-vercel.env}"

# Budget: same shape as RC-2B.2 (3 tasks). Per RC-2B.2 Observation A,
# max_candidates_per_task is not actually wired into candidate_count;
# documented but not fixed.
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
rc2c.sh — RC-2C real Vercel preview dogfood runner

Usage:
  scripts/rc2c.sh                       # dry-run: print commands only
  scripts/rc2c.sh --run                 # execute real Codex + real Vercel preview

Env:
  CODEX_BIN          path to codex binary (default: /opt/homebrew/bin/codex)
  WORKSPACE          agent-studio workspace path (default: /tmp/rc2c-real)
  VERCEL_ENV_FILE    file to source for Vercel env (default: ~/.local-agent-vercel.env)

Defaults to dry-run. --run will:
  - consume ~75-120k Codex tokens (3-task RC-2B.2-equivalent re-run)
  - create a real Vercel preview deployment in your linked scope
  - run a real HTTP smoke check against the deployed URL

Note: RC-2C uses a fresh workspace (per spec preference). If you want
to validate ONLY the deploy + smoke ladder without re-burning Codex
tokens, you can instead run:

  agent-studio --root /tmp/rc2b2-real autonomous deploy --yes --json

against the existing RC-2B.2 workspace (after ensuring its
agent-studio.yaml has deploy.enabled=true). Not the default path
because the RC-2B.2 workspace's yaml had deploy.enabled=false.
HELP
      exit 0
      ;;
    *) echo "rc2c.sh: unknown arg '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
say()   { printf '\n[rc2c] %s\n' "$*"; }
warn()  { printf '\n[rc2c WARN] %s\n' "$*" >&2; }
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
say "RC-2C dogfood — mode: $RUN_MODE"
say "repo:        $REPO_ROOT"
say "dogfood:     $DOGFOOD_REPO"
say "workspace:   $WORKSPACE"
say "codex bin:   $CODEX_BIN"
say "vercel env:  $VERCEL_ENV_FILE"

if [[ ! -x "$CODEX_BIN" ]]; then
  warn "CODEX_BIN '$CODEX_BIN' is not executable. Try: which codex"
  exit 1
fi
say "codex --version: $("$CODEX_BIN" --version 2>&1 | head -1)"

if ! command -v vercel >/dev/null 2>&1; then
  warn "vercel CLI not on PATH. Install via: npm i -g vercel"
  exit 1
fi
say "vercel --version: $(vercel --version 2>&1 | head -1)"

if [[ ! -f "$VERCEL_ENV_FILE" ]]; then
  warn "VERCEL_ENV_FILE not found at $VERCEL_ENV_FILE"
  warn "Expected to contain: VERCEL_TOKEN / VERCEL_ORG_ID / VERCEL_PROJECT_ID"
  exit 1
fi
# Source the env file in the current shell so subprocesses inherit
# VERCEL_TOKEN etc. without echoing the values. This is the ONLY place
# the token is read; nothing in this script ever prints $VERCEL_TOKEN.
# shellcheck disable=SC1090
source "$VERCEL_ENV_FILE"

missing=()
for var in VERCEL_TOKEN VERCEL_ORG_ID VERCEL_PROJECT_ID; do
  if [[ -z "${!var:-}" ]]; then
    missing+=("$var")
  fi
done
if [[ ${#missing[@]} -gt 0 ]]; then
  warn "Vercel env missing: ${missing[*]}"
  exit 1
fi
say "Vercel env: VERCEL_TOKEN=present  VERCEL_ORG_ID=present  VERCEL_PROJECT_ID=present"
say "(values intentionally not echoed)"

if [[ ! -d "$DOGFOOD_REPO" ]]; then
  warn "dogfood repo missing at $DOGFOOD_REPO"
  exit 1
fi

# -----------------------------------------------------------------------------
# Cost / blast-radius warning
# -----------------------------------------------------------------------------
cat <<'WARN'

------------------------------------------------------------------------------
RC-2C COST + BLAST-RADIUS WARNING

  - Codex tokens:  ~75-120k (re-runs the 3-task RC-2B.2 sequence with
                   the same dogfood requirements; nothing in the patch
                   worker changed since RC-2B.2)
  - Wall clock:    ~15-20 min for Codex + ~30-90s for Vercel deploy +
                   ~5s for smoke check
  - Vercel:        ONE preview deployment created in your linked
                   scope (pianxing11281128s-projects). Preview URL
                   stays public until deleted via `vercel rm`.
  - Production:    NOT touched. deploy.environment=preview,
                   vercel.prod=false hard-coded below.
  - Rollback:      NOT enabled. preview-only env, no rollback CLI call.

Dry-run (default) only prints what would happen — 0 tokens, 0 deploys.
------------------------------------------------------------------------------
WARN

# -----------------------------------------------------------------------------
# Plan
# -----------------------------------------------------------------------------
say "Plan:"
echo "  1. Wipe workspace at $WORKSPACE"
echo "  2. agent-studio init under that workspace"
echo "  3. agent-studio new --from $DOGFOOD_REPO/requirements.md"
echo "  4. Seed project dir with dogfood files (package.json / scripts / src / .gitignore)"
echo "  5. Write RC-2C agent-studio.yaml:"
echo "       agentic.patch_worker = codex"
echo "       agentic.codex.command = $CODEX_BIN"
echo "       autonomous.budgets.max_tasks_per_session = $BUDGET_MAX_TASKS"
echo "       deploy.enabled = true                          ← real Vercel ON"
echo "       deploy.target  = vercel"
echo "       deploy.environment = preview                   ← preview only"
echo "       deploy.vercel.prod = false                     ← double-confirm"
echo "       deploy.vercel.inspect = true"
echo "       deploy.smoke_checks.enabled = true"
echo "       deploy.smoke_checks.checks = [{name=home, path=/, expect_status=[200]}]"
echo "       deploy.rollback.enabled = false                ← preview, no rollback"
echo "  6. git init + baseline commit"
echo "  7. autonomous preflight (must report all PASS)"
echo "  8. autonomous start (real Codex × 3 tasks → real Vercel preview deploy → real smoke)"
echo "  9. autonomous status / logs --tail 80 / reviews list"
echo " 10. autonomous validate-artifacts --json"
echo " 11. Print: 3 commit hashes + deployment URL + smoke status"

# -----------------------------------------------------------------------------
# Step 1-4: workspace + project setup
# -----------------------------------------------------------------------------
do_or_print "rm -rf '$WORKSPACE'"
do_or_print "mkdir -p '$WORKSPACE'"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' init"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' new --from '$DOGFOOD_REPO/requirements.md'"

if [[ "$RUN_MODE" == "run" ]]; then
  PROJECT_DIR="$(ls -d "$WORKSPACE/.agent-studio/projects/"*/ 2>/dev/null | head -1 | sed 's:/$::')"
  if [[ -z "${PROJECT_DIR:-}" ]]; then
    warn "no project dir created"
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
# RC-2C.1.5: vercel.json tells Vercel to serve dist/ (not the repo
# root). Without it, the first deploy serves the unbuilt source which
# silently misses the dogfood acceptance criteria.
do_or_print "cp '$DOGFOOD_REPO/vercel.json' '$PROJECT_DIR/'"

# Vercel link: the project dir needs .vercel/project.json so the
# vercel CLI knows which project to deploy to. We copy from the
# dogfood repo (which has it according to the user's pre-flight).
if [[ -d "$DOGFOOD_REPO/.vercel" ]]; then
  do_or_print "cp -r '$DOGFOOD_REPO/.vercel' '$PROJECT_DIR/'"
else
  warn ".vercel/ not found in dogfood repo — vercel deploy may fail with 'project not linked'"
  warn "  Fix: cd $DOGFOOD_REPO && vercel link --token \"\$VERCEL_TOKEN\" --scope <your-scope>"
  warn "  Re-run rc2c.sh after that."
fi

# -----------------------------------------------------------------------------
# Step 5: RC-2C agent-studio.yaml
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
  enabled: true
  target: vercel
  environment: preview
  project_path: "."
  vercel:
    prod: false
    prebuilt: false
    build_before_deploy: false
    inspect: true
    inspect_timeout: "5m"
    token_env: "VERCEL_TOKEN"
    org_id_env: "VERCEL_ORG_ID"
    project_id_env: "VERCEL_PROJECT_ID"
  smoke_checks:
    enabled: true
    timeout_sec: 10
    retries: 0
    checks:
      - name: home
        method: GET
        path: /
        expect_status: [200]
  rollback:
    enabled: false
    production_only: true
    trigger_on_smoke_failure: false
YAML
)"

if [[ "$RUN_MODE" == "run" ]]; then
  printf '%s\n' "$YAML_BODY" > "$PROJECT_DIR/agent-studio.yaml"
  say "wrote $PROJECT_DIR/agent-studio.yaml"
else
  say "agent-studio.yaml that will be written:"
  printf '%s\n' "$YAML_BODY" | sed 's/^/    /'
fi

# Belt-and-suspenders: confirm deploy.environment != production AND
# vercel.prod != true AND rollback.enabled != true.
if [[ "$RUN_MODE" == "run" ]]; then
  if grep -qE '^  environment: production' "$PROJECT_DIR/agent-studio.yaml"; then
    warn "environment=production detected — refusing to continue (RC-2C is preview-only)"
    exit 1
  fi
  if grep -qE '^    prod: true' "$PROJECT_DIR/agent-studio.yaml"; then
    warn "vercel.prod=true detected — refusing to continue"
    exit 1
  fi
  if grep -qE '^  enabled: true' "$PROJECT_DIR/agent-studio.yaml" \
       && grep -qE '^  rollback:' "$PROJECT_DIR/agent-studio.yaml" \
       && awk '/^  rollback:/{flag=1; next} flag && /^    enabled: true/{print; exit}' "$PROJECT_DIR/agent-studio.yaml" | grep -q .; then
    warn "rollback.enabled=true detected — refusing to continue (RC-2C is rollback-disabled)"
    exit 1
  fi
fi

# -----------------------------------------------------------------------------
# Step 6: git baseline
# -----------------------------------------------------------------------------
do_or_print "(cd '$PROJECT_DIR' && git init -q -b main)"
do_or_print "(cd '$PROJECT_DIR' && git config user.email rc2c@dogfood)"
do_or_print "(cd '$PROJECT_DIR' && git config user.name rc2c)"
do_or_print "(cd '$PROJECT_DIR' && git add -A)"
do_or_print "(cd '$PROJECT_DIR' && git -c commit.gpgsign=false commit -q -m 'rc2c baseline')"

# -----------------------------------------------------------------------------
# Step 7: preflight (cheap, no Codex / Vercel calls)
# -----------------------------------------------------------------------------
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous preflight"

if [[ "$RUN_MODE" != "run" ]]; then
  say "DRY-RUN finished. To actually execute, re-run with --run."
  say "Re-run will consume Codex tokens AND create a real Vercel preview deployment."
  exit 0
fi

# -----------------------------------------------------------------------------
# Step 8-10: actual run (only --run)
# -----------------------------------------------------------------------------
say "STARTING autonomous run — Codex tokens + real Vercel deploy ahead."
say "Press Ctrl+C in the next 5s to abort..."
sleep 5

# Pass through Vercel env to the autonomous start subprocess. The
# patch worker (Codex) and the deploy adapter (vercel CLI) BOTH need
# inherited env. agent-studio is invoked as a normal subprocess so
# the current shell's env is inherited automatically.
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous start"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous status"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous logs --tail 80"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous reviews list"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous validate-artifacts --json"

# -----------------------------------------------------------------------------
# Step 11: post-run summary — pull deployment URL + smoke status from artifacts
# -----------------------------------------------------------------------------
say "Per-task git history:"
(cd "$PROJECT_DIR" && git log --oneline -10) || true

SESS_DIR="$(ls -d "$PROJECT_DIR/.agent/autonomous/sessions/"*/ 2>/dev/null | head -1 | sed 's:/$::')"
if [[ -n "${SESS_DIR:-}" ]]; then
  DEPLOY_JSON="$(ls -t "$SESS_DIR/deployments/"*/deployment.json 2>/dev/null | head -1 || true)"
  SMOKE_JSON="$(ls -t "$SESS_DIR/smoke-checks/"*/smoke-check.json 2>/dev/null | head -1 || true)"
  if [[ -n "${DEPLOY_JSON:-}" ]]; then
    say "deployment.json: $DEPLOY_JSON"
    python3 -c "import json,sys; d=json.load(open('$DEPLOY_JSON')); print(f\"  status: {d.get('status')}\"); print(f\"  url:    {d.get('deployment_url')}\")" || true
  else
    warn "no deployment.json found — deploy may have been skipped or failed before write"
  fi
  if [[ -n "${SMOKE_JSON:-}" ]]; then
    say "smoke-check.json: $SMOKE_JSON"
    python3 -c "import json,sys; s=json.load(open('$SMOKE_JSON')); print(f\"  status: {s.get('status')}\"); print(f\"  failure: {s.get('failure')}\")" || true
  else
    warn "no smoke-check.json found — smoke may have been skipped"
  fi
fi

cat <<'POST'

------------------------------------------------------------------------------
POST-RUN TRIAGE — RC-2C

  A. session completed + 3 commits + deployment.status=ready +
     deployment_url present + smoke.status=passed +
     validate-artifacts ok=true + 0 open reviews
     → RC-2C verified. The full ladder is shipped.

  B. Codex completed but Vercel failed (vercel_cli_missing /
     vercel_auth_missing / vercel_deploy_failed)
     → Inspect deployment.json failure block. Fix env / link /
       project_id; do NOT change product code unless it's a real
       wiring bug.

  C. Vercel deployed but smoke failed
     → Inspect smoke-check.json. Likely the deployed URL doesn't
       serve the expected content; could be a Vercel build issue
       (the dogfood project's npm run build assembles dist/
       but Vercel may need a vercel.json to know to serve dist/).
     → Review item created automatically; no rollback (preview).
------------------------------------------------------------------------------
POST
