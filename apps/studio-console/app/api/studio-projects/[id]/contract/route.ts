/**
 * PUT /api/studio-projects/[id]/contract  —— 更新合同的某个文件。
 *
 * Body: { file: ContractFileName, content: string }
 *
 * 当 file === "lock.json" 且 locked = true 时，服务端会再次校验前置条件
 * 并自动盖时间戳；不满足直接 400 + errors[]。
 */

import { NextResponse } from "next/server";
import {
  CONTRACT_FILE_NAMES,
  updateContractFile,
  type ContractFileName,
} from "@/lib/studioProjects";

export const dynamic = "force-dynamic";

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  if (!id || id.includes("/") || id.includes("\\") || id.includes("..")) {
    return NextResponse.json({ error: "invalid project id" }, { status: 400 });
  }

  let body: unknown;
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: "body must be valid JSON" }, { status: 400 });
  }
  if (!body || typeof body !== "object") {
    return NextResponse.json(
      { error: "body must be { file, content }" },
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
      { error: `file not allowed: ${file}`, allowed: CONTRACT_FILE_NAMES },
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
        { error: "锁定前置条件未满足", errors: result.errors },
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
