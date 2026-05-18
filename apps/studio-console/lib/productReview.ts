import fs from "node:fs/promises";
import path from "node:path";
import {
  assertReadable,
  relToWorkspace,
  studioProjectsRoot,
  workspaceRoot,
} from "./paths";
import {
  loadStudioProjectDetail,
  newChangeId,
} from "./studioProjects";

export type ProductReviewSeverity = "critical" | "high" | "medium" | "low";
export type ProductReviewFindingStatus = "open" | "resolved" | "not_applicable";
export type ProductReviewVerdict =
  | "pass"
  | "pass_with_recommendations"
  | "needs_work"
  | "unsafe"
  // Kept for old persisted reviews.
  | "needs_change_plan"
  | "blocked";

export type ProductReviewFinding = {
  id: string;
  severity: ProductReviewSeverity;
  status: ProductReviewFindingStatus;
  title: string;
  evidence: string[];
  recommendation: string;
  generatedChangeId?: string | null;
};

export type ProductReviewRecommendedChange = {
  id: string;
  title: string;
  priority: "P0" | "P1" | "P2";
  rationale: string;
  draftId: string;
  changeRequestPath: string;
  risk: "low" | "medium" | "high";
  estimatedDifficulty: "S" | "M" | "L";
  dependencies: string[];
};

export type ProductReviewResult = {
  schema_version: "studio.product_review.v2";
  studio_project_id: string;
  runtime_project_id: string | null;
  projectId: string;
  projectName: string;
  reviewId: string;
  createdAt: string;
  score: number;
  maxScore: 100;
  verdict: ProductReviewVerdict;
  summary: string;
  context: {
    runtimeLinked: boolean;
    agentProjectId: string | null;
    agentProjectPath: string | null;
    latestDeliveredChangeId: string | null;
    latestDeliveredSha: string | null;
    providerMode: string | null;
  };
  findings: ProductReviewFinding[];
  recommendedChanges: ProductReviewRecommendedChange[];
  inputs_read: string[];
  artifacts: {
    reviewMd: string;
    reviewJson: string;
    changePlanMd: string;
  };
};

type DraftPlan = {
  id: "CR-D" | "CR-E" | "CR-F";
  title: string;
  priority: "P0" | "P1" | "P2";
  rationale: string;
  markdown: string;
  risk: "low" | "medium" | "high";
  estimatedDifficulty: "S" | "M" | "L";
  dependencies: string[];
};

type RuntimeSnapshot = {
  text: string;
  files: string[];
  inputsRead: string[];
};

const PRODUCT_REVIEWS_DIR = "product-reviews";
const LEGACY_PRODUCT_REVIEW_DIR = "product-review";
const SOURCE_DIRS = ["app", "components", "lib", "docs"] as const;
const SOURCE_EXTENSIONS = new Set([
  ".ts",
  ".tsx",
  ".js",
  ".jsx",
  ".md",
  ".json",
]);
const MAX_FILES = 90;
const MAX_FILE_CHARS = 30_000;
const MAX_TOTAL_CHARS = 240_000;

export async function loadProductReview(
  projectId: string,
): Promise<ProductReviewResult | null> {
  assertSafeProjectId(projectId);
  const projectDir = path.join(studioProjectsRoot(), projectId);
  const latestPath = path.join(projectDir, PRODUCT_REVIEWS_DIR, "latest.json");
  try {
    assertReadable(latestPath);
    const pointer = JSON.parse(await fs.readFile(latestPath, "utf-8")) as {
      reviewId?: unknown;
    };
    if (typeof pointer.reviewId === "string") {
      const reviewJsonPath = path.join(
        projectDir,
        PRODUCT_REVIEWS_DIR,
        pointer.reviewId,
        "product-review.json",
      );
      assertReadable(reviewJsonPath);
      return JSON.parse(await fs.readFile(reviewJsonPath, "utf-8")) as ProductReviewResult;
    }
  } catch {
    // Fall through to legacy single-review location.
  }

  const legacyReviewJsonPath = path.join(
    projectDir,
    LEGACY_PRODUCT_REVIEW_DIR,
    "product-review.json",
  );
  try {
    assertReadable(legacyReviewJsonPath);
    return JSON.parse(await fs.readFile(legacyReviewJsonPath, "utf-8")) as ProductReviewResult;
  } catch {
    return null;
  }
}

