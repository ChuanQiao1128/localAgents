/**
 * Studio Project —— 一个 project 就是一个合同。
 *
 * 文件结构（RC-5A.12.1）：
 *   .studio-console/projects/<projectId>/
 *     project.json                  元数据（name / template / createdAt / updatedAt）
 *     contract/
 *       raw-requirements.md
 *       discussion.md
 *       product-contract.md
 *       mvp-requirements.md
 *       open-questions.md
 *       lock.json
 *
 * 运行时状态（task-graph / autonomous-session / changes / review-items）
 * 在 `.agent-studio/projects/<agentProjectId>/`。RC-5A.12.5A 起，
 * project.json 会显式持久化 agentProjectId / agentProjectPath；老项目如果
 * 没有映射，则兼容地尝试用 Studio project id 读取。
 *
 * 与 RC-5A.4 的 .studio-console/contracts/<cr_xxx>/ 区别：那是「独立 contract」
 * 模型，已被本模块取代；旧目录会继续被旧版页面（/design 等）读取，新工作台
 * 只看本模块。
 */

import fs from "node:fs/promises";
import path from "node:path";
import { randomBytes } from "node:crypto";
import {
  assertReadable,
  relToWorkspace,
  studioProjectsRoot,
  workspaceRoot,
} from "./paths";
import { loadProjectDetail } from "./projects";
import type {
  ChangeSummary,
  ProjectDetail,
  ReviewItemSummary,
} from "./projects";

// ===========================================================================
// 类型
// ===========================================================================

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

export type StudioProjectMeta = {
  id: string;
  name: string;
  template: string | null;
  createdAt: string;
  updatedAt: string;
  /** Runtime project id used by agent-studio CLI, once prepared. */
  agentProjectId: string | null;
  /** Workspace-relative path to the runtime project, once prepared. */
  agentProjectPath: string | null;
};

export type StudioProjectSummary = {
  id: string;
  name: string;
  /** abs path to .studio-console/projects/<id>/ */
  path: string;
  /** workspace-relative path to .studio-console/projects/<id>/ */
  relPath: string;
  meta: StudioProjectMeta;
  contract: {
    locked: boolean;
    canLock: boolean;
    unresolvedQuestions: number;
    preconditionErrors: string[];
    /** workspace-relative path to contract/mvp-requirements.md（用于 CLI 命令）。 */
    mvpRequirementsRelPath: string;
  };
  /** Runtime project id used by agent-studio CLI, once prepared. */
  agentProjectId: string | null;
  /** Workspace-relative path to the runtime project, once prepared. */
  agentProjectPath: string | null;
  /** 运行时是否存在 .agent-studio/projects/<agentProjectId>/（即跑过 agent-studio）。 */
  hasRunState: boolean;
  taskCount: number;
  completedCount: number;
  changeCount: number;
  latestSessionId: string | null;
  latestSessionStatus: string | null;
  reviewQueueOpen: number;
  reviewQueueBlocking: number;
  /** 最近 delivered change 的 commit sha（前 12 字符）。 */
  latestDeliveredSha: string | null;
  /** Safe provider configuration summary. Secret values are never returned. */
  providerReadiness: ProviderReadiness | null;
};

export type StudioProjectDetail = StudioProjectSummary & {
  /** 6 个合同文件的内容。 */
  files: Record<ContractFileName, string>;
  lockState: LockState;
  /** 关联的 .agent-studio/projects/<id>/ 详情；没跑过则为 null。 */
  runDetail: ProjectDetail | null;
};

export type ProviderReadinessState = "connected" | "missing" | "not_applicable";

export type ProviderReadinessItem = {
  status: ProviderReadinessState;
  label: string;
  detail: string;
};

export type ProviderReadiness = {
  rewriteProvider: ProviderReadinessItem;
  detectorProvider: ProviderReadinessItem;
  currentMode: string;
  source: "runtime-env-local" | "not-linked";
  secretsExposed: false;
};

// ===========================================================================
// 默认模板
// ===========================================================================

