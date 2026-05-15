#!/usr/bin/env bash
# RC-3B: Next.js + Tailwind + Prisma data model probe.
#
# Goal: prove Local Agent Dev Studio can drive the data layer of a
# Next.js project shape end-to-end (real Codex × 3 schema-edit tasks
# → npm run build (which now also runs `prisma generate`) →
# Vercel preview deploy → smoke). NO real Postgres / NO migrations
# framework / NO auth / NO Stripe / NO RAG / NO real AI — those are
# RC-3C/D/E/F/G/H.
#
# DEFAULT IS DRY-RUN. Pass --run to actually execute.
# --run will: install Next.js + Tailwind + React + Prisma deps locally
# (~30-60s with cached npm), burn ~75-120k Codex tokens, create a
# real Vercel preview deployment.
#
# Required env (loaded from ~/.local-agent-vercel.env):
#   VERCEL_TOKEN / VERCEL_ORG_ID / VERCEL_PROJECT_ID
# Required binaries on PATH:
#   $CODEX_BIN (default /opt/homebrew/bin/codex)
#   vercel
#   npm
#
# IMPORTANT: VERCEL_PROJECT_ID should point at a NEW Vercel project
# (e.g. `rc3b-prisma-probe`). Reusing the rc3a-saas-shape project ID
# will work but mixes deployments across milestones.
#
# Hard NO-list (same as rc2c.sh + rc3a.sh):
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
DOGFOOD_REPO="$REPO_ROOT/.dogfood/rc3b-prisma-probe"
WORKSPACE="${WORKSPACE:-/tmp/rc3b-real}"
CODEX_BIN="${CODEX_BIN:-/opt/homebrew/bin/codex}"
VERCEL_ENV_FILE="${VERCEL_ENV_FILE:-$HOME/.local-agent-vercel.env}"

# Same conservative budgets as RC-3A's clean pass.
BUDGET_MAX_TASKS=3
BUDGET_MAX_INNER_RUNS=3
BUDGET_MAX_CANDIDATES=1
BUDGET_MAX_REPAIR=1
BUDGET_MAX_ABANDONED=1
BUDGET_MAX_CORRECTIVE=1

# Same 900s integration timeout as RC-3A. Prisma adds `prisma generate`
# (~2-5s) before `next build`; well within budget.
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
rc3b.sh — RC-3B Next.js + Prisma data model probe

Usage:
  scripts/rc3b.sh                       # dry-run: print commands only
  scripts/rc3b.sh --run                 # execute (real Codex + real Vercel)
  CODEX_BIN=/path/to/codex scripts/rc3b.sh --run

Env:
  CODEX_BIN          path to codex binary (default: /opt/homebrew/bin/codex)
  WORKSPACE          agent-studio workspace (default: /tmp/rc3b-real)
  VERCEL_ENV_FILE    Vercel env file (default: ~/.local-agent-vercel.env)

Defaults to dry-run. --run will:
  - run `npm install` in the seeded project (Prisma adds ~50 MB of
    deps + the generated `@prisma/client`)
  - consume ~75-120k Codex tokens (3 tasks × 1 candidate cap)
  - create a real Vercel preview deployment in your linked scope
HELP
      exit 0
      ;;
    *) echo "rc3b.sh: unknown arg '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
