/**
 * POST /api/studio-projects/[id]/link-runtime
 *
 * Manual dogfood bridge for projects whose runtime project already exists but
 * whose Studio project.json does not yet have agentProjectId/agentProjectPath.
 */

import { NextResponse } from "next/server";
import { linkExistingRuntimeProject } from "@/lib/prepareRuntime";

export const dynamic = "force-dynamic";

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || id.includes("/") || id.includes("\\") || id.includes("..")) {
    return NextResponse.json({ ok: false, error: "invalid project id" }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: "body must be valid JSON" }, { status: 400 });
  }
  const obj = (body ?? {}) as Record<string, unknown>;
  const runtimeRef =
    typeof obj.runtimeRef === "string"
      ? obj.runtimeRef
      : typeof obj.agentProjectId === "string"
        ? obj.agentProjectId
        : typeof obj.agentProjectPath === "string"
          ? obj.agentProjectPath
          : "";
  if (!runtimeRef.trim()) {
    return NextResponse.json(
      { ok: false, error: "runtimeRef is required" },
      { status: 400 },
    );
  }

  try {
    const result = await linkExistingRuntimeProject(id, runtimeRef);
    if (!result.ok) {
      const status = result.error.includes("not found") ? 404 : 400;
      return NextResponse.json(result, { status });
    }
    return NextResponse.json(result);
  } catch (exc) {
    return NextResponse.json(
      { ok: false, error: "failed to link runtime project", detail: String(exc) },
      { status: 500 },
    );
  }
}