export async function runStudioProductReview(
  projectId: string,
): Promise<ProductReviewResult> {
  assertSafeProjectId(projectId);
  const detail = await loadStudioProjectDetail(projectId);
  if (!detail) throw new Error(`Studio project not found: ${projectId}`);

  const reviewId = `product_review_${new Date()
    .toISOString()
    .replace(/[-:.]/g, "")
    .slice(0, 15)}`;
  const createdAt = new Date().toISOString();
  const reviewDir = path.join(detail.path, PRODUCT_REVIEWS_DIR, reviewId);
  assertReadable(detail.path);
  await fs.mkdir(reviewDir, { recursive: true });

  const latestDelivered = latestDeliveredChange(detail.runDetail?.changes ?? []);
  const runtimeSnapshot = await loadRuntimeSnapshot(detail.agentProjectPath, {
    studioProjectDir: detail.path,
    contractFiles: detail.files,
    latestDelivered,
  });
  const findings = evaluateProduct(detail.id, runtimeSnapshot, {
    providerMode: detail.providerReadiness?.currentMode ?? null,
    runtimeLinked: Boolean(detail.agentProjectId && detail.agentProjectPath),
  });
  const plans = buildDraftPlans(detail.id, detail.name, findings);
  const recommendedChanges: ProductReviewRecommendedChange[] = [];
  for (const plan of plans) {
    const draft = await upsertReviewDraft(projectId, plan, reviewId);
    for (const finding of findings) {
      if (
        finding.status === "open" &&
        generatedPlanMatchesFinding(plan.id, finding.id)
      ) {
        finding.generatedChangeId = draft.draftId;
      }
    }
    recommendedChanges.push({
      id: plan.id,
      title: plan.title,
      priority: plan.priority,
      rationale: plan.rationale,
      draftId: draft.draftId,
      changeRequestPath: draft.changeRequestPath,
      risk: plan.risk,
      estimatedDifficulty: plan.estimatedDifficulty,
      dependencies: plan.dependencies,
    });
  }

  const score = deriveScore(findings, detail.agentProjectPath !== null);
  const verdict = deriveVerdict(findings, Boolean(detail.agentProjectPath && detail.agentProjectId));
  const summary = summarize(verdict, findings, recommendedChanges);

  const resultWithoutArtifacts = {
    schema_version: "studio.product_review.v2" as const,
    studio_project_id: detail.id,
    runtime_project_id: detail.agentProjectId,
    projectId: detail.id,
    projectName: detail.name,
    reviewId,
    createdAt,
    score,
    maxScore: 100 as const,
    verdict,
    summary,
    context: {
      runtimeLinked: Boolean(detail.agentProjectId && detail.agentProjectPath),
      agentProjectId: detail.agentProjectId,
      agentProjectPath: detail.agentProjectPath,
      latestDeliveredChangeId: latestDelivered?.changeId ?? null,
      latestDeliveredSha: latestDelivered?.sha ?? detail.latestDeliveredSha ?? null,
      providerMode: detail.providerReadiness?.currentMode ?? null,
    },
    findings,
    recommendedChanges,
    inputs_read: runtimeSnapshot.inputsRead,
  };

  const reviewMdPath = path.join(reviewDir, "product-review.md");
  const reviewJsonPath = path.join(reviewDir, "product-review.json");
  const changePlanPath = path.join(reviewDir, "prioritized-change-plan.md");
  const artifacts = {
    reviewMd: relToWorkspace(reviewMdPath),
    reviewJson: relToWorkspace(reviewJsonPath),
    changePlanMd: relToWorkspace(changePlanPath),
  };
  const result: ProductReviewResult = {
    ...resultWithoutArtifacts,
    artifacts,
  };

  await fs.writeFile(reviewMdPath, renderReviewMarkdown(result), "utf-8");
  await fs.writeFile(changePlanPath, renderChangePlanMarkdown(result), "utf-8");
  await fs.writeFile(reviewJsonPath, JSON.stringify(result, null, 2), "utf-8");
  await fs.writeFile(
    path.join(detail.path, PRODUCT_REVIEWS_DIR, "latest.json"),
    JSON.stringify({ reviewId, reviewJson: artifacts.reviewJson, createdAt }, null, 2),
    "utf-8",
  );
  await bumpProjectUpdatedAt(projectId);
  return result;
}

function assertSafeProjectId(projectId: string) {
  if (
    !projectId ||
    projectId.includes("/") ||
    projectId.includes("\\") ||
    projectId.includes("..")
  ) {
    throw new Error("invalid project id");
  }
}

