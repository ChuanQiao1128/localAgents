"use client";

import Link from "next/link";
import { use, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import ArtifactViewerModal from "@/components/ArtifactViewerModal";
import CommandBlock from "@/components/CommandBlock";
import MarkdownEditor from "@/components/MarkdownEditor";
import ModeToggle, { getActiveMode } from "@/components/ModeToggle";
import OpenQuestionsPanel from "@/components/OpenQuestionsPanel";
import ProjectStatusPill from "@/components/ProjectStatusPill";
import ProjectTabs, { getActiveTab } from "@/components/ProjectTabs";
import ProjectFlowDag from "@/components/ProjectFlowDag";
import StatusBadge from "@/components/StatusBadge";
import { runChangeRequestQuality } from "@/lib/changeRequestQuality";
import { runPreflight } from "@/lib/preflight";
import { deriveProjectDeliveryFlow } from "@/lib/projectFlow";
import { deriveStudioProjectStatus } from "@/lib/projectStatus";
import {
  PRODUCT_CONTRACT_TEMPLATE,
  MVP_REQUIREMENTS_TEMPLATE,
} from "@/lib/templates";
import type {
  AutonomousSessionLike,
  ChangeDraftClient,
  ChangeDraftSummaryClient,
  ChangeSummaryClient,
  ContractFileName,
  CreateChangeDraftResponse,
  ListChangeDraftsResponse,
  PreviewRunResponse,
  PreviewRunState,
  PreviewRunStartStopResponse,
  PreviewRunStatusClient,
  ProductReviewResponse,
  ProductReviewResultClient,
  RuntimeDevelopmentKickoffResponse,
  RuntimeDevelopmentRunResponse,
  RuntimeDevelopmentStatusClient,
  RuntimeDevelopmentStopResponse,
  RuntimePrepareKickoffResponse,
  RuntimePrepareRunResponse,
  RuntimePrepareStatusClient,
  ReviewItemSummary,
  StudioProjectDetailClient,
  TaskLike,
  UpdateChangeDraftResponse,
  UpdateStudioContractResponse,
} from "@/lib/types";

/**
 * Studio Project Workspace —— RC-5A.12.1：一个项目就是一个合同。
 *
 * URL：/projects/<projectId>?tab=discuss|develop|deliver[&mode=new|change]
 *
 * 数据源：/api/studio-projects/[id]
 *   - 元数据 + 6 个合同文件 + 锁定状态  来自 .studio-console/projects/<id>/
 *   - 运行时（task graph / sessions / changes / reviews）  来自 .agent-studio/projects/<id>/
 */

const POLL_INTERVAL_MS = 3000;
const RUN_POLL_INTERVAL_MS = 2000;

function formatTimestamp(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function formatDuration(seconds: number) {
  if (!Number.isFinite(seconds) || seconds <= 0) return "0s";
  const mins = Math.floor(seconds / 60);
  const secs = Math.floor(seconds % 60);
  if (mins <= 0) return `${secs}s`;
  const hours = Math.floor(mins / 60);
  const remMins = mins % 60;
  if (hours <= 0) return `${mins}m ${secs}s`;
  return `${hours}h ${remMins}m`;
}

function isActiveChangeRunState(s: string | null | undefined) {
  return ["queued", "starting", "running", "repairing", "stopping"].includes(
    s ?? "",
  );
}

function latestDelivered(changes: ChangeSummaryClient[]): ChangeSummaryClient | null {
  const delivered = changes.filter((change) => change.state === "delivered");
  if (delivered.length === 0) return null;
  return delivered.sort((a, b) => changeAppliedAtMs(b) - changeAppliedAtMs(a))[0] ?? null;
}

function changeAppliedAtMs(change: ChangeSummaryClient) {
  const parsed = Date.parse(change.appliedAt ?? "");
  return Number.isFinite(parsed) ? parsed : 0;
}

export default function ProjectWorkspacePage({
  params,
}: {
  params: Promise<{ projectId: string }>;
}) {
  const { projectId } = use(params);
  const search = useSearchParams();
  const tab = getActiveTab(search?.get("tab"));
  const mode = getActiveMode(search?.get("mode"));

  const [detail, setDetail] = useState<StudioProjectDetailClient | null>(null);
  const [pageError, setPageError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [openArtifact, setOpenArtifact] = useState<string | null>(null);

  async function loadDetail(opts?: { silent?: boolean }) {
    if (!opts?.silent) setLoading(true);
    setPageError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(projectId)}`,
        { cache: "no-store" },
      );
      const body = (await res.json()) as
        | StudioProjectDetailClient
        | { error: string };
      if (!res.ok || "error" in body) {
        if (res.status === 404) {
          setDetail(null);
          return;
        }
        throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
      }
      setDetail(body as StudioProjectDetailClient);
    } catch (exc) {
      setPageError(String(exc));
    } finally {
      if (!opts?.silent) setLoading(false);
    }
  }

  useEffect(() => {
    void loadDetail();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  const isRunning = detail?.latestSessionStatus === "running";
  useEffect(() => {
    if (!isRunning) return;
    const t = setInterval(
      () => void loadDetail({ silent: true }),
      POLL_INTERVAL_MS,
    );
    return () => clearInterval(t);
  }, [isRunning, projectId]);

  // 合同文件保存
  async function saveContractFile(file: ContractFileName, content: string) {
    if (!detail) throw new Error("project not loaded");
    const res = await fetch(
      `/api/studio-projects/${encodeURIComponent(detail.id)}/contract`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ file, content }),
      },
    );
    const body = (await res.json()) as UpdateStudioContractResponse;
    if (!res.ok || ("ok" in body && body.ok === false)) {
      const err =
        "errors" in body && body.errors && body.errors.length > 0
          ? body.errors.join("; ")
          : "error" in body
            ? body.error
            : `HTTP ${res.status}`;
      throw new Error(err);
    }
    await loadDetail();
  }

  async function toggleLock(target: boolean) {
    if (!detail) return;
    setBusy(true);
    setPageError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/contract`,
        {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            file: "lock.json",
            content: JSON.stringify({
              locked: target,
              lockedAt: null,
              lockedBy: "operator",
              unlockedAt: null,
            }),
          }),
        },
      );
      const body = (await res.json()) as UpdateStudioContractResponse;
      if (!res.ok || ("ok" in body && body.ok === false)) {
        const err =
          "errors" in body && body.errors && body.errors.length > 0
            ? body.errors.join("; ")
            : "error" in body
              ? body.error
              : `HTTP ${res.status}`;
        throw new Error(err);
      }
      await loadDetail();
    } catch (exc) {
      setPageError(String(exc));
    } finally {
      setBusy(false);
    }
  }

  const status = useMemo(
    () => (detail ? deriveStudioProjectStatus(detail) : null),
    [detail],
  );

  return (
    <>
      <header className="workspace-header">
        <div className="workspace-header-top">
          <div className="workspace-header-id">
            <Link href="/projects" className="workspace-header-back">
              ← 项目列表
            </Link>
            <h1 className="workspace-title">
              {detail?.name ?? projectId}
            </h1>
            {detail && (
              <code className="cell-code workspace-header-path">
                {detail.id}
              </code>
            )}
          </div>
          {status && <ProjectStatusPill status={status} />}
        </div>
        <ProjectTabs active={tab} basePath={`/projects/${projectId}`} />
      </header>

      {pageError && (
        <div className="design-error">
          <strong>错误：</strong> {pageError}
        </div>
      )}

      {loading && !detail && <div className="design-empty">加载项目中…</div>}

      {!loading && !detail && (
        <div className="design-empty">
          <p>
            <strong>项目 <code>{projectId}</code> 不存在。</strong>
          </p>
          <p style={{ marginTop: "var(--sp-2)" }}>
            它可能已被删除，或者 id 写错了。回{" "}
            <Link
              href="/projects"
              style={{ color: "var(--color-info)", textDecoration: "underline" }}
            >
              项目列表
            </Link>{" "}
            重新挑选 / 新建。
          </p>
        </div>
      )}

      {detail && (() => {
        const flow = deriveProjectDeliveryFlow({ detail });
        const hasActionable = flow.nodes.some(
          (n) =>
            n.status === "current" ||
            n.status === "blocked" ||
            n.status === "available",
        );
        return (
          <>
          {hasActionable && (
            <ProjectFlowDag
              projectId={detail.id}
              nodes={flow.nodes}
              edges={flow.edges}
            />
          )}
          <div className="project-workspace-grid">
            <main className="project-workspace-main">
              {tab === "discuss" && (
              <DiscussTab
                detail={detail}
                mode={mode}
                basePath={`/projects/${projectId}`}
                busy={busy}
                setBusy={setBusy}
                setPageError={setPageError}
                onSaveFile={saveContractFile}
                onToggleLock={toggleLock}
              />
            )}

            {tab === "develop" && (
              <DevelopTab
                detail={detail}
                loading={loading}
                isRunning={isRunning}
                onRefresh={() => void loadDetail()}
                onOpenArtifact={(p) => setOpenArtifact(p)}
              />
            )}

            {status && tab === "deliver" && (
              <DeliverTab
                detail={detail}
                status={status}
                onOpenArtifact={(p) => setOpenArtifact(p)}
              />
            )}
          </main>

            <aside className="project-workspace-inspector">
              <ProjectInspector detail={detail} />
            </aside>
          </div>
          </>
        );
      })()}

      <ArtifactViewerModal
        open={openArtifact !== null}
        path={openArtifact}
        onClose={() => setOpenArtifact(null)}
      />
    </>
  );
}

function ProjectInspector({ detail }: { detail: StudioProjectDetailClient }) {
  const [previewStatus, setPreviewStatus] =
    useState<PreviewRunStatusClient | null>(null);
  const isRuntimeLinked = Boolean(detail.agentProjectId && detail.agentProjectPath);
  const runDetail = detail.runDetail;
  const latestDeliveredChange = latestDelivered(runDetail?.changes ?? []);
  const greenfieldCompleted = runDetail?.latestSessionStatus === "completed";

  useEffect(() => {
    let cancelled = false;
    async function loadPreviewStatus() {
      try {
        const res = await fetch(
          `/api/studio-projects/${encodeURIComponent(detail.id)}/preview`,
          { cache: "no-store" },
        );
        const body = (await res.json()) as PreviewRunResponse | { error: string };
        if (!cancelled && res.ok && !("error" in body)) {
          setPreviewStatus(body.status);
        }
      } catch {
        if (!cancelled) setPreviewStatus(null);
      }
    }
    void loadPreviewStatus();
    return () => {
      cancelled = true;
    };
  }, [detail.id, detail.agentProjectId, detail.agentProjectPath]);

  return (
    <div className="inspector-card">
      <div className="inspector-head">
        <h2 className="section-title">Project Inspector</h2>
        <span className="badge">local</span>
      </div>
      <dl className="inspector-list">
        <div>
          <dt>Studio project</dt>
          <dd>
            <code className="cell-code">{detail.id}</code>
          </dd>
        </div>
        <div>
          <dt>Contract</dt>
          <dd>
            <StatusBadge variant={detail.lockState.locked ? "completed" : "pending"}>
              {detail.lockState.locked ? "locked" : "draft"}
            </StatusBadge>
          </dd>
        </div>
        <div>
          <dt>Runtime</dt>
          <dd>
            <StatusBadge variant={isRuntimeLinked ? "completed" : "needs-review"}>
              {isRuntimeLinked ? "linked" : "missing"}
            </StatusBadge>
          </dd>
        </div>
        {detail.agentProjectId && (
          <div>
            <dt>agentProjectId</dt>
            <dd>
              <code className="cell-code">{detail.agentProjectId}</code>
            </dd>
          </div>
        )}
        {previewStatus?.status === "running" && previewStatus.url && (
          <div>
            <dt>Preview</dt>
            <dd>
              <a href={previewStatus.url} target="_blank" rel="noreferrer">
                {previewStatus.url}
              </a>
            </dd>
          </div>
        )}
        {latestDeliveredChange && (
          <div>
            <dt>Latest delivered</dt>
            <dd>
              <code className="cell-code">{latestDeliveredChange.changeId}</code>
              {latestDeliveredChange.sha && (
                <code className="cell-code">{latestDeliveredChange.sha.slice(0, 8)}</code>
              )}
            </dd>
          </div>
        )}
        <div>
          <dt>Review queue</dt>
          <dd>
            <CountChip
              variant={detail.reviewQueueOpen ? "needs-review" : "completed"}
              label="open"
              value={detail.reviewQueueOpen}
            />
            <CountChip
              variant={detail.reviewQueueBlocking ? "failed" : "completed"}
              label="blocking"
              value={detail.reviewQueueBlocking}
            />
          </dd>
        </div>
      </dl>
      {(detail.reviewQueueOpen > 0 || detail.reviewQueueBlocking > 0) &&
        latestDeliveredChange && (
          <p className="inspector-note">
            Open review items may include historical failed attempts. Latest delivery is{" "}
            <code>{latestDeliveredChange.changeId}</code>.
          </p>
        )}

      {!isRuntimeLinked && (
        <p className="inspector-note">Runtime project is not linked yet.</p>
      )}
      {isRuntimeLinked && greenfieldCompleted && (
        <p className="inspector-note">
          Greenfield development already completed. Use Change Request for further work.
        </p>
      )}

      {detail.id === "ai-writing-naturalizer" && (
        <div className="provider-readiness-card">
          <h3>Provider readiness</h3>
          {detail.providerReadiness ? (
            <dl className="inspector-list">
              <div>
                <dt>Rewrite provider</dt>
                <dd>
                  <StatusBadge
                    variant={
                      detail.providerReadiness.rewriteProvider.status === "connected"
                        ? "completed"
                        : "needs-review"
                    }
                  >
                    {detail.providerReadiness.rewriteProvider.label}
                  </StatusBadge>
                  <p className="inspector-note">
                    {detail.providerReadiness.rewriteProvider.detail}
                  </p>
                </dd>
              </div>
              <div>
                <dt>Detector provider</dt>
                <dd>
                  <StatusBadge
                    variant={
                      detail.providerReadiness.detectorProvider.status === "connected"
                        ? "completed"
                        : "needs-review"
                    }
                  >
                    {detail.providerReadiness.detectorProvider.label}
                  </StatusBadge>
                  <p className="inspector-note">
                    {detail.providerReadiness.detectorProvider.detail}
                  </p>
                </dd>
              </div>
              <div>
                <dt>Current mode</dt>
                <dd>{detail.providerReadiness.currentMode}</dd>
              </div>
            </dl>
          ) : (
            <p className="inspector-note">
              Provider readiness is not tracked for this project.
            </p>
          )}
          <p className="inspector-note">
            Secret values are checked only by presence and are never rendered.
          </p>
        </div>
      )}

      <div className="provider-readiness-card">
        <h3>Agent coverage</h3>
        <dl className="inspector-list">
          <div>
            <dt>Product</dt>
            <dd>contract / scope / acceptance</dd>
          </div>
          <div>
            <dt>UI Design</dt>
            <dd>visual direction / UX flow / component spec</dd>
          </div>
          <div>
            <dt>Developer</dt>
            <dd>candidate patch / repair loop</dd>
          </div>
          <div>
            <dt>QA + Review</dt>
            <dd>build gates / promotion / handoff</dd>
          </div>
        </dl>
        <p className="inspector-note">
          Missing specialist agents should be added as explicit contract tasks before
          greenfield development starts.
        </p>
      </div>
    </div>
  );
}

