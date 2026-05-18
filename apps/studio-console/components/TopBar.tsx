"use client";

/**
 * 顶部条 —— 显示安全模式徽章 + 项目选择说明。
 *
 * v1 不在 TopBar 做全局项目选择；项目选择发生在每个工作页内部
 * （Run / Evidence / Review / Change Request 各自的左侧栏），因为不同
 * 页面对"哪个项目最有意义"的判断不一样。这里只展示一个静态提示，
 * 避免之前那个 "(none yet — wired in RC-5A.6)" 误导用户。
 */

export default function TopBar() {
  return (
    <header className="topbar">
      <div className="topbar-left">
        <div className="project-picker">
          <span>项目选择</span>
          <span className="cell-muted" style={{ fontSize: 12 }}>
            在每个工作页内部选择
          </span>
        </div>
      </div>
      <div className="topbar-right">
        <span
          className="safety-badge"
          data-mode="preview"
          title="Live 模式需要单独开启；当前所有命令都只能复制，不会自动执行。"
        >
          安全模式：Preview
        </span>
      </div>
    </header>
  );
}