const DEFAULT_TEMPLATES: Record<ContractFileName, string> = {
  "raw-requirements.md":
    "# 原始需求\n\n" +
    "在这里写下你最初的设想 —— 灵感、要点、约束、参考、用户痛点。\n" +
    "随便写，没有格式要求；后面会一起把它整理成产品合同。\n",
  "discussion.md":
    "# 讨论笔记\n\n" +
    "记录每一轮讨论的决策、取舍、想过又否决的方案。\n" +
    "便签性质，不直接进合同；用来回看「为什么这样设计」。\n",
  "product-contract.md":
    "# 产品合同\n\n" +
    "## 产品定位\n\n" +
    "（一句话：产品是什么、面向谁、解决什么问题）\n\n" +
    "## 目标用户\n\n" +
    "- （用户画像 1）\n\n" +
    "## MVP 范围\n\n" +
    "- （第一版必须有的能力 1）\n" +
    "- （第一版必须有的能力 2）\n\n" +
    "## 非目标 / 非范围\n\n" +
    "- （v1 明确不做的事）\n\n" +
    "## 用户主流程\n\n" +
    "1. （一步）\n\n" +
    "## 验收标准\n\n" +
    "- （可观察 / 可测试的标准 1）\n" +
    "- `npm run build` 通过\n" +
    "- `npm run typecheck` 通过\n",
  "mvp-requirements.md":
    "# MVP 需求\n\n" +
    "把上面 MVP 范围拆成 task。每个 H2 `## task-NNN — 标题` 都会变成\n" +
    "一个 autonomous task。Scope: 行用纯路径（不要反引号）。\n\n" +
    "## task-001 — （任务标题）\n\n" +
    "（一段意图描述）\n\n" +
    "Scope:\n- app/**\n\n" +
    "Acceptance:\n- npm run build passes\n- npm run typecheck passes\n- （其他验收点）\n\n" +
    "Risk: low\n\n" +
    "---\n\n" +
    "## Non-goals (across all tasks)\n\n" +
    "- 不修改 package.json / lockfile\n" +
    "- 不引入新依赖\n" +
    "- 不调用任何外部 API\n",
  "open-questions.md":
    "# 待解决问题\n\n" +
    "锁定合同要求所有 - [ ] 都已勾选。已勾选的 - [x] 会保留作审计。\n\n" +
    "- [ ] （未决问题示例 —— 锁定前必须勾掉）\n" +
    "- [x] （已决示例）\n",
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

// ===========================================================================
// CRUD
// ===========================================================================

const ID_RE = /^[a-z0-9][a-z0-9_-]{2,40}$/i;

export function newStudioProjectId(): string {
  return `prj_${randomBytes(5).toString("hex")}`;
}

/** name → 安全 id（slug）。如果已存在则追加 hex 后缀。 */
function slugify(name: string): string {
  const base = name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 30);
  if (base.length < 3) return newStudioProjectId();
  return base;
}

export async function listStudioProjects(): Promise<StudioProjectSummary[]> {
  const root = studioProjectsRoot();
  let entries: string[];
  try {
    entries = await fs.readdir(root);
  } catch {
    return [];
  }
  const summaries = await Promise.all(
    entries.map((id) => loadStudioProjectSummary(id).catch(() => null)),
  );
  return summaries
    .filter((x): x is StudioProjectSummary => x !== null)
    .sort((a, b) => b.meta.updatedAt.localeCompare(a.meta.updatedAt));
}

