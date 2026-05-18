/**
 * RC-5A.12.5B — Start Development Run Manager.
 *
 * This module is intentionally narrow: it only runs the hardcoded
 * `agent-studio autonomous preflight` and `agent-studio autonomous start`
 * commands for a prepared runtime project. It never deploys, pushes, publishes,
 * or executes arbitrary shell supplied by the client.
 */

import { spawn, type SpawnOptions } from "node:child_process";
import { existsSync } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { loadProjectDetail } from "./projects";
import { projectsRoot, workspaceRoot } from "./paths";
import { loadStudioProjectSummary } from "./studioProjects";

export type DevelopmentRunState =
  | "starting"
  | "running"
  | "needs_human"
  | "completed"
  | "failed"
  | "stopped";

export type DevelopmentRunPhase =
  | "preflight"
  | "autonomous_start"
  | "post_run"
  | null;

export type DevelopmentRunStatus = {
  runId: string;
  kind: "autonomous_start";
  status: DevelopmentRunState;
  phase: DevelopmentRunPhase;
  agentProjectId: string;
  agentProjectPath: string;
  startedAt: string;
  updatedAt: string;
  finishedAt: string | null;
  pid: number | null;
  sessionId: string | null;
  runtimeSessionStatus: string | null;
  currentTaskId: string | null;
  reviewOpenCount: number;
  reviewBlockingCount: number;
  taskCounts: Record<string, number>;
  preflightExitCode: number | null;
  validateArtifactsExitCode: number | null;
  exitCode: number | null;
  error: string | null;
};

export type StartDevelopmentResult =
  | { ok: true; runId: string }
  | { ok: false; error: string };

export type StopDevelopmentResult =
  | { ok: true; runId: string; status: "stopped" }
  | { ok: false; error: string };

type CommandResult = {
  exitCode: number;
  stdout: string;
  stderr: string;
  errorSummary?: string;
};

type RuntimeMapping = {
  studioProjectPath: string;
  agentProjectId: string;
  agentProjectPathRel: string;
  agentProjectPathAbs: string;
  runtimeDirName: string;
};

export async function startDevelopmentJob(
  studioProjectId: string,
): Promise<StartDevelopmentResult> {
  const mapping = await resolveRuntimeMapping(studioProjectId);
  if (!mapping.ok) return { ok: false, error: mapping.error };

  const active = await readActiveConsoleRun(mapping.value.studioProjectPath);
  if (active) {
    return {
      ok: false,
      error: `another run is already ${active.status} (${active.runId})`,
    };
  }

  const runDetail = await loadProjectDetail(mapping.value.runtimeDirName).catch(
    () => null,
  );
  if (runDetail?.latestSessionStatus === "completed") {
    return {
      ok: false,
      error:
        "runtime project already has a completed session. Use Change Request instead.",
    };
  }
  if ((runDetail?.reviewQueue.blocking ?? 0) > 0) {
    return {
      ok: false,
      error: "runtime project has blocking review items. Resolve them first.",
    };
  }
  const patchWorker = await readConfiguredPatchWorker(
    mapping.value.agentProjectPathAbs,
  );
  if (patchWorker !== "codex") {
    return {
      ok: false,
      error:
        "runtime project is not configured for Codex patch generation. Expected agentic.patch_worker: codex.",
    };
  }

  const runId = "studio_run_" + randomBytes(5).toString("hex");
  const runDir = path.join(mapping.value.studioProjectPath, "runs", runId);
  await fs.mkdir(runDir, { recursive: true });
  await fs.writeFile(path.join(runDir, "stdout.log"), "", "utf-8");
  await fs.writeFile(path.join(runDir, "stderr.log"), "", "utf-8");

  const now = new Date().toISOString();
  const initial: DevelopmentRunStatus = {
    runId,
    kind: "autonomous_start",
    status: "starting",
    phase: "preflight",
    agentProjectId: mapping.value.agentProjectId,
    agentProjectPath: mapping.value.agentProjectPathRel,
    startedAt: now,
    updatedAt: now,
    finishedAt: null,
    pid: null,
    sessionId: runDetail?.latestSessionId ?? null,
    runtimeSessionStatus: runDetail?.latestSessionStatus ?? null,
    currentTaskId: currentTaskId(runDetail?.latestSession),
    reviewOpenCount: runDetail?.reviewQueue.open ?? 0,
    reviewBlockingCount: runDetail?.reviewQueue.blocking ?? 0,
    taskCounts: countTaskStatuses(runDetail?.taskGraph),
    preflightExitCode: null,
    validateArtifactsExitCode: null,
    exitCode: null,
    error: null,
  };
  await writeStatus(runDir, initial);
  await fs.writeFile(
    path.join(runDir, "command.json"),
    JSON.stringify(
      {
        kind: "autonomous_start",
        studioProjectId,
        agentProjectId: mapping.value.agentProjectId,
        agentProjectPath: mapping.value.agentProjectPathRel,
        commands: {
          preflight: {
            cmd: agentStudioPythonBin(),
            args: agentStudioArgs([
              "autonomous",
              "preflight",
              "--project",
              mapping.value.agentProjectId,
            ]),
            cwd: workspaceRoot(),
          },
          start: {
            cmd: agentStudioPythonBin(),
            args: agentStudioArgs([
              "autonomous",
              "start",
              "--project",
              mapping.value.agentProjectId,
            ]),
            cwd: workspaceRoot(),
          },
        },
        deploy: false,
        gitPush: false,
        startedAt: now,
      },
      null,
      2,
    ),
    "utf-8",
  );

  void executeDevelopmentRun(runDir, mapping.value);
  return { ok: true, runId };
}

