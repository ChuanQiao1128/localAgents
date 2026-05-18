"use client";

/**
 * 项目工作台顶部 3-tab 栏。tab 状态走 URL `?tab=discuss|develop|deliver`。
 * 切换用 router.replace 避免污染 history。
 */

import { useRouter, useSearchParams } from "next/navigation";

export type WorkspaceTab = "discuss" | "develop" | "deliver";

export const WORKSPACE_TABS: ReadonlyArray<{
  id: WorkspaceTab;
  num: string;
  labelZh: string;
  labelEn: string;
  hintZh: string;
}> = [
  {
    id: "discuss",
    num: "1",
    labelZh: "讨论与锁定",
    labelEn: "Discuss & Lock",
    hintZh: "决定要做什么。",
  },
  {
    id: "develop",
    num: "2",
    labelZh: "开发中",
    labelEn: "Develop",
    hintZh: "看 Studio 怎么把它做出来。",
  },
  {
    id: "deliver",
    num: "3",
    labelZh: "交付结果",
    labelEn: "Deliver",
    hintZh: "审视 Studio 交付的证据。",
  },
];

export function getActiveTab(value: string | null | undefined): WorkspaceTab {
  if (value === "develop" || value === "deliver" || value === "discuss") {
    return value;
  }
  return "discuss";
}

export default function ProjectTabs({
  active,
  basePath,
}: {
  active: WorkspaceTab;
  /** 例如 `/projects/<id>` —— router 会把 tab 加为 querystring。 */
  basePath: string;
}) {
  const router = useRouter();
  const search = useSearchParams();

  function go(next: WorkspaceTab) {
    const params = new URLSearchParams(search?.toString() ?? "");
    params.set("tab", next);
    // 切换 tab 时清掉 mode（避免 Discuss 的子状态泄漏到其他 tab 上）。
    if (next !== "discuss") params.delete("mode");
    const qs = params.toString();
    router.replace(qs ? `${basePath}?${qs}` : basePath, { scroll: false });
  }

  return (
    <nav className="project-tabs" aria-label="项目工作台">
      {WORKSPACE_TABS.map((t) => {
        const isActive = t.id === active;
        return (
          <button
            key={t.id}
            type="button"
            role="tab"
            aria-selected={isActive}
            className={
              "project-tab" + (isActive ? " active" : "")
            }
            onClick={() => go(t.id)}
            title={t.hintZh}
          >
            <span className="project-tab-num">{t.num}</span>
            <span className="project-tab-text">
              <span className="project-tab-label">{t.labelZh}</span>
              <span className="project-tab-en">{t.labelEn}</span>
            </span>
          </button>
        );
      })}
    </nav>
  );
}
