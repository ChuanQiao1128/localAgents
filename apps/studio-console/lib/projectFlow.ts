/**
 * RC-5K Phase 1 —— Project Delivery Flow DAG.
 *
 * This is a *conceptual* flow describing the Studio product workflow, NOT a
 * runtime task graph visualization. The DAG answers "where am I and what's
 * next?" for the user — Discuss → Lock → Prepare Runtime → Develop → Deliver →
 * Product Review → Recommended Changes → Change Request → Updated Delivery →
 * (loops back to Product Review).
 *
 * Notes:
 *  - Pure functions only. No fs, no fetch. UI imports + renders from here.
 *  - The back edge updated_delivery → product_review is marked `back: true`
 *    so the renderer can draw it differently (dashed / labeled), but the
 *    semantics ARE cyclic and that's intentional.
 *  - Status derivation is conservative: when we can't tell, we say `pending`
 *    or `available` (never falsely `done`).
 */

import type {
  ChangeSummaryClient,
  ContractFileName,
  StudioProjectDetailClient,
} from "./types";

export type FlowNodeStatus =
  | "done"
  | "current"
  | "available"
  | "blocked"
  | "pending"
  | "optional";

export type FlowNodeId =
  | "project_created"
  | "requirements_discussed"
  | "contract_locked"
  | "runtime_prepared"
  | "development_started"
  | "development_running"
  | "needs_human"
  | "development_completed"
  | "delivered"
  | "product_review"
  | "recommended_changes"
  | "change_request_running"
  | "updated_delivery";

export type FlowNode = {
  id: FlowNodeId;
  labelZh: string;
  labelEn: string;
  hint: string;
  /** Tab route fragment, e.g. "discuss" / "develop" / "deliver" / "develop&mode=change". */
  tab: string;
  status: FlowNodeStatus;
};

export type FlowEdge = {
  from: FlowNodeId;
  to: FlowNodeId;
  /** Conceptual return edge (rendered dashed, labeled "下一轮评审"). */
  back?: boolean;
};

const NODE_DEFS: Array<Omit<FlowNode, "status">> = [
  {
    id: "project_created",
    labelZh: "项目已创建",
    labelEn: "Project Created",
    hint: "项目目录已生成,合同文件就位",
    tab: "discuss",
  },
  {
    id: "requirements_discussed",
    labelZh: "需求已讨论",
    labelEn: "Requirements Discussed",
    hint: "raw-requirements / discussion / product-contract / mvp-requirements 写出有意义内容",
    tab: "discuss",
  },
  {
    id: "contract_locked",
    labelZh: "合同已锁定",
    labelEn: "Contract Locked",
    hint: "lock.json locked=true,锁定后才能 Prepare Runtime",
    tab: "discuss",
  },
  {
    id: "runtime_prepared",
    labelZh: "Runtime 已准备",
    labelEn: "Runtime Prepared",
    hint: "agent-studio CLI 已链接 / 初始化对应 runtime project",
    tab: "develop",
  },
  {
    id: "development_started",
    labelZh: "开发已启动",
    labelEn: "Development Started",
    hint: "已有 autonomous session 记录",
    tab: "develop",
  },
  {
    id: "development_running",
    labelZh: "开发中",
    labelEn: "Development Running",
    hint: "autonomous 跑动中,可能进入 Needs Human 分支",
    tab: "develop",
  },
  {
    id: "needs_human",
    labelZh: "需要人工处理",
    labelEn: "Needs Human",
    hint: "review queue 有 blocking 项,或 session 暂停等待审核",
    tab: "develop",
  },
  {
    id: "development_completed",
    labelZh: "开发完成",
    labelEn: "Development Completed",
    hint: "task graph 全部 completed 或 autonomous session completed",
    tab: "deliver",
  },
  {
    id: "delivered",
    labelZh: "已交付",
    labelEn: "Delivered",
    hint: "至少一个 change 进入 delivered,有 commit + 预览",
    tab: "deliver",
  },
  {
    id: "product_review",
    labelZh: "产品评审",
    labelEn: "Product Review",
    hint: "Studio 自评,产出 score / verdict / 推荐变更",
    tab: "develop",
  },
  {
    id: "recommended_changes",
    labelZh: "推荐变更",
    labelEn: "Recommended Changes",
    hint: "Studio Product Review 自动生成的 CR draft 列表",
    tab: "develop",
  },
  {
    id: "change_request_running",
    labelZh: "变更执行中",
    labelEn: "Change Request Running",
    hint: "选定 CR 正在通过 Codex + Promotion + Apply 流",
    tab: "develop",
  },
  {
    id: "updated_delivery",
    labelZh: "更新已交付",
    labelEn: "Updated Delivery",
    hint: "变更 commit 已落,触发下一轮 Product Review",
    tab: "deliver",
  },
];

