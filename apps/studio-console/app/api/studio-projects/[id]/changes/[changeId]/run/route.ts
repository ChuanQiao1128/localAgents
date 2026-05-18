import { execFile, spawn } from "node:child_process";
import { existsSync } from "node:fs";
import fs from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import { promisify } from "node:util";
import { NextResponse } from "next/server";
import { runChangeRequestQuality } from "@/lib/changeRequestQuality";
import { workspaceRoot } from "@/lib/paths";
import {
  loadChangeDraft,
  loadStudioProjectSummary,
} from "@/lib/studioProjects";

export const dynamic = "force-dynamic";

const execFileAsync = promisify(execFile);

type ChangeRunState =
  | "queued"
  | "starting"
  | "running"
  | "repairing"
  | "stopping"
  | "stopped"
  | "completed"
  | "failed"
  | "needs_human";

type SupervisorDiagnosis = {
  severity: "info" | "warning" | "error";
  title: string;
  summary: string;
  nextAction: string;
};

type ChangeRunStatus = {
  kind: "change_run";
  runId: string;
  draftId: string;
  status: ChangeRunState;
  currentStep: string | null;
  agentProjectId: string;
  agentProjectPath?: string | null;
  changeRequestPath: string;
  startedAt: string;
  updatedAt: string;
  finishedAt: string | null;
  exitCode: number | null;
  error: string | null;
  currentPid: number | null;
  currentCandidate?: string | null;
  currentStrategy?: string | null;
  failureType?: string | null;
  repairAttempt?: number | null;
  elapsedSec?: number;
  lastHeartbeat?: string | null;
  diagnosis?: SupervisorDiagnosis | null;
  watchdog?: {
    policy: string;
    timeoutCandidateIds: string[];
    triggeredAt: string | null;
  } | null;
};

type RuntimeCandidateSummary = {
  id: string;
  strategy: string | null;
  patchStatus: string | null;
  patchReason: string | null;
  sourcePatchPresent: boolean | null;
  evalPassed: boolean | null;
  failureType: string | null;
  repairAttempts: number | null;
  repairStopReason: string | null;
  repairAction: string | null;
  lastEvent: string | null;
};

type RuntimeRunSummary = {
  runId: string;
  path: string;
  decision: string | null;
  selectedCandidate: string | null;
  candidateCount: number;
  candidates: RuntimeCandidateSummary[];
} | null;

type SupervisorSnapshot = {
  state: ChangeRunState;
  isActive: boolean;
  isStale: boolean;
  elapsedSec: number;
  lastHeartbeat: string | null;
  currentStep: string | null;
  currentPid: number | null;
  currentCandidate: string | null;
  currentStrategy: string | null;
  failureType: string | null;
  repairAttempt: number | null;
  diagnosis: SupervisorDiagnosis | null;
};