// ===========================================================================
// Discuss & Lock
// ===========================================================================

const CONTRACT_FILE_TABS: ReadonlyArray<{
  key: ContractFileName;
  labelZh: string;
  helper: string;
}> = [
  {
    key: "raw-requirements.md",
    labelZh: "原始需求",
    helper: "随便写 —— 灵感、要点、约束。Studio 不直接消费它。",
  },
  {
    key: "discussion.md",
    labelZh: "讨论笔记",
    helper: "决策、取舍、想过又否决的方案。便签性质，不进合同。",
  },
  {
    key: "product-contract.md",
    labelZh: "产品合同",
    helper:
      "≥ 50 字符可锁定。点击右侧「创建模板」一键填入脚手架。",
  },
  {
    key: "mvp-requirements.md",
    labelZh: "MVP 需求",
    helper:
      "≥ 50 字符 + 至少一个 ## task 标题。每个任务都会变成一个 autonomous task。",
  },
];

function DiscussTab(props: {
  detail: StudioProjectDetailClient;
  mode: "new" | "change";
  basePath: string;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setPageError: (msg: string | null) => void;
  onSaveFile: (file: ContractFileName, content: string) => Promise<void>;
  onToggleLock: (target: boolean) => Promise<void>;
}) {
  const { detail, mode, basePath } = props;

  return (
    <>
      <ModeToggle active={mode} basePath={basePath} />
      <p className="tab-tagline">
        {mode === "new"
          ? "决定要做什么 —— 在右侧四个文档里收敛需求，确认无误后点 Lock。"
          : "MVP 已经做完了？给项目继续提改动。"}
      </p>

      {mode === "new" ? <DiscussNewMode {...props} /> : <DiscussChangeMode detail={detail} />}
    </>
  );
}

function DiscussNewMode(props: {
  detail: StudioProjectDetailClient;
  busy: boolean;
  setBusy: (b: boolean) => void;
  setPageError: (msg: string | null) => void;
  onSaveFile: (file: ContractFileName, content: string) => Promise<void>;
  onToggleLock: (target: boolean) => Promise<void>;
}) {
  const { detail, busy, setPageError, onSaveFile, onToggleLock } = props;
  const [activeFile, setActiveFile] = useState<ContractFileName>(
    "raw-requirements.md",
  );
  const isLocked = detail.lockState.locked;

  return (
    <>
      <DiscussLockControl
        detail={detail}
        busy={busy}
        onToggleLock={onToggleLock}
      />

      <div className="tabs" role="tablist">
        {CONTRACT_FILE_TABS.map((t) => (
          <button
            key={t.key}
            type="button"
            role="tab"
            aria-selected={activeFile === t.key}
            className={"tab-button" + (activeFile === t.key ? " active" : "")}
            onClick={() => setActiveFile(t.key)}
          >
            {t.labelZh}
          </button>
        ))}
      </div>

      {CONTRACT_FILE_TABS.map((t) =>
        activeFile === t.key ? (
          <MarkdownEditor
            key={`${detail.id}::${t.key}`}
            initialValue={detail.files[t.key] ?? ""}
            onSave={(text) => onSaveFile(t.key, text)}
            fileLabel={t.key}
            helperText={t.helper}
            readOnly={isLocked}
            extraActions={renderTemplateButton(
              t.key,
              isLocked,
              busy,
              async (file, template) => {
                const existing = (detail.files[file] || "").trim();
                if (
                  existing.length > 0 &&
                  !window.confirm(
                    `${file} 已有内容，确认用模板覆盖？该操作不可撤销。`,
                  )
                ) {
                  return;
                }
                try {
                  await onSaveFile(file, template);
                } catch (exc) {
                  setPageError(String(exc));
                }
              },
            )}
          />
        ) : null,
      )}

      <section className="oq-section">
        <div className="oq-section-head">
          <h2 className="section-title">未决问题</h2>
          <p className="section-subtitle">
            合同锁定要求所有 <code>- [ ]</code> 都已勾选。已勾选的{" "}
            <code>- [x]</code> 会保留作审计。
          </p>
        </div>
        <OpenQuestionsPanel
          key={detail.id}
          initialValue={detail.files["open-questions.md"] ?? ""}
          onSave={(md) => onSaveFile("open-questions.md", md)}
          readOnly={isLocked}
        />
      </section>

      {!isLocked &&
        (detail.files["mvp-requirements.md"] ?? "").trim().length > 0 && (
          <DiscussPreflightSummary
            mvpRequirements={detail.files["mvp-requirements.md"]}
            productContract={detail.files["product-contract.md"]}
            openQuestions={detail.files["open-questions.md"]}
          />
        )}
    </>
  );
}

function DiscussLockControl({
  detail,
  busy,
  onToggleLock,
}: {
  detail: StudioProjectDetailClient;
  busy: boolean;
  onToggleLock: (target: boolean) => Promise<void>;
}) {
  const isLocked = detail.lockState.locked;
  const canLock = detail.contract.canLock;
  const errors = detail.contract.preconditionErrors;

  return (
    <section className="card discuss-lock-control">
      <div className="section-head">
        <h2 className="section-title">锁定状态</h2>
        <div style={{ display: "flex", gap: "var(--sp-2)" }}>
          {isLocked ? (
            <>
              <StatusBadge variant="locked">已锁定</StatusBadge>
              <button
                type="button"
                className="btn"
                data-variant="ghost"
                onClick={() => void onToggleLock(false)}
                disabled={busy}
              >
                解锁
              </button>
            </>
          ) : canLock ? (
            <>
              <StatusBadge variant="completed">可以锁定</StatusBadge>
              <button
                type="button"
                className="btn"
                data-variant="primary"
                onClick={() => void onToggleLock(true)}
                disabled={busy}
              >
                锁定 MVP 合同
              </button>
            </>
          ) : (
            <>
              <StatusBadge variant="warning">草稿</StatusBadge>
              <button
                type="button"
                className="btn"
                data-variant="primary"
                disabled
                title="先解决下方所有阻塞项再锁定"
              >
                锁定 MVP 合同
              </button>
            </>
          )}
        </div>
      </div>
      {!isLocked && errors.length > 0 && (
        <ul className="precondition-list">
          {errors.map((err, i) => (
            <li key={i} className="precondition-item">
              <span className="precondition-mark">×</span>
              <span>{err}</span>
            </li>
          ))}
        </ul>
      )}
      {!isLocked && errors.length === 0 && (
        <p className="precondition-ok">✓ 所有锁定前置条件都满足。</p>
      )}
      {isLocked && (
        <p className="cell-muted" style={{ fontSize: 12, marginTop: "var(--sp-2)" }}>
          锁定时间 <code>{detail.lockState.lockedAt ?? "(未知)"}</code> ·
          锁定人 <code>{detail.lockState.lockedBy ?? "(未知)"}</code>。
          编辑器为只读；点击「解锁」重新进入草稿状态。
        </p>
      )}
    </section>
  );
}

function renderTemplateButton(
  file: ContractFileName,
  isLocked: boolean,
  busy: boolean,
  onApply: (file: ContractFileName, tpl: string) => void,
): React.ReactNode {
  if (file === "product-contract.md") {
    return (
      <button
        type="button"
        className="btn"
        data-variant="ghost"
        onClick={() => onApply("product-contract.md", PRODUCT_CONTRACT_TEMPLATE)}
        disabled={isLocked || busy}
        title="用确定性的产品合同模板覆盖此文件"
      >
        创建模板
      </button>
    );
  }
  if (file === "mvp-requirements.md") {
    return (
      <button
        type="button"
        className="btn"
        data-variant="ghost"
        onClick={() => onApply("mvp-requirements.md", MVP_REQUIREMENTS_TEMPLATE)}
        disabled={isLocked || busy}
        title="用确定性的 MVP 需求模板覆盖此文件"
      >
        创建模板
      </button>
    );
  }
  return null;
}

function DiscussPreflightSummary({
  mvpRequirements,
  productContract,
  openQuestions,
}: {
  mvpRequirements: string;
  productContract: string;
  openQuestions: string;
}) {
  const result = useMemo(
    () =>
      runPreflight({
        mvpRequirements: mvpRequirements ?? "",
        productContract: productContract ?? "",
        openQuestions: openQuestions ?? "",
      }),
    [mvpRequirements, productContract, openQuestions],
  );
  return (
    <section className="card discuss-preflight-summary">
      <div className="section-head">
        <h2 className="section-title">开发前检查（预览）</h2>
        <div style={{ display: "flex", gap: "var(--sp-2)" }}>
          {result.passed ? (
            <StatusBadge variant="completed">就绪</StatusBadge>
          ) : (
            <StatusBadge variant="failed">{result.errorCount} 项错误</StatusBadge>
          )}
          {result.warningCount > 0 && (
            <StatusBadge variant="warning">{result.warningCount} 警告</StatusBadge>
          )}
          <Link
            href="/plan"
            className="btn"
            data-variant="ghost"
            style={{ padding: "4px 10px", fontSize: 12 }}
          >
            完整检查 →
          </Link>
        </div>
      </div>
      <p className="section-subtitle">
        {result.passedCount}/{result.totalCount} 项通过。仅提示 ——
        真正阻塞由 Promotion / Apply Gate 在运行时执行。
      </p>
      {!result.passed && (
        <ul className="precondition-list">
          {result.checks
            .filter((c) => !c.passed && c.severity === "error")
            .slice(0, 5)
            .map((c) => (
              <li key={c.id} className="precondition-item">
                <span className="precondition-mark">×</span>
                <span>
                  <strong>{c.name}</strong> · {c.message}
                </span>
              </li>
            ))}
        </ul>
      )}
    </section>
  );
}

