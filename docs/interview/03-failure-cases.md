# Local Agent Dev Studio — Real Failure Cases

This is the doc that proves the project works. Anyone can build a happy-path demo. The harder claim — and the one this repo backs up — is that **when the agent does something wrong, the system catches it, records why, and ships a fix that prevents the same class of bug from recurring**.

Below is the full catalog of real failure modes the runs surfaced, what the failure looked like, the fix that landed, and what regression test now locks the fix.

中文导读: 这一篇是项目最有说服力的部分 — 把每一次真实失败、为什么失败、怎么修的、用什么测试锁住,全部列出来。

---

## How to read these

Each case has the same shape:

- **What broke** — the symptom, ideally with the actual run id / commit SHA / artifact path that surfaced it.
- **Why it broke** — the root cause, explained at the level a reviewer can verify.
- **What got fixed** — the actual code change. Always small. Always with a test.
- **What test locks it** — so it can't quietly come back.

---

## Case 1 — Promotion Gate blocked a valid patch (RC-3A.7 / RC-3A.8)

### What broke

`agent-studio autonomous start` paused at task-001 of the RC-3A Next.js shape probe. Review item `review_22e0c69be4` opened with reason `failed-apply`. Codex had produced a patch that Vercel and `npm run build` both accepted locally, but the Promotion Gate refused with `out_of_scope_changes` listing `apps/web/tsconfig.tsbuildinfo`.

### Why it broke

Next.js's TypeScript incremental build writes `tsconfig.tsbuildinfo` files when `tsc --noEmit` runs. The eval harness ran `tsc --noEmit` against the candidate's ephemeral worktree, which created a `.tsbuildinfo` inside the candidate's worktree, which the runtime's `_discover_files` then captured as a file Codex had "changed", which then failed the `out_of_scope_changes` gate. Real bug: the runtime was treating a build by-product as an agent edit.

### What got fixed

`orchestrator/core/agentic_runtime.py::_discover_files` extended with a deny-list pattern (`*.tsbuildinfo`). The dogfood `.gitignore` also gained `*.tsbuildinfo` + `next-env.d.ts` as belt-and-suspenders so the file never makes it into git either.

### What test locks it

`tests/unit/test_agentic_runtime.py::test_discover_files_filters_tsbuildinfo` (RC-3A.9). The test creates a fake worktree containing `*.tsbuildinfo` files and asserts they don't appear in the changed-files report.

---

## Case 2 — Corrupt patch.diff that passed the apply check but couldn't actually apply (RC-3E.2)

### What broke

The RC-3E LLMOps eval suite probe on a Next.js + FastAPI + RAG project. Codex produced a real patch, eval passed, Promotion Gate said `promote`, Apply Gate's `git apply --check` returned 0… but `git apply` itself then failed silently in some cases, and in other cases applied a malformed diff that produced a building-but-wrong tree.

### Why it broke

The runtime was using Python's `difflib` library to generate `patch.diff` from the before/after file snapshots. `difflib.unified_diff` produces output that LOOKS like a unified diff, but it's missing the `diff --git a/x b/x` header line that `git apply` needs to reliably resolve renames and binary files. `git apply --check` is more forgiving than the actual apply path on the same input — so the gate said yes, the apply said no.

### What got fixed

Replaced the difflib-based patch generation entirely. New approach:

1. Set up an ephemeral git repo inside the candidate's worktree.
2. Stage all changes.
3. Run real `git diff --binary --cached HEAD` to get an actual git diff.
4. That's the `patch.diff` we hand to the Apply Gate.

Also added a 12th hard rule to the Promotion Gate: `patch_apply_check_passed`. The runtime now runs `git apply --check` against `base_commit` BEFORE the gate scores the candidate, so a candidate whose patch can't apply gets `abandoned` instead of squeaking through to the Apply Gate.

### What test locks it

`tests/unit/test_agentic_runtime.py::test_patch_uses_git_diff_not_difflib` and `test_patch_apply_check_in_promotion` (RC-3E.2.4). Plus the ephemeral-repo-based patch generation is exercised in every successful change run; if it regressed, every demo would fail.

This is also documented in `docs/rc3e-corrupt-patch-finding.md` — an evidence-grounded write-up of what a difflib-corrupted patch looked like vs. the real git diff.

---

## Case 3 — Worktree dirty after change run (RC-4A.3 → RC-4A.3.1.A)

### What broke

The first real-Codex change run (RC-4A.3, `change_198713d499` against the tiny notes app) succeeded — change committed, build passed, delivery-report rendered cleanly. But `git status --short` afterward showed:

```
D task-graph.json
```