function invalidId(id: string): boolean {
  return !id || id.includes("/") || id.includes("\\") || id.includes("..");
}

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string; changeId: string }> },
) {
  const { id, changeId } = await params;
  if (invalidId(id) || invalidId(changeId)) {
    return NextResponse.json({ error: "invalid id" }, { status: 400 });
  }

  try {
    const summary = await loadStudioProjectSummary(id);
    if (!summary) {
      return NextResponse.json({ error: "project not found" }, { status: 404 });
    }
    return NextResponse.json(
      await readLatestChangeRun(
        summary.path,
        changeId,
        summary.agentProjectPath,
      ),
    );
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to read change run", detail: String(exc) },
      { status: 500 },
    );
  }
}

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string; changeId: string }> },
) {
  const { id, changeId } = await params;
  if (invalidId(id) || invalidId(changeId)) {
    return NextResponse.json({ ok: false, error: "invalid id" }, { status: 400 });
  }

  try {
    const summary = await loadStudioProjectSummary(id);
    if (!summary) {
      return NextResponse.json(
        { ok: false, error: "project not found" },
        { status: 404 },
      );
    }
    if (!summary.agentProjectId || !summary.agentProjectPath) {
      return NextResponse.json(
        { ok: false, error: "Runtime project is not linked yet." },
        { status: 400 },
      );
    }

    const active = await readActiveChangeRun(summary.path);
    if (active) {
      return NextResponse.json(
        {
          ok: false,
          error: `another change run is already ${active.status} (${active.runId})`,
        },
        { status: 409 },
      );
    }

    const draft = await loadChangeDraft(id, changeId);
    if (!draft) {
      return NextResponse.json(
        { ok: false, error: "change draft not found" },
        { status: 404 },
      );
    }
    const quality = runChangeRequestQuality(draft.content);
    if (!quality.passed) {
      return NextResponse.json(
        {
          ok: false,
          error: "change request quality scan has errors",
          checks: quality.checks.filter((check) => !check.passed),
        },
        { status: 400 },
      );
    }

    const runId = `change_run_${randomBytes(5).toString("hex")}`;
    const runDir = path.join(summary.path, "runs", runId);
    await fs.mkdir(runDir, { recursive: true });
    await fs.writeFile(path.join(runDir, "stdout.log"), "", "utf-8");
    await fs.writeFile(path.join(runDir, "stderr.log"), "", "utf-8");

    const now = new Date().toISOString();
    const status: ChangeRunStatus = {
      kind: "change_run",
      runId,
      draftId: changeId,
      status: "queued",
      currentStep: "change new",
      agentProjectId: summary.agentProjectId,
      agentProjectPath: summary.agentProjectPath,
      changeRequestPath: draft.changeRequestPath,
      startedAt: now,
      updatedAt: now,
      finishedAt: null,
      exitCode: null,
      error: null,
      currentPid: null,
      currentCandidate: null,
      currentStrategy: null,
      failureType: null,
      repairAttempt: null,
      elapsedSec: 0,
      lastHeartbeat: now,
      diagnosis: null,
      watchdog: null,
    };
    await writeStatus(runDir, status);
    await fs.writeFile(
      path.join(runDir, "command.json"),
      JSON.stringify(
        {
          kind: "change_run",
          studioProjectId: id,
          draftId: changeId,
          agentProjectId: summary.agentProjectId,
          changeRequestPath: draft.changeRequestPath,
          deploy: false,
          gitPush: false,
          commands: [
            "change new",
            "change validate",
            "change run",
            "change status",
            "change validate",
          ],
        },
        null,
        2,
      ),
      "utf-8",
    );

    void executeChangeRun(runDir, status);
    return NextResponse.json({ ok: true, runId, status }, { status: 202 });
  } catch (exc) {
    return NextResponse.json(
      { ok: false, error: String(exc instanceof Error ? exc.message : exc) },
      { status: 500 },
    );
  }
}

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ id: string; changeId: string }> },
) {
  const { id, changeId } = await params;
  if (invalidId(id) || invalidId(changeId)) {
    return NextResponse.json({ ok: false, error: "invalid id" }, { status: 400 });
  }

  try {
    const summary = await loadStudioProjectSummary(id);
    if (!summary) {
      return NextResponse.json(
        { ok: false, error: "project not found" },
        { status: 404 },
      );
    }
    const latest = await latestChangeRun(summary.path, changeId);
    if (!latest?.status) {
      return NextResponse.json(
        { ok: false, error: "change run not found" },
        { status: 404 },
      );
    }
    if (!isActiveState(latest.status.status)) {
      const cleanup = await cleanupRuntimeAfterStoppedRun(
        summary.agentProjectPath,
        changeId,
      );
      if (cleanup.log) {
        await append(path.join(latest.dir, "stdout.log"), `\n[studio] ${cleanup.log}\n`);
      }
      return NextResponse.json({ ok: true, status: latest.status, cleanup });
    }

    const stopping: ChangeRunStatus = {
      ...latest.status,
      status: "stopping",
      error: "Stopping run process tree...",
      diagnosis: {
        severity: "warning",
        title: "Stopping run",
        summary: "Studio is terminating the recorded change-run process tree.",
        nextAction: "Wait for the run state to become stopped before starting another change.",
      },
    };
    await writeStatus(latest.dir, stopping);

    const pid = latest.status.currentPid;
    if (pid) {
      await stopProcessGroup(pid);
    }
    const cleanup = await cleanupRuntimeAfterStoppedRun(
      summary.agentProjectPath,
      changeId,
    );

    const stopped: ChangeRunStatus = {
      ...latest.status,
      status: "stopped",
      currentStep: null,
      currentPid: null,
      elapsedSec: elapsedSec(latest.status.startedAt),
      lastHeartbeat: new Date().toISOString(),
      finishedAt: new Date().toISOString(),
      exitCode: null,
      error: pid
        ? `Stopped by operator. Terminated recorded pid ${pid}.`
        : "Stopped by operator. No recorded child pid was available.",
      diagnosis: {
        severity: "warning",
        title: "Run stopped",
        summary: "The change run was stopped by the operator before promotion completed.",
        nextAction: "Review the candidate panel, then rerun the change from Studio when ready.",
      },
    };
    await append(
      path.join(latest.dir, "stdout.log"),
      `\n[studio] ${stopped.error}\n${cleanup.log ? `[studio] ${cleanup.log}\n` : ""}`,
    );
    await writeStatus(latest.dir, stopped);
    return NextResponse.json({ ok: true, status: stopped });
  } catch (exc) {
    return NextResponse.json(
      { ok: false, error: String(exc instanceof Error ? exc.message : exc) },
      { status: 500 },
    );
  }
}

