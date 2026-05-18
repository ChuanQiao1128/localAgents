import DeprecatedRoute from "@/components/DeprecatedRoute";

export default function PlanDeprecatedPage() {
  return (
    <DeprecatedRoute
      oldName="开发前检查 / Pre-flight"
      oldHref="/plan"
      replacementLabel="去项目列表"
      replacementHref="/projects"
      replacementHint="开发前检查现在以「Pre-flight 摘要卡」的形式直接出现在项目工作台 → 讨论与锁定 tab 的最下方 —— 锁定按钮上面那张卡。"
    />
  );
}