export async function createStudioProject(opts: {
  name: string;
  template?: string | null;
  /** 可选：直接指定 id（必须满足 ID_RE）；否则从 name slugify。 */
  id?: string;
}): Promise<{ id: string }> {
  const name = opts.name.trim();
  if (name.length < 1) throw new Error("项目名不能为空");
  if (name.length > 80) throw new Error("项目名过长（最多 80 字符）");

  await fs.mkdir(studioProjectsRoot(), { recursive: true });

  let id = opts.id?.trim() ?? slugify(name);
  if (!ID_RE.test(id)) id = newStudioProjectId();

  const dir = path.join(studioProjectsRoot(), id);
  // 如果目录已存在 → 追加 hex 后缀直到不冲突。
  let attempt = id;
  let counter = 0;
  while (await dirExists(path.join(studioProjectsRoot(), attempt))) {
    counter += 1;
    attempt = `${id}-${randomBytes(2).toString("hex")}`;
    if (counter > 5) throw new Error("无法生成唯一项目 id，请换一个名字");
  }
  id = attempt;
  const finalDir = path.join(studioProjectsRoot(), id);
  assertReadable(finalDir);

  await fs.mkdir(finalDir);
  await fs.mkdir(path.join(finalDir, "contract"));

  const now = new Date().toISOString();
  const meta: StudioProjectMeta = {
    id,
    name,
    template: opts.template ?? null,
    createdAt: now,
    updatedAt: now,
    agentProjectId: null,
    agentProjectPath: null,
  };
  await fs.writeFile(
    path.join(finalDir, "project.json"),
    JSON.stringify(meta, null, 2),
    "utf-8",
  );

  for (const file of CONTRACT_FILE_NAMES) {
    await fs.writeFile(
      path.join(finalDir, "contract", file),
      DEFAULT_TEMPLATES[file],
      "utf-8",
    );
  }

  return { id };
}

export async function loadStudioProjectSummary(
  id: string,
): Promise<StudioProjectSummary | null> {
  if (!isValidId(id)) return null;
  const dir = path.join(studioProjectsRoot(), id);
  if (!(await dirExists(dir))) return null;
  assertReadable(dir);

  const meta = await readMeta(dir, id);
  const files = await readAllContractFiles(dir);
  const lockState = parseLockState(files["lock.json"]);
  const errors = lockPreconditionErrors(files);
  const unresolved = countUnresolvedQuestions(files["open-questions.md"]);

  // 跑过 agent-studio 的话顺带读取运行时摘要。agentProjectId 是 CLI/DB
  // project id（project_xxx），而 .agent-studio/projects/<dir>/ 使用 slug
  // 目录名；文件读取需要从 agentProjectPath 取 basename。老项目没有
  // mapping 时保留同 id 兼容。
  const runtimeProjectDir = meta.agentProjectPath
    ? path.basename(meta.agentProjectPath)
    : id;
  const runDetail = await loadProjectDetail(runtimeProjectDir).catch(() => null);
  const agentProjectPath = meta.agentProjectPath ?? (runDetail ? runDetail.relPath : null);
  const providerReadiness = await loadProviderReadiness(id, agentProjectPath);

  return {
    id,
    name: meta.name,
    path: dir,
    relPath: relToWorkspace(dir),
    meta,
    contract: {
      locked: lockState.locked,
      canLock: errors.length === 0 && !lockState.locked,
      unresolvedQuestions: unresolved,
      preconditionErrors: errors,
      mvpRequirementsRelPath: relToWorkspace(
        path.join(dir, "contract", "mvp-requirements.md"),
      ),
    },
    agentProjectId: meta.agentProjectId,
    agentProjectPath,
    hasRunState: runDetail !== null,
    taskCount: runDetail?.taskCount ?? 0,
    completedCount: runDetail?.completedCount ?? 0,
    changeCount: runDetail?.changeCount ?? 0,
    latestSessionId: runDetail?.latestSessionId ?? null,
    latestSessionStatus: runDetail?.latestSessionStatus ?? null,
    reviewQueueOpen: runDetail?.reviewQueue.open ?? 0,
    reviewQueueBlocking: runDetail?.reviewQueue.blocking ?? 0,
    latestDeliveredSha: deriveLatestDeliveredSha(runDetail?.changes ?? []),
    providerReadiness,
  };
}

export async function loadStudioProjectDetail(
  id: string,
): Promise<StudioProjectDetail | null> {
  const summary = await loadStudioProjectSummary(id);
  if (!summary) return null;
  const files = await readAllContractFiles(summary.path);
  const lockState = parseLockState(files["lock.json"]);
  const runtimeProjectDir = summary.agentProjectPath
    ? path.basename(summary.agentProjectPath)
    : id;
  const runDetail = await loadProjectDetail(runtimeProjectDir).catch(() => null);
  return {
    ...summary,
    files,
    lockState,
    runDetail,
  };
}

