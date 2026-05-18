/**
 * GET /api/studio-projects/[id]/run
 *
 * Reads the latest Studio Console prepare run for a Studio project, including
 * stdout/stderr tails for the Develop tab.
 */

import { NextResponse } from "next/server";
import { readLatestDevelopmentRunStatus } from "@/lib/developmentRun";
import { readLatestRunStatus } from "@/lib/prepareRuntime";
import { loadStudioProjectSummary } from "@/lib/studioProjects";

export const dynamic = "force-dynamic";

export async function GET(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || id.includes("/") || id.includes("\\") || id.includes("..")) {
    return NextResponse.json({ error: "invalid project id" }, { status: 400 });
  }

  try {
    const summary = await loadStudioProjectSummary(id);
    if (!summary) {
      return NextResponse.json(
        { error: `studio project not found: ${id}` },
        { status: 404 },
      );
    }
    const url = new URL(req.url);
    const kind = url.searchParams.get("kind");
    if (kind === "development" || kind === "autonomous") {
      if (!summary.agentProjectId || !summary.agentProjectPath) {
        return NextResponse.json({ status: null, stdoutTail: "", stderrTail: "" });
      }
      const latest = await readLatestDevelopmentRunStatus(summary.path, { tailLines: 80 });
      if (
        latest?.status &&
        (latest.status.agentProjectId !== summary.agentProjectId ||
          latest.status.agentProjectPath !== summary.agentProjectPath)
      ) {
        return NextResponse.json({ status: null, stdoutTail: "", stderrTail: "" });
      }
      return NextResponse.json(
        latest ?? { status: null, stdoutTail: "", stderrTail: "" },
      );
    }
    const latest = await readLatestRunStatus(summary.path, { tailLines: 80 });
    if (latest?.status) {
      const status = latest.status;
      const activePrepare = status.state === "queued" || status.state === "running";
      if (!summary.agentProjectId || !summary.agentProjectPath) {
        if (!activePrepare) {
          return NextResponse.json({ status: null, stdoutTail: "", stderrTail: "" });
        }
      } else if (
        status.agentProjectId &&
        status.agentProjectPath &&
        (status.agentProjectId !== summary.agentProjectId ||
          status.agentProjectPath !== summary.agentProjectPath)
      ) {
        return NextResponse.json({ status: null, stdoutTail: "", stderrTail: "" });
      }
    }
    return NextResponse.json(
      latest ?? { status: null, stdoutTail: "", stderrTail: "" },
    );
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to load project run", detail: String(exc) },
      { status: 500 },
    );
  }
}
