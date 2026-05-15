#!/usr/bin/env bash
# RC-3A: Next.js + Tailwind shape probe.
#
# Goal: prove Local Agent Dev Studio can drive a Next.js project shape
# end-to-end (real Codex × 3 small UI tasks → npm run build →
# Vercel preview deploy → smoke). NO auth / NO db / NO Stripe / NO
# real AI — those are RC-3B/C/D.
#
# DEFAULT IS DRY-RUN. Pass --run to actually execute.
# --run will: install Next.js + Tailwind + React deps locally
# (~15-30s with cached npm; first time 1-2 min), burn ~75-120k Codex
# tokens, create a real Vercel preview deployment.
#
# Required env (loaded from ~/.local-agent-vercel.env):
#   VERCEL_TOKEN / VERCEL_ORG_ID / VERCEL_PROJECT_ID
# Required binaries on PATH:
#   $CODEX_BIN (default /opt/homebrew/bin/codex)
#   vercel
#   npm
#
# Hard NO-list (same as rc2c.sh):
#   - NOT production deploy
#   - NOT rollback
#   - NOT --dangerously-bypass-approvals-and-sandbox / --yolo
#   - tokens never echoed; only token_present=true persists in artifacts

set -euo pipefail

# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DOGFOOD_REPO="$REPO_ROOT/.dogfood/rc3a-saas-shape"
WORKSPACE="${WORKSPACE:-/tmp/rc3a-real}"
CODEX_BIN="${CODEX_BIN:-/opt/homebrew/bin/codex}"
VERCEL_ENV_FILE="${VERCEL_ENV_FILE:-$HOME/.local-agent-vercel.env}"

# Same conservative budgets as RC-2C.2's clean pass.
BUDGET_MAX_TASKS=3
BUDGET_MAX_INNER_RUNS=3
BUDGET_MAX_CANDIDATES=1
BUDGET_MAX_REPAIR=1
BUDGET_MAX_ABANDONED=1
BUDGET_MAX_CORRECTIVE=1

# RC-3A integration gives Codex more time per task: Next.js builds are
# heavier than the static dogfood. 600s is the default; we bump
# explicitly so a slow first build doesn't time out.
INTEGRATION_TIMEOUT_SEC=900

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
rc3a.sh — RC-3A Next.js shape probe

Usage:
  scripts/rc3a.sh                       # dry-run: print commands only
  scripts/rc3a.sh --run                 # execute (real Codex + real Vercel)
  CODEX_BIN=/path/to/codex scripts/rc3a.sh --run

Env:
  CODEX_BIN          path to codex binary (default: /opt/homebrew/bin/codex)
  WORKSPACE          agent-studio workspace (default: /tmp/rc3a-real)
  VERCEL_ENV_FILE    Vercel env file (default: ~/.local-agent-vercel.env)

Defaults to dry-run. --run will:
  - run `npm ci` in the seeded project (Next.js needs node_modules
    locally so the integration step's `npm run build` can run)
  - consume ~75-120k Codex tokens (3 tasks × 1 candidate cap)
  - create a real Vercel preview deployment in your linked scope
HELP
      exit 0
      ;;
    *) echo "rc3a.sh: unknown arg '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
say()   { printf '\n[rc3a] %s\n' "$*"; }
warn()  { printf '\n[rc3a WARN] %s\n' "$*" >&2; }
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
say "RC-3A Next.js shape probe — mode: $RUN_MODE"
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

if ! command -v npm >/dev/null 2>&1; then
  warn "npm not on PATH (required for `npm ci` setup step)"
  exit 1
fi
say "npm --version: $(npm --version 2>&1 | head -1)"
say "node --version: $(node --version 2>&1 | head -1)"

if [[ ! -f "$VERCEL_ENV_FILE" ]]; then
  warn "VERCEL_ENV_FILE not found at $VERCEL_ENV_FILE"
  exit 1
fi
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
say "Vercel env: VERCEL_TOKEN/ORG_ID/PROJECT_ID all present (values not echoed)"

if [[ ! -d "$DOGFOOD_REPO" ]]; then
  warn "dogfood repo missing at $DOGFOOD_REPO"
  exit 1
fi

# -----------------------------------------------------------------------------
# Cost / blast radius warning
# -----------------------------------------------------------------------------
cat <<'WARN'

