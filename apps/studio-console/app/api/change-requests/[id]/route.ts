/**
 * GET /api/change-requests/[id]   — load full draft (markdown + meta).
 * PUT /api/change-requests/[id]   — update one of two allowed fields.
 *                                   Body: { field: "change-request.md" | "meta.json", content: string }
 *
 * No DELETE in v1.
 */

import { NextResponse } from "next/server";
import {
  loadChangeRequestDraft,
  updateChangeRequestDraft,
} from "@/lib/changeRequests";

export const dynamic = "force-dynamic";

const ALLOWED_FIELDS = ["change-request.md", "meta.json"] as const;
type AllowedField = (typeof ALLOWED_FIELDS)[number];

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || id.includes("/") || id.includes("\\") || id.includes("..")) {
    return NextResponse.json({ error: "invalid draft id" }, { status: 400 });
  }
  try {
    const draft = await loadChangeRequestDraft(id);
    if (!draft) {
      return NextResponse.json(
        { error: `change request draft not found: ${id}` },
        { status: 404 },
      );
    }
    return NextResponse.json(draft);
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to load change request draft", detail: String(exc) },
      { status: 500 },
    );
  }
}

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || id.includes("/") || id.includes("\\") || id.includes("..")) {
    return NextResponse.json({ error: "invalid draft id" }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "body must be valid JSON" }, { status: 400 });
  }
  if (!body || typeof body !== "object") {
    return NextResponse.json(
      { error: "body must be an object with `field` and `content`" },
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
    const result = await updateChangeRequestDraft(
      id,
      field as AllowedField,
      content,
    );
    if (!result.ok) {
      return NextResponse.json({ error: result.error }, { status: 400 });
    }
    return NextResponse.json({ ok: true, field });
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to update change request draft", detail: String(exc) },
      { status: 500 },
    );
  }
}