async function executeChangeRun(
  runDir: string,
  initialStatus: ChangeRunStatus,
): Promise<void> {
  let status: ChangeRunStatus = {
    ...initialStatus,
    status: "starting",
    lastHeartbeat: new Date().toISOString(),
    elapsedSec: elapsedSec(initialStatus.startedAt),
  };
  await writeStatus(runDir, status);

  const steps: Array<{ name: string; args: string[] }> = [
    {
      name: "change new",
      args: [
        "--root",
        workspaceRoot(),
        "change",
        "new",
        "--from",
        status.changeRequestPath,
        "--project",
        status.agentProjectId,
        "--json",
      ],
    },
    {
      name: "change validate",
      args: [
        "--root",
        workspaceRoot(),
        "change",
        "validate",
        "latest",
        "--project",
        status.agentProjectId,
        "--json",
      ],
    },
    {
      name: "change run",
      args: [
        "--root",
        workspaceRoot(),
        "change",
        "run",
        "latest",
        "--project",
        status.agentProjectId,
        "--json",
      ],
    },
    {
      name: "change status",
      args: [
        "--root",
        workspaceRoot(),
        "change",
        "status",
        "latest",
        "--project",
        status.agentProjectId,
        "--json",
      ],
    },
    {
      name: "change validate final",
      args: [
        "--root",
        workspaceRoot(),
        "change",
        "validate",
        "latest",
        "--project",
        status.agentProjectId,
        "--json",
      ],
    },
  ];

  for (const step of steps) {
    const latestBeforeStep = await readStatus(runDir);
    if (latestBeforeStep?.status === "stopped") {
      return;
    }
    status = {
      ...(latestBeforeStep ?? status),
      status: step.name === "change run" ? "running" : "running",
    };
    status.currentStep = step.name;
    status.currentPid = null;
    status.lastHeartbeat = new Date().toISOString();
    status.elapsedSec = elapsedSec(status.startedAt);
    await writeStatus(runDir, status);
    const heartbeat = windowlessInterval(async () => {
      const latest = await readStatus(runDir);
      if (!latest || !isActiveState(latest.status)) return;
      const nextStatus: ChangeRunStatus = {
        ...latest,
        elapsedSec: elapsedSec(latest.startedAt),
        lastHeartbeat: new Date().toISOString(),
      };
      if (step.name === "change run") {
        const watchdogStatus = await maybeStopRepeatedTimeouts(nextStatus);
        if (watchdogStatus) {
          if (watchdogStatus.currentPid) {
            await stopProcessGroup(watchdogStatus.currentPid);
          }
          const cleanup = await cleanupRuntimeAfterStoppedRun(
            watchdogStatus.agentProjectPath,
            watchdogStatus.draftId,
          );
          await append(
            path.join(runDir, "stdout.log"),
            `\n[studio] ${watchdogStatus.error}\n${cleanup.log ? `[studio] ${cleanup.log}\n` : ""}`,
          );
          await writeStatus(runDir, { ...watchdogStatus, currentPid: null });
          return;
        }
      }
      await writeStatus(runDir, nextStatus);
    }, 5000);
    const result = await runCommand(
      runDir,
      agentStudioPythonBin(),
      agentStudioArgs(step.args),
      async (pid) => {
        const latest = await readStatus(runDir);
        if (latest?.status === "stopped") return;
        status = {
          ...(latest ?? status),
          currentPid: pid,
          lastHeartbeat: new Date().toISOString(),
          elapsedSec: elapsedSec(status.startedAt),
        };
        await writeStatus(runDir, status);
      },
    );
    clearInterval(heartbeat);
    const latest = await readStatus(runDir);
    if (latest?.status === "stopped" || latest?.status === "needs_human") {
      return;
    }
    status = latest ?? status;
    status.currentPid = null;
    status.lastHeartbeat = new Date().toISOString();
    status.elapsedSec = elapsedSec(status.startedAt);
    status.exitCode = result.exitCode;
    if (result.exitCode !== 0) {
      const changeRunPayload =
        step.name === "change run" ? parseLastJsonObject(result.stdout) : null;
      if (changeRunPayload?.result === "needs-human-review") {
        status.status = "needs_human";
        status.error = "Change run needs human review.";
        status.finishedAt = new Date().toISOString();
        status.currentStep = null;
        status.failureType = "needs-human-review";
        status.diagnosis = {
          severity: "warning",
          title: "Human review required",
          summary:
            "The change run completed its pipeline but Promotion/Apply Gate requested human review.",
          nextAction:
            "Inspect the review queue and candidate evidence before deciding whether to rerun or split the change.",
        };
        await writeStatus(runDir, status);
        return;
      }
      status.status = "failed";
      status.error = `${step.name} failed with exit code ${result.exitCode}`;
      status.finishedAt = new Date().toISOString();
      status.currentStep = null;
      status.failureType = "command_failure";
      status.diagnosis = {
        severity: "error",
        title: "Change run command failed",
        summary: `${step.name} exited with code ${result.exitCode}.`,
        nextAction: "Inspect the supervisor summary and runtime candidate table before rerunning.",
      };
      await writeStatus(runDir, status);
      return;
    }
  }

  status.status = "completed";
  status.error = null;
  status.currentStep = null;
  status.currentPid = null;
  status.finishedAt = new Date().toISOString();
  status.elapsedSec = elapsedSec(status.startedAt);
  status.lastHeartbeat = new Date().toISOString();
  status.diagnosis = {
    severity: "info",
    title: "Change run completed",
    summary: "Studio completed the fixed change run command chain.",
    nextAction: "Open Deliver or inspect the latest change evidence.",
  };
  await writeStatus(runDir, status);
}

function agentStudioPythonBin(): string {
  const fromEnv = process.env.AGENT_STUDIO_PYTHON;
  const candidates = [
    fromEnv,
    "/opt/homebrew/opt/python@3.13/bin/python3.13",
    "/opt/homebrew/bin/python3.13",
    "/usr/local/bin/python3.13",
    "python3",
  ].filter((candidate): candidate is string => Boolean(candidate));
  for (const candidate of candidates) {
    if (!path.isAbsolute(candidate) || existsSync(candidate)) return candidate;
  }
  return "python3";
}

function agentStudioArgs(args: string[]): string[] {
  return [path.join(workspaceRoot(), "agent-studio"), ...args];
}

async function maybeStopRepeatedTimeouts(
  status: ChangeRunStatus,
): Promise<ChangeRunStatus | null> {
  if (!status.agentProjectPath || status.watchdog?.triggeredAt) return null;
  const runtimeSummary = await readRuntimeRunSummary(status.agentProjectPath);
  const activeStall = activeCandidateStall(status, runtimeSummary);
  if (activeStall) return activeStall;
  const timeoutCandidateIds =
    runtimeSummary?.candidates
      .filter(
        (candidate) =>
          candidate.patchReason === "codex_cli_timeout" ||
          candidate.failureType === "codex_cli_timeout",
      )
      .map((candidate) => candidate.id) ?? [];
  if (timeoutCandidateIds.length < 2) return null;

  const now = new Date().toISOString();
  return {
    ...status,
    status: "needs_human",
    currentStep: null,
    finishedAt: now,
    exitCode: null,
    failureType: "codex_cli_timeout",
    elapsedSec: elapsedSec(status.startedAt),
    lastHeartbeat: now,
    error: `Studio stopped change run after repeated Codex patch-worker timeouts (${timeoutCandidateIds.join(", ")}).`,
    diagnosis: {
      severity: "warning",
      title: "Repeated candidate timeouts",
      summary:
        "Two candidate strategies exceeded their Codex patch-worker timeout. Studio stopped the run before spending more time on additional candidates.",
      nextAction:
        "Split the change into a smaller request or reduce provider scope, then rerun from Studio.",
    },
    watchdog: {
      policy: "stop_after_two_codex_candidate_timeouts",
      timeoutCandidateIds,
      triggeredAt: now,
    },
  };
}