export async function stopDevelopmentJob(
  studioProjectId: string,
): Promise<StopDevelopmentResult> {
  const summary = await loadStudioProjectSummary(studioProjectId);
  if (!summary) {
    return { ok: false, error: `studio project not found: ${studioProjectId}` };
  }
  const active = await readLatestDevelopmentRunStatus(summary.path);
  const status = active?.status;
  if (!status || !isActiveDevelopmentState(status.status)) {
    return { ok: false, error: "no active development run" };
  }

  const runDir = path.join(summary.path, "runs", status.runId);
  const pidRecord = await readPidRecord(runDir);
  const pid = pidRecord?.pid ?? status.pid;
  if (!pid || !Number.isInteger(pid) || pid <= 0) {
    return { ok: false, error: "active run has no recorded pid" };
  }

  try {
    process.kill(pid, "SIGTERM");
  } catch (exc) {
    return { ok: false, error: `failed to stop recorded pid ${pid}: ${String(exc)}` };
  }

  status.status = "stopped";
  status.phase = null;
  status.finishedAt = new Date().toISOString();
  status.error = "stopped by operator";
  await touchStatus(runDir, status);
  await fs.appendFile(
    path.join(runDir, "stderr.log"),
    `[studio-run] sent SIGTERM to recorded pid ${pid}\n`,
    "utf-8",
  );
  return { ok: true, runId: status.runId, status: "stopped" };
}

export async function readLatestDevelopmentRunStatus(
  studioProjectPath: string,
  opts?: { tailLines?: number },
): Promise<{
  status: DevelopmentRunStatus | null;
  stdoutTail: string;
  stderrTail: string;
} | null> {
  const latest = await findLatestRunDir(studioProjectPath, "autonomous_start");
  if (!latest) return null;
  let status = await readStatus(latest.path);
  if (!status) return null;
  status = await hydrateRuntimeSignals(latest.path, status);
  const tail = opts?.tailLines ?? 80;
  return {
    status,
    stdoutTail: await readTail(path.join(latest.path, "stdout.log"), tail),
    stderrTail: await readTail(path.join(latest.path, "stderr.log"), tail),
  };
}

