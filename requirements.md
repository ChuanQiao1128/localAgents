# Local Agent Studio / Agent Factory Requirements

## 1. Product Goal

Local Agent Studio is a local-first Agent Factory runtime. It turns product ideas and scoped change requests into reviewed, test-verified code changes by orchestrating Codex / Claude Code / patch-worker as execution backends.

The product must not compete with Codex as a coding model or editor. Its value is the workflow layer Codex does not own by itself:

- versioned requirements, design, and tasks
- approval gates bound to artifact hashes
- explicit run state machine
- observable candidate patch / repair / verification loops
- delivery reports and optional PR handoff
- product review and follow-up change request generation
- evaluation metrics across repeated runs

Short version:

```text
Codex writes candidate patches.
Studio decides when a run is allowed, what evidence is required, and whether the result is safe to deliver.
```

## 2. Final Target Experience

The final Agent Factory workflow should be:

```text
Idea / Change Request
  -> Clarify
  -> requirements.md
  -> Approve requirements
  -> design.md
  -> Approve design
  -> tasks.md
  -> Approve tasks
  -> Autonomous implementation
  -> Build / typecheck / test verification
  -> Repair loop if needed
  -> Reviewer / product review
  -> Delivery report
  -> Optional GitHub PR
  -> Metrics / evaluation
```

The system may automate implementation and repair. Planning and merging remain human-gated.

## 3. Operating Principles

1. Chat is not the state machine. Workflow state must be stored in structured artifacts and run records.
2. Prompt obedience is not enforcement. Critical rules must be enforced by code and UI gates.
3. Requirements, design, and tasks are first-class artifacts, not loose notes.
4. Implementation agents must not silently rewrite the approved spec.
5. Failed builds, stale approvals, dirty worktrees, missing scope, and blocked reviews must stop delivery.
6. Delivery means verified evidence, not an agent summary.
7. Each long-running milestone must be independently commit-ready.
8. Secrets are never read, printed, modified, copied, or committed.

## 4. Personas

### Primary user

A solo builder or AI engineer using local coding agents to build private projects, demos, and portfolio-quality products.

### Secondary future user

A small engineering team that wants a private pre-PR agentic development workflow with approval gates and evidence reports.

## 5. System Layers

### Layer 1: Execution Backend

Responsible for code execution tasks:

- Codex CLI
- Claude Code
- patch-worker
- future coding agents

Responsibilities:

- inspect code
- generate candidate patches
- edit files in allowed scope
- run commands
- summarize implementation

### Layer 2: Agent Factory Runtime

Responsible for workflow enforcement:

- spec lifecycle
- approval gates
- run state machine
- event log
- candidate scoring
- verification gates
- repair loop
- delivery provenance
- metrics

### Layer 3: Studio Console

Responsible for operator visibility and control:

- project workspace
- requirements / design / tasks status
- approve / reject gates
- run progress
- candidate patch viewer
- command trace
- review queue
- preview manager
- delivery report
- product review and recommended change requests

## 6. Milestones

### AF-1: Spec Pipeline Hardening

Goal: implementation must be impossible until requirements, design, and tasks are approved and unchanged.

Acceptance:

- Create `specs/<spec-id>/requirements.md`, `design.md`, `tasks.md`.
- Approvals include artifact name, version, SHA-256, approver, and timestamp.
- Editing an approved artifact marks it `changed_since_approval`.
- Implementation and change run gates reject missing or stale approvals.
- Delivery reports include approved spec versions and hashes.
- Studio Console shows draft / approved / stale / blocked state.
- CLI and UI agree on gate status.

### AF-2: Implementation Run Manager

Goal: long-running implementation is observable, stoppable, retryable, and explainable.

Acceptance:

- Every run writes durable JSONL events.
- UI shows current phase, task, candidate, command, elapsed time, and failure category.
- Stop only kills the recorded pid.
- Retry uses the same approved spec unless a new spec version is approved.
- Candidate patch viewer shows changed files, strategy, verification result, and selection reason.
- Command trace and repair history are visible.

### AF-3: CI Repair Agent

Goal: build/typecheck/test failures become structured repair loops, not blind retries.

Acceptance:

- Failures are classified as build, typecheck, unit test, lint, dependency, runtime, environment, or spec ambiguity.
- Repair attempts stay within allowed scope.
- The original failing command is rerun after repair.
- Repeated same-class failures stop and create a human review item.
- Repair report includes cause, patch summary, commands rerun, and remaining risk.

### AF-4: GitHub PR Integration

Goal: local verified delivery can become a reviewable PR when explicitly enabled.

Acceptance:

- Branch creation follows a safe naming policy.
- PR body includes linked spec versions/hashes, implementation summary, validation, risks, and review checklist.
- No auto-merge.
- No auto-push unless explicitly enabled by the user for that milestone.
- CI failure feedback can feed back into AF-3 repair flow.

### AF-5: Evaluation Dashboard

Goal: measure the Agent Factory across repeated runs.

Acceptance:

- Every run writes `metrics.json`.
- Metrics include duration, status, commands run, build/typecheck/test result, repair attempts, files changed, human gates, and failure category.
- Dashboard shows success rate, failure taxonomy, average repair attempts, average delivery time, and spec drift count.
- Seeded benchmark runs can be compared over time.

### AF-6: Packaging and Public Demo Boundary

Goal: decide what can be safely shown publicly without exposing secrets or private orchestration details.

Acceptance:

- Public demo story is documented.
- Private/local-only surfaces are labeled.
- Secret-bearing local artifacts are excluded.
- Optional public `ci-repair-agent-demo` can be extracted without exposing the full Studio internals.

## 7. Human Gates

The system must require human confirmation for:

- approving requirements
- approving design
- approving tasks
- accepting product positioning or compliance changes
- accepting broad scope changes
- adding dependencies
- changing `.env.example` or secret handling
- opening a non-draft PR
- merging or deploying

For the current Agent Factory long-run, the user has pre-authorized milestone-level local commits and GitHub pushes after validation passes. This authorization applies only to milestone branches/commits produced by the long-run. It does not authorize merge, deploy, secret changes, or non-draft PR creation.

The system may run automatically for:

- generating draft requirements/design/tasks
- computing artifact hashes
- detecting stale approvals
- generating scoped implementation plans
- running candidate patches
- running build/typecheck/test
- classifying failures
- attempting bounded repair
- generating delivery reports
- generating product review findings
- drafting follow-up change requests
- creating a local commit after a milestone passes validation
- pushing the milestone branch/commit to GitHub after validation, when the current long-run mode is enabled

## 8. Long-Run Commit Protocol

Long-running Codex work must proceed milestone by milestone.

Each milestone must:

1. Start from a named change request or plan file.
2. Record baseline status.
3. Implement only the milestone scope.
4. Run the milestone validation commands.
5. Summarize files changed, validation, risks, and next step.
6. Create a local commit after validation passes.
7. Push the milestone branch/commit after validation passes, because the current long-run has explicit user authorization for stage-level GitHub push.
8. Do not merge, deploy, or open a non-draft PR automatically.
9. Stop at any human gate, repeated failure, dirty-worktree ambiguity, or secret-handling risk.

Continuous long-run mode means the worker should keep progressing through automatic steps and milestone validations until it reaches:

- a human approval gate
- a failed validation it cannot repair within scope
- a repeated failure stop condition
- a dirty-worktree ambiguity
- a secret-handling risk
- the end of AF-5 / AF-6

It does not mean the worker may approve requirements/design/tasks on behalf of the user.

Recommended first execution unit:

```text
docs/agent-factory/change-requests/AF-1A_SPEC_APPROVAL_HASHES.md
```

## 9. Frontend Requirements

Studio Console must support the Agent Factory workflow instead of showing only command snippets.

Required UI surfaces:

- project state inspector
- spec pipeline panel
- approval status and hash/version state
- implementation blocked reason
- active run timeline
- stop/retry controls
- candidate patch table
- command trace
- repair history
- delivery provenance
- product review card
- recommended change request list
- evaluation metrics dashboard

UI rules:

- Do not show Start Development when required mapping or spec approvals are missing.
- Do not imply a task is delivered when gates failed.
- Put raw logs behind collapsible details or a right rail.
- Show operator next action clearly.
- Separate delivery status from preview status.

## 10. Non-Goals

- No SaaS product in this phase.
- No auth, Stripe, billing, production deployment, or multi-tenant permissions.
- No automatic production deploy.
- No automatic merge.
- No automatic push unless explicitly enabled.
- No secret reading or secret display.
- No replacement coding model.
- No attempt to reimplement Codex.
- No broad UI redesign unrelated to the current AF milestone.

## 11. Success Criteria

The project is successful when a user can:

1. Create a feature or change request.
2. Review and approve requirements, design, and tasks.
3. Start implementation only after gates pass.
4. Watch the run progress and stop it safely.
5. See candidate patches, commands, failures, repairs, and verification.
6. Receive a delivery report tied to approved spec hashes.
7. Run product review and generate next scoped change requests.
8. Optionally prepare a GitHub PR with evidence.
9. Inspect dashboard metrics across runs.

The interview-ready claim:

```text
Local Agent Studio turns ad-hoc Codex usage into a local-first Agent Factory:
versioned specs, approval gates, observable runs, bounded repair, delivery evidence, and eval.
```