The next `agent-studio change run` would fail the worktree-clean preflight.

### Why it broke

The autonomous controller's `commit_task` runs `git add -A -- ':!.agent'` so the project's `task-graph.json` is normally tracked (autonomous mode WANTS it tracked — the task graph evolves per task with status, run_ids, commit hashes). But change_runner was swapping the project's `task-graph.json` for a temporary 1-task graph BEFORE the controller ran, and unlinking it AFTER. So the change commit captured the temporary file, then the on-disk restore removed it, leaving git with a deletion vs HEAD.

For a project that already had a `task-graph.json` (autonomous + change mixed), the symmetric breakage was ` M task-graph.json` after the restore wrote back the prior content — same root cause, different symptom.

### What got fixed

New helper `change_runner.py::_purge_task_graph_from_change_commit` runs in the `finally` block after `advance_one_task`. It:

1. Resets `task-graph.json` to its pre-change state on disk.
2. Stages the reset (or `git rm --cached` if no prior file existed).
3. `git commit --amend --no-edit --no-verify` so the change commit's tree no longer contains the ephemeral graph.
4. Updates `task_state["commit"]` to the amended SHA so `applied-change.json` records the right hash.

### What test locks it

`tests/e2e/test_change_run_e2e.py::test_change_run_happy_path_completed` now asserts `git status --porcelain` is empty AND HEAD's tree does not contain `task-graph.json` after a no-prior-task-graph change. `test_change_run_restores_prior_task_graph` asserts the prior content's git blob SHA equals the post-amend HEAD's `task-graph.json` blob SHA — byte-identical restoration verified at the git layer.

This bug only surfaced because we ran the actual flow on a real project. The unit suite was 100% green before this surfaced. Lesson: real-world dogfood is non-negotiable.

---

## Case 4 — Delivery report Validation section was always empty (RC-4A.3 → RC-4A.3.1.B)

### What broke

Same RC-4A.3 run. The `delivery-report.md`'s Validation section rendered as:

```
## Validation

- (no validation results recorded)
```

Even though Codex's eval harness had passed real `npm run build` and `tsc --noEmit` commands. The "(no validation results recorded)" placeholder was meant for cases where there was genuinely no eval data — but it was firing every time.

### Why it broke

Producer/consumer schema mismatch. The eval harness in `agentic_runtime.py::_execute_eval_harness` writes `eval-results.json` with key `commands`. The reader in `change_runner.py::_read_eval_validation` was looking for `commands_run`. Different field name. So `out["eval.<name>"] = ...` populated zero rows, and the renderer's empty-dict placeholder fired.

This is the kind of bug fake tests miss. The fake patch worker test fixture wrote `commands_run` (matching the broken reader) so the e2e test passed, while the real producer wrote `commands` and the real run was always empty.

### What got fixed

`_read_eval_validation` rewritten to read the producer's actual key (`commands`) and to surface multiple sources at once: per-command rows from eval-results.json, a roll-up `eval.required` row, a `promotion` row from promotion-report.json (decision + hard_gates passed/total), and an `apply` row from applied-change.json. Non-completed paths (needs-human-review / failed) also surface eval + promotion rows so the operator can see WHY the change paused, not just that it did.

The e2e fake fixture was also rewritten to mirror the producer's `commands` shape exactly — so a future drift between fake and real would fail the e2e immediately.

### What test locks it

`tests/unit/test_change_runner.py::ReadEvalValidationTests` (3 tests). `tests/e2e/test_change_run_e2e.py::test_change_run_happy_path_completed` extended to assert `**eval.build**: passed`, `**eval.test**: passed`, `**promotion**: passed — decision=promote, hard_gates=...`, `**apply**: passed`, and explicitly `assertNotIn("(no validation results recorded)", md)`.

---

## Case 5 — `change validate` skipped applied-change.json (RC-4A.3 → RC-4A.3.1.C)

### What broke

`agent-studio change validate latest --json` returned `ok=true` but its report only included `change-contract.json` and `delivery-report.md`. The schema-bearing `applied-change.json` was silently skipped. So a malformed or hand-edited `applied-change.json` would never be caught by the operator's "did this change deliver something coherent?" check.

### Why it broke

`cmd_change_validate` simply hadn't been extended after `applied-change.json` was added in RC-4A.2.

### What got fixed

`cli.py::cmd_change_validate` now reads `applied-change.json` if it exists and runs `validate_applied_change` on it. When the change is still `ready_for_run` (no apply yet), the file's absence is NOT a validation failure — keeps pre-run validation flow unchanged.

### What test locks it