------------------------------------------------------------------------------
RC-3A COST + BLAST-RADIUS WARNING

  - npm install:    ~15s cached / 1-2 min cold; ~250-300 MB node_modules
                    in the EPHEMERAL workspace ($WORKSPACE), wiped each run.
  - Codex tokens:   ~75-120k (3 tasks × 1 candidate, same cap as RC-2C.2).
                    Next.js patches will be slightly larger than RC-2C
                    static-HTML patches; estimate may skew higher.
  - Wall clock:     ~5-10 min Codex + ~30-60s npm install + ~30-90s Vercel
                    deploy + ~5s smoke.
  - Vercel:         ONE preview deployment in scope
                    pianxing11281128s-projects, NEW project name
                    `rc3a-saas-shape` (NOT same as rc2-creator-tracker;
                    Vercel will auto-create on first deploy if linked
                    via .vercel/ — RC-3A starts WITHOUT a .vercel/
                    link, so the first `vercel deploy` may prompt OR
                    auto-link via VERCEL_PROJECT_ID env).
  - Production:     NOT touched.
  - Rollback:       NOT enabled.

Dry-run prints commands only. 0 tokens, 0 deploys.
------------------------------------------------------------------------------
WARN

# -----------------------------------------------------------------------------
# Plan
# -----------------------------------------------------------------------------
say "Plan:"
echo "  1. Wipe workspace at $WORKSPACE"
echo "  2. agent-studio init"
echo "  3. agent-studio new --from $DOGFOOD_REPO/requirements.md"
echo "  4. Seed project with Next.js + Tailwind starter:"
echo "       package.json, next.config.mjs, tsconfig.json, tailwind.config.ts,"
echo "       postcss.config.mjs, app/, .gitignore"
echo "  5. Write RC-3A agent-studio.yaml (deploy.enabled=true preview, no rollback)"
echo "  6. (NEW vs RC-2C) npm ci in the project — installs node_modules so"
echo "     the integration step's \`npm run build\` works locally"
echo "  7. git init + baseline commit"
echo "  8. autonomous preflight"
echo "  9. autonomous start (real Codex × 3 → real Vercel preview → real smoke)"
echo " 10. autonomous status / logs --tail 80 / reviews list"
echo " 11. autonomous validate-artifacts --json"
echo " 12. Print: 3 commit hashes + deployment URL + smoke status"

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
  PROJECT_DIR="<workspace>/.agent-studio/projects/ai-writing-humanizer-XXXXXX"
  say "project dir (dry-run placeholder): $PROJECT_DIR"
fi

# Copy the Next.js starter files. Use rsync-style explicit list so a
# stray file in DOGFOOD_REPO can't sneak in.
for item in package.json next.config.mjs tsconfig.json tailwind.config.ts postcss.config.mjs .gitignore vercel.json; do
  do_or_print "cp '$DOGFOOD_REPO/$item' '$PROJECT_DIR/'"
done
# Optional lockfile — copy if present so `npm ci` can run deterministically.
if [[ -f "$DOGFOOD_REPO/package-lock.json" ]]; then
  do_or_print "cp '$DOGFOOD_REPO/package-lock.json' '$PROJECT_DIR/'"
fi
do_or_print "cp -r '$DOGFOOD_REPO/app' '$PROJECT_DIR/'"

# -----------------------------------------------------------------------------
# Step 5: RC-3A agent-studio.yaml
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
    max_abandoned_tasks: $BUDGET_MAX_ABANDONED
    max_corrective_tasks: $BUDGET_MAX_CORRECTIVE

integration:
  every_n_tasks: 1
  run_at_session_end: true
  timeout_sec: $INTEGRATION_TIMEOUT_SEC

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

# Belt-and-suspenders safety
if [[ "$RUN_MODE" == "run" ]]; then
  if grep -qE '^  environment: production' "$PROJECT_DIR/agent-studio.yaml"; then
    warn "environment=production detected — refusing"
    exit 1
  fi
  if grep -qE '^    prod: true' "$PROJECT_DIR/agent-studio.yaml"; then
    warn "vercel.prod=true detected — refusing"
    exit 1
  fi
fi

