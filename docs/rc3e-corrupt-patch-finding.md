# RC-3E.2 — Corrupt patch.diff at Apply Gate (runtime fix)

Date: 2026-05-11.
Status: **runtime fix landed, RC-3E ready for fresh rerun**. Do NOT
resume the paused session (`session_6bb2dd41ef`); the runtime
behavior beneath it has changed.

## Diagnosis

**Root cause of corrupt patch:** `_unified_file_diff` in
`orchestrator/core/agentic_runtime.py` hand-serialized unified diffs
via Python's `difflib.unified_diff`. The output omitted three things
git's apply-parser needs:

1. **`diff --git a/X b/X` boundary header** — without this, git can't
   reliably tell where one file's diff ends and the next begins. With
   ~9 changed files (the RC-3E task-002 patch shape), boundary
   ambiguity stacks until git rejects the whole stream as
   `corrupt patch at line <N>`.
2. **`new file mode 100644` / `deleted file mode` markers** — for new
   files, difflib emits `--- a/X\n+++ b/X\n@@ -0,0 +A,B @@\n` only.
   Git needs the explicit mode markers to treat it as an addition
   rather than a modification of a non-existent file.
3. **`\ No newline at end of file` markers + binary handling** —
   difflib doesn't emit either; binary files were stubbed as
   `Binary files differ` which git's `--check` rejects.

**Why git apply failed (line 549):** the corruption fires at the
hunk-count parser as soon as accumulated boundary ambiguity makes
the line accounting in a `@@ -A,B +C,D @@` header stop matching the
content lines that follow. `line 549` was effectively right after
the final hunk of `backend/tests/test_rewrite.py`, where git was
still trying to consume what it thought was more of the previous
file but had already exited the last well-formed hunk.

**Why the user's regenerated patch attempt was invalid:** the
operator ran `git diff --binary` from inside the candidate
worktree at `/tmp/rc3e-real/.../.agent/worktrees/run_<id>/candidate-a/`.
That worktree IS not its own git repo — it lives inside the parent
project's `.git`. Git resolved `git diff` against the parent
project's HEAD and saw only `task-graph.json` modifications (the
agent-studio runtime-state file the parent project commits at
baseline + updates during the session). The output was a clean
patch listing only `task-graph.json` — completely unrelated to
Codex's actual output. The candidate worktree's filesystem state
WAS correct; just the diff tool was looking at the wrong repo.

The eval / promotion / scope checks were all correct (26 backend
tests passed, build passed, scope clean) because those operate on
the candidate worktree's file contents directly — they don't use
the patch.diff text. Only the final `git apply` step consumed the
malformed text, which is why the failure surfaced at the Apply Gate
rather than earlier.

## Fix

### Patch generation (`_diff_directories` + new `_build_git_patch`)

Replaced the difflib path with an **ephemeral-repo strategy**:

1. Create a fresh `tempfile.TemporaryDirectory()` (NOT inside the
   parent project, NOT inside `.agent/worktrees/`).
2. Copy every base file (filtered by `_discover_files`, so `.git/`,
   `node_modules/`, `__pycache__/`, etc. are excluded) into the
   tempdir.
3. `git init` the tempdir, configure user, `git add -A && git commit
   --allow-empty -m base`.
4. Apply each classified change (modified / added / deleted) to the
   tempdir, then `git add -A` again.
5. `git diff --binary --cached HEAD` → produces canonical patch with
   `diff --git` headers, `new file mode` markers, proper hunk counts,
   binary-safe encoding, no-newline markers.
6. Self-validate by `git reset --hard HEAD` and running `git apply
   --check` on the produced patch in the same tempdir. Result is
   surfaced as `apply_check.passed` + `apply_check.stderr` in the
   `changed_files` artifact.

Notes:
- The candidate worktree is NEVER used as a git repo. Files are
  read as bytes and copied in. Sidesteps the nested-worktree
  footgun.
- Empty changed-file list → returns `("", apply_check_passed=True)`
  fast-path.
- `git` failure (timeout, OSError, missing binary) → returns marker
  patch + apply_check_passed=False with the exception text in
  stderr; the new hard gate (below) then disqualifies the candidate.

### Promotion apply-check (new hard gate)

Added `patch_apply_check_passed` to `_evaluate_candidate_hard_gates`'s
returned dict. Reads the field from `candidate["changed_files"]`
(produced by `_build_git_patch`). Defaults True for backward-compat
with archived candidates that pre-date the field.

