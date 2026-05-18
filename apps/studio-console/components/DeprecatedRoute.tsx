/**
 * Deprecation stub —— 旧 6 个 runtime-视角页面的统一替代页。
 *
 * 旧路径继续存在（防 bookmark 死链），但页面只显示"该功能已合并到项目工作台"+
 * 一个跳转按钮。读不到旧数据 / 不调用任何 API。
 */

import Link from "next/link";

export default function DeprecatedRoute({
  oldName,
  oldHref,
  replacementLabel,
  replacementHref,
  replacementHint,
}: {
  /** 旧页面的中文名，例如 "需求合同 / Design"。 */
  oldName: string;
  /** 旧页面的 path（用于 URL 显示）。 */
  oldHref: string;
  /** 新位置的 label。 */
  replacementLabel: string;
  /** 新位置的 href。 */
  replacementHref: string;
  /** 一句话解释新位置在哪。 */
  replacementHint: string;
}) {
  return (
    <>
      <h1 className="page-title">页面已迁移</h1>
      <p className="page-subtitle">
        <code>{oldHref}</code>（{oldName}）已经从主流程中移除。
      </p>

      <section className="card deprecation-card">
        <h2 className="section-title">这个功能现在在哪？</h2>
        <p style={{ margin: "var(--sp-2) 0", lineHeight: 1.7 }}>
          {replacementHint}
        </p>
        <div style={{ display: "flex", gap: "var(--sp-2)", marginTop: "var(--sp-3)" }}>
          <Link
            href={replacementHref}
            className="btn"
            data-variant="primary"
          >
            {replacementLabel} →
          </Link>
          <Link href="/projects" className="btn" data-variant="ghost">
            返回项目列表
          </Link>
        </div>
        <p
          className="cell-muted"
          style={{ fontSize: 12, marginTop: "var(--sp-4)" }}
        >
          为什么搬走？早期版本把后端模块直接铺成 7 个并列页面，让用户难以
          理解工作流。RC-5A.12 之后，所有日常操作都在
          <strong>项目工作台</strong>里完成 —— 一个项目就是一个合同，一条
          交付循环只有讨论 / 开发 / 交付 三步。
        </p>
      </section>
    </>
  );
}
