/**
 * 项目状态机 —— 把 ProjectDetailResponse + 关联 contract（可选）+ 已完成的 change
 * 综合到 8 个面向用户的状态之一。
 *
 * 派生顺序按上→下、首次匹配生效。规则在 docs/STUDIO_CONSOLE_SPEC.md 之外
 * 没有锁定的源；这里就是源。
 */

import type {
  ChangeSummaryClient,
  ContractSummary,
  ProjectDetailResponse,
  StudioProjectSummaryClient,
} from "./types";

export type ProjectStatusKey =
  | "draft"
  | "locked"
  | "ready"
  | "running"
  | "needs_human"
  | "completed"
  | "delivered"
  | "failed";

export type ProjectStatus = {
  key: ProjectStatusKey;
  /** 中文显示标签。 */
  labelZh: string;
  /** 英文工程标签（小字副标）。 */
  labelEn: string;
  /** 一句话解释，给 tooltip / 副标。 */
  hintZh: string;
  /** 映射到 StatusBadge variant。 */
  variant:
    | "default"
    | "completed"
    | "running"
    | "needs-review"
    | "failed"
    | "locked"
    | "delivered";
};

export type DeriveProjectStatusInput = {
  /** 来自 /api/projects/[id]，可能为 null（项目还没创建）。 */
  detail: ProjectDetailResponse | null;
  /** 关联的 contract，可能为 null（还没找到对应的 contract draft）。 */
  contract: ContractSummary | null;
};

const STATUS_TABLE: Record<ProjectStatusKey, Omit<ProjectStatus, "key">> = {
  draft: {
    labelZh: "草稿",
    labelEn: "Draft",
    hintZh: "项目刚开始，还在讨论需求。",
    variant: "default",
  },
  locked: {
    labelZh: "已锁定",
    labelEn: "Locked",
    hintZh: "MVP 合同已锁定，等待生成项目。",
    variant: "locked",
  },
  ready: {
    labelZh: "可开跑",
    labelEn: "Ready",
    hintZh: "项目已经存在 task graph，等待 agent-studio autonomous start。",
    variant: "default",
  },
  running: {
    labelZh: "运行中",
    labelEn: "Running",
    hintZh: "autonomous loop 正在执行，Run Monitor 会每 3 秒刷新。",
    variant: "running",
  },
  needs_human: {
    labelZh: "待人工",
    labelEn: "Needs Human",
    hintZh: "Studio 因 gate 失败、scope 越界或敏感修改暂停，等待你处理。",
    variant: "needs-review",
  },
  completed: {
    labelZh: "已完成",
    labelEn: "Completed",
    hintZh: "task graph 全部完成；尚未触发 change 交付。",
    variant: "completed",
  },
  delivered: {
    labelZh: "已交付",
    labelEn: "Delivered",
    hintZh: "至少一个 change 走完 delivery，证据齐全。",
    variant: "delivered",
  },
  failed: {
    labelZh: "失败",
    labelEn: "Failed",
    hintZh: "session / change 终态为 failed；建议查看证据或重试。",
    variant: "failed",
  },
};

function build(key: ProjectStatusKey): ProjectStatus {
  return { key, ...STATUS_TABLE[key] };
}

/**
 * 从 Studio Project（一项目一合同）派生状态。RC-5A.12.1 起的主入口；
 * 老的 deriveProjectStatus 留给 legacy 页面用。
 */
export function deriveStudioProjectStatus(
  studio: StudioProjectSummaryClient,
): ProjectStatus {
  // 1. running
  if (studio.latestSessionStatus === "running") return build("running");
  // 2. delivered（有 latestDeliveredSha 即至少有一个 change delivered）
  // Historical review items from abandoned/failed attempts are still shown in
  // the inspector, but they should not hide a later successful delivery.
  if (studio.latestDeliveredSha != null) return build("delivered");
  // 3. needs_human
  if (
    studio.latestSessionStatus === "paused" ||
    studio.latestSessionStatus === "needs_human_review" ||
    studio.latestSessionStatus === "needs-human-review" ||
    studio.reviewQueueOpen > 0
  ) {
    return build("needs_human");
  }
  // 3. failed
  if (
    studio.latestSessionStatus === "failed" ||
    studio.latestSessionStatus === "abandoned"
  ) {
    return build("failed");
  }
  // 5. completed
  if (
    studio.latestSessionStatus === "completed" &&
    studio.taskCount > 0 &&
    studio.completedCount === studio.taskCount
  ) {
    return build("completed");
  }
  // 6. ready —— 有 task graph，无 session
  if (studio.taskCount > 0 && studio.latestSessionId == null) {
    return build("ready");
  }
  // 7. locked
  if (studio.contract.locked) return build("locked");
  // 8. draft
  return build("draft");
}

/**
 * 派生项目状态。规则按文档顺序：
 *   1. running           latestSession.status === "running"
 *   2. needs_human       paused / open reviews / needs_human_review change
 *   3. failed            session 失败 OR 任何 change 失败
 *   4. delivered         有 change 且全部 delivered
 *   5. completed         session 完成 + tasks 全 completed
 *   6. ready             有 task graph 但还没 session
 *   7. locked            没项目，但有 locked 的 contract
 *   8. draft             兜底
 */
export function deriveProjectStatus(
  input: DeriveProjectStatusInput,
): ProjectStatus {
  const { detail, contract } = input;

  // 1. running
  if (detail?.latestSessionStatus === "running") {
    return build("running");
  }

  // 2. needs_human
  if (
    detail?.latestSessionStatus === "paused" ||
    detail?.latestSessionStatus === "needs_human_review" ||
    detail?.latestSessionStatus === "needs-human-review" ||
    (detail?.reviewQueue.open ?? 0) > 0 ||
    (detail?.changes ?? []).some(
      (c: ChangeSummaryClient) => c.state === "needs_human_review",
    )
  ) {
    return build("needs_human");
  }

  // 3. failed
  if (
    detail?.latestSessionStatus === "failed" ||
    detail?.latestSessionStatus === "abandoned" ||
    (detail?.changes ?? []).some(
      (c: ChangeSummaryClient) => c.state === "failed",
    )
  ) {
    return build("failed");
  }

  // 4. delivered
  const changes = detail?.changes ?? [];
  if (
    changes.length > 0 &&
    changes.every((c: ChangeSummaryClient) => c.state === "delivered")
  ) {
    return build("delivered");
  }

  // 5. completed
  if (
    detail?.latestSessionStatus === "completed" &&
    (detail?.taskCount ?? 0) > 0 &&
    detail.completedCount === detail.taskCount
  ) {
    return build("completed");
  }

  // 6. ready
  if ((detail?.taskCount ?? 0) > 0 && detail?.latestSessionId == null) {
    return build("ready");
  }

  // 7. locked
  if (contract?.lockState.locked) {
    return build("locked");
  }

  // 8. draft
  return build("draft");
}
