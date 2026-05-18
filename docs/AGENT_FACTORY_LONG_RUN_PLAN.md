# Agent Factory Long-Run Plan

This document is the execution contract for long-running Codex work on Local Agent Studio. It turns the high-level `AGENTS.md` direction into a staged, auditable plan from AF-1 through AF-5, with AF-6 as an optional packaging phase.

## Goal

Upgrade Local Agent Studio from a local dogfood tool into a local-first Agent Factory runtime:

```text
Idea / Change Request
  -> approved requirements
  -> approved design
  -> approved tasks
  -> autonomous implementation
  -> verification and repair
  -> delivery report
  -> optional GitHub PR
  -> eval metrics
```

The goal is not fully unsupervised merge/deploy. The target is autonomous implementation with human-gated planning and merging.

## Source of Truth

Every long-running worker must read these files before starting:

```text
AGENTS.md
requirements.md
docs/AGENT_FACTORY_LONG_RUN_PLAN.md
docs/AF-1_SPEC_PIPELINE_PLAN.md
```

For the first execution unit, also read:

```text
docs/agent-factory/change-requests/AF-1A_SPEC_APPROVAL_HASHES.md
```

If these documents conflict, use this priority:

```text
1. The current user instruction
2. AGENTS.md safety and repository rules
3. requirements.md product requirements
4. The current milestone change request
5. The broader long-run plan
```

## Non-goals

- Do not create a SaaS product.
- Do not add auth, billing, deployment, or multi-user permissions.
- Do not read, print, modify, or delete `.env.local`.
- Do not auto-merge.
- Do not auto-deploy.
- Do not push to GitHub unless the current long-run mode explicitly enables push. For this Agent Factory long-run, milestone-level push after validation is enabled by the user.
- Do not delete dogfood evidence.
- Do not rewrite unrelated dirty files.

## Human vs Automatic Work

The long run may automatically do:

- draft requirements, design, and tasks
- compute hashes and approval state
- generate implementation plans
- create candidate patches in allowed scope
- run local validation commands
- classify failures
- attempt bounded repair
- write delivery reports
- write metrics
- draft product review findings and follow-up change requests
- commit a completed milestone after validation passes
- push the milestone branch/commit after validation passes, because this long-run has explicit user authorization

The long run must stop for human confirmation before:

- approving requirements, design, or tasks
- accepting product positioning / safety / compliance decisions
- adding new external providers
- changing secret handling
- modifying or reading `.env.local`
- adding dependencies or lockfile changes unless the milestone explicitly allows it
- opening a non-draft PR
- merging
- deploying
- deleting dogfood evidence

The long run must stop and report instead of continuing when:

- the worktree contains unrelated dirty files in the target write set
- the same failure class repeats past the milestone retry cap
- the milestone needs a broader scope than its change request allows
- implementation discovers a design/spec issue
- validation cannot be run locally
- any command might expose secrets

## Long-Run Preflight

Before starting a multi-hour run:

1. Confirm `AGENTS.md` exists at repo root.
2. Confirm root `package.json` points to `apps/studio-console`, not the legacy dashboard.
3. Confirm `.env.local` is ignored and untouched.
4. Confirm runtime artifacts are ignored:
   - `.agent-studio/`
   - `.studio-console/`
   - `.next/`
   - `.next.bak*/`
   - logs and pid files
5. Record current branch and git status.
6. Start from a clean milestone baseline when possible.
7. Run baseline validation:

```bash
python3 -m unittest discover -s tests
npm --workspace apps/studio-console run typecheck
npm --workspace apps/studio-console run build
```

If the full test suite is too slow, run targeted tests for the subsystem being changed plus Studio Console typecheck/build.

## Phase Gate Protocol

Each milestone is a bounded stage. A long-running worker must not blur multiple AF milestones together.

For every milestone:

1. Read the milestone change request.
2. Record current branch and git status.
3. Identify the exact allowed write set.
4. Implement only that milestone.
5. Run targeted tests first.
6. Run Studio Console typecheck/build if UI or shared types changed.
7. Run broader tests when the change touches runtime gates, CLI, or orchestration.
8. Write a short milestone delivery note.
9. Commit when validation passed.
10. Push the milestone branch/commit when validation passed.
11. Continue to the next automatic milestone unless a human gate, stop condition, or unresolved failure is reached.

The first long-run milestone is:

```text
AF-1A: Spec Artifact Store and Hash Approvals
```

It is intentionally smaller than all AF-1. Do not start AF-1B until AF-1A is validated.

## Continuous Run Semantics

When the operator says "run to the end", interpret that as:

```text
Continue automatically through all non-human-gated steps and all validated milestones.
Stop only at explicit human gates, unrepairable failures, dirty-worktree ambiguity, or secret risk.
```

It does not authorize:

```text
approving requirements/design/tasks
accepting product/safety/compliance decisions
merging
deploying
opening a non-draft PR
reading or modifying .env.local
```

## Git Discipline

Each milestone must produce one local commit after validation:

```text
AF-1A: add spec artifact store and hash approvals
AF-1B: enforce approved spec gates before implementation
AF-1C: add Spec Pipeline UI
AF-1D: link delivery reports to approved spec versions
AF-2A: add durable run event log
...
```

