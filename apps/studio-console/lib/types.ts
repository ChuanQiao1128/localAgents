/**
 * Shared API response types — imported by both server (route handlers via
 * NextResponse.json shape) and client (fetch() consumers). Pure types only;
 * no Node imports, no fs, nothing that breaks the client bundle.
 *
 * Mirrors the producer types in lib/projects.ts and lib/contracts.ts.
 * Kept in sync by hand — if these drift, the route handlers' tests
 * would catch it (when we add tests in RC-5A.11 polish).
 */

export type ProjectSummary = {
  id: string;
  path: string;
  relPath: string;
  taskCount: number;
  completedCount: number;
  changeCount: number;
  latestSessionId: string | null;
  latestSessionStatus: string | null;
};

export type ListProjectsResponse = {
  projects: ProjectSummary[];
  workspaceRoot: string;
};

export type ArtifactPayload = {
  path: string;
  relPath: string;
  basename: string;
  extension: string;
  size: number;
  encoding: "utf-8" | "base64";
  content: string;
};

export type ArtifactErrorResponse = {
  error: string;
  detail?: string;
};

// ---------------------------------------------------------------------------
// Contract draft types — duplicated client-safe copy of lib/contracts.ts
// shapes (the original lives next to fs/promises imports and would break the
// client bundle if imported directly).
// ---------------------------------------------------------------------------

export const CONTRACT_FILE_NAMES = [
  "raw-requirements.md",
  "discussion.md",
  "product-contract.md",
  "mvp-requirements.md",
  "open-questions.md",
  "lock.json",
] as const;

export type ContractFileName = (typeof CONTRACT_FILE_NAMES)[number];

export type LockState = {
  locked: boolean;
  lockedAt: string | null;
  lockedBy: string | null;
  unlockedAt: string | null;
};

export type ContractSummary = {
  id: string;
  path: string;
  relPath: string;
  lockState: LockState;
  unresolvedQuestions: number;
  canLock: boolean;
  preconditionErrors: string[];
};

export type Contract = ContractSummary & {
  files: Record<ContractFileName, string>;
};

export type ListContractsResponse = {
  contracts: ContractSummary[];
};

export type CreateContractResponse = {
  id: string;
};

export type UpdateContractFileResponse =
  | { ok: true; file: ContractFileName; lockState?: LockState }
  | { ok: false; error: string; errors?: string[] };

// ---------------------------------------------------------------------------
// Project detail types — client-safe mirror of lib/projects.ts shapes.
// ---------------------------------------------------------------------------

export type ReviewItemSummary = {
  reviewId: string;
  sessionId: string;
  status: string;
  severity: string;
  reasonCode: string;
  title: string;
  summary: string | null;
  taskId: string | null;
  runId: string | null;
  candidate: string | null;
  sourceType: string | null;
  createdAt: string | null;
  reviewItemPath: string | null;
  promotionReportPath: string | null;
  evalResultsPath: string | null;
  changedFilesPath: string | null;
  patchDiffPath: string | null;
};

export type ChangeSummaryClient = {
  changeId: string;
  state:
    | "ready_for_run"
    | "applied"
    | "delivered"
    | "needs_human_review"
    | "failed";
  goal: string | null;
  appliedChangeJson: string | null;
  deliveryReportMd: string | null;
  branch: string | null;
  sha: string | null;
  taskId: string | null;
  runId: string | null;
  candidate: string | null;
  promotionDecision: string | null;
  appliedAt: string | null;
  filesTouched: string[];
  promotionReportPath: string | null;
  evalResultsPath: string | null;
  changedFilesPath: string | null;
  repairHistoryPath: string | null;
};

export type ProjectDetailResponse = ProjectSummary & {
  /** task-graph.json contents — opaque shape; cast at use site. */
  taskGraph: TaskGraphLike | null;
  /** autonomous-session.json — opaque shape; cast at use site. */
  latestSession: AutonomousSessionLike | null;
  reviewQueue: {
    total: number;
    open: number;
    blocking: number;
    /** Items whose status is no longer "open". */
    resolved: number;
    items: ReviewItemSummary[];
  };
  changes: ChangeSummaryClient[];
};

/**
 * Loose shape for task-graph.json. The orchestrator's schema validates the
 * canonical fields; UI tolerates missing optional ones.
 */
export type TaskGraphLike = {
  tasks?: TaskLike[];
  [key: string]: unknown;
};

export type TaskLike = {
  id?: string;
  title?: string;
  status?: string;
  intent?: string;
  scope_paths?: string[];
  acceptance?: string[];
  risk?: string;
  depends?: string[];
  run_id?: string | null;
  run_ids?: string[];
  commit?: string | null;
  source?: string;
  [key: string]: unknown;
};

// ---------------------------------------------------------------------------
// Change Request draft types — client-safe mirror of lib/changeRequests.ts.
// ---------------------------------------------------------------------------

export type ChangeRequestDraftSummaryClient = {
  id: string;
  path: string;
  relPath: string;
  changeRequestPath: string;
  projectId: string | null;
  title: string | null;
  createdAt: string;
  updatedAt: string;
  size: number;
};