async function executeDevelopmentRun(
  runDir: string,
  mapping: RuntimeMapping,
): Promise<void> {
  let status = (await readStatus(runDir))!;
  await appendStdout(
    runDir,
    `[studio-run] starting autonomous run for ${mapping.agentProjectId}\n`,
  );

  const clean = await runCommandCapture("git", ["status", "--porcelain"], {
    cwd: mapping.agentProjectPathAbs,
    runDir,
  });
  if (clean.exitCode !== 0) {
    status.status = "failed";
    status.phase = null;
    status.finishedAt = new Date().toISOString();
    status.exitCode = clean.exitCode;
    status.error = "runtime git status failed before autonomous start";
    await touchStatus(runDir, status);
    return;
  }
  if (clean.stdout.trim().length > 0) {
    status.status = "failed";
    status.phase = null;
    status.finishedAt = new Date().toISOString();
    status.exitCode = 1;
    status.error = "runtime git worktree is dirty";
    await appendStderr(runDir, `[studio-run] dirty worktree:\n${clean.stdout}\n`);
    await touchStatus(runDir, status);
    return;
  }

  const preflight = await runCommandCapture(
    agentStudioPythonBin(),
    agentStudioArgs([
      "autonomous",
      "preflight",
      "--project",
      mapping.agentProjectId,
    ]),
    { cwd: workspaceRoot(), runDir },
  );
  status = await hydrateRuntimeSignals(runDir, status);
  status.preflightExitCode = preflight.exitCode;
  if (preflight.exitCode !== 0) {
    status.status = "failed";
    status.phase = null;
    status.finishedAt = new Date().toISOString();
    status.exitCode = preflight.exitCode;
    status.error = "autonomous preflight failed";
    await touchStatus(runDir, status);
    return;
  }

  status.status = "running";
  status.phase = "autonomous_start";
  await touchStatus(runDir, status);

  const cmd = agentStudioPythonBin();
  const args = agentStudioArgs([
    "autonomous",
    "start",
    "--project",
    mapping.agentProjectId,
  ]);
  const spawnOpts: SpawnOptions = {
    cwd: workspaceRoot(),
    env: redactEnv(process.env),
    stdio: ["ignore", "pipe", "pipe"],
  };
  let child;
  try {
    child = spawn(cmd, args, spawnOpts);
  } catch (exc) {
    status.status = "failed";
    status.phase = null;
    status.finishedAt = new Date().toISOString();
    status.exitCode = 127;
    status.error = `failed to spawn autonomous start: ${String(exc)}`;
    await touchStatus(runDir, status);
    await appendStderr(runDir, `[spawn-error] ${status.error}\n`);
    return;
  }

  status.pid = child.pid ?? null;
  await touchStatus(runDir, status);
  await fs.writeFile(
    path.join(runDir, "pid.json"),
    JSON.stringify(
      {
        pid: child.pid ?? null,
        cmd,
        args,
        cwd: workspaceRoot(),
        startedAt: new Date().toISOString(),
      },
      null,
      2,
    ),
    "utf-8",
  );
  await appendStdout(runDir, `$ ${cmd} ${args.join(" ")}  (cwd=${workspaceRoot()})\n`);

  child.stdout?.on("data", (chunk: Buffer) => {
    void appendStdout(runDir, chunk.toString());
  });
  child.stderr?.on("data", (chunk: Buffer) => {
    void appendStderr(runDir, chunk.toString());
  });
  child.on("error", (err) => {
    void appendStderr(runDir, `[spawn-error] ${err.message}\n`);
  });
  child.on("close", (code) => {
    void finalizeDevelopmentRun(runDir, mapping, code ?? 1);
  });
}

async function finalizeDevelopmentRun(
  runDir: string,
  mapping: RuntimeMapping,
  exitCode: number,
): Promise<void> {
  let status = await readStatus(runDir);
  if (!status) return;
  if (status.status === "stopped") {
    status.exitCode = exitCode;
    await touchStatus(runDir, status);
    return;
  }

  status.phase = "post_run";
  status.exitCode = exitCode;
  await touchStatus(runDir, status);

  const cliStatus = await runCommandCapture(
    agentStudioPythonBin(),
    agentStudioArgs([
      "autonomous",
      "status",
      "--project",
      mapping.agentProjectId,
      "--json",
    ]),
    { cwd: workspaceRoot(), runDir },
  );
  applyAutonomousStatusJson(status, cliStatus.stdout);

  const validate = await runCommandCapture(
    agentStudioPythonBin(),
    agentStudioArgs([
      "autonomous",
      "validate-artifacts",
      "--project",
      mapping.agentProjectId,
      "--json",
    ]),
    { cwd: workspaceRoot(), runDir },
  );
  status.validateArtifactsExitCode = validate.exitCode;
  status = await hydrateRuntimeSignals(runDir, status);

  if (status.reviewBlockingCount > 0 || status.reviewOpenCount > 0) {
    status.status = "needs_human";
    status.error = "runtime has open review items";
  } else if (status.runtimeSessionStatus === "paused") {
    status.status = "needs_human";
    status.error = "autonomous session paused";
  } else if (exitCode !== 0) {
    status.status = "failed";
    status.error = `autonomous start exited ${exitCode}`;
  } else if (validate.exitCode !== 0) {
    status.status = "needs_human";
    status.error = "artifact validation reported errors";
  } else {
    const hygiene = await runPostRunHygiene(runDir, mapping.agentProjectPathAbs);
    if (!hygiene.ok) {
      status.status = "needs_human";
      status.error = hygiene.error;
    } else {
      status.status = "completed";
      status.error = null;
    }
  }
  status.phase = null;
  status.finishedAt = new Date().toISOString();
  await touchStatus(runDir, status);
}

