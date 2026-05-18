/**
 * RC-5A.12.5A — Runtime Project Bootstrap.
 *
 * 自动化 Mini Release Notes Builder dogfood 里手工跑的 8 步链路：
 *   1. agent-studio init                  (idempotent)
 *   2. agent-studio new --from <mvp>      (生成 runtime project + task graph)
 *   3. copy templates/nextjs-app/         (Next.js 脚手架)
 *   4. write agent-studio.yaml            (codex worker, deploy disabled)
 *   5. npm install
 *   6. npm run typecheck
 *   7. npm run build
 *   8. git init + baseline commit
 *   9. agent-studio autonomous preflight
 *
 * 状态写入 .studio-console/projects/<id>/runs/<run_id>/{status.json,
 * stdout.log, stderr.log, command.json}。
 *
 * 不做：
 * - autonomous start —— 留给 RC-5A.12.5B
 * - 任何网络 I/O 除了 npm install 隐式拉包
 * - 任何 git push / vercel deploy
 * - secret 透传 —— prepare 不需要 OPENAI/ANTHROPIC API key
 */

import { spawn, type SpawnOptions } from "node:child_process";
import { existsSync } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { projectsRoot, studioProjectsRoot, templatesRoot, workspaceRoot } from "./paths";
import { loadStudioProjectSummary } from "./studioProjects";

// ===========================================================================
// 类型
// ===========================================================================

export type StepState = "pending" | "running" | "completed" | "failed" | "skipped";

export type StepStatus = {
  id: string;
  label: string;
  state: StepState;
  startedAt: string | null;
  completedAt: string | null;
  exitCode: number | null;
  /** 简短失败说明（如适用）。完整 stderr 在 stderr.log。 */
  errorSummary: string | null;
};

export type RunState =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "stopped";

export type RunStatus = {
  runId: string;
  type: "prepare";
  state: RunState;
  startedAt: string;
  updatedAt: string;
  completedAt: string | null;
  currentStep: string | null;
  steps: StepStatus[];
  /** 解析出的 runtime project id（agent-studio new 之后设置）。 */
  agentProjectId: string | null;
  /** workspace-relative 路径。 */
  agentProjectPath: string | null;
  error: string | null;
};

export type PrepareKickoffResult =
  | { ok: true; runId: string }
  | { ok: false; error: string };

export type LinkRuntimeResult =
  | { ok: true; agentProjectId: string; agentProjectPath: string }
  | { ok: false; error: string };

// ===========================================================================
// 步骤定义
// ===========================================================================

type StepDef = {
  id: string;
  label: string;
  /** 已经满足条件就跳过，避免重跑。 */
  skipIf?: (ctx: PrepareContext) => Promise<boolean>;
  /** 真正执行；返回 exitCode 0 视为成功。 */
  run: (ctx: PrepareContext) => Promise<{ exitCode: number; errorSummary?: string }>;
};

type PrepareContext = {
  studioProjectId: string;
  studioProjectPath: string;        // .studio-console/projects/<id>/
  contractMvpAbsPath: string;
  runDir: string;                    // .studio-console/projects/<id>/runs/<run_id>/
  // populated mid-flow:
  agentProjectId: string | null;
  agentProjectPath: string | null;   // .agent-studio/projects/<agentProjectId>/
  // appended each spawn:
  appendStdout: (line: string) => Promise<void>;
  appendStderr: (line: string) => Promise<void>;
};

// ===========================================================================
// 入口
// ===========================================================================

/**
 * 同步阶段：校验前置条件 + 创建 run dir + 写 status.json，立刻返回 runId。
 * 异步阶段：在后台 promise 中跑 8 步。前端轮询 /api/studio-projects/[id]/run
 * 拿进度。
 *
 * 单 project 同时只允许一个活跃 run；如已有 → 返回错误。
 */