export type ChangeRequestDraftClient = ChangeRequestDraftSummaryClient & {
  content: string;
};

export type ListChangeRequestDraftsResponse = {
  drafts: ChangeRequestDraftSummaryClient[];
};

export type CreateChangeRequestDraftResponse = {
  id: string;
};

export type UpdateChangeRequestDraftResponse =
  | { ok: true; field: "change-request.md" | "meta.json" }
  | { ok: false; error: string };

// ---------------------------------------------------------------------------
// Studio Project（RC-5A.12.1 IA Reset）—— 一个 project 一个合同。
// 客户端只用以下 mirror，server 类型在 lib/studioProjects.ts。
// ---------------------------------------------------------------------------

export type StudioProjectMetaClient = {
  id: string;
  name: string;
  template: string | null;
  createdAt: string;
  updatedAt: string;
  /** Runtime project id used by agent-studio CLI, once prepared. */
  agentProjectId: string | null;
  /** Workspace-relative path to the runtime project, once prepared. */
  agentProjectPath: string | null;
};

export type StudioProjectSummaryClient = {
  id: string;
  name: string;
  path: string;
  relPath: string;
  meta: StudioProjectMetaClient;
  contract: {
    locked: boolean;
    canLock: boolean;
    unresolvedQuestions: number;
    preconditionErrors: string[];
    mvpRequirementsRelPath: string;
  };
  /** Runtime project id used by agent-studio CLI, once prepared. */
  agentProjectId: string | null;
  /** Workspace-relative path to the runtime project, once prepared. */
  agentProjectPath: string | null;
  hasRunState: boolean;
  taskCount: number;
  completedCount: number;
  changeCount: number;
  latestSessionId: string | null;
  latestSessionStatus: string | null;
  reviewQueueOpen: number;
  reviewQueueBlocking: number;
  latestDeliveredSha: string | null;
  providerReadiness: ProviderReadinessClient | null;
};

export type StudioProjectDetailClient = StudioProjectSummaryClient & {
  /** 合同 6 个文件内容。键和 ContractFileName 一致。 */
  files: Record<ContractFileName, string>;
  lockState: LockState;
  /** 关联的 .agent-studio runtime 详情；没跑过为 null。沿用 ProjectDetailResponse 形状。 */
  runDetail: ProjectDetailResponse | null;
};

export type ProviderReadinessClient = {
  rewriteProvider: ProviderReadinessItemClient;
  detectorProvider: ProviderReadinessItemClient;
  currentMode: string;
  source: "runtime-env-local" | "not-linked";
  secretsExposed: false;
};

export type ProviderReadinessItemClient = {
  status: "connected" | "missing" | "not_applicable";
  label: string;
  detail: string;
};

export type ListStudioProjectsResponse = {
  projects: StudioProjectSummaryClient[];
};

export type CreateStudioProjectResponse = {
  id: string;
};

export type UpdateStudioContractResponse =
  | { ok: true; file: ContractFileName; lockState?: LockState }
  | { ok: false; error: string; errors?: string[] };

// Runtime bootstrap（RC-5A.12.5A）

export type RuntimePrepareStepState =
  | "pending"
  | "running"
  | "completed"
  | "failed"
  | "skipped";

export type RuntimePrepareStepClient = {
  id: string;
  label: string;
  state: RuntimePrepareStepState;
  startedAt: string | null;
  completedAt: string | null;
  exitCode: number | null;
  errorSummary: string | null;
};

export type RuntimePrepareRunState =
  | "queued"
  | "running"
  | "completed"
  | "failed"
  | "stopped";

export type RuntimePrepareStatusClient = {
  runId: string;
  type: "prepare";
  state: RuntimePrepareRunState;
  startedAt: string;
  updatedAt: string;
  completedAt: string | null;
  currentStep: string | null;
  steps: RuntimePrepareStepClient[];
  agentProjectId: string | null;
  agentProjectPath: string | null;
  error: string | null;
};

export type RuntimePrepareRunResponse = {
  status: RuntimePrepareStatusClient | null;
  stdoutTail: string;
  stderrTail: string;
};

export type RuntimePrepareKickoffResponse =
  | { ok: true; runId: string }
  | { ok: false; error: string };

// Start Development run manager（RC-5A.12.5B）

export type RuntimeDevelopmentRunState =
  | "starting"
  | "running"
  | "needs_human"
  | "completed"
  | "failed"
  | "stopped";

export type RuntimeDevelopmentPhase =
  | "preflight"
  | "autonomous_start"
  | "post_run"
  | null;

export type RuntimeDevelopmentStatusClient = {
  runId: string;
  kind: "autonomous_start";
  status: RuntimeDevelopmentRunState;
  phase: RuntimeDevelopmentPhase;
  agentProjectId: string;
  agentProjectPath: string;
  startedAt: string;
  updatedAt: string;
  finishedAt: string | null;
  pid: number | null;
  sessionId: string | null;
  runtimeSessionStatus: string | null;
  currentTaskId: string | null;
  reviewOpenCount: number;
  reviewBlockingCount: number;
  taskCounts: Record<string, number>;
  preflightExitCode: number | null;
  validateArtifactsExitCode: number | null;
  exitCode: number | null;
  error: string | null;
};

