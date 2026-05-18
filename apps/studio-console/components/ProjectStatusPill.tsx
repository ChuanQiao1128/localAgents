/**
 * 项目状态徽章 —— 中文主标 + 英文副标，鼠标悬停显示一句话解释。
 * 派生逻辑在 lib/projectStatus.ts 里。
 */

import StatusBadge from "./StatusBadge";
import type { ProjectStatus } from "@/lib/projectStatus";

export default function ProjectStatusPill({
  status,
  size = "md",
}: {
  status: ProjectStatus;
  size?: "sm" | "md";
}) {
  return (
    <span
      className={
        "project-status-pill" + (size === "sm" ? " project-status-pill-sm" : "")
      }
      title={status.hintZh}
    >
      <StatusBadge variant={status.variant}>{status.labelZh}</StatusBadge>
      <span className="project-status-pill-en">{status.labelEn}</span>
    </span>
  );
}