export async function startPrepareJob(
  studioProjectId: string,
): Promise<PrepareKickoffResult> {
  const summary = await loadStudioProjectSummary(studioProjectId);
  if (!summary) {
    return { ok: false, error: `studio project not found: ${studioProjectId}` };
  }
  if (!summary.contract.locked) {
    return {
      ok: false,
      error: "contract is not locked — lock the MVP contract before preparing the runtime project",
    };
  }
  // mvp-requirements.md 必须存在且非空
  const contractMvpAbs = path.join(
    summary.path,
    "contract",
    "mvp-requirements.md",
  );
  try {
    const stat = await fs.stat(contractMvpAbs);
    if (!stat.isFile() || stat.size === 0) {
      return { ok: false, error: "contract/mvp-requirements.md is empty" };
    }
  } catch {
    return { ok: false, error: "contract/mvp-requirements.md is missing" };
  }

  // 已有活跃 run → 拒绝
  const active = await readActiveRun(summary.path);
  if (active && (active.state === "running" || active.state === "queued")) {
    return {
      ok: false,
      error: `another run is already ${active.state} (${active.runId}). Wait for it to finish.`,
    };
  }

  // 已有 runtime mapping / run state → 拒绝（避免无意覆盖）
  if (summary.agentProjectId || summary.agentProjectPath || summary.hasRunState) {
    return {
      ok: false,
      error:
        "runtime project already exists for this studio project. Delete the mapped .agent-studio runtime project and clear agentProjectId/agentProjectPath if you want to re-bootstrap.",
    };
  }

  const runId = "run_" + randomBytes(5).toString("hex");
  const runDir = path.join(summary.path, "runs", runId);
  await fs.mkdir(runDir, { recursive: true });

  const now = new Date().toISOString();
  const initialStatus: RunStatus = {
    runId,
    type: "prepare",
    state: "queued",
    startedAt: now,
    updatedAt: now,
    completedAt: null,
    currentStep: null,
    steps: STEP_DEFS.map((s) => ({
      id: s.id,
      label: s.label,
      state: "pending",
      startedAt: null,
      completedAt: null,
      exitCode: null,
      errorSummary: null,
    })),
    agentProjectId: null,
    agentProjectPath: null,
    error: null,
  };
  await writeStatus(runDir, initialStatus);

  await fs.writeFile(
    path.join(runDir, "command.json"),
    JSON.stringify(
      {
        type: "prepare",
        studioProjectId,
        contractMvpRelPath: path.relative(workspaceRoot(), contractMvpAbs),
        startedAt: now,
      },
      null,
      2,
    ),
    "utf-8",
  );
  await fs.writeFile(path.join(runDir, "stdout.log"), "", "utf-8");
  await fs.writeFile(path.join(runDir, "stderr.log"), "", "utf-8");

  // 后台 fire-and-forget。任何 throw 都被 _execute 捕获并写到 status.json。
  void _execute({
    runId,
    runDir,
    studioProjectId,
    studioProjectPath: summary.path,
    contractMvpAbsPath: contractMvpAbs,
  });

  return { ok: true, runId };
}

/**
 * Manual dogfood escape hatch: link a Studio project to an already-created
 * runtime project under .agent-studio/projects/. Accepts a CLI project id
 * (`project_xxx`), a runtime dir name, a workspace-relative path, or an
 * absolute runtime path.
 */
export async function linkExistingRuntimeProject(
  studioProjectId: string,
  runtimeRef: string,
): Promise<LinkRuntimeResult> {
  const summary = await loadStudioProjectSummary(studioProjectId);
  if (!summary) {
    return { ok: false, error: `studio project not found: ${studioProjectId}` };
  }
  const resolved = await resolveRuntimeProjectRef(runtimeRef);
  if (!resolved) {
    return {
      ok: false,
      error:
        "runtime project not found. Use project_xxx, a .agent-studio/projects/<dir> path, or a runtime directory name.",
    };
  }
  await persistAgentMapping({
    studioProjectPath: summary.path,
    agentProjectId: resolved.agentProjectId,
    agentProjectPath: path.relative(workspaceRoot(), resolved.agentProjectPath),
  });
  return {
    ok: true,
    agentProjectId: resolved.agentProjectId,
    agentProjectPath: path.relative(workspaceRoot(), resolved.agentProjectPath),
  };
}

