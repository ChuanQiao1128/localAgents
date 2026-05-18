# AF-1 Spec Pipeline Hardening

AF-1 is the first Agent Factory milestone. It makes `requirements.md`, `design.md`, and `tasks.md` first-class, versioned, approved artifacts that control implementation.

## Problem

Local Agent Studio can already run greenfield projects and change requests, but the planning layer is still too loose:

- `mvp-requirements.md` and change requests exist, but requirements/design/tasks are not a single formal lifecycle.
- Approval is not consistently tied to artifact content hashes.
- Implementation can be started from project state without a strong visible spec provenance chain.
- Delivery reports do not always cite the approved spec versions that authorized the work.

This is risky for long-running autonomous development because the agent can drift from the user's approved intent.

## Goal

Create a spec pipeline where implementation is only allowed after:

```text
requirements.md approved
design.md approved
tasks.md approved
```

Each approval must bind to the exact artifact content hash. If the artifact changes, approval becomes stale.

## Core Data Model

Spec workspace:

```text
specs/<spec-id>/
  requirements.md
  design.md
  tasks.md
  approvals.json
  status.json
  design-issues.md
  versions/
    requirements.v1.md
    design.v1.md
    tasks.v1.md
```

Approval record:

```json
{
  "artifact": "requirements.md",
  "version": 1,
  "sha256": "<content-sha256>",
  "status": "approved",
  "approved_by": "user",
  "approved_at": "2026-05-18T00:00:00Z"
}
```

Status record:

```json
{
  "spec_id": "spec_xxx",
  "state": "TASKS_APPROVED",
  "artifacts": {
    "requirements.md": {
      "version": 1,
      "sha256": "<hash>",
      "approval": "approved"
    },
    "design.md": {
      "version": 1,
      "sha256": "<hash>",
      "approval": "approved"
    },
    "tasks.md": {
      "version": 1,
      "sha256": "<hash>",
      "approval": "approved"
    }
  }
}
```

## State Machine

```text
IDEA
CLARIFYING
REQUIREMENTS_DRAFTED
REQUIREMENTS_APPROVED
DESIGN_DRAFTED
DESIGN_APPROVED
TASKS_DRAFTED
TASKS_APPROVED
IMPLEMENTING
VERIFYING
REVIEW_READY
DELIVERED
```

Blocked states:

```text
BLOCKED_NEEDS_CLARIFICATION
BLOCKED_DESIGN_ISSUE
BUILD_FAILED
TYPECHECK_FAILED
REPAIRING
STOPPED_BY_USER
FAILED
```

## Implementation Plan

### AF-1A: Spec Store and Approval Hashes

Add server-side library functions for:

- create spec workspace
- read spec artifacts
- compute artifact SHA-256
- approve artifact
- detect stale approval
- write version snapshot
- write status summary

Tests:

- approval stores hash
- unchanged file remains approved
- edited file becomes `changed_since_approval`
- version snapshot is created
- invalid artifact path is refused

### AF-1B: Implementation Gate

Add a gate function:

```text
can_start_implementation(spec_id) -> ok | blocked(reason)
```

Rules:

- requirements/design/tasks must exist
- all three must be approved
- approved hash must match current file hash
- stale approval blocks implementation

Wire the gate into CLI and Studio Console start/change run paths.

### AF-1C: Studio Console UI

Add a Spec Pipeline panel:

- requirements status
- design status
- tasks status
- current hash prefix
- version
- approve button
- changed-since-approval warning
- implementation blocked reason

Do not overload Deliver. This belongs near Discuss/Develop because it controls whether development can start.

### AF-1D: Delivery Provenance

Add `spec_provenance` to delivery outputs:

```json
{
  "requirements": { "version": 1, "sha256": "..." },
  "design": { "version": 1, "sha256": "..." },
  "tasks": { "version": 1, "sha256": "..." }
}
```

Delivery report must include the same fields in human-readable form.

### AF-1E: Dogfood

Create a tiny local spec and run it through:

```text
draft requirements
approve requirements
draft design
approve design
draft tasks
approve tasks
implementation
delivery report
```

Do not use production secrets.

## Acceptance Criteria

- A spec workspace can be created from an idea or change request.
- Requirements/design/tasks are stored as separate artifacts.
- Each artifact can be approved independently.
- Approval records include version and SHA-256.
- Editing an approved artifact invalidates approval.
- Implementation is blocked until all required artifacts are approved and unchanged.
- Delivery report references approved artifact versions/hashes.
- Studio Console shows spec status clearly.
- CLI and UI agree on gate status.
- Tests cover hash approval, stale approval, gate blocking, and delivery provenance.

## Validation

Run:

```bash
python3 -m unittest discover -s tests
npm --workspace apps/studio-console run typecheck
npm --workspace apps/studio-console run build
```

At minimum for AF-1A:

```bash
python3 -m unittest tests.unit.test_spec_pipeline
npm --workspace apps/studio-console run typecheck
```
