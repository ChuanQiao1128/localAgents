/**
 * GET /api/contracts/[id]    — read all 6 files + lock state + lock-precondition errors.
 * PUT /api/contracts/[id]    — update one of the 6 allowed files.
 *                              Body: { file: ContractFileName, content: string }
 *                              When file === "lock.json" with locked=true, server-side
 *                              re-checks lock preconditions and rejects with 400 + errors[].
 *
 * No DELETE in v1.
 */

import { NextResponse } from "next/server";
import {
  CONTRACT_FILE_NAMES,
  loadContract,
  updateContractFile,
  type ContractFileName,
} from "@/lib/contracts";

export const dynamic = "force-dynamic";

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || id.includes("/") || id.includes("\\") || id.includes("..")) {
    return NextResponse.json({ error: "invalid contract id" }, { status: 400 });
  }
  try {
    const contract = await loadContract(id);
    if (!contract) {
      return NextResponse.json(
        { error: `contract not found: ${id}` },
        { status: 404 },
      );
    }
    return NextResponse.json(contract);
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to load contract", detail: String(exc) },
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
    return NextResponse.json({ error: "invalid contract id" }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "body must be valid JSON" }, { status: 400 });
  }

  if (!body || typeof body !== "object") {
    return NextResponse.json(
      { error: "body must be an object with `file` and `content`" },
      { status: 400 },
    );
  }
  const { file, content } = body as { file?: unknown; content?: unknown };
  if (typeof file !== "string" || typeof content !== "string") {
    return NextResponse.json(
      { error: "body.file (string) and body.content (string) are required" },
      { status: 400 },
    );
  }
  if (!(CONTRACT_FILE_NAMES as readonly string[]).includes(file)) {
    return NextResponse.json(
      {
        error: `file not allowed: ${file}`,
        allowed: CONTRACT_FILE_NAMES,
      },
      { status: 400 },
    );
  }

  try {
    const result = await updateContractFile(
      id,
      file as ContractFileName,
      content,
    );
    if (!result.ok) {
      return NextResponse.json(
        { error: "lock preconditions not met", errors: result.errors },
        { status: 400 },
      );
    }
    return NextResponse.json({
      ok: true,
      file,
      lockState: result.lockState,
    });
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to update contract file", detail: String(exc) },
      { status: 500 },
    );
  }
}
