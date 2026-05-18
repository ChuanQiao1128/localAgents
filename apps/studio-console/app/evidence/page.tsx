import DeprecatedRoute from "@/components/DeprecatedRoute";

export default function EvidenceDeprecatedPage() {
  return (
    <DeprecatedRoute
      oldName="交付证据 / Delivery Evidence"
      oldHref="/evidence"
      replacementLabel="去项目列表"
      replacementHref="/projects"
      replacementHint="所有 delivery-report / applied-change / promotion-report / eval-results / changed-files / repair-history 现在都在项目工作台 → 「交付结果」tab。每个 change 一张卡，高级证据折叠在「高级证据」详情里。"
    />
  );
}