async function runPostRunHygiene(
  runDir: string,
  runtimePath: string,
): Promise<{ ok: true } | { ok: false; error: string }> {
  const status = await runCommandCapture("git", ["status", "--porcelain"], {
    cwd: runtimePath,
    runDir,
  });
  if (status.exitCode !== 0) {
    return { ok: false, error: "git status failed after autonomous run" };
  }
  const dirty = parsePorcelain(status.stdout);
  if (dirty.length === 0) return { ok: true };

  const onlyTaskGraph = dirty.every((entry) => entry.path === "task-graph.json");
  if (!onlyTaskGraph) {
    return {
      ok: false,
      error: "dirty_worktree_after_run",
    };
  }

  const add = await runCommandCapture("git", ["add", "task-graph.json"], {
    cwd: runtimePath,
    runDir,
  });
  if (add.exitCode !== 0) {
    return { ok: false, error: "failed to stage completed task graph" };
  }
  const commit = await runCommandCapture(
    "git",
    ["commit", "-q", "-m", "Record completed task graph"],
    { cwd: runtimePath, runDir },
  );
  if (commit.exitCode !== 0) {
    return { ok: false, error: "failed to commit completed task graph" };
  }
  return { ok: true };
}

async function resolveRuntimeMapping(
  studioProjectId: string,
): Promise<{ ok: true; value: RuntimeMapping } | { ok: false; error: string }> {
  const summary = await loadStudioProjectSummary(studioProjectId);
  if (!summary) {
    return { ok: false, error: `studio project not found: ${studioProjectId}` };
  }
  const agentProjectId = summary.meta.agentProjectId;
  const agentProjectPathRel = summary.meta.agentProjectPath;
  if (!agentProjectId || !agentProjectPathRel) {
    return { ok: false, error: "Runtime project is not linked yet." };
  }

  const abs = path.resolve(workspaceRoot(), agentProjectPathRel);
  const root = path.resolve(projectsRoot());
  if (abs !== root && !abs.startsWith(root + path.sep)) {
    return { ok: false, error: "linked runtime project path is outside .agent-studio/projects" };
  }
  try {
    const stat = await fs.stat(abs);
    if (!stat.isDirectory()) {
      return { ok: false, error: "linked runtime project path is not a directory" };
    }
  } catch {
    return { ok: false, error: "linked runtime project path does not exist" };
  }

  return {
    ok: true,
    value: {
      studioProjectPath: summary.path,
      agentProjectId,
      agentProjectPathRel,
      agentProjectPathAbs: abs,
      runtimeDirName: path.basename(abs),
    },
  };
}