export async function updateContractFile(
  id: string,
  file: ContractFileName,
  content: string,
): Promise<{ ok: true; lockState?: LockState } | { ok: false; errors: string[] }> {
  if (!CONTRACT_FILE_NAMES.includes(file)) {
    return { ok: false, errors: [`file not allowed: ${file}`] };
  }
  if (!isValidId(id)) return { ok: false, errors: [`invalid project id: ${id}`] };
  const dir = path.join(studioProjectsRoot(), id);
  if (!(await dirExists(dir))) {
    return { ok: false, errors: [`project not found: ${id}`] };
  }
  assertReadable(dir);
  const contractDir = path.join(dir, "contract");

  // lock.json 走单独路径：服务端再次校验前置条件，自动盖时间戳。
  if (file === "lock.json") {
    let requested: LockState;
    try {
      requested = parseLockState(content);
    } catch (exc) {
      return { ok: false, errors: [`invalid lock.json: ${String(exc)}`] };
    }
    if (requested.locked) {
      const files = await readAllContractFiles(dir);
      const errors = lockPreconditionErrors(files);
      if (errors.length > 0) {
        return { ok: false, errors };
      }
      requested.lockedAt = new Date().toISOString();
      requested.lockedBy = requested.lockedBy ?? "operator";
      requested.unlockedAt = null;
    } else {
      const prev = parseLockState(
        await fs
          .readFile(path.join(contractDir, "lock.json"), "utf-8")
          .catch(() => "{}"),
      );
      requested.lockedAt = prev.lockedAt;
      requested.lockedBy = prev.lockedBy;
      requested.unlockedAt = new Date().toISOString();
    }
    await fs.writeFile(
      path.join(contractDir, "lock.json"),
      JSON.stringify(requested, null, 2),
      "utf-8",
    );
    await touchMeta(dir);
    return { ok: true, lockState: requested };
  }

  // 普通文件：直写。
  await fs.writeFile(path.join(contractDir, file), content, "utf-8");
  await touchMeta(dir);
  return { ok: true };
}

// ===========================================================================
// 内部
// ===========================================================================

function isValidId(id: string): boolean {
  return ID_RE.test(id);
}

async function dirExists(p: string): Promise<boolean> {
  try {
    const stat = await fs.stat(p);
    return stat.isDirectory();
  } catch {
    return false;
  }
}

async function readMeta(
  dir: string,
  fallbackId: string,
): Promise<StudioProjectMeta> {
  try {
    const text = await fs.readFile(path.join(dir, "project.json"), "utf-8");
    const raw = JSON.parse(text) as Partial<StudioProjectMeta>;
    return {
      id: typeof raw.id === "string" ? raw.id : fallbackId,
      name: typeof raw.name === "string" ? raw.name : fallbackId,
      template: typeof raw.template === "string" ? raw.template : null,
      createdAt:
        typeof raw.createdAt === "string"
          ? raw.createdAt
          : new Date().toISOString(),
      updatedAt:
        typeof raw.updatedAt === "string"
          ? raw.updatedAt
          : new Date().toISOString(),
      agentProjectId:
        typeof raw.agentProjectId === "string" && raw.agentProjectId.length > 0
          ? raw.agentProjectId
          : null,
      agentProjectPath:
        typeof raw.agentProjectPath === "string" && raw.agentProjectPath.length > 0
          ? raw.agentProjectPath
          : null,
    };
  } catch {
    const now = new Date().toISOString();
    return {
      id: fallbackId,
      name: fallbackId,
      template: null,
      createdAt: now,
      updatedAt: now,
      agentProjectId: null,
      agentProjectPath: null,
    };
  }
}