Surfaced in `_build_promotion_report`:
- Top-level `hard_gates["patch_apply_check_passed"]`
- New `gate_details` entry naming RC-3E.2 as the rationale
- Per-candidate summary entry alongside the existing gate summary fields

A candidate with `patch_apply_check_passed=False` is automatically
disqualified by the existing `disqualified = not all(hard_gates.values())`
in `_score_candidate`. The Promotion Gate's selection ladder then
falls through to `needs-human-review` (because the candidate has
source_patch_present=True and required_eval_passed=True but no
eligible candidate exists). The Apply Gate is no longer the first
detector.

Apply Gate semantics, security critic semantics, and review queue
semantics are unchanged. The fix is **purely additive** at the
runtime layer.

## Tests

5 new targeted tests in `tests/unit/test_agentic_runtime.py`:

| Test | Asserts |
|---|---|
| `test_patch_generation_produces_git_applyable_patch_for_modified_file` | Single modified file → `diff --git` header present + independent `git apply --check` against a fresh repo passes |
| `test_patch_generation_produces_git_applyable_patch_for_new_file` | New file in patch → `new file mode 100644` header present + apply-check passes |
| `test_patch_generation_handles_multiple_files_without_hunk_corruption` | Repro of RC-3E task-002 shape (9 changed files: 1 modified + 4 new modules + 1 modified test + 3 new tests) → apply-check passes, all in scope |
| `test_promotion_rejects_candidate_when_patch_apply_check_fails` | A candidate with `patch_apply_check_passed=False` from `_build_git_patch` is disqualified by `_evaluate_candidate_hard_gates` even when every other gate passes |
| `test_patch_generation_does_not_leak_parent_repo_diff_when_changed_is_nested` | When the `changed` directory lives inside a parent git repo (mirrors real `.agent/worktrees/...` placement), the produced patch reflects ONLY the in-scope file changes, NOT the parent repo's `task-graph.json` etc. |

**Targeted result:** 7 passed (5 new + 2 existing matched by `-k
"patch_generation or patch_apply or diff_directories"`).
**Regression sweep:** 192 passed across `test_agentic_runtime.py`,
`test_run_package.py`, `test_autonomous.py`,
`test_codex_patch_worker.py`, `test_rc2c1_fixes.py` (5 most relevant
files). 0 failures.

## Files changed

| File | Change |
|---|---|
| `orchestrator/core/agentic_runtime.py` | Imports `tempfile`. `_diff_directories` now delegates to new `_build_git_patch` for patch text + apply-check. `_build_git_patch` (new) uses ephemeral git repo. `_git` (new) helper. `_evaluate_candidate_hard_gates` adds `patch_apply_check_passed` to its returned dict. `_build_promotion_report` adds `patch_apply_check_passed` to top-level `hard_gates`, `gate_details`, and per-candidate `candidate_summaries`. The legacy `_unified_file_diff` is now unreferenced (left in place — can be removed in a follow-up). |
| `tests/unit/test_agentic_runtime.py` | 5 new targeted tests covering modified / new / multi-file / disqualification / nested-worktree-leak. |
| `docs/rc3e-corrupt-patch-finding.md` | This document. |

What did NOT change: Promotion Gate selection logic, Apply Gate
rules, security critic, artifact validators (validators are loose on
hard_gates / changed_files keys), agent-studio.yaml, eval schema,
review queue, RC-3E dogfood seed (RC-3E.1 hygiene fixes still hold).

## Next

Fresh rerun **RC-3E from scratch** in a wiped workspace:

```bash
cd ~/Documents/LocalAgents && ./scripts/rc3e.sh --run 2>&1 | tee /tmp/rc3e-run-rerun2.log
```

The script will wipe `/tmp/rc3e-real/` before re-seeding. Do **NOT**
attempt to resume `session_6bb2dd41ef`; the runtime patch generation
beneath it has changed and stale candidate `changed_files` records
are missing the `patch_apply_check_passed` field (they would
back-compat to True under the default, but the patch.diff itself
would still be the corrupt difflib output that triggered this whole
investigation). The pending `review_bd6a002d3e` is discardable along
with the old session state.

Predicted RC-3E rerun outcome: same 3-task structure, same 26+ tests
passing under the new patch generator. The apply-check hard gate is
backstop; on a clean run it should never fire because every patch
the new generator emits is git-applyable by construction.
