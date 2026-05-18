import { spawn } from "node:child_process";
import { existsSync } from "node:fs";
import fs from "node:fs/promises";
import net from "node:net";
import path from "node:path";
import { projectsRoot, workspaceRoot } from "./paths";
import { loadStudioProjectSummary } from "./studioProjects";

export type PreviewRunState = "stopped" | "starting" | "running" | "failed";

export type PreviewRunStatus = {
  status: PreviewRunState;
  url: string | null;
  port: number | null;
  pid: number | null;
  startedAt: string | null;
  stoppedAt: string | null;
  updatedAt: string;
  error: string | null;
  agentProjectId: string | null;
  agentProjectPath: string | null;
};

export type PreviewRunResponse = {
  status: PreviewRunStatus;
  stdoutTail: string;
  stderrTail: string;
};

export type PreviewRunResult =
  | { ok: true; status: PreviewRunStatus }
  | { ok: false; error: string };

type PreviewContext =
  | {
      ok: true;
      runDir: string;
      basePort: number;
      agentProjectId: string;
      agentProjectPathRel: string;
      agentProjectPathAbs: string;
    }
  | { ok: false; error: string };

type PidRecord = {
  pid: number | null;
  port: number | null;
  url: string | null;
  startedAt: string | null;
  stoppedAt: string | null;
};

export async function readPreviewStatus(
  studioProjectId: string,
  opts?: { tailLines?: number },
): Promise<PreviewRunResponse> {
  const context = await previewContext(studioProjectId);
  if (!context.ok) {
    return {
      status: baseStatus(null, null, null, null, context.error),
      stdoutTail: "",
      stderrTail: "",
    };
  }

  const saved =
    (await readStatus(context.runDir)) ??
    baseStatus(
      context.basePort,
      previewUrl(context.basePort),
      context.agentProjectId,
      context.agentProjectPathRel,
      null,
  );
  const status = await hydrateLifecycle(context.runDir, saved);
  await writeStatus(context.runDir, status);
  if (status.pid || status.startedAt || status.stoppedAt) {
    await writePidRecord(context.runDir, {
      pid: status.pid,
      port: status.port,
      url: status.url,
      startedAt: status.startedAt,
      stoppedAt: status.stoppedAt,
    });
  }

  const tail = opts?.tailLines ?? 80;
  return {
    status,
    stdoutTail: await readTail(path.join(context.runDir, "stdout.log"), tail),
    stderrTail: await readTail(path.join(context.runDir, "stderr.log"), tail),
  };
}

export async function startPreviewServer(
  studioProjectId: string,
  opts?: { restart?: boolean },
): Promise<PreviewRunResult> {
  const context = await previewContext(studioProjectId);
  if (!context.ok) return { ok: false, error: context.error };

  await fs.mkdir(context.runDir, { recursive: true });

  const current = await readPreviewStatus(studioProjectId, { tailLines: 1 });
  if (current.status.status === "running" && current.status.pid) {
    if (!opts?.restart) return { ok: true, status: current.status };
    await stopPreviewServer(studioProjectId);
  }

  if (!existsSync(path.join(context.agentProjectPathAbs, "package.json"))) {
    return { ok: false, error: "runtime project has no package.json" };
  }

  const nextBin = path.join(context.agentProjectPathAbs, "node_modules", ".bin", "next");
  if (!existsSync(nextBin)) {
    return {
      ok: false,
      error: "Next.js binary not found. Run Prepare Runtime Project before starting preview.",
    };
  }

  const port = await findAvailablePort(context.basePort);
  if (port == null) {
    return {
      ok: false,
      error: `No preview port available in ${context.basePort}-${context.basePort + 50}.`,
    };
  }

  const url = previewUrl(port);
  await fs.writeFile(path.join(context.runDir, "stdout.log"), "", "utf-8");
  await fs.writeFile(path.join(context.runDir, "stderr.log"), "", "utf-8");

  const now = new Date().toISOString();
  const status: PreviewRunStatus = {
    status: "starting",
    url,
    port,
    pid: null,
    startedAt: now,
    stoppedAt: null,
    updatedAt: now,
    error: null,
    agentProjectId: context.agentProjectId,
    agentProjectPath: context.agentProjectPathRel,
  };
  await writeStatus(context.runDir, status);

  const runtimeLocalEnv = await readRuntimeDotEnvLocal(context.agentProjectPathAbs);
  const child = spawn(
    nextBin,
    ["start", "-p", String(port), "--hostname", "127.0.0.1"],
    {
      cwd: context.agentProjectPathAbs,
      detached: false,
      stdio: ["ignore", "pipe", "pipe"],
      env: {
        ...process.env,
        ...runtimeLocalEnv,
        NODE_ENV: "production",
        NEXT_TELEMETRY_DISABLED: "1",
      },
    },
  );

  status.pid = child.pid ?? null;
  status.updatedAt = new Date().toISOString();
  await writeStatus(context.runDir, status);
  await writePidRecord(context.runDir, {
    pid: status.pid,
    port,
    url,
    startedAt: status.startedAt,
    stoppedAt: null,
  });

  child.stdout?.on("data", (chunk: Buffer) => {
    void fs.appendFile(path.join(context.runDir, "stdout.log"), chunk);
  });
  child.stderr?.on("data", (chunk: Buffer) => {
    void fs.appendFile(path.join(context.runDir, "stderr.log"), chunk);
  });
  child.on("error", (err) => {
    void markPreviewStopped(context.runDir, "failed", err.message);
  });
  child.on("close", (code) => {
    void (async () => {
      const latest = await readStatus(context.runDir);
      if (latest?.status !== "running" && latest?.status !== "starting") return;
      await markPreviewStopped(
        context.runDir,
        code === 0 ? "stopped" : "failed",
        code === 0 ? null : `preview process exited with code ${code ?? "unknown"}`,
      );
    })();
  });
  child.unref();

  const ready = await waitForHttp(url, 8000);
  const latest = (await readStatus(context.runDir)) ?? status;
  if (ready && latest.status === "starting") {
    latest.status = "running";
    latest.error = null;
    latest.updatedAt = new Date().toISOString();
    await writeStatus(context.runDir, latest);
    return { ok: true, status: latest };
  }

  return { ok: true, status: latest };
}