/**
 * 读取最新（active OR 最近完成）的 run 状态，含 stdout/stderr 尾部。
 */
export async function readLatestRunStatus(
  studioProjectPath: string,
  opts?: { tailLines?: number },
): Promise<{
  status: RunStatus | null;
  stdoutTail: string;
  stderrTail: string;
} | null> {
  const runsDir = path.join(studioProjectPath, "runs");
  let entries: string[];
  try {
    entries = await fs.readdir(runsDir);
  } catch {
    return null;
  }
  if (entries.length === 0) return null;
  const stats = await Promise.all(
    entries.map(async (id) => {
      const dir = path.join(runsDir, id);
      const status = await readStatus(dir);
      if (!status) return null;
      try {
        const stat = await fs.stat(dir);
        return { id, mtime: stat.mtimeMs };
      } catch {
        return null;
      }
    }),
  );
  const sorted = stats
    .filter((x): x is { id: string; mtime: number } => x !== null)
    .sort((a, b) => b.mtime - a.mtime);
  if (sorted.length === 0) return null;
  const runDir = path.join(runsDir, sorted[0].id);
  const status = await readStatus(runDir);
  if (!status) return null;
  const tail = opts?.tailLines ?? 30;
  const stdoutTail = await readTail(path.join(runDir, "stdout.log"), tail);
  const stderrTail = await readTail(path.join(runDir, "stderr.log"), tail);
  return { status, stdoutTail, stderrTail };
}

// ===========================================================================
// 步骤定义（顺序执行）
// ===========================================================================

const AGENT_STUDIO_YAML_BODY = `# Generated by Studio Console (RC-5A.12.5A bootstrap).
# Codex patch worker, single candidate to keep tokens low, deploy disabled.
agentic:
  patch_worker: codex
  codex:
    command: codex
    sandbox: workspace-write
    ask_for_approval: never
    timeout_sec: 360
    max_prompt_chars: 60000

autonomous:
  budgets:
    max_candidates_per_task: 1
    max_repair_attempts_per_candidate: 1

deploy:
  enabled: false
`;

