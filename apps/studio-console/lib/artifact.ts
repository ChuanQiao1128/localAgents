/**
 * Safe artifact reader — every read goes through `assertReadable`, with a
 * size cap and binary-content detection on top.
 *
 * Locked spec: docs/STUDIO_CONSOLE_SPEC.md § 12 (path allowlist) +
 *              `lib/paths.ts` (the actual security primitive).
 */

import fs from "node:fs/promises";
import path from "node:path";
import { assertReadable, relToWorkspace } from "./paths";

/** 5 MB cap — refuse anything larger. Catches accidental binary loads. */
const MAX_READ_BYTES = 5 * 1024 * 1024;

export type ArtifactPayload = {
  path: string;
  relPath: string;
  basename: string;
  extension: string;
  size: number;
  encoding: "utf-8" | "base64";
  /** UTF-8 text OR base64-encoded bytes if the file is binary. */
  content: string;
};

export class ArtifactReadError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ArtifactReadError";
  }
}

/**
 * Resolve `input` (absolute or relative to workspace root), enforce the
 * path allowlist, then read the file. Throws ArtifactReadError on any
 * security or size violation; caller maps to HTTP status.
 */
export async function readArtifact(input: string): Promise<ArtifactPayload> {
  if (!input || typeof input !== "string") {
    throw new ArtifactReadError(400, "missing path");
  }
  let absPath: string;
  try {
    absPath = assertReadable(input);
  } catch (exc) {
    throw new ArtifactReadError(400, `path not allowed: ${(exc as Error).message}`);
  }

  let stat;
  try {
    stat = await fs.stat(absPath);
  } catch (exc) {
    throw new ArtifactReadError(404, `file not found: ${(exc as Error).message}`);
  }
  if (!stat.isFile()) {
    throw new ArtifactReadError(400, `not a regular file: ${absPath}`);
  }
  if (stat.size > MAX_READ_BYTES) {
    throw new ArtifactReadError(
      413,
      `file too large: ${stat.size} bytes (max ${MAX_READ_BYTES})`,
    );
  }

  const buf = await fs.readFile(absPath);
  const encoding: "utf-8" | "base64" = isBinary(buf) ? "base64" : "utf-8";
  const content = encoding === "utf-8" ? buf.toString("utf-8") : buf.toString("base64");

  return {
    path: absPath,
    relPath: relToWorkspace(absPath),
    basename: path.basename(absPath),
    extension: path.extname(absPath),
    size: stat.size,
    encoding,
    content,
  };
}

/**
 * Quick-and-dirty binary detection: a null byte in the first 8KB ⇒ binary.
 * Catches images, archives, compiled artifacts. False positives are rare
 * for the artifact set the Console actually serves (markdown / JSON / .ts).
 */
function isBinary(buf: Buffer, sample = 8192): boolean {
  const len = Math.min(buf.length, sample);
  for (let i = 0; i < len; i++) {
    if (buf[i] === 0) return true;
  }
  return false;
}
