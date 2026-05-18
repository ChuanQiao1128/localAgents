/**
 * GET /api/artifact?path=<path>
 *
 * Reads any single file inside the path allowlist (lib/paths.ts). Path
 * may be absolute or relative-to-workspace-root. Rejects:
 *   - missing `path` query → 400
 *   - path outside allowlist → 400
 *   - path that resolves to a non-file → 400
 *   - file > 5 MB → 413
 *   - file not found → 404
 *
 * Binary content (null byte in first 8KB) returned as base64 with
 *   `encoding: "base64"`. Text returned as utf-8.
 */

import { NextResponse } from "next/server";
import { ArtifactReadError, readArtifact } from "@/lib/artifact";

export const dynamic = "force-dynamic";

export async function GET(req: Request) {
  const url = new URL(req.url);
  const inputPath = url.searchParams.get("path") ?? "";

  try {
    const payload = await readArtifact(inputPath);
    return NextResponse.json(payload);
  } catch (exc) {
    if (exc instanceof ArtifactReadError) {
      return NextResponse.json(
        { error: exc.message },
        { status: exc.status },
      );
    }
    return NextResponse.json(
      { error: "failed to read artifact", detail: String(exc) },
      { status: 500 },
    );
  }
}
