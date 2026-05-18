/**
 * Read-side helpers for projects under `<root>/.agent-studio/projects/`.
 *
 * Every read goes through `lib/paths.ts::assertReadable` so the API routes
 * stay thin and the security boundary lives in one place.
 *
 * Locked spec: docs/STUDIO_CONSOLE_SPEC.md § 11 (API routes).
 */

import fs from "node:fs/promises";
import path from "node:path";
import { assertReadable, projectsRoot, relToWorkspace } from "./paths";

export type ProjectSummary = {
  /** dir name under .agent-studio/projects (used as the surface id). */
  id: string;
  /** absolute filesystem path. */
  path: string;
  /** path relative to workspace root, for display. */
  relPath: string;
  /** Number of tasks in task-graph.json (0 if missing). */
  taskCount: number;
  /** Number of tasks with status="completed". */
  completedCount: number;
  /** Number of change-mode change dirs under .agent/changes/. */
  changeCount: number;
  /** Latest autonomous session id (most recent mtime), or null. */
  latestSessionId: string | null;
  /** Latest autonomous session status: running/paused/completed, or null. */
  latestSessionStatus: string | null;
};

export type ProjectDetail = ProjectSummary & {
  /** task-graph.json contents, or null if missing/unreadable. */
  taskGraph: unknown | null;
  /** Latest session full payload, or null. */
  latestSession: unknown | null;
  /** Review queue summary across all sessions. */
  reviewQueue: {
    total: number;
    open: number;
    blocking: number;
    /** Items whose status is no longer "open" (approved/rejected/resolved). */
    resolved: number;
    items: ReviewItemSummary[];
  };
  /** Per-change summaries from .agent/changes/<change_id>/. */
  changes: ChangeSummary[];
};

export type ReviewItemSummary = {
  reviewId: string;
  sessionId: string;
  status: string;
  severity: string;
  reasonCode: string;
  title: string;
  /** Free-text body / description, when present in the JSON. */
  summary: string | null;
  taskId: string | null;
  runId: string | null;
  candidate: string | null;
  /** e.g. apply_failure / eval_failure / integration_failure / deployment_failure / smoke_check_failure / rollback_failure. */
  sourceType: string | null;
  createdAt: string | null;
  /** Workspace-relative path to the review-item JSON itself. */
  reviewItemPath: string | null;
  /** Resolved + existence-probed downstream artifact paths (workspace-relative). */
  promotionReportPath: string | null;
  evalResultsPath: string | null;
  changedFilesPath: string | null;
  patchDiffPath: string | null;
};

export type ChangeSummary = {
  changeId: string;
  state: "ready_for_run" | "applied" | "delivered" | "needs_human_review" | "failed";
  goal: string | null;
  /** Absolute path to applied-change.json (when present); already path-allowlist verified. */
  appliedChangeJson: string | null;
  /** Absolute path to delivery-report.md (when present). */
  deliveryReportMd: string | null;
  branch: string | null;
  sha: string | null;
  /** task_id from change-contract.json or applied-change.json. */
  taskId: string | null;
  /** run_id extracted from applied-change.json (RC-3A+ shape). */
  runId: string | null;
  /** Selected candidate id (when promotion picked one). */
  candidate: string | null;
  /** Promotion decision (e.g. "selected", "rejected", "no_candidate"). */
  promotionDecision: string | null;
  /** ISO timestamp from applied-change.json applied_at field if present. */
  appliedAt: string | null;
  /** Files the apply-gate touched (best-effort across schema variations). */
  filesTouched: string[];
  /** Absolute resolved paths for each downstream artifact (null if not on disk). */
  promotionReportPath: string | null;
  evalResultsPath: string | null;
  changedFilesPath: string | null;
  repairHistoryPath: string | null;
};

/**
 * List all projects under .agent-studio/projects. Returns empty list if
 * the dir doesn't exist (cold-clone case — no projects yet).
 */
export async function listProjects(): Promise<ProjectSummary[]> {
  const root = projectsRoot();
  let entries: string[];
  try {
    entries = await fs.readdir(root);
  } catch {
    return [];
  }

  const summaries = await Promise.all(
    entries.map((dirName) => loadProjectSummary(dirName).catch(() => null)),
  );
  return summaries
    .filter((x): x is ProjectSummary => x !== null)
    .sort((a, b) => a.id.localeCompare(b.id));
}