const STEP_DEFS: StepDef[] = [
  {
    id: "agent-studio-init",
    label: "agent-studio init",
    skipIf: async () => {
      // .agent-studio at workspace root → already initialized
      try {
        const stat = await fs.stat(path.join(workspaceRoot(), ".agent-studio"));
        return stat.isDirectory();
      } catch {
        return false;
      }
    },
    run: async (ctx) => {
      return spawnAndCapture(agentStudioPythonBin(), agentStudioArgs(["init"]), {
        cwd: workspaceRoot(),
        ctx,
      });
    },
  },
  {
    id: "agent-studio-new",
    label: "agent-studio new --from <mvp>",
    skipIf: async (ctx) => ctx.agentProjectId !== null,
    run: async (ctx) => {
      const before = await listAgentProjects();
      const result = await spawnAndCapture(
        agentStudioPythonBin(),
        agentStudioArgs(["new", "--from", ctx.contractMvpAbsPath]),
        { cwd: workspaceRoot(), ctx },
      );
      if (result.exitCode !== 0) return result;
      const after = await listAgentProjects();
      const created = after.find((id) => !before.includes(id));
      if (!created) {
        return {
          exitCode: 1,
          errorSummary:
            "agent-studio new exited 0 but no new project dir appeared under .agent-studio/projects/",
          };
      }
      ctx.agentProjectPath = path.join(projectsRoot(), created);
      ctx.agentProjectId = (await readAgentProjectId(ctx.agentProjectPath)) ?? created;
      await ctx.appendStdout(
        `[bootstrap] detected new runtime project: ${ctx.agentProjectId} (${created})\n`,
      );
      return result;
    },
  },
  {
    id: "copy-template",
    label: "copy templates/nextjs-app/",
    run: async (ctx) => {
      if (!ctx.agentProjectPath) {
        return { exitCode: 1, errorSummary: "agentProjectPath not set" };
      }
      const tplDir = path.join(templatesRoot(), "nextjs-app");
      try {
        await copyDirRecursive(tplDir, ctx.agentProjectPath);
        await ctx.appendStdout(`[bootstrap] template copied to ${ctx.agentProjectPath}\n`);
        return { exitCode: 0 };
      } catch (exc) {
        return {
          exitCode: 1,
          errorSummary: `copy failed: ${String(exc)}`,
        };
      }
    },
  },
  {
    id: "agent-studio-yaml",
    label: "write agent-studio.yaml",
    run: async (ctx) => {
      if (!ctx.agentProjectPath) {
        return { exitCode: 1, errorSummary: "agentProjectPath not set" };
      }
      const yamlPath = path.join(ctx.agentProjectPath, "agent-studio.yaml");
      try {
        await fs.writeFile(yamlPath, AGENT_STUDIO_YAML_BODY, "utf-8");
        await ctx.appendStdout(`[bootstrap] wrote ${yamlPath}\n`);
        return { exitCode: 0 };
      } catch (exc) {
        return {
          exitCode: 1,
          errorSummary: `write failed: ${String(exc)}`,
        };
      }
    },
  },
  {
    id: "npm-install",
    label: "npm install",
    run: async (ctx) => {
      if (!ctx.agentProjectPath) {
        return { exitCode: 1, errorSummary: "agentProjectPath not set" };
      }
      return spawnAndCapture("npm", ["install"], {
        cwd: ctx.agentProjectPath,
        ctx,
      });
    },
  },
  {
    id: "typecheck",
    label: "npm run typecheck",
    run: async (ctx) => {
      if (!ctx.agentProjectPath) {
        return { exitCode: 1, errorSummary: "agentProjectPath not set" };
      }
      return spawnAndCapture("npm", ["run", "typecheck"], {
        cwd: ctx.agentProjectPath,
        ctx,
      });
    },
  },
  {
    id: "build",
    label: "npm run build",
    run: async (ctx) => {
      if (!ctx.agentProjectPath) {
        return { exitCode: 1, errorSummary: "agentProjectPath not set" };
      }
      return spawnAndCapture("npm", ["run", "build"], {
        cwd: ctx.agentProjectPath,
        ctx,
      });
    },
  },
  {
    id: "git-baseline",
    label: "git init + baseline commit",
    run: async (ctx) => {
      if (!ctx.agentProjectPath) {
        return { exitCode: 1, errorSummary: "agentProjectPath not set" };
      }
      const cwd = ctx.agentProjectPath;
      const init = await spawnAndCapture("git", ["init", "-q", "-b", "main"], { cwd, ctx });
      if (init.exitCode !== 0) return init;
      // git config — defensive in case the repo doesn't inherit user.email/name
      await spawnAndCapture("git", ["config", "user.email", "studio-console@local"], { cwd, ctx });
      await spawnAndCapture("git", ["config", "user.name", "Studio Console"], { cwd, ctx });
      const add = await spawnAndCapture("git", ["add", "."], { cwd, ctx });
      if (add.exitCode !== 0) return add;
      const commit = await spawnAndCapture(
        "git",
        ["commit", "-q", "-m", "baseline: scaffolded by Studio Console"],
        { cwd, ctx },
      );
      return commit;
    },
  },
  {
    id: "preflight",
    label: "agent-studio autonomous preflight",
    run: async (ctx) => {
      if (!ctx.agentProjectId) {
        return { exitCode: 1, errorSummary: "agentProjectId not set" };
      }
      return spawnAndCapture(
        agentStudioPythonBin(),
        agentStudioArgs(["autonomous", "preflight", "--project", ctx.agentProjectId]),
        { cwd: workspaceRoot(), ctx },
      );
    },
  },
];

// ===========================================================================
// 执行器
// ===========================================================================

