"use client";

/**
 * RC-5K Phase 1 —— ProjectFlowDag.
 *
 * Renders the conceptual project delivery flow. Two modes:
 *
 *   - Compact (default): a 3-node strip showing previous-done · current ·
 *     next-available. Optimized for "where am I, what do I click next?".
 *   - Full (toggle): all 13 nodes in flow order, with the conceptual
 *     return edge (updated_delivery → product_review) rendered dashed
 *     and labeled "下一轮评审".
 *
 * Plain React + CSS. No external libs. Nodes navigate via <a> tags when an
 * href is computable for the current node.
 */

import { useMemo, useState } from "react";
import Link from "next/link";
import {
  compactWindow,
  type FlowEdge,
  type FlowNode,
  type FlowNodeId,
  type FlowNodeStatus,
} from "@/lib/projectFlow";

const STATUS_LABEL_ZH: Record<FlowNodeStatus, string> = {
  done: "已完成",
  current: "当前",
  available: "可执行",
  blocked: "需要处理",
  pending: "未到",
  optional: "可选分支",
};

const STATUS_ICON: Record<FlowNodeStatus, string> = {
  done: "✓",
  current: "●",
  available: "→",
  blocked: "!",
  pending: "·",
  optional: "·",
};

/** Build a navigation href for a node. Some nodes need extra query (?mode=change). */
function nodeHref(projectId: string, node: FlowNode): string {
  // recommended_changes / change_request_running both surface inside
  // Develop tab — the existing ProductReviewPanel already lists generated
  // CR drafts there with their Run actions.
  if (
    node.id === "recommended_changes" ||
    node.id === "change_request_running"
  ) {
    return `/projects/${encodeURIComponent(projectId)}?tab=develop`;
  }
  return `/projects/${encodeURIComponent(projectId)}?tab=${node.tab}`;
}

export default function ProjectFlowDag({
  projectId,
  nodes,
  edges,
}: {
  projectId: string;
  nodes: FlowNode[];
  edges: FlowEdge[];
}) {
  const [expanded, setExpanded] = useState(false);

  const compactNodes = useMemo(() => compactWindow(nodes), [nodes]);

  // Active focus = first current/blocked, else last done.
  const focus = useMemo(() => {
    const c = nodes.find(
      (n) => n.status === "current" || n.status === "blocked",
    );
    if (c) return c;
    let lastDone: FlowNode | null = null;
    for (const n of nodes) if (n.status === "done") lastDone = n;
    return lastDone;
  }, [nodes]);

  return (
    <section className="project-flow-shell">
      <div className="flow-shell-head">
        <div>
          <h2 className="flow-shell-title">项目流程</h2>
          <p className="flow-shell-sub">
            {expanded
              ? "完整交付流程 · Discuss → Lock → Runtime → Develop → Deliver → Product Review → 下一轮"
              : "当前阶段与下一步"}
          </p>
        </div>
        <div className="flow-shell-actions">
          {focus && !expanded && (
            <span className="cell-muted" style={{ fontSize: 12 }}>
              当前焦点:<strong>{focus.labelZh}</strong>
            </span>
          )}
          <button
            type="button"
            className="btn flow-expand-button"
            data-variant="ghost"
            onClick={() => setExpanded((v) => !v)}
            aria-expanded={expanded}
          >
            {expanded ? "收起" : "展开完整流程"}
          </button>
        </div>
      </div>

      {expanded ? (
        <FlowDagFull projectId={projectId} nodes={nodes} edges={edges} />
      ) : (
        <FlowDagCompact projectId={projectId} nodes={compactNodes} />
      )}
    </section>
  );
}

