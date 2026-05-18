/**
 * POST /api/studio-projects/[id]/stop
 *
 * Stops only the child process pid recorded by the Studio run manager.
 */

import { NextResponse } from "next/server";
import { stopDevelopmentJob } from "@/lib/developmentRun";

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
    const result = await stopDevelopmentJob(id);
    if (!result.ok) {
      const status = result.error.includes("not found") ? 404 : 400;
      return NextResponse.json(result, { status });
    }
    return NextResponse.json(result);
  } catch (exc) {
    return NextResponse.json(
      { ok: false, error: "failed to stop development run", detail: String(exc) },
      { status: 500 },
    );
  }
}