export async function stopPreviewServer(
  studioProjectId: string,
): Promise<PreviewRunResult> {
  const context = await previewContext(studioProjectId);
  if (!context.ok) return { ok: false, error: context.error };

  const status =
    (await readStatus(context.runDir)) ??
    baseStatus(
      context.basePort,
      previewUrl(context.basePort),
      context.agentProjectId,
      context.agentProjectPathRel,
      null,
    );
  const pidRecord = await readPidRecord(context.runDir);
  const recordedPid = pidRecord?.pid ?? status.pid;

  if (recordedPid && Number.isInteger(recordedPid) && recordedPid > 0) {
    try {
      process.kill(recordedPid, "SIGTERM");
    } catch {
      // Already gone. The final state still becomes stopped.
    }
  }

  status.status = "stopped";
  status.pid = null;
  status.error = null;
  status.stoppedAt = new Date().toISOString();
  status.updatedAt = status.stoppedAt;
  await writeStatus(context.runDir, status);
  await writePidRecord(context.runDir, {
    pid: null,
    port: status.port,
    url: status.url,
    startedAt: status.startedAt,
    stoppedAt: status.stoppedAt,
  });
  return { ok: true, status };
}

async function previewContext(studioProjectId: string): Promise<PreviewContext> {
  const summary = await loadStudioProjectSummary(studioProjectId);
  if (!summary) return { ok: false, error: `studio project not found: ${studioProjectId}` };
  if (!summary.agentProjectId || !summary.agentProjectPath) {
    return { ok: false, error: "Runtime project is not linked yet." };
  }

  const agentProjectPathAbs = path.isAbsolute(summary.agentProjectPath)
    ? path.resolve(summary.agentProjectPath)
    : path.resolve(workspaceRoot(), summary.agentProjectPath);
  const root = path.resolve(projectsRoot());
  if (
    agentProjectPathAbs !== root &&
    !agentProjectPathAbs.startsWith(root + path.sep)
  ) {
    return { ok: false, error: "runtime project path is outside .agent-studio/projects" };
  }
  try {
    const stat = await fs.stat(agentProjectPathAbs);
    if (!stat.isDirectory()) return { ok: false, error: "runtime project path is not a directory" };
  } catch {
    return { ok: false, error: "runtime project path does not exist" };
  }

  return {
    ok: true,
    runDir: path.join(summary.path, "preview"),
    basePort: previewPort(studioProjectId),
    agentProjectId: summary.agentProjectId,
    agentProjectPathRel: summary.agentProjectPath,
    agentProjectPathAbs,
  };
}

async function readRuntimeDotEnvLocal(projectDir: string): Promise<Record<string, string>> {
  const envPath = path.join(projectDir, ".env.local");
  let raw: string;
  try {
    raw = await fs.readFile(envPath, "utf-8");
  } catch {
    return {};
  }

  const env: Record<string, string> = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;

    const match = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(trimmed);
    if (!match) continue;

    let value = match[2] ?? "";
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    }
    env[match[1]] = value;
  }
  return env;
}

async function hydrateLifecycle(
  runDir: string,
  status: PreviewRunStatus,
): Promise<PreviewRunStatus> {
  if (
    (status.status === "running" || status.status === "starting") &&
    status.pid &&
    !isPidAlive(status.pid)
  ) {
    status.status = "failed";
    status.error = "preview process is not running";
    status.stoppedAt = new Date().toISOString();
    status.updatedAt = status.stoppedAt;
    await writeStatus(runDir, status);
    return status;
  }

  if (status.status === "starting" && status.url && (await canFetch(status.url))) {
    status.status = "running";
    status.error = null;
    status.updatedAt = new Date().toISOString();
    await writeStatus(runDir, status);
  }
  return status;
}

