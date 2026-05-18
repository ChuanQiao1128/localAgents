import DeprecatedRoute from "@/components/DeprecatedRoute";

export default function RunDeprecatedPage() {
  return (
    <DeprecatedRoute
      oldName="运行监控 / Run Monitor"
      oldHref="/run"
      replacementLabel="去项目列表"
      replacementHref="/projects"
      replacementHint="任务时间线 / session 状态 / 自动刷新 / Action Required 现在都在项目工作台 → 「开发中」tab。点击任意项目进入。"
    />
  );
}
