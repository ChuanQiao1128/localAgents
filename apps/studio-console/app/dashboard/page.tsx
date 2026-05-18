"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import SummaryCard from "@/components/SummaryCard";
import ArtifactViewerModal from "@/components/ArtifactViewerModal";
import ProjectStatusPill from "@/components/ProjectStatusPill";
import { deriveStudioProjectStatus } from "@/lib/projectStatus";
import type {
  ListStudioProjectsResponse,
  StudioProjectSummaryClient,
} from "@/lib/types";

/**
 * Studio Console Dashboard.
 *
 * Three sections:
 *   1. Hero — positioning lines + the "model is not the system" tagline.
 *   2. Demo matrix — RC-4C 3/3 green table, hardcoded from EVALUATION.md
 *      (the matrix is a fixed historical fact; verify-source links open
 *      the committed docs in the artifact viewer modal).
 *   3. Project discovery — live read of <root>/.agent-studio/projects/ via
 *      /api/projects. Cold-clone case shows the "Load demo seeds" command
 *      preview (Preview mode only — operator copies + runs in terminal).
 *   4. Reading list — link cards into the committed evidence + interview docs.
 */

// ---------------------------------------------------------------------------
// Hardcoded RC-4C matrix evidence — facts, not config. If RC-4C results
// ever change, update this constant + the corresponding doc together.
// ---------------------------------------------------------------------------

type MatrixRow = {
  demo: string;
  vertical: string;
  greenfieldCommits: number;
  changeArchetype: string;
  changeCommit: string;
  changeBranch: string;
  filesTouched: string[];
  build: "passed" | "failed";
  validate: "ok" | "fail";
  reviewQueue: number;
};

const MATRIX: readonly MatrixRow[] = [
  {
    demo: "ai-writing-quality-editor",
    vertical: "AI text-quality product",
    greenfieldCommits: 3,
    changeArchetype: "Add deterministic clarity score 0-100",
    changeCommit: "15864c9",
    changeBranch: "agentic/change/change_c19add9a71",
    filesTouched: ["app/page.tsx", "components/analyzer.ts"],
    build: "passed",
    validate: "ok",
    reviewQueue: 0,
  },
  {
    demo: "ai-usage-cost-planner",
    vertical: "AI SaaS cost / token-budget logic",
    greenfieldCommits: 3,
    changeArchetype: "Add budget warning + break-even comparison",
    changeCommit: "1f15c39",
    changeBranch: "agentic/change/change_9bdae25130",
    filesTouched: ["app/page.tsx"],
    build: "passed",
    validate: "ok",
    reviewQueue: 0,
  },
  {
    demo: "agent-review-queue-console",
    vertical: "Agent workflow / human-in-the-loop governance",
    greenfieldCommits: 3,
    changeArchetype: "Add SLA risk badges (urgent / review-soon)",
    changeCommit: "63979f5",
    changeBranch: "agentic/change/change_e8525afae2",
    filesTouched: ["app/page.tsx", "components/reviews.ts"],
    build: "passed",
    validate: "ok",
    reviewQueue: 0,
  },
];

const READING_LIST: readonly { path: string; label: string; description: string }[] = [
  { path: "docs/EVALUATION.md", label: "EVALUATION.md", description: "Matrix-level summary (3/3 green, RC-4C.1 fixes, limitations)" },
  { path: "docs/rc5a-mini-release-notes-e2e-report.md", label: "RC-5A Mini Release E2E", description: "Greenfield + change request + real bug fix dogfood evidence" },
  { path: "docs/rc5b-naturalizer-generation-report.md", label: "RC-5B Naturalizer report", description: "Naturalizer generation, preview, and current provider limitations" },
  { path: "docs/STUDIO_DEMO_SCRIPT.md", label: "STUDIO_DEMO_SCRIPT.md", description: "5-minute demo path and 15-minute technical deep dive" },
  { path: "docs/rc4c-demo-suite-report.md", label: "rc4c-demo-suite-report.md", description: "Per-demo run-by-run details (commits, runs, timings)" },
  { path: "docs/ARCHITECTURE.md", label: "ARCHITECTURE.md", description: "Top-level architecture walkthrough" },
  { path: "docs/INTERVIEW_STORY.md", label: "INTERVIEW_STORY.md", description: "30s / 1min / 5min narration scripts + Q&A" },
  { path: "docs/RESUME_BULLETS.md", label: "RESUME_BULLETS.md", description: "3-bullet + 5-bullet variants, AI-Engineer / Full-stack-AI" },
  { path: "docs/PROJECT_STATUS.md", label: "PROJECT_STATUS.md", description: "Completed RC milestones + next steps" },
  { path: "docs/interview/01-project-summary.md", label: "interview / 01 — project summary", description: "Plain-English summary + glossary" },
  { path: "docs/interview/02-architecture-walkthrough.md", label: "interview / 02 — architecture walkthrough", description: "Component-by-component with ASCII diagrams + data flow trace" },
  { path: "docs/interview/03-failure-cases.md", label: "interview / 03 — failure cases", description: "8 real failure modes + symptom / cause / fix / test" },
  { path: "docs/interview/04-demo-matrix-story.md", label: "interview / 04 — demo matrix story", description: "Narrative for the 5 / 10 / 30 minute demo" },
];