export type RuntimeDevelopmentRunResponse = {
  status: RuntimeDevelopmentStatusClient | null;
  stdoutTail: string;
  stderrTail: string;
};

export type RuntimeDevelopmentKickoffResponse =
  | { ok: true; runId: string }
  | { ok: false; error: string };

export type RuntimeDevelopmentStopResponse =
  | { ok: true; runId: string; status: "stopped" }
  | { ok: false; error: string };

// Local generated-app preview（Deliver tab）

export type PreviewRunState = "stopped" | "starting" | "running" | "failed";

export type PreviewRunStatusClient = {
  status: PreviewRunState;
  url: string | null;
  port: number | null;
  pid: number | null;
  startedAt: string | null;
  stoppedAt: string | null;
  agentProjectId: string | null;
  agentProjectPath: string | null;
  updatedAt: string;
  error: string | null;
};

export type PreviewRunResponse = {
  status: PreviewRunStatusClient;
  stdoutTail: string;
  stderrTail: string;
};

export type PreviewRunStartStopResponse =
  | { ok: true; status: PreviewRunStatusClient }
  | { ok: false; error: string };

// 项目级 change draft（RC-5A.12.2）

export type ChangeDraftMetaClient = {
  title: string | null;
  createdAt: string;
  updatedAt: string;
};

export type ChangeDraftSummaryClient = {
  id: string;
  projectId: string;
  changeRequestPath: string;
  meta: ChangeDraftMetaClient;
  size: number;
};

export type ChangeDraftClient = ChangeDraftSummaryClient & {
  content: string;
};

export type ListChangeDraftsResponse = {
  drafts: ChangeDraftSummaryClient[];
};

export type CreateChangeDraftResponse = {
  id: string;
};

export type UpdateChangeDraftResponse =
  | { ok: true; field: "change-request.md" | "meta.json" }
  | { ok: false; error: string };

// Product review + automated change planning（RC-5F）

export type ProductReviewSeverityClient = "critical" | "high" | "medium" | "low";
export type ProductReviewFindingStatusClient = "open" | "resolved" | "not_applicable";

export type ProductReviewFindingClient = {
  id: string;
  severity: ProductReviewSeverityClient;
  status?: ProductReviewFindingStatusClient;
  title: string;
  evidence: string | string[];
  recommendation: string;
  generatedChangeId?: string | null;
};

export type ProductReviewRecommendedChangeClient = {
  id: string;
  title: string;
  priority: "P0" | "P1" | "P2";
  rationale: string;
  draftId: string;
  changeRequestPath: string;
  risk?: "low" | "medium" | "high";
  estimatedDifficulty?: "S" | "M" | "L";
  dependencies?: string[];
};

export type ProductReviewResultClient = {
  schema_version?: "studio.product_review.v2";
  studio_project_id?: string;
  runtime_project_id?: string | null;
  projectId: string;
  projectName: string;
  reviewId: string;
  createdAt: string;
  score: number;
  maxScore: 100;
  verdict:
    | "pass"
    | "pass_with_recommendations"
    | "needs_work"
    | "unsafe"
    | "needs_change_plan"
    | "blocked";
  summary: string;
  context: {
    runtimeLinked: boolean;
    agentProjectId: string | null;
    agentProjectPath: string | null;
    latestDeliveredChangeId: string | null;
    latestDeliveredSha: string | null;
    providerMode: string | null;
  };
  findings: ProductReviewFindingClient[];
  recommendedChanges: ProductReviewRecommendedChangeClient[];
  inputs_read?: string[];
  artifacts: {
    reviewMd: string;
    reviewJson: string;
    changePlanMd: string;
  };
};

export type ProductReviewResponse =
  | { ok: true; review: ProductReviewResultClient | null }
  | { ok: false; error: string };

/**
 * Loose shape for autonomous-session.json. Fields evolved across MVP-4*
 * milestones; the UI defensively reads what's present.
 */
export type AutonomousSessionLike = {
  session_id?: string;
  status?: string;
  branch?: string;
  patch_worker?: string | { name?: string; [k: string]: unknown };
  pause_reason?: string | null;
  paused_reason?: string | null;
  created_at?: string;
  completed_at?: string | null;
  task_counts?: {
    total?: number;
    completed?: number;
    pending?: number;
    running?: number;
    needs_human_review?: number;
    abandoned?: number;
    [k: string]: unknown;
  };
  integration?: {
    runs?: number;
    passed?: number;
    failed?: number;
    last_status?: string | null;
    last_result?: string | null;
    [k: string]: unknown;
  };
  deployment?: {
    enabled?: boolean;
    status?: string | null;
    url?: string | null;
    [k: string]: unknown;
  };
  review_queue?: {
    open?: number;
    blocking?: number;
    [k: string]: unknown;
  };
  [key: string]: unknown;
};
