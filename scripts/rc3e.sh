#!/usr/bin/env bash
# RC-3E: Deterministic LLMOps Eval Suite Shape Probe.
#
# Goal: prove Local Agent Dev Studio can drive Codex to scaffold a
# deterministic, dependency-free LLMOps eval shape — golden dataset
# loader → batch CLI invocation → with-RAG vs without-RAG dual run →
# structured artifact (latency + cost_usd field) → frontend Eval Suite
# section.
#
# NO real OpenAI / Anthropic / any LLM API call. NO LLM-as-judge.
# NO numpy / pandas / sklearn / langchain / ragas / deepeval / evals.
# NO backend deployment. NO real cost (cost_usd field is 0.0 — schema
# is forward-compatible, value is N/A this milestone).
#
# IMPORTANT: eval is observe-only.
# - eval is NOT in `npm run build` chain (build remains
#   `npm run backend:test && prisma generate && next build`)
# - eval is NOT in agent-studio integration commands
# - eval failure does NOT block deploy in this milestone
# - this script runs `npm run eval` ONCE post-autonomous-run as
#   verification + reports the artifact path; failure is surfaced but
#   does not change the exit status of the autonomous run itself
#
# DEFAULT IS DRY-RUN. Pass --run to actually execute.
# --run will:
#   - npm install (~30-60s)
#   - python venv + pip install (cached after first run)
#   - burn ~75-150k Codex tokens (3 tasks × 1 candidate cap)
#   - create a real Vercel preview deployment for the Next.js frontend
#   - run `npm run eval` once after the autonomous run completes
#
# Required env (loaded from ~/.local-agent-vercel.env):
#   VERCEL_TOKEN / VERCEL_ORG_ID / VERCEL_PROJECT_ID
#
# Required binaries on PATH:
#   $CODEX_BIN (default /opt/homebrew/bin/codex), vercel, npm, python3
#
# IMPORTANT: VERCEL_PROJECT_ID should point at a NEW Vercel project
# `rc3e-llmops-eval-suite-probe`. Use the corrected env-rewrite recipe
# from docs/rc3e-prep-report.md (do NOT `source` old env file before
# `cat >`-ing the new one — that's the standing RC-3B/C gotcha).
#
# Hard NO-list (same as rc2c.sh / rc3a.sh / rc3b.sh / rc3c.sh / rc3d.sh):
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
DOGFOOD_REPO="$REPO_ROOT/.dogfood/rc3e-llmops-eval-suite-probe"
WORKSPACE="${WORKSPACE:-/tmp/rc3e-real}"
CODEX_BIN="${CODEX_BIN:-/opt/homebrew/bin/codex}"
VERCEL_ENV_FILE="${VERCEL_ENV_FILE:-$HOME/.local-agent-vercel.env}"

BUDGET_MAX_TASKS=3
BUDGET_MAX_INNER_RUNS=3
BUDGET_MAX_CANDIDATES=1
BUDGET_MAX_REPAIR=1
BUDGET_MAX_ABANDONED=1
BUDGET_MAX_CORRECTIVE=1

INTEGRATION_TIMEOUT_SEC=1200

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
rc3e.sh — RC-3E LLMOps Eval Suite Shape Probe

Usage:
  scripts/rc3e.sh                       # dry-run: print commands only
  scripts/rc3e.sh --run                 # execute (real Codex + real Vercel)
  CODEX_BIN=/path/to/codex scripts/rc3e.sh --run

Env:
  CODEX_BIN          path to codex binary (default: /opt/homebrew/bin/codex)
  WORKSPACE          agent-studio workspace (default: /tmp/rc3e-real)
  VERCEL_ENV_FILE    Vercel env file (default: ~/.local-agent-vercel.env)

Defaults to dry-run. --run will:
  - run `npm install` then `python3 -m venv backend/.venv` + pip install
  - consume ~75-150k Codex tokens (3 tasks × 1 candidate cap)
  - create a real Vercel preview deployment for the Next.js frontend
  - run `npm run eval` once post-autonomous-run as verification
HELP
      exit 0
      ;;
    *) echo "rc3e.sh: unknown arg '$arg' (try --help)" >&2; exit 2 ;;
  esac
done

# -----------------------------------------------------------------------------
# Helpers
# -----------------------------------------------------------------------------
say()   { printf '\n[rc3e] %s\n' "$*"; }
warn()  { printf '\n[rc3e WARN] %s\n' "$*" >&2; }
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
say "RC-3E LLMOps Eval Suite Shape Probe — mode: $RUN_MODE"
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

