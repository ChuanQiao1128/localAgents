# RC-2B Dogfood Result — Real Codex Patch Worker

Date: 2026-05-10. Scope: same dogfood repo as RC-2A
(`.dogfood/rc2-creator-tracker/`), same `requirements.md`,
`deploy.enabled=false`, but with `agentic.patch_worker=codex` opted in
through `agent-studio.yaml`. Goal: prove the autonomous controller can
drive the Codex CLI as the inner patch worker, capture a real source
diff, and run the existing apply / commit / integration ladder against
it. No fakes for the dogfood run; tests fake the command runner only.

## Patch worker

- implemented: yes — `_run_codex_patch_worker` in
  `orchestrator/core/agentic_runtime.py` already existed (MVP-1 era);
  RC-2B added the missing safety + plumbing pieces:
  - new public `build_codex_patch_worker_command(...)` with hard-coded
    safety invariants (sandbox / approval allow-lists,
    `--yolo` / `danger-full-access` / `--dangerously-bypass-...`
    forbid-list — refusal raises `ValueError` before any subprocess
    is spawned)
  - new public `codex_cli_available(*, command="codex")` preflight
  - new public `_default_codex_runner(...)` so unit tests can inject a
    fake without forking subprocesses
  - new injected `command_runner=...` kwarg on
    `_run_codex_patch_worker` for the same reason
  - added `--ask-for-approval on-request` to the codex argv (was
    missing — codex's default approval mode would block on most
    workspace-write operations)
  - new failure type `codex_command_refused` for the case when a
    builder-level safety refusal happens at runtime
- codex CLI available in this environment: **NO**
  (`which codex` → not found; the user's real Mac is expected to
  install `npm i -g @openai/codex` per the brief)
- command (for a real run, sanitized):
  ```
  codex exec
    -C <worktree>
    -m <model>
    --sandbox workspace-write
    --ask-for-approval on-request
    --skip-git-repo-check
    --output-last-message <run_dir>/candidates/<id>/codex-last-message.md
    -- <prompt>
  ```
- sandbox: `workspace-write` (allow-list pinned in
  `_CODEX_ALLOWED_SANDBOXES`)
- approval mode: `on-request` (allow-list pinned in
  `_CODEX_ALLOWED_APPROVALS`)
- forbidden tokens (raise on any usage):
  `_CODEX_FORBIDDEN_TOKENS = {"--yolo",
  "--dangerously-bypass-approvals-and-sandbox", "danger-full-access"}`

## Dogfood outcome

- session status: `paused` (`pause_reason: needs_human_review`)
- tasks completed: 0
- commits: 0
- open reviews: 1 (blocking, needs-human-review on task-001)
- validate-artifacts: `ok: true`
- ready-for-deployment: **no** — but for a different reason than RC-2A.
  RC-2A paused because no `patch_worker` was configured; **RC-2B paused
  because the configured patch worker (`codex`) is not installed in this
  sandbox environment.** The system handled it correctly: preflight
  classified it as `codex_cli_not_found` BEFORE forking subprocess, so
  the failure type is precise and actionable rather than a generic
  "empty diff".

## Inner loop

- run id: `run_40cfc50a97`
- candidates: 3 (candidate-a/b/c, conservative/test-focused/broader-fix)
- source_patch_present: `false` for every candidate (no Codex CLI to
  produce a diff)
- per-candidate `changed-files.json::reason`:
  `codex_cli_not_found` (with `details.looked_for: "codex"`)