function activeCandidateStall(
  status: ChangeRunStatus,
  runtimeSummary: RuntimeRunSummary,
): ChangeRunStatus | null {
  if (!runtimeSummary || status.currentStep !== "change run") return null;
  const maxSec = positiveIntFromEnv("STUDIO_CHANGE_CANDIDATE_STALL_SEC", 1200);
  const elapsed = elapsedSec(status.startedAt);
  if (elapsed < maxSec) return null;
  const activeCandidate =
    runtimeSummary.candidates.find((candidate) => candidate.id === status.currentCandidate) ??
    runtimeSummary.candidates[runtimeSummary.candidates.length - 1] ??
    null;
  if (!activeCandidate) return null;
  const hasEvidence =
    activeCandidate.patchStatus !== null ||
    activeCandidate.patchReason !== null ||
    activeCandidate.sourcePatchPresent !== null ||
    activeCandidate.evalPassed !== null ||
    activeCandidate.failureType !== null ||
    activeCandidate.lastEvent !== null;
  if (hasEvidence) return null;

  const now = new Date().toISOString();
  return {
    ...status,
    status: "needs_human",
    currentStep: null,
    finishedAt: now,
    exitCode: null,
    failureType: "candidate_stalled",
    elapsedSec: elapsed,
    lastHeartbeat: now,
    error: `Studio stopped change run after ${elapsed}s without candidate evidence (${activeCandidate.id}).`,
    diagnosis: {
      severity: "warning",
      title: "Candidate stalled",
      summary:
        "The active Codex candidate ran beyond the Studio watchdog window without producing patch/eval evidence.",
      nextAction:
        "Split the change smaller, reduce prompt scope, or rerun after adjusting the runtime timeout.",
    },
    watchdog: {
      policy: `stop_active_candidate_without_evidence_after_${maxSec}s`,
      timeoutCandidateIds: [activeCandidate.id],
      triggeredAt: now,
    },
  };
}

function positiveIntFromEnv(name: string, fallback: number): number {
  const raw = process.env[name];
  if (!raw) return fallback;
  const parsed = Number.parseInt(raw, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

async function cleanupRuntimeAfterStoppedRun(
  agentProjectPath: string | null | undefined,
  _draftId: string,
): Promise<{ log: string | null }> {
  if (!agentProjectPath) return { log: null };
  const runtimeRoot = path.isAbsolute(agentProjectPath)
    ? agentProjectPath
    : path.join(workspaceRoot(), agentProjectPath);
  const gitDir = path.join(runtimeRoot, ".git");
  try {
    await fs.stat(gitDir);
  } catch {
    return { log: "Runtime cleanup skipped: runtime project is not a git repo." };
  }

  const status = await execFileAsync("git", ["status", "--porcelain"], {
    cwd: runtimeRoot,
  }).catch(() => ({ stdout: "" }));
  const dirtyLines = String(status.stdout || "")
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter(Boolean);
  let cleanupLog: string | null = null;
  if (dirtyLines.length > 0) {
    const onlyTaskGraph = dirtyLines.every((line) => line.slice(3) === "task-graph.json");
    if (onlyTaskGraph) {
      const hasTrackedTaskGraph = dirtyLines.some((line) => !line.startsWith("?? "));
      const hasUntrackedTaskGraph = dirtyLines.some((line) => line.startsWith("?? "));
      if (hasTrackedTaskGraph) {
        await execFileAsync("git", ["checkout", "--", "task-graph.json"], {
          cwd: runtimeRoot,
        }).catch(() => null);
      }
      if (hasUntrackedTaskGraph) {
        await fs.rm(path.join(runtimeRoot, "task-graph.json"), { force: true }).catch(() => null);
      }
      cleanupLog = "Runtime cleanup: restored generated task-graph.json after stopped run.";
    } else {
      cleanupLog = `Runtime cleanup skipped: non-task-graph changes remain (${dirtyLines.join(", ")}).`;
    }
  }

  const sessionLog = await pauseStaleRuntimeSessions(runtimeRoot);
  return { log: [cleanupLog, sessionLog].filter(Boolean).join(" ") || null };
}

async function pauseStaleRuntimeSessions(runtimeRoot: string): Promise<string | null> {
  const sessionsRoot = path.join(runtimeRoot, ".agent", "autonomous", "sessions");
  let entries: string[];
  try {
    entries = await fs.readdir(sessionsRoot);
  } catch {
    return null;
  }
  const now = new Date().toISOString();
  let paused = 0;
  await Promise.all(
    entries.map(async (entry) => {
      const file = path.join(sessionsRoot, entry, "autonomous-session.json");
      const payload = await readJson(file);
      if (!payload || payload.status !== "running") return;
      payload.status = "paused";
      payload.pause_reason = "stopped_by_studio_run_manager";
      payload.halt_requested = true;
      payload.updated_at = now;
      await fs.writeFile(file, JSON.stringify(payload, null, 2), "utf-8");
      paused += 1;
    }),
  );
  return paused > 0
    ? `Runtime cleanup: paused ${paused} stale autonomous session(s).`
    : null;
}

function runCommand(
  runDir: string,
  cmd: string,
  args: string[],
  onStart: (pid: number) => Promise<void>,
): Promise<{ exitCode: number; stdout: string; stderr: string }> {
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    const child = spawn(cmd, args, {
      cwd: workspaceRoot(),
      env: process.env,
      detached: true,
      stdio: ["ignore", "pipe", "pipe"],
    });
    if (typeof child.pid === "number") {
      void onStart(child.pid).catch(() => {});
      void fs
        .writeFile(
          path.join(runDir, "pid.json"),
          JSON.stringify({ pid: child.pid }, null, 2),
          "utf-8",
        )
        .catch(() => {});
    }
    void append(
      path.join(runDir, "stdout.log"),
      `$ ${cmd} ${args.join(" ")}  (cwd=${workspaceRoot()})\n`,
    );
    child.stdout?.on("data", (chunk: Buffer) => {
      const text = chunk.toString();
      stdout = keepTail(stdout + text);
      void append(path.join(runDir, "stdout.log"), redact(text));
    });
    child.stderr?.on("data", (chunk: Buffer) => {
      const text = chunk.toString();
      stderr = keepTail(stderr + text);
      void append(path.join(runDir, "stderr.log"), redact(text));
    });
    child.on("error", (err) => {
      stderr = keepTail(`${stderr}\n[spawn-error] ${err.message}\n`);
      void append(path.join(runDir, "stderr.log"), `[spawn-error] ${err.message}\n`);
    });
    child.on("close", (code) => resolve({ exitCode: code ?? 1, stdout, stderr }));
  });
}