say()   { printf '\n[rc3b] %s\n' "$*"; }
warn()  { printf '\n[rc3b WARN] %s\n' "$*" >&2; }
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
say "RC-3B Next.js + Prisma data model probe — mode: $RUN_MODE"
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
  warn "npm not on PATH (required for npm install setup step)"
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
RC-3B COST + BLAST-RADIUS WARNING

  - npm install:    ~30-60s cold (Prisma is ~50 MB on top of Next.js
                    deps); ~300-350 MB node_modules in the EPHEMERAL
                    workspace ($WORKSPACE), wiped each run.
  - Codex tokens:   ~75-120k (3 tasks × 1 candidate cap, same as RC-3A).
                    Schema edits are smaller than RC-3A UI patches; may
                    skew lower.
  - Wall clock:     ~5-10 min Codex + ~30-60s npm install + ~30-90s
                    Vercel deploy + ~5s smoke + ~2-5s prisma generate
                    per integration.
  - Vercel:         ONE preview deployment. VERCEL_PROJECT_ID should
                    be a NEW project (e.g. rc3b-prisma-probe). Reusing
                    rc3a-saas-shape's project ID will work but blends
                    deployments across milestones.
                    The `rc3b-prisma-probe` Vercel project (when you
                    create it) MUST have Deployment Protection /
                    Vercel Authentication DISABLED, same as rc3a — or
                    smoke will 401 (RC-3A run-3 lesson).
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
echo "  4. Seed project with Next.js + Tailwind + Prisma starter:"
echo "       package.json, next.config.mjs, tsconfig.json, tailwind.config.ts,"
echo "       postcss.config.mjs, vercel.json, .gitignore, .env, app/, prisma/"
echo "  5. Write RC-3B agent-studio.yaml (deploy.enabled=true preview, no rollback,"
echo "     integration command list adds prisma:validate + prisma:generate)"
echo "  6. npm install in the project — installs Next.js + Prisma + generates"
echo "     @prisma/client on postinstall"
echo "  7. git init + baseline commit"
echo "  8. autonomous preflight"
echo "  9. autonomous start (real Codex × 3 schema edits → real Vercel preview → real smoke)"
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

# Copy the Next.js + Prisma starter files. Use rsync-style explicit list so a
# stray file in DOGFOOD_REPO can't sneak in.
for item in package.json next.config.mjs tsconfig.json tailwind.config.ts postcss.config.mjs .gitignore vercel.json .env; do
  do_or_print "cp '$DOGFOOD_REPO/$item' '$PROJECT_DIR/'"
done
# Optional lockfile — copy if present so `npm ci` can run deterministically.
if [[ -f "$DOGFOOD_REPO/package-lock.json" ]]; then
  do_or_print "cp '$DOGFOOD_REPO/package-lock.json' '$PROJECT_DIR/'"
fi
do_or_print "cp -r '$DOGFOOD_REPO/app' '$PROJECT_DIR/'"
do_or_print "cp -r '$DOGFOOD_REPO/prisma' '$PROJECT_DIR/'"

# -----------------------------------------------------------------------------
# Step 5: RC-3B agent-studio.yaml
# Differences vs RC-3A:
#   - integration commands list `npx prisma validate` + `npx prisma generate`
#     after the existing typecheck + build, so the eval harness can confirm
#     schema correctness before the build step is asked to consume the client
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
# Step 6: install deps. Same fallback pattern as rc3a.sh — npm ci if a
# lockfile is seeded; npm install otherwise. The dogfood ships without a
# lockfile by design (RC-3A.6 was deliberately deferred); npm install will
# also generate the @prisma/client during postinstall.
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
do_or_print "(cd '$PROJECT_DIR' && git config user.email rc3b@dogfood)"
do_or_print "(cd '$PROJECT_DIR' && git config user.name rc3b)"
do_or_print "(cd '$PROJECT_DIR' && git add -A)"
do_or_print "(cd '$PROJECT_DIR' && git -c commit.gpgsign=false commit -q -m 'rc3b baseline')"

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
POST-RUN TRIAGE — RC-3B

  A. session completed + 3 commits + deployment.status=ready +
     smoke.status=passed + validate-artifacts ok=true + 0 reviews
     → RC-3B verified. Next.js + Prisma data model works end-to-end.
     → Next milestone: RC-3C (FastAPI / LLM pipeline).

  B. Codex generates schema patches but `prisma validate` /
     `prisma generate` / Next.js build / Vercel build fails →
     real failure surface. Allowed fixes per spec:
     prompt / context-pack / repair-loop / eval-wiring /
     changed-files classification. Likely culprits to look at first:
       - Codex didn't include the back-reference `results RewriteResult[]`
         on RewriteJob → relation invalid
       - Generated Prisma client output dir tripped diff_within_scope
         (analogous to RC-3A's tsbuildinfo gap; runtime may need
         another suffix filter)
       - Missing `prisma generate` step in build chain → `@prisma/client`
         import fails on Vercel
       - DATABASE_URL not visible to integration / Vercel build
       - `prisma:validate` script not declared by Codex when modifying
         schema (eval harness wiring gap)

  C. Codex / Vercel env / SSO failure → no product code change; fix env
     (likely needs a fresh rc3b-prisma-probe Vercel project + auth disabled,
     same operator pattern as RC-3A).
------------------------------------------------------------------------------
POST
