# RC-5D Agent Team Review

## Decision

For real AI Writing Naturalizer development, Studio should treat UI design as a first-class task before implementation. The current runtime still uses Codex as the patch worker, but the project contract now carries the specialist-agent responsibilities explicitly so the generated task graph includes design, implementation, QA, and review evidence.

## Required Agent Coverage

- Product Agent: owns positioning, scope, non-goals, acceptance criteria, and provider safety constraints.
- UI Design Agent: owns visual direction, editor workflow, before/after layout, confidence/risk presentation, and accessible empty/loading/error states.
- Developer Agent: owns scoped implementation, candidate patches, repair loops, and local build/typecheck execution.
- QA Agent: owns smoke flows, persistence checks, fallback checks, and provider mode verification.
- Reviewer Agent: owns promotion evidence, out-of-scope checks, secret exposure checks, and pre-PR handoff.
- Release/Handoff Agent: owns generated website preview, delivery report, applied change evidence, and operator-facing summary.

## Current Gaps Closed In This Pass

- UI Design is now represented in the Naturalizer MVP requirements as task-001 rather than being implied by implementation.
- Studio Project Inspector now shows Agent coverage, including UI Design, Developer, QA, and Review responsibilities.
- Real provider integrations remain change-request work, not greenfield assumptions.

## Remaining Gaps

- Specialist agents are still expressed through deterministic task contracts and Studio UI, not separate isolated model sessions.
- Provider readiness only reports planned/current mode; it does not read or display secret values.
- Automatic recovery can stop repeated timeouts, but it does not yet rewrite a smaller change request automatically.