async function touchMeta(dir: string): Promise<void> {
  const meta = await readMeta(dir, path.basename(dir));
  meta.updatedAt = new Date().toISOString();
  await fs.writeFile(
    path.join(dir, "project.json"),
    JSON.stringify(meta, null, 2),
    "utf-8",
  );
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
      out[file] = await fs.readFile(
        path.join(dir, "contract", file),
        "utf-8",
      );
    } catch {
      out[file] = file === "lock.json" ? DEFAULT_TEMPLATES["lock.json"] : "";
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
  return text.split("\n").filter((line) => /^\s*-\s+\[\s\]/.test(line)).length;
}

async function loadProviderReadiness(
  studioProjectId: string,
  agentProjectPath: string | null,
): Promise<ProviderReadiness | null> {
  if (studioProjectId !== "ai-writing-naturalizer") return null;
  if (!agentProjectPath) {
    return {
      rewriteProvider: {
        status: "missing",
        label: "not linked",
        detail: "Runtime project is not linked yet.",
      },
      detectorProvider: {
        status: "missing",
        label: "not linked",
        detail: "Runtime project is not linked yet.",
      },
      currentMode: "local deterministic / heuristic",
      source: "not-linked",
      secretsExposed: false,
    };
  }

  const runtimePath = path.isAbsolute(agentProjectPath)
    ? path.resolve(agentProjectPath)
    : path.resolve(workspaceRoot(), agentProjectPath);
  const keys = await readRuntimeEnvKeyPresence(runtimePath);
  const rewriteReady = Boolean(keys.CODEX_BIN && keys.CODEX_MODEL);
  const detectorReady = Boolean(keys.DETECTOR_API_URL && keys.DETECTOR_API_KEY);

  return {
    rewriteProvider: rewriteReady
      ? {
          status: "connected",
          label: "Codex CLI configured",
          detail: "CODEX_BIN and CODEX_MODEL are present server-side.",
        }
      : {
          status: "missing",
          label: "fallback only",
          detail: "CODEX_BIN or CODEX_MODEL is missing; rewrite falls back locally.",
        },
    detectorProvider: detectorReady
      ? {
          status: "connected",
          label: "Detector endpoint configured",
          detail: "DETECTOR_API_URL and DETECTOR_API_KEY are present server-side.",
        }
      : {
          status: "missing",
          label: "heuristic only",
          detail:
            "DETECTOR_API_URL or DETECTOR_API_KEY is missing; detector falls back locally.",
        },
    currentMode:
      rewriteReady && detectorReady
        ? "Codex CLI rewrite / configured detector"
        : rewriteReady
          ? "Codex CLI rewrite / heuristic detector"
          : detectorReady
            ? "local rewrite / configured detector"
            : "local deterministic / heuristic",
    source: "runtime-env-local",
    secretsExposed: false,
  };
}

async function readRuntimeEnvKeyPresence(
  runtimePath: string,
): Promise<Record<string, boolean>> {
  const envPath = path.join(runtimePath, ".env.local");
  let raw: string;
  try {
    raw = await fs.readFile(envPath, "utf-8");
  } catch {
    return {};
  }

  const out: Record<string, boolean> = {};
  for (const line of raw.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith("#")) continue;
    const match = /^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/.exec(trimmed);
    if (!match) continue;
    out[match[1]] = (match[2] ?? "").trim().length > 0;
  }
  return out;
}

function lockPreconditionErrors(
  files: Record<ContractFileName, string>,
): string[] {
  const errors: string[] = [];
  const productContract = (files["product-contract.md"] ?? "").trim();
  if (productContract.length < 50) {
    errors.push(
      `product-contract.md 至少需要 50 字符（当前 ${productContract.length}）。`,
    );
  }
  const mvp = (files["mvp-requirements.md"] ?? "").trim();
  if (mvp.length < 50) {
    errors.push(`mvp-requirements.md 至少需要 50 字符（当前 ${mvp.length}）。`);
  }
  const unresolved = countUnresolvedQuestions(files["open-questions.md"] ?? "");
  if (unresolved > 0) {
    errors.push(
      `open-questions.md 还有 ${unresolved} 个未解决项；请先勾选或删除。`,
    );
  }
  if (!/^##\s+task/im.test(files["mvp-requirements.md"] ?? "")) {
    errors.push(
      "mvp-requirements.md 没有 `## task` 二级标题 —— autonomous parser 至少需要一个。",
    );
  }
  return errors;
}

