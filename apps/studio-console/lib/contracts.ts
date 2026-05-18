/**
 * Contract draft storage under `<root>/.studio-console/contracts/<id>/`.
 *
 * Six sub-files (locked spec § 6):
 *   raw-requirements.md
 *   discussion.md
 *   product-contract.md
 *   mvp-requirements.md
 *   open-questions.md
 *   lock.json
 *
 * Lock rules (locked spec § 8):
 *   - product-contract.md ≥ 50 chars (stripped)
 *   - mvp-requirements.md ≥ 50 chars (stripped)
 *   - open-questions.md has 0 unresolved `- [ ]` items
 *   - mvp-requirements.md has at least one `## task` H2 heading
 */

import fs from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { assertReadable, contractsRoot, relToWorkspace } from "./paths";

export const CONTRACT_FILE_NAMES = [
  "raw-requirements.md",
  "discussion.md",
  "product-contract.md",
  "mvp-requirements.md",
  "open-questions.md",
  "lock.json",
] as const;

export type ContractFileName = (typeof CONTRACT_FILE_NAMES)[number];

export type LockState = {
  locked: boolean;
  lockedAt: string | null;
  lockedBy: string | null;
  unlockedAt: string | null;
};

export type ContractSummary = {
  id: string;
  path: string;
  relPath: string;
  lockState: LockState;
  /** Number of unresolved `- [ ]` items (lock blocker). */
  unresolvedQuestions: number;
  /** Lock preconditions evaluated server-side. */
  canLock: boolean;
  preconditionErrors: string[];
};

export type Contract = ContractSummary & {
  files: Record<ContractFileName, string>;
};

const DEFAULT_FILE_TEMPLATES: Record<ContractFileName, string> = {
  "raw-requirements.md":
    "# Raw requirements\n\n" +
    "Paste the user's unstructured requirements here. Anything goes — bullet points, " +
    "rambling notes, screenshots-described-in-text. The Design Workspace will help you " +
    "iterate this into a Product Contract.\n",
  "discussion.md":
    "# Discussion notes\n\n" +
    "Decisions, rationale, trade-offs, things you considered and rejected. This stays " +
    "as your scratch pad — it doesn't flow into the Product Contract directly.\n",
  "product-contract.md":
    "# Product Contract\n\n" +
    "## Problem\n\n(What user pain are we solving?)\n\n" +
    "## Goals\n\n- (G1)\n- (G2)\n\n" +
    "## Non-goals\n\n- (N1)\n\n" +
    "## Personas\n\n- (P1)\n\n" +
    "## Success metrics\n\n- (M1)\n\n" +
    "## Open questions\n\n(Move resolved ones into open-questions.md as `- [x]`.)\n",
  "mvp-requirements.md":
    "# MVP requirements\n\n" +
    "The carved-out v1 slice that gets fed to `agent-studio new --from`. Use the " +
    "deterministic decomposer's expected shape:\n\n" +
    "## task-001 — (title)\n\n(Intent paragraph.)\n\n" +
    "Scope:\n- app/**\n\n" +
    "Acceptance:\n- (criterion 1)\n- (criterion 2)\n\n" +
    "Risk: low\n",
  "open-questions.md":
    "# Open questions\n\n" +
    "Use markdown checkboxes. Lock MVP Contract requires zero unresolved (`- [ ]`) items.\n\n" +
    "- [ ] (an unresolved question — block lock until checked)\n" +
    "- [x] (a resolved question — does not block lock)\n",
  "lock.json": JSON.stringify(
    {
      locked: false,
      lockedAt: null,
      lockedBy: null,
      unlockedAt: null,
    },
    null,
    2,
  ),
};

/** Generate a `cr_<10-char-hex>` id matching the orchestrator's short-id pattern. */
export function newContractId(): string {
  return `cr_${randomBytes(5).toString("hex")}`;
}

/**
 * List all contracts under .studio-console/contracts/.
 */
export async function listContracts(): Promise<ContractSummary[]> {
  const root = contractsRoot();
  let entries: string[];
  try {
    entries = await fs.readdir(root);
  } catch {
    return [];
  }
  const summaries = await Promise.all(
    entries.map((id) => loadContractSummary(id).catch(() => null)),
  );
  return summaries
    .filter((x): x is ContractSummary => x !== null)
    .sort((a, b) => b.id.localeCompare(a.id)); // newest-first
}

/**
 * Create a fresh contract dir with the 6 default files seeded from
 * templates. Returns the new id.
 */
export async function createContract(): Promise<string> {
  await fs.mkdir(contractsRoot(), { recursive: true });
  const id = newContractId();
  const dir = path.join(contractsRoot(), id);
  // Path-allowlist sanity check before mkdir.
  assertReadable(dir);
  await fs.mkdir(dir, { recursive: false });
  for (const file of CONTRACT_FILE_NAMES) {
    await fs.writeFile(
      path.join(dir, file),
      DEFAULT_FILE_TEMPLATES[file],
      "utf-8",
    );
  }
  return id;
}

/**
 * Load contract summary (lock state + precondition validation, no file
 * contents). Returns null if the contract doesn't exist.
 */
export async function loadContractSummary(
  id: string,
): Promise<ContractSummary | null> {
  if (!isValidContractId(id)) return null;
  const dir = path.join(contractsRoot(), id);
  try {
    const stat = await fs.stat(dir);
    if (!stat.isDirectory()) return null;
  } catch {
    return null;
  }
  assertReadable(dir);

  const files = await readAllContractFiles(dir);
  const lockState = parseLockState(files["lock.json"]);
  const unresolved = countUnresolvedQuestions(files["open-questions.md"]);
  const errors = lockPreconditionErrors(files);
  return {
    id,
    path: dir,
    relPath: relToWorkspace(dir),
    lockState,
    unresolvedQuestions: unresolved,
    canLock: errors.length === 0 && !lockState.locked,
    preconditionErrors: errors,
  };
}

