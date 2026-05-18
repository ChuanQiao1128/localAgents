/**
 * Change Request draft storage under `<root>/.studio-console/changes/<id>/`.
 *
 * Two files per draft:
 *   change-request.md  — the operator-authored markdown draft
 *   meta.json          — { projectId, title, createdAt, updatedAt }
 *
 * The orchestrator's `agent-studio change new --from ...` reads the .md
 * directly. The Console never executes that command — it only persists
 * the draft and prints the copy-only command. Live execution lives in
 * RC-5A.10.
 *
 * Path allowlist enforced in lib/paths.ts; every read/write goes through
 * `assertReadable` so a buggy or malicious request cannot escape into
 * the rest of the workspace.
 */

import fs from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { assertReadable, changeDraftsRoot, relToWorkspace } from "./paths";

export type ChangeRequestDraftSummary = {
  id: string;
  /** absolute path to the draft dir. */
  path: string;
  /** workspace-relative path to the draft dir. */
  relPath: string;
  /** workspace-relative path to change-request.md (what `agent-studio change new --from` consumes). */
  changeRequestPath: string;
  projectId: string | null;
  title: string | null;
  createdAt: string;
  updatedAt: string;
  /** Length of the change-request.md in bytes (for sidebar display). */
  size: number;
};

export type ChangeRequestDraft = ChangeRequestDraftSummary & {
  /** Full markdown body. */
  content: string;
};

const DEFAULT_TEMPLATE = `# (Title here)

## Goal

(One-paragraph description of what this change should do and why.)

## Scope paths

- app/**

## Non-goals

- Do not modify package.json, package-lock.json, or any lockfile.
- Do not change tsconfig.json or next.config.mjs.
- Do not introduce a new dependency.

## Acceptance criteria

- (Testable criterion 1)
- (Testable criterion 2)
- \`npm run build\` passes.
- \`npm run typecheck\` passes.
`;

const META_FILE = "meta.json";
const REQUEST_FILE = "change-request.md";

type MetaShape = {
  projectId?: string | null;
  title?: string | null;
  createdAt?: string;
  updatedAt?: string;
};

export function newChangeRequestId(): string {
  return `cr_${randomBytes(5).toString("hex")}`;
}

/**
 * List every draft under .studio-console/changes/. Returns empty list
 * when the dir doesn't exist (cold case).
 */
export async function listChangeRequestDrafts(): Promise<
  ChangeRequestDraftSummary[]
