// Runtime projects are executed from their own project root. Keep Next's trace
// root pinned to that cwd so it does not infer the parent LocalAgents workspace
// and warn about additional lockfiles above the generated app.

/** @type {import('next').NextConfig} */
const nextConfig = {
  outputFileTracingRoot: process.cwd(),
};

export default nextConfig;