async function loadRuntimeSnapshot(
  agentProjectPath: string | null,
  opts: {
    studioProjectDir: string;
    contractFiles: Record<string, string>;
    latestDelivered: { changeId: string; sha: string | null; appliedAt: string | null } | null;
  },
): Promise<RuntimeSnapshot> {
  const chunks: string[] = [];
  const files: string[] = [];
  const inputsRead: string[] = [];

  for (const [name, content] of Object.entries(opts.contractFiles)) {
    inputsRead.push(relToWorkspace(path.join(opts.studioProjectDir, "contract", name)));
    chunks.push(`--- studio-contract/${name} ---\n${content.slice(0, MAX_FILE_CHARS)}`);
  }

  await appendIfReadable(
    path.join(opts.studioProjectDir, "preview", "status.json"),
    chunks,
    inputsRead,
    "studio-preview/status.json",
  );

  if (!agentProjectPath) return { text: chunks.join("\n\n"), files: [] , inputsRead };
  const runtimeRoot = path.resolve(workspaceRoot(), agentProjectPath);
  assertReadable(runtimeRoot);

  if (opts.latestDelivered) {
    const latestChangeDir = path.join(
      runtimeRoot,
      ".agent",
      "changes",
      opts.latestDelivered.changeId,
    );
    await appendIfReadable(
      path.join(latestChangeDir, "delivery-report.md"),
      chunks,
      inputsRead,
      `.agent/changes/${opts.latestDelivered.changeId}/delivery-report.md`,
    );
    await appendIfReadable(
      path.join(latestChangeDir, "applied-change.json"),
      chunks,
      inputsRead,
      `.agent/changes/${opts.latestDelivered.changeId}/applied-change.json`,
    );
  }

  for (const dirName of SOURCE_DIRS) {
    const absDir = path.join(runtimeRoot, dirName);
    await collectSourceFiles(runtimeRoot, absDir, chunks, files, inputsRead);
    if (files.length >= MAX_FILES || chunks.join("\n").length >= MAX_TOTAL_CHARS) break;
  }
  return { text: chunks.join("\n\n"), files, inputsRead };
}

async function appendIfReadable(
  absPath: string,
  chunks: string[],
  inputsRead: string[],
  label: string,
): Promise<void> {
  try {
    if (path.basename(absPath).startsWith(".env")) return;
    assertReadable(absPath);
    const text = await fs.readFile(absPath, "utf-8");
    inputsRead.push(label.startsWith(".") ? label : relToWorkspace(absPath));
    chunks.push(`--- ${label} ---\n${text.slice(0, MAX_FILE_CHARS)}`);
  } catch {
    // Optional evidence source.
  }
}

async function collectSourceFiles(
  runtimeRoot: string,
  currentDir: string,
  chunks: string[],
  files: string[],
  inputsRead: string[],
): Promise<void> {
  if (files.length >= MAX_FILES || chunks.join("\n").length >= MAX_TOTAL_CHARS) return;
  let entries: Array<{ name: string; isDirectory: () => boolean; isFile: () => boolean }>;
  try {
    assertReadable(currentDir);
    entries = await fs.readdir(currentDir, { withFileTypes: true });
  } catch {
    return;
  }
  for (const entry of entries) {
    if (files.length >= MAX_FILES || chunks.join("\n").length >= MAX_TOTAL_CHARS) return;
    if (entry.name.startsWith(".")) continue;
    if (entry.name.startsWith(".env")) continue;
    if (["node_modules", ".next", ".git", "coverage"].includes(entry.name)) continue;
    const absPath = path.join(currentDir, entry.name);
    if (entry.isDirectory()) {
      await collectSourceFiles(runtimeRoot, absPath, chunks, files, inputsRead);
      continue;
    }
    if (!entry.isFile()) continue;
    const ext = path.extname(entry.name);
    if (!SOURCE_EXTENSIONS.has(ext)) continue;
    try {
      assertReadable(absPath);
      const rel = path.relative(runtimeRoot, absPath);
      const text = await fs.readFile(absPath, "utf-8");
      files.push(rel);
      inputsRead.push(rel);
      chunks.push(`--- ${rel} ---\n${text.slice(0, MAX_FILE_CHARS)}`);
    } catch {
      // Ignore unreadable source files; review remains best-effort.
    }
  }
}

