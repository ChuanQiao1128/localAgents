/**
 * GET  /api/change-requests   — list every draft under .studio-console/changes/.
 * POST /api/change-requests   — create a new draft, optionally pre-associated with a project.
 *
 * Body for POST (all fields optional):
 *   { projectId?: string | null; title?: string | null }
 *
 * No DELETE in v1 — drafts persist on disk. Operator can rm them manually
 * if they want to clean up.
 */

import { NextResponse } from "next/server";
import {
  createChangeRequestDraft,
  listChangeRequestDrafts,
} from "@/lib/changeRequests";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const drafts = await listChangeRequestDrafts();
    return NextResponse.json({ drafts });
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to list change request drafts", detail: String(exc) },
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
  const projectId =
    typeof obj.projectId === "string" ? obj.projectId : null;
  const title = typeof obj.title === "string" ? obj.title : null;
  try {
    const id = await createChangeRequestDraft({ projectId, title });
    return NextResponse.json({ id }, { status: 201 });
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to create change request draft", detail: String(exc) },
      { status: 500 },
    );
  }
}