// （旧 LOAD_DEMO_SEEDS_COMMAND 已废弃 —— 详见上方 Helpers 注释。）

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function DashboardPage() {
  const [projects, setProjects] = useState<
    StudioProjectSummaryClient[] | null
  >(null);
  const [projectsError, setProjectsError] = useState<string | null>(null);
  const [projectsLoading, setProjectsLoading] = useState(true);

  const [openArtifact, setOpenArtifact] = useState<string | null>(null);

  async function loadProjects() {
    setProjectsLoading(true);
    setProjectsError(null);
    try {
      const res = await fetch("/api/studio-projects", { cache: "no-store" });
      const body = (await res.json()) as
        | ListStudioProjectsResponse
        | { error: string };
      if (!res.ok || "error" in body) {
        throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
      }
      setProjects(body.projects);
    } catch (exc) {
      setProjectsError(String(exc));
      setProjects([]);
    } finally {
      setProjectsLoading(false);
    }
  }

  useEffect(() => {
    void loadProjects();
  }, []);

  return (
    <>
      {/* ---------- Hero ---------- */}
      <section className="hero">
        <h1 className="hero-title">Local Agent Studio 本地控制台</h1>
        <p className="hero-tagline">
          Codex 负责写补丁；Studio 是它周围的交付运行时（delivery runtime）。
        </p>
        <blockquote className="hero-blockquote">
          模型不是系统。交付闭环才是系统。
        </blockquote>
      </section>

      {/* ---------- Interview demo path ---------- */}
      <section className="card demo-path-card">
        <div className="section-head">
          <div>
            <h2 className="section-title">Interview Demo Path</h2>
            <p className="section-subtitle">
              面试演示按这条路径走，先展示完整交付闭环，再解释为什么 Studio
              不是另一个聊天窗口。
            </p>
          </div>
          <span className="badge" data-variant="completed">5 steps</span>
        </div>
        <ol className="demo-path-list">
          <li>打开 Mini Release Notes dogfood 工作台</li>
          <li>展示 Generated Website / Open Website</li>
          <li>展示 bug-fix Change Request 证据</li>
          <li>打开 AI Writing Naturalizer 生成网站</li>
          <li>解释 Studio gates、delivery report 和 pre-PR handoff</li>
        </ol>
        <div className="demo-path-actions">
          <Link href="/projects/mini-release-notes-builder" className="btn" data-variant="primary">
            Open Mini Release workspace
          </Link>
          <Link href="/projects/ai-writing-naturalizer" className="btn" data-variant="primary">
            Open Naturalizer workspace
          </Link>
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            onClick={() => setOpenArtifact("docs/rc5a-mini-release-notes-e2e-report.md")}
          >
            Open Mini Release E2E report
          </button>
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            onClick={() => setOpenArtifact("docs/rc5b-naturalizer-generation-report.md")}
          >
            Open Naturalizer generation report
          </button>
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            onClick={() => setOpenArtifact("docs/STUDIO_DEMO_SCRIPT.md")}
          >
            Open demo script
          </button>
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            onClick={() => setOpenArtifact("docs/RESUME_BULLETS.md")}
          >
            Open resume bullets
          </button>
        </div>
      </section>

      {/* ---------- Workplace value ---------- */}
      <section className="card workplace-value-card">
        <div className="section-head">
          <h2 className="section-title">Workplace value</h2>
          <span className="badge">pre-PR</span>
        </div>
        <p className="section-subtitle">
          Codex writes candidate patches. Studio decides whether they are safe to apply.
        </p>
        <ul className="workplace-value-list">
          <li>Scoped change request：每次变更先有范围、非目标和验收标准。</li>
          <li>Deterministic build/typecheck gates：通过可执行信号再交付。</li>
          <li>Human review when blocked：卡住时进入人工审查，不伪装成功。</li>
          <li>Delivery report for pre-PR handoff：把实现、验证和剩余风险整理成交接材料。</li>
        </ul>
        <p className="section-subtitle" style={{ marginBottom: 0 }}>
          For tiny edits, direct Codex is faster. Studio optimizes for controlled delivery, not raw typing speed.
        </p>
      </section>

      {/* ---------- 如何使用本控制台（精简到 3 步对应 3 个 tab）---------- */}
      <section className="how-to-use-card">
        <div className="section-head">
          <h2 className="section-title">如何使用本控制台</h2>
          <span className="cell-muted" style={{ fontSize: 12 }}>
            一个项目，3 个屏幕
          </span>
        </div>
        <p className="section-subtitle">
          所有日常操作都在<strong>项目工作台</strong>里完成。打开任意项目后，
          顶部三个 tab 对应交付循环的三个阶段。Console 只读写本地文件 ——
          <code>agent-studio</code> 命令需要你粘到终端跑。
        </p>
        <ol className="how-to-use-list">
          <li>
            <strong>讨论与锁定</strong>
            <p>
              和需求合同一来一回（产品定位 / MVP 范围 / 验收标准 / 待办问题），
              确认无误后 Lock。或在"变更请求"模式下给已有项目提改动。
            </p>
          </li>
          <li>
            <strong>开发中</strong>
            <p>
              复制生成的 <code>agent-studio</code> 命令到终端跑，回到这个 tab
              看任务进度。Studio 卡住时，顶部会自动出现"需要处理"区。
            </p>
          </li>
          <li>
            <strong>交付结果</strong>
            <p>
              查看每次 change 的 delivery-report / applied-change，以及
              promotion-report / eval-results / changed-files / repair-history
              这些证据。完成后回到"讨论与锁定 / 变更请求"提交下一个迭代。
            </p>
          </li>
        </ol>
        <div style={{ marginTop: "var(--sp-3)", display: "flex", gap: "var(--sp-2)" }}>
          <Link href="/projects" className="btn" data-variant="primary">
            打开项目列表 →
          </Link>
        </div>
      </section>

      {/* ---------- Summary cards ---------- */}
      <section className="summary-grid">
        <SummaryCard value="3 / 3" label="Demo 全绿" hint="RC-4C 矩阵" variant="success" />
        <SummaryCard value="12" label="真实 Codex commit" hint="9 个 greenfield + 3 个 change-mode" variant="info" />
        <SummaryCard value="3" label="覆盖垂类" hint="文本质量 · 成本规划 · 审核队列" />
        <SummaryCard value="0" label="待审核条目" hint="每次变更都干净通过 gate" variant="success" />
      </section>

      {/* ---------- Demo matrix ---------- */}
      <section className="card matrix-section">
        <div className="section-head">
          <h2 className="section-title">RC-4C Demo 矩阵</h2>
          <span className="badge" data-variant="completed">
            3 / 3 全绿
          </span>
        </div>
        <p className="section-subtitle">
          三个不同的 Next.js + TypeScript 垂类，同一套编排。同一份
          deterministic decomposer、同一份 12 条 Promotion Gate、同一份
          10 条 Apply Gate。下方文档可验证矩阵级证据。
        </p>

        <div className="matrix-table-wrap">
          <table className="data-table">
            <thead>
              <tr>
                <th>Demo</th>
                <th>垂类</th>
                <th>Greenfield</th>
                <th>变更请求</th>
                <th>Change commit</th>
                <th>Build</th>
                <th>Validate</th>
                <th>Reviews</th>
              </tr>
            </thead>
            <tbody>
              {MATRIX.map((row) => (
                <tr key={row.demo}>
                  <td>
                    <code className="cell-code">{row.demo}</code>
                  </td>
                  <td className="cell-muted">{row.vertical}</td>
                  <td>
                    <span className="badge" data-variant="completed">
                      {row.greenfieldCommits} commits
                    </span>
                  </td>
                  <td className="cell-muted">{row.changeArchetype}</td>
                  <td>
                    <code className="cell-code">{row.changeCommit}</code>
                  </td>
                  <td>
                    <span className="badge" data-variant={row.build === "passed" ? "completed" : "failed"}>
                      {row.build}
                    </span>
                  </td>
                  <td>
                    <span className="badge" data-variant={row.validate === "ok" ? "completed" : "failed"}>
                      {row.validate}
                    </span>
                  </td>
                  <td>
                    <span className="badge">{row.reviewQueue}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        <div className="matrix-actions">
          <button
            type="button"
            className="btn"
            data-variant="primary"
            onClick={() => setOpenArtifact("docs/EVALUATION.md")}
          >
            打开 EVALUATION.md
          </button>
          <button
            type="button"
            className="btn"
            data-variant="ghost"
            onClick={() => setOpenArtifact("docs/rc4c-demo-suite-report.md")}
          >
            打开 rc4c-demo-suite-report.md
          </button>
        </div>
      </section>

      {/* ---------- Studio Projects ---------- */}
      <section className="card discovery-section">
        <div className="section-head">
          <h2 className="section-title">我的项目</h2>
          <div style={{ display: "flex", gap: "var(--sp-2)" }}>
            <button
              type="button"
              className="btn"
              data-variant="ghost"
              onClick={() => void loadProjects()}
              disabled={projectsLoading}
            >
              {projectsLoading ? "刷新中…" : "刷新"}
            </button>
            <Link
              href="/projects"
              className="btn"
              data-variant="primary"
            >
              打开项目列表 →
            </Link>
          </div>
        </div>

        {projectsError && (
          <div className="discovery-error">
            <strong>读取项目失败：</strong> {projectsError}
          </div>
        )}

        {!projectsLoading && (projects?.length ?? 0) === 0 && !projectsError && (
          <div className="cold-clone-empty" style={{ marginTop: "var(--sp-3)" }}>
            <p className="cold-clone-headline">还没有项目。</p>
            <p className="cold-clone-body">
              到{" "}
              <Link
                href="/projects"
                style={{ color: "var(--color-info)", textDecoration: "underline" }}
              >
                项目列表 → + 新建项目
              </Link>{" "}
              创建第一个 —— 你会进入<strong>讨论与锁定</strong>页面，
              开始写需求合同。
            </p>
          </div>
        )}

        {(projects?.length ?? 0) > 0 && (
          <table className="data-table">
            <thead>
              <tr>
                <th>项目</th>
                <th>状态</th>
                <th>任务 / 变更</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {projects!.slice(0, 10).map((p) => {
                const status = deriveStudioProjectStatus(p);
                return (
                  <tr key={p.id}>
                    <td>
                      <strong>{p.name}</strong>
                      <div className="cell-muted" style={{ fontSize: 11, marginTop: 2 }}>
                        <code>{p.id}</code>
                      </div>
                    </td>
                    <td>
                      <ProjectStatusPill status={status} size="sm" />
                    </td>
                    <td>
                      <span
                        className="badge"
                        data-variant={
                          p.completedCount === p.taskCount && p.taskCount > 0
                            ? "completed"
                            : "default"
                        }
                      >
                        {p.completedCount} / {p.taskCount}
                      </span>{" "}
                      <span className="badge">{p.changeCount} change</span>
                    </td>
                    <td>
                      <Link
                        href={`/projects/${p.id}`}
                        className="btn"
                        data-variant="primary"
                        style={{ padding: "4px 10px", fontSize: 12 }}
                      >
                        打开 →
                      </Link>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </section>

      {/* ---------- Reading list ---------- */}
      <section className="card reading-section">
        <div className="section-head">
          <h2 className="section-title">证据与文档阅读</h2>
        </div>
        <p className="section-subtitle">
          下方每个链接都在弹层中打开本地仓库内的文件，使用与 Console
          其他读取相同的路径白名单。
        </p>
        <ul className="reading-list">
          {READING_LIST.map((item) => (
            <li key={item.path}>
              <button
                type="button"
                className="reading-link"
                onClick={() => setOpenArtifact(item.path)}
              >
                <span className="reading-link-label">{item.label}</span>
                <span className="reading-link-desc">{item.description}</span>
              </button>
            </li>
          ))}
        </ul>
      </section>

      {/* ---------- Modal ---------- */}
      <ArtifactViewerModal
        open={openArtifact !== null}
        path={openArtifact}
        onClose={() => setOpenArtifact(null)}
      />
    </>
  );
}

// （旧的 ColdCloneEmptyState / statusVariant / LOAD_DEMO_SEEDS_COMMAND 已废弃 —
//  RC-5A.12.1 起不再扫描 .agent-studio/projects；冷克隆经验从「Load demo seeds」
//  命令转为「在 /projects 页面 + 新建项目」按钮。）