function previewPort(studioProjectId: string): number {
  let hash = 0;
  for (const ch of studioProjectId) {
    hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  }
  return 4100 + (hash % 1000);
}

function previewUrl(port: number): string {
  return `http://127.0.0.1:${port}`;
}

function baseStatus(
  port: number | null,
  url: string | null,
  agentProjectId: string | null,
  agentProjectPath: string | null,
  error: string | null,
): PreviewRunStatus {
  return {
    status: error ? "failed" : "stopped",
    url,
    port,
    pid: null,
    startedAt: null,
    stoppedAt: null,
    updatedAt: new Date().toISOString(),
    error,
    agentProjectId,
    agentProjectPath,
  };
}

async function readStatus(runDir: string): Promise<PreviewRunStatus | null> {
  try {
    const raw = await fs.readFile(path.join(runDir, "status.json"), "utf-8");
    const parsed = JSON.parse(raw) as Partial<PreviewRunStatus> & {
      state?: string;
    };
    const status = parsed.status ?? parsed.state;
    if (
      status !== "stopped" &&
      status !== "starting" &&
      status !== "running" &&
      status !== "failed"
    ) {
      return null;
    }
    return {
      status,
      url: typeof parsed.url === "string" ? parsed.url : null,
      port: typeof parsed.port === "number" ? parsed.port : null,
      pid: typeof parsed.pid === "number" ? parsed.pid : null,
      startedAt: typeof parsed.startedAt === "string" ? parsed.startedAt : null,
      stoppedAt: typeof parsed.stoppedAt === "string" ? parsed.stoppedAt : null,
      updatedAt:
        typeof parsed.updatedAt === "string"
          ? parsed.updatedAt
          : new Date().toISOString(),
      error: typeof parsed.error === "string" ? parsed.error : null,
      agentProjectId:
        typeof parsed.agentProjectId === "string" ? parsed.agentProjectId : null,
      agentProjectPath:
        typeof parsed.agentProjectPath === "string" ? parsed.agentProjectPath : null,
    };
  } catch {
    return null;
  }
}

async function writeStatus(runDir: string, status: PreviewRunStatus): Promise<void> {
  await fs.mkdir(runDir, { recursive: true });
  await fs.writeFile(
    path.join(runDir, "status.json"),
    JSON.stringify(status, null, 2),
    "utf-8",
  );
}

async function readPidRecord(runDir: string): Promise<PidRecord | null> {
  try {
    const raw = await fs.readFile(path.join(runDir, "pid.json"), "utf-8");
    const parsed = JSON.parse(raw) as Partial<PidRecord>;
    return {
      pid: typeof parsed.pid === "number" ? parsed.pid : null,
      port: typeof parsed.port === "number" ? parsed.port : null,
      url: typeof parsed.url === "string" ? parsed.url : null,
      startedAt: typeof parsed.startedAt === "string" ? parsed.startedAt : null,
      stoppedAt: typeof parsed.stoppedAt === "string" ? parsed.stoppedAt : null,
    };
  } catch {
    return null;
  }
}

async function writePidRecord(runDir: string, record: PidRecord): Promise<void> {
  await fs.mkdir(runDir, { recursive: true });
  await fs.writeFile(
    path.join(runDir, "pid.json"),
    JSON.stringify(record, null, 2),
    "utf-8",
  );
}

async function markPreviewStopped(
  runDir: string,
  statusValue: "stopped" | "failed",
  error: string | null,
): Promise<void> {
  const status = await readStatus(runDir);
  if (!status) return;
  status.status = statusValue;
  status.pid = null;
  status.error = error;
  status.stoppedAt = new Date().toISOString();
  status.updatedAt = status.stoppedAt;
  await writeStatus(runDir, status);
  await writePidRecord(runDir, {
    pid: null,
    port: status.port,
    url: status.url,
    startedAt: status.startedAt,
    stoppedAt: status.stoppedAt,
  });
}

async function findAvailablePort(basePort: number): Promise<number | null> {
  for (let port = basePort; port <= basePort + 50; port += 1) {
    if (await isPortAvailable(port)) return port;
  }
  return null;
}

function isPortAvailable(port: number): Promise<boolean> {
  return new Promise((resolve) => {
    const server = net.createServer();
    server.once("error", () => resolve(false));
    server.once("listening", () => {
      server.close(() => resolve(true));
    });
    server.listen(port, "127.0.0.1");
  });
}

async function canFetch(url: string): Promise<boolean> {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1000) });
    return response.status < 500;
  } catch {
    return false;
  }
}

async function waitForHttp(url: string, timeoutMs: number): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (await canFetch(url)) return true;
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  return false;
}

function isPidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function readTail(filePath: string, lines: number): Promise<string> {
  try {
    const text = await fs.readFile(filePath, "utf-8");
    const parts = text.split(/\r?\n/);
    return parts.slice(Math.max(0, parts.length - lines)).join("\n").trimEnd();
  } catch {
    return "";
  }
}