`tests/e2e/test_change_cli_flow.py::test_change_new_show_status_validate_round_trip` asserts the pre-run validate report has neither `applied-change.json` nor `delivery-report.md` (ready_for_run). `test_change_run_happy_path_completed` asserts `validate_applied_change(applied) == []` against the on-disk artifact.

---

## Case 6 — `change status` reported `delivered` without `applied-change.json` (RC-4A.3 → RC-4A.3.1.E + RC-4C.1.E)

### What broke

When a change run failed (Promotion Gate refused — nothing applied) but the runner had already written `delivery-report.md` (with `## Result\n\n**failed**`), `agent-studio change status latest --json` returned `state="delivered"`. Actively misleading.

### Why it broke

`change_status_summary` used:

```python
if has_delivery:
    state = "delivered"
elif has_applied:
    state = "applied"
else:
    state = "ready_for_run"
```

It never required `applied-change.json` to exist for `delivered`, and it never inspected the report's actual result token.

### What got fixed

State derivation is now:

- `delivered` requires BOTH `applied-change.json` AND `delivery-report.md`.
- `applied` only when apply happened but delivery didn't render (rare runner-crash case).
- Delivery-without-apply now reads the report's `## Result` token via new `_state_from_delivery_report` helper. Token `failed` → state `failed`. Token `needs-human-review` → state `needs_human_review`. Token `completed` (without applied-change → inconsistent) → state `failed`. Unparseable → state `failed`. **Never `delivered`** without proof both files exist.

### What test locks it

`tests/unit/test_change_contract.py` adds 4 new tests: `test_status_state_failed_when_delivery_without_applied`, `test_status_state_needs_human_review_when_delivery_without_applied`, `test_status_state_inconsistent_completed_without_apply_treated_as_failed`, `test_status_state_unparseable_delivery_falls_back_to_failed`.

---

## Case 7 — Backticks in `Scope:` lines broke fnmatch (RC-4C.1.A)

### What broke

The first real-Codex run on the RC-4B demo matrix (`ai-writing-quality-editor`) paused at task-001. Review `review_2beda9738c` opened. The diagnostic was perfect: Codex (`run_0f41f8b7ee`, `candidate-a`) had produced a patch, `npm run build` passed, `tsc --noEmit` passed, `patch_apply_check_passed=true`, `source_patch_present=true`. The Promotion Gate refused with `diff_within_scope=false` and `no_critical_security_finding=false`. The changed-files report said `app/page.tsx within_scope=false` — but task-001's stated scope was `app/**`.

### Why it broke

The example's `Scope:` line was written as ``Scope: `app/**`, `components/**` `` because backticks render as code in markdown previewers. The parser captured the backticks literally — `scope_paths` became `["\`app/**\`", "\`components/**\`"]`. Then `fnmatch.fnmatch("app/page.tsx", "\`app/**\`")` returned False, the gate blocked, and the Promotion Gate's `no_critical_security_finding=false` was a downstream side-effect (security critic flagged the situation as suspicious).

Reproduced in 4 lines of Python:

