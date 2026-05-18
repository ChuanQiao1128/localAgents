"use client";

import Link from "next/link";

/**
 * 顶部工作流条 —— 让用户在任何一页都能立刻看出
 * "我现在在哪一步、整条路怎么走、下一步去哪"。
 *
 * Design Contract → Pre-flight → Run Monitor →
 *   Human Review (卡住时) → Delivery Evidence → Change Request
 *
 * Dashboard 不在工作流条里 —— 它是入口页，不是流程的一步。
 */

export type WorkflowStepId =
  | "design"
  | "plan"
  | "run"
  | "review"
  | "evidence"
  | "change-request";

type Step = {
  id: WorkflowStepId;
  num: string;
  label: string;
  href: string;
  /** 当某些步骤是分支（不一定每次都走）时标注。 */
  branchNote?: string;
};

const STEPS: readonly Step[] = [
  { id: "design", num: "1", label: "需求合同", href: "/design" },
  { id: "plan", num: "2", label: "开发前检查", href: "/plan" },
  { id: "run", num: "3", label: "运行监控", href: "/run" },
  {
    id: "review",
    num: "4",
    label: "人工审核",
    href: "/review",
    branchNote: "卡住时",
  },
  { id: "evidence", num: "5", label: "交付证据", href: "/evidence" },
  {
    id: "change-request",
    num: "6",
    label: "变更请求",
    href: "/change-request",
    branchNote: "MVP 之后",
  },
];

export default function WorkflowGuide({
  current,
}: {
  /** 当前页对应的步骤；Dashboard 传 null。 */
  current: WorkflowStepId | null;
}) {
  return (
    <nav className="workflow-guide" aria-label="工作流流程">
      <span className="workflow-guide-label">工作流</span>
      <ol className="workflow-guide-steps">
        {STEPS.map((step, idx) => {
          const isActive = current === step.id;
          return (
            <li
              key={step.id}
              className={
                "workflow-guide-step" + (isActive ? " active" : "")
              }
              aria-current={isActive ? "step" : undefined}
            >
              <Link href={step.href} className="workflow-guide-link">
                <span className="workflow-guide-num">{step.num}</span>
                <span className="workflow-guide-text">
                  {step.label}
                  {step.branchNote && (
                    <span className="workflow-guide-branch">
                      （{step.branchNote}）
                    </span>
                  )}
                </span>
              </Link>
              {idx < STEPS.length - 1 && (
                <span className="workflow-guide-arrow" aria-hidden="true">
                  →
                </span>
              )}
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
