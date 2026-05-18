/**
 * Pre-flight scanner — pure, deterministic, no I/O.
 *
 * Runs over a locked contract's three operator-authored markdown files
 * (mvp-requirements.md, product-contract.md, open-questions.md) and
 * returns a list of advisory checks. The scanner is **advisory only**;
 * it does NOT replace the Promotion Gate or Apply Gate, which still
 * enforce safety at runtime.
 *
 * Why two layers? The Plan Workspace runs this BEFORE the operator
 * spends Codex tokens — it surfaces likely-failure conditions cheaply
 * (regex + heuristics on already-loaded text). The runtime gates are
 * the load-bearing safety boundary.
 *
 * Pure-function design: imports nothing from `node:fs` / `process` so
 * this file is safe to import from both client (`app/plan/page.tsx`)
 * and server (future `/api/preflight` if we add one).
 */

export type PreflightSeverity = "error" | "warning" | "info";

export type PreflightCheck = {
  /** Stable id for telemetry/test assertions. */
  id: string;
  severity: PreflightSeverity;
  /** Short human-readable name (column 2 of the checks table). */
  name: string;
  /** True iff the check is satisfied. Failed warnings/errors surface in the UI. */
  passed: boolean;
  /** Explanatory message rendered in the UI regardless of pass/fail. */
  message: string;
  /** Optional snippet of the offending text (e.g. a Scope bullet line). */
  evidence?: string;
};

export type PreflightInput = {
  mvpRequirements: string;
  productContract: string;
  openQuestions: string;
};

export type PreflightResult = {
  /** True iff zero failed errors. Warnings do NOT block. */
  passed: boolean;
  errorCount: number;
  warningCount: number;
  passedCount: number;
  totalCount: number;
  checks: PreflightCheck[];
};

// ---------------------------------------------------------------------------
// Scanner
// ---------------------------------------------------------------------------

export function runPreflight(input: PreflightInput): PreflightResult {
  const checks: PreflightCheck[] = [];
  for (const def of CHECK_DEFINITIONS) {
    const partial = def.test(input);
    checks.push({
      id: def.id,
      name: def.name,
      severity: def.severity,
      ...partial,
    });
  }
  let errorCount = 0;
  let warningCount = 0;
  let passedCount = 0;
  for (const c of checks) {
    if (c.passed) {
      passedCount++;
      continue;
    }
    if (c.severity === "error") errorCount++;
    else if (c.severity === "warning") warningCount++;
  }
  return {
    passed: errorCount === 0,
    errorCount,
    warningCount,
    passedCount,
    totalCount: checks.length,
    checks,
  };
}

// ---------------------------------------------------------------------------
// Check definitions
// ---------------------------------------------------------------------------

type CheckResultPartial = {
  passed: boolean;
  message: string;
  evidence?: string;
};

type CheckDefinition = {
  id: string;
  name: string;
  severity: PreflightSeverity;
  test: (input: PreflightInput) => CheckResultPartial;
};

