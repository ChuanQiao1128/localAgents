/**
 * GET  /api/studio-projects/[id]/product-review —— load latest review.
 * POST /api/studio-projects/[id]/product-review —— run deterministic product review
 * and generate scoped Change Request drafts.
 */

import { NextResponse } from "next/server";
import {
  loadProductReview,
  runStudioProductReview,
} from "@/lib/productReview";

export const dynamic = "force-dynamic";

function invalidId(id: string): boolean {
  return !id || id.includes("/") || id.includes("\\") || id.includes("..");
}

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (invalidId(id)) {
    return NextResponse.json({ ok: false, error: "invalid project id" }, { status: 400 });
  }
  try {
    const review = await loadProductReview(id);
    return NextResponse.json({ ok: true, review });
  } catch (exc) {
    return NextResponse.json(
      { ok: false, error: String(exc instanceof Error ? exc.message : exc) },
      { status: 500 },
    );
  }
}

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (invalidId(id)) {
    return NextResponse.json({ ok: false, error: "invalid project id" }, { status: 400 });
  }
  try {
    const review = await runStudioProductReview(id);
    return NextResponse.json({ ok: true, review });
  } catch (exc) {
    return NextResponse.json(
      { ok: false, error: String(exc instanceof Error ? exc.message : exc) },
      { status: 500 },
    );
  }
}