async function readConfiguredPatchWorker(runtimePath: string): Promise<string> {
  let text = "";
  try {
    text = await fs.readFile(path.join(runtimePath, "agent-studio.yaml"), "utf-8");
  } catch {
    return "none";
  }
  const lines = text.split("\n");
  let inAgentic = false;
  for (const line of lines) {
    if (/^\S/.test(line)) {
      inAgentic = /^agentic:\s*(#.*)?$/.test(line.trim());
      continue;
    }
    if (!inAgentic) continue;
    const match = /^\s+patch_worker:\s*([A-Za-z0-9_-]+)/.exec(line);
    if (match) return match[1] ?? "none";
  }
  return "none";
}

async function readActiveConsoleRun(
  studioProjectPath: string,
): Promise<{ runId: string; status: string } | null> {
  const runsDir = path.join(studioProjectPath, "runs");
  let entries: string[];
  try {
    entries = await fs.readdir(runsDir);
  } catch {
    return null;
  }
  for (const entry of entries) {
    const status = await readUnknownStatus(path.join(runsDir, entry));
    if (!status) continue;
    const state =
      typeof status.status === "string"
        ? status.status
        : typeof status.state === "string"
          ? status.state
          : "";
    if (["queued", "starting", "running"].includes(state)) {
      return {
        runId: typeof status.runId === "string" ? status.runId : entry,
        status: state,
      };
    }
  }
  return null;
}

async function findLatestRunDir(
  studioProjectPath: string,
  kind: "autonomous_start",
): Promise<{ id: string; path: string; mtime: number } | null> {
  const runsDir = path.join(studioProjectPath, "runs");
  let entries: string[];
  try {
    entries = await fs.readdir(runsDir);
  } catch {
    return null;
  }
  const matches: { id: string; path: string; mtime: number }[] = [];
  for (const entry of entries) {
    const dir = path.join(runsDir, entry);
    const status = await readUnknownStatus(dir);
    if (status?.kind !== kind) continue;
    try {
      const stat = await fs.stat(dir);
      matches.push({ id: entry, path: dir, mtime: stat.mtimeMs });
    } catch {
      // ignore stale entry
    }
  }
  matches.sort((a, b) => b.mtime - a.mtime);
  return matches[0] ?? null;
}

async function hydrateRuntimeSignals(
  runDir: string,
  status: DevelopmentRunStatus,
): Promise<DevelopmentRunStatus> {
  const detail = await loadProjectDetail(path.basename(status.agentProjectPath)).catch(
    () => null,
  );
  status.sessionId = detail?.latestSessionId ?? status.sessionId;
  status.runtimeSessionStatus = detail?.latestSessionStatus ?? status.runtimeSessionStatus;
  status.currentTaskId = currentTaskId(detail?.latestSession);
  status.reviewOpenCount = detail?.reviewQueue.open ?? status.reviewOpenCount;
  status.reviewBlockingCount =
    detail?.reviewQueue.blocking ?? status.reviewBlockingCount;
  status.taskCounts = countTaskStatuses(detail?.taskGraph, status.currentTaskId);
  await touchStatus(runDir, status);
  return status;
}

function applyAutonomousStatusJson(
  status: DevelopmentRunStatus,
  stdout: string,
): void {
  try {
    const payload = JSON.parse(stdout) as Record<string, unknown>;
    const session = payload.session as Record<string, unknown> | null;
    const review = payload.review_status as Record<string, unknown> | null;
    const counts = payload.task_counts as Record<string, unknown> | null;
    if (session) {
      status.sessionId =
        typeof session.session_id === "string" ? session.session_id : status.sessionId;
      status.runtimeSessionStatus =
        typeof session.status === "string" ? session.status : status.runtimeSessionStatus;
      status.currentTaskId =
        typeof session.current_task_id === "string" ? session.current_task_id : null;
    }
    if (review) {
      status.reviewOpenCount = asNumber(review.open_review_count, status.reviewOpenCount);
      status.reviewBlockingCount = asNumber(
        review.blocking_review_count,
        status.reviewBlockingCount,
      );
    }
    if (counts) {
      status.taskCounts = Object.fromEntries(
        Object.entries(counts).map(([k, v]) => [k, asNumber(v, 0)]),
      );
    }
  } catch {
    // The JSON command is an enhancement; artifact reads are the fallback.
  }
}

function currentTaskId(session: unknown): string | null {
  if (!session || typeof session !== "object") return null;
  const value = (session as Record<string, unknown>).current_task_id;
  return typeof value === "string" ? value : null;
}

function countTaskStatuses(
  taskGraph: unknown,
  currentRunningTaskId?: string | null,
): Record<string, number> {
  const tasks =
    taskGraph && typeof taskGraph === "object"
      ? (taskGraph as { tasks?: Array<{ id?: unknown; status?: unknown }> }).tasks
      : null;
  const counts: Record<string, number> = {
    completed: 0,
    pending: 0,
    running: 0,
    "needs-human-review": 0,
    abandoned: 0,
  };
  if (!Array.isArray(tasks)) return counts;
  for (const task of tasks) {
    const taskId = typeof task.id === "string" ? task.id : null;
    const declaredStatus = typeof task.status === "string" ? task.status : "unknown";
    const key =
      currentRunningTaskId && taskId === currentRunningTaskId && declaredStatus === "pending"
        ? "running"
        : declaredStatus;
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return counts;
}

function parsePorcelain(text: string): Array<{ status: string; path: string }> {
  return text
    .split("\n")
    .map((line) => line.trimEnd())
    .filter(Boolean)
    .map((line) => {
      const status = line.slice(0, 2);
      const rawPath = line.slice(3).trim();
      return {
        status,
        path: rawPath.includes(" -> ") ? rawPath.split(" -> ").pop()! : rawPath,
      };
    });
}

async function runCommandCapture(
  cmd: string,
  args: string[],
  opts: { cwd: string; runDir: string },
): Promise<CommandResult> {
  return new Promise<CommandResult>((resolve) => {
    const spawnOpts: SpawnOptions = {
      cwd: opts.cwd,
      env: redactEnv(process.env),
      stdio: ["ignore", "pipe", "pipe"],
    };
    let stdout = "";
    let stderr = "";
    let child;
    try {
      child = spawn(cmd, args, spawnOpts);
    } catch (exc) {
      void appendStderr(opts.runDir, `[spawn-error] ${cmd}: ${String(exc)}\n`);
      resolve({
        exitCode: 127,
        stdout,
        stderr,
        errorSummary: `spawn failed: ${String(exc)}`,
      });
      return;
    }
    void appendStdout(opts.runDir, `$ ${cmd} ${args.join(" ")}  (cwd=${opts.cwd})\n`);
    child.stdout?.on("data", (chunk: Buffer) => {
      const text = chunk.toString();
      stdout += text;
      void appendStdout(opts.runDir, text);
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      const text = chunk.toString();
      stderr += text;
      void appendStderr(opts.runDir, text);
    });
    child.on("error", (err) => {
      stderr += err.message;
      void appendStderr(opts.runDir, `[spawn-error] ${cmd}: ${err.message}\n`);
      resolve({ exitCode: 127, stdout, stderr, errorSummary: err.message });
    });
    child.on("close", (code) => {
      resolve({ exitCode: code ?? 1, stdout, stderr });
    });
  });
}

async function readStatus(runDir: string): Promise<DevelopmentRunStatus | null> {
  try {
    const raw = await fs.readFile(path.join(runDir, "status.json"), "utf-8");
    const parsed = JSON.parse(raw) as DevelopmentRunStatus;
    return parsed.kind === "autonomous_start" ? parsed : null;
  } catch {
    return null;
  }
}

async function readUnknownStatus(
  runDir: string,
): Promise<Record<string, unknown> | null> {
  try {
    const raw = await fs.readFile(path.join(runDir, "status.json"), "utf-8");
    return JSON.parse(raw) as Record<string, unknown>;
  } catch {
    return null;
  }
}

async function writeStatus(
  runDir: string,
  status: DevelopmentRunStatus,
): Promise<void> {
  await fs.writeFile(
    path.join(runDir, "status.json"),
    JSON.stringify(status, null, 2),
    "utf-8",
  );
}

async function touchStatus(
  runDir: string,
  status: DevelopmentRunStatus,
): Promise<void> {
  status.updatedAt = new Date().toISOString();
  await writeStatus(runDir, status);
}

async function readPidRecord(runDir: string): Promise<{ pid?: number } | null> {
  try {
    const raw = await fs.readFile(path.join(runDir, "pid.json"), "utf-8");
    return JSON.parse(raw) as { pid?: number };
  } catch {
    return null;
  }
}

async function readTail(filePath: string, lines: number): Promise<string> {
  try {
    const text = await fs.readFile(filePath, "utf-8");
    return text.split("\n").slice(-lines - 1).join("\n");
  } catch {
    return "";
  }
}

function isActiveDevelopmentState(status: DevelopmentRunState): boolean {
  return status === "starting" || status === "running";
}

function agentStudioPythonBin(): string {
  const fromEnv = process.env.AGENT_STUDIO_PYTHON;
  const candidates = [
    fromEnv,
    "/opt/homebrew/opt/python@3.13/bin/python3.13",
    "/opt/homebrew/bin/python3.13",
    "/usr/local/bin/python3.13",
    "python3",
  ].filter((x): x is string => Boolean(x));

  for (const candidate of candidates) {
    if (!path.isAbsolute(candidate) || existsSync(candidate)) return candidate;
  }
  return "python3";
}

function agentStudioArgs(args: string[]): string[] {
  return [path.join(workspaceRoot(), "agent-studio"), "--root", workspaceRoot(), ...args];
}

function redactEnv(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const out = { ...env };
  const mutable = out as Record<string, string | undefined>;
  mutable.NODE_ENV = undefined;
  mutable.NEXT_PHASE = undefined;
  const secretPatterns = [
    /API_KEY/i,
    /TOKEN/i,
    /SECRET/i,
    /PASSWORD/i,
    /PRIVATE_KEY/i,
  ];
  for (const key of Object.keys(out)) {
    if (secretPatterns.some((pattern) => pattern.test(key))) {
      delete out[key];
    }
  }
  return out;
}

function asNumber(value: unknown, fallback: number): number {
  return typeof value === "number" && Number.isFinite(value) ? value : fallback;
}

async function appendStdout(runDir: string, text: string): Promise<void> {
  await fs.appendFile(path.join(runDir, "stdout.log"), text, "utf-8");
}

async function appendStderr(runDir: string, text: string): Promise<void> {
  await fs.appendFile(path.join(runDir, "stderr.log"), text, "utf-8");
}