async function readLatestChangeRun(
  projectPath: string,
  draftId: string,
  agentProjectPath?: string | null,
) {
  const latest = await latestChangeRun(projectPath, draftId);
  if (!latest) {
    return {
      status: null,
      stdoutTail: "",
      stderrTail: "",
      runtimeSummary: null,
      supervisor: null,
    };
  }
  if (!latest.status) {
    return {
      status: null,
      stdoutTail: await readTail(path.join(latest.dir, "stdout.log"), 80),
      stderrTail: await readTail(path.join(latest.dir, "stderr.log"), 80),
      runtimeSummary: await readRuntimeRunSummary(agentProjectPath),
      supervisor: null,
    };
  }
  const status = await reconcileStatus(latest.dir, latest.status);
  const runtimeSummary = await readRuntimeRunSummary(agentProjectPath);
  const supervisor = buildSupervisorSnapshot(status, runtimeSummary);
  return {
    status: {
      ...status,
      currentCandidate: supervisor.currentCandidate,
      currentStrategy: supervisor.currentStrategy,
      failureType: supervisor.failureType,
      repairAttempt: supervisor.repairAttempt,
      currentPid: supervisor.currentPid,
      elapsedSec: supervisor.elapsedSec,
      lastHeartbeat: supervisor.lastHeartbeat,
      diagnosis: supervisor.diagnosis,
    },
    stdoutTail: await readTail(path.join(latest.dir, "stdout.log"), 80),
    stderrTail: await readTail(path.join(latest.dir, "stderr.log"), 80),
    runtimeSummary,
    supervisor,
  };
}

async function latestChangeRun(projectPath: string, draftId: string) {
  const runs = await findChangeRuns(projectPath);
  return (
    runs
      .filter((run) => run.status?.draftId === draftId)
      .sort((a, b) => b.mtime - a.mtime)[0] ?? null
  );
}

async function readActiveChangeRun(projectPath: string) {
  const runs = await findChangeRuns(projectPath);
  const newestFirst = [...runs].sort((a, b) => b.mtime - a.mtime);
  for (const run of newestFirst) {
    if (!run.status || !isActiveState(run.status.status)) continue;
    const reconciled = await reconcileStatus(run.dir, run.status);
    if (isActiveState(reconciled.status)) return reconciled;
  }
  return null;
}

async function findChangeRuns(projectPath: string): Promise<
  Array<{ dir: string; mtime: number; status: ChangeRunStatus | null }>
> {
  const runsDir = path.join(projectPath, "runs");
  let entries: string[];
  try {
    entries = await fs.readdir(runsDir);
  } catch {
    return [];
  }
  const runs = await Promise.all(
    entries.map(async (entry) => {
      const dir = path.join(runsDir, entry);
      const status = await readStatus(dir);
      if (status?.kind !== "change_run") return null;
      const stat = await fs.stat(dir).catch(() => null);
      return { dir, mtime: stat?.mtimeMs ?? 0, status };
    }),
  );
  return runs.filter(
    (run): run is { dir: string; mtime: number; status: ChangeRunStatus } =>
      run !== null,
  );
}

async function readStatus(runDir: string): Promise<ChangeRunStatus | null> {
  try {
    const parsed = JSON.parse(
      await fs.readFile(path.join(runDir, "status.json"), "utf-8"),
    ) as Partial<ChangeRunStatus>;
    if (parsed.kind !== "change_run" || typeof parsed.runId !== "string") {
      return null;
    }
    return parsed as ChangeRunStatus;
  } catch {
    return null;
  }
}

async function writeStatus(runDir: string, status: ChangeRunStatus) {
  status.updatedAt = new Date().toISOString();
  await fs.writeFile(
    path.join(runDir, "status.json"),
    JSON.stringify(status, null, 2),
    "utf-8",
  );
}

async function append(file: string, text: string) {
  await fs.appendFile(file, text, "utf-8").catch(() => {});
}

async function readTail(file: string, maxLines: number): Promise<string> {
  try {
    const text = await fs.readFile(file, "utf-8");
    const lines = text.split(/\r?\n/);
    return lines.slice(Math.max(0, lines.length - maxLines)).join("\n");
  } catch {
    return "";
  }
}