Commit requirements:

- Include the milestone id in the subject.
- Include validation commands in the commit body or delivery notes.
- Do not include `.env.local`, secrets, local preview state, `.agent-studio/`, or `.studio-console/`.
- Do not commit generated `.next` or `.next.bak*`.
- Push after validation for this long-run because the operator has explicitly enabled stage-level GitHub push.

## AF-1: Spec Pipeline Hardening

Purpose: make requirements, design, and tasks first-class approved artifacts.

Milestones:

### AF-1A: Spec Artifact Store and Hash Approvals

Add a project/feature spec workspace with:

```text
requirements.md
design.md
tasks.md
approvals.json
status.json
versions/
```

Acceptance:

- Compute SHA-256 for every spec artifact.
- Approve records include artifact, version, hash, approver, and timestamp.
- Editing an approved artifact changes status to `changed_since_approval`.
- Tests cover approval invalidation.

### AF-1B: Implementation Gate

Block implementation unless tasks are approved and still match their approved hash.

Acceptance:

- CLI refuses implementation when requirements/design/tasks are missing or stale.
- Studio Console refuses Start Development / Run Change when required specs are stale.
- Error explains which artifact blocks execution.

### AF-1C: Spec Pipeline UI

Expose the spec lifecycle in the project workspace.

Acceptance:

- Show requirements/design/tasks status.
- Show current version/hash.
- Show approve action.
- Show changed-since-approval state.
- Show implementation blocked reason.

### AF-1D: Delivery Trace to Approved Specs

Every delivery report must cite approved spec versions and hashes.

Acceptance:

- `delivery-report.md` includes requirements/design/tasks version/hash.
- `applied-change.json` includes a `spec_provenance` object.
- Tests validate missing provenance is rejected.

### AF-1E: Dogfood AF-1

Run a small feature through the full spec pipeline.

Acceptance:

- Create spec workspace.
- Approve requirements/design/tasks.
- Run implementation.
- Confirm delivery report references approved specs.

## AF-2: Implementation Run Manager

Purpose: make long-running implementation observable and controllable.

Milestones:

- AF-2A durable event log
- AF-2B status polling and active run recovery
- AF-2C stop/cancel/retry safety
- AF-2D candidate patch viewer
- AF-2E command trace and repair history

Acceptance:

- Every run writes JSONL events.
- UI shows current phase, task, candidate, command, elapsed time, and failure category.
- Stop only kills the recorded pid.
- Retry uses the same approved spec unless the user explicitly creates a new version.

## AF-3: CI Repair Agent

Purpose: turn build/typecheck/test failures into structured repair loops.

Milestones:

- AF-3A failure classifier
- AF-3B repair task generation
- AF-3C repair patch loop
- AF-3D repair report and retry cap
- AF-3E seeded local CI repair demos

Acceptance:

- Failure categories include build, typecheck, unit test, lint, dependency, runtime, environment, and spec ambiguity.
- Repair patches stay in allowed scope.
- Original failing command is rerun after repair.
- Repeated same-class failures stop and create human review.

## AF-4: GitHub PR Integration

Purpose: turn local verified delivery into a reviewable PR.

Milestones:

- AF-4A branch creation policy
- AF-4B PR body generator
- AF-4C GitHub issue/label trigger design
- AF-4D optional PR creation command
- AF-4E CI failure feedback into AF-3

Acceptance:

- PR body includes linked approved specs, implementation summary, validation, risks, and review checklist.
- No auto-merge.
- No auto-push unless explicitly enabled.
- Secrets never appear in PR body or logs.

## AF-5: Evaluation Dashboard

Purpose: measure the Agent Factory instead of only demoing it.

Milestones:

- AF-5A metrics schema
- AF-5B run metrics writer
- AF-5C failure taxonomy dashboard
- AF-5D success-rate and repair-rate dashboard
- AF-5E seeded benchmark report

Acceptance:

- Every run writes `metrics.json`.
- Dashboard shows success rate, failure types, repair attempts, duration, files changed, approvals required, and spec drift.
- Metrics can compare workflow versions.

## AF-6: Portfolio and Public Demo Packaging

Optional phase after AF-5.

Purpose: extract safe public-facing material without exposing private prompts, secrets, or real dogfood logs.

Milestones:

- AF-6A public `ci-repair-agent-demo` extraction plan
- AF-6B seeded toy failures
- AF-6C demo video script
- AF-6D portfolio case study
- AF-6E sanitized architecture docs

Acceptance:

- Public demo contains no secrets or private runtime logs.
- Demo can run on a toy repo.
- Case study explains safety boundaries and eval results.

## Stop Conditions

The long run must stop and report rather than continue if:

- `.env.local` would need to be read or changed.
- A migration, dependency add, deployment, push, or merge is required without explicit approval.
- The same failure category repeats past the configured retry cap.
- Required tests cannot run due to environment errors.
- A task requires product/safety direction not already captured in approved specs.
- Worktree has unrelated changes that would be overwritten.

## Required End-of-Milestone Report

Every milestone must end with:

```text
Milestone:
Files changed:
Validation:
Spec/version impact:
Known limitations:
Next milestone:
Commit status:
```