async function _execute(args: {
  runId: string;
  runDir: string;
  studioProjectId: string;
  studioProjectPath: string;
  contractMvpAbsPath: string;
}): Promise<void> {
  const { runId, runDir, studioProjectId, studioProjectPath, contractMvpAbsPath } = args;
  const ctx: PrepareContext = {
    studioProjectId,
    studioProjectPath,
    contractMvpAbsPath,
    runDir,
    agentProjectId: null,
    agentProjectPath: null,
    appendStdout: (line) => fs.appendFile(path.join(runDir, "stdout.log"), line, "utf-8"),
    appendStderr: (line) => fs.appendFile(path.join(runDir, "stderr.log"), line, "utf-8"),
  };

  const status = (await readStatus(runDir))!;
  status.state = "running";
  status.updatedAt = new Date().toISOString();
  await writeStatus(runDir, status);

  for (const def of STEP_DEFS) {
    const step = status.steps.find((s) => s.id === def.id)!;
    try {
      if (def.skipIf && (await def.skipIf(ctx))) {
        step.state = "skipped";
        step.startedAt = new Date().toISOString();
        step.completedAt = step.startedAt;
        await ctx.appendStdout(`[bootstrap] step ${def.id} skipped (precondition met)\n`);
        await touchStatus(runDir, status);
        continue;
      }
      step.state = "running";
      step.startedAt = new Date().toISOString();
      status.currentStep = def.id;
      await touchStatus(runDir, status);
      await ctx.appendStdout(`\n[bootstrap] === step ${def.id}: ${def.label} ===\n`);

      const result = await def.run(ctx);
      step.exitCode = result.exitCode;
      step.completedAt = new Date().toISOString();
      if (result.exitCode === 0) {
        step.state = "completed";
        await ctx.appendStdout(`[bootstrap] step ${def.id} → exit 0\n`);
      } else {
        step.state = "failed";
        step.errorSummary = result.errorSummary ?? `exit ${result.exitCode}`;
        status.state = "failed";
        status.error = `step ${def.id} failed: ${step.errorSummary}`;
        status.currentStep = null;
        status.completedAt = new Date().toISOString();
        await touchStatus(runDir, status);
        await ctx.appendStderr(
          `[bootstrap] step ${def.id} failed: ${step.errorSummary}\n`,
        );
        return;
      }
      // 成功后把 ctx 状态同步进 status.json
      status.agentProjectId = ctx.agentProjectId;
      status.agentProjectPath = ctx.agentProjectPath
        ? path.relative(workspaceRoot(), ctx.agentProjectPath)
        : null;
      await touchStatus(runDir, status);
    } catch (exc) {
      step.state = "failed";
      step.errorSummary = String(exc);
      step.completedAt = new Date().toISOString();
      status.state = "failed";
      status.error = `step ${def.id} threw: ${String(exc)}`;
      status.currentStep = null;
      status.completedAt = new Date().toISOString();
      await touchStatus(runDir, status);
      await ctx.appendStderr(
        `[bootstrap] step ${def.id} threw: ${String(exc)}\n`,
      );
      return;
    }
  }

  // 全部步骤通过 — 先落盘 mapping，再把 run 标记为 completed。这样前端看到
  // completed 后刷新 project detail 时，agentProjectId 已经可读。
  await ctx.appendStdout(
    `\n[bootstrap] all steps completed. agentProjectId=${ctx.agentProjectId}\n`,
  );
  if (!ctx.agentProjectId || !ctx.agentProjectPath) {
    status.state = "failed";
    status.currentStep = null;
    status.completedAt = new Date().toISOString();
    status.error = "runtime mapping was not detected after bootstrap";
    await touchStatus(runDir, status);
    await ctx.appendStderr(`[bootstrap] failed: ${status.error}\n`);
    return;
  }

  try {
    await persistAgentMapping({
      studioProjectPath,
      agentProjectId: ctx.agentProjectId,
      agentProjectPath: path.relative(workspaceRoot(), ctx.agentProjectPath),
    });
  } catch (exc) {
    status.state = "failed";
    status.currentStep = null;
    status.completedAt = new Date().toISOString();
    status.error = `failed to persist runtime mapping: ${String(exc)}`;
    await touchStatus(runDir, status);
    await ctx.appendStderr(`[bootstrap] ${status.error}\n`);
    return;
  }

  status.state = "completed";
  status.currentStep = null;
  status.completedAt = new Date().toISOString();
  await touchStatus(runDir, status);
}

// ===========================================================================
// 工具
// ===========================================================================