async function readRuntimeRunSummary(
  agentProjectPath?: string | null,
): Promise<RuntimeRunSummary> {
  if (!agentProjectPath) return null;
  const runtimeRoot = path.isAbsolute(agentProjectPath)
    ? agentProjectPath
    : path.join(workspaceRoot(), agentProjectPath);
  const runsDir = path.join(runtimeRoot, ".agent", "runs");
  let entries: string[];
  try {
    entries = await fs.readdir(runsDir);
  } catch {
    return null;
  }
  const runDirs = (
    await Promise.all(
      entries
        .filter((entry) => entry.startsWith("run_"))
        .map(async (entry) => {
          const dir = path.join(runsDir, entry);
          const stat = await fs.stat(dir).catch(() => null);
          return stat?.isDirectory() ? { id: entry, dir, mtime: stat.mtimeMs } : null;
        }),
    )
  )
    .filter((run): run is { id: string; dir: string; mtime: number } => run !== null)
    .sort((a, b) => b.mtime - a.mtime);
  const latest = runDirs[0];
  if (!latest) return null;
  const promotion = await readJson(path.join(latest.dir, "promotion-report.json"));
  const candidatesDir = path.join(latest.dir, "candidates");
  let candidateEntries: string[] = [];
  try {
    candidateEntries = await fs.readdir(candidatesDir);
  } catch {
    candidateEntries = [];
  }
  const candidates = await Promise.all(
    candidateEntries
      .filter((entry) => entry.startsWith("candidate-"))
      .sort()
      .map((entry) =>
        readRuntimeCandidateSummary(
          path.join(candidatesDir, entry),
          entry,
          runtimeRoot,
          latest.id,
        ),
      ),
  );
  const inferredCandidates =
    candidates.length > 0
      ? candidates
      : await readWorktreeCandidateSummaries(runtimeRoot, latest.id);
  return {
    runId: latest.id,
    path: path.relative(workspaceRoot(), latest.dir),
    decision: stringOrNull(promotion?.decision),
    selectedCandidate: stringOrNull(promotion?.selected_candidate),
    candidateCount: inferredCandidates.length,
    candidates: inferredCandidates,
  };
}

async function readWorktreeCandidateSummaries(
  runtimeRoot: string,
  runId: string,
): Promise<RuntimeCandidateSummary[]> {
  const worktreesDir = path.join(runtimeRoot, ".agent", "worktrees", runId);
  let entries: string[];
  try {
    entries = await fs.readdir(worktreesDir);
  } catch {
    return [];
  }
  return Promise.all(
    entries
      .filter((entry) => entry.startsWith("candidate-"))
      .sort()
      .map(async (entry) => {
        const changed = await changedSourceFiles(runtimeRoot, path.join(worktreesDir, entry));
        const sourcePatchPresent = changed.some((file) =>
          ["source", "test", "config"].includes(changeCategory(file)),
        );
        return {
          id: entry,
          strategy: null,
          patchStatus: changed.length > 0 ? "in_progress" : null,
          patchReason: null,
          sourcePatchPresent: sourcePatchPresent ? true : null,
          evalPassed: null,
          failureType: null,
          repairAttempts: null,
          repairStopReason: null,
          repairAction: null,
          lastEvent:
            changed.length > 0
              ? `worktree diff detected: ${changed.slice(0, 4).join(", ")}${
                  changed.length > 4 ? ` +${changed.length - 4}` : ""
                }`
              : null,
        };
      }),
  );
}

async function changedSourceFiles(baseRoot: string, changedRoot: string): Promise<string[]> {
  const [baseFiles, changedFiles] = await Promise.all([
    discoverComparableFiles(baseRoot),
    discoverComparableFiles(changedRoot),
  ]);
  const all = [...new Set([...baseFiles, ...changedFiles])].sort();
  const changed: string[] = [];
  for (const relative of all) {
    const baseFile = path.join(baseRoot, relative);
    const changedFile = path.join(changedRoot, relative);
    const [baseBytes, changedBytes] = await Promise.all([
      fs.readFile(baseFile).catch(() => null),
      fs.readFile(changedFile).catch(() => null),
    ]);
    if (!baseBytes || !changedBytes || !baseBytes.equals(changedBytes)) {
      changed.push(relative);
    }
  }
  return changed;
}

async function discoverComparableFiles(root: string): Promise<string[]> {
  const files: string[] = [];
  async function walk(dir: string) {
    let entries: Array<import("node:fs").Dirent>;
    try {
      entries = await fs.readdir(dir, { withFileTypes: true });
    } catch {
      return;
    }
    for (const entry of entries) {
      const absolute = path.join(dir, entry.name);
      const relative = path.relative(root, absolute).split(path.sep).join("/");
      if (skipComparablePath(relative, entry.isDirectory())) continue;
      if (entry.isDirectory()) {
        await walk(absolute);
      } else if (entry.isFile()) {
        files.push(relative);
      }
    }
  }
  await walk(root);
  return files;
}

function skipComparablePath(relative: string, isDirectory: boolean): boolean {
  const parts = relative.split("/");
  if (
    parts.includes(".git") ||
    parts.includes(".agent") ||
    parts.includes("node_modules") ||
    parts.includes(".next") ||
    parts.includes("dist") ||
    parts.includes("build") ||
    parts.includes("coverage")
  ) {
    return true;
  }
  if (!isDirectory && (relative.endsWith(".tsbuildinfo") || relative === ".DS_Store")) {
    return true;
  }
  return false;
}

function changeCategory(relative: string): string {
  const lower = relative.toLowerCase();
  if (lower.includes(".test.") || lower.includes(".spec.") || lower.startsWith("tests/")) {
    return "test";
  }
  if (
    lower === "package.json" ||
    lower.endsWith("config.js") ||
    lower.endsWith("config.mjs") ||
    lower.endsWith("config.ts") ||
    lower === "tsconfig.json"
  ) {
    return "config";
  }
  if (
    lower.startsWith("app/") ||
    lower.startsWith("components/") ||
    lower.startsWith("lib/") ||
    lower.startsWith("src/")
  ) {
    return "source";
  }
  return "other";
}