function evaluateProduct(
  projectId: string,
  snapshot: RuntimeSnapshot,
  opts: { providerMode: string | null; runtimeLinked: boolean },
): ProductReviewFinding[] {
  const text = snapshot.text.toLowerCase();
  const findings: ProductReviewFinding[] = [];
  const isNaturalizer = projectId.includes("naturalizer") || text.includes("naturalizer");

  if (snapshot.files.length === 0) {
    findings.push(makeFinding({
      id: "GEN-001",
      severity: "critical",
      status: "open",
      title: "Runtime source context is missing",
      evidence: [
        "Studio could not read app/components/lib/docs source files from the linked runtime project.",
      ],
      recommendation:
        "Link or prepare the runtime project before asking Studio to generate product change requests.",
    }));
    return findings;
  }

  if (!isNaturalizer) {
    findings.push(makeFinding({
      id: "GEN-002",
      severity: "medium",
      status: "open",
      title: "Generic product review only",
      evidence: [
        "No specialized product rubric matched this project, so Studio produced a minimal delivery review.",
      ],
      recommendation:
        "Add a project-specific product rubric before relying on automated change planning.",
    }));
    return findings;
  }

  const detectorScoreAsSuccess =
    text.includes("selected detector score") ||
    text.includes("score delta") ||
    text.includes("detector score as success") ||
    text.includes("detector score is the success");
  const referenceSignalLanguage =
    text.includes("third-party reference signal") ||
    (text.includes("reference signal") &&
      (text.includes("reference only") || text.includes("not optimized")));
  findings.push(makeFinding({
    id: "NAT-001",
    severity: detectorScoreAsSuccess && !referenceSignalLanguage ? "high" : "low",
    status: detectorScoreAsSuccess && !referenceSignalLanguage ? "open" : "resolved",
    title: "Detector score is not treated as the success metric",
    evidence: detectorScoreAsSuccess
      ? [
          "Source still contains detector-score-as-success wording such as selected detector score or score delta.",
        ]
      : [
          "Studio did not find prominent detector-score-as-success labels in the reviewed source.",
        ],
    recommendation:
      "Keep detector output informational and never use score reduction as a product success gate.",
  }));

  findings.push(makeFinding({
    id: "NAT-002",
    severity: referenceSignalLanguage ? "low" : "high",
    status: referenceSignalLanguage ? "resolved" : "open",
    title: "Detector output is framed as a reference signal",
    evidence: referenceSignalLanguage
      ? [
          "Reviewed source includes reference-signal or reference-only wording for detector output.",
        ]
      : [
          "Reviewed source does not clearly frame detector output as a third-party reference signal.",
        ],
    recommendation:
      "Use labels such as Third-party reference signal and Reference signal change in user-facing UI.",
  }));

  const hasScoreIncreaseWarning =
    (text.includes("reference score increased") || text.includes("reference signal increased")) &&
    (text.includes("add more user context") || text.includes("verify claims"));
  findings.push(makeFinding({
    id: "NAT-003",
    severity: hasScoreIncreaseWarning ? "low" : "medium",
    status: hasScoreIncreaseWarning ? "resolved" : "open",
    title: "Score increase has a clear user warning",
    evidence: hasScoreIncreaseWarning
      ? [
          "Reviewed source warns users to add more context or verify claims when the reference score increases.",
        ]
      : [
          "Studio did not find a warning for the case where rewritten reference score is higher than original.",
        ],
    recommendation:
      "When rewritten score is higher than original, tell users to add context, verify claims, or manually edit before use.",
  }));

  const bypassTermsPresent =
    text.includes("bypass") || text.includes("evasion") || text.includes("evade");
  const bypassDisclaimed =
    text.includes("does not claim to bypass") ||
    text.includes("does not measure success by detector evasion") ||
    text.includes("not optimized against");
  findings.push(makeFinding({
    id: "NAT-004",
    severity: bypassTermsPresent && !bypassDisclaimed ? "high" : "low",
    status: bypassTermsPresent && !bypassDisclaimed ? "open" : "resolved",
    title: "Bypass or evasion framing is avoided",
    evidence: bypassTermsPresent && !bypassDisclaimed
      ? ["Bypass/evasion wording appears without a clear refusal or disclaimer."]
      : ["Bypass/evasion risk is either absent or explicitly disclaimed."],
    recommendation:
      "Avoid detector-bypass framing; state that detector outputs are reference signals only.",
  }));

  const hasAntiFabrication =
    text.includes("claim_not_in_source") ||
    (text.includes("fabricat") && text.includes("source")) ||
    (text.includes("introduced") && text.includes("claim"));
  findings.push(makeFinding({
    id: "NAT-005",
    severity: hasAntiFabrication ? "low" : "high",
    status: hasAntiFabrication ? "resolved" : "open",
    title: "Anti-fabrication guardrail is present",
    evidence: hasAntiFabrication
      ? ["Reviewed source includes anti-fabrication or unsupported-claim checks."]
      : [
          "Studio did not find a post-hoc claim check or explicit rewrite guardrail for newly introduced facts.",
        ],
    recommendation:
      "Treat rewrite output as untrusted and flag newly introduced claims for user verification.",
  }));

  const hasContextFields =
    text.includes("audience") &&
    text.includes("purpose") &&
    (text.includes("preserve") ||
      text.includes("preservedfacts") ||
      text.includes("facts or claims") ||
      text.includes("must keep")) &&
    (text.includes("actually did") ||
      text.includes("actualwork") ||
      text.includes("actual work") ||
      text.includes("what actually happened") ||
      text.includes("what i did")) &&
    (text.includes("constraintstonenotes") ||
      text.includes("constraints or tone") ||
      text.includes("tone notes"));
  findings.push(makeFinding({
    id: "NAT-006",
    severity: hasContextFields ? "low" : "medium",
    status: hasContextFields ? "resolved" : "open",
    title: "User context capture supports specificity without invention",
    evidence: hasContextFields
      ? [
          "Reviewed source includes audience, purpose, actual-work, and preserved-facts context fields.",
        ]
      : [
          "Studio did not find the full audience/purpose/actual-work/preserved-facts context shape.",
        ],
    recommendation:
      "Capture structured user context so the rewrite can become specific without inventing facts.",
  }));

  const providerModeClear =
    Boolean(opts.providerMode) ||
    text.includes("rewrite provider") ||
    text.includes("detector provider") ||
    text.includes("real provider") ||
    text.includes("mock") ||
    text.includes("codex cli");
  findings.push(makeFinding({
    id: "NAT-007",
    severity: providerModeClear ? "low" : "medium",
    status: providerModeClear ? "resolved" : "open",
    title: "Provider mode is visible without exposing secrets",
    evidence: providerModeClear
      ? ["Provider mode/readiness labels are available without secret values."]
      : ["Studio did not find clear provider mode labels for real/mock/fallback behavior."],
    recommendation:
      "Show real/mock/fallback provider mode, but never display secret values or endpoints.",
  }));

  const terminalStateClear =
    text.includes("one rewrite produces one report") ||
    text.includes("does not auto") ||
    text.includes("no automatic retry");
  const hasNextAction =
    terminalStateClear &&
    (text.includes("add context") ||
      text.includes("add more user context") ||
      text.includes("verify claims") ||
      text.includes("nextsuggestions"));
  findings.push(makeFinding({
    id: "NAT-008",
    severity: hasNextAction ? "low" : "medium",
    status: hasNextAction ? "resolved" : "open",
    title: "Next action is clear when reference signals remain high",
    evidence: hasNextAction
      ? [
          "Reviewed source includes terminal-state and next-action guidance instead of automatic retries.",
        ]
      : [
          "Studio did not find clear next-action guidance for high or increased reference signals.",
        ],
    recommendation:
      "Make one rewrite produce one report; if reference signals remain high, ask for context, verification, or manual edits.",
  }));

  return findings;
}

