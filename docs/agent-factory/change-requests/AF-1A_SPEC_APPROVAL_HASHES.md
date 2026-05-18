# AF-1A: Add spec artifact store and hash-based approvals

## Goal

Add the first Agent Factory spec pipeline primitive to Local Agent Studio: a local spec workspace that stores `requirements.md`, `design.md`, `tasks.md`, and approval metadata tied to artifact content hashes.

This change should not implement the full UI or implementation gate yet. It should create the backend/library foundation and tests for AF-1.

## Scope

- `orchestrator/**`
- `tests/**`
- `docs/**`
- `apps/studio-console/lib/**` only if shared TypeScript types are needed

## Non-goals

- Do not implement GitHub PR integration.
- Do not implement CI Repair Agent.
- Do not implement Evaluation Dashboard.
- Do not redesign Studio Console UI in this change.
- Do not run autonomous Codex development.
- Do not add external providers.
- Do not add dependencies unless absolutely necessary.
- Do not read, print, modify, or delete `.env.local`.
- Do not deploy.
- Do not merge or deploy.
- Git push is allowed only after validation passes because the operator has explicitly enabled stage-level push for the Agent Factory long-run.

## Requirements

Implement a spec pipeline storage module that supports:

- creating a spec workspace
- reading `requirements.md`, `design.md`, and `tasks.md`
- computing SHA-256 for each artifact
- approving one artifact at a time
- storing approval records in `approvals.json`
- writing version snapshots under `versions/`
- reporting whether an approval is current or stale

Suggested workspace:

```text
specs/<spec-id>/
  requirements.md
  design.md
  tasks.md
  approvals.json
  status.json
  design-issues.md
  versions/
```

## Acceptance

- `create_spec_workspace` creates the expected files and directories.
- Approving `requirements.md` stores artifact name, version, SHA-256, approver, and timestamp.
- Approving `design.md` and `tasks.md` works the same way.
- Editing an approved artifact causes its approval status to become `changed_since_approval`.
- Unchanged approved artifacts remain `approved`.
- Invalid artifact names are refused.
- Path traversal is refused.
- Version snapshots are written when approvals occur.
- A status summary can be read by CLI/UI code.
- Tests cover:
  - create workspace
  - approve artifact
  - stale approval after edit
  - unchanged approval remains current
  - invalid artifact refused
  - path traversal refused
  - version snapshot exists

## Validation

Run:

```bash
python3 -m unittest discover -s tests
npm --workspace apps/studio-console run typecheck
```

If Studio Console files are changed, also run:

```bash
npm --workspace apps/studio-console run build
```

## Commit

After validation, commit with:

```text
AF-1A: add spec artifact store and hash approvals
```

Then push the milestone branch/commit to GitHub if remote configuration is valid and no secret/local artifact is staged.