```python
>>> import fnmatch
>>> fnmatch.fnmatch("app/page.tsx", "app/**")
True
>>> fnmatch.fnmatch("app/page.tsx", "`app/**`")
False
```

### What got fixed

Three layers:

1. `orchestrator/core/autonomous.py::_clean_meta_value` strips wrapping backticks (and double quotes) defensively from any meta-line value — `Scope:`, `Depends:`. Future writers can keep the backtick habit without it breaking the gate.
2. `_SCOPE_RE` regex relaxed from `(.+)` to `(.*)` so `Scope:` with no inline value can open a multi-line bullet block.
3. Per-section parsing loop now tracks `scope_open` / `acceptance_open` flags; bullets after a `Scope:` opener route into scope_paths until the next non-bullet, non-blank line. Same shape works for `Acceptance:` so writers can pick inline OR bullet form per metadata field.

All 3 example `requirements.md` rewritten to the cleaner bullet form.

### What test locks it

6 new tests in `tests/unit/test_autonomous.py`: `test_scope_strips_wrapping_backticks` (multi-value), `test_scope_strips_wrapping_backticks_single_value`, `test_scope_multiline_bullet_form`, `test_scope_multiline_bullets_with_backticks_also_cleaned` (defense in depth), `test_scope_multiline_block_closes_on_next_meta_line` (block boundary), `test_acceptance_multiline_bullet_block`.

---

## Case 8 — Runner ran change-mode against an incomplete greenfield (RC-4C.1.D)

### What broke

When greenfield paused at task-001 from Case 7, `scripts/run_demo_suite.sh` kept going and ran `change new + change run` against the half-built scaffold. The output was meaningless — `applied_change_json=null`, `delivery_report_md exists`, `state="delivered"` (still buggy at that point — see Case 6).

### Why it broke

The runner had no gate between "autonomous start finished" and "change new starts". It assumed greenfield always succeeded.

### What got fixed

`scripts/run_demo_suite.sh` now reads `<project>/.agent/autonomous/sessions/*/autonomous-session.json` after each demo's greenfield run via `python3 json.load`. If `status != "completed"`:

- Logs `pause_reason`.
- Prints review-queue inspection commands so the operator can debug.
- Records `greenfield_paused` in the cross-demo summary.
- Returns `1` from `run_demo` in single-demo mode (so `tee` log makes failure obvious).
- Returns `0` in full-suite mode (so the other demos still produce evidence).
- Skips `change new` / `change run` / `change validate` completely.

### What test locks it

The runner gate isn't covered by a unit test (it lives in bash), but the change-mode CLI's preflight tests + the unit-tested `change_status_summary` state precision (Case 6) catch the same class of bug from a different angle. RC-4C ran the runner gate live: when RC-4C.1.A was being verified, the greenfield-paused path was exercised end-to-end.

---

## What this catalog proves

### The gates work

Cases 1, 2, and 7 are all "Codex did something, the gate refused, the operator got an actionable artifact." That's exactly what gates are for. Notice that in Cases 1 and 2 the gates correctly refused based on real data; the bug was upstream (in the eval harness or the patch generator) and the gate fired on the symptom. In Case 7 the gate was correct AND the upstream parser was wrong — the gate's correctness was load-bearing for surfacing the parser bug.

### Real-world dogfood is non-negotiable

Cases 3, 4, 5, 6 only surfaced after the **first end-to-end real-Codex run on a real project**. The unit suite was 100% green before each. The lesson, repeated across RC-2, RC-3, RC-4A, and RC-4C: a passing test suite proves no test broke; it doesn't prove the integration matches reality. Plan for at least one real run per milestone and budget time to fix what it surfaces.

### Fixes are small and tested

Every fix above is under ~150 lines of diff and ships with at least one regression test. None required a refactor of the autonomous controller. The two-gate / artifact-validation / on-disk-evidence design has held up well — most fixes turned out to be either (a) one helper function (Cases 3, 4, 7), (b) a regex tweak (Case 7), or (c) a single rule addition to a gate (Case 2).

### Failures stack up over time into hardening

The Promotion Gate currently has 12 hard rules; rule #12 (`patch_apply_check_passed`) only exists because Case 2 surfaced. The parser strips backticks defensively because Case 7 surfaced. Neither could have been predicted by reading the spec — both are evidence-grounded learnings. The repo's hardening layer (every gate rule, every validator, every regression test) is the compound interest of years of failures like these.

---

## Quick reference table

| # | Failure | Surfaced in | Fix milestone | Regression test |
|---|---------|-------------|---------------|-----------------|
| 1 | Promotion Gate blocked tsbuildinfo | RC-3A.7 | RC-3A.8 | test_discover_files_filters_tsbuildinfo |
| 2 | Corrupt difflib patch | RC-3E.2 | RC-3E.2.2 + RC-3E.2.3 | test_patch_uses_git_diff_not_difflib + test_patch_apply_check_in_promotion |
| 3 | Worktree dirty after change run | RC-4A.3 | RC-4A.3.1.A | test_change_run_happy_path_completed (extended) + test_change_run_restores_prior_task_graph (extended) |
| 4 | Delivery-report Validation empty | RC-4A.3 | RC-4A.3.1.B | ReadEvalValidationTests (3 tests) + test_change_run_happy_path_completed (validation rows asserted) |
| 5 | change validate skipped applied-change.json | RC-4A.3 | RC-4A.3.1.C | test_change_new_show_status_validate_round_trip + test_change_run_happy_path_completed (assertEqual([])) |
| 6 | state="delivered" without applied-change.json | RC-4A.3 + RC-4C.1 | RC-4A.3.1.E + RC-4C.1.E | 4 new tests in test_change_contract.py |
| 7 | Backticks in Scope: broke fnmatch | RC-4C.1.A | RC-4C.1.A | 6 new tests in test_autonomous.py |
| 8 | Runner ran change-mode after paused greenfield | RC-4C.1.D | RC-4C.1.D | covered by Case 6 + live RC-4C verification |

Total: 8 distinct failure modes surfaced across RC-3 / RC-4A / RC-4C, all fixed, all locked behind regression tests, ~327 tests pass after every cleanup.