function makeFinding(finding: ProductReviewFinding): ProductReviewFinding {
  return {
    ...finding,
    generatedChangeId: finding.generatedChangeId ?? null,
  };
}

function deriveScore(findings: ProductReviewFinding[], runtimeLinked: boolean): number {
  if (!runtimeLinked) return 30;
  const penalty = findings.filter((finding) => finding.status === "open").reduce((sum, finding) => {
    if (finding.severity === "critical") return sum + 18;
    if (finding.severity === "high") return sum + 12;
    if (finding.severity === "medium") return sum + 7;
    return sum + 2;
  }, 0);
  return Math.max(0, Math.min(100, 100 - penalty));
}

function deriveVerdict(
  findings: ProductReviewFinding[],
  runtimeLinked: boolean,
): ProductReviewVerdict {
  if (!runtimeLinked) return "needs_work";
  const open = findings.filter((finding) => finding.status === "open");
  if (
    open.some(
      (finding) =>
        finding.severity === "critical" ||
        (finding.id === "NAT-004" && finding.severity === "high"),
    )
  ) {
    return "unsafe";
  }
  if (open.some((finding) => finding.severity === "high")) return "needs_work";
  if (open.length > 0) return "pass_with_recommendations";
  return "pass";
}

function summarize(
  verdict: ProductReviewResult["verdict"],
  findings: ProductReviewFinding[],
  changes: ProductReviewRecommendedChange[],
): string {
  const open = findings.filter((finding) => finding.status === "open");
  if (verdict === "blocked" || (verdict === "needs_work" && open.some((f) => f.id === "GEN-001"))) {
    return "Studio cannot produce reliable product changes until the runtime project is linked and readable.";
  }
  if (verdict === "unsafe") {
    return "Studio found a product-positioning or safety-boundary risk that should be resolved before demo or handoff.";
  }
  if (verdict === "pass") {
    return "No hard product blockers were found. Continue with polish or manual review.";
  }
  if (verdict === "pass_with_recommendations") {
    return `Product review passed with ${open.length} recommendation(s); Studio generated ${changes.length} scoped Change Request draft(s) where useful.`;
  }
  const critical = open.filter((f) => f.severity === "critical").length;
  const high = open.filter((f) => f.severity === "high").length;
  return `Studio found ${critical} critical and ${high} high-priority product issue(s), and generated ${changes.length} scoped Change Request draft(s).`;
}