- eval executed: `false` (no patch to evaluate against)
- eval passed: n/a
- promotion decision: `needs-human-review`
  (`source_patch_present: false` and
  `required_eval_executed: false` both fail at the gate — same
  honest behavior as RC-2A, but now the failure REASON is
  precise: "Codex CLI not installed" rather than "no patch worker
  configured")

## Git

- commits created: 0 (consistent with 0 promotions)
- changed files: none
- session branch: `agentic/autonomous/session_dfeae3050e`
  (created from main, never touched main)
- commit trailers verified: n/a (no commits)

## Findings

### Product issues

**RC-2B-001 (FIXED — small surgical change):** the existing
`_run_codex_patch_worker` did not include `--ask-for-approval`. Codex's
default approval mode (`untrusted`) blocks on workspace mutations. For
unattended autonomous mode we need `on-request` so codex can write
files without prompting. Fix: added the flag explicitly to the argv
builder, gated by an allow-list. Pre-fix this would have caused codex
to hang waiting for approval on the first file write.

**RC-2B-002 (FIXED — defense in depth):** the patch worker did NOT
refuse `--yolo` / `danger-full-access` / `--dangerously-bypass-...`.
Pre-fix, a future config typo or CLI flag could have widened the
sandbox without anyone noticing. Fix: enumerated allow-lists
(`_CODEX_ALLOWED_SANDBOXES` / `_CODEX_ALLOWED_APPROVALS`) and a
forbid-list (`_CODEX_FORBIDDEN_TOKENS`); the pure command builder
raises `ValueError` before a subprocess is spawned. The runtime maps
that to a clean `codex_command_refused` failure record so an audit
trail exists.

**RC-2B-003 (FIXED — preflight):** `codex_cli_not_found` was previously
detected by catching `FileNotFoundError` AFTER the subprocess fork
attempt. Fix: `codex_cli_available()` runs `shutil.which()` BEFORE
the fork, so we can give a clean failure with `details.looked_for`
populated. Also makes the operator's review item more actionable:
"install `@openai/codex` and re-run" instead of "no diff was produced".

**RC-2B-004 (FIXED — autonomous controller propagation):** RC-2A
documented that `agent-studio.yaml`'s `agentic:` block was not loaded.
Fix: new `AgenticConfig` dataclass + `load_agentic_config()` in
`orchestrator/core/deploy.py` (parallel to the existing
`load_deploy_config`); `cmd_autonomous_start` reads the config and
passes `patch_worker=` + `codex_*=` kwargs into the inner loop. Default
behavior unchanged for projects without an `agentic:` block (still
`patch_worker="none"`). Unknown `patch_worker` values fail loud with
a `ValueError` rather than silently downgrading.

### Prompt / context issues

Cannot evaluate at this layer — Codex never ran. The existing
`_render_patch_worker_prompt` was not modified. If real Codex behavior
on a real Mac shows prompt issues (under-specified scope, missing
context, hallucinated paths), those would be RC-2B.1 follow-ups.

### Codex behavior

Cannot evaluate. Environment did not have `@openai/codex` installed,
which is the expected outcome for a sandboxed CI runner. The user's
real Mac (per the spec: `npm i -g @openai/codex`) is the right
environment for this evaluation.

### UX / CLI issues

- Pre-fix: when `agentic.patch_worker=codex` was configured but codex
  CLI was missing, the only signal was `source_patch_present: false`
  with reason `empty_or_non_source_diff` — indistinguishable from
  "Codex returned an empty patch". Now the review item names the exact
  problem: `codex_cli_not_found` + `looked_for: codex`. Operators can
  fix in one step.
- The new `agentic:` config block lives next to `deploy:` in the same
  `agent-studio.yaml`, mirroring the existing pattern. README still
  needs an `## RC-2B / Codex patch worker` quickstart subsection (not
  added this pass — would expand scope; documented for follow-up).

## Fixes applied

| Fix | Files changed | Tests added |
|---|---|---|
| `--ask-for-approval on-request` + sandbox/approval allow-lists + forbid-list | `orchestrator/core/agentic_runtime.py` (new `build_codex_patch_worker_command`, `codex_cli_available`, `_default_codex_runner`, refactored `_run_codex_patch_worker` to accept `command_runner`) | `tests/unit/test_codex_patch_worker.py` (24 tests) |
| `agentic:` config block + `load_agentic_config` | `orchestrator/core/deploy.py` (new `AgenticConfig`, `CodexPatchWorkerConfig`, `load_agentic_config`) | covered by AgenticConfigTests in same file |
| Autonomous controller propagation | `orchestrator/cli.py` (`cmd_autonomous_start::_run_inner_loop` now reads config) | covered by AutonomousPropagationTests |
| Dogfood config opt-in | `.dogfood/rc2-creator-tracker/agent-studio.yaml` (added `agentic:` block) | n/a (config) |

Code change shape:

```
# BEFORE — unit test could not exercise without forking real Codex
def _run_codex_patch_worker(...):
    command = ["codex", "exec", "-C", str(worktree), ...]
    completed = subprocess.run(command, ...)

# AFTER — pure builder + injected runner = unit-testable + safe
def build_codex_patch_worker_command(*, sandbox, ask_for_approval, ...):
    if sandbox not in _CODEX_ALLOWED_SANDBOXES: raise ValueError(...)
    return [..., "--sandbox", sandbox, "--ask-for-approval", ask_for_approval, ...]

def _run_codex_patch_worker(*, command_runner=None, ...):
    if command_runner is None and not codex_cli_available(): return failure(...)
    command = build_codex_patch_worker_command(...)
    completed = (command_runner or _default_codex_runner)(command, ...)
```

## Test results

- `tests.unit.test_codex_patch_worker` (NEW): **24/24 pass**
- targeted (codex_patch_worker + agentic_runtime + autonomous +
  eval_harness_root_package + pause_then_render + run_package +
  artifact_validation + backward_compat_session + next_actions +
  smoke_rollback + deploy): **286/286 pass**
- e2e (autonomous_cli + cli_flow + golden_path): **55/55 pass**
- estimated full suite total: **513 passed / 2 skipped / 0 failed**
  (up from 489; 24 new tests this pass)

## Recommendation

- **private beta ready: not yet — but the gap is now precisely
  characterized.** With `codex` installed and authenticated on the
  user's real Mac, the system has every piece in place to run a real
  autonomous task end-to-end:
  1. Config knob exists and is loaded
  2. Controller propagates `patch_worker=codex` into the inner loop
  3. Inner loop preflights the CLI and raises a clean
     `codex_cli_not_found` if missing
  4. Command builder enforces `workspace-write` + `on-request` and
     refuses dangerous flags
  5. Worktree is prepared, codex runs, diff is computed,
     `patch.diff` + `changed-files.json` written
  6. Apply Gate / commit / integration / review queue all
     unchanged — they will see a real source patch the same way they
     see any candidate today

- **blockers before beta:**
  1. **Run RC-2B on a machine with codex installed** (this sandbox
     does not have it; not a product bug). Expected outcome: at least
     task-001 generates a real patch; reaches one of (a) promote →
     apply → commit, or (b) failure WITH evidence (real codex output,
     real review item).
  2. README quickstart section for `agentic:` block (small docs gap).
  3. Optional: add a manual `agent-studio autonomous preflight`
     subcommand that runs `codex_cli_available()` + auth check before
     `start`, so users see the missing-codex error in 50ms instead of
     after the first task pause.

- **next suggested step:** **RC-2B.1 on a real-codex-installed machine,
  same dogfood repo, same `agent-studio.yaml`.** No new product code
  expected; this is purely an environment exercise. If the inner
  loop produces a real patch and the apply gate accepts it, RC-2C
  (real Vercel preview) is unblocked.

## Net production state after RC-2B

- All RC-1 + RC-1.1 + RC-2A items closed
- Codex patch worker is **wired, tested, safe-by-default, opt-in**
- 4 small product fixes shipped (approval flag, allow-list, preflight,
  config propagation), each with regression tests
- Estimated **513 passed / 2 skipped / 0 failed**
- The remaining beta blocker is purely operational: install Codex on
  the host running the dogfood, then re-run. No additional product
  code is expected for that step.
