import DeprecatedRoute from "@/components/DeprecatedRoute";

export default function DesignDeprecatedPage() {
  return (
    <DeprecatedRoute
      oldName="需求合同 / Design"
      oldHref="/design"
      replacementLabel="去项目列表新建项目"
      replacementHref="/projects"
      replacementHint="编写产品合同 / MVP 需求 / 未决问题 / 锁定 —— 现在都在项目工作台的「讨论与锁定」tab 里完成。每个项目独占一份合同，不再有独立漂浮的 contract draft。"
    />
  );
}
