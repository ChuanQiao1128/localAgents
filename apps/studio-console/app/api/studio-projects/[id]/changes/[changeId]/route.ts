/**
 * GET /api/studio-projects/[id]/changes/[changeId]  —— 加载完整 draft（meta + 内容）
 * PUT /api/studio-projects/[id]/changes/[changeId]  —— 更新 change-request.md 或 meta.json
 *
 * Body for PUT: { field: "change-request.md" | "meta.json", content: string }
 */

import { NextResponse } from "next/server";
import {
  loadChangeDraft,
  updateChangeDraft,
} from "@/lib/studioProjects";

export const dynamic = "force-dynamic";

const ALLOWED_FIELDS = ["change-request.md", "meta.json"] as const;
type AllowedField = (typeof ALLOWED_FIELDS)[number];

function validIds(id: string, changeId: string): boolean {
  return (
    !!id &&
    !!changeId &&
    !id.includes("/") &&
    !id.includes("\\") &&
    !id.includes("..") &&
    !changeId.includes("/") &&
    !changeId.includes("\\") &&
    !changeId.includes("..")
  );
}

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string; changeId: string }> },
) {
  const { id, changeId } = await params;
  if (!validIds(id, changeId)) {
    return NextResponse.json({ error: "invalid id" }, { status: 400 });
  }
  try {
    const draft = await loadChangeDraft(id, changeId);
    if (!draft) {
      return NextResponse.json(
        { error: `change draft not found: ${id}/${changeId}` },
        { status: 404 },
      );
    }
    return NextResponse.json(draft);
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to load change draft", detail: String(exc) },
      { status: 500 },
    );
  }
}

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ id: string; changeId: string }> },
) {
  const { id, changeId } = await params;
  if (!validIds(id, changeId)) {
    return NextResponse.json({ error: "invalid id" }, { status: 400 });
  }
  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "body must be valid JSON" }, { status: 400 });
  }
  if (!body || typeof body !== "object") {
    return NextResponse.json(
      { error: "body must be { field, content }" },
      { status: 400 },
    );
  }
  const { field, content } = body as { field?: unknown; content?: unknown };
  if (typeof field !== "string" || typeof content !== "string") {
    return NextResponse.json(
      { error: "body.field (string) and body.content (string) are required" },
      { status: 400 },
    );
  }
  if (!(ALLOWED_FIELDS as readonly string[]).includes(field)) {
    return NextResponse.json(
      { error: `field not allowed: ${field}`, allowed: ALLOWED_FIELDS },
      { status: 400 },
    );
  }
  try {
    const result = await updateChangeDraft(
      id,
      changeId,
      field as AllowedField,
      content,
    );
    if (!result.ok) {
      return NextResponse.json({ error: result.error }, { status: 400 });
    }
    return NextResponse.json({ ok: true, field });
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to update change draft", detail: String(exc) },
      { status: 500 },
    );
  }
}
