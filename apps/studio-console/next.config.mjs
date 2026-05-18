import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // RC-5A: pin the file-tracing root to this app dir so Next doesn't auto-
  // detect the repo's outer package-lock.json (the agent-studio Python repo
  // happens to have a Node lockfile too). Avoids the "We detected multiple
  // lockfiles" warning on every build.
  outputFileTracingRoot: __dirname,
  // The Console reads workspace files via server-side API routes; static
  // export is intentionally out of scope.
};

export default nextConfig;
