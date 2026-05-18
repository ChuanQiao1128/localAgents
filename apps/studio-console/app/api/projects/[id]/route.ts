/**
 * GET /api/projects/[id] — full project detail.
 *
 * 404 if the project dir doesn't exist or fails the path allowlist.
 */

import { NextResponse } from "next/server";
import { loadProjectDetail } from "@/lib/projects";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || typeof id !== "string") {
    return NextResponse.json({ error: "missing project id" }, { status: 400 });
  }
  // Defensive: refuse anything that looks like path traversal in the id.
  if (id.includes("/") || id.includes("\\") || id.includes("..")) {
    return NextResponse.json({ error: "invalid project id" }, { status: 400 });
  }
  try {
    const detail = await loadProjectDetail(id);
    if (!detail) {
      return NextResponse.json(
        { error: `project not found: ${id}` },
        { status: 404 },
      );
    }
    return NextResponse.json(detail);
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to load project", detail: String(exc) },
      { status: 500 },
    );
  }
}