async function readRuntimeCandidateSummary(
  candidateDir: string,
  id: string,
  runtimeRoot?: string,
  runId?: string,
): Promise<RuntimeCandidateSummary> {
  const score = await readJson(path.join(candidateDir, "score.json"));
  const evalResults = await readJson(path.join(candidateDir, "eval-results.json"));
  const repairHistory = await readJson(path.join(candidateDir, "repair-history.json"));
  const lastEvent = await readLastJsonlMessage(path.join(candidateDir, "run-log.jsonl"));
  const finalFailure =
    repairHistory && typeof repairHistory.final_failure === "object"
      ? repairHistory.final_failure
      : null;
  const attempts = Array.isArray(repairHistory?.attempts)
    ? repairHistory.attempts.length
    : null;
  const hasPersistedEvidence =
    score ||
    evalResults ||
    repairHistory ||
    lastEvent;
  if (!hasPersistedEvidence && runtimeRoot && runId) {
    const inferred = (
      await readWorktreeCandidateSummaries(runtimeRoot, runId)
    ).find((candidate) => candidate.id === id);
    if (inferred) return inferred;
  }

  return {
    id,
    strategy: stringOrNull(score?.strategy),
    patchStatus: stringOrNull(score?.patch_status),
    patchReason: stringOrNull(score?.patch_reason),
    sourcePatchPresent:
      typeof score?.source_patch_present === "boolean"
        ? score.source_patch_present
        : null,
    evalPassed:
      typeof evalResults?.required_eval_passed === "boolean"
        ? evalResults.required_eval_passed
        : null,
    failureType: stringOrNull(evalResults?.failure_summary?.failure_type),
    repairAttempts: attempts,
    repairStopReason: stringOrNull(repairHistory?.stop_reason),
    repairAction: stringOrNull(
      evalResults?.failure_summary?.repair_action ??
        (finalFailure as Record<string, unknown> | null)?.repair_action,
    ),
    lastEvent,
  };
}

async function reconcileStatus(
  runDir: string,
  status: ChangeRunStatus,
): Promise<ChangeRunStatus> {
  if (!isActiveState(status.status)) {
    return {
      ...status,
      elapsedSec: status.elapsedSec ?? elapsedSec(status.startedAt),
      lastHeartbeat: status.lastHeartbeat ?? status.updatedAt ?? null,
    };
  }
  const now = new Date().toISOString();
  const secondsSinceUpdate = secondsBetween(status.updatedAt, now);
  if (status.currentPid && !isProcessAlive(status.currentPid) && secondsSinceUpdate > 10) {
    const failed: ChangeRunStatus = {
      ...status,
      status: "failed",
      currentStep: null,
      currentPid: null,
      finishedAt: now,
      exitCode: null,
      failureType: "stale_process",
      elapsedSec: elapsedSec(status.startedAt),
      lastHeartbeat: now,
      error: `Recorded process ${status.currentPid} is no longer running.`,
      diagnosis: {
        severity: "error",
        title: "Stale process",
        summary: "Studio found a running change status, but the recorded process no longer exists.",
        nextAction: "Rerun the change from Studio. The previous process cannot continue.",
      },
    };
    await writeStatus(runDir, failed);
    return failed;
  }
  if (!status.currentPid && secondsSinceUpdate > 120) {
    const failed: ChangeRunStatus = {
      ...status,
      status: "failed",
      currentStep: null,
      finishedAt: now,
      exitCode: null,
      failureType: "stale_status",
      elapsedSec: elapsedSec(status.startedAt),
      lastHeartbeat: now,
      error: "Change run status stopped updating and has no active child pid.",
      diagnosis: {
        severity: "error",
        title: "Stale run status",
        summary: "Studio found an active change status with no child process and no recent heartbeat.",
        nextAction: "Rerun the change from Studio after checking the latest runtime candidate evidence.",
      },
    };
    await writeStatus(runDir, failed);
    return failed;
  }
  return {
    ...status,
    elapsedSec: elapsedSec(status.startedAt),
    lastHeartbeat: status.lastHeartbeat ?? status.updatedAt ?? null,
  };
}

function buildSupervisorSnapshot(
  status: ChangeRunStatus,
  runtimeSummary: RuntimeRunSummary,
): SupervisorSnapshot {
  const candidates = runtimeSummary?.candidates ?? [];
  const latestCandidate =
    [...candidates].reverse().find((candidate) =>
      Boolean(
        candidate.patchReason ||
          candidate.patchStatus ||
          candidate.evalPassed !== null ||
          candidate.lastEvent,
      ),
    ) ?? candidates[candidates.length - 1] ?? null;
  const failureType =
    status.failureType ??
    latestCandidate?.failureType ??
    (latestCandidate?.patchReason === "codex_cli_timeout"
      ? "codex_cli_timeout"
      : null);
  const diagnosis =
    status.diagnosis ??
    diagnoseChangeRun(status, runtimeSummary, latestCandidate, failureType);

  return {
    state: status.status,
    isActive: isActiveState(status.status),
    isStale: failureType === "stale_process" || failureType === "stale_status",
    elapsedSec: elapsedSec(status.startedAt),
    lastHeartbeat: status.lastHeartbeat ?? status.updatedAt ?? null,
    currentStep: status.currentStep,
    currentPid: status.currentPid,
    currentCandidate: latestCandidate?.id ?? status.currentCandidate ?? null,
    currentStrategy: latestCandidate?.strategy ?? status.currentStrategy ?? null,
    failureType,
    repairAttempt: latestCandidate?.repairAttempts ?? status.repairAttempt ?? null,
    diagnosis,
  };
}

