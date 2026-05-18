/**
 * GET  /api/contracts        — list contracts under .studio-console/contracts/.
 * POST /api/contracts        — create a fresh contract dir with default templates.
 *
 * No DELETE in v1 — see locked spec § 11.
 */

import { NextResponse } from "next/server";
import { createContract, listContracts } from "@/lib/contracts";

export const dynamic = "force-dynamic";

export async function GET() {
  try {
    const contracts = await listContracts();
    return NextResponse.json({ contracts });
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to list contracts", detail: String(exc) },
      { status: 500 },
    );
  }
}

export async function POST() {
  try {
    const id = await createContract();
    return NextResponse.json({ id }, { status: 201 });
  } catch (exc) {
    return NextResponse.json(
      { error: "failed to create contract", detail: String(exc) },
      { status: 500 },
    );
  }
}
