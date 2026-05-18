"use client";

/**
 * Discuss & Lock 内的模式切换：新建合同 vs 变更请求。
 * 状态走 URL `?mode=new|change`，默认 `new`。
 */

import { useRouter, useSearchParams } from "next/navigation";

export type DiscussMode = "new" | "change";

export function getActiveMode(value: string | null | undefined): DiscussMode {
  return value === "change" ? "change" : "new";
}

export default function ModeToggle({
  active,
  basePath,
}: {
  active: DiscussMode;
  basePath: string;
}) {
  const router = useRouter();
  const search = useSearchParams();

  function go(next: DiscussMode) {
    const params = new URLSearchParams(search?.toString() ?? "");
    params.set("tab", "discuss");
    if (next === "new") {
      params.delete("mode");
    } else {
      params.set("mode", "change");
    }
    const qs = params.toString();
    router.replace(qs ? `${basePath}?${qs}` : basePath, { scroll: false });
  }

  return (
    <div className="project-mode-toggle" role="tablist" aria-label="工作模式">
      <button
        type="button"
        role="tab"
        aria-selected={active === "new"}
        className={"mode-button" + (active === "new" ? " active" : "")}
        onClick={() => go("new")}
        title="为新项目编写并锁定一份 MVP 合同。"
      >
        新建合同
        <span className="mode-button-en">New contract</span>
      </button>
      <button
        type="button"
        role="tab"
        aria-selected={active === "change"}
        className={"mode-button" + (active === "change" ? " active" : "")}
        onClick={() => go("change")}
        title="给已有项目写一份 change request。"
      >
        变更请求
        <span className="mode-button-en">Change request</span>
      </button>
    </div>
  );
}