function deriveLatestDeliveredSha(changes: ChangeSummary[]): string | null {
  return latestDeliveredChange(changes)?.sha?.slice(0, 12) ?? null;
}

function latestDeliveredChange(changes: ChangeSummary[]): ChangeSummary | null {
  const delivered = changes.filter((c) => c.state === "delivered" && c.sha);
  if (delivered.length === 0) return null;
  return delivered.sort((a, b) => changeTimestamp(b) - changeTimestamp(a))[0] ?? null;
}

function changeTimestamp(change: ChangeSummary): number {
  const parsed = Date.parse(change.appliedAt ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
}

// ===========================================================================
// 项目级 Change Draft（RC-5A.12.2）
// ===========================================================================
//
// 文件结构：.studio-console/projects/<projectId>/changes/<changeId>/
//   change-request.md
//   meta.json             { title, createdAt, updatedAt }
//
// 与 .studio-console/changes/<id>/（旧 change-request 独立 draft）不同 ——
// 那是 RC-5A.9 的临时模型，已废弃。新模型让每个 change draft 明确归属
// 于一个项目。
//
// 运行时态（applied / delivered）依然在
// .agent-studio/projects/<projectId>/.agent/changes/<changeId>/，由
// loadProjectDetail 读取并通过 runDetail.changes 暴露。

const CHANGE_TEMPLATE = `# (变更标题)

## Goal

(一段话：这次改动要做什么、为什么。)

## Scope paths

- app/**

## Non-goals

- 不修改 package.json、package-lock.json 或任何 lockfile。
- 不引入新依赖。

## Acceptance criteria

- (可测试的验收点 1)
- (可测试的验收点 2)
- \`npm run build\` 通过
- \`npm run typecheck\` 通过
`;

export type ChangeDraftMeta = {
  title: string | null;
  createdAt: string;
  updatedAt: string;
};

export type ChangeDraftSummary = {
  id: string;
  projectId: string;
  /** workspace-relative path to the change-request.md（CLI 命令拼接用）。 */
  changeRequestPath: string;
  meta: ChangeDraftMeta;
  size: number;
};

export type ChangeDraft = ChangeDraftSummary & {
  content: string;
};

export function newChangeId(): string {
  return `cr_${randomBytes(5).toString("hex")}`;
}

function isValidChangeId(id: string): boolean {
  return /^cr_[a-f0-9]{6,32}$/i.test(id);
}

function changesDir(projectId: string): string {
  return path.join(studioProjectsRoot(), projectId, "changes");
}

function changeDir(projectId: string, changeId: string): string {
  return path.join(changesDir(projectId), changeId);
}

export async function listChangeDrafts(
  projectId: string,
): Promise<ChangeDraftSummary[]> {
  if (!isValidId(projectId)) return [];
  const dir = changesDir(projectId);
  let entries: string[];
  try {
    entries = await fs.readdir(dir);
  } catch {
    return [];
  }
  const summaries = await Promise.all(
    entries.map((id) => loadChangeDraftSummary(projectId, id).catch(() => null)),
  );
  return summaries
    .filter((x): x is ChangeDraftSummary => x !== null)
    .sort((a, b) => b.meta.updatedAt.localeCompare(a.meta.updatedAt));
}

export async function createChangeDraft(
  projectId: string,
  opts?: { title?: string | null },
): Promise<{ id: string }> {
  if (!isValidId(projectId)) throw new Error(`invalid project id: ${projectId}`);
  if (!(await dirExists(path.join(studioProjectsRoot(), projectId)))) {
    throw new Error(`project not found: ${projectId}`);
  }
  await fs.mkdir(changesDir(projectId), { recursive: true });
  const id = newChangeId();
  const dir = changeDir(projectId, id);
  assertReadable(dir);
  await fs.mkdir(dir);
  const now = new Date().toISOString();
  const meta: ChangeDraftMeta = {
    title: opts?.title ?? null,
    createdAt: now,
    updatedAt: now,
  };
  await fs.writeFile(
    path.join(dir, "change-request.md"),
    CHANGE_TEMPLATE,
    "utf-8",
  );
  await fs.writeFile(
    path.join(dir, "meta.json"),
    JSON.stringify(meta, null, 2),
    "utf-8",
  );
  // 顺便 bump project meta.json
  await touchMeta(path.join(studioProjectsRoot(), projectId));
  return { id };
}

export async function loadChangeDraftSummary(
  projectId: string,
  id: string,
): Promise<ChangeDraftSummary | null> {
  if (!isValidId(projectId) || !isValidChangeId(id)) return null;
  const dir = changeDir(projectId, id);
  if (!(await dirExists(dir))) return null;
  assertReadable(dir);
  const meta = await readChangeMeta(dir);
  const requestPath = path.join(dir, "change-request.md");
  let size = 0;
  try {
    size = (await fs.stat(requestPath)).size;
  } catch {
    return null;
  }
  return {
    id,
    projectId,
    changeRequestPath: relToWorkspace(requestPath),
    meta,
    size,
  };
}

export async function loadChangeDraft(
  projectId: string,
  id: string,
): Promise<ChangeDraft | null> {
  const summary = await loadChangeDraftSummary(projectId, id);
  if (!summary) return null;
  const content = await fs
    .readFile(
      path.join(studioProjectsRoot(), projectId, "changes", id, "change-request.md"),
      "utf-8",
    )
    .catch(() => "");
  return { ...summary, content };
}

export async function updateChangeDraft(
  projectId: string,
  id: string,
  field: "change-request.md" | "meta.json",
  content: string,
): Promise<{ ok: true } | { ok: false; error: string }> {
  if (!isValidId(projectId)) {
    return { ok: false, error: `invalid project id: ${projectId}` };
  }
  if (!isValidChangeId(id)) {
    return { ok: false, error: `invalid change id: ${id}` };
  }
  const dir = changeDir(projectId, id);
  if (!(await dirExists(dir))) {
    return { ok: false, error: `change draft not found: ${id}` };
  }
  assertReadable(dir);
  if (field === "change-request.md") {
    await fs.writeFile(
      path.join(dir, "change-request.md"),
      content,
      "utf-8",
    );
    await bumpChangeUpdatedAt(dir);
    return { ok: true };
  }
  if (field === "meta.json") {
    let parsed: Partial<ChangeDraftMeta>;
    try {
      const raw = JSON.parse(content) as Record<string, unknown>;
      parsed = {
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
    const existing = await readChangeMeta(dir);
    const merged: ChangeDraftMeta = {
      ...existing,
      ...parsed,
      updatedAt: new Date().toISOString(),
      createdAt: existing.createdAt ?? new Date().toISOString(),
    };
    await fs.writeFile(
      path.join(dir, "meta.json"),
      JSON.stringify(merged, null, 2),
      "utf-8",
    );
    return { ok: true };
  }
  return { ok: false, error: `field not allowed: ${field as string}` };
}

async function readChangeMeta(dir: string): Promise<ChangeDraftMeta> {
  try {
    const raw = JSON.parse(
      await fs.readFile(path.join(dir, "meta.json"), "utf-8"),
    ) as Partial<ChangeDraftMeta>;
    return {
      title: typeof raw.title === "string" ? raw.title : null,
      createdAt:
        typeof raw.createdAt === "string"
          ? raw.createdAt
          : new Date().toISOString(),
      updatedAt:
        typeof raw.updatedAt === "string"
          ? raw.updatedAt
          : new Date().toISOString(),
    };
  } catch {
    const now = new Date().toISOString();
    return { title: null, createdAt: now, updatedAt: now };
  }
}

async function bumpChangeUpdatedAt(dir: string): Promise<void> {
  const meta = await readChangeMeta(dir);
  meta.updatedAt = new Date().toISOString();
  await fs.writeFile(
    path.join(dir, "meta.json"),
    JSON.stringify(meta, null, 2),
    "utf-8",
  );
}

// 重导出常用类型给客户端镜像复用。
export type { ChangeSummary, ProjectDetail, ReviewItemSummary };