/**
 * Load a single project's summary by dir name. Returns null if the dir
 * doesn't exist or isn't a project (no task-graph.json AND no .agent/).
 */
export async function loadProjectSummary(
  dirName: string,
): Promise<ProjectSummary | null> {
  const projectPath = path.join(projectsRoot(), dirName);
  let stat;
  try {
    stat = await fs.stat(projectPath);
  } catch {
    return null;
  }
  if (!stat.isDirectory()) return null;
  // Path-allowlist check — refuses anything that escaped via `..` etc.
  try {
    assertReadable(projectPath);
  } catch {
    return null;
  }

  const taskGraph = await readJson<{ tasks?: Array<{ status?: string }> }>(
    path.join(projectPath, "task-graph.json"),
  );
  const tasks = taskGraph?.tasks ?? [];
  const taskCount = tasks.length;
  const completedCount = tasks.filter((t) => t?.status === "completed").length;

  const changesDir = path.join(projectPath, ".agent", "changes");
  const changeCount = await countDirEntries(changesDir);

  const latest = await loadLatestSession(projectPath);

  return {
    id: dirName,
    path: projectPath,
    relPath: relToWorkspace(projectPath),
    taskCount,
    completedCount,
    changeCount,
    latestSessionId: latest?.sessionId ?? null,
    latestSessionStatus: latest?.status ?? null,
  };
}

/**
 * Load full project detail. Returns null if the project doesn't exist.
 */
export async function loadProjectDetail(
  dirName: string,
): Promise<ProjectDetail | null> {
  const summary = await loadProjectSummary(dirName);
  if (!summary) return null;

  const taskGraph = await readJson(
    path.join(summary.path, "task-graph.json"),
  );
  const latestSessionPayload = summary.latestSessionId
    ? await readJson(
        path.join(
          summary.path,
          ".agent",
          "autonomous",
          "sessions",
          summary.latestSessionId,
          "autonomous-session.json",
        ),
      )
    : null;

  const reviewQueue = await loadReviewQueueSummary(summary.path);
  const changes = await loadChangeSummaries(summary.path);

  return {
    ...summary,
    taskGraph,
    latestSession: latestSessionPayload,
    reviewQueue,
    changes,
  };
}

// ---------------------------------------------------------------------------
// Internals
// ---------------------------------------------------------------------------

async function readJson<T = unknown>(p: string): Promise<T | null> {
  try {
    assertReadable(p);
  } catch {
    return null;
  }
  try {
    const text = await fs.readFile(p, "utf-8");
    return JSON.parse(text) as T;
  } catch {
    return null;
  }
}

async function countDirEntries(dirPath: string): Promise<number> {
  try {
    const entries = await fs.readdir(dirPath);
    return entries.length;
  } catch {
    return 0;
  }
}

async function loadLatestSession(projectPath: string): Promise<{
  sessionId: string;
  status: string;
} | null> {
  const sessionsDir = path.join(
    projectPath,
    ".agent",
    "autonomous",
    "sessions",
  );
  let entries: string[];
  try {
    entries = await fs.readdir(sessionsDir);
  } catch {
    return null;
  }
  const candidates: { sessionId: string; mtime: number; status: string }[] = [];
  for (const sessionId of entries) {
    const sessionFile = path.join(
      sessionsDir,
      sessionId,
      "autonomous-session.json",
    );
    let stat;
    try {
      stat = await fs.stat(sessionFile);
    } catch {
      continue;
    }
    const payload = await readJson<{ status?: string }>(sessionFile);
    if (!payload) continue;
    candidates.push({
      sessionId,
      mtime: stat.mtimeMs,
      status: String(payload.status ?? "unknown"),
    });
  }
  if (candidates.length === 0) return null;
  candidates.sort((a, b) => b.mtime - a.mtime);
  const top = candidates[0];
  return { sessionId: top.sessionId, status: top.status };
}

