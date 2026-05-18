/**
 * GET /api/projects — list projects under .agent-studio/projects/.
 *
 * Returns:
 *   { projects: ProjectSummary[], workspaceRoot: string }
 *
 * Empty list (not 404) when no projects exist yet — that's the cold-clone
 * case the Dashboard handles gracefully.
 */

import { NextResponse } from "next/server";
import { listProjects } from "@/lib/projects";
import { workspaceRoot } from "@/lib/paths";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const projects = await listProjects();
    return NextResponse.json({
      projects,
      workspaceRoot: workspaceRoot(),
    });
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to list projects", detail: String(exc) },
      { status: 500 },
    );
  }
}