function buildDraftPlans(
  projectId: string,
  projectName: string,
  findings: ProductReviewFinding[],
): DraftPlan[] {
  if (!projectId.includes("naturalizer")) return [];
  const openIds = new Set(
    findings.filter((finding) => finding.status === "open").map((finding) => finding.id),
  );
  const needsDetectorReframe =
    openIds.has("NAT-001") ||
    openIds.has("NAT-002") ||
    openIds.has("NAT-003") ||
    openIds.has("NAT-004");
  const needsVerification = openIds.has("NAT-005");
  const needsGuidance = openIds.has("NAT-003") || openIds.has("NAT-008");
  const plans: DraftPlan[] = [];
  if (needsDetectorReframe) {
    plans.push({
      id: "CR-D",
      title: "Product Review CR-D — Reframe detector as reference signal",
      priority: "P0",
      rationale:
        "Prevents users from reading detector output as the product's success metric or as detector-bypass optimization.",
      risk: "medium",
      estimatedDifficulty: "S",
      dependencies: [],
      markdown: renderDetectorReferenceChange(projectName),
    });
  }
  if (needsVerification) {
    plans.push({
      id: "CR-E",
      title: "Product Review CR-E — Improve rewrite result verification",
      priority: "P0",
      rationale:
        "Makes the rewrite output auditable by warning about unsupported claims before users rely on it.",
      risk: "high",
      estimatedDifficulty: "M",
      dependencies: [],
      markdown: renderVerificationChange(projectName),
    });
  }
  if (needsGuidance) {
    plans.push({
      id: "CR-F",
      title: "Product Review CR-F — Improve guidance when reference signal increases",
      priority: "P1",
      rationale:
        "Keeps the workflow controlled: one rewrite produces one report, then the user decides whether to add context or revise.",
      risk: "medium",
      estimatedDifficulty: "S",
      dependencies: [],
      markdown: renderReferenceSignalGuidanceChange(projectName),
    });
  }
  return plans;
}

function generatedPlanMatchesFinding(planId: DraftPlan["id"], findingId: string): boolean {
  if (planId === "CR-D") return ["NAT-001", "NAT-002", "NAT-003", "NAT-004"].includes(findingId);
  if (planId === "CR-E") return findingId === "NAT-005";
  if (planId === "CR-F") return ["NAT-003", "NAT-008"].includes(findingId);
  return false;
}

async function upsertReviewDraft(
  projectId: string,
  plan: DraftPlan,
  reviewId: string,
): Promise<{ draftId: string; changeRequestPath: string }> {
  const projectDir = path.join(studioProjectsRoot(), projectId);
  const changesDir = path.join(projectDir, "changes");
  assertReadable(projectDir);
  await fs.mkdir(changesDir, { recursive: true });
  let draftId = await findExistingReviewDraft(changesDir, plan.id);
  if (!draftId) {
    draftId = newChangeId();
    await fs.mkdir(path.join(changesDir, draftId));
  }
  const draftDir = path.join(changesDir, draftId);
  const now = new Date().toISOString();
  const meta = await readDraftMeta(draftDir, now);
  const nextMeta = {
    ...meta,
    title: plan.title,
    updatedAt: now,
    source: "product-review",
    productReviewId: reviewId,
    productReviewChangeId: plan.id,
    priority: plan.priority,
  };
  await fs.writeFile(path.join(draftDir, "change-request.md"), plan.markdown, "utf-8");
  await fs.writeFile(path.join(draftDir, "meta.json"), JSON.stringify(nextMeta, null, 2), "utf-8");
  return {
    draftId,
    changeRequestPath: relToWorkspace(path.join(draftDir, "change-request.md")),
  };
}