const EDGES: FlowEdge[] = [
  { from: "project_created", to: "requirements_discussed" },
  { from: "requirements_discussed", to: "contract_locked" },
  { from: "contract_locked", to: "runtime_prepared" },
  { from: "runtime_prepared", to: "development_started" },
  { from: "development_started", to: "development_running" },
  { from: "development_running", to: "development_completed" },
  { from: "development_running", to: "needs_human" },
  { from: "needs_human", to: "development_running" },
  { from: "development_completed", to: "delivered" },
  { from: "delivered", to: "product_review" },
  { from: "product_review", to: "recommended_changes" },
  { from: "recommended_changes", to: "change_request_running" },
  { from: "change_request_running", to: "updated_delivery" },
  { from: "updated_delivery", to: "product_review", back: true },
];

/** Generic flow with every node `pending`. Used by docs/Dashboard later. */
export function buildGenericDeliveryFlow(): {
  nodes: FlowNode[];
  edges: FlowEdge[];
} {
  return {
    nodes: NODE_DEFS.map((def) => ({ ...def, status: "pending" as const })),
    edges: [...EDGES],
  };
}

export type DeriveProjectFlowInput = {
  detail: StudioProjectDetailClient;
};

/**
 * Walk the project state once and mark every node with a conservative status.
 *
 * The page passes the same `detail` it already holds — no extra fetch.
 * `product_review` and `recommended_changes` cannot reliably distinguish
 * "done" from "available" without reading product-review.json; we mark them
 * `available` after a delivery so the user knows it's their next action.
 */