if ! command -v python3 >/dev/null 2>&1; then
  warn "python3 not on PATH (required for backend venv + pytest + eval CLI)"
  exit 1
fi
say "python3 --version: $(python3 --version 2>&1 | head -1)"

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
RC-3E COST + BLAST-RADIUS WARNING

  - npm install:    ~30-60s; ~300-350 MB node_modules.
  - pip install:    ~30-60s first time; cached thereafter (no new deps —
                    Codex MUST NOT add openai / anthropic / langchain /
                    ragas / deepeval / evals / numpy / pandas / sklearn).
  - Codex tokens:   ~75-150k (3 tasks × 1 candidate cap).
  - Wall clock:     ~5-10 min Codex + ~60-120s setup + ~30-90s Vercel
                    deploy + ~few seconds for `npm run eval` post-run.
  - Vercel:         ONE preview deployment for the Next.js frontend.
                    The FastAPI backend is NOT deployed in RC-3E.
                    Use a NEW Vercel project: rc3e-llmops-eval-suite-probe.
                    MUST have Deployment Protection / Vercel Authentication
                    DISABLED, same as rc3a/b/c/d.
                    Seed vercel.json is frontend-only ({"framework":"nextjs"});
                    if `vercel link` re-injects experimentalServices, that's
                    a known observation — record but do NOT activate backend
                    deploy in RC-3E.
  - Backend deploy: NOT touched.
  - Production:     NOT touched.
  - Rollback:       NOT enabled.
  - Real LLM:       NOT called. cost_usd field in eval artifact is 0.0.
  - LLM-as-judge:   NOT used. All metrics deterministic + programmatic.
  - Eval as gate:   NOT enforced. Eval is observe-only post-run.

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
echo "  4. Seed project with RC-3D-completed baseline + RC-3E additions:"
echo "       package.json, configs, vercel.json (frontend-only override),"
echo "       .gitignore, .env, app/, prisma/, backend/ (incl. data/eval/"
echo "       rewrite_golden.jsonl + data/style_guides/), scripts/"
echo "  5. Write RC-3E agent-studio.yaml (deploy.enabled=true preview, no rollback)"
echo "  6. npm install in the project"
echo "  7. git init + baseline commit"
echo "  8. autonomous preflight"
echo "  9. autonomous start (real Codex × 3 → real Vercel preview frontend → real smoke)"
echo " 10. autonomous status / logs --tail 80 / reviews list"
echo " 11. autonomous validate-artifacts --json"
echo " 12. POST-RUN: npm run eval in the workspace project dir + print artifact"
echo " 13. Print: 3 commit hashes + deployment URL + smoke status + eval artifact path"

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

# Copy seed files. Use rsync-style explicit list so a stray file in
# DOGFOOD_REPO can't sneak in.
for item in package.json next.config.mjs tsconfig.json tailwind.config.ts postcss.config.mjs .gitignore vercel.json .env; do
  do_or_print "cp '$DOGFOOD_REPO/$item' '$PROJECT_DIR/'"
done
# Optional lockfile — copy if present so `npm ci` can run deterministically.
if [[ -f "$DOGFOOD_REPO/package-lock.json" ]]; then
  do_or_print "cp '$DOGFOOD_REPO/package-lock.json' '$PROJECT_DIR/'"
fi
do_or_print "cp -r '$DOGFOOD_REPO/app' '$PROJECT_DIR/'"
do_or_print "cp -r '$DOGFOOD_REPO/prisma' '$PROJECT_DIR/'"
do_or_print "cp -r '$DOGFOOD_REPO/backend' '$PROJECT_DIR/'"
do_or_print "cp -r '$DOGFOOD_REPO/scripts' '$PROJECT_DIR/'"

