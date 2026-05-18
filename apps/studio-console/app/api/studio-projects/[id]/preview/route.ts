import { NextResponse } from "next/server";
import {
  readPreviewStatus,
  startPreviewServer,
  stopPreviewServer,
} from "@/lib/previewRun";

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
    return NextResponse.json({ error: "invalid project id" }, { status: 400 });
  }

  try {
    return NextResponse.json(await readPreviewStatus(id, { tailLines: 80 }));
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to read preview status", detail: String(exc) },
      { status: 500 },
    );
  }
}

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (invalidId(id)) {
    return NextResponse.json({ ok: false, error: "invalid project id" }, { status: 400 });
  }

  try {
    const url = new URL(req.url);
    const result = await startPreviewServer(id, {
      restart: url.searchParams.get("restart") === "1",
    });
    return NextResponse.json(result, { status: result.ok ? 202 : 400 });
  } catch (exc) {
    return NextResponse.json(
      { ok: false, error: "failed to start preview", detail: String(exc) },
      { status: 500 },
    );
  }
}

export async function DELETE(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (invalidId(id)) {
    return NextResponse.json({ ok: false, error: "invalid project id" }, { status: 400 });
  }

  try {
    const result = await stopPreviewServer(id);
    return NextResponse.json(result, { status: result.ok ? 200 : 400 });
  } catch (exc) {
    return NextResponse.json(
      { ok: false, error: "failed to stop preview", detail: String(exc) },
      { status: 500 },
    );
  }
}