async function findExistingReviewDraft(
  changesDir: string,
  planId: string,
): Promise<string | null> {
  let entries: string[];
  try {
    entries = await fs.readdir(changesDir);
  } catch {
    return null;
  }
  for (const entry of entries) {
    if (!/^cr_[a-f0-9]{6,32}$/i.test(entry)) continue;
    try {
      const meta = JSON.parse(
        await fs.readFile(path.join(changesDir, entry, "meta.json"), "utf-8"),
      ) as Record<string, unknown>;
      if (
        meta.source === "product-review" &&
        meta.productReviewChangeId === planId
      ) {
        return entry;
      }
    } catch {
      // Ignore malformed old drafts.
    }
  }
  return null;
}

async function readDraftMeta(
  draftDir: string,
  now: string,
): Promise<Record<string, unknown>> {
  try {
    return JSON.parse(
      await fs.readFile(path.join(draftDir, "meta.json"), "utf-8"),
    ) as Record<string, unknown>;
  } catch {
    return { title: null, createdAt: now, updatedAt: now };
  }
}

async function bumpProjectUpdatedAt(projectId: string): Promise<void> {
  const projectJson = path.join(studioProjectsRoot(), projectId, "project.json");
  try {
    const raw = JSON.parse(await fs.readFile(projectJson, "utf-8")) as Record<string, unknown>;
    raw.updatedAt = new Date().toISOString();
    await fs.writeFile(projectJson, JSON.stringify(raw, null, 2), "utf-8");
  } catch {
    // Non-fatal; review artifacts already exist.
  }
}

function latestDeliveredChange(changes: Array<{ state: string; appliedAt: string | null; changeId: string; sha: string | null }>) {
  const delivered = changes.filter((change) => change.state === "delivered");
  return delivered.sort((a, b) => Date.parse(b.appliedAt ?? "") - Date.parse(a.appliedAt ?? ""))[0] ?? null;
}

function renderReviewMarkdown(result: ProductReviewResult): string {
  const openFindings = result.findings.filter((finding) => finding.status === "open");
  return [
    `# Product Review — ${result.projectName}`,
    "",
    `Schema: \`${result.schema_version}\``,
    `Review: \`${result.reviewId}\``,
    `Created: ${result.createdAt}`,
    `Score: ${result.score}/${result.maxScore}`,
    `Verdict: \`${result.verdict}\``,
    "",
    "## Summary",
    "",
    result.summary,
    "",
    "## Context",
    "",
    `- Studio project: \`${result.projectId}\``,
    `- Runtime project: \`${result.runtime_project_id ?? "n/a"}\``,
    `- Runtime linked: ${result.context.runtimeLinked ? "yes" : "no"}`,
    `- Runtime path: \`${result.context.agentProjectPath ?? "n/a"}\``,
    `- Latest delivered change: \`${result.context.latestDeliveredChangeId ?? "n/a"}\``,
    `- Provider mode: ${result.context.providerMode ?? "n/a"}`,
    `- Inputs read: ${result.inputs_read.length}`,
    "",
    "## Rubric",
    "",
    "- Product positioning",
    "- User value",
    "- Workflow completeness",
    "- UI clarity",
    "- Safety / abuse risk",
    "- Hallucination risk",
    "- Provider integration",
    "- Evidence quality",
    "- Demo readiness",
    "- Next action clarity",
    "",
    "## Findings",
    "",
    openFindings.length === 0
      ? "No open findings. Resolved/not-applicable checks are retained below for auditability."
      : `Open findings: ${openFindings.length}`,
    "",
    ...result.findings.map((finding) =>
      [
        `### ${finding.id} — ${finding.title}`,
        "",
        `Severity: \`${finding.severity}\``,
        `Status: \`${finding.status}\``,
        `Generated change: \`${finding.generatedChangeId ?? "n/a"}\``,
        "",
        "Evidence:",
        ...finding.evidence.map((item) => `- ${item}`),
        "",
        `Recommendation: ${finding.recommendation}`,
        "",
      ].join("\n"),
    ),
    "## Generated Change Requests",
    "",
    ...result.recommendedChanges.map(
      (change) =>
        `- ${change.priority} \`${change.id}\` ${change.title}: \`${change.draftId}\` (${change.changeRequestPath})`,
    ),
    "",
  ].join("\n");
}

