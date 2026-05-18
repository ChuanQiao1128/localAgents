/**
 * 页面用途说明卡片 —— 每页都嵌一份，回答四个问题：
 *   1. 这页是干嘛的？
 *   2. 什么时候用？
 *   3. 它读 / 写什么？
 *   4. 下一步去哪？
 *
 * 文案是页面级 props 写死，不是数据驱动 —— 让用户在任何时刻一眼
 * 看到这页"在工作流里到底是什么角色"。
 */

import type React from "react";

export type PagePurposeProps = {
  what: React.ReactNode;
  when: React.ReactNode;
  /** "它读 / 写什么"。读写说明合并成一行，更精炼。 */
  io: React.ReactNode;
  next: React.ReactNode;
};

export default function PagePurpose({ what, when, io, next }: PagePurposeProps) {
  return (
    <section className="page-purpose" aria-label="页面用途说明">
      <dl className="page-purpose-list">
        <div className="page-purpose-row">
          <dt>这页是干嘛的</dt>
          <dd>{what}</dd>
        </div>
        <div className="page-purpose-row">
          <dt>什么时候用</dt>
          <dd>{when}</dd>
        </div>
        <div className="page-purpose-row">
          <dt>读 / 写</dt>
          <dd>{io}</dd>
        </div>
        <div className="page-purpose-row">
          <dt>下一步</dt>
          <dd>{next}</dd>
        </div>
      </dl>
    </section>
  );
}