const CHECK_DEFINITIONS: CheckDefinition[] = [
  // ------- Errors (block "Ready to run") -----------------------------------
  {
    id: "mvp-not-empty",
    name: "MVP requirements is non-empty",
    severity: "error",
    test: ({ mvpRequirements }) => {
      const len = mvpRequirements.trim().length;
      return len > 0
        ? { passed: true, message: `mvp-requirements.md has ${len} chars.` }
        : {
            passed: false,
            message:
              "mvp-requirements.md is empty — autonomous controller has nothing to plan.",
          };
    },
  },
  {
    id: "task-headings",
    name: "At least one `## task` heading",
    severity: "error",
    test: ({ mvpRequirements }) => {
      const matches = mvpRequirements.match(/^##\s+task[-\s]/gim) ?? [];
      return matches.length > 0
        ? {
            passed: true,
            message: `Found ${matches.length} task heading(s) — autonomous parser will pick up each as one task.`,
          }
        : {
            passed: false,
            message:
              "No `## task` H2 headings — `agent-studio new --from` will produce an empty task graph.",
          };
    },
  },
  {
    id: "acceptance-blocks",
    name: "At least one Acceptance: block",
    severity: "error",
    test: ({ mvpRequirements }) => {
      const matches = mvpRequirements.match(/^Acceptance\s*:/gim) ?? [];
      return matches.length > 0
        ? {
            passed: true,
            message: `Found ${matches.length} Acceptance: block(s).`,
          }
        : {
            passed: false,
            message:
              "No Acceptance: blocks — the Promotion Gate has nothing to evaluate against.",
          };
    },
  },
  {
    id: "scope-no-lockfiles",
    name: "Scope does not touch package.json / lockfiles",
    severity: "error",
    test: ({ mvpRequirements }) => {
      const lockRe = /package\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock/i;
      const offenders = collectScopeBulletsMatching(mvpRequirements, lockRe);
      if (offenders.length === 0) {
        return {
          passed: true,
          message: "No Scope bullet references package.json or lockfiles.",
        };
      }
      // Allow if Non-goals explicitly forbids modification — operator may
      // legitimately MENTION it in scope as "files to read" while forbidding
      // changes via Non-goals.
      const nonGoals = extractSection(mvpRequirements, /^#*\s*Non-goals/im);
      const explicitlyForbidden =
        nonGoals !== null &&
        lockRe.test(nonGoals) &&
        /(do not|don'?t|never|must not)/i.test(nonGoals);
      if (explicitlyForbidden) {
        return {
          passed: true,
          message:
            "Scope mentions package.json/lockfile but Non-goals explicitly forbids modification — acceptable.",
          evidence: offenders[0],
        };
      }
      return {
        passed: false,
        message: `${offenders.length} Scope bullet(s) reference package.json or a lockfile. Apply Gate will refuse the patch — tighten Scope before running.`,
        evidence: offenders[0],
      };
    },
  },
  {
    id: "no-secrets-in-body",
    name: "No likely secrets in contract body",
    severity: "error",
    test: ({ mvpRequirements, productContract }) => {
      const combined = mvpRequirements + "\n" + productContract;
      const tokenPatterns: Array<{ re: RegExp; label: string }> = [
        { re: /sk[_-][A-Za-z0-9]{20,}/, label: "Stripe-shaped secret key" },
        { re: /AKIA[0-9A-Z]{16}/, label: "AWS access key id" },
        { re: /xox[bpoas]-[A-Za-z0-9-]{10,}/, label: "Slack token" },
        { re: /ghp_[A-Za-z0-9]{20,}/, label: "GitHub PAT" },
        {
          re: /-----BEGIN [A-Z ]+PRIVATE KEY-----/,
          label: "PEM private key",
        },
      ];
      for (const { re, label } of tokenPatterns) {
        const m = combined.match(re);
        if (m) {
          return {
            passed: false,
            message: `${label} appears in the contract — remove BEFORE running and rotate the credential.`,
            evidence: m[0].slice(0, 32) + "…",
          };
        }
      }
      const literalAssign =
        /(api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*["'][^"'\n]{8,}["']/i.exec(
          combined,
        );
      if (literalAssign) {
        return {
          passed: false,
          message:
            "Likely literal credential value in the contract body — remove before running.",
          evidence: literalAssign[0].slice(0, 64) + "…",
        };
      }
      return {
        passed: true,
        message: "No likely secret patterns detected.",
      };
    },
  },
  {
    id: "open-questions-resolved",
    name: "All open questions resolved",
    severity: "error",
    test: ({ openQuestions }) => {
      const unresolved = (openQuestions.match(/^\s*-\s+\[\s\]/gm) ?? []).length;
      return unresolved === 0
        ? { passed: true, message: "No unresolved `- [ ]` items." }
        : {
            passed: false,
            message: `${unresolved} unresolved open question(s). Lock gate already blocks this; flagging for Plan visibility.`,
          };
    },
  },
  // ------- Warnings (do not block) -----------------------------------------
  {
    id: "scope-block-present",
    name: "At least one Scope: block",
    severity: "warning",
    test: ({ mvpRequirements }) => {
      const matches = mvpRequirements.match(/^Scope\s*:/gim) ?? [];
      return matches.length > 0
        ? {
            passed: true,
            message: `Found ${matches.length} Scope: block(s).`,
          }
        : {
            passed: false,
            message:
              "No Scope: blocks — Apply Gate will fall back to default scoping. Add explicit Scope: bullets per task.",
          };
    },
  },
  {
    id: "scope-no-backticks",
    name: "Scope bullets are parser-safe (no backticks)",
    severity: "warning",
    test: ({ mvpRequirements }) => {
      const offenders = collectScopeBulletsMatching(mvpRequirements, /`/);
      return offenders.length === 0
        ? { passed: true, message: "Scope bullets are parser-safe." }
        : {
            passed: false,
            message: `${offenders.length} Scope bullet(s) contain backticks. The autonomous parser will capture them literally — use plain "app/**" instead. (RC-4C.1 finding.)`,
            evidence: offenders[0],
          };
    },
  },
  {
    id: "deploy-mention",
    name: "Deploy / Vercel / production mentions",
    severity: "warning",
    test: ({ mvpRequirements }) => {
      const matches =
        mvpRequirements.match(/\b(vercel|deploy(?:ment)?|production)\b/gi) ?? [];
      return matches.length === 0
        ? { passed: true, message: "No deploy/production references." }
        : {
            passed: false,
            message: `Contract references deploy/production ${matches.length} time(s). Studio's deploy step is opt-in — confirm the agent-studio.yaml deploy: block before running.`,
            evidence: uniq(matches).slice(0, 5).join(", "),
          };
    },
  },
  {
    id: "migration-mention",
    name: "DB migration mentions",
    severity: "warning",
    test: ({ mvpRequirements }) => {
      const matches =
        mvpRequirements.match(
          /\b(prisma\s+migrate|migrate\s+dev|db\s+push|alembic|drizzle\s+migrate|migration)\b/gi,
        ) ?? [];
      return matches.length === 0
        ? { passed: true, message: "No DB migration references." }
        : {
            passed: false,
            message: `Contract references DB migration ${matches.length} time(s). Migrations are higher-risk; review the Apply Gate output manually after the run.`,
            evidence: uniq(matches).slice(0, 5).join(", "),
          };
    },
  },
  {
    id: "product-contract-substantial",
    name: "Product contract is substantial",
    severity: "warning",
    test: ({ productContract }) => {
      const len = productContract.trim().length;
      if (len >= 200) {
        return {
          passed: true,
          message: `${len} chars — substantial enough to drive task scoping.`,
        };
      }
      if (len >= 50) {
        return {
          passed: false,
          message: `${len} chars — passes the lock gate (≥ 50) but is thin. Consider expanding before consuming Codex tokens.`,
        };
      }
      return {
        passed: false,
        message: `${len} chars — below the lock gate's 50-char minimum. The contract should not be in this state.`,
      };
    },
  },
  {
    id: "non-goals-present",
    name: "Explicit Non-goals section",
    severity: "warning",
    test: ({ mvpRequirements }) => {
      const has =
        /^#+\s*Non-goals/im.test(mvpRequirements) ||
        /^Non-goals\s*:/im.test(mvpRequirements);
      return has
        ? { passed: true, message: "Non-goals section present." }
        : {
            passed: false,
            message:
              "No Non-goals section. Consider adding one to bound what Codex must not touch (deps, configs, etc.).",
          };
    },
  },
  {
    id: "build-typecheck-acceptance",
    name: "Acceptance includes build + typecheck",
    severity: "warning",
    test: ({ mvpRequirements }) => {
      const hasBuild =
        /(npm|pnpm|yarn)\s+run\s+build/i.test(mvpRequirements) ||
        /\bnext\s+build\b/i.test(mvpRequirements);
      const hasTypecheck =
        /(npm|pnpm|yarn)\s+run\s+typecheck/i.test(mvpRequirements) ||
        /\btsc\b(\s+--noEmit)?/i.test(mvpRequirements);
      if (hasBuild && hasTypecheck) {
        return {
          passed: true,
          message: "Acceptance includes both build and typecheck.",
        };
      }
      const missing: string[] = [];
      if (!hasBuild) missing.push("`npm run build`");
      if (!hasTypecheck) missing.push("`npm run typecheck`");
      return {
        passed: false,
        message: `Acceptance does not mention ${missing.join(" or ")}. The eval harness probes these by default — make the criteria explicit.`,
      };
    },
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Walk lines, find each `Scope:` block, return the bullet lines that
 * match `pattern`. A Scope block ends at the first blank line or the
 * first line matching another `Word:` label.
 */
function collectScopeBulletsMatching(
  text: string,
  pattern: RegExp,
): string[] {
  const lines = text.split("\n");
  const offenders: string[] = [];
  let inScope = false;
  for (const line of lines) {
    if (/^Scope\s*:/i.test(line)) {
      inScope = true;
      continue;
    }
    if (!inScope) continue;
    if (line.trim() === "") {
      inScope = false;
      continue;
    }
    if (/^[A-Za-z][A-Za-z\s-]*:\s*/.test(line) && !/^\s*-/.test(line)) {
      // Looks like another labeled block (Acceptance:, Risk:, Depends:).
      inScope = false;
      continue;
    }
    if (pattern.test(line)) offenders.push(line.trim());
  }
  return offenders;
}

/**
 * Return the body text under an H2/H3 heading matching `headingRe`,
 * up to the next heading. Returns null if not found.
 */
function extractSection(text: string, headingRe: RegExp): string | null {
  const lines = text.split("\n");
  let start = -1;
  for (let i = 0; i < lines.length; i++) {
    if (headingRe.test(lines[i])) {
      start = i + 1;
      break;
    }
  }
  if (start === -1) return null;
  let end = lines.length;
  for (let j = start; j < lines.length; j++) {
    if (/^#+\s+/.test(lines[j])) {
      end = j;
      break;
    }
  }
  return lines.slice(start, end).join("\n");
}

function uniq<T>(xs: T[]): T[] {
  return Array.from(new Set(xs));
}
