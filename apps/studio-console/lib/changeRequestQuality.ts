/**
 * Pure deterministic quality scanner + structure parser for change-request.md.
 *
 * Mirrors the spirit of lib/preflight.ts: cheap regex / heuristic checks
 * that surface likely failures BEFORE the operator runs `agent-studio
 * change new --from ...`. Advisory only — the orchestrator's
 * change_request_parser.py + Apply Gate are still the load-bearing
 * safety boundary.
 */

export type CRSeverity = "error" | "warning" | "info";

export type CRCheck = {
  id: string;
  severity: CRSeverity;
  name: string;
  passed: boolean;
  message: string;
  evidence?: string;
};

export type CRQualityResult = {
  passed: boolean;
  errorCount: number;
  warningCount: number;
  passedCount: number;
  totalCount: number;
  checks: CRCheck[];
};

export type CRPreview = {
  title: string | null;
  goal: string | null;
  scopePaths: string[];
  nonGoals: string[];
  acceptance: string[];
};

// ---------------------------------------------------------------------------
// Parser
// ---------------------------------------------------------------------------

export function parseChangeRequestPreview(text: string): CRPreview {
  const title = extractH1(text);
  const goal = extractSectionBody(text, /^#+\s*Goal\b/im);
  // RC-5A.13: align with the orchestrator's parser — accept all three
  // recognized scope section names (`## Scope`, `## Scope paths`,
  // `## Files to change`). Mismatched names used to silently produce
  // scope_paths=0 and cause Apply Gate to refuse every file.
  const scopePaths = extractBullets(
    extractSectionBody(
      text,
      /^#+\s*(Scope(\s+paths)?|Files\s+to\s+change)\b/im,
    ) ?? "",
  ).map(stripBackticks);
  const nonGoals = extractBullets(
    extractSectionBody(text, /^#+\s*Non-goals\b/im) ?? "",
  );
  const acceptance = extractBullets(
    extractSectionBody(text, /^#+\s*Acceptance(\s+criteria)?\b/im) ?? "",
  );
  return {
    title,
    goal: goal ? goal.trim() : null,
    scopePaths,
    nonGoals,
    acceptance,
  };
}

/**
 * 去掉 scope 路径外侧的反引号 —— 与 orchestrator parser
 * `_strip_backticks()` 行为对齐。`` `app/**` `` → ``app/**``。
 */
function stripBackticks(value: string): string {
  if (!value) return value;
  const m = value.trim().match(/^`+(.+?)`+$/);
  return m ? m[1].trim() : value.trim();
}

// ---------------------------------------------------------------------------
// Scanner
// ---------------------------------------------------------------------------

export function runChangeRequestQuality(text: string): CRQualityResult {
  const preview = parseChangeRequestPreview(text);
  const checks: CRCheck[] = [];
  for (const def of CHECK_DEFS) {
    const partial = def.test(text, preview);
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

type CheckDef = {
  id: string;
  name: string;
  severity: CRSeverity;
  test: (text: string, preview: CRPreview) => CheckResultPartial;
};

const CHECK_DEFS: CheckDef[] = [
  // ----- Errors ----------------------------------------------------------
  {
    id: "request-not-empty",
    name: "Change request is non-empty",
    severity: "error",
    test: (text) => {
      const len = text.trim().length;
      if (len < 50) {
        return {
          passed: false,
          message: `Change request is only ${len} chars — too thin for a real change. Add at least a Goal + Scope + Acceptance.`,
        };
      }
      return {
        passed: true,
        message: `${len} chars.`,
      };
    },
  },
  {
    id: "has-goal",
    name: "Goal section present",
    severity: "error",
    test: (_text, preview) => {
      if (preview.goal && preview.goal.length > 10) {
        return {
          passed: true,
          message: "Goal section has content.",
        };
      }
      return {
        passed: false,
        message:
          "No `## Goal` section — `agent-studio change new` needs a clear goal to derive the change contract.",
      };
    },
  },
  {
    id: "has-scope-paths",
    name: "Scope paths declared",
    severity: "error",
    test: (_text, preview) => {
      if (preview.scopePaths.length === 0) {
        return {
          passed: false,
          message:
            "No Scope paths bullets — Apply Gate cannot enforce scope without explicit paths. Add bullets under `## Scope paths`.",
        };
      }
      return {
        passed: true,
        message: `Found ${preview.scopePaths.length} scope path bullet(s).`,
      };
    },
  },
  {
    id: "has-acceptance",
    name: "Acceptance criteria declared",
    severity: "error",
    test: (_text, preview) => {
      if (preview.acceptance.length === 0) {
        return {
          passed: false,
          message:
            "No Acceptance criteria bullets — Promotion Gate has nothing to evaluate against.",
        };
      }
      return {
        passed: true,
        message: `Found ${preview.acceptance.length} acceptance bullet(s).`,
      };
    },
  },
  {
    id: "scope-no-lockfiles",
    name: "Scope does not touch package.json / lockfiles",
    severity: "error",
    test: (text, preview) => {
      const lockRe =
        /package\.json|package-lock\.json|pnpm-lock\.yaml|yarn\.lock/i;
      const offenders = preview.scopePaths.filter((p) => lockRe.test(p));
      if (offenders.length === 0) {
        return {
          passed: true,
          message: "No Scope bullet references package.json or a lockfile.",
        };
      }
      // Allow if Non-goals explicitly forbids it.
      const nonGoalsText = preview.nonGoals.join("\n");
      const explicit =
        lockRe.test(nonGoalsText) &&
        /(do not|don'?t|never|must not)/i.test(nonGoalsText);
      if (explicit) {
        return {
          passed: true,
          message:
            "Scope references package.json/lockfile but Non-goals explicitly forbids modification — acceptable.",
          evidence: offenders[0],
        };
      }
      // Suppress unused-arg lint by grazing text length.
      void text.length;
      return {
        passed: false,
        message: `${offenders.length} Scope bullet(s) reference package.json or a lockfile. Apply Gate will refuse the patch — tighten Scope or add an explicit Non-goal.`,
        evidence: offenders[0],
      };
    },
  },
  {
    id: "no-secrets",
    name: "No likely secrets in the request body",
    severity: "error",
    test: (text) => {
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
        const m = text.match(re);
        if (m) {
          return {
            passed: false,
            message: `${label} appears in the change request — remove BEFORE running and rotate the credential.`,
            evidence: m[0].slice(0, 32) + "…",
          };
        }
      }
      const literalAssign =
        /(api[_-]?key|password|secret|access[_-]?token)\s*[:=]\s*["'][^"'\n]{8,}["']/i.exec(
          text,
        );
      if (literalAssign) {
        return {
          passed: false,
          message:
            "Likely literal credential value in the request body — remove before running.",
          evidence: literalAssign[0].slice(0, 64) + "…",
        };
      }
      return {
        passed: true,
        message: "No likely secret patterns detected.",
      };
    },
  },
  // ----- Warnings --------------------------------------------------------
  {
    id: "has-non-goals",
    name: "Non-goals declared",
    severity: "warning",
    test: (_text, preview) => {
      if (preview.nonGoals.length === 0) {
        return {
          passed: false,
          message:
            "No Non-goals bullets. Adding them tightens what Codex must NOT touch (deps, configs, etc.).",
        };
      }
      return {
        passed: true,
        message: `Found ${preview.nonGoals.length} non-goal bullet(s).`,
      };
    },
  },
  {
    id: "no-ambiguity",
    name: "No vague phrases without acceptance",
    severity: "warning",
    test: (text, preview) => {
      const vagueRe =
        /\b(make it (better|nicer|cleaner|prettier)|improve\s+(things|stuff|the code)|polish|tidy up|clean up|refactor (everything|things))\b/i;
      const m = text.match(vagueRe);
      if (!m) {
        return {
          passed: true,
          message: "No obviously vague phrases detected.",
        };
      }
      // If acceptance is concrete (>= 2 bullets), the vagueness is offset.
      if (preview.acceptance.length >= 2) {
        return {
          passed: true,
          message: `Vague phrase "${m[0]}" present, but acceptance criteria pin it down (${preview.acceptance.length} bullets).`,
          evidence: m[0],
        };
      }
      return {
        passed: false,
        message: `Phrase "${m[0]}" is hard to verify. Add concrete acceptance criteria so the Promotion Gate has something to check.`,
        evidence: m[0],
      };
    },
  },
  {
    id: "no-deploy",
    name: "Deploy / production mentions",
    severity: "warning",
    test: (text) => {
      const matches =
        text.match(/\b(vercel|deploy(?:ment)?|production)\b/gi) ?? [];
      if (matches.length === 0) {
        return {
          passed: true,
          message: "No deploy/production references.",
        };
      }
      return {
        passed: false,
        message: `Change request references deploy/production ${matches.length} time(s). Studio's deploy step is opt-in — confirm agent-studio.yaml deploy: block before running.`,
        evidence: Array.from(new Set(matches)).slice(0, 5).join(", "),
      };
    },
  },
  {
    id: "no-migration",
    name: "DB migration mentions",
    severity: "warning",
    test: (text) => {
      const matches =
        text.match(
          /\b(prisma\s+migrate|migrate\s+dev|db\s+push|alembic|drizzle\s+migrate|migration)\b/gi,
        ) ?? [];
      if (matches.length === 0) {
        return {
          passed: true,
          message: "No DB migration references.",
        };
      }
      return {
        passed: false,
        message: `Change request mentions DB migration ${matches.length} time(s). Migrations are higher-risk; review the Apply Gate output manually after the run.`,
        evidence: Array.from(new Set(matches)).slice(0, 5).join(", "),
      };
    },
  },
  {
    id: "build-typecheck-acceptance",
    name: "Acceptance includes build + typecheck",
    severity: "warning",
    test: (_text, preview) => {
      const joined = preview.acceptance.join("\n");
      const hasBuild =
        /(npm|pnpm|yarn)\s+run\s+build/i.test(joined) ||
        /\bnext\s+build\b/i.test(joined);
      const hasTypecheck =
        /(npm|pnpm|yarn)\s+run\s+typecheck/i.test(joined) ||
        /\btsc\b(\s+--noEmit)?/i.test(joined);
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
  {
    id: "not-too-long",
    name: "Change request is reasonably sized",
    severity: "warning",
    test: (text) => {
      const len = text.length;
      if (len > 8000) {
        return {
          passed: false,
          message: `Change request is ${len} chars — likely too broad for one change. Consider splitting into multiple smaller change requests.`,
        };
      }
      return {
        passed: true,
        message: `${len} chars — within a reasonable range.`,
      };
    },
  },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function extractH1(text: string): string | null {
  const m = text.match(/^#\s+(.+?)\s*$/m);
  if (!m) return null;
  const candidate = m[1].trim();
  // Filter out the placeholder so the preview doesn't lie.
  if (/^\(?title here\)?$/i.test(candidate)) return null;
  return candidate.length > 0 ? candidate : null;
}

function extractSectionBody(
  text: string,
  headingRe: RegExp,
): string | null {
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

function extractBullets(sectionText: string): string[] {
  const lines = sectionText.split("\n");
  const out: string[] = [];
  for (const line of lines) {
    const m = line.match(/^\s*-\s+(.+?)\s*$/);
    if (m) {
      const item = m[1].trim();
      // Skip placeholder bullets (parenthesised hints).
      if (/^\([^)]+\)$/.test(item)) continue;
      out.push(item);
    }
  }
  return out;
}