# -----------------------------------------------------------------------------
# Step 6: NEW vs RC-2C — install deps so integration's `npm run build` works.
# Prefer `npm ci` if a package-lock.json was seeded; fall back to `npm install`
# (which also generates the lockfile on first run).
# -----------------------------------------------------------------------------
if [[ -f "$DOGFOOD_REPO/package-lock.json" ]]; then
  do_or_print "(cd '$PROJECT_DIR' && npm ci --no-audit --no-fund)"
else
  do_or_print "(cd '$PROJECT_DIR' && npm install --no-audit --no-fund)"
fi

# -----------------------------------------------------------------------------
# Step 7: git baseline
# -----------------------------------------------------------------------------
do_or_print "(cd '$PROJECT_DIR' && git init -q -b main)"
do_or_print "(cd '$PROJECT_DIR' && git config user.email rc3a@dogfood)"
do_or_print "(cd '$PROJECT_DIR' && git config user.name rc3a)"
do_or_print "(cd '$PROJECT_DIR' && git add -A)"
do_or_print "(cd '$PROJECT_DIR' && git -c commit.gpgsign=false commit -q -m 'rc3a baseline')"

# -----------------------------------------------------------------------------
# Step 8: preflight
# -----------------------------------------------------------------------------
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous preflight"

if [[ "$RUN_MODE" != "run" ]]; then
  say "DRY-RUN finished. Re-run with --run to actually consume tokens + deploy."
  exit 0
fi

# -----------------------------------------------------------------------------
# Step 9-11: real run (only --run)
# -----------------------------------------------------------------------------
say "STARTING autonomous run — Codex tokens + real Vercel deploy ahead."
say "Press Ctrl+C in the next 5s to abort..."
sleep 5

do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous start"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous status"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous logs --tail 80"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous reviews list"
do_or_print "'$REPO_ROOT/agent-studio' --root '$WORKSPACE' autonomous validate-artifacts --json"

# -----------------------------------------------------------------------------
# Step 12: post-run summary
# -----------------------------------------------------------------------------
say "Per-task git history:"
(cd "$PROJECT_DIR" && git log --oneline -10) || true

SESS_DIR="$(ls -d "$PROJECT_DIR/.agent/autonomous/sessions/"*/ 2>/dev/null | head -1 | sed 's:/$::')"
if [[ -n "${SESS_DIR:-}" ]]; then
  DEPLOY_JSON="$(ls -t "$SESS_DIR/deployments/"*/deployment.json 2>/dev/null | head -1 || true)"
  SMOKE_JSON="$(ls -t "$SESS_DIR/smoke-checks/"*/smoke-check.json 2>/dev/null | head -1 || true)"
  if [[ -n "${DEPLOY_JSON:-}" ]]; then
    say "deployment.json: $DEPLOY_JSON"
    python3 -c "import json; d=json.load(open('$DEPLOY_JSON')); print(f\"  status: {d.get('status')}\"); print(f\"  url:    {d.get('deployment_url')}\")" || true
  fi
  if [[ -n "${SMOKE_JSON:-}" ]]; then
    say "smoke-check.json: $SMOKE_JSON"
    python3 -c "import json; s=json.load(open('$SMOKE_JSON')); print(f\"  status: {s.get('status')}\"); print(f\"  failure: {s.get('failure')}\")" || true
  fi
fi

cat <<'POST'

------------------------------------------------------------------------------
POST-RUN TRIAGE — RC-3A

  A. session completed + 3 commits + deployment.status=ready +
     smoke.status=passed + validate-artifacts ok=true + 0 reviews
     → RC-3A verified. Next.js + Tailwind shape works end-to-end.
     → Next milestone: RC-3B (Prisma data model probe).

  B. Codex generates patches but Next.js build / typecheck fails on
     some task → real failure surface. Allowed fixes per spec:
     prompt / context-pack / repair-loop / eval-wiring /
     changed-files classification. Likely culprits to look at first:
       - Codex didn't add `"use client"` to interactive component
       - Tailwind class purge config doesn't include new file path
       - tsconfig path alias not used as expected
       - integration timeout (Next.js builds are slower than the
         static dogfood — yaml uses 900s; bump if needed)

  C. Codex / Vercel env failure → no product code change; fix env.
------------------------------------------------------------------------------
POST
