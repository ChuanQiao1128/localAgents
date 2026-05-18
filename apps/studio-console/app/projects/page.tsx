"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import ProjectStatusPill from "@/components/ProjectStatusPill";
import { deriveStudioProjectStatus } from "@/lib/projectStatus";
import type {
  CreateStudioProjectResponse,
  ListStudioProjectsResponse,
  StudioProjectSummaryClient,
} from "@/lib/types";

/**
 * 项目列表页 —— RC-5A.12.1 起，只显示 .studio-console/projects/。
 * 旧 .agent-studio/projects/ 不再作为「主项目」展示（运行后自动关联）。
 */

export default function StudioProjectsListPage() {
  const router = useRouter();
  const [projects, setProjects] = useState<StudioProjectSummaryClient[]>([]);
  const [loading, setLoading] = useState(true);
  const [pageError, setPageError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");

  async function load() {
    setLoading(true);
    setPageError(null);
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
      setPageError(String(exc));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  async function handleCreate() {
    const name = newName.trim();
    if (name.length === 0) {
      setPageError("项目名不能为空");
      return;
    }
    setCreating(true);
    setPageError(null);
    try {
      const res = await fetch("/api/studio-projects", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name }),
      });
      const body = (await res.json()) as
        | CreateStudioProjectResponse
        | { error: string };
      if (!res.ok || "error" in body) {
        throw new Error("error" in body ? body.error : `HTTP ${res.status}`);
      }
      setShowNew(false);
      setNewName("");
      // 直接跳到新工作台 —— 让用户立刻进入"讨论与锁定"。
      router.push(`/projects/${body.id}`);
    } catch (exc) {
      setPageError(String(exc));
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      <h1 className="page-title">项目 · Projects</h1>
      <p className="page-subtitle">
        每个项目独占一个工作台。Console 不会自动执行 <code>agent-studio</code>，
        所有 CLI 命令都需要你粘到终端跑。
      </p>

      {pageError && (
        <div className="design-error">
          <strong>错误：</strong> {pageError}
        </div>
      )}

      <section className="card">
        <div className="section-head">
          <h2 className="section-title">所有项目</h2>
          <div style={{ display: "flex", gap: "var(--sp-2)" }}>
            <button
              type="button"
              className="btn"
              data-variant="ghost"
              onClick={() => void load()}
              disabled={loading}
            >
              {loading ? "刷新中…" : "刷新"}
            </button>
            <button
              type="button"
              className="btn"
              data-variant="primary"
              onClick={() => setShowNew(true)}
              disabled={creating}
            >
              + 新建项目
            </button>
          </div>
        </div>

        {showNew && (
          <div className="new-project-form">
            <label className="change-field">
              <span>项目名</span>
              <input
                type="text"
                className="oq-panel-input"
                value={newName}
                onChange={(e) => setNewName(e.target.value)}
                placeholder="例如：AI Writing Naturalizer"
                disabled={creating}
                autoFocus
                onKeyDown={(e) => {
                  if (e.key === "Enter" && newName.trim()) {
                    e.preventDefault();
                    void handleCreate();
                  }
                }}
              />
              <small className="cell-muted">
                项目名可以是中文或英文。系统会基于它生成 id（出现在文件路径里）。
              </small>
            </label>
            <div style={{ display: "flex", gap: "var(--sp-2)", marginTop: "var(--sp-3)" }}>
              <button
                type="button"
                className="btn"
                data-variant="primary"
                onClick={() => void handleCreate()}
                disabled={creating || !newName.trim()}
              >
                {creating ? "创建中…" : "创建并进入工作台"}
              </button>
              <button
                type="button"
                className="btn"
                data-variant="ghost"
                onClick={() => {
                  setShowNew(false);
                  setNewName("");
                }}
                disabled={creating}
              >
                取消
              </button>
            </div>
          </div>
        )}

        {!loading && projects.length === 0 && !showNew && (
          <div className="cold-clone-empty" style={{ marginTop: "var(--sp-3)" }}>
            <p className="cold-clone-headline">还没有项目。</p>
            <p className="cold-clone-body">
              点击上面的 <em>+ 新建项目</em> 创建第一个 ——
              你会进入<strong>讨论与锁定</strong>页面，开始写需求合同。
            </p>
          </div>
        )}

        {projects.length > 0 && (
          <table className="data-table">
            <thead>
              <tr>
                <th>项目</th>
                <th>状态</th>
                <th>合同</th>
                <th>任务 / 变更</th>
                <th>更新时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {projects.map((p) => {
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
                      {p.contract.locked ? (
                        <span className="badge" data-variant="locked">
                          已锁定
                        </span>
                      ) : p.contract.canLock ? (
                        <span className="badge" data-variant="completed">
                          可锁定
                        </span>
                      ) : (
                        <span className="badge" data-variant="warning">
                          草稿
                        </span>
                      )}
                      {p.contract.unresolvedQuestions > 0 && (
                        <span
                          className="cell-muted"
                          style={{ fontSize: 11, marginLeft: 6 }}
                        >
                          {p.contract.unresolvedQuestions} 待答
                        </span>
                      )}
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
                    <td className="cell-muted" style={{ fontSize: 11 }}>
                      {formatDate(p.meta.updatedAt)}
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

      <p className="cell-muted" style={{ fontSize: 12, marginTop: "var(--sp-4)" }}>
        提示：旧版 6 页（需求合同 / 开发前检查 / 运行监控 / 人工审核 /
        交付证据 / 变更请求）已经从主导航移除，但路径仍然存在
        （<code>/design</code>、<code>/plan</code> 等）作为「专家深度调试」入口。
      </p>
    </>
  );
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleString();
  } catch {
    return iso;
  }
}
