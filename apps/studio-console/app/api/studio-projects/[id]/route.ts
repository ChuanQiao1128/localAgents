/**
 * GET /api/studio-projects/[id] —— 完整详情 = 元数据 + 6 个合同文件 +
 *   关联的 .agent-studio runtime 详情（task-graph / sessions / changes / reviews）。
 *
 * 404 当 .studio-console/projects/<id>/ 不存在。
 */

import { NextResponse } from "next/server";
import { loadStudioProjectDetail } from "@/lib/studioProjects";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || id.includes("/") || id.includes("\\") || id.includes("..")) {
    return NextResponse.json({ error: "invalid project id" }, { status: 400 });
  }
  try {
    const detail = await loadStudioProjectDetail(id);
    if (!detail) {
      return NextResponse.json(
        { error: `studio project not found: ${id}` },
        { status: 404 },
      );
    }
    return NextResponse.json(detail);
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to load studio project", detail: String(exc) },
      { status: 500 },
    );
  }
}