async function readActiveRun(studioProjectPath: string): Promise<RunStatus | null> {
  const out = await readLatestRunStatus(studioProjectPath);
  return out?.status ?? null;
}

async function writeStatus(runDir: string, status: RunStatus): Promise<void> {
  await fs.writeFile(
    path.join(runDir, "status.json"),
    JSON.stringify(status, null, 2),
    "utf-8",
  );
}

async function touchStatus(runDir: string, status: RunStatus): Promise<void> {
  status.updatedAt = new Date().toISOString();
  await writeStatus(runDir, status);
}

async function readStatus(runDir: string): Promise<RunStatus | null> {
  try {
    const raw = await fs.readFile(path.join(runDir, "status.json"), "utf-8");
    const parsed = JSON.parse(raw) as Partial<RunStatus> & { type?: unknown };
    return parsed.type === "prepare" ? (parsed as RunStatus) : null;
  } catch {
    return null;
  }
}

async function readTail(filePath: string, lines: number): Promise<string> {
  try {
    const text = await fs.readFile(filePath, "utf-8");
    const all = text.split("\n");
    const tail = all.slice(-lines - 1);
    return tail.join("\n");
  } catch {
    return "";
  }
}

async function listAgentProjects(): Promise<string[]> {
  try {
    const entries = await fs.readdir(projectsRoot());
    return entries;
  } catch {
    return [];
  }
}