export function deriveProjectDeliveryFlow(
  input: DeriveProjectFlowInput,
): { nodes: FlowNode[]; edges: FlowEdge[] } {
  const { detail } = input;
  const isLocked = detail.lockState.locked;
  const isRuntimeLinked = Boolean(
    detail.agentProjectId && detail.agentProjectPath,
  );
  const sessionStatus = detail.latestSessionStatus ?? null;
  const sessionExists = Boolean(detail.latestSessionId);
  const taskCount = detail.taskCount ?? 0;
  const completedCount = detail.completedCount ?? 0;
  const allCompleted = taskCount > 0 && completedCount === taskCount;
  const changes: ChangeSummaryClient[] = detail.runDetail?.changes ?? [];
  const deliveredChanges = changes.filter((c) => c.state === "delivered");
  const hasDelivered =
    Boolean(detail.latestDeliveredSha) || deliveredChanges.length > 0;
  const activeChange = changes.some(
    (c) =>
      c.state === "ready_for_run" ||
      c.state === "applied" ||
      c.state === "needs_human_review",
  );
  const hasMultipleDeliveries = deliveredChanges.length >= 2;
  const discussed = hasMeaningfulDiscussion(detail.files);

  // "needs_human" — distinguish actively blocking from stale-but-flagged.
  // The review queue keeps items even after the project moves on with a
  // newer successful delivery; without filtering, every delivered project
  // with a historical blocking item would render as "needs_human = blocked".
  //
  // We compute the latest delivery timestamp and only count review items
  // newer than it as actually blocking. Plus the session itself being in a
  // needs-human state, IF we haven't already delivered past that pause.
  const latestDeliveryMs = latestDeliveredAtMs(changes);
  const rawItems = detail.runDetail?.reviewQueue?.items;
  const reviewItems = Array.isArray(rawItems) ? rawItems : [];
  const effectiveBlocking = reviewItems.filter((item) => {
    if (item.status !== "open") return false;
    if (item.severity !== "blocking") return false;
    if (latestDeliveryMs == null) return true;
    const createdMs = item.createdAt ? Date.parse(item.createdAt) : NaN;
    if (!Number.isFinite(createdMs)) return true;
    return createdMs > latestDeliveryMs;
  }).length;
  const sessionWaitingHuman =
    sessionStatus === "needs_human_review" ||
    sessionStatus === "needs-human-review" ||
    sessionStatus === "paused";
  const needsHumanActive =
    effectiveBlocking > 0 || (sessionWaitingHuman && !hasDelivered);
  const runningActive =
    sessionStatus === "running" || sessionStatus === "starting";

  const status: Record<FlowNodeId, FlowNodeStatus> = {
    project_created: "done", // we're rendering = the project exists
    requirements_discussed: "pending",
    contract_locked: "pending",
    runtime_prepared: "pending",
    development_started: "pending",
    development_running: "pending",
    needs_human: "optional",
    development_completed: "pending",
    delivered: "pending",
    product_review: "pending",
    recommended_changes: "pending",
    change_request_running: "pending",
    updated_delivery: "pending",
  };

  // requirements_discussed
  if (discussed || isLocked) {
    status.requirements_discussed = "done";
  } else {
    status.requirements_discussed = "current";
  }

  // contract_locked
  if (isLocked) {
    status.contract_locked = "done";
  } else if (status.requirements_discussed === "done") {
    status.contract_locked = "current";
  }

  // runtime_prepared
  if (isRuntimeLinked) {
    status.runtime_prepared = "done";
  } else if (isLocked) {
    status.runtime_prepared = "available";
  }

  // development_started
  if (sessionExists) {
    status.development_started = "done";
  } else if (isRuntimeLinked) {
    status.development_started = "available";
  }

  // development_running
  if (runningActive) {
    status.development_running = "current";
  } else if (
    sessionStatus === "completed" ||
    allCompleted ||
    hasDelivered ||
    sessionStatus === "failed" ||
    sessionStatus === "abandoned"
  ) {
    status.development_running = "done";
  } else if (sessionExists) {
    status.development_running = "available";
  }

  // needs_human (branch — only highlights when active)
  if (needsHumanActive) {
    status.needs_human = "blocked";
  }
  // else stays "optional"

  // development_completed
  if (sessionStatus === "completed" || allCompleted || hasDelivered) {
    status.development_completed = "done";
  } else if (status.development_running === "current") {
    status.development_completed = "pending";
  }

  // delivered
  if (hasDelivered) {
    status.delivered = "done";
  } else if (status.development_completed === "done") {
    status.delivered = "available";
  }

  // product_review (no fetch — "available" once delivered; user clicks to run)
  if (hasDelivered) {
    status.product_review = "available";
  }

  // recommended_changes — conservative: surfaces only when at least one
  // change exists past initial delivery, OR when an active change is in flight.
  if (changes.length > 0) {
    status.recommended_changes = "available";
  }

  // change_request_running
  if (activeChange) {
    status.change_request_running = "current";
  } else if (changes.length > 0 && hasDelivered) {
    status.change_request_running = "available";
  }

  // updated_delivery
  if (hasMultipleDeliveries) {
    status.updated_delivery = "done";
  } else if (status.change_request_running === "current") {
    status.updated_delivery = "available";
  }

  // If everything up to delivered is done AND no active change, set
  // product_review or recommended_changes as the visible "current" so the
  // compact view knows where to point the user.
  if (
    status.delivered === "done" &&
    !runningActive &&
    !activeChange &&
    !needsHumanActive
  ) {
    if (changes.length === 0 || !hasMultipleDeliveries) {
      // No follow-up change yet → next step is Product Review.
      status.product_review = "current";
    }
  }

  const nodes = NODE_DEFS.map((def) => ({ ...def, status: status[def.id] }));
  return { nodes, edges: [...EDGES] };
}