function DiscussChangeMode({ detail }: { detail: StudioProjectDetailClient }) {
  const [drafts, setDrafts] = useState<ChangeDraftSummaryClient[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [draft, setDraft] = useState<ChangeDraftClient | null>(null);
  const [loading, setLoading] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function loadDrafts(autoSelect?: string) {
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/changes`,
        { cache: "no-store" },
      );
      const body = (await res.json()) as
        | ListChangeDraftsResponse
        | { error: string };
      if (!res.ok || "error" in body) {
        throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
      }
      setDrafts(body.drafts);
      if (autoSelect) {
        setSelectedId(autoSelect);
      } else {
        const currentStillExists =
          selectedId && body.drafts.some((d) => d.id === selectedId);
        const activeDraftId = currentStillExists
          ? null
          : await findActiveChangeRunDraftId(detail.id, body.drafts);
        setSelectedId(
          currentStillExists
            ? selectedId
            : activeDraftId ??
                body.drafts.find((d) => d.size > 0)?.id ??
                body.drafts[0]?.id ??
                null,
        );
      }
    } catch (exc) {
      setErr(String(exc));
    }
  }

  async function loadDraft(id: string) {
    setLoading(true);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/changes/${encodeURIComponent(id)}`,
        { cache: "no-store" },
      );
      const body = (await res.json()) as
        | ChangeDraftClient
        | { error: string };
      if (!res.ok || "error" in body) {
        throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
      }
      setDraft(body as ChangeDraftClient);
    } catch (exc) {
      setErr(String(exc));
      setDraft(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void loadDrafts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.id]);

  useEffect(() => {
    if (selectedId) void loadDraft(selectedId);
    else setDraft(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  async function handleCreate() {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/changes`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        },
      );
      const body = (await res.json()) as
        | CreateChangeDraftResponse
        | { error: string };
      if (!res.ok || "error" in body) {
        throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
      }
      await loadDrafts(body.id);
    } catch (exc) {
      setErr(String(exc));
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveContent(content: string) {
    if (!draft) throw new Error("no draft selected");
    const res = await fetch(
      `/api/studio-projects/${encodeURIComponent(detail.id)}/changes/${encodeURIComponent(draft.id)}`,
      {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ field: "change-request.md", content }),
      },
    );
    const body = (await res.json()) as UpdateChangeDraftResponse;
    if (!res.ok || ("ok" in body && body.ok === false)) {
      const errStr = "error" in body ? body.error : `HTTP ${res.status}`;
      throw new Error(errStr);
    }
    await Promise.all([loadDraft(draft.id), loadDrafts()]);
  }

  const runtimeChanges = detail.runDetail?.changes ?? [];

  return (
    <>
      <section className="card change-context-card">
        <div className="section-head">
          <h2 className="section-title">Change Request scope</h2>
          <StatusBadge variant={detail.agentProjectId ? "completed" : "needs-review"}>
            {detail.agentProjectId ? "runtime linked" : "runtime missing"}
          </StatusBadge>
        </div>
        <p className="section-subtitle">
          Change Request belongs to this project. Use this after the initial MVP is delivered.
          每个 change 必须声明可执行范围，缺 scope 时不会显示为可运行。
        </p>
        <div className="scope-example-grid">
          <div>
            <strong>Inline scope</strong>
            <code>Scope: app/**, components/**, lib/**</code>
          </div>
          <div>
            <strong>Scope paths section</strong>
            <code>{"## Scope paths\n- app/**"}</code>
          </div>
        </div>
      </section>

      <section className="card">
        <div className="section-head">
          <h2 className="section-title">变更草稿</h2>
          <button
            type="button"
            className="btn"
            data-variant="primary"
            onClick={() => void handleCreate()}
            disabled={busy}
          >
            + 新建变更
          </button>
        </div>
        {err && (
          <div className="design-error" style={{ marginTop: "var(--sp-2)" }}>
            <strong>错误：</strong> {err}
          </div>
        )}
        {drafts.length === 0 ? (
          <p className="cell-muted">
            还没有变更草稿。点击「+ 新建变更」开始写第一份{" "}
            <code>change-request.md</code> ——
            它会落到{" "}
            <code>.studio-console/projects/{detail.id}/changes/&lt;cr_id&gt;/</code>。
          </p>
        ) : (
          <ul className="contract-list">
            {drafts.map((d) => (
              <li key={d.id}>
                <button
                  type="button"
                  className={
                    "contract-link" + (d.id === selectedId ? " active" : "")
                  }
                  onClick={() => setSelectedId(d.id)}
                >
                  <span className="contract-link-id">
                    {d.meta.title || d.id}
                  </span>
                  <span className="contract-link-meta">
                    <code className="cell-code" style={{ fontSize: 10 }}>
                      {d.id}
                    </code>
                    <span className="cell-muted" style={{ fontSize: 11 }}>
                      {d.size} 字节
                    </span>
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {loading && <div className="design-empty">加载草稿中…</div>}

      {draft && (
        <ChangeDraftEditor
          projectId={detail.id}
          runtimeProjectId={detail.agentProjectId}
          draft={draft}
          onSave={handleSaveContent}
        />
      )}

      <section className="card">
        <div className="section-head">
          <h2 className="section-title">已运行的变更（来自 runtime）</h2>
          <span className="badge">{runtimeChanges.length}</span>
        </div>
        {runtimeChanges.length === 0 ? (
          <p className="cell-muted">
            还没跑过 change。把上方草稿的命令复制到终端执行后，
            完成的 change 会出现在这里和「交付结果」tab。
          </p>
        ) : (
          <table className="data-table">
            <thead>
              <tr>
                <th>change</th>
                <th>状态</th>
                <th>commit</th>
              </tr>
            </thead>
            <tbody>
              {runtimeChanges.slice(0, 8).map((c) => (
                <tr key={c.changeId}>
                  <td>
                    <code className="cell-code">{c.changeId}</code>
                    {c.goal && (
                      <div className="cell-muted" style={{ fontSize: 12, marginTop: 2 }}>
                        {truncate(c.goal, 60)}
                      </div>
                    )}
                  </td>
                  <td>
                    <StatusBadge variant={changeStateVariant(c.state)}>
                      {translateChangeState(c.state)}
                    </StatusBadge>
                  </td>
                  <td>
                    {c.sha ? (
                      <code className="cell-code">{c.sha.slice(0, 10)}</code>
                    ) : (
                      <span className="cell-muted">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </>
  );
}

async function findActiveChangeRunDraftId(
  projectId: string,
  drafts: ChangeDraftSummaryClient[],
): Promise<string | null> {
  for (const draft of drafts.slice(0, 12)) {
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(projectId)}/changes/${encodeURIComponent(draft.id)}/run`,
        { cache: "no-store" },
      );
      if (!res.ok) continue;
      const body = (await res.json()) as {
        status?: { status?: string } | null;
      };
      if (isActiveChangeRunState(body.status?.status)) {
        return draft.id;
      }
    } catch {
      // Draft selection should remain usable even if run metadata is stale.
    }
  }
  return null;
}

type ChangeRunDiagnosis = {
  severity: "info" | "warning" | "error";
  title: string;
  summary: string;
  nextAction: string;
};

type ChangeRuntimeSummary = {
  runId: string;
  path: string;
  decision: string | null;
  selectedCandidate: string | null;
  candidateCount: number;
  candidates: Array<{
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
  }>;
};

type ChangeRunSupervisor = {
  state: string;
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
  diagnosis: ChangeRunDiagnosis | null;
};

function ChangeDraftEditor({
  projectId,
  runtimeProjectId,
  draft,
  onSave,
}: {
  projectId: string;
  runtimeProjectId: string | null;
  draft: ChangeDraftClient;
  onSave: (content: string) => Promise<void>;
}) {
  const quality = useMemo(
    () => runChangeRequestQuality(draft.content),
    [draft.content],
  );
  const hasScopeError = quality.checks.some(
    (check) => check.id === "has-scope-paths" && !check.passed,
  );
  const [changeRun, setChangeRun] = useState<{
    status: {
      runId: string;
      status: string;
      currentStep: string | null;
      error: string | null;
      startedAt?: string;
      updatedAt?: string;
      finishedAt?: string | null;
      currentPid?: number | null;
      currentCandidate?: string | null;
      currentStrategy?: string | null;
      failureType?: string | null;
      repairAttempt?: number | null;
      elapsedSec?: number;
      lastHeartbeat?: string | null;
      diagnosis?: ChangeRunDiagnosis | null;
      watchdog?: {
        policy: string;
        timeoutCandidateIds: string[];
        triggeredAt: string | null;
      } | null;
    } | null;
    stdoutTail: string;
    stderrTail: string;
    runtimeSummary: ChangeRuntimeSummary | null;
    supervisor: ChangeRunSupervisor | null;
  } | null>(null);
  const [runBusy, setRunBusy] = useState(false);
  const [stopBusy, setStopBusy] = useState(false);
  const [runErr, setRunErr] = useState<string | null>(null);
  const [lastRunPollAt, setLastRunPollAt] = useState<string | null>(null);

  const cliProjectId = runtimeProjectId ?? projectId;
  const cmdNew = `agent-studio change new --from ${draft.changeRequestPath} --project ${cliProjectId}`;
  const cmdRun = `agent-studio change run latest --project ${cliProjectId}`;
  const cmdStatus = `agent-studio change status latest --project ${cliProjectId}`;
  const cmdValidate = `agent-studio change validate latest --project ${cliProjectId} --json`;
  const activeChangeRun = isActiveChangeRunState(changeRun?.status?.status);

  async function loadChangeRun() {
    const res = await fetch(
      `/api/studio-projects/${encodeURIComponent(projectId)}/changes/${encodeURIComponent(draft.id)}/run`,
      { cache: "no-store" },
    );
    const body = (await res.json()) as
      | {
          status: {
            runId: string;
            status: string;
            currentStep: string | null;
            error: string | null;
            currentPid?: number | null;
            currentCandidate?: string | null;
            currentStrategy?: string | null;
            failureType?: string | null;
            repairAttempt?: number | null;
            elapsedSec?: number;
            lastHeartbeat?: string | null;
            diagnosis?: ChangeRunDiagnosis | null;
            watchdog?: {
              policy: string;
              timeoutCandidateIds: string[];
              triggeredAt: string | null;
            } | null;
          } | null;
          stdoutTail: string;
          stderrTail: string;
          runtimeSummary: ChangeRuntimeSummary | null;
          supervisor: ChangeRunSupervisor | null;
        }
      | { error: string };
    if (!res.ok || "error" in body) {
      throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
    }
    setChangeRun(body);
    setLastRunPollAt(new Date().toISOString());
  }

  async function handleRunChange() {
    setRunBusy(true);
    setRunErr(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(projectId)}/changes/${encodeURIComponent(draft.id)}/run`,
        { method: "POST" },
      );
      const body = (await res.json()) as
        | {
            ok: true;
            status: {
              runId: string;
              status: string;
              currentStep: string | null;
              error: string | null;
            };
          }
        | { ok: false; error: string };
      if (!res.ok || !body.ok) {
        throw new Error(!body.ok ? body.error : `HTTP ${res.status}`);
      }
      await loadChangeRun();
    } catch (exc) {
      setRunErr(String(exc instanceof Error ? exc.message : exc));
    } finally {
      setRunBusy(false);
    }
  }

  async function handleStopChange() {
    setStopBusy(true);
    setRunErr(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(projectId)}/changes/${encodeURIComponent(draft.id)}/run`,
        { method: "DELETE" },
      );
      const body = (await res.json()) as
        | { ok: true; status: { status: string } }
        | { ok: false; error: string };
      if (!res.ok || !body.ok) {
        throw new Error(!body.ok ? body.error : `HTTP ${res.status}`);
      }
      await loadChangeRun();
    } catch (exc) {
      setRunErr(String(exc instanceof Error ? exc.message : exc));
    } finally {
      setStopBusy(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function tick() {
      try {
        await loadChangeRun();
      } catch {
        // Keep this panel advisory; the command fallback remains visible.
      }
    }
    void tick();
    const interval = window.setInterval(() => {
      if (!cancelled) void tick();
    }, activeChangeRun ? 2000 : 6000);
    return () => {
      cancelled = true;
      window.clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, draft.id, activeChangeRun]);

  return (
    <>
      <section className="card change-editor-card">
        <div className="section-head">
          <h2 className="section-title">编辑 change-request.md</h2>
          <code className="cell-code">{draft.changeRequestPath}</code>
        </div>
        <MarkdownEditor
          key={draft.id}
          initialValue={draft.content}
          onSave={onSave}
          fileLabel="change-request.md"
          helperText={
            "保存 change-request.md 后，可以直接点击下方按钮在后台运行；终端命令只保留为 fallback。"
          }
        />
      </section>

      <section className="card">
        <div className="section-head">
          <h2 className="section-title">质量扫描</h2>
          <div style={{ display: "flex", gap: "var(--sp-2)" }}>
            {quality.passed ? (
              <StatusBadge variant="completed">就绪</StatusBadge>
            ) : (
              <StatusBadge variant="failed">
                {quality.errorCount} 项错误
              </StatusBadge>
            )}
            {quality.warningCount > 0 && (
              <StatusBadge variant="warning">
                {quality.warningCount} 警告
              </StatusBadge>
            )}
          </div>
        </div>
        <p className="section-subtitle">
          {quality.passedCount}/{quality.totalCount} 项通过。仅提示 ——
          运行时由 Promotion / Apply Gate 强制约束。
        </p>
        {!quality.passed && (
          <ul className="precondition-list">
            {quality.checks
              .filter((c) => !c.passed && c.severity === "error")
              .slice(0, 5)
              .map((c) => (
                <li key={c.id} className="precondition-item">
                  <span className="precondition-mark">×</span>
                  <span>
                    <strong>{c.name}</strong> · {c.message}
                  </span>
                </li>
              ))}
          </ul>
        )}
      </section>

      <section className="card">
        <div className="section-head">
          <div>
            <h2 className="section-title">运行变更</h2>
            <p className="section-subtitle" style={{ margin: "6px 0 0" }}>
              后台自动执行 change new / validate / run，并实时轮询运行状态。
            </p>
          </div>
          <div style={{ display: "flex", gap: "var(--sp-2)", alignItems: "center" }}>
            {activeChangeRun && (
              <button
                type="button"
                className="btn"
                data-variant="danger"
                disabled={stopBusy}
                onClick={() => void handleStopChange()}
              >
                {stopBusy ? "停止中…" : "停止运行"}
              </button>
            )}
            <button
              type="button"
              className="btn"
              data-variant="primary"
              disabled={
                runBusy ||
                activeChangeRun ||
                !quality.passed ||
                !runtimeProjectId
              }
              onClick={() => void handleRunChange()}
            >
              {activeChangeRun ? "运行中…" : "提交并运行 Change"}
            </button>
          </div>
        </div>
        {runErr && (
          <div className="design-error" style={{ marginBottom: "var(--sp-3)" }}>
            <strong>运行失败：</strong> {runErr}
          </div>
        )}
        {hasScopeError && (
          <div className="design-error" style={{ marginBottom: "var(--sp-3)" }}>
            <strong>Scope missing：</strong> 这是硬错误。先补{" "}
            <code>Scope: app/**, components/**, lib/**</code> 或{" "}
            <code>## Scope paths</code> bullet，Studio 才会允许运行。
          </div>
        )}
        {changeRun?.status && (
          <>
            <div
              className="mini-grid"
              style={{
                marginBottom: "var(--sp-3)",
                gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))",
              }}
            >
              <div>
                <span className="cell-muted">Run</span>
                <code className="cell-code">{changeRun.status.runId}</code>
              </div>
              <div>
                <span className="cell-muted">State</span>
                <StatusBadge variant={changeRunStateVariant(changeRun.status.status)}>
                  {translateChangeRunState(changeRun.status.status)}
                </StatusBadge>
              </div>
              <div>
                <span className="cell-muted">Step</span>
                <code className="cell-code">
                  {changeRun.supervisor?.currentStep ??
                    changeRun.status.currentStep ??
                    "—"}
                </code>
              </div>
              <div>
                <span className="cell-muted">Candidate</span>
                <code className="cell-code">
                  {changeRun.supervisor?.currentCandidate ?? "—"}
                </code>
              </div>
              <div>
                <span className="cell-muted">Strategy</span>
                <code className="cell-code">
                  {changeRun.supervisor?.currentStrategy ?? "—"}
                </code>
              </div>
              <div>
                <span className="cell-muted">Failure</span>
                <code className="cell-code">
                  {changeRun.supervisor?.failureType ?? "—"}
                </code>
              </div>
              <div>
                <span className="cell-muted">Elapsed</span>
                <code className="cell-code">
                  {formatDuration(changeRun.supervisor?.elapsedSec ?? changeRun.status.elapsedSec ?? 0)}
                </code>
              </div>
              <div>
                <span className="cell-muted">PID</span>
                <code className="cell-code">
                  {changeRun.supervisor?.currentPid ??
                    changeRun.status.currentPid ??
                    "—"}
                </code>
              </div>
              <div>
                <span className="cell-muted">Last poll</span>
                <code className="cell-code">
                  {lastRunPollAt ? formatTimestamp(lastRunPollAt) : "—"}
                </code>
              </div>
            </div>

            {changeRun.supervisor?.diagnosis && (
              <ChangeRunDiagnosisPanel diagnosis={changeRun.supervisor.diagnosis} />
            )}

            {changeRun.status.watchdog?.triggeredAt && (
              <div
                style={{
                  border: "1px solid #fde68a",
                  background: "#fffbeb",
                  borderRadius: 8,
                  marginBottom: "var(--sp-3)",
                  padding: "10px 12px",
                }}
              >
                <div style={{ display: "flex", gap: "var(--sp-2)", alignItems: "center" }}>
                  <StatusBadge variant="warning">auto guard</StatusBadge>
                  <strong>Studio 自动停止了连续 timeout 的候选运行</strong>
                </div>
                <p className="section-subtitle" style={{ margin: "6px 0 0" }}>
                  Policy: <code>{changeRun.status.watchdog.policy}</code> · Candidates:{" "}
                  <code>{changeRun.status.watchdog.timeoutCandidateIds.join(", ")}</code>
                </p>
              </div>
            )}

            <div
              style={{
                display: "grid",
                gap: "var(--sp-3)",
                gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)",
                marginBottom: "var(--sp-3)",
              }}
            >
              <RunLogPanel
                title="stdout.log"
                text={changeRun.stdoutTail}
                empty="后台进程暂时还没有输出。Codex patch-worker 运行时可能几分钟没有 stdout。"
              />
              <RunLogPanel
                title="stderr.log"
                text={changeRun.stderrTail}
                empty="没有错误输出。"
              />
            </div>

            {changeRun.runtimeSummary && (
              <RuntimeCandidatePanel summary={changeRun.runtimeSummary} />
            )}
          </>
        )}
        {changeRun?.status?.error && (
          <div className="design-error" style={{ marginBottom: "var(--sp-3)" }}>
            <strong>Run manager:</strong> {changeRun.status.error}
          </div>
        )}
        {!runtimeProjectId && (
          <div className="design-error" style={{ marginBottom: "var(--sp-3)" }}>
            <strong>Runtime project is not linked yet.</strong> 先到 Develop 执行
            Prepare Runtime Project。
          </div>
        )}
        <details style={{ marginTop: "var(--sp-3)" }}>
          <summary
            style={{
              color: "var(--color-muted)",
              cursor: "pointer",
              fontWeight: 700,
            }}
          >
            手动命令 fallback
          </summary>
          <p className="section-subtitle" style={{ marginTop: "var(--sp-3)" }}>
            自动运行失败时再复制到终端执行。命令会使用已链接的 runtime project id。
          </p>
          <CommandBlock
            command={cmdNew}
            hint="1. 用这份 draft 生成 change-contract.json。"
          />
          <CommandBlock
            command={cmdRun}
            hint="2. 跑最新 change（autonomous loop）。"
          />
          <CommandBlock
            command={cmdStatus}
            hint="再渲染一次状态。"
          />
          <CommandBlock
            command={cmdValidate}
            hint="重新校验所有 artifacts。"
          />
        </details>
      </section>
    </>
  );
}

function RunLogPanel({
  title,
  text,
  empty,
}: {
  title: string;
  text: string;
  empty: string;
}) {
  return (
    <div>
      <div
        className="cell-muted"
        style={{ fontSize: 12, fontWeight: 700, marginBottom: 6 }}
      >
        {title}
      </div>
      <pre
        style={{
          background: "#0f172a",
          borderRadius: 8,
          color: "#e2e8f0",
          fontSize: 12,
          lineHeight: 1.5,
          margin: 0,
          maxHeight: 260,
          minHeight: 120,
          overflow: "auto",
          padding: 14,
          whiteSpace: "pre-wrap",
        }}
      >
        {text.trim() ? text : empty}
      </pre>
    </div>
  );
}

function ChangeRunDiagnosisPanel({
  diagnosis,
}: {
  diagnosis: ChangeRunDiagnosis;
}) {
  const border =
    diagnosis.severity === "error"
      ? "#fecaca"
      : diagnosis.severity === "warning"
        ? "#fde68a"
        : "#bfdbfe";
  const background =
    diagnosis.severity === "error"
      ? "#fef2f2"
      : diagnosis.severity === "warning"
        ? "#fffbeb"
        : "#eff6ff";
  return (
    <div
      style={{
        background,
        border: `1px solid ${border}`,
        borderRadius: 8,
        marginBottom: "var(--sp-3)",
        padding: "12px 14px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: "var(--sp-2)" }}>
        <StatusBadge variant={diagnosisSeverityVariant(diagnosis.severity)}>
          {diagnosis.severity}
        </StatusBadge>
        <strong>{diagnosis.title}</strong>
      </div>
      <p className="section-subtitle" style={{ margin: "8px 0 0" }}>
        {diagnosis.summary}
      </p>
      <p className="section-subtitle" style={{ margin: "4px 0 0" }}>
        <strong>Next:</strong> {diagnosis.nextAction}
      </p>
    </div>
  );
}

function RuntimeCandidatePanel({
  summary,
}: {
  summary: ChangeRuntimeSummary;
}) {
  return (
    <div style={{ marginBottom: "var(--sp-3)" }}>
      <div className="mini-grid" style={{ marginBottom: "var(--sp-2)" }}>
        <div>
          <span className="cell-muted">Inner run</span>
          <code className="cell-code">{summary.runId}</code>
        </div>
        <div>
          <span className="cell-muted">Runtime package</span>
          <code className="cell-code">{summary.path}</code>
        </div>
        <div>
          <span className="cell-muted">Candidates observed</span>
          <code className="cell-code">{summary.candidateCount}</code>
        </div>
        <div>
          <span className="cell-muted">Decision</span>
          <code className="cell-code">{summary.decision ?? "pending"}</code>
        </div>
        <div>
          <span className="cell-muted">Selected</span>
          <code className="cell-code">{summary.selectedCandidate ?? "—"}</code>
        </div>
      </div>
      {summary.candidates.length > 0 && (
        <table className="data-table">
          <thead>
            <tr>
              <th>candidate</th>
              <th>strategy</th>
              <th>patch</th>
              <th>eval</th>
              <th>repair</th>
              <th>last event</th>
            </tr>
          </thead>
          <tbody>
            {summary.candidates.map((candidate) => (
              <tr key={candidate.id}>
                <td>
                  <code className="cell-code">{candidate.id}</code>
                </td>
                <td className="cell-muted">{candidate.strategy ?? "—"}</td>
                <td>
                  <StatusBadge
                    variant={
                      candidate.sourcePatchPresent
                        ? "completed"
                        : candidate.patchReason === "codex_cli_timeout"
                          ? "failed"
                          : "default"
                    }
                  >
                    {candidate.patchReason ?? candidate.patchStatus ?? "pending"}
                  </StatusBadge>
                </td>
                <td>
                  <StatusBadge
                    variant={
                      candidate.evalPassed === true
                        ? "completed"
                        : candidate.evalPassed === false
                          ? "failed"
                          : "default"
                    }
                  >
                    {candidate.evalPassed === true
                      ? "passed"
                      : candidate.failureType ?? "pending"}
                  </StatusBadge>
                </td>
                <td className="cell-muted">
                  {candidate.repairAttempts ?? 0}
                  {candidate.repairStopReason
                    ? ` · ${candidate.repairStopReason}`
                    : ""}
                </td>
                <td className="cell-muted">{candidate.lastEvent ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ProductReviewPanel({
  detail,
  onOpenArtifact,
}: {
  detail: StudioProjectDetailClient;
  onOpenArtifact: (p: string) => void;
}) {
  const [review, setReview] = useState<ProductReviewResultClient | null>(null);
  const [busy, setBusy] = useState(false);
  const [runBusyId, setRunBusyId] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function loadReview(opts?: { silent?: boolean }) {
    if (!opts?.silent) setErr(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/product-review`,
        { cache: "no-store" },
      );
      const body = (await res.json()) as ProductReviewResponse;
      if (!res.ok || !body.ok) {
        throw new Error(body.ok ? `HTTP ${res.status}` : body.error);
      }
      setReview(body.review);
    } catch (exc) {
      if (!opts?.silent) setErr(String(exc instanceof Error ? exc.message : exc));
    }
  }

  async function runReview() {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/product-review`,
        { method: "POST" },
      );
      const body = (await res.json()) as ProductReviewResponse;
      if (!res.ok || !body.ok || !body.review) {
        throw new Error(body.ok ? `HTTP ${res.status}` : body.error);
      }
      setReview(body.review);
    } catch (exc) {
      setErr(String(exc instanceof Error ? exc.message : exc));
    } finally {
      setBusy(false);
    }
  }

  async function runGeneratedChange(draftId: string) {
    setRunBusyId(draftId);
    setErr(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/changes/${encodeURIComponent(draftId)}/run`,
        { method: "POST" },
      );
      const body = (await res.json()) as
        | { ok: true; status: { runId: string; status: string } }
        | { ok: false; error: string };
      if (!res.ok || !body.ok) {
        throw new Error(!body.ok ? body.error : `HTTP ${res.status}`);
      }
    } catch (exc) {
      setErr(String(exc instanceof Error ? exc.message : exc));
    } finally {
      setRunBusyId(null);
    }
  }

  useEffect(() => {
    void loadReview({ silent: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.id, detail.agentProjectId, detail.agentProjectPath, detail.latestDeliveredSha]);

  const openFindings =
    review?.findings.filter((finding) => (finding.status ?? "open") === "open") ?? [];
  const topFindings =
    openFindings.length > 0
      ? openFindings.slice(0, 4)
      : review?.findings.slice(0, 4) ?? [];

  return (
    <section className="card product-review-card">
      <div className="section-head">
        <div>
          <h2 className="section-title">Studio Product Review</h2>
          <p className="section-subtitle" style={{ margin: "6px 0 0" }}>
            Studio 自己审视生成产品，产出优先级变更计划和可运行的 Change Request 草稿。
          </p>
        </div>
        <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
          {review && (
            <StatusBadge variant={productReviewVariant(review.verdict)}>
              {review.verdict} · {review.score}/100
            </StatusBadge>
          )}
          <button
            type="button"
            className="btn"
            data-variant="primary"
            disabled={busy}
            onClick={() => void runReview()}
          >
            {busy ? "Reviewing…" : "Run Product Review"}
          </button>
        </div>
      </div>

      {err && (
        <div className="design-error" style={{ marginBottom: "var(--sp-3)" }}>
          <strong>Product Review 失败：</strong> {err}
        </div>
      )}

      {!review ? (
        <p className="cell-muted">
          还没有产品评审。初次开发或重要变更完成后运行一次，让 Studio 生成下一组
          scoped Change Request。
        </p>
      ) : (
        <>
          <p className="section-subtitle">{review.summary}</p>
          <div className="mini-grid" style={{ marginTop: "var(--sp-3)" }}>
            <div>
              <span className="cell-muted">review</span>
              <code className="cell-code">{review.reviewId}</code>
            </div>
            <div>
              <span className="cell-muted">runtime</span>
              <StatusBadge variant={review.context.runtimeLinked ? "completed" : "failed"}>
                {review.context.runtimeLinked ? "linked" : "missing"}
              </StatusBadge>
            </div>
            <div>
              <span className="cell-muted">latest change</span>
              <code className="cell-code">
                {review.context.latestDeliveredChangeId ?? "n/a"}
              </code>
            </div>
            <div>
              <span className="cell-muted">provider mode</span>
              <code className="cell-code">{review.context.providerMode ?? "n/a"}</code>
            </div>
            <div>
              <span className="cell-muted">inputs read</span>
              <code className="cell-code">{review.inputs_read?.length ?? "n/a"}</code>
            </div>
          </div>

          <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap", marginTop: "var(--sp-3)" }}>
            <button
              type="button"
              className="btn"
              data-variant="ghost"
              onClick={() => onOpenArtifact(review.artifacts.reviewMd)}
            >
              Open product-review.md
            </button>
            <button
              type="button"
              className="btn"
              data-variant="ghost"
              onClick={() => onOpenArtifact(review.artifacts.changePlanMd)}
            >
              Open change plan
            </button>
            <Link
              className="btn"
              data-variant="ghost"
              href={`/projects/${detail.id}?tab=discuss&mode=change`}
            >
              Open generated drafts
            </Link>
          </div>

          {topFindings.length > 0 && (
            <div style={{ marginTop: "var(--sp-4)" }}>
              <h3 className="section-kicker">
                {openFindings.length > 0 ? "Open findings" : "Resolved checks"}
              </h3>
              <ul className="precondition-list">
                {topFindings.map((finding) => (
                  <li key={finding.id} className="precondition-item">
                    <StatusBadge variant={findingSeverityVariant(finding.severity)}>
                      {finding.severity}
                    </StatusBadge>
                    <StatusBadge
                      variant={(finding.status ?? "open") === "resolved" ? "completed" : "default"}
                    >
                      {finding.status ?? "open"}
                    </StatusBadge>
                    <span>
                      <strong>{finding.title}</strong>
                      <span className="cell-muted"> · {finding.recommendation}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {review.recommendedChanges.length > 0 && (
            <div style={{ marginTop: "var(--sp-4)" }}>
              <h3 className="section-kicker">Generated Change Requests</h3>
              <div className="evidence-card-stack">
                {review.recommendedChanges.map((change) => (
                  <div key={change.id} className="artifact-row">
                    <div>
                      <strong>{change.title}</strong>
                      <p className="cell-muted" style={{ margin: "4px 0 0" }}>
                        {change.priority} · risk {change.risk ?? "n/a"} ·{" "}
                        difficulty {change.estimatedDifficulty ?? "n/a"} ·{" "}
                        <code>{change.draftId}</code> · {change.rationale}
                      </p>
                    </div>
                    <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
                      <Link
                        className="btn"
                        data-variant="ghost"
                        href={`/projects/${detail.id}?tab=discuss&mode=change`}
                      >
                        View draft
                      </Link>
                      <button
                        type="button"
                        className="btn"
                        data-variant="primary"
                        disabled={runBusyId !== null}
                        onClick={() => void runGeneratedChange(change.draftId)}
                      >
                        {runBusyId === change.draftId ? "Starting…" : "Run Change"}
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </>
      )}
    </section>
  );
}

// ===========================================================================
// Develop
// ===========================================================================

function DevelopTab({
  detail,
  loading,
  isRunning,
  onRefresh,
  onOpenArtifact,
}: {
  detail: StudioProjectDetailClient;
  loading: boolean;
  isRunning: boolean;
  onRefresh: () => void;
  onOpenArtifact: (p: string) => void;
}) {
  const runDetail = detail.runDetail;
  const session: AutonomousSessionLike | null = runDetail?.latestSession ?? null;
  const tasks: TaskLike[] = Array.isArray(runDetail?.taskGraph?.tasks)
    ? runDetail!.taskGraph!.tasks!
    : [];
  const counts = session?.task_counts ?? {};
  // Reviews older than the latest successful delivery are historical and do
  // not block the user — the project has already moved past them. Split
  // open reviews into "fresh" (after latest delivery) and "stale" (before)
  // so the Action Required banner only screams for actual pending decisions.
  const allReviewItems = Array.isArray(runDetail?.reviewQueue?.items)
    ? runDetail!.reviewQueue!.items
    : [];
  const openReviews = allReviewItems.filter((i) => i.status === "open");
  const latestDeliveredAtMs = (() => {
    let best: number | null = null;
    for (const c of runDetail?.changes ?? []) {
      if (c.state !== "delivered" || !c.appliedAt) continue;
      const ms = Date.parse(c.appliedAt);
      if (Number.isFinite(ms) && (best == null || ms > best)) best = ms;
    }
    return best;
  })();
  const freshReviews = openReviews.filter((r) => {
    if (latestDeliveredAtMs == null) return true;
    const ms = r.createdAt ? Date.parse(r.createdAt) : NaN;
    if (!Number.isFinite(ms)) return true;
    return ms > latestDeliveredAtMs;
  });
  const staleReviews = openReviews.filter((r) => !freshReviews.includes(r));
  // Keep the old name for downstream gating logic but point it at the fresh set.
  const blockingReviews = freshReviews;

  const sessionDir =
    runDetail && runDetail.latestSessionId
      ? `${runDetail.relPath}/.agent/autonomous/sessions/${runDetail.latestSessionId}`
      : null;

  const isLocked = detail.lockState.locked;
  const [prepareRun, setPrepareRun] =
    useState<RuntimePrepareStatusClient | null>(null);
  const [prepareStdout, setPrepareStdout] = useState("");
  const [prepareStderr, setPrepareStderr] = useState("");
  const [prepareBusy, setPrepareBusy] = useState(false);
  const [prepareError, setPrepareError] = useState<string | null>(null);
  const [linkRuntimeRef, setLinkRuntimeRef] = useState("");
  const [linkBusy, setLinkBusy] = useState(false);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [developmentRun, setDevelopmentRun] =
    useState<RuntimeDevelopmentStatusClient | null>(null);
  const [developmentStdout, setDevelopmentStdout] = useState("");
  const [developmentStderr, setDevelopmentStderr] = useState("");
  const [developmentBusy, setDevelopmentBusy] = useState(false);
  const [developmentError, setDevelopmentError] = useState<string | null>(null);
  const isRuntimeLinked = Boolean(detail.agentProjectId && detail.agentProjectPath);
  const runtimeProjectId = isRuntimeLinked ? detail.agentProjectId : null;
  const runtimeProjectPath = isRuntimeLinked ? detail.agentProjectPath : null;
  const prepareIsActive =
    prepareRun?.state === "queued" || prepareRun?.state === "running";
  const developmentIsActive =
    developmentRun?.status === "starting" || developmentRun?.status === "running";
  const runtimeAlreadyCompleted = runDetail?.latestSessionStatus === "completed";
  const showBootstrapPanel =
    !isRuntimeLinked || prepareIsActive || prepareRun?.state === "failed";
  const startBlockedReason = !isLocked
    ? "Lock the contract first."
    : !isRuntimeLinked
      ? "Runtime project is not linked yet."
      : blockingReviews.length > 0
        ? "Action Required: blocking review items exist."
        : runtimeAlreadyCompleted
          ? "Runtime project already has completed session. Use Change Request instead."
          : null;

  async function loadPrepareRun(opts?: { silent?: boolean }) {
    if (!opts?.silent) setPrepareError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/run?kind=prepare`,
        { cache: "no-store" },
      );
      const body = (await res.json()) as
        | RuntimePrepareRunResponse
        | { error: string };
      if (!res.ok || "error" in body) {
        throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
      }
      setPrepareRun(body.status);
      setPrepareStdout(body.stdoutTail);
      setPrepareStderr(body.stderrTail);
      if (body.status?.state === "completed") {
        onRefresh();
      }
    } catch (exc) {
      if (!opts?.silent) setPrepareError(String(exc));
    }
  }

  async function startPrepareRuntime() {
    setPrepareBusy(true);
    setPrepareError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/prepare`,
        { method: "POST" },
      );
      const body = (await res.json()) as RuntimePrepareKickoffResponse;
      if (!res.ok || !body.ok) {
        throw new Error(body.ok ? `HTTP ${res.status}` : body.error);
      }
      await loadPrepareRun();
    } catch (exc) {
      setPrepareError(String(exc));
    } finally {
      setPrepareBusy(false);
    }
  }

  async function linkExistingRuntime() {
    setLinkBusy(true);
    setLinkError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/link-runtime`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ runtimeRef: linkRuntimeRef }),
        },
      );
      const body = (await res.json()) as
        | { ok: true; agentProjectId: string; agentProjectPath: string }
        | { ok: false; error: string };
      if (!res.ok || !body.ok) {
        throw new Error(body.ok ? `HTTP ${res.status}` : body.error);
      }
      setLinkRuntimeRef("");
      await onRefresh();
    } catch (exc) {
      setLinkError(String(exc));
    } finally {
      setLinkBusy(false);
    }
  }

  async function loadDevelopmentRun(opts?: { silent?: boolean }) {
    if (!opts?.silent) setDevelopmentError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/run?kind=development`,
        { cache: "no-store" },
      );
      const body = (await res.json()) as
        | RuntimeDevelopmentRunResponse
        | { error: string };
      if (!res.ok || "error" in body) {
        throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
      }
      setDevelopmentRun(body.status);
      setDevelopmentStdout(body.stdoutTail);
      setDevelopmentStderr(body.stderrTail);
      if (
        body.status?.status === "completed" ||
        body.status?.status === "needs_human" ||
        body.status?.status === "failed" ||
        body.status?.status === "stopped"
      ) {
        onRefresh();
      }
    } catch (exc) {
      if (!opts?.silent) setDevelopmentError(String(exc));
    }
  }

  async function startDevelopmentRun() {
    setDevelopmentBusy(true);
    setDevelopmentError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/start`,
        { method: "POST" },
      );
      const body = (await res.json()) as RuntimeDevelopmentKickoffResponse;
      if (!res.ok || !body.ok) {
        throw new Error(body.ok ? `HTTP ${res.status}` : body.error);
      }
      await loadDevelopmentRun();
    } catch (exc) {
      setDevelopmentError(String(exc));
    } finally {
      setDevelopmentBusy(false);
    }
  }

  async function stopDevelopmentRun() {
    setDevelopmentBusy(true);
    setDevelopmentError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/stop`,
        { method: "POST" },
      );
      const body = (await res.json()) as RuntimeDevelopmentStopResponse;
      if (!res.ok || !body.ok) {
        throw new Error(body.ok ? `HTTP ${res.status}` : body.error);
      }
      await loadDevelopmentRun();
    } catch (exc) {
      setDevelopmentError(String(exc));
    } finally {
      setDevelopmentBusy(false);
    }
  }

  useEffect(() => {
    void loadPrepareRun({ silent: true });
    void loadDevelopmentRun({ silent: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.id]);

  useEffect(() => {
    if (!prepareIsActive) return;
    const t = setInterval(
      () => void loadPrepareRun({ silent: true }),
      POLL_INTERVAL_MS,
    );
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prepareIsActive, detail.id]);

  useEffect(() => {
    if (!developmentIsActive) return;
    const t = setInterval(() => {
      void loadDevelopmentRun({ silent: true });
      onRefresh();
    }, RUN_POLL_INTERVAL_MS);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [developmentIsActive, detail.id]);

  return (
    <>
      <p className="tab-tagline">
        启动、观察、停止本地开发流程。日志收在右侧，主栏只保留决策和进度。
      </p>

      {/* 顶部 Action Required (only fresh reviews newer than latest delivery) */}
      {blockingReviews.length > 0 && (
        <ActionRequiredBanner
          items={blockingReviews}
          projectId={detail.id}
          onOpenArtifact={onOpenArtifact}
        />
      )}
      {blockingReviews.length === 0 && staleReviews.length > 0 && (
        <p
          className="cell-muted"
          style={{
            fontSize: 12,
            margin: "0 0 var(--sp-3)",
            padding: "var(--sp-2) var(--sp-3)",
            background: "var(--color-surface-alt)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-sm)",
          }}
        >
          {staleReviews.length} 项历史 review（创建早于最新交付,已不再阻塞）·{" "}
          <Link
            href="/review"
            style={{ color: "var(--color-info)", textDecoration: "underline" }}
          >
            完整 Review Queue →
          </Link>
        </p>
      )}

      <ProductReviewPanel
        detail={detail}
        onOpenArtifact={onOpenArtifact}
      />

      <div className="develop-workspace">
        <main className="develop-main">
      {showBootstrapPanel && (
      <section className="card">
        <div className="section-head">
          <h2 className="section-title">Runtime Project Bootstrap</h2>
          <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
            {prepareRun && (
              <StatusBadge variant={statusVariant(prepareRun.state)}>
                {translateSessionStatus(prepareRun.state)}
              </StatusBadge>
            )}
            {isRuntimeLinked && (
              <StatusBadge variant="completed">Ready to Start Development</StatusBadge>
            )}
            <button
              type="button"
              className="btn"
              data-variant="ghost"
              onClick={() => void loadPrepareRun()}
              disabled={prepareBusy}
            >
              刷新
            </button>
          </div>
        </div>
        <p className="section-subtitle">
          这一步只准备 runtime project：生成 task graph、复制 Next.js scaffold、安装依赖、
          typecheck、build、baseline commit 和 preflight。不会启动 autonomous run。
        </p>

        {!isLocked && (
          <p className="cell-muted">
            合同还没 Lock。先在讨论页锁定合同，才能准备 runtime project。
          </p>
        )}

        {isLocked && !isRuntimeLinked && (
          <p className="cell-muted" style={{ fontSize: 13 }}>
            Runtime project is not linked yet.
          </p>
        )}

        {isLocked && !isRuntimeLinked && (
          <div style={{ display: "grid", gap: "var(--sp-3)" }}>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              onClick={() => void startPrepareRuntime()}
              disabled={prepareBusy || prepareIsActive}
            >
              {prepareBusy || prepareIsActive
                ? "Preparing Runtime Project…"
                : "Prepare Runtime Project"}
            </button>
            {prepareError && (
              <p className="design-error" style={{ margin: 0 }}>
                <strong>Prepare 失败：</strong> {prepareError}
              </p>
            )}
            <div
              style={{
                display: "grid",
                gap: "var(--sp-2)",
                maxWidth: 720,
              }}
            >
              <label className="cell-muted" style={{ fontSize: 12 }}>
                Link existing runtime project
              </label>
              <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
                <input
                  value={linkRuntimeRef}
                  onChange={(e) => setLinkRuntimeRef(e.target.value)}
                  placeholder="project_xxx or .agent-studio/projects/<dir>"
                  style={{
                    flex: "1 1 320px",
                    minWidth: 0,
                    padding: "8px 10px",
                    border: "1px solid var(--color-border)",
                    borderRadius: "var(--radius-md)",
                    fontFamily: "var(--font-mono)",
                    fontSize: 12,
                  }}
                />
                <button
                  type="button"
                  className="btn"
                  data-variant="ghost"
                  onClick={() => void linkExistingRuntime()}
                  disabled={linkBusy || linkRuntimeRef.trim().length === 0}
                >
                  {linkBusy ? "Linking…" : "Link"}
                </button>
              </div>
              {linkError && (
                <p className="design-error" style={{ margin: 0 }}>
                  <strong>Link 失败：</strong> {linkError}
                </p>
              )}
            </div>
          </div>
        )}

        {isRuntimeLinked && (
          <dl className="summary-defs" style={{ marginTop: "var(--sp-3)" }}>
            <div>
              <dt>agent project id</dt>
              <dd>
                <code className="cell-code">{runtimeProjectId}</code>
              </dd>
            </div>
            {runtimeProjectPath && (
              <div>
                <dt>agent project path</dt>
                <dd>
                  <code className="cell-code">{runtimeProjectPath}</code>
                </dd>
              </div>
            )}
            <div>
              <dt>下一步</dt>
              <dd>
                <StatusBadge variant="completed">preflight passed / ready</StatusBadge>
              </dd>
            </div>
          </dl>
        )}

        {prepareRun && (
          <PrepareRunPanel
            run={prepareRun}
          />
        )}
      </section>
      )}

      {!isRuntimeLinked ? (
      <section className="card runtime-next-card">
        <div className="section-head">
          <h2 className="section-title">Runtime project is not linked yet.</h2>
          <StatusBadge variant="needs-review">prepare required</StatusBadge>
        </div>
        <p className="section-subtitle">
          先准备或链接 runtime project。缺少 agentProjectId / agentProjectPath 时，
          Studio 不会显示 Start Development，避免跑到错误项目。
        </p>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          onClick={() => void startPrepareRuntime()}
          disabled={!isLocked || prepareBusy || prepareIsActive}
        >
          {prepareBusy || prepareIsActive ? "Preparing Runtime Project…" : "Prepare Runtime Project"}
        </button>
      </section>
      ) : runtimeAlreadyCompleted && !developmentIsActive ? (
      <section className="card runtime-next-card">
        <div className="section-head">
          <h2 className="section-title">Initial development is complete.</h2>
          <StatusBadge variant="completed">greenfield completed</StatusBadge>
        </div>
        <p className="section-subtitle">
          Greenfield development already completed. Use Change Request for further work.
          这更接近真实工作流：MVP 交付后，每次后续修改都走 scoped change request。
        </p>
        <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
          <Link
            href={`/projects/${detail.id}?tab=discuss&mode=change`}
            className="btn"
            data-variant="primary"
          >
            Start a Change Request →
          </Link>
          <Link
            href={`/projects/${detail.id}?tab=deliver`}
            className="btn"
            data-variant="ghost"
          >
            Open Deliver
          </Link>
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            onClick={() => void loadDevelopmentRun()}
            disabled={developmentBusy}
          >
            Refresh Run
          </button>
        </div>
      </section>
      ) : (
      <section className="card">
        <div className="section-head">
          <h2 className="section-title">Start Development</h2>
          <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
            {developmentRun && (
              <StatusBadge variant={statusVariant(developmentRun.status)}>
                {translateSessionStatus(developmentRun.status)}
              </StatusBadge>
            )}
            {developmentRun?.phase && (
              <StatusBadge variant="running">{developmentRun.phase}</StatusBadge>
            )}
            {developmentRun?.status === "completed" && (
              <Link
                href={`/projects/${detail.id}?tab=deliver`}
                className="btn"
                data-variant="primary"
                style={{ padding: "4px 10px", fontSize: 12 }}
              >
                Open Deliver
              </Link>
            )}
          </div>
        </div>
        <p className="section-subtitle">
          受控运行 autonomous start：先跑 preflight，通过后启动开发进程。
          不部署、不 git push、不自动批准 review。
        </p>

        {isRuntimeLinked && (
          <dl className="summary-defs" style={{ marginTop: "var(--sp-3)" }}>
            <div>
              <dt>runtime project</dt>
              <dd>
                <code className="cell-code">{runtimeProjectId}</code>{" "}
                <code className="cell-code">{runtimeProjectPath}</code>
              </dd>
            </div>
            {developmentRun?.pid && (
              <div>
                <dt>pid</dt>
                <dd>
                  <code className="cell-code">{developmentRun.pid}</code>
                </dd>
              </div>
            )}
            {(developmentRun?.sessionId || runDetail?.latestSessionId) && (
              <div>
                <dt>session id</dt>
                <dd>
                  <code className="cell-code">
                    {developmentRun?.sessionId ?? runDetail?.latestSessionId}
                  </code>
                </dd>
              </div>
            )}
            <div>
              <dt>review queue</dt>
              <dd>
                <CountChip
                  variant={developmentRun?.reviewOpenCount ? "needs-review" : "completed"}
                  label="open"
                  value={developmentRun?.reviewOpenCount ?? detail.reviewQueueOpen}
                />
                <CountChip
                  variant={developmentRun?.reviewBlockingCount ? "failed" : "completed"}
                  label="blocking"
                  value={developmentRun?.reviewBlockingCount ?? detail.reviewQueueBlocking}
                />
              </dd>
            </div>
          </dl>
        )}

        {!isRuntimeLinked && (
          <div className="design-empty" style={{ marginTop: "var(--sp-3)" }}>
            <p>
              <strong>Runtime project is not linked yet.</strong>
            </p>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              onClick={() => void startPrepareRuntime()}
              disabled={!isLocked || prepareBusy || prepareIsActive}
              style={{ marginTop: "var(--sp-2)" }}
            >
              Prepare Runtime Project
            </button>
          </div>
        )}

        {isRuntimeLinked && startBlockedReason && !developmentIsActive && (
          <p className="cell-muted" style={{ fontSize: 13 }}>
            {startBlockedReason}
          </p>
        )}

        {developmentError && (
          <p className="design-error" style={{ margin: "var(--sp-2) 0 0" }}>
            <strong>Development run 失败：</strong> {developmentError}
          </p>
        )}

        {isRuntimeLinked && (
          <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap", marginTop: "var(--sp-3)" }}>
            {developmentRun?.status === "completed" || runtimeAlreadyCompleted ? (
              <Link
                href={`/projects/${detail.id}?tab=deliver`}
                className="btn"
                data-variant="primary"
              >
                Open Deliver →
              </Link>
            ) : developmentIsActive ? (
              <button
                type="button"
                className="btn"
                data-variant="danger"
                onClick={() => void stopDevelopmentRun()}
                disabled={developmentBusy}
              >
                {developmentBusy ? "Stopping…" : "Stop Run"}
              </button>
            ) : (
              <button
                type="button"
                className="btn"
                data-variant="primary"
                onClick={() => void startDevelopmentRun()}
                disabled={developmentBusy || Boolean(startBlockedReason)}
              >
                {developmentBusy ? "Starting…" : "Start Development"}
              </button>
            )}
            <button
              type="button"
              className="btn"
              data-variant="ghost"
              onClick={() => void loadDevelopmentRun()}
              disabled={developmentBusy}
            >
              Refresh Run
            </button>
          </div>
        )}

        {developmentRun && (
          <DevelopmentRunPanel
            run={developmentRun}
          />
        )}
      </section>
      )}

      {/* 没有运行过 —— 引导用户启动 */}
      {!runDetail && (
        <div className="design-empty">
          <p>
            <strong>这个项目还没有 autonomous session。</strong>
          </p>
          {!isLocked && (
            <p style={{ marginTop: "var(--sp-2)" }}>
              先在<Link href={`/projects/${detail.id}?tab=discuss`} style={{ color: "var(--color-info)", textDecoration: "underline" }}>讨论与锁定</Link>页面把合同 Lock。
            </p>
          )}
          {isLocked && !isRuntimeLinked && (
            <p style={{ marginTop: "var(--sp-2)" }}>
              Runtime project is not linked yet. 先点击上方 <strong>Prepare Runtime Project</strong>，
              或手动链接已有 runtime project。
            </p>
          )}
          {isLocked && isRuntimeLinked && runtimeProjectId && (
            <>
              <p style={{ marginTop: "var(--sp-2)" }}>
                Runtime project 已准备好。点击上方 <strong>Start Development</strong>
                会先跑 preflight，再启动 autonomous start。
              </p>
              <p className="cell-muted" style={{ fontSize: 12, marginTop: "var(--sp-3)" }}>
                Run Manager 只允许硬编码的 start/stop 路径；不部署、不 git push。
              </p>
            </>
          )}
        </div>
      )}

      {runDetail && (
        <section className="card">
          <div className="section-head">
            <h2 className="section-title">任务时间线</h2>
            <span className="badge">{tasks.length} 项</span>
          </div>
          {tasks.length === 0 ? (
            <p className="cell-muted">
              没有 task graph。在终端跑{" "}
              <code>agent-studio new --from {detail.contract.mvpRequirementsRelPath}</code>。
            </p>
          ) : (
            <ul className="task-timeline">
              {tasks.map((t, i) => (
                <li key={String(t.id ?? i)} className="task-timeline-item">
                  <StatusBadge variant={taskStatusVariant(t.status)}>
                    {translateTaskStatus(t.status)}
                  </StatusBadge>
                  <div className="task-timeline-body">
                    <div className="task-timeline-head">
                      <code className="cell-code">{String(t.id ?? `task-${i + 1}`)}</code>
                      <strong>{t.title ?? "(未命名)"}</strong>
                    </div>
                    {t.intent && (
                      <p className="cell-muted" style={{ fontSize: 12, margin: "2px 0 0" }}>
                        {truncate(t.intent, 160)}
                      </p>
                    )}
                  </div>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}
        </main>
        <aside className="develop-diagnostics">
          <RuntimeDiagnosticsAside
            prepareStdout={prepareStdout}
            prepareStderr={prepareStderr}
            developmentStdout={developmentStdout}
            developmentStderr={developmentStderr}
          />
        </aside>
      </div>
    </>
  );
}

function PrepareRunPanel({
  run,
}: {
  run: RuntimePrepareStatusClient;
}) {
  const steps = Array.isArray(run.steps) ? run.steps : [];

  return (
    <div style={{ marginTop: "var(--sp-4)", display: "grid", gap: "var(--sp-3)" }}>
      <div className="task-timeline">
        {steps.map((step) => (
          <div key={step.id} className="task-timeline-item">
            <StatusBadge variant={statusVariant(step.state)}>
              {translateSessionStatus(step.state)}
            </StatusBadge>
            <div className="task-timeline-body">
              <div className="task-timeline-head">
                <code className="cell-code">{step.id}</code>
                <strong>{step.label}</strong>
              </div>
              {step.errorSummary && (
                <p className="cell-muted" style={{ fontSize: 12, margin: "2px 0 0" }}>
                  {step.errorSummary}
                </p>
              )}
            </div>
          </div>
        ))}
      </div>

      {run.error && (
        <p className="design-error" style={{ margin: 0 }}>
          <strong>Run error：</strong> {run.error}
        </p>
      )}
    </div>
  );
}

function DevelopmentRunPanel({
  run,
}: {
  run: RuntimeDevelopmentStatusClient;
}) {
  const reviewCount = run.taskCounts["needs-human-review"] ?? run.taskCounts.needs_human_review ?? 0;
  return (
    <div style={{ marginTop: "var(--sp-4)", display: "grid", gap: "var(--sp-3)" }}>
      <dl className="summary-defs">
        <div>
          <dt>run id</dt>
          <dd>
            <code className="cell-code">{run.runId}</code>
          </dd>
        </div>
        <div>
          <dt>state</dt>
          <dd>
            <StatusBadge variant={statusVariant(run.status)}>
              {translateSessionStatus(run.status)}
            </StatusBadge>{" "}
            {run.phase && <code className="cell-code">{run.phase}</code>}
          </dd>
        </div>
        {run.currentTaskId && (
          <div>
            <dt>current task</dt>
            <dd>
              <code className="cell-code">{run.currentTaskId}</code>
            </dd>
          </div>
        )}
        <div>
          <dt>task progress</dt>
          <dd>
            <CountChip variant="completed" label="done" value={asNum(run.taskCounts.completed)} />
            <CountChip variant="default" label="pending" value={asNum(run.taskCounts.pending)} />
            <CountChip variant="running" label="running" value={asNum(run.taskCounts.running)} />
            <CountChip variant="needs-review" label="review" value={asNum(reviewCount)} />
            <CountChip variant="failed" label="abandoned" value={asNum(run.taskCounts.abandoned)} />
          </dd>
        </div>
        <div>
          <dt>checks</dt>
          <dd>
            <span className="cell-muted" style={{ fontSize: 12 }}>
              preflight={run.preflightExitCode ?? "—"} · validate-artifacts=
              {run.validateArtifactsExitCode ?? "—"} · exit={run.exitCode ?? "—"}
            </span>
          </dd>
        </div>
      </dl>

      {run.error && (
        <p className="design-error" style={{ margin: 0 }}>
          <strong>Run manager：</strong> {run.error}
        </p>
      )}
    </div>
  );
}

function RuntimeDiagnosticsAside({
  prepareStdout,
  prepareStderr,
  developmentStdout,
  developmentStderr,
}: {
  prepareStdout: string;
  prepareStderr: string;
  developmentStdout: string;
  developmentStderr: string;
}) {
  const hasPrepareLogs = Boolean(prepareStdout || prepareStderr);
  const hasDevelopmentLogs = Boolean(developmentStdout || developmentStderr);

  return (
    <div className="runtime-log-card">
      <div className="runtime-log-card-head">
        <div>
          <h2 className="section-title">Run Logs</h2>
          <p className="section-subtitle">诊断信息放这里，不占主流程。</p>
        </div>
      </div>

      {!hasPrepareLogs && !hasDevelopmentLogs && (
        <p className="cell-muted" style={{ margin: 0 }}>
          还没有 stdout / stderr。
        </p>
      )}

      {hasDevelopmentLogs && (
        <details className="runtime-log-group" open>
          <summary>development run</summary>
          <RuntimeLog title="stdout.log" text={developmentStdout} />
          <RuntimeLog title="stderr.log" text={developmentStderr} />
        </details>
      )}

      {hasPrepareLogs && (
        <details className="runtime-log-group" open={!hasDevelopmentLogs}>
          <summary>runtime bootstrap</summary>
          <RuntimeLog title="stdout.log" text={prepareStdout} />
          <RuntimeLog title="stderr.log" text={prepareStderr} />
        </details>
      )}
    </div>
  );
}

function RuntimeLog({ title, text }: { title: string; text: string }) {
  if (!text) {
    return (
      <div>
        <div className="cell-muted" style={{ fontSize: 12, marginBottom: 4 }}>
          {title}
        </div>
        <div className="command-block">
          <pre className="command-block-pre">
            <code>(empty)</code>
          </pre>
        </div>
      </div>
    );
  }
  return (
    <div>
      <div className="cell-muted" style={{ fontSize: 12, marginBottom: 4 }}>
        {title}
      </div>
      <div className="command-block" style={{ maxHeight: 260, overflow: "auto" }}>
        <pre className="command-block-pre">
          <code>{text}</code>
        </pre>
      </div>
    </div>
  );
}

function ActionRequiredBanner({
  items,
  projectId,
  onOpenArtifact,
}: {
  items: ReviewItemSummary[];
  projectId: string;
  onOpenArtifact: (path: string) => void;
}) {
  return (
    <section className="card action-required-card">
      <div className="section-head">
        <h2 className="section-title">⚠ 需要处理</h2>
        <Link
          href="/review"
          className="btn"
          data-variant="ghost"
          style={{ padding: "4px 10px", fontSize: 12 }}
        >
          完整 Review Queue →
        </Link>
      </div>
      <p className="section-subtitle">
        Studio 暂停了 {items.length} 项等待人工决策。下方命令复制到终端执行；
        Console 不会自动批准 / 拒绝。
      </p>
      {items.slice(0, 3).map((item) => (
        <div key={item.reviewId} className="action-required-item">
          <div className="action-required-head">
            <code className="cell-code">{item.reviewId}</code>
            <StatusBadge variant={severityVariant(item.severity)}>
              {item.severity}
            </StatusBadge>
            <span className="cell-muted" style={{ fontSize: 12 }}>
              {item.reasonCode || item.sourceType || "—"}
            </span>
          </div>
          {item.title && (
            <p style={{ margin: "var(--sp-1) 0 var(--sp-2)", fontSize: 13 }}>
              {item.title}
            </p>
          )}
          <div className="artifact-button-row" style={{ marginBottom: "var(--sp-2)" }}>
            {item.reviewItemPath && (
              <button
                type="button"
                className="btn"
                data-variant="ghost"
                onClick={() => onOpenArtifact(item.reviewItemPath!)}
                style={{ padding: "4px 8px", fontSize: 11 }}
              >
                review-item.json
              </button>
            )}
            {item.promotionReportPath && (
              <button
                type="button"
                className="btn"
                data-variant="ghost"
                onClick={() => onOpenArtifact(item.promotionReportPath!)}
                style={{ padding: "4px 8px", fontSize: 11 }}
              >
                promotion-report.json
              </button>
            )}
            {item.changedFilesPath && (
              <button
                type="button"
                className="btn"
                data-variant="ghost"
                onClick={() => onOpenArtifact(item.changedFilesPath!)}
                style={{ padding: "4px 8px", fontSize: 11 }}
              >
                changed-files.json
              </button>
            )}
          </div>
          <CommandBlock
            command={`agent-studio autonomous reviews show ${item.reviewId} --project ${projectId} --session ${item.sessionId}`}
            hint="查看完整 review item。"
          />
          {item.status === "open" && (
            <CommandBlock
              command={`agent-studio autonomous reviews approve ${item.reviewId} --project ${projectId} --session ${item.sessionId} --yes`}
              hint="批准（人工 override + commit trailer）。"
            />
          )}
        </div>
      ))}
      {items.length > 3 && (
        <p className="cell-muted" style={{ fontSize: 12 }}>
          还有 {items.length - 3} 项；完整在 Review Queue。
        </p>
      )}
    </section>
  );
}

// ===========================================================================
// Deliver
// ===========================================================================

function DeliverTab({
  detail,
  status,
  onOpenArtifact,
}: {
  detail: StudioProjectDetailClient;
  status: ReturnType<typeof deriveStudioProjectStatus>;
  onOpenArtifact: (path: string) => void;
}) {
  const runDetail = detail.runDetail;
  const changes = runDetail?.changes ?? [];
  const isRuntimeLinked = Boolean(detail.agentProjectId && detail.agentProjectPath);
  const runtimeProjectId = isRuntimeLinked ? detail.agentProjectId : null;
  const runtimeProjectPath = isRuntimeLinked ? detail.agentProjectPath : null;
  const sessionDir =
    isRuntimeLinked && runDetail && runDetail.latestSessionId
      ? `${runDetail.relPath}/.agent/autonomous/sessions/${runDetail.latestSessionId}`
      : null;
  const greenfieldComplete =
    runDetail?.latestSessionStatus === "completed" && changes.length === 0;
  const latestDeliveredChange = latestDelivered(changes);
  const [previewStatus, setPreviewStatus] =
    useState<PreviewRunStatusClient | null>(null);
  const [previewStdout, setPreviewStdout] = useState("");
  const [previewStderr, setPreviewStderr] = useState("");
  const [previewBusy, setPreviewBusy] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);

  async function loadPreview(opts?: { silent?: boolean }) {
    if (!opts?.silent) setPreviewError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/preview`,
        { cache: "no-store" },
      );
      const body = (await res.json()) as PreviewRunResponse | { error: string };
      if (!res.ok || "error" in body) {
        throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
      }
      setPreviewStatus(body.status);
      setPreviewStdout(body.stdoutTail);
      setPreviewStderr(body.stderrTail);
    } catch (exc) {
      if (!opts?.silent) setPreviewError(String(exc));
    }
  }

  async function startPreview() {
    setPreviewBusy(true);
    setPreviewError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/preview`,
        { method: "POST" },
      );
      const body = (await res.json()) as PreviewRunStartStopResponse;
      if (!res.ok || !body.ok) {
        throw new Error(body.ok ? `HTTP ${res.status}` : body.error);
      }
      await loadPreview({ silent: true });
    } catch (exc) {
      setPreviewError(String(exc));
    } finally {
      setPreviewBusy(false);
    }
  }

  async function restartPreview() {
    setPreviewBusy(true);
    setPreviewError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/preview?restart=1`,
        { method: "POST" },
      );
      const body = (await res.json()) as PreviewRunStartStopResponse;
      if (!res.ok || !body.ok) {
        throw new Error(body.ok ? `HTTP ${res.status}` : body.error);
      }
      await loadPreview({ silent: true });
    } catch (exc) {
      setPreviewError(String(exc));
    } finally {
      setPreviewBusy(false);
    }
  }

  async function stopPreview() {
    setPreviewBusy(true);
    setPreviewError(null);
    try {
      const res = await fetch(
        `/api/studio-projects/${encodeURIComponent(detail.id)}/preview`,
        { method: "DELETE" },
      );
      const body = (await res.json()) as PreviewRunStartStopResponse;
      if (!res.ok || !body.ok) {
        throw new Error(body.ok ? `HTTP ${res.status}` : body.error);
      }
      await loadPreview({ silent: true });
    } catch (exc) {
      setPreviewError(String(exc));
    } finally {
      setPreviewBusy(false);
    }
  }

  useEffect(() => {
    void loadPreview({ silent: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [detail.id, detail.agentProjectId, detail.agentProjectPath]);

  useEffect(() => {
    if (
      previewStatus?.status !== "running" &&
      previewStatus?.status !== "starting"
    ) {
      return;
    }
    const t = setInterval(() => void loadPreview({ silent: true }), 5000);
    return () => clearInterval(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [previewStatus?.status, detail.id]);

  return (
    <>
      <p className="tab-tagline">
        打开生成出来的网站，必要时再审视 delivery-report 和运行证据。
      </p>

      <WebsitePreviewCard
        isRuntimeLinked={isRuntimeLinked}
        projectId={detail.id}
        runtimeProjectId={runtimeProjectId}
        runtimeProjectPath={runtimeProjectPath}
        previewStatus={previewStatus}
        previewStdout={previewStdout}
        previewStderr={previewStderr}
        previewBusy={previewBusy}
        previewError={previewError}
        onStartPreview={() => void startPreview()}
        onRestartPreview={() => void restartPreview()}
        onStopPreview={() => void stopPreview()}
        onRefreshPreview={() => void loadPreview()}
      />

      <section className="card">
        <div className="section-head">
          <h2 className="section-title">交付摘要</h2>
          <ProjectStatusPill status={status} size="sm" />
        </div>
        <dl className="summary-defs">
          <div>
            <dt>项目</dt>
            <dd>
              <strong>{detail.name}</strong>{" "}
              <code className="cell-code">{detail.id}</code>
            </dd>
          </div>
          <div>
            <dt>runtime project</dt>
            <dd>
              {isRuntimeLinked && runtimeProjectId && runtimeProjectPath ? (
                <>
                  <code className="cell-code">{runtimeProjectId}</code>{" "}
                  <code className="cell-code">{runtimeProjectPath}</code>
                </>
              ) : (
                <>
                  <span className="cell-muted">Runtime project is not linked yet.</span>{" "}
                  <Link
                    href={`/projects/${detail.id}?tab=develop`}
                    className="btn"
                    data-variant="ghost"
                    style={{ padding: "4px 10px", fontSize: 12 }}
                  >
                    Prepare Runtime Project
                  </Link>
                </>
              )}
            </dd>
          </div>
          <div>
            <dt>变更数量</dt>
            <dd>
              <span className="badge">{changes.length}</span>
              <span className="count-chip" data-variant="completed">
                <strong>{changes.filter((c) => c.state === "delivered").length}</strong>
                <span>已交付</span>
              </span>
              <span className="count-chip" data-variant="needs-review">
                <strong>{changes.filter((c) => c.state === "needs_human_review").length}</strong>
                <span>待审核</span>
              </span>
              <span className="count-chip" data-variant="failed">
                <strong>{changes.filter((c) => c.state === "failed").length}</strong>
                <span>失败</span>
              </span>
            </dd>
          </div>
          {sessionDir && (
            <div>
              <dt>session 文件</dt>
              <dd>
                <button
                  type="button"
                  className="btn"
                  data-variant="ghost"
                  onClick={() => onOpenArtifact(`${sessionDir}/autonomous-session.json`)}
                  style={{ padding: "4px 10px", fontSize: 12 }}
                >
                  autonomous-session.json
                </button>
                <details className="advanced-artifacts inline-advanced-artifacts">
                  <summary>Advanced artifacts</summary>
                  <div className="artifact-button-row" style={{ marginTop: "var(--sp-2)" }}>
                    <button
                      type="button"
                      className="btn"
                      data-variant="ghost"
                      onClick={() => onOpenArtifact(`${sessionDir}/final-run-status.md`)}
                      style={{ padding: "4px 10px", fontSize: 12 }}
                    >
                      final-run-status.md
                    </button>
                    {runDetail && (
                      <button
                        type="button"
                        className="btn"
                        data-variant="ghost"
                        onClick={() => onOpenArtifact(`${runDetail.relPath}/task-graph.json`)}
                        style={{ padding: "4px 10px", fontSize: 12 }}
                      >
                        task-graph.json
                      </button>
                    )}
                  </div>
                </details>
              </dd>
            </div>
          )}
        </dl>
      </section>

      <PrePRHandoffCard
        detail={detail}
        latestDeliveredChange={latestDeliveredChange}
        reviewOpen={runDetail?.reviewQueue.open ?? detail.reviewQueueOpen}
        reviewBlocking={runDetail?.reviewQueue.blocking ?? detail.reviewQueueBlocking}
      />

      <ProductReviewPanel detail={detail} onOpenArtifact={onOpenArtifact} />

      {changes.length === 0 ? (
        <div className="design-empty">
          {greenfieldComplete ? (
            <>
              <p>
                <strong>初次构建已完成。</strong>
              </p>
              <p style={{ marginTop: "var(--sp-2)" }}>
                这个区域只展示后续 change request 的交付证据。当前产品请用上方
                <strong> Open Website</strong> 打开预览，或查看 session 文件。
              </p>
            </>
          ) : (
            <>
              <p>
                <strong>这个项目还没有 change。</strong>
              </p>
              <p style={{ marginTop: "var(--sp-2)" }}>
                在<Link href={`/projects/${detail.id}?tab=discuss&mode=change`} style={{ color: "var(--color-info)", textDecoration: "underline" }}>讨论与锁定 / 变更请求</Link>
                提交一个 change，Studio 跑完后证据会出现在这里。
              </p>
            </>
          )}
        </div>
      ) : (
        <div className="evidence-card-stack">
          {changes.map((c) => (
            <DeliverChangeCard
              key={c.changeId}
              change={c}
              onOpenArtifact={onOpenArtifact}
            />
          ))}
        </div>
      )}

      <section className="card">
        <div className="section-head">
          <h2 className="section-title">下一步</h2>
        </div>
        <div style={{ display: "flex", gap: "var(--sp-2)", flexWrap: "wrap" }}>
          <Link
            href={`/projects/${detail.id}?tab=discuss&mode=change`}
            className="btn"
            data-variant="primary"
          >
            提交下一个变更请求 →
          </Link>
          <Link href="/evidence" className="btn" data-variant="ghost">
            完整证据中心 →
          </Link>
        </div>
      </section>
    </>
  );
}

function PrePRHandoffCard({
  detail,
  latestDeliveredChange,
  reviewOpen,
  reviewBlocking,
}: {
  detail: StudioProjectDetailClient;
  latestDeliveredChange: ChangeSummaryClient | null;
  reviewOpen: number;
  reviewBlocking: number;
}) {
  const [copied, setCopied] = useState(false);
  const filesTouched = latestDeliveredChange?.filesTouched ?? [];
  const validationSummary = latestDeliveredChange
    ? reviewBlocking > 0
      ? `Needs human review: ${reviewBlocking} blocking item(s).`
      : "Delivery evidence available; review queue has no blocking item."
    : detail.latestSessionStatus === "completed"
      ? "Greenfield session completed; no delivered change request yet."
      : "No delivered change evidence yet.";

  const handoffText = [
    `# ${detail.name} pre-PR handoff`,
    "",
    `Studio project: ${detail.id}`,
    `Runtime project: ${detail.agentProjectId ?? "not linked"}`,
    `Runtime path: ${detail.agentProjectPath ?? "not linked"}`,
    `Branch: ${latestDeliveredChange?.branch ?? "n/a"}`,
    `Latest commit: ${latestDeliveredChange?.sha ?? detail.latestDeliveredSha ?? "n/a"}`,
    `Delivered change: ${latestDeliveredChange?.changeId ?? "n/a"}`,
    "",
    "## Validation",
    validationSummary,
    `Review queue: ${reviewOpen} open / ${reviewBlocking} blocking`,
    "",
    "## Files touched",
    ...(filesTouched.length > 0 ? filesTouched.map((f) => `- ${f}`) : ["- n/a"]),
    "",
    "## Evidence",
    `Delivery report: ${latestDeliveredChange?.deliveryReportMd ?? "n/a"}`,
    `Applied change: ${latestDeliveredChange?.appliedChangeJson ?? "n/a"}`,
  ].join("\n");

  async function copyHandoff() {
    await navigator.clipboard.writeText(handoffText);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1600);
  }

  return (
    <section className="card pre-pr-handoff-card">
      <div className="section-head">
        <div>
          <h2 className="section-title">Pre-PR Handoff</h2>
          <p className="section-subtitle">
            给同事或 reviewer 的简短交接稿，不创建 GitHub PR、不推送远端。
          </p>
        </div>
        <button
          type="button"
          className="btn"
          data-variant="primary"
          onClick={() => void copyHandoff()}
        >
          {copied ? "Copied" : "Copy handoff summary"}
        </button>
      </div>
      <dl className="summary-defs">
        <div>
          <dt>project</dt>
          <dd>
            <strong>{detail.name}</strong>{" "}
            <code className="cell-code">{detail.id}</code>
          </dd>
        </div>
        <div>
          <dt>branch</dt>
          <dd>
            <code className="cell-code">{latestDeliveredChange?.branch ?? "n/a"}</code>
          </dd>
        </div>
        <div>
          <dt>latest commit</dt>
          <dd>
            <code className="cell-code">
              {latestDeliveredChange?.sha ?? detail.latestDeliveredSha ?? "n/a"}
            </code>
          </dd>
        </div>
        <div>
          <dt>delivered change</dt>
          <dd>
            <code className="cell-code">{latestDeliveredChange?.changeId ?? "n/a"}</code>
          </dd>
        </div>
        <div>
          <dt>files touched</dt>
          <dd>
            {filesTouched.length > 0 ? (
              <ul className="scope-list">
                {filesTouched.slice(0, 6).map((file) => (
                  <li key={file}>
                    <code>{file}</code>
                  </li>
                ))}
                {filesTouched.length > 6 && (
                  <li className="cell-muted">+{filesTouched.length - 6} more</li>
                )}
              </ul>
            ) : (
              <span className="cell-muted">n/a</span>
            )}
          </dd>
        </div>
        <div>
          <dt>validation</dt>
          <dd>{validationSummary}</dd>
        </div>
        <div>
          <dt>delivery-report.md</dt>
          <dd>
            <code className="cell-code">{latestDeliveredChange?.deliveryReportMd ?? "n/a"}</code>
          </dd>
        </div>
        <div>
          <dt>applied-change.json</dt>
          <dd>
            <code className="cell-code">{latestDeliveredChange?.appliedChangeJson ?? "n/a"}</code>
          </dd>
        </div>
      </dl>
    </section>
  );
}

function WebsitePreviewCard({
  isRuntimeLinked,
  projectId,
  runtimeProjectId,
  runtimeProjectPath,
  previewStatus,
  previewStdout,
  previewStderr,
  previewBusy,
  previewError,
  onStartPreview,
  onRestartPreview,
  onStopPreview,
  onRefreshPreview,
}: {
  isRuntimeLinked: boolean;
  projectId: string;
  runtimeProjectId: string | null;
  runtimeProjectPath: string | null;
  previewStatus: PreviewRunStatusClient | null;
  previewStdout: string;
  previewStderr: string;
  previewBusy: boolean;
  previewError: string | null;
  onStartPreview: () => void;
  onRestartPreview: () => void;
  onStopPreview: () => void;
  onRefreshPreview: () => void;
}) {
  const previewState = previewStatus?.status ?? "stopped";
  const isRunning = previewState === "running";
  const isStarting = previewState === "starting";
  const url = previewStatus?.url ?? null;

  return (
    <section className="card website-preview-card">
      <div className="section-head">
        <div>
          <h2 className="section-title">Generated Website</h2>
          <p className="section-subtitle">
            这里是最终产品的本地预览入口，不用去日志里找。
          </p>
        </div>
        <div className="preview-actions">
          {isRunning && url && (
            <a
              href={url}
              target="_blank"
              rel="noreferrer"
              className="btn"
              data-variant="primary"
            >
              Open Website →
            </a>
          )}
          {isRuntimeLinked && !isRunning && !isStarting && (
            <button
              type="button"
              className="btn"
              data-variant="primary"
              onClick={onStartPreview}
              disabled={previewBusy}
            >
              {previewBusy ? "Starting Preview…" : "Start Preview"}
            </button>
          )}
          {isStarting && (
            <button type="button" className="btn" data-variant="primary" disabled>
              Starting Preview…
            </button>
          )}
          {(isRunning || isStarting) && (
            <button
              type="button"
              className="btn"
              data-variant="ghost"
              onClick={onRestartPreview}
              disabled={previewBusy}
            >
              Restart Preview
            </button>
          )}
          {(isRunning || isStarting) && (
            <button
              type="button"
              className="btn"
              data-variant="ghost"
              onClick={onStopPreview}
              disabled={previewBusy}
            >
              Stop Preview
            </button>
          )}
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            onClick={onRefreshPreview}
            disabled={previewBusy}
          >
            Refresh
          </button>
        </div>
      </div>

      {!isRuntimeLinked ? (
        <div className="preview-empty">
          <strong>Runtime project is not linked yet.</strong>
          <Link href={`/projects/${projectId}?tab=develop`} className="btn" data-variant="ghost">
            Prepare Runtime Project
          </Link>
        </div>
      ) : (
        <dl className="summary-defs preview-summary">
          <div>
            <dt>preview</dt>
            <dd>
              <StatusBadge variant={previewStatusVariant(previewState)}>
                {translateSessionStatus(previewState)}
              </StatusBadge>
              {url && <code className="cell-code">{url}</code>}
            </dd>
          </div>
          {previewStatus?.startedAt && (
            <div>
              <dt>started</dt>
              <dd>
                <code className="cell-code">{previewStatus.startedAt}</code>
              </dd>
            </div>
          )}
          {previewStatus?.stoppedAt && (
            <div>
              <dt>stopped</dt>
              <dd>
                <code className="cell-code">{previewStatus.stoppedAt}</code>
              </dd>
            </div>
          )}
          <div>
            <dt>runtime project</dt>
            <dd>
              {runtimeProjectId && <code className="cell-code">{runtimeProjectId}</code>}
              {runtimeProjectPath && <code className="cell-code">{runtimeProjectPath}</code>}
            </dd>
          </div>
          {previewStatus?.pid && (
            <div>
              <dt>pid</dt>
              <dd>
                <code className="cell-code">{previewStatus.pid}</code>
              </dd>
            </div>
          )}
        </dl>
      )}

      {(previewError || previewStatus?.error) && (
        <p className="design-error" style={{ margin: "var(--sp-3) 0 0" }}>
          <strong>Preview：</strong> {previewError ?? previewStatus?.error}
        </p>
      )}

      {(previewStdout || previewStderr) && (
        <details className="advanced-artifacts preview-logs">
          <summary>Preview logs</summary>
          <div className="runtime-log-grid">
            <RuntimeLog title="stdout.log" text={previewStdout} />
            <RuntimeLog title="stderr.log" text={previewStderr} />
          </div>
        </details>
      )}
    </section>
  );
}

function DeliverChangeCard({
  change: c,
  onOpenArtifact,
}: {
  change: ChangeSummaryClient;
  onOpenArtifact: (p: string) => void;
}) {
  return (
    <section className="card evidence-card" data-state={c.state}>
      <div className="evidence-card-head">
        <div>
          <h3 className="evidence-card-title">
            <code>{c.changeId}</code>
          </h3>
          {c.goal && <p className="evidence-card-goal">{c.goal}</p>}
        </div>
        <div className="evidence-card-state">
          <StatusBadge variant={changeStateVariant(c.state)}>
            {translateChangeState(c.state)}
          </StatusBadge>
        </div>
      </div>
      <dl className="summary-defs evidence-card-meta">
        {c.branch && (
          <div>
            <dt>分支</dt>
            <dd>
              <code className="cell-code">{c.branch}</code>
            </dd>
          </div>
        )}
        {c.sha && (
          <div>
            <dt>commit</dt>
            <dd>
              <code className="cell-code">{c.sha.slice(0, 12)}</code>
            </dd>
          </div>
        )}
        {c.appliedAt && (
          <div>
            <dt>应用时间</dt>
            <dd>
              <code className="cell-code">{c.appliedAt}</code>
            </dd>
          </div>
        )}
        {c.filesTouched.length > 0 && (
          <div>
            <dt>变更文件 ({c.filesTouched.length})</dt>
            <dd>
              <ul className="scope-list">
                {c.filesTouched.slice(0, 5).map((f) => (
                  <li key={f}>
                    <code>{f}</code>
                  </li>
                ))}
                {c.filesTouched.length > 5 && (
                  <li className="cell-muted">+{c.filesTouched.length - 5} 个</li>
                )}
              </ul>
            </dd>
          </div>
        )}
      </dl>

      <div className="artifact-button-row">
        {c.deliveryReportMd ? (
          <button
            type="button"
            className="btn"
            data-variant="primary"
            onClick={() => onOpenArtifact(c.deliveryReportMd!)}
          >
            查看 delivery-report.md
          </button>
        ) : (
          <button type="button" className="btn" data-variant="ghost" disabled>
            delivery-report.md <span className="evidence-missing-tag">缺失</span>
          </button>
        )}
        {c.appliedChangeJson ? (
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            onClick={() => onOpenArtifact(c.appliedChangeJson!)}
          >
            查看 applied-change.json
          </button>
        ) : (
          <button type="button" className="btn" data-variant="ghost" disabled>
            applied-change.json <span className="evidence-missing-tag">缺失</span>
          </button>
        )}
      </div>

      <details className="advanced-artifacts">
        <summary>Advanced artifacts（promotion / eval / changed-files / repair-history）</summary>
        <div className="artifact-button-row" style={{ marginTop: "var(--sp-2)" }}>
          {[
            { label: "promotion-report.json", path: c.promotionReportPath },
            { label: "eval-results.json", path: c.evalResultsPath },
            { label: "changed-files.json", path: c.changedFilesPath },
            { label: "repair-history.json", path: c.repairHistoryPath },
          ].map((a) =>
            a.path ? (
              <button
                key={a.label}
                type="button"
                className="btn"
                data-variant="ghost"
                onClick={() => onOpenArtifact(a.path!)}
                style={{ fontSize: 12, padding: "4px 8px" }}
              >
                {a.label}
              </button>
            ) : (
              <button
                key={a.label}
                type="button"
                className="btn"
                data-variant="ghost"
                disabled
                style={{ fontSize: 12, padding: "4px 8px" }}
              >
                {a.label} <span className="evidence-missing-tag">缺失</span>
              </button>
            ),
          )}
        </div>
      </details>
    </section>
  );
}

// ===========================================================================
// 工具
// ===========================================================================

function CountChip({
  variant,
  label,
  value,
}: {
  variant: "completed" | "running" | "needs-review" | "failed" | "default";
  label: string;
  value: number;
}) {
  return (
    <span className="count-chip" data-variant={variant}>
      <strong>{value}</strong>
      <span>{label}</span>
    </span>
  );
}

function asNum(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function truncate(s: string, n: number): string {
  if (s.length <= n) return s;
  return s.slice(0, n - 1).trimEnd() + "…";
}

function statusVariant(
  status: string | null | undefined,
): "completed" | "running" | "pending" | "needs-review" | "failed" | "default" {
  switch (status) {
    case "completed":
    case "delivered":
      return "completed";
    case "running":
    case "in_progress":
    case "starting":
      return "running";
    case "queued":
    case "pending":
    case "skipped":
      return "pending";
    case "paused":
    case "needs_human":
    case "needs-human-review":
    case "needs_human_review":
      return "needs-review";
    case "failed":
    case "abandoned":
    case "stopped":
      return "failed";
    default:
      return "default";
  }
}

function previewStatusVariant(
  status: PreviewRunState,
): "completed" | "running" | "pending" | "needs-review" | "failed" | "default" {
  switch (status) {
    case "running":
      return "running";
    case "starting":
      return "pending";
    case "failed":
      return "failed";
    case "stopped":
      return "default";
    default:
      return "default";
  }
}

function translateSessionStatus(s: string | null | undefined): string {
  switch (s) {
    case "queued":
      return "排队中";
    case "starting":
      return "启动中";
    case "pending":
      return "待运行";
    case "idle":
      return "未启动";
    case "completed":
      return "已完成";
    case "running":
      return "运行中";
    case "skipped":
      return "已跳过";
    case "paused":
      return "已暂停";
    case "needs_human_review":
    case "needs_human":
    case "needs-human-review":
      return "待人工";
    case "stopped":
      return "已停止";
    case "failed":
      return "失败";
    case "abandoned":
      return "放弃";
    default:
      return s ?? "未知";
  }
}

function taskStatusVariant(
  s: string | null | undefined,
): "completed" | "running" | "needs-review" | "failed" | "default" {
  switch (s) {
    case "completed":
      return "completed";
    case "running":
    case "in_progress":
      return "running";
    case "needs_human_review":
    case "needs-human-review":
      return "needs-review";
    case "failed":
    case "abandoned":
      return "failed";
    default:
      return "default";
  }
}

function translateTaskStatus(s: string | null | undefined): string {
  switch (s) {
    case "completed":
      return "已完成";
    case "running":
    case "in_progress":
      return "跑动中";
    case "needs_human_review":
    case "needs-human-review":
      return "待审核";
    case "failed":
      return "失败";
    case "abandoned":
      return "放弃";
    case "pending":
    default:
      return "待跑";
  }
}

function changeStateVariant(
  s: ChangeSummaryClient["state"],
): "completed" | "running" | "needs-review" | "failed" | "default" {
  switch (s) {
    case "delivered":
      return "completed";
    case "applied":
      return "running";
    case "needs_human_review":
      return "needs-review";
    case "failed":
      return "failed";
    case "ready_for_run":
      return "default";
    default:
      return "default";
  }
}

function translateChangeState(s: ChangeSummaryClient["state"]): string {
  switch (s) {
    case "delivered":
      return "已交付";
    case "applied":
      return "已应用";
    case "needs_human_review":
      return "待审核";
    case "failed":
      return "失败";
    case "ready_for_run":
      return "待跑";
    default:
      return s;
  }
}

function changeRunStateVariant(
  s: string,
): "completed" | "running" | "needs-review" | "failed" | "default" {
  switch (s) {
    case "completed":
      return "completed";
    case "queued":
    case "starting":
    case "running":
    case "repairing":
    case "stopping":
      return "running";
    case "failed":
      return "failed";
    case "needs_human":
    case "stopped":
      return "needs-review";
    default:
      return "default";
  }
}

function productReviewVariant(
  s: ProductReviewResultClient["verdict"],
): "completed" | "running" | "warning" | "needs-review" | "failed" | "default" {
  switch (s) {
    case "pass":
      return "completed";
    case "pass_with_recommendations":
      return "warning";
    case "needs_work":
    case "needs_change_plan":
      return "needs-review";
    case "unsafe":
    case "blocked":
      return "failed";
    default:
      return "default";
  }
}

function findingSeverityVariant(
  s: string,
): "completed" | "warning" | "needs-review" | "failed" | "default" {
  switch (s) {
    case "critical":
      return "failed";
    case "high":
      return "needs-review";
    case "medium":
      return "warning";
    case "low":
      return "default";
    default:
      return "default";
  }
}

function translateChangeRunState(s: string): string {
  switch (s) {
    case "queued":
      return "排队中";
    case "starting":
      return "启动中";
    case "running":
      return "运行中";
    case "repairing":
      return "修复中";
    case "stopping":
      return "停止中";
    case "completed":
      return "已完成";
    case "needs_human":
      return "待人工";
    case "failed":
      return "失败";
    case "stopped":
      return "已停止";
    default:
      return s;
  }
}

function diagnosisSeverityVariant(
  s: string,
): "completed" | "warning" | "needs-review" | "failed" | "default" {
  switch (s) {
    case "info":
      return "default";
    case "warning":
      return "warning";
    case "error":
      return "failed";
    default:
      return "default";
  }
}

function severityVariant(
  s: string,
): "completed" | "warning" | "needs-review" | "failed" | "default" {
  switch (s) {
    case "blocking":
      return "failed";
    case "warning":
      return "needs-review";
    default:
      return "default";
  }
}