> {
  const root = changeDraftsRoot();
  let entries: string[];
  try {
    entries = await fs.readdir(root);
  } catch {
    return [];
  }
  const summaries = await Promise.all(
    entries.map((id) => loadDraftSummary(id).catch(() => null)),
  );
  return summaries
    .filter((x): x is ChangeRequestDraftSummary => x !== null)
    .sort((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}

/**
 * Create a fresh draft dir with a default template + meta.json. Returns
 * the new id. Caller may pass `projectId` to pre-associate the draft
 * with an existing agent-studio project.
 */
export async function createChangeRequestDraft(opts?: {
  projectId?: string | null;
  title?: string | null;
}): Promise<string> {
  await fs.mkdir(changeDraftsRoot(), { recursive: true });
  const id = newChangeRequestId();
  const dir = path.join(changeDraftsRoot(), id);
  assertReadable(dir);
  await fs.mkdir(dir, { recursive: false });
  const now = new Date().toISOString();
  const meta: MetaShape = {
    projectId: opts?.projectId ?? null,
    title: opts?.title ?? null,
    createdAt: now,
    updatedAt: now,
  };
  await fs.writeFile(
    path.join(dir, REQUEST_FILE),
    DEFAULT_TEMPLATE,
    "utf-8",
  );
  await fs.writeFile(
    path.join(dir, META_FILE),
    JSON.stringify(meta, null, 2),
    "utf-8",
  );
  return id;
}

export async function loadDraftSummary(
  id: string,
): Promise<ChangeRequestDraftSummary | null> {
  if (!isValidDraftId(id)) return null;
  const dir = path.join(changeDraftsRoot(), id);
  let stat;
  try {
    stat = await fs.stat(dir);
  } catch {
    return null;
  }
  if (!stat.isDirectory()) return null;
  assertReadable(dir);

  const meta = await readMeta(dir);
  const requestPath = path.join(dir, REQUEST_FILE);
  let size = 0;
  try {
    size = (await fs.stat(requestPath)).size;
  } catch {
    return null; // dir exists but no request file — not a real draft
  }
  return {
    id,
    path: dir,
    relPath: relToWorkspace(dir),
    changeRequestPath: relToWorkspace(requestPath),
    projectId: meta.projectId ?? null,
    title: meta.title ?? null,
    createdAt: meta.createdAt ?? new Date(stat.birthtime).toISOString(),
    updatedAt: meta.updatedAt ?? new Date(stat.mtime).toISOString(),
    size,
  };
}

export async function loadChangeRequestDraft(
  id: string,
): Promise<ChangeRequestDraft | null> {
  const summary = await loadDraftSummary(id);
  if (!summary) return null;
  const content = await fs
    .readFile(path.join(summary.path, REQUEST_FILE), "utf-8")
    .catch(() => "");
  return { ...summary, content };
}

/**
 * Update either change-request.md (markdown) or the meta.json sidecar.
 * Always bumps updatedAt server-side.
 */
export async function updateChangeRequestDraft(
  id: string,
  field: "change-request.md" | "meta.json",
  content: string,
): Promise<{ ok: true } | { ok: false; error: string }> {
  if (!isValidDraftId(id)) {
    return { ok: false, error: `invalid draft id: ${id}` };
  }
  const dir = path.join(changeDraftsRoot(), id);
  assertReadable(dir);
  try {
    const stat = await fs.stat(dir);
    if (!stat.isDirectory()) {
      return { ok: false, error: `draft not found: ${id}` };
    }
  } catch {
    return { ok: false, error: `draft not found: ${id}` };
  }

  if (field === "change-request.md") {
    await fs.writeFile(path.join(dir, REQUEST_FILE), content, "utf-8");
    await bumpUpdatedAt(dir);
    return { ok: true };
  }

  if (field === "meta.json") {
    let parsed: MetaShape;
    try {
      const raw = JSON.parse(content) as Record<string, unknown>;
      parsed = {
        projectId:
          typeof raw.projectId === "string"
            ? raw.projectId
            : raw.projectId === null
              ? null
              : undefined,
        title:
          typeof raw.title === "string"
            ? raw.title
            : raw.title === null
              ? null
              : undefined,
      };
    } catch (exc) {
      return { ok: false, error: `invalid meta.json: ${String(exc)}` };
    }
    const existing = await readMeta(dir);
    const merged: MetaShape = {
      ...existing,
      ...parsed,
      updatedAt: new Date().toISOString(),
      createdAt: existing.createdAt ?? new Date().toISOString(),
    };
    await fs.writeFile(
      path.join(dir, META_FILE),
      JSON.stringify(merged, null, 2),
      "utf-8",
    );
    return { ok: true };
  }

  return { ok: false, error: `field not allowed: ${field as string}` };
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

function isValidDraftId(id: string): boolean {
  return /^cr_[a-f0-9]{6,32}$/i.test(id);
}

async function readMeta(dir: string): Promise<MetaShape> {
  try {
    const text = await fs.readFile(path.join(dir, META_FILE), "utf-8");
    return JSON.parse(text) as MetaShape;
  } catch {
    return {};
  }
}

async function bumpUpdatedAt(dir: string): Promise<void> {
  const existing = await readMeta(dir);
  const merged: MetaShape = {
    ...existing,
    updatedAt: new Date().toISOString(),
    createdAt: existing.createdAt ?? new Date().toISOString(),
  };
  await fs.writeFile(
    path.join(dir, META_FILE),
    JSON.stringify(merged, null, 2),
    "utf-8",
  );
}
