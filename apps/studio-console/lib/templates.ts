/**
 * Deterministic templates for Product Contract + MVP Requirements.
 *
 * No LLM call — these are fixed scaffolds the operator fills in. Every
 * template includes the section headers the downstream Studio expects.
 *
 * Locked spec:
 *   - product contract template includes positioning / users / MVP scope /
 *     out of scope / user flow / technical assumptions / acceptance /
 *     risks / success criteria
 *   - mvp requirements template includes 3-5 task sections with Scope:,
 *     Acceptance:, Non-goals, build/typecheck acceptance lines
 */

export const PRODUCT_CONTRACT_TEMPLATE = `# Product Contract

> A short, focused contract between operator and Studio. Fill in each
> section. Move resolved decisions out of "Open questions" into the
> open-questions.md checklist as \`- [x]\`.

## Product positioning

(One-paragraph summary: what this product is, what category it competes
in, what the operator's distinctive angle is. Avoid marketing — be precise.)

## Target users

- (User segment 1: who they are + what they currently do without this product)
- (User segment 2)

## MVP scope

What's IN the first shippable slice (paste-able into mvp-requirements.md as the basis for tasks):

- (Capability 1)
- (Capability 2)
- (Capability 3)

## Out of scope (for the MVP)

What we explicitly will NOT build in v1, even though it might be tempting:

- (Out-of-scope 1)
- (Out-of-scope 2)

## User flow

The 1-2 happy paths the user takes through the MVP. Numbered steps:

1. (Step 1 — user action)
2. (Step 2 — system response)
3. (Step 3)

## Technical assumptions

- Stack: (e.g. Next.js 15 + TypeScript + localStorage; no backend in MVP)
- External services: (none / which ones / which keys)
- Deployment: (none in MVP / Vercel preview / etc.)

## Acceptance criteria

The contract is satisfied if:

- (Criterion 1 — testable / observable)
- (Criterion 2)
- (Criterion 3 — \`npm run build\` passes; \`npm run typecheck\` passes)

## Risks / open questions

- (Risk 1 — and the mitigation we'll attempt)
- (Risk 2)

(For binary yes/no decisions, move them into open-questions.md as
\`- [ ]\` items so the lock gate tracks them.)

## Success criteria

Studio's success criterion for THIS contract is to **demonstrate Studio's
delivery capability**, not product-market fit:

- The MVP requirements decompose into clear tasks.
- Each task has scope_paths + acceptance criteria the gates can check.
- The Studio runs through greenfield generation + at least one change
  request without unresolved review-queue items.
- Every change leaves a \`delivery-report.md\` + \`applied-change.json\`
  + git commit with provenance trailers.

PMF, retention, monetization, and partnerships are explicitly out of scope
for this contract.
`;

export const MVP_REQUIREMENTS_TEMPLATE = `# MVP Requirements

> Carved-out v1 slice that gets fed to \`agent-studio new --from\`. Each
> H2 \`## task-NNN — title\` becomes one autonomous task. Use parser-safe
> Scope blocks (multi-line bullets, no backticks).

## task-001 — (concise title)

(One-paragraph intent. What this task builds, why now, how it depends
on later tasks if at all.)

Scope:
- app/**

Acceptance:
- (testable criterion 1)
- (testable criterion 2)
- \`npm run build\` passes.
- \`npm run typecheck\` passes.

Risk: low

## task-002 — (concise title)

(Intent paragraph.)

Scope:
- app/**
- components/**

Acceptance:
- (criterion 1)
- (criterion 2)
- \`npm run build\` passes.
- \`npm run typecheck\` passes.

Depends: task-001

Risk: medium

## task-003 — (concise title)

(Intent paragraph.)

Scope:
- app/**
- components/**

Acceptance:
- (criterion 1)
- (criterion 2)
- \`npm run build\` passes.
- \`npm run typecheck\` passes.

Depends: task-002

Risk: medium

---

## Non-goals (across all tasks)

- Do not add any new dependency. Do not modify package.json or package-lock.json.
- Do not change tsconfig.json, next.config.mjs, or .gitignore.
- Do not introduce a CSS framework (no Tailwind / styled-components / etc.).
- Do not add tests, build scripts, deploy config, or auth.
- Do not call any external API. No fetch, no network, no LLM client.
`;
