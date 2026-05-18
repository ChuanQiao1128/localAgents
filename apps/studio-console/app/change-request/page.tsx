import DeprecatedRoute from "@/components/DeprecatedRoute";

export default function ChangeRequestDeprecatedPage() {
  return (
    <DeprecatedRoute
      oldName="变更请求 / Change Request"
      oldHref="/change-request"
      replacementLabel="去项目列表"
      replacementHref="/projects"
      replacementHint="变更请求不再是独立页面 —— 它是项目工作台 → 「讨论与锁定」 → 模式切换到「变更请求」的下一轮迭代。打开任意已交付的项目，把模式切到变更即可。change-request.md 现在直接住在 .studio-console/projects/<id>/changes/ 下。"
    />
  );
}