function latestDeliveredAtMs(changes: ChangeSummaryClient[]): number | null {
  let best: number | null = null;
  for (const change of changes) {
    if (change.state !== "delivered") continue;
    if (!change.appliedAt) continue;
    const ms = Date.parse(change.appliedAt);
    if (!Number.isFinite(ms)) continue;
    if (best == null || ms > best) best = ms;
  }
  return best;
}

function hasMeaningfulDiscussion(
  files: Record<ContractFileName, string>,
): boolean {
  const KEYS: ContractFileName[] = [
    "raw-requirements.md",
    "discussion.md",
    "product-contract.md",
    "mvp-requirements.md",
  ];
  for (const key of KEYS) {
    const value = files[key] ?? "";
    // 50 chars threshold avoids counting bare templates / placeholder text.
    if (value.trim().length > 50) return true;
  }
  return false;
}

/**
 * Pick at most 3 nodes for the compact view:
 *   [previous-done, current, next-available]
 *
 * If no "current" exists, pick the most recent "done" as focus and the next
 * "available" / "pending" as the suggested step. Always returns 1-3 nodes in
 * flow order.
 */
export function compactWindow(nodes: FlowNode[]): FlowNode[] {
  // Find first node whose status is "current" or "blocked" (the active focus).
  let activeIdx = nodes.findIndex(
    (n) => n.status === "current" || n.status === "blocked",
  );

  if (activeIdx === -1) {
    // No active step. The most useful focus is the EARLIEST available node
    // (i.e. "what should you do next?"). Falling back to last-done would
    // point at the past, not the next action.
    let focusIdx = nodes.findIndex((n) => n.status === "available");
    if (focusIdx === -1) {
      // Nothing available either → focus = last done; user is at a steady state.
      let lastDone = -1;
      for (let i = 0; i < nodes.length; i += 1) {
        if (nodes[i].status === "done") lastDone = i;
      }
      if (lastDone === -1) return [nodes[0]];
      focusIdx = lastDone;
    }
    // Build window: previous done + focus + next non-pending (skipping needs_human).
    let prevDone = -1;
    for (let i = focusIdx - 1; i >= 0; i -= 1) {
      if (nodes[i].status === "done") {
        prevDone = i;
        break;
      }
    }
    let next = -1;
    for (let i = focusIdx + 1; i < nodes.length; i += 1) {
      if (nodes[i].id === "needs_human") continue;
      if (nodes[i].status !== "pending") {
        next = i;
        break;
      }
    }
    const out: FlowNode[] = [];
    if (prevDone !== -1) out.push(nodes[prevDone]);
    out.push(nodes[focusIdx]);
    if (next !== -1) out.push(nodes[next]);
    return out;
  }

  // Has an active step → show prev done + current + next.
  let prevDone = -1;
  for (let i = activeIdx - 1; i >= 0; i -= 1) {
    if (nodes[i].status === "done") {
      prevDone = i;
      break;
    }
  }
  let next = -1;
  for (let i = activeIdx + 1; i < nodes.length; i += 1) {
    if (nodes[i].id === "needs_human") continue; // skip optional branch
    if (nodes[i].status !== "pending") {
      next = i;
      break;
    }
  }
  if (next === -1 && activeIdx + 1 < nodes.length) {
    next = activeIdx + 1;
  }
  const out: FlowNode[] = [];
  if (prevDone !== -1) out.push(nodes[prevDone]);
  out.push(nodes[activeIdx]);
  if (next !== -1) out.push(nodes[next]);
  return out;
}