async function loadReviewQueueSummary(projectPath: string): Promise<{
  total: number;
  open: number;
  blocking: number;
  resolved: number;
  items: ReviewItemSummary[];
}> {
  const sessionsDir = path.join(
    projectPath,
    ".agent",
    "autonomous",
    "sessions",
  );
  const items: ReviewItemSummary[] = [];
  let sessions: string[];
  try {
    sessions = await fs.readdir(sessionsDir);
  } catch {
    return { total: 0, open: 0, blocking: 0, resolved: 0, items: [] };
  }
  for (const sessionId of sessions) {
    const reviewDir = path.join(sessionsDir, sessionId, "review-items");
    let files: string[];
    try {
      files = await fs.readdir(reviewDir);
    } catch {
      continue;
    }
    for (const file of files) {
      if (!file.endsWith(".json")) continue;
      const itemPath = path.join(reviewDir, file);
      const payload = await readJson<ReviewItemShape>(itemPath);
      if (!payload) continue;

      const reviewId = String(
        payload.review_id ?? file.replace(/\.json$/, ""),
      );
      const runId =
        firstString(
          payload.run_id,
          payload.task_run_id,
          payload.taskRunId,
          payload.context?.run_id,
        ) ?? null;
      const candidate =
        firstString(
          payload.candidate,
          payload.candidate_id,
          payload.selected_candidate,
          payload.context?.candidate,
        ) ?? null;
      const sourceType =
        firstString(
          payload.source_type,
          payload.sourceType,
          payload.source,
        ) ?? null;
      const summary =
        firstString(
          payload.summary,
          payload.description,
          payload.body,
          payload.message,
        ) ?? null;
      const createdAt =
        firstString(
          payload.created_at,
          payload.createdAt,
          payload.timestamp,
        ) ?? null;

      // Resolve downstream evidence paths (probe existence — only set if real).
      let promotionReportPath: string | null = null;
      let evalResultsPath: string | null = null;
      let changedFilesPath: string | null = null;
      let patchDiffPath: string | null = null;
      if (runId) {
        const runDir = path.join(projectPath, ".agent", "runs", runId);
        promotionReportPath = await resolveIfExists(
          path.join(runDir, "promotion-report.json"),
        );
        const candDir = candidate
          ? path.join(runDir, "candidates", candidate)
          : null;
        if (candDir) {
          evalResultsPath = await resolveIfExists(
            path.join(candDir, "eval-results.json"),
          );
          changedFilesPath = await resolveIfExists(
            path.join(candDir, "changed-files.json"),
          );
          patchDiffPath =
            (await resolveIfExists(path.join(candDir, "patch.diff"))) ??
            (await resolveIfExists(path.join(candDir, "candidate.patch"))) ??
            null;
        }
        if (!evalResultsPath) {
          evalResultsPath = await resolveIfExists(
            path.join(runDir, "eval-results.json"),
          );
        }
        if (!changedFilesPath) {
          changedFilesPath = await resolveIfExists(
            path.join(runDir, "changed-files.json"),
          );
        }
      }
      // Also accept explicit evidence paths from the review JSON itself.
      const explicit = collectStrings(
        payload.evidence,
        payload.evidence_paths,
        payload.evidencePaths,
      );
      if (explicit.length > 0 && !patchDiffPath) {
        const candidateDiff = explicit.find((p) =>
          /\.(diff|patch)$/i.test(p),
        );
        if (candidateDiff) {
          // Resolve explicit path against project root if it's relative.
          const abs = path.isAbsolute(candidateDiff)
            ? candidateDiff
            : path.join(projectPath, candidateDiff);
          patchDiffPath = await resolveIfExists(abs);
        }
      }

      items.push({
        reviewId,
        sessionId,
        status: String(payload.status ?? "open"),
        severity: String(payload.severity ?? "info"),
        reasonCode: String(payload.reason_code ?? payload.reasonCode ?? ""),
        title: String(payload.title ?? ""),
        summary,
        taskId:
          firstString(payload.task_id, payload.taskId, payload.context?.task_id) ??
          null,
        runId,
        candidate,
        sourceType,
        createdAt,
        reviewItemPath: relToWorkspace(itemPath),
        promotionReportPath: promotionReportPath
          ? relToWorkspace(promotionReportPath)
          : null,
        evalResultsPath: evalResultsPath
          ? relToWorkspace(evalResultsPath)
          : null,
        changedFilesPath: changedFilesPath
          ? relToWorkspace(changedFilesPath)
          : null,
        patchDiffPath: patchDiffPath ? relToWorkspace(patchDiffPath) : null,
      });
    }
  }
  // Sort newest-first by createdAt when present, then by reviewId.
  items.sort((a, b) => {
    if (a.createdAt && b.createdAt) {
      return b.createdAt.localeCompare(a.createdAt);
    }
    if (a.createdAt) return -1;
    if (b.createdAt) return 1;
    return b.reviewId.localeCompare(a.reviewId);
  });
  const open = items.filter((i) => i.status === "open").length;
  const blocking = items.filter(
    (i) => i.status === "open" && i.severity === "blocking",
  ).length;
  const resolved = items.length - open;
  return { total: items.length, open, blocking, resolved, items };
}

