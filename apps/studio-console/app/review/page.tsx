import DeprecatedRoute from "@/components/DeprecatedRoute";

export default function ReviewDeprecatedPage() {
  return (
    <DeprecatedRoute
      oldName="人工审核 / Human Review"
      oldHref="/review"
      replacementLabel="去项目列表"
      replacementHref="/projects"
      replacementHint="人工审核已经从主流程拿掉 —— 它本来就只在 Studio 暂停时才有意义。现在 Studio 卡住时，「需要处理」红色横幅会直接出现在项目工作台 → 「开发中」tab 的顶部，附 approve / reject / resolve 命令。"
    />
  );
}
