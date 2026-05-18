/**
 * POST /api/studio-projects/[id]/start
 *
 * RC-5A.12.5B Start Development Run Manager. Starts the hardcoded
 * `agent-studio autonomous start` path for an already-linked runtime project.
 */

import { NextResponse } from "next/server";
import { startDevelopmentJob } from "@/lib/developmentRun";

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
    const result = await startDevelopmentJob(id);
    if (!result.ok) {
      const status = result.error.includes("not found") ? 404 : 400;
      return NextResponse.json(result, { status });
    }
    return NextResponse.json(result, { status: 202 });
  } catch (exc) {
    return NextResponse.json(
      { ok: false, error: "failed to start development run", detail: String(exc) },
      { status: 500 },
    );
  }
}