/**
 * Loose shape for review-item JSON. Schemas evolved across MVP-4D /
 * RC-2B / RC-3 / RC-4; we read defensively.
 */
type ReviewItemShape = {
  review_id?: string;
  status?: string;
  severity?: string;
  reason_code?: string;
  reasonCode?: string;
  title?: string;
  summary?: string;
  description?: string;
  body?: string;
  message?: string;
  task_id?: string | null;
  taskId?: string | null;
  run_id?: string;
  task_run_id?: string;
  taskRunId?: string;
  candidate?: string;
  candidate_id?: string;
  selected_candidate?: string;
  source_type?: string;
  sourceType?: string;
  source?: string;
  created_at?: string;
  createdAt?: string;
  timestamp?: string;
  evidence?: string[];
  evidence_paths?: string[];
  evidencePaths?: string[];
  context?: {
    run_id?: string;
    task_id?: string;
    candidate?: string;
  };
};

async function loadChangeSummaries(projectPath: string): Promise<ChangeSummary[]> {
  const changesDir = path.join(projectPath, ".agent", "changes");
  let entries: string[];
  try {
    entries = await fs.readdir(changesDir);
  } catch {
    return [];
  }
  const summaries: ChangeSummary[] = [];
  for (const changeId of entries) {
    const dir = path.join(changesDir, changeId);
    const stat = await fs.stat(dir).catch(() => null);
    if (!stat?.isDirectory()) continue;

    const contractPath = path.join(dir, "change-contract.json");
    const appliedPath = path.join(dir, "applied-change.json");
    const deliveryPath = path.join(dir, "delivery-report.md");

    const contract = await readJson<{
      goal?: string;
      task_id?: string | null;
    }>(contractPath);
    if (!contract) continue; // not a real change dir

    const hasApplied = await fileExists(appliedPath);
    const hasDelivery = await fileExists(deliveryPath);

    // ----- state derivation -----
    let state: ChangeSummary["state"];
    if (hasApplied && hasDelivery) {
      state = "delivered";
    } else if (hasApplied) {
      state = "applied";
    } else if (hasDelivery) {
      // Read result token from delivery-report.md to derive precise state
      // (matches change_contract.py::_state_from_delivery_report logic).
      try {
        const text = await fs.readFile(deliveryPath, "utf-8");
        const match = text.match(
          /^\*\*(completed|needs-human-review|failed)\b/m,
        );
        const token = match?.[1] ?? null;
        if (token === "needs-human-review") state = "needs_human_review";
        else state = "failed";
      } catch {
        state = "failed";
      }
    } else {
      state = "ready_for_run";
    }

    // ----- enrich from applied-change.json -----
    let branch: string | null = null;
    let sha: string | null = null;
    let runId: string | null = null;
    let candidate: string | null = null;
    let promotionDecision: string | null = null;
    let appliedAt: string | null = null;
    let filesTouched: string[] = [];
    let taskId: string | null = contract.task_id ?? null;

    if (hasApplied) {
      const applied = await readJson<AppliedChangeShape>(appliedPath);
      if (applied) {
        branch = applied.commit?.branch ?? null;
        sha = applied.commit?.sha ?? null;
        runId =
          firstString(applied.run_id, applied.task_run_id, applied.taskRunId) ??
          null;
        candidate =
          firstString(
            applied.candidate,
            applied.selected_candidate,
            applied.selectedCandidate,
            applied.candidate_id,
          ) ?? null;
        promotionDecision =
          firstString(
            applied.promotion_decision,
            applied.promotionDecision,
            applied.decision,
            applied.promotion?.decision,
          ) ?? null;
        appliedAt =
          firstString(
            applied.applied_at,
            applied.appliedAt,
            applied.applied_at_iso,
            applied.timestamp,
          ) ?? null;
        filesTouched = collectStrings(
          applied.files_touched,
          applied.changed_files,
          applied.paths_touched,
          applied.commit?.files,
        );
        taskId = firstString(applied.task_id, taskId) ?? taskId;
      }
    }

    // ----- resolve downstream artifact paths (probe existence) -----
    let promotionReportPath: string | null = null;
    let evalResultsPath: string | null = null;
    let changedFilesPath: string | null = null;
    let repairHistoryPath: string | null = null;

    if (runId) {
      const runDir = path.join(projectPath, ".agent", "runs", runId);
      promotionReportPath = await resolveIfExists(
        path.join(runDir, "promotion-report.json"),
      );
      const candidateDir = candidate
        ? path.join(runDir, "candidates", candidate)
        : null;
      if (candidateDir) {
        evalResultsPath = await resolveIfExists(
          path.join(candidateDir, "eval-results.json"),
        );
        changedFilesPath = await resolveIfExists(
          path.join(candidateDir, "changed-files.json"),
        );
        repairHistoryPath = await resolveIfExists(
          path.join(candidateDir, "repair-history.json"),
        );
      }
      // Fallback: some shapes (older MVP-3 single-candidate) put eval-results
      // straight under the run dir. Probe that too if candidate-scoped is null.
      if (!evalResultsPath) {
        evalResultsPath = await resolveIfExists(
          path.join(runDir, "eval-results.json"),
        );
      }
      if (!changedFilesPath) {
        changedFilesPath = await resolveIfExists(
          path.join(runDir, "changed-files.json"),
        );
      }
    }

    summaries.push({
      changeId,
      state,
      goal: contract.goal ?? null,
      // Paths returned to the client are workspace-relative for safe display
      // and consistent /api/artifact?path= consumption (the route accepts both).
      appliedChangeJson: hasApplied ? relToWorkspace(appliedPath) : null,
      deliveryReportMd: hasDelivery ? relToWorkspace(deliveryPath) : null,
      branch,
      sha,
      taskId,
      runId,
      candidate,
      promotionDecision,
      appliedAt,
      filesTouched,
      promotionReportPath: promotionReportPath
        ? relToWorkspace(promotionReportPath)
        : null,
      evalResultsPath: evalResultsPath ? relToWorkspace(evalResultsPath) : null,
      changedFilesPath: changedFilesPath
        ? relToWorkspace(changedFilesPath)
        : null,
      repairHistoryPath: repairHistoryPath
        ? relToWorkspace(repairHistoryPath)
        : null,
    });
  }
  // Sort newest-first by changeId (lexicographically reverse — change_ids
  // include hex suffix; not strictly time-sorted but stable enough for v1).
  summaries.sort((a, b) => b.changeId.localeCompare(a.changeId));
  return summaries;
}