async function readAgentProjectId(agentProjectPath: string): Promise<string | null> {
  try {
    const text = await fs.readFile(
      path.join(agentProjectPath, ".agent", "project.yaml"),
      "utf-8",
    );
    const match = /^id:\s*(.+?)\s*$/m.exec(text);
    return match?.[1]?.replace(/^["']|["']$/g, "") ?? null;
  } catch {
    return null;
  }
}

async function resolveRuntimeProjectRef(
  runtimeRef: string,
): Promise<{ agentProjectId: string; agentProjectPath: string } | null> {
  const ref = runtimeRef.trim();
  if (!ref) return null;

  const byPath = await resolveRuntimeProjectPath(ref);
  if (byPath) return byPath;

  if (ref.startsWith("project_")) {
    for (const dirName of await listAgentProjects()) {
      const candidatePath = path.join(projectsRoot(), dirName);
      const candidateId = await readAgentProjectId(candidatePath);
      if (candidateId === ref) {
        return { agentProjectId: candidateId, agentProjectPath: candidatePath };
      }
    }
  }

  return null;
}

async function resolveRuntimeProjectPath(
  runtimeRef: string,
): Promise<{ agentProjectId: string; agentProjectPath: string } | null> {
  const trimmed = runtimeRef.trim();
  const candidates = [
    path.isAbsolute(trimmed) ? trimmed : null,
    trimmed.startsWith(".agent-studio/")
      ? path.join(workspaceRoot(), trimmed)
      : null,
    path.join(projectsRoot(), trimmed),
  ].filter((x): x is string => Boolean(x));

  for (const candidate of candidates) {
    const resolved = path.resolve(candidate);
    const root = path.resolve(projectsRoot());
    if (resolved !== root && !resolved.startsWith(root + path.sep)) continue;
    try {
      const stat = await fs.stat(resolved);
      if (!stat.isDirectory()) continue;
    } catch {
      continue;
    }
    const agentProjectId = await readAgentProjectId(resolved);
    if (!agentProjectId) continue;
    return { agentProjectId, agentProjectPath: resolved };
  }
  return null;
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

/**
 * Recursive copy without external deps. Skips node_modules / .git / .next
 * just in case (templates shouldn't ship those, but defense in depth).
 */
async function copyDirRecursive(src: string, dst: string): Promise<void> {
  await fs.mkdir(dst, { recursive: true });
  const entries = await fs.readdir(src, { withFileTypes: true });
  for (const entry of entries) {
    if (entry.name === "node_modules" || entry.name === ".git" || entry.name === ".next") {
      continue;
    }
    const s = path.join(src, entry.name);
    const d = path.join(dst, entry.name);
    if (entry.isDirectory()) {
      await copyDirRecursive(s, d);
    } else if (entry.isFile()) {
      await fs.copyFile(s, d);
    }
    // symlinks intentionally skipped — templates should be plain files
  }
}

/**
 * Spawn a child process; stream stdout/stderr to the run's log files line by
 * line; resolve with exit code (or 1 if process errored before exit).
 *
 * Defensive timeouts: npm install can legitimately take a long time on a cold
 * cache, so this v1 has NO hard timeout. If you need a guard, add it as a
 * Stop button in RC-5A.12.5B's run manager.
 */
async function spawnAndCapture(
  cmd: string,
  args: string[],
  opts: {
    cwd: string;
    ctx: PrepareContext;
    env?: NodeJS.ProcessEnv;
  },
): Promise<{ exitCode: number; errorSummary?: string }> {
  return new Promise<{ exitCode: number; errorSummary?: string }>((resolve) => {
    const spawnOpts: SpawnOptions = {
      cwd: opts.cwd,
      // Inherit PATH so `agent-studio` / `npm` / `git` resolve. Filter
      // potential secrets out of the child env.
      env: redactEnv({ ...process.env, ...(opts.env ?? {}) }),
      stdio: ["ignore", "pipe", "pipe"],
    };
    let child;
    try {
      child = spawn(cmd, args, spawnOpts);
    } catch (exc) {
      void opts.ctx.appendStderr(`[spawn-error] ${cmd}: ${String(exc)}\n`);
      resolve({ exitCode: 127, errorSummary: `spawn failed: ${String(exc)}` });
      return;
    }
    void opts.ctx.appendStdout(`$ ${cmd} ${args.join(" ")}  (cwd=${opts.cwd})\n`);
    child.stdout?.on("data", (chunk: Buffer) => {
      void opts.ctx.appendStdout(chunk.toString());
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      void opts.ctx.appendStderr(chunk.toString());
    });
    child.on("error", (err) => {
      void opts.ctx.appendStderr(`[spawn-error] ${cmd}: ${err.message}\n`);
      resolve({ exitCode: 127, errorSummary: err.message });
    });
    child.on("close", (code) => {
      resolve({ exitCode: code ?? 1 });
    });
  });
}

/**
 * Strip well-known secret env keys from the child env. Bootstrap doesn't
 * need them — agent-studio new + npm install + tsc + build + git all run
 * without LLM credentials.
 */
function redactEnv(env: NodeJS.ProcessEnv): NodeJS.ProcessEnv {
  const out = { ...env };
  // Do not leak the Console server's runtime mode into generated apps. In
  // development, Next.js sets NODE_ENV=development for the server process; if
  // inherited by `npm run build`, the child Next build can behave incorrectly.
  const mutable = out as Record<string, string | undefined>;
  mutable.NODE_ENV = undefined;
  mutable.NEXT_PHASE = undefined;
  const SECRET_KEYS = [
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
    "GITHUB_TOKEN",
    "VERCEL_TOKEN",
    "STRIPE_SECRET_KEY",
    "SLACK_TOKEN",
  ];
  for (const k of SECRET_KEYS) {
    if (k in out) delete out[k];
  }
  return out;
}

/**
 * Patch agent_project_id / agent_project_path into the studio project's
 * project.json. We re-read first so we don't clobber other fields a future
 * subtask might add.
 */
async function persistAgentMapping(args: {
  studioProjectPath: string;
  agentProjectId: string;
  agentProjectPath: string;
}): Promise<void> {
  const projectJson = path.join(args.studioProjectPath, "project.json");
  let raw: Record<string, unknown> = {};
  try {
    const text = await fs.readFile(projectJson, "utf-8");
    raw = JSON.parse(text) as Record<string, unknown>;
  } catch {
    // project.json should exist; if not, write a fresh one with the mapping.
  }
  raw.agentProjectId = args.agentProjectId;
  raw.agentProjectPath = args.agentProjectPath;
  raw.updatedAt = new Date().toISOString();
  await fs.writeFile(projectJson, JSON.stringify(raw, null, 2), "utf-8");
}
