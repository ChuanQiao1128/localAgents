/**
 * GET  /api/studio-projects   — 列出所有 studio project 摘要。
 * POST /api/studio-projects   — 新建项目。Body: { name: string, template?: string|null, id?: string }
 */

import { NextResponse } from "next/server";
import {
  createStudioProject,
  listStudioProjects,
} from "@/lib/studioProjects";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const projects = await listStudioProjects();
    return NextResponse.json({ projects });
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to list studio projects", detail: String(exc) },
      { status: 500 },
    );
  }
}

export async function POST(req: Request) {
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  const obj = (body ?? {}) as Record<string, unknown>;
  const name = typeof obj.name === "string" ? obj.name : "";
  const template = typeof obj.template === "string" ? obj.template : null;
  const id = typeof obj.id === "string" ? obj.id : undefined;
  try {
    const result = await createStudioProject({ name, template, id });
    return NextResponse.json(result, { status: 201 });
  } catch (exc) {
    return NextResponse.json(
      { error: String(exc instanceof Error ? exc.message : exc) },
      { status: 400 },
    );
  }
}