function renderChangePlanMarkdown(result: ProductReviewResult): string {
  return [
    `# Prioritized Change Plan — ${result.projectName}`,
    "",
    "Studio generated these drafts from product-review findings. Run them one at a time through the normal Change Request gate.",
    "",
    result.recommendedChanges.length === 0
      ? "No Change Request drafts were generated because there are no open product-review findings that require scoped follow-up."
      : "",
    ...result.recommendedChanges.map((change, index) =>
      [
        `## ${index + 1}. ${change.title}`,
        "",
        `Priority: ${change.priority}`,
        `Draft: \`${change.draftId}\``,
        `Path: \`${change.changeRequestPath}\``,
        `Risk: ${change.risk}`,
        `Estimated difficulty: ${change.estimatedDifficulty}`,
        `Dependencies: ${change.dependencies.length > 0 ? change.dependencies.join(", ") : "none"}`,
        "",
        change.rationale,
        "",
      ].join("\n"),
    ),
  ].join("\n");
}

function renderDetectorReferenceChange(projectName: string): string {
  return `# Reframe detector as reference signal

## Goal

Change ${projectName}'s UI language so detector output is clearly a third-party reference signal, not a product success metric or detector-optimization target.

## Scope paths

- app/**
- components/**
- lib/**

## Non-goals

- Do not add authentication.
- Do not add billing.
- Do not add a database.
- Do not add upload.
- Do not add new npm dependencies.
- Do not add or change rewrite providers.
- Do not add or change detector providers.
- Do not optimize against detector scores.
- Do not add automatic rewrite retry loops.
- Do not claim detector bypass.
- Do not expose, read, print, persist, or modify secret values.
- Do not edit .env.local or any env file.

## Acceptance criteria

- The UI uses labels such as "Third-party reference signal" and "Reference signal change" instead of detector-score-as-success wording.
- If rewritten score is higher than original score, the UI shows a visible warning such as "Reference score increased. Add more user context or verify claims before using."
- Selection reason says candidate selection prioritizes preserved claims, clarity, specificity, fewer stock phrases, and anti-fabrication checks.
- The report explicitly says detector output is reference only and is not optimized against.
- Existing risk report, copy actions, history, rewrite flow, and detector calls still work.
- \`npm run build\` passes.
- \`npm run typecheck\` passes.
`;
}

function renderVerificationChange(projectName: string): string {
  return `# Improve rewrite result verification and claim warnings

## Goal

Add anti-fabrication and claim verification warnings to ${projectName}. The rewrite should sound more specific only when the source text or user context supports that specificity, and the UI should warn users before they rely on unsupported claims.

## Scope paths

- app/**
- components/**
- lib/**

## Non-goals

- Do not add authentication.
- Do not add billing.
- Do not add a database.
- Do not add upload.
- Do not add new dependencies.
- Do not add a new LLM provider.
- Do not add a fact-checking service.
- Do not add RAG.
- Do not add detector optimization loops.
- Do not claim detector bypass.
- Do not expose, read, print, persist, or modify secret values.
- Do not edit .env.local or any env file.

## Acceptance criteria

- The UI warns users to verify facts and claims before using rewritten output.
- The report includes "verify claims before use" or equivalent wording.
- Unsupported or newly introduced specifics are flagged for user verification when detectable.
- Safe generic-phrase removals are separated from changes that require verification.
- Existing copy actions, local history, provider mode, and detector reference signals still work.
- \`npm run build\` passes.
- \`npm run typecheck\` passes.
`;
}

function renderReferenceSignalGuidanceChange(projectName: string): string {
  return `# Improve user guidance when reference signal increases

## Goal

Improve ${projectName}'s user guidance when the rewritten third-party reference signal is higher than the original. The app should explain what the user can do next without automatically retrying or optimizing against detector output.

## Scope paths

- app/**
- components/**
- lib/**

## Non-goals

- Do not add authentication.
- Do not add billing.
- Do not add a database.
- Do not add upload.
- Do not add new dependencies.
- Do not add automatic rewrite loops.
- Do not add detector optimization loops.
- Do not claim detector bypass.
- Do not expose, read, print, persist, or modify secret values.
- Do not edit .env.local or any env file.

## Acceptance criteria

- If rewritten reference score is higher than original, the UI suggests concrete next actions: add more context, verify claims, try another tone, or manually edit.
- The app does not automatically retry to chase a detector score; one rewrite produces one report and waits for the user.
- The selection explanation stays focused on preserved claims, clarity, specificity, fewer stock phrases, and anti-fabrication checks.
- Existing copy actions, local history, provider mode, and detector reference signals still work.
- \`npm run build\` passes.
- \`npm run typecheck\` passes.
`;
}