/**
 * Loose shape for applied-change.json — fields evolved across MVP-3 / RC-4
 * milestones; we read defensively across snake / camel and several
 * historical names.
 */
type AppliedChangeShape = {
  run_id?: string;
  task_run_id?: string;
  taskRunId?: string;
  candidate?: string;
  selected_candidate?: string;
  selectedCandidate?: string;
  candidate_id?: string;
  task_id?: string;
  promotion_decision?: string;
  promotionDecision?: string;
  decision?: string;
  promotion?: { decision?: string };
  applied_at?: string;
  appliedAt?: string;
  applied_at_iso?: string;
  timestamp?: string;
  files_touched?: string[];
  changed_files?: string[];
  paths_touched?: string[];
  commit?: {
    branch?: string;
    sha?: string;
    files?: string[];
  };
};

function firstString(...candidates: unknown[]): string | null {
  for (const c of candidates) {
    if (typeof c === "string" && c.length > 0) return c;
  }
  return null;
}

function collectStrings(...lists: unknown[]): string[] {
  const out: string[] = [];
  const seen = new Set<string>();
  for (const list of lists) {
    if (!Array.isArray(list)) continue;
    for (const item of list) {
      if (typeof item !== "string" || item.length === 0) continue;
      if (seen.has(item)) continue;
      seen.add(item);
      out.push(item);
    }
  }
  return out;
}

/** Returns the absolute path if the file exists AND passes the allowlist; else null. */
async function resolveIfExists(p: string): Promise<string | null> {
  try {
    assertReadable(p);
  } catch {
    return null;
  }
  return (await fileExists(p)) ? p : null;
}

async function fileExists(p: string): Promise<boolean> {
  try {
    const stat = await fs.stat(p);
    return stat.isFile();
  } catch {
    return false;
  }
}