# -----------------------------------------------------------------------------
# Step 5: RC-3E agent-studio.yaml
# Same shape as RC-3D — eval is NOT in integration commands.
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
        expected_status: 200
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
# Step 6: install Node deps. Python venv is created lazily by
# scripts/backend-test.sh on first integration.
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
do_or_print "(cd '$PROJECT_DIR' && git config user.email rc3e@dogfood)"
do_or_print "(cd '$PROJECT_DIR' && git config user.name rc3e)"
do_or_print "(cd '$PROJECT_DIR' && git add -A)"
do_or_print "(cd '$PROJECT_DIR' && git -c commit.gpgsign=false commit -q -m 'rc3e baseline')"

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
# Step 12: POST-RUN eval verification (NEW vs RC-3D).
# Runs `npm run eval` inside the workspace project dir. Failure is
# surfaced (set +e + capture exit code) but does NOT abort the script —
# eval is observe-only this milestone.
# -----------------------------------------------------------------------------
say "POST-RUN: running npm run eval as observe-only verification..."
EVAL_EXIT=0
if [[ -d "$PROJECT_DIR" ]]; then
  ( cd "$PROJECT_DIR" && npm run eval ) || EVAL_EXIT=$?
  if [[ "$EVAL_EXIT" -eq 0 ]]; then
    say "npm run eval: exit 0 (observe-only pass)"
  else
    warn "npm run eval: exit $EVAL_EXIT (observe-only — does NOT block this script)"
  fi

  # Print latest eval artifact path + key fields
  EVAL_ARTIFACT="$(ls -t "$PROJECT_DIR/backend/data/eval/runs/"*/eval-result.json 2>/dev/null | head -1 || true)"
  if [[ -n "${EVAL_ARTIFACT:-}" ]]; then
    say "eval artifact: $EVAL_ARTIFACT"
    python3 - <<PY || true
import json
with open("$EVAL_ARTIFACT") as fh:
    e = json.load(fh)
print(f"  schema_version:     {e.get('schema_version')}")
print(f"  eval_run_id:        {e.get('eval_run_id')}")
print(f"  case_count:         {e.get('case_count')}")
print(f"  passed / failed:    {e.get('passed')} / {e.get('failed')}")
print(f"  cost_usd:           {e.get('cost_usd')}")
m = e.get("metrics") or {}
print(f"  metrics:            structural={m.get('structural_pass')} containment={m.get('containment_pass')} retrieval_hit={m.get('retrieval_hit_pass')}")
w = e.get("with_rag_summary") or {}
n = e.get("without_rag_summary") or {}
print(f"  with_rag_summary:   avg_applied={w.get('avg_applied_style_rules')} avg_latency_ms={w.get('avg_latency_ms')}")
print(f"  without_rag_summary: avg_applied={n.get('avg_applied_style_rules')} avg_latency_ms={n.get('avg_latency_ms')}")
PY
  else
    warn "no eval artifact found under $PROJECT_DIR/backend/data/eval/runs/"
  fi
else
  warn "PROJECT_DIR missing; skipping post-run eval"
fi

# -----------------------------------------------------------------------------
# Step 13: post-run summary
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
POST-RUN TRIAGE — RC-3E

  A. session completed + 3 commits + deployment.status=ready +
     smoke.status=passed + validate-artifacts ok=true + 0 reviews +
     `npm run eval` exit 0 + valid eval artifact written
     → RC-3E verified. Deterministic LLMOps eval shape works end-to-end.
     → Next milestone: RC-3F (Observability — separate scoping).

  B. backend:test fails on a task → real failure surface. Allowed fixes
     per spec: prompt / context-pack / repair-loop / eval-wiring /
     changed-files classification. Likely culprits to look at first:
       - Codex imported openai / anthropic / langchain / ragas / deepeval /
         evals / numpy / pandas / sklearn → integration shows ImportError;
         tighten prompt, do NOT accept the dep
       - Eval CLI eval_run_id depends on time/random → not deterministic
         → tighten task-002 acceptance / prompt
       - Codex modified rewrite_golden.jsonl (out-of-bounds — it's input
         data) → tighten requirements wording
       - Codex added eval to npm run build / integration → would block
         deploy on eval failure; tighten task-002 explicit "NOT in build"
       - disable_retrieval flag exists but retrieval still runs → check
         processor.py diff
       - Frontend tries to fetch eval artifacts → tighten task-003
       - RC-3D Style Guide RAG section removed → tighten task-003
       - npm run eval command works locally but the script's
         post-run verification can't find the artifact → check the
         output path the CLI actually writes to vs what this script
         expects (backend/data/eval/runs/eval_*/eval-result.json)

  C. Codex / Vercel env / SSO failure → no product code change; fix env
     (likely needs a fresh rc3e Vercel project + auth disabled, same
     operator pattern as rc3a/b/c/d — see prep doc for CORRECTED
     env-rewrite recipe).

  D. Eval CLI exits non-zero (POST-RUN) → observe-only this milestone.
     The script surfaces the warning and the artifact path; does NOT
     fail. Inspect the artifact JSON to see which case(s) failed and
     why. NOT a runtime bug.
------------------------------------------------------------------------------
POST