/**
 * Load full contract (summary + all 6 file contents).
 */
export async function loadContract(id: string): Promise<Contract | null> {
  const summary = await loadContractSummary(id);
  if (!summary) return null;
  const files = await readAllContractFiles(summary.path);
  return { ...summary, files };
}

/**
 * Update one of the 6 allowed files. If the file is `lock.json` and the
 * caller is trying to set `locked: true`, server-side preconditions are
 * re-checked and a non-empty error list throws.
 */
export async function updateContractFile(
  id: string,
  file: ContractFileName,
  content: string,
): Promise<{ ok: true; lockState?: LockState } | { ok: false; errors: string[] }> {
  if (!CONTRACT_FILE_NAMES.includes(file)) {
    return { ok: false, errors: [`file not allowed: ${file}`] };
  }
  const dir = path.join(contractsRoot(), id);
  assertReadable(dir);
  try {
    const stat = await fs.stat(dir);
    if (!stat.isDirectory()) {
      return { ok: false, errors: [`contract not found: ${id}`] };
    }
  } catch {
    return { ok: false, errors: [`contract not found: ${id}`] };
  }

  // Lock-update path: re-validate preconditions on the OTHER files
  // (the new lock.json hasn't been written yet, so we project the request
  // onto the current files-on-disk).
  if (file === "lock.json") {
    let requested: LockState;
    try {
      requested = parseLockState(content);
    } catch (exc) {
      return { ok: false, errors: [`invalid lock.json shape: ${String(exc)}`] };
    }
    if (requested.locked) {
      const files = await readAllContractFiles(dir);
      const errors = lockPreconditionErrors(files);
      if (errors.length > 0) {
        return { ok: false, errors };
      }
      // Stamp lockedAt server-side so the client can't lie about timing.
      requested.lockedAt = new Date().toISOString();
      requested.lockedBy = requested.lockedBy ?? "operator";
      requested.unlockedAt = null;
      content = JSON.stringify(requested, null, 2);
    } else {
      // Unlock — preserve historical lockedAt if previously set, stamp unlockedAt.
      const prevText = await fs.readFile(path.join(dir, "lock.json"), "utf-8")
        .catch(() => "{}");
      const prev = parseLockState(prevText);
      requested.lockedAt = prev.lockedAt;
      requested.lockedBy = prev.lockedBy;
      requested.unlockedAt = new Date().toISOString();
      content = JSON.stringify(requested, null, 2);
    }
    await fs.writeFile(path.join(dir, "lock.json"), content, "utf-8");
    return { ok: true, lockState: requested };
  }

  // Non-lock file: write through.
  await fs.writeFile(path.join(dir, file), content, "utf-8");
  return { ok: true };
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function isValidContractId(id: string): boolean {
  // cr_<10-char-hex> per the spec; defensively also accept short-id-like
  // patterns from the orchestrator (10+ hex).
  return /^cr_[a-f0-9]{6,32}$/i.test(id);
}

async function readAllContractFiles(
  dir: string,
): Promise<Record<ContractFileName, string>> {
  const out: Record<ContractFileName, string> = {} as Record<
    ContractFileName,
    string
  >;
  for (const file of CONTRACT_FILE_NAMES) {
    try {
      out[file] = await fs.readFile(path.join(dir, file), "utf-8");
    } catch {
      out[file] = file === "lock.json"
        ? DEFAULT_FILE_TEMPLATES["lock.json"]
        : "";
    }
  }
  return out;
}

function parseLockState(text: string): LockState {
  let raw: Record<string, unknown> = {};
  try {
    raw = JSON.parse(text || "{}") as Record<string, unknown>;
  } catch {
    raw = {};
  }
  return {
    locked: Boolean(raw.locked),
    lockedAt: typeof raw.lockedAt === "string" ? raw.lockedAt : null,
    lockedBy: typeof raw.lockedBy === "string" ? raw.lockedBy : null,
    unlockedAt: typeof raw.unlockedAt === "string" ? raw.unlockedAt : null,
  };
}

function countUnresolvedQuestions(text: string): number {
  if (!text) return 0;
  return text
    .split("\n")
    .filter((line) => /^\s*-\s+\[\s\]/.test(line)).length;
}

/**
 * Returns an empty list iff every lock precondition is met.
 *
 * Locked spec § 8 — load-bearing. Server-side validation runs before
 * writing lock.json with `locked: true`, so a buggy or malicious client
 * can't bypass.
 */
function lockPreconditionErrors(
  files: Record<ContractFileName, string>,
): string[] {
  const errors: string[] = [];
  const productContract = (files["product-contract.md"] ?? "").trim();
  if (productContract.length < 50) {
    errors.push(
      "product-contract.md must be at least 50 chars (currently " +
        `${productContract.length}).`,
    );
  }
  const mvp = (files["mvp-requirements.md"] ?? "").trim();
  if (mvp.length < 50) {
    errors.push(
      "mvp-requirements.md must be at least 50 chars (currently " +
        `${mvp.length}).`,
    );
  }
  const unresolved = countUnresolvedQuestions(files["open-questions.md"] ?? "");
  if (unresolved > 0) {
    errors.push(
      `open-questions.md has ${unresolved} unresolved checkbox(es); resolve them before locking.`,
    );
  }
  if (!/^##\s+task/im.test(files["mvp-requirements.md"] ?? "")) {
    errors.push(
      "mvp-requirements.md has no `## task` H2 heading — the autonomous parser needs at least one.",
    );
  }
  return errors;
}
