/**
 * Path allowlist + workspace root resolution.
 *
 * Load-bearing security primitive — every file read by an API route MUST
 * pass `assertReadable()` before fs.readFile is called. Any path that
 * resolves outside the allowed roots is rejected as a 400.
 *
 * Locked spec: docs/STUDIO_CONSOLE_SPEC.md § 12.
 */

import path from "node:path";

/**
 * The repo root the Console operates against. Override with the
 * `LOCALAGENTS_ROOT` env var if running the Console from outside its
 * default location (e.g. inside a separate clone).
 *
 * Default: two parents up from `process.cwd()` — assumes the Console
 * is launched from `apps/studio-console/` (which is what `npm run dev`
 * inside that dir produces).
 */
export function workspaceRoot(): string {
  const override = process.env.LOCALAGENTS_ROOT;
  if (override) return path.resolve(override);
  return path.resolve(process.cwd(), "..", "..");
}

/** `<root>/.agent-studio/projects/` — where existing agent-studio projects live. */
export function projectsRoot(): string {
  return path.join(workspaceRoot(), ".agent-studio", "projects");
}

/** `<root>/.studio-console/contracts/` — where contract drafts live. */
export function contractsRoot(): string {
  return path.join(workspaceRoot(), ".studio-console", "contracts");
}

/** `<root>/.studio-console/runs/` — where Live mode tracks spawned processes (RC-5A.10). */
export function runsRoot(): string {
  return path.join(workspaceRoot(), ".studio-console", "runs");
}

/** `<root>/.studio-console/changes/` — where Change Request drafts live (RC-5A.9). */
export function changeDraftsRoot(): string {
  return path.join(workspaceRoot(), ".studio-console", "changes");
}

/**
 * `<root>/.studio-console/projects/` — Studio Console 自己的项目数据
 * （RC-5A.12.1）。每个 project 一个目录，里头包含 project.json 元数据 +
 * contract/ 子目录（六个文件，与 .studio-console/contracts/ 同结构）。
 *
 * 与 `.agent-studio/projects/<id>/` 的关系：约定 id 一致，运行时状态从
 * 后者读取（loadProjectDetail）。
 */
export function studioProjectsRoot(): string {
  return path.join(workspaceRoot(), ".studio-console", "projects");
}

/** `<root>/examples/` — read-only for the Dashboard's demo matrix card. */
export function examplesRoot(): string {
  return path.join(workspaceRoot(), "examples");
}

/**
 * `<root>/apps/studio-console/templates/` — built-in scaffolds used by
 * Runtime Bootstrap (RC-5A.12.5A). Read-only for the Console; the
 * bootstrap process copies these into the runtime project dir.
 */
export function templatesRoot(): string {
  return path.join(
    workspaceRoot(),
    "apps",
    "studio-console",
    "templates",
  );
}

/**
 * `<root>/docs/interview/` — read-only for the Dashboard's deep-link cards
 * into the interview narration docs. Listed as a separate root (not in
 * `allowedFiles()`) because interview/ is a directory with multiple files.
 */
export function interviewDocsRoot(): string {
  return path.join(workspaceRoot(), "docs", "interview");
}

/**
 * Allowed directory roots. A read is permitted if the resolved absolute
 * path equals or starts with one of these (with a path separator).
 */
function allowedRoots(): readonly string[] {
  return [
    projectsRoot(),
    contractsRoot(),
    runsRoot(),
    changeDraftsRoot(),
    studioProjectsRoot(),
    templatesRoot(),
    examplesRoot(),
    interviewDocsRoot(),
  ];
}

/**
 * Specific files outside the allowed-root set that are still permitted.
 * Used for the Dashboard's evidence cards that point at committed docs.
 */
function allowedFiles(): readonly string[] {
  const root = workspaceRoot();
  return [
    path.join(root, "docs", "EVALUATION.md"),
    path.join(root, "docs", "rc4c-demo-suite-report.md"),
    path.join(root, "docs", "STUDIO_CONSOLE_SPEC.md"),
    path.join(root, "docs", "ARCHITECTURE.md"),
    path.join(root, "docs", "INTERVIEW_STORY.md"),
    path.join(root, "docs", "RESUME_BULLETS.md"),
    path.join(root, "docs", "PROJECT_STATUS.md"),
    path.join(root, "README.md"),
  ];
}

/**
 * Defense-in-depth: even if a path passes `isAllowedReadPath`, refuse
 * filenames that look like secrets. Catches the case where a developer
 * accidentally drops a `.env` into `examples/` etc.
 */
const FORBIDDEN_BASENAME_PATTERNS: readonly RegExp[] = [
  /^\.env(\..*)?$/i, // .env, .env.local, .env.production
  /^id_(rsa|ed25519|ecdsa|dsa)(\.pub)?$/i, // SSH keys
  /\.(pem|key|p12|pfx)$/i, // certificates / private keys
  /^secrets?\..*$/i, // secret.* / secrets.*
  /credentials?\.(json|yaml|yml|env)$/i, // credentials.json etc.
];

/**
 * Defense-in-depth: refuse traversal into engine-internal dirs even when
 * they happen to live under an allowed root. `node_modules/` and `.git/`
 * are the most common offenders; reading them would leak the operator's
 * dev environment without serving any legitimate Console use case.
 */
const FORBIDDEN_PATH_SEGMENTS: readonly string[] = [
  "node_modules",
  ".git",
  ".next",
  ".venv",
  "__pycache__",
];

/**
 * True iff `absPath` (after `path.resolve`) is inside one of the allowed
 * roots OR equals one of the allowed files, AND does not match any of the
 * defense-in-depth blocklists. Symlinks are NOT followed — if a future RC
 * adds them, audit before whitelisting.
 *
 * This function is the entire security boundary for `/api/artifact`. Treat
 * it as load-bearing.
 */
export function isAllowedReadPath(absPath: string): boolean {
  const resolved = path.resolve(absPath);

  // Defense-in-depth filename block (runs FIRST so allowlist passes don't
  // accidentally let a `.env` through).
  const basename = path.basename(resolved);
  for (const pattern of FORBIDDEN_BASENAME_PATTERNS) {
    if (pattern.test(basename)) return false;
  }
  const segments = resolved.split(path.sep);
  for (const segment of segments) {
    if (FORBIDDEN_PATH_SEGMENTS.includes(segment)) return false;
  }

  for (const file of allowedFiles()) {
    if (resolved === file) return true;
  }
  for (const root of allowedRoots()) {
    if (resolved === root) return true;
    if (resolved.startsWith(root + path.sep)) return true;
  }
  return false;
}

/**
 * Convenience: resolve a possibly-relative path against the workspace root,
 * then assert it's readable. Throws Error if not — caller is responsible for
 * mapping that to an HTTP 400.
 */
export function assertReadable(input: string): string {
  const resolved = path.isAbsolute(input)
    ? path.resolve(input)
    : path.resolve(workspaceRoot(), input);

  if (!isAllowedReadPath(resolved)) {
    throw new Error(
      `path is outside the allowlist: ${input} (resolved to ${resolved})`,
    );
  }
  return resolved;
}

/**
 * Convenience: turn an absolute path inside the workspace into a path
 * relative to the workspace root. Used for displaying paths in the UI
 * without leaking the operator's home directory layout.
 */
export function relToWorkspace(absPath: string): string {
  return path.relative(workspaceRoot(), absPath);
}