function FlowDagCompact({
  projectId,
  nodes,
}: {
  projectId: string;
  nodes: FlowNode[];
}) {
  return (
    <ol className="flow-dag compact">
      {nodes.map((node, idx) => (
        <li key={node.id} className="flow-step">
          <FlowNodeCard projectId={projectId} node={node} />
          {idx < nodes.length - 1 && (
            <span className="flow-edge" aria-hidden>
              →
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

function FlowDagFull({
  projectId,
  nodes,
  edges,
}: {
  projectId: string;
  nodes: FlowNode[];
  edges: FlowEdge[];
}) {
  // Map nodes by id for quick lookup when rendering edges/branches.
  const byId = useMemo(() => {
    const m = new Map<FlowNodeId, FlowNode>();
    for (const n of nodes) m.set(n.id, n);
    return m;
  }, [nodes]);

  // Mainline (linear) nodes in flow order, excluding `needs_human` which is a
  // side branch off `development_running`.
  const mainline: FlowNodeId[] = [
    "project_created",
    "requirements_discussed",
    "contract_locked",
    "runtime_prepared",
    "development_started",
    "development_running",
    "development_completed",
    "delivered",
    "product_review",
    "recommended_changes",
    "change_request_running",
    "updated_delivery",
  ];

  const needsHumanNode = byId.get("needs_human");
  const backEdge = edges.find((e) => e.back);

  return (
    <div className="flow-dag full">
      <ol className="flow-dag-mainline">
        {mainline.map((id, idx) => {
          const node = byId.get(id);
          if (!node) return null;
          const isBranchAnchor = node.id === "development_running";
          return (
            <li key={node.id} className="flow-step-full">
              <FlowNodeCard projectId={projectId} node={node} />
              {isBranchAnchor && needsHumanNode && (
                <div className="flow-branch" aria-label="需要人工分支">
                  <span className="flow-branch-edge" aria-hidden>
                    ↪
                  </span>
                  <FlowNodeCard projectId={projectId} node={needsHumanNode} />
                  <span className="flow-branch-back" aria-hidden>
                    ⤴ 解决后回到开发中
                  </span>
                </div>
              )}
              {idx < mainline.length - 1 && (
                <span className="flow-edge-vert" aria-hidden>
                  ↓
                </span>
              )}
            </li>
          );
        })}
      </ol>

      {backEdge && (
        <div className="flow-back-edge" aria-label="下一轮评审回边">
          <span aria-hidden>↺</span>
          <span>
            下一轮评审:<code className="cell-code">{backEdge.from}</code>
            {" → "}
            <code className="cell-code">{backEdge.to}</code>
          </span>
        </div>
      )}

      <FlowLegend />
    </div>
  );
}

function FlowNodeCard({
  projectId,
  node,
}: {
  projectId: string;
  node: FlowNode;
}) {
  const inner = (
    <>
      <span className="flow-node-icon" aria-hidden>
        {STATUS_ICON[node.status]}
      </span>
      <span className="flow-node-body">
        <span className="flow-node-label">{node.labelZh}</span>
        <span className="flow-node-status">
          {STATUS_LABEL_ZH[node.status]}
        </span>
      </span>
    </>
  );

  // Only "available" / "current" / "blocked" nodes are actionable —
  // done/optional/pending nodes don't need a click target right now.
  const actionable =
    node.status === "current" ||
    node.status === "available" ||
    node.status === "blocked";

  if (actionable) {
    return (
      <Link
        href={nodeHref(projectId, node)}
        className="flow-node"
        data-status={node.status}
        title={node.hint}
      >
        {inner}
      </Link>
    );
  }
  return (
    <div className="flow-node" data-status={node.status} title={node.hint}>
      {inner}
    </div>
  );
}

function FlowLegend() {
  const statuses: FlowNodeStatus[] = [
    "done",
    "current",
    "available",
    "blocked",
    "pending",
    "optional",
  ];
  return (
    <div className="flow-legend" aria-label="状态图例">
      {statuses.map((s) => (
        <span key={s} className="flow-legend-chip" data-status={s}>
          <span aria-hidden>{STATUS_ICON[s]}</span>
          {STATUS_LABEL_ZH[s]}
        </span>
      ))}
    </div>
  );
}
