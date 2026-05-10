// RC-2A dogfood build. Deterministic; no framework install needed.
// Reads src/*.html (and src/*.js if present), assembles a tiny dist/index.html,
// asserts the source contains the load-bearing strings the requirements
// promise. Exits non-zero on any failure so the autonomous integration
// runner sees a real `npm run build` outcome.

import { readdirSync, readFileSync, mkdirSync, writeFileSync, existsSync } from "node:fs";
import { join, resolve } from "node:path";

const ROOT = resolve(import.meta.dirname, "..");
const SRC = join(ROOT, "src");
const DIST = join(ROOT, "dist");

function fail(message) {
  console.error(`build: ${message}`);
  process.exit(1);
}

if (!existsSync(SRC)) {
  fail(`src/ directory missing at ${SRC}`);
}

const sourceFiles = readdirSync(SRC, { withFileTypes: true })
  .filter((d) => d.isFile())
  .map((d) => d.name)
  .sort();

if (sourceFiles.length === 0) {
  fail(`src/ contains no files`);
}

const indexCandidates = sourceFiles.filter((f) => f === "index.html");
if (indexCandidates.length === 0) {
  fail(`src/index.html is missing — that is the page the build assembles`);
}

const indexBody = readFileSync(join(SRC, "index.html"), "utf8");

// Acceptance signals from requirements.md — the build refuses to ship
// a page that doesn't carry the contract.
const required = [
  "Creator Project Tracker",     // page title
  "project-list",                // identifier the list mounts under
];
for (const needle of required) {
  if (!indexBody.includes(needle)) {
    fail(`src/index.html is missing required string: ${JSON.stringify(needle)}`);
  }
}

mkdirSync(DIST, { recursive: true });
writeFileSync(join(DIST, "index.html"), indexBody, "utf8");

// Echo a small summary so the integration log is informative.
console.log(`build: assembled dist/index.html (${indexBody.length} bytes)`);
console.log(`build: source files = ${JSON.stringify(sourceFiles)}`);
