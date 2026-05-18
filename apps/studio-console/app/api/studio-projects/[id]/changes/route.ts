/**
 * GET  /api/studio-projects/[id]/changes  —— 列出该项目下所有 change draft。
 * POST /api/studio-projects/[id]/changes  —— 新建 draft。Body: { title?: string|null }
 */

import { NextResponse } from "next/server";
import {
  createChangeDraft,
  listChangeDrafts,
} from "@/lib/studioProjects";

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
    const drafts = await listChangeDrafts(id);
    return NextResponse.json({ drafts });
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to list change drafts", detail: String(exc) },
      { status: 500 },
    );
  }
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || id.includes("/") || id.includes("\\") || id.includes("..")) {
    return NextResponse.json({ error: "invalid project id" }, { status: 400 });
  }
  let body: unknown = {};
  try {
    body = await req.json();
  } catch {
    body = {};
  }
  const obj = (body ?? {}) as Record<string, unknown>;
  const title = typeof obj.title === "string" ? obj.title : null;
  try {
    const result = await createChangeDraft(id, { title });
    return NextResponse.json(result, { status: 201 });
  } catch (exc) {
    return NextResponse.json(
      { error: String(exc instanceof Error ? exc.message : exc) },
      { status: 400 },
    );
  }
}