function diagnoseChangeRun(
  status: ChangeRunStatus,
  runtimeSummary: RuntimeRunSummary,
  candidate: RuntimeCandidateSummary | null,
  failureType: string | null,
): SupervisorDiagnosis | null {
  if (status.status === "stopped") {
    return {
      severity: "warning",
      title: "Run stopped",
      summary: "The change run was stopped before promotion completed.",
      nextAction: "Review the latest candidate evidence, then rerun from Studio when ready.",
    };
  }
  if (status.status === "completed") {
    return {
      severity: "info",
      title: "Run completed",
      summary: runtimeSummary?.decision
        ? `Promotion decision: ${runtimeSummary.decision}.`
        : "The change run command chain completed.",
      nextAction: "Open Deliver or inspect the latest delivery evidence.",
    };
  }
  if (failureType === "codex_cli_timeout" || candidate?.patchReason === "codex_cli_timeout") {
    return {
      severity: "warning",
      title: "Candidate timed out",
      summary: `${candidate?.id ?? "A candidate"} exceeded its Codex patch-worker timeout before producing a usable patch.`,
      nextAction: "Studio should skip this strategy on rerun or use a smaller change request.",
    };
  }
  if (failureType === "build_failure") {
    return {
      severity: "error",
      title: "Build failed after patch",
      summary: `${candidate?.id ?? "The candidate"} produced a patch, but required build validation failed.`,
      nextAction:
        candidate?.repairStopReason === "repair_loop_disabled"
          ? "Enable or budget a repair loop, then rerun the change from Studio."
          : candidate?.repairAction ?? "Let Studio run a repair attempt or split the change.",
    };
  }
  if (candidate && candidate.sourcePatchPresent === false) {
    return {
      severity: "warning",
      title: "No source patch",
      summary: `${candidate.id} did not produce a source/test/config diff.`,
      nextAction: "Rerun with a real patch worker and enough source context.",
    };
  }
  if (isActiveState(status.status)) {
    return {
      severity: "info",
      title: "Run active",
      summary: "Studio is running the fixed change pipeline in the background.",
      nextAction: "Wait for candidate evidence or use Stop Run if it exceeds the expected runtime.",
    };
  }
  if (status.status === "failed") {
    return {
      severity: "error",
      title: "Run failed",
      summary: status.error ?? "The change run failed before delivery.",
      nextAction: "Inspect runtime evidence, then rerun from Studio after fixing the blocking issue.",
    };
  }
  return null;
}

async function readJson(file: string): Promise<Record<string, any> | null> {
  try {
    const parsed = JSON.parse(await fs.readFile(file, "utf-8"));
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch {
    return null;
  }
}

async function readLastJsonlMessage(file: string): Promise<string | null> {
  try {
    const lines = (await fs.readFile(file, "utf-8"))
      .split(/\r?\n/)
      .filter((line) => line.trim().length > 0);
    const last = lines[lines.length - 1];
    if (!last) return null;
    const parsed = JSON.parse(last) as { event?: string; message?: string };
    const event = typeof parsed.event === "string" ? parsed.event : "";
    const message = typeof parsed.message === "string" ? parsed.message : "";
    return [event, message].filter(Boolean).join(": ") || null;
  } catch {
    return null;
  }
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function isActiveState(status: string): boolean {
  return ["queued", "starting", "running", "repairing", "stopping"].includes(status);
}

function elapsedSec(startedAt: string): number {
  const started = Date.parse(startedAt);
  if (!Number.isFinite(started)) return 0;
  return Math.max(0, Math.round((Date.now() - started) / 1000));
}

function secondsBetween(start: string | null | undefined, end: string): number {
  const a = Date.parse(start ?? "");
  const b = Date.parse(end);
  if (!Number.isFinite(a) || !Number.isFinite(b)) return 0;
  return Math.max(0, Math.round((b - a) / 1000));
}

function windowlessInterval(callback: () => void | Promise<void>, ms: number) {
  return setInterval(() => {
    void callback();
  }, ms);
}

function redact(text: string): string {
  return text
    .replace(/(DETECTOR_API_KEY=)[^\s"']+/gi, "$1[REDACTED]")
    .replace(/(key["']?\s*:\s*["'])[^"']+(["'])/gi, "$1[REDACTED]$2")
    .replace(/sk-[A-Za-z0-9_-]{12,}/g, "[REDACTED_KEY]");
}

function keepTail(text: string, maxChars = 80_000): string {
  return text.length > maxChars ? text.slice(text.length - maxChars) : text;
}

function parseLastJsonObject(text: string): Record<string, unknown> | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const starts: number[] = [];
  for (let index = 0; index < trimmed.length; index += 1) {
    if (trimmed[index] === "{") starts.push(index);
  }
  for (const start of starts.reverse()) {
    const candidate = trimmed.slice(start);
    try {
      const parsed = JSON.parse(candidate) as unknown;
      return parsed && typeof parsed === "object"
        ? (parsed as Record<string, unknown>)
        : null;
    } catch {
      // Keep scanning earlier JSON-looking blocks.
    }
  }
  return null;
}

async function stopProcessGroup(pid: number): Promise<void> {
  signalProcess(pid, "SIGTERM");
  await new Promise((resolve) => setTimeout(resolve, 1500));
  if (isProcessAlive(pid)) {
    signalProcess(pid, "SIGKILL");
  }
}

function signalProcess(pid: number, signal: NodeJS.Signals) {
  try {
    process.kill(-pid, signal);
    return;
  } catch {
    // Fall through to direct pid kill. Older runs may not have been detached.
  }
  try {
    process.kill(pid, signal);
  } catch {
    // Process already exited.
  }
}

function isProcessAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}
