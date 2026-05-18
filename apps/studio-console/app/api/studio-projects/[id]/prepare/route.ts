/**
 * POST /api/studio-projects/[id]/prepare
 *
 * RC-5A.12.5A Runtime Project Bootstrap. Kicks off the background prepare job
 * only; it does not run `agent-studio autonomous start`.
 */

import { NextResponse } from "next/server";
import { startPrepareJob } from "@/lib/prepareRuntime";

export const dynamic = "force-dynamic";

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || id.includes("/") || id.includes("\\") || id.includes("..")) {
    return NextResponse.json({ ok: false, error: "invalid project id" }, { status: 400 });
  }

  try {
    const result = await startPrepareJob(id);
    if (!result.ok) {
      const status = result.error.includes("not found") ? 404 : 400;
      return NextResponse.json(result, { status });
    }
    return NextResponse.json(result, { status: 202 });
  } catch (exc) {
    return NextResponse.json(
      { ok: false, error: "failed to start prepare job", detail: String(exc) },
      { status: 500 },
    );
  }
}
