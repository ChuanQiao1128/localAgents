from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.bootstrap import initialize_workspace
from orchestrator.agents import AgentContext, AgentRunner
from orchestrator.agents.architect import ArchitectAgent
from orchestrator.agents.codex_multimodal_critic import CodexCliMultimodalCriticAgent
from orchestrator.agents.design_critique import DesignCritiqueAgent
from orchestrator.agents.developer import DeveloperAgent
from orchestrator.agents.developer_team import DeveloperTeamAgent
from orchestrator.agents.downstream_teams import DownstreamTeamsAgent
from orchestrator.agents.implementation_hardening import ImplementationHardeningAgent
from orchestrator.agents.prd_benchmark import PrdBenchmarkAgent
from orchestrator.agents.prd_council import PrdCouncilAgent
from orchestrator.agents.prd_draft import LocalPrdDraftAgent
from orchestrator.agents.prd_manual import ManualCodexPrdAgent, validate_prd_files
from orchestrator.agents.prd_options import PrdOptionsAgent
from orchestrator.agents.prd_product_fit import PrdProductFitAgent
from orchestrator.agents.prd_quality import PrdCritiqueAgent, PrdScoreAgent
from orchestrator.agents.prd_research import PrdResearchAgent
from orchestrator.agents.prd_research_v2 import PrdResearchV2Agent
from orchestrator.agents.prd_team_review import PrdTeamReviewAgent
from orchestrator.agents.product_review_team import ProductBuildReviewAgent
from orchestrator.agents.product_manager import ProductManagerAgent
from orchestrator.agents.qa import QAAgent
from orchestrator.agents.example_visual_critic import ExampleVisualCriticAgent
from orchestrator.agents.reference_example_discovery import ReferenceExampleDiscoveryAgent
from orchestrator.agents.reference_visual_research import ReferenceVisualResearchAgent
from orchestrator.agents.reviewer import ReviewerAgent
from orchestrator.agents.team_system_review import TeamSystemReviewAgent
from orchestrator.agents.ui_product_team import UiProductTeamAgent
from orchestrator.agents.ui_designer import UiDesignerAgent
from orchestrator.agents.visual_variant_multimodal_review import VisualVariantMultimodalReviewAgent
from orchestrator.config import load_local_env, resolve_paths
from orchestrator.core.agent_registry import AgentRegistry
from orchestrator.core.agentic_runtime import (
    AGENTIC_ABANDONMENT_LOG_RELPATH,
    AgenticProjectRuntime,
    AgenticRunResult,
    _read_abandonment_history,
)
from orchestrator.core.run_package import (
    CandidateReport,
    ProjectRunPackages,
    RunPackage,
    iter_candidate_summaries,
)
from orchestrator.core.cost_tracker import CostTracker
from orchestrator.core.event_bus import EventBus
from orchestrator.core.run_manager import create_engine
from orchestrator.db import Database


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    # Opt the production CLI into LLM-driven phase execution. Tests that
    # construct WorkflowEngine directly remain on the deterministic stub
    # unless they explicitly enable this flag or inject a runner.
    os.environ.setdefault("LOCALAGENTS_USE_LLM", "1")
    try:
        args.handler(args)
    except Exception as exc:  # pragma: no cover - CLI boundary
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-studio")
    parser.add_argument(
        "--root",
        default=None,
        help="Workspace root. Defaults to the current directory.",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    init_parser = subcommands.add_parser("init", help="Initialize the local studio workspace.")
    init_parser.set_defaults(handler=cmd_init)

    new_parser = subcommands.add_parser("new", help="Create a local software project.")
    new_parser.add_argument("idea", nargs="?", default=None, help="Project idea (optional when --from is given).")
    new_parser.add_argument("--name", default=None, help="Optional project name.")
    new_parser.add_argument(
        "--from",
        dest="from_path",
        default=None,
        help="MVP-4A: ingest a requirements markdown file. Generates prd.md, acceptance-criteria.json, architecture.md, task-graph.json.",
    )
    new_parser.set_defaults(handler=cmd_new)

    run_parser = subcommands.add_parser("run", help="Run a workflow for the latest project.")
    run_parser.add_argument("workflow", nargs="?", default="software_project")
    run_parser.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    run_parser.add_argument(
        "--force",
        action="store_true",
        help="Start a new run even if a previous run on this project is still active or awaiting a gate.",
    )
    run_parser.add_argument(
        "--autonomous",
        action="store_true",
        help="Run end-to-end without stopping: auto-approve gates, isolate phase failures, write a final-run-status report.",
    )
    run_parser.add_argument(
        "--agentic-patch-worker",
        choices=["none", "codex"],
        default=None,
        help="agentic_project only: generate a candidate source patch with the selected worker.",
    )
    run_parser.add_argument(
        "--agentic-execute-eval",
        action="store_true",
        help="agentic_project only: execute required eval commands and record stdout/stderr/exit codes.",
    )
    run_parser.add_argument(
        "--agentic-model",
        default=None,
        help="agentic_project only: model for Codex patch-worker. Defaults to gpt-5.5.",
    )
    run_parser.add_argument(
        "--agentic-timeout",
        type=int,
        default=900,
        help="agentic_project only: timeout seconds for patch-worker and eval commands.",
    )
    run_parser.add_argument(
        "--agentic-repair-loops",
        type=int,
        default=None,
        help="agentic_project only: max automated repair loops after required eval failure. Defaults to 0.",
    )
    run_parser.add_argument(
        "--agentic-candidate-count",
        type=int,
        default=None,
        help="agentic_project only: number of candidates to generate sequentially (1-3). Defaults to 3.",
    )
    run_parser.set_defaults(handler=cmd_run)

    resume_parser = subcommands.add_parser(
        "resume",
        help="Resume an existing run from its last incomplete phase. Useful after Ctrl+C, crash, or `approve` of a gated phase.",
    )
    resume_parser.add_argument("run_id", nargs="?", default=None, help="Run id. Defaults to latest run on the latest project.")
    resume_parser.add_argument("--project", default=None, help="Project id (only used when run_id is omitted).")
    resume_parser.add_argument(
        "--autonomous",
        action="store_true",
        help="Continue under autonomous-mode rules (auto-approve gates, isolate phase failures, write report on completion).",
    )
    resume_parser.set_defaults(handler=cmd_resume)

    prd_parser = subcommands.add_parser("prd", help="Manual Codex PRD workflow.")
    prd_subcommands = prd_parser.add_subparsers(dest="prd_command", required=True)

    prd_prepare = prd_subcommands.add_parser("prepare", help="Prepare a prompt pack for Codex/ChatGPT.")
    prd_prepare.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_prepare.set_defaults(handler=cmd_prd_prepare)

    prd_research = prd_subcommands.add_parser("research", help="Run Tavily-backed PRD research.")
    prd_research.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_research.add_argument("--max-queries", type=int, default=6)
    prd_research.add_argument("--results-per-query", type=int, default=5)
    prd_research.add_argument(
        "--mock",
        action="store_true",
        help="Use deterministic mock search instead of Tavily, even if TAVILY_API_KEY is set.",
    )
    prd_research.set_defaults(handler=cmd_prd_research)

    prd_research_v2 = prd_subcommands.add_parser(
        "research-v2",
        help="Generate enriched PRD research artifacts from existing sources.",
    )
    prd_research_v2.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_research_v2.set_defaults(handler=cmd_prd_research_v2)

    prd_visual_research = prd_subcommands.add_parser(
        "visual-research",
        help="Capture desktop/mobile screenshots for top reference products.",
    )
    prd_visual_research.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_visual_research.add_argument("--limit", type=int, default=4, help="Reference products to capture. Default: 4.")
    prd_visual_research.set_defaults(handler=cmd_prd_visual_research)

    prd_discover_examples = prd_subcommands.add_parser(
        "discover-examples",
        help="Discover concrete portfolio/template example URLs from verified reference seeds.",
    )
    prd_discover_examples.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_discover_examples.add_argument("--limit", type=int, default=10, help="Top examples to keep. Default: 10.")
    prd_discover_examples.add_argument(
        "--per-seed",
        type=int,
        default=6,
        help="Candidate examples to keep from each reference seed before global ranking. Default: 6.",
    )
    prd_discover_examples.add_argument(
        "--max-per-source",
        type=int,
        default=3,
        help="Maximum selected examples from one source seed. Default: 3.",
    )
    prd_discover_examples.add_argument(
        "--no-screenshots",
        action="store_true",
        help="Only discover and score URLs; skip browser screenshot capture.",
    )
    prd_discover_examples.add_argument(
        "--desktop-only",
        action="store_true",
        help="Capture desktop screenshots only instead of desktop and mobile.",
    )
    prd_discover_examples.add_argument(
        "--progress",
        action="store_true",
        help="Print progress while entry pages are scanned and examples are screenshotted.",
    )
    prd_discover_examples.set_defaults(handler=cmd_prd_discover_examples)

    prd_critique_examples = prd_subcommands.add_parser(
        "critique-examples",
        help="Turn discovered example screenshots into visual standards for UI and v0 agents.",
    )
    prd_critique_examples.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_critique_examples.set_defaults(handler=cmd_prd_critique_examples)

    prd_multimodal_critic = prd_subcommands.add_parser(
        "multimodal-critic",
        help="Use Codex CLI image input to run a multimodal design review without an API key.",
    )
    prd_multimodal_critic.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_multimodal_critic.add_argument(
        "--provider",
        choices=["codex-cli"],
        default="codex-cli",
        help="Multimodal critic provider. Default: codex-cli.",
    )
    prd_multimodal_critic.add_argument("--model", default="gpt-5.5", help="Codex CLI model. Default: gpt-5.5.")
    prd_multimodal_critic.add_argument("--limit", type=int, default=4, help="Screenshot count to send. Default: 4.")
    prd_multimodal_critic.add_argument(
        "--timeout",
        type=int,
        default=900,
        help="Seconds to wait for Codex CLI. Default: 900.",
    )
    prd_multimodal_critic.set_defaults(handler=cmd_prd_multimodal_critic)

    prd_benchmark = prd_subcommands.add_parser(
        "benchmark",
        help="Generate local PRD benchmark library artifacts without external API calls.",
    )
    prd_benchmark.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_benchmark.set_defaults(handler=cmd_prd_benchmark)

    prd_options = prd_subcommands.add_parser(
        "options",
        help="Generate multiple PM strategy options from PRD research.",
    )
    prd_options.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_options.set_defaults(handler=cmd_prd_options)

    prd_select = prd_subcommands.add_parser(
        "select",
        help="Select one PRD option before drafting the final PRD.",
    )
    prd_select.add_argument("option_id", help="Option id, for example: option-b.")
    prd_select.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_select.add_argument("--notes", default="", help="Optional decision notes.")
    prd_select.set_defaults(handler=cmd_prd_select)

    prd_council = prd_subcommands.add_parser(
        "council",
        help="Generate separate PRD council role artifacts and a debate synthesis.",
    )
    prd_council.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_council.add_argument(
        "--prepare",
        action="store_true",
        help="Prepare manual Codex/ChatGPT prompt packs for each council role.",
    )
    prd_council.add_argument(
        "--import-dir",
        default=None,
        help="Import council role JSON responses from a prompt-pack directory.",
    )
    prd_council.set_defaults(handler=cmd_prd_council)

    prd_import = prd_subcommands.add_parser("import", help="Import a PRD JSON response.")
    prd_import.add_argument("path", help="Path to JSON response from Codex/ChatGPT.")
    prd_import.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_import.add_argument(
        "--no-approve",
        action="store_true",
        help="Do not approve the pending PRD gate after a valid import.",
    )
    prd_import.set_defaults(handler=cmd_prd_import)

    prd_draft = prd_subcommands.add_parser("draft", help="Generate a local PRD JSON draft from research sources.")
    prd_draft.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_draft.add_argument("--output", default=None, help="Output JSON path. Defaults to the run artifact folder.")
    prd_draft.add_argument(
        "--import",
        dest="do_import",
        action="store_true",
        help="Import and validate the generated draft immediately.",
    )
    prd_draft.add_argument(
        "--no-approve",
        action="store_true",
        help="With --import, do not approve the pending PRD gate after validation passes.",
    )
    prd_draft.set_defaults(handler=cmd_prd_draft)

    prd_validate = prd_subcommands.add_parser("validate", help="Validate imported PRD artifacts.")
    prd_validate.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_validate.set_defaults(handler=cmd_prd_validate)

    prd_score = prd_subcommands.add_parser("score", help="Run independent PRD quality scoring.")
    prd_score.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_score.set_defaults(handler=cmd_prd_score)

    prd_critique = prd_subcommands.add_parser("critique", help="Generate multi-role PRD critique.")
    prd_critique.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_critique.set_defaults(handler=cmd_prd_critique)

    prd_product_fit = prd_subcommands.add_parser("product-fit", help="Evaluate whether the PRD describes a good product.")
    prd_product_fit.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_product_fit.set_defaults(handler=cmd_prd_product_fit)

    prd_team_review = prd_subcommands.add_parser("team-review", help="Review and optimize the PRD agent team design.")
    prd_team_review.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_team_review.set_defaults(handler=cmd_prd_team_review)

    prd_build_review = prd_subcommands.add_parser(
        "build-review",
        help="Run the PRD/product team against generated implementation output.",
    )
    prd_build_review.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    prd_build_review.set_defaults(handler=cmd_prd_build_review)

    design_parser = subcommands.add_parser("design", help="UI design workflow.")
    design_subcommands = design_parser.add_subparsers(dest="design_command", required=True)

    design_draft = design_subcommands.add_parser("draft", help="Generate UI design artifacts from PRD context.")
    design_draft.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    design_draft.set_defaults(handler=cmd_design_draft)

    design_critique = design_subcommands.add_parser("critique", help="Evaluate UI design quality.")
    design_critique.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    design_critique.set_defaults(handler=cmd_design_critique)

    design_team = design_subcommands.add_parser(
        "team",
        help="Run the full UI Product Team and generate design remediation handoff artifacts.",
    )
    design_team.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    design_team.set_defaults(handler=cmd_design_team)

    # NOTE: `design directions` and `design v0-smoke` were removed when the v0
    # paid API was dropped. The legacy logic still lives in
    # orchestrator/agents/visual_direction.py awaiting a Claude-CLI rewrite.

    design_review_variants = design_subcommands.add_parser(
        "review-variants",
        help="Use Codex CLI image input to review visual direction screenshots and select a winner.",
    )
    design_review_variants.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    design_review_variants.add_argument(
        "--provider",
        choices=["codex-cli"],
        default="codex-cli",
        help="Multimodal review provider. Default: codex-cli.",
    )
    design_review_variants.add_argument("--model", default="gpt-5.5", help="Codex CLI model. Default: gpt-5.5.")
    design_review_variants.add_argument("--timeout", type=int, default=1200, help="Seconds to wait for Codex CLI. Default: 1200.")
    design_review_variants.set_defaults(handler=cmd_design_review_variants)

    architecture_parser = subcommands.add_parser("architecture", help="Architecture planning workflow.")
    architecture_subcommands = architecture_parser.add_subparsers(dest="architecture_command", required=True)

    architecture_draft = architecture_subcommands.add_parser(
        "draft",
        help="Generate architecture artifacts from PRD and design gates.",
    )
    architecture_draft.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    architecture_draft.set_defaults(handler=cmd_architecture_draft)

    implementation_parser = subcommands.add_parser("implementation", help="Implementation workflow.")
    implementation_subcommands = implementation_parser.add_subparsers(dest="implementation_command", required=True)

    implementation_draft = implementation_subcommands.add_parser(
        "draft",
        help="Generate deterministic code artifacts from architecture tasks.",
    )
    implementation_draft.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    implementation_draft.set_defaults(handler=cmd_implementation_draft)

    implementation_team = implementation_subcommands.add_parser(
        "team",
        help="Run the Developer Team and generate remediation implementation contracts.",
    )
    implementation_team.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    implementation_team.set_defaults(handler=cmd_implementation_team)

    implementation_harden = implementation_subcommands.add_parser(
        "harden",
        help="Run the implementation hardening pass for backend/API and browser interaction tests.",
    )
    implementation_harden.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    implementation_harden.add_argument(
        "--target",
        choices=["backend-api"],
        default="backend-api",
        help="Hardening target. Default: backend-api.",
    )
    implementation_harden.set_defaults(handler=cmd_implementation_harden)

    status_parser = subcommands.add_parser("status", help="Show latest project/run status.")
    status_parser.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    status_parser.add_argument("--json", action="store_true", help="Print raw JSON.")
    status_parser.set_defaults(handler=cmd_status)

    approve_parser = subcommands.add_parser("approve", help="Approve a pending gate.")
    approve_parser.add_argument("target", help="Phase id or gate id, for example: prd.")
    approve_parser.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    approve_parser.set_defaults(handler=cmd_approve)

    reject_parser = subcommands.add_parser("reject", help="Reject a pending gate.")
    reject_parser.add_argument("target", help="Phase id or gate id, for example: prd.")
    reject_parser.add_argument("--reason", default="Rejected by user.")
    reject_parser.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    reject_parser.set_defaults(handler=cmd_reject)

    retry_parser = subcommands.add_parser("retry", help="Retry a phase in the latest run.")
    retry_parser.add_argument("phase", help="Phase id, for example: prd.")
    retry_parser.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    retry_parser.set_defaults(handler=cmd_retry)

    logs_parser = subcommands.add_parser("logs", help="Show event logs for a run.")
    logs_parser.add_argument("run_id", nargs="?", default=None)
    logs_parser.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    logs_parser.set_defaults(handler=cmd_logs)

    diff_parser = subcommands.add_parser("diff", help="Show a task diff placeholder.")
    diff_parser.add_argument("task_id")
    diff_parser.set_defaults(handler=cmd_diff)

    agents_parser = subcommands.add_parser("agents", help="List loaded agent configs.")
    agents_parser.set_defaults(handler=cmd_agents)

    run_agent_parser = subcommands.add_parser("run-agent", help="Run one configured agent through the stub runtime.")
    run_agent_parser.add_argument("agent_id")
    run_agent_parser.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    run_agent_parser.add_argument("--instructions", default="Run the requested agent task.")
    run_agent_parser.add_argument(
        "--materialize",
        action="store_true",
        help="Write deterministic artifacts for supported agents.",
    )
    run_agent_parser.set_defaults(handler=cmd_run_agent)

    costs_parser = subcommands.add_parser("costs", help="Show token/cost totals for the latest run.")
    costs_parser.add_argument("run_id", nargs="?", default=None)
    costs_parser.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    costs_parser.set_defaults(handler=cmd_costs)

    diagnose_parser = subcommands.add_parser(
        "diagnose",
        help="Inspect a run for stub fallbacks and validation failures, with fix suggestions.",
    )
    diagnose_parser.add_argument("--run", dest="run_id", default=None, help="Run id. Defaults to latest run.")
    diagnose_parser.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    diagnose_parser.set_defaults(handler=cmd_diagnose)

    abandonments_parser = subcommands.add_parser(
        "agentic-abandonments",
        help="Inspect agentic_project abandonment records for a project.",
    )
    abandonments_subcommands = abandonments_parser.add_subparsers(dest="abandonments_command", required=True)
    abandonments_list = abandonments_subcommands.add_parser(
        "list",
        help="List abandonment records from .agent/agentic-abandonments.jsonl.",
    )
    abandonments_list.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    abandonments_list.add_argument("--json", action="store_true", help="Print raw JSON list.")
    abandonments_list.set_defaults(handler=cmd_agentic_abandonments_list)

    candidates_parser = subcommands.add_parser(
        "agentic-candidates",
        help="Inspect, dry-run, or apply candidate patches from an agentic_project run.",
    )
    candidates_subcommands = candidates_parser.add_subparsers(dest="candidates_command", required=True)

    candidates_list = candidates_subcommands.add_parser(
        "list",
        help="List candidates for a run with score, decision, and selection marker.",
    )
    candidates_list.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    candidates_list.add_argument("--run", dest="run_id", default=None, help="Run id. Defaults to latest run.")
    candidates_list.add_argument("--json", action="store_true", help="Print raw JSON list.")
    candidates_list.set_defaults(handler=cmd_agentic_candidates_list)

    candidates_show = candidates_subcommands.add_parser(
        "show",
        help="Show a candidate's full evidence: score breakdown, eval, repair, critics.",
    )
    candidates_show.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    candidates_show.add_argument("--run", dest="run_id", default=None, help="Run id. Defaults to latest run.")
    candidates_show.add_argument(
        "--candidate",
        required=True,
        help="Candidate id (e.g. candidate-b) or `selected` to use promotion.selected_candidate.",
    )
    candidates_show.add_argument("--json", action="store_true", help="Print raw JSON dump.")
    candidates_show.set_defaults(handler=cmd_agentic_candidates_show)

    autonomous_parser = subcommands.add_parser(
        "autonomous",
        help="MVP-4A: drive a project end-to-end from requirements.md (Resumable Autonomous Controller).",
    )
    autonomous_subcommands = autonomous_parser.add_subparsers(dest="autonomous_command", required=True)

    autonomous_start = autonomous_subcommands.add_parser(
        "start",
        help="Start (or resume) the autonomous session for a project.",
    )
    autonomous_start.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_start.add_argument(
        "--allow-dirty-worktree",
        action="store_true",
        help="Skip the worktree clean check. NOT RECOMMENDED — only for debugging.",
    )
    autonomous_start.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Hard cap on tasks to advance in this invocation (separate from session budgets). Use to do a few tasks then stop.",
    )
    autonomous_start.set_defaults(handler=cmd_autonomous_start)

    autonomous_status = autonomous_subcommands.add_parser(
        "status",
        help="Show the current autonomous session state, task counts, and budget.",
    )
    autonomous_status.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_status.add_argument("--json", action="store_true", help="Print raw JSON dump.")
    autonomous_status.set_defaults(handler=cmd_autonomous_status)

    autonomous_logs = autonomous_subcommands.add_parser(
        "logs",
        help="Tail the controller-log.jsonl event stream for the current session.",
    )
    autonomous_logs.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_logs.add_argument("--tail", type=int, default=20, help="Number of trailing log lines to show.")
    autonomous_logs.set_defaults(handler=cmd_autonomous_logs)

    autonomous_halt = autonomous_subcommands.add_parser(
        "halt",
        help="Cooperative halt — sets halt_requested; the controller pauses after the current task finishes.",
    )
    autonomous_halt.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_halt.set_defaults(handler=cmd_autonomous_halt)

    autonomous_resume = autonomous_subcommands.add_parser(
        "resume",
        help="Resume a paused session and continue advancing tasks.",
    )
    autonomous_resume.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_resume.add_argument(
        "--max-steps",
        type=int,
        default=None,
        help="Hard cap on tasks to advance in this invocation.",
    )
    autonomous_resume.set_defaults(handler=cmd_autonomous_resume)

    autonomous_integrate = autonomous_subcommands.add_parser(
        "integrate",
        help="Manually run an integration check against the project working tree (does not advance tasks).",
    )
    autonomous_integrate.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_integrate.add_argument("--json", action="store_true", help="Print raw JSON result.")
    autonomous_integrate.set_defaults(handler=cmd_autonomous_integrate)

    # RC-1 audit: surface the artifact_validation helper as a CLI so operators
    # can drift-check a session without writing Python.
    autonomous_validate = autonomous_subcommands.add_parser(
        "validate-artifacts",
        help="RC-1: validate every persisted artifact in a session for schema + redaction sanity. Exits non-zero if any error is found.",
    )
    autonomous_validate.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_validate.add_argument("--session", dest="session_id", default=None, help="Session id. Defaults to most recently updated session.")
    autonomous_validate.add_argument("--json", action="store_true", help="Print raw JSON validation report.")
    autonomous_validate.set_defaults(handler=cmd_autonomous_validate_artifacts)

    # RC-2B.11: preflight check before `autonomous start`. Cheap, no
    # subprocess fork; surfaces missing prerequisites (git repo, clean
    # worktree, task-graph, codex CLI when configured) in 50ms instead
    # of failing on the first task pause.
    autonomous_preflight = autonomous_subcommands.add_parser(
        "preflight",
        help="RC-2B: check prerequisites for `autonomous start` (git repo, worktree clean, task graph, configured patch worker present). Exit 0 only when all checks pass.",
    )
    autonomous_preflight.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_preflight.add_argument("--json", action="store_true", help="Print raw JSON result.")
    autonomous_preflight.set_defaults(handler=cmd_autonomous_preflight)

    # MVP-4E: deploy subcommand. --dry-run / --yes are mutually exclusive
    # AND one is required; this prevents accidental real deploys.
    autonomous_smoke = autonomous_subcommands.add_parser(
        "smoke",
        help="MVP-4F: run smoke checks against a deployed URL (uses latest deployment by default).",
    )
    autonomous_smoke.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_smoke.add_argument("--session", dest="session_id", default=None, help="Session id. Defaults to latest active session.")
    autonomous_smoke.add_argument("--deployment", dest="deployment_id", default=None, help="Deployment id (resolves URL from deployment.json).")
    autonomous_smoke.add_argument("--url", default=None, help="Explicit deployment URL to smoke-check.")
    autonomous_smoke.add_argument(
        "--rollback-on-failure",
        action="store_true",
        help="If smoke fails AND environment=production AND --yes, run rollback. Otherwise skipped.",
    )
    autonomous_smoke.add_argument("--yes", action="store_true", help="Required confirmation for --rollback-on-failure.")
    autonomous_smoke.add_argument("--json", action="store_true", help="Print raw JSON result.")
    autonomous_smoke.set_defaults(handler=cmd_autonomous_smoke)

    autonomous_rollback = autonomous_subcommands.add_parser(
        "rollback",
        help="MVP-4F: roll back the production deployment. Requires --dry-run or --yes.",
    )
    autonomous_rollback.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_rollback.add_argument("--session", dest="session_id", default=None, help="Session id.")
    autonomous_rollback.add_argument("--deployment-url", dest="deployment_url", default=None, help="Optional explicit deployment URL to roll back.")
    rollback_mode = autonomous_rollback.add_mutually_exclusive_group(required=True)
    rollback_mode.add_argument("--dry-run", action="store_true", help="Print sanitized rollback command; do not call vercel.")
    rollback_mode.add_argument("--yes", action="store_true", help="Run the real rollback and write rollback.json.")
    autonomous_rollback.add_argument("--json", action="store_true", help="Print raw JSON result.")
    autonomous_rollback.set_defaults(handler=cmd_autonomous_rollback)

    autonomous_deploy = autonomous_subcommands.add_parser(
        "deploy",
        help="MVP-4E: Vercel deploy adapter. Requires --dry-run or --yes; --yes runs the real CLI.",
    )
    autonomous_deploy.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    autonomous_deploy.add_argument("--session", dest="session_id", default=None, help="Session id. Defaults to latest active session.")
    deploy_mode = autonomous_deploy.add_mutually_exclusive_group(required=True)
    deploy_mode.add_argument("--dry-run", action="store_true", help="Print sanitized commands; do not call vercel CLI.")
    deploy_mode.add_argument("--yes", action="store_true", help="Run the real vercel deploy and write deployment.json.")
    target_mode = autonomous_deploy.add_mutually_exclusive_group(required=False)
    target_mode.add_argument("--prod", action="store_true", help="Override config: production deploy.")
    target_mode.add_argument("--preview", action="store_true", help="Override config: preview deploy.")
    autonomous_deploy.add_argument("--prebuilt", action="store_true", help="Override config: build then deploy --prebuilt.")
    autonomous_deploy.add_argument("--json", action="store_true", help="Print raw JSON result.")
    autonomous_deploy.set_defaults(handler=cmd_autonomous_deploy)

    # MVP-4D: reviews subcommand group (list / show / approve / reject / resolve)
    reviews_parser = autonomous_subcommands.add_parser(
        "reviews",
        help="MVP-4D: Human Review Queue — inspect, approve, reject, or resolve human-decision pauses.",
    )
    reviews_subcommands = reviews_parser.add_subparsers(dest="reviews_command", required=True)

    reviews_list = reviews_subcommands.add_parser("list", help="List review items (default: only open).")
    reviews_list.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    reviews_list.add_argument("--session", dest="session_id", default=None, help="Session id. Defaults to latest active session.")
    reviews_list.add_argument("--all", action="store_true", help="Include closed (approved/rejected/resolved) items.")
    reviews_list.add_argument("--json", action="store_true", help="Print raw JSON list.")
    reviews_list.set_defaults(handler=cmd_autonomous_reviews_list)

    reviews_show = reviews_subcommands.add_parser("show", help="Print a review item's full evidence + suggested commands.")
    reviews_show.add_argument("review_id")
    reviews_show.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    reviews_show.add_argument("--session", dest="session_id", default=None, help="Session id.")
    reviews_show.add_argument("--json", action="store_true", help="Print raw JSON dump.")
    reviews_show.set_defaults(handler=cmd_autonomous_reviews_show)

    reviews_approve = reviews_subcommands.add_parser(
        "approve",
        help="HUMAN OVERRIDE: approve a needs-human-review/needs-more-context/failed-apply review and (when applicable) safe-apply the candidate.",
    )
    reviews_approve.add_argument("review_id")
    reviews_approve.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    reviews_approve.add_argument("--session", dest="session_id", default=None, help="Session id.")
    reviews_approve.add_argument("--yes", action="store_true", required=True, help="Required confirmation flag.")
    reviews_approve.set_defaults(handler=cmd_autonomous_reviews_approve)

    reviews_reject = reviews_subcommands.add_parser(
        "reject",
        help="Reject a review item; the underlying task is marked blocked.",
    )
    reviews_reject.add_argument("review_id")
    reviews_reject.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    reviews_reject.add_argument("--session", dest="session_id", default=None, help="Session id.")
    reviews_reject.add_argument("--reason", required=True, help="Required human-readable reject reason.")
    reviews_reject.set_defaults(handler=cmd_autonomous_reviews_reject)

    reviews_resolve = reviews_subcommands.add_parser(
        "resolve",
        help="Mark a review resolved (e.g. user fixed it manually outside the controller).",
    )
    reviews_resolve.add_argument("review_id")
    reviews_resolve.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    reviews_resolve.add_argument("--session", dest="session_id", default=None, help="Session id.")
    reviews_resolve.add_argument("--note", required=True, help="Required human-readable resolution note.")
    reviews_resolve.add_argument(
        "--mark-task",
        choices=["pending", "completed", "blocked"],
        default=None,
        help="Optional follow-up action on the underlying task.",
    )
    reviews_resolve.set_defaults(handler=cmd_autonomous_reviews_resolve)

    runs_parser = subcommands.add_parser(
        "agentic-runs",
        help="Inspect agentic_project runs (list and per-run summary).",
    )
    runs_subcommands = runs_parser.add_subparsers(dest="runs_command", required=True)

    runs_list = runs_subcommands.add_parser(
        "list",
        help="List all agentic_project runs for a project, most recent first.",
    )
    runs_list.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    runs_list.add_argument("--json", action="store_true", help="Print raw JSON list.")
    runs_list.set_defaults(handler=cmd_agentic_runs_list)

    runs_show = runs_subcommands.add_parser(
        "show",
        help="Show a single run's high-level summary across all candidates.",
    )
    runs_show.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    runs_show.add_argument("--run", dest="run_id", required=True, help="Run id (required).")
    runs_show.add_argument("--json", action="store_true", help="Print raw JSON dump.")
    runs_show.set_defaults(handler=cmd_agentic_runs_show)

    candidates_apply = candidates_subcommands.add_parser(
        "apply",
        help="Apply a candidate patch to the project working tree (refuses unless every Apply Gate passes).",
    )
    candidates_apply.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    candidates_apply.add_argument("--run", dest="run_id", default=None, help="Run id. Defaults to latest run.")
    candidates_apply.add_argument(
        "--candidate",
        required=True,
        help="Candidate id (e.g. candidate-b) or `selected` to use promotion.selected_candidate.",
    )
    apply_mode = candidates_apply.add_mutually_exclusive_group(required=True)
    apply_mode.add_argument("--dry-run", action="store_true", help="Run all Apply Gate checks, do not modify the worktree.")
    apply_mode.add_argument("--yes", action="store_true", help="Run all Apply Gate checks AND apply the patch to the working tree.")
    candidates_apply.set_defaults(handler=cmd_agentic_candidates_apply)

    # RC-4A.1: Change Request Mode foundation. The `change` group is the
    # change-mode counterpart to `autonomous`: where autonomous drives a
    # greenfield project from requirements.md, change drives a SINGLE
    # modification to an existing project from change-request.md. RC-4A.1
    # ships only the artifact + CLI foundation; `change run` wiring to
    # AutonomousController lands in RC-4A.2.
    change_parser = subcommands.add_parser(
        "change",
        help="RC-4A: drive a single modification to an existing project from a change-request.md.",
    )
    change_subcommands = change_parser.add_subparsers(dest="change_command", required=True)

    change_new = change_subcommands.add_parser(
        "new",
        help="Create a change session from a change-request.md (parses input, scans repo, writes artifacts; does NOT run Codex).",
    )
    change_new.add_argument("--from", dest="from_path", required=True, help="Path to change-request.md")
    change_new.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    change_new.add_argument("--json", action="store_true", help="Print raw JSON of the created change.")
    change_new.set_defaults(handler=cmd_change_new)

    change_list = change_subcommands.add_parser(
        "list",
        help="List all change sessions for the project (oldest first).",
    )
    change_list.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    change_list.add_argument("--json", action="store_true", help="Print raw JSON list.")
    change_list.set_defaults(handler=cmd_change_list)

    change_show = change_subcommands.add_parser(
        "show",
        help="Show a change session's contract + artifact paths.",
    )
    change_show.add_argument("change_id", nargs="?", default="latest", help="Change id, or 'latest' (default).")
    change_show.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    change_show.add_argument("--json", action="store_true", help="Print raw JSON dump.")
    change_show.set_defaults(handler=cmd_change_show)

    change_status = change_subcommands.add_parser(
        "status",
        help="Show the change session's current state (RC-4A.1: created / ready_for_run / applied / delivered).",
    )
    change_status.add_argument("change_id", nargs="?", default="latest", help="Change id, or 'latest' (default).")
    change_status.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    change_status.add_argument("--json", action="store_true", help="Print raw JSON dump.")
    change_status.set_defaults(handler=cmd_change_status)

    change_validate = change_subcommands.add_parser(
        "validate",
        help="Validate the change session's persisted artifacts (change-contract.json + delivery-report.md if present).",
    )
    change_validate.add_argument("change_id", nargs="?", default="latest", help="Change id, or 'latest' (default).")
    change_validate.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    change_validate.add_argument("--json", action="store_true", help="Print raw JSON validation report.")
    change_validate.set_defaults(handler=cmd_change_validate)

    change_run = change_subcommands.add_parser(
        "run",
        help="RC-4A.2: run the change end-to-end via the autonomous pipeline (1-task task-graph).",
    )
    change_run.add_argument("change_id", nargs="?", default="latest", help="Change id, or 'latest' (default).")
    change_run.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    change_run.add_argument("--json", action="store_true", help="Print raw JSON result.")
    change_run.add_argument(
        "--allow-dirty-worktree",
        action="store_true",
        help="Skip the pre-run worktree-clean check (override at your own risk).",
    )
    change_run.set_defaults(handler=cmd_change_run)

    workflows_parser = subcommands.add_parser("workflows", help="List workflow configs.")
    workflows_parser.set_defaults(handler=cmd_workflows)

    teams_parser = subcommands.add_parser("teams", help="Plan downstream agent teams.")
    teams_subcommands = teams_parser.add_subparsers(dest="teams_command", required=True)

    teams_plan = teams_subcommands.add_parser(
        "plan",
        help="Generate UI, developer, QA, and review team plans from post-build product review.",
    )
    teams_plan.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    teams_plan.set_defaults(handler=cmd_teams_plan)

    teams_review = teams_subcommands.add_parser(
        "review",
        help="Review and optimize all agent teams.",
    )
    teams_review.add_argument("--project", default=None, help="Project id. Defaults to latest project.")
    teams_review.set_defaults(handler=cmd_teams_review)
    return parser


def cmd_init(args: argparse.Namespace) -> None:
    paths = resolve_paths(args.root)
    initialize_workspace(paths)
    print(f"Initialized Local Agent Dev Studio at {paths.root}")
    print(f"SQLite: {paths.db_path}")
    print(f"Projects: {paths.projects_dir}")


def cmd_new(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    from_path = getattr(args, "from_path", None)
    idea = args.idea
    if from_path and not idea:
        # Use the requirements file's H1 (or filename) as a fallback idea.
        try:
            md_text = Path(from_path).read_text(encoding="utf-8")
            for line in md_text.splitlines():
                if line.startswith("# "):
                    idea = line[2:].strip()
                    break
        except OSError:
            pass
        if not idea:
            idea = Path(from_path).stem
    if not idea:
        raise SystemExit("error: provide either an idea positional arg or --from <requirements.md>")
    project = engine.create_project(idea, paths.projects_dir, args.name)
    print(f"Created project: {project['name']}")
    print(f"Project id: {project['id']}")
    print(f"Path: {project['path']}")
    if from_path:
        from orchestrator.core.autonomous import ingest_requirements
        task_graph = ingest_requirements(Path(project["path"]), Path(from_path))
        print(f"Ingested requirements: {Path(from_path).resolve()}")
        print(f"Generated artifacts:")
        print(f"  - {Path(project['path']) / 'requirements.md'}")
        print(f"  - {Path(project['path']) / 'prd.md'}")
        print(f"  - {Path(project['path']) / 'acceptance-criteria.json'}")
        print(f"  - {Path(project['path']) / 'architecture.md'}")
        print(f"  - {Path(project['path']) / 'task-graph.json'}")
        print(f"Tasks: {len(task_graph.get('tasks') or [])}")
        print()
        print("Next: ./agent-studio autonomous start")


def cmd_run(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)

    # Guard against accidentally starting a second run while the first is
    # still mid-flight or waiting on a gate. Without this guard, `run` always
    # creates a fresh run id, hiding any pending approval the previous run
    # was holding. Use `approve <gate>` to advance an existing run, or
    # `--force` to explicitly start a new parallel run.
    existing = engine.latest_run(project_id)
    if existing and existing["status"] in {"running", "needs_approval"} and not getattr(args, "force", False):
        raise SystemExit(
            f"error: project already has an active run {existing['id']} "
            f"(status={existing['status']}, phase={existing['current_phase']}).\n"
            f"  - to advance an awaiting-gate run: python3 -m orchestrator.cli approve <gate>\n"
            f"  - to start a fresh parallel run anyway: pass --force\n"
            f"  - to abandon the existing run: cancel it via SQL or delete the project"
        )

    if getattr(args, "autonomous", False):
        os.environ["LOCALAGENTS_AUTONOMOUS"] = "1"

    if args.workflow == "agentic_project":
        project = engine.require_project(project_id)
        patch_worker = (
            args.agentic_patch_worker
            or os.environ.get("LOCALAGENTS_AGENTIC_PATCH_WORKER")
            or "none"
        )
        execute_eval = bool(args.agentic_execute_eval or os.environ.get("LOCALAGENTS_AGENTIC_EXECUTE_EVAL") == "1")
        result = AgenticProjectRuntime(engine.db).run(
            project=project,
            patch_worker=patch_worker,
            execute_eval=execute_eval,
            model=args.agentic_model or os.environ.get("LOCALAGENTS_AGENTIC_MODEL") or "gpt-5.5",
            timeout_sec=int(args.agentic_timeout or 900),
            max_repair_loops=int(
                args.agentic_repair_loops
                if args.agentic_repair_loops is not None
                else os.environ.get("LOCALAGENTS_AGENTIC_REPAIR_LOOPS") or 0
            ),
            candidate_count=int(
                args.agentic_candidate_count
                if args.agentic_candidate_count is not None
                else os.environ.get("LOCALAGENTS_AGENTIC_CANDIDATE_COUNT") or 3
            ),
        )
        _print_agentic_run_result(result)
        return

    result = engine.run(project_id, args.workflow)
    _print_run_result(result)


def cmd_resume(args: argparse.Namespace) -> None:
    """Resume an existing run from its last incomplete phase.

    If ``run_id`` is omitted we resume the latest run on the project.
    """
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    run_id = args.run_id
    if not run_id:
        project_id = args.project or _latest_project_id(engine)
        run = engine.latest_run(project_id, include_cancelled=True)
        if not run:
            raise SystemExit(f"error: no run found for project {project_id}")
        run_id = run["id"]

    run = engine.require_run(run_id)
    if run["status"] == "completed":
        print(f"Run {run_id} is already completed. Nothing to resume.")
        return
    if run["status"] == "cancelled":
        raise SystemExit(
            f"error: run {run_id} is cancelled. Start a new run with `run` instead "
            f"or update the DB to clear the cancelled flag."
        )

    if getattr(args, "autonomous", False):
        os.environ["LOCALAGENTS_AUTONOMOUS"] = "1"

    result = engine.resume(run_id)
    _print_run_result(result)


def cmd_prd_prepare(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    pack = ManualCodexPrdAgent(db).prepare_prompt_pack(
        project=project,
        run_id=run["id"] if run else None,
    )
    print("Prepared manual Codex PRD prompt pack.")
    print(f"Prompt: {pack.prompt_path}")
    print(f"Template: {pack.template_path}")
    print(f"Schema: {pack.schema_path}")


def cmd_prd_research(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    provider = None
    if args.mock:
        from orchestrator.tools.search_tools import MockSearchProvider

        provider = MockSearchProvider()
    result = PrdResearchAgent(provider=provider, db=db).run(
        project=project,
        run_id=run["id"] if run else None,
        max_queries=args.max_queries,
        results_per_query=args.results_per_query,
    )
    print(f"Research provider: {result.provider}")
    print(f"Queries: {len(result.queries)}")
    print(f"Sources: {len(result.sources)}")
    print(f"Research: {result.research_path}")
    print(f"Sources JSON: {result.sources_path}")
    if result.research_v2:
        print(f"Research plan: {result.research_v2.research_plan_path}")
        print(f"Research planner JSON: {result.research_v2.research_planner_json_path}")
        print(f"Source quality: {result.research_v2.source_quality_path}")
        print(f"Reference products: {result.research_v2.reference_products_path}")
        print(f"Reference critic: {result.research_v2.reference_critic_path}")
        print(f"Feature patterns: {result.research_v2.feature_patterns_path}")
        print(f"UX patterns: {result.research_v2.ux_patterns_path}")
        print(f"PM benchmarks: {result.research_v2.product_management_benchmarks_path}")
        print(f"Evidence chain: {result.research_v2.evidence_chain_path}")
        print(f"Evidence gate: {result.research_v2.evidence_gate_path}")
        print(f"Visual reference analysis: {result.research_v2.visual_reference_analysis_path}")


def cmd_prd_research_v2(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = PrdResearchV2Agent(db).run(
        project=project,
        run_id=run["id"] if run else None,
    )
    print("Generated PRD Research v2 artifacts.")
    print(f"Research plan: {result.research_plan_path}")
    print(f"Research planner JSON: {result.research_planner_json_path}")
    print(f"Source quality: {result.source_quality_path}")
    print(f"Reference products: {result.reference_products_path}")
    print(f"Reference critic: {result.reference_critic_path}")
    print(f"Feature patterns: {result.feature_patterns_path}")
    print(f"UX patterns: {result.ux_patterns_path}")
    print(f"PM benchmarks: {result.product_management_benchmarks_path}")
    print(f"Evidence chain: {result.evidence_chain_path}")
    print(f"Evidence gate: {result.evidence_gate_path}")
    print(f"Screenshot plan: {result.screenshots_readme_path}")
    print(f"Visual reference analysis: {result.visual_reference_analysis_path}")


def cmd_prd_visual_research(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    result = ReferenceVisualResearchAgent().run(project=project, limit=args.limit)
    print("Captured reference visual research screenshots.")
    print(f"Attempted: {result.attempted}")
    print(f"Captured: {result.captured}")
    print(f"Report: {result.report_path}")
    print(f"Manifest: {result.manifest_path}")
    print(f"Screenshots: {result.screenshots_dir}")


def cmd_prd_discover_examples(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    result = ReferenceExampleDiscoveryAgent().run(
        project=project,
        limit=args.limit,
        per_seed=args.per_seed,
        max_per_source=args.max_per_source,
        capture=not args.no_screenshots,
        include_mobile=not args.desktop_only,
        progress=(lambda message: print(message, flush=True)) if args.progress else None,
    )
    print("Discovered concrete reference examples.")
    print(f"Seeds scanned: {result.seeds_scanned}")
    print(f"Candidates found: {result.candidates_found}")
    print(f"Selected examples: {result.selected_examples}")
    print(f"Screenshots attempted: {result.captures_attempted}")
    print(f"Screenshots captured: {result.captures_captured}")
    print(f"Report: {result.report_path}")
    print(f"Examples JSON: {result.examples_json_path}")
    print(f"Candidates JSON: {result.candidates_path}")
    print(f"Visual critic: {result.visual_critic_path}")
    print(f"Visual critic JSON: {result.visual_critic_json_path}")
    print(f"Screenshots: {result.screenshots_dir}")


def cmd_prd_critique_examples(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    result = ExampleVisualCriticAgent().run(project=project)
    print("Generated example visual critic.")
    print(f"Status: {result.status}")
    print(f"Examples: {result.example_count}")
    print(f"Screenshot-backed: {result.screenshot_backed}")
    print(f"Image-analyzed: {result.image_analyzed}")
    print(f"Report: {result.report_path}")
    print(f"JSON: {result.json_path}")


def cmd_prd_multimodal_critic(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    if args.provider != "codex-cli":
        raise ValueError("Only --provider codex-cli is currently supported.")
    result = CodexCliMultimodalCriticAgent().run(
        project=project,
        model=args.model,
        limit=args.limit,
        timeout_seconds=args.timeout,
    )
    print("Generated Codex CLI multimodal critic.")
    print(f"Status: {result.status}")
    print(f"Return code: {result.returncode}")
    print(f"Images: {result.image_count}")
    print(f"Prompt: {result.prompt_path}")
    print(f"Output: {result.output_path}")
    print(f"Report: {result.report_path}")
    print(f"JSON: {result.json_path}")


def cmd_prd_benchmark(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = PrdBenchmarkAgent(db).run(
        project=project,
        run_id=run["id"] if run else None,
    )
    print("Generated local PRD benchmark library.")
    print(f"Index: {result.index_path}")
    print(f"Domain template: {result.domain_template_path}")
    print(f"Quality gates: {result.quality_gates_path}")
    print(f"Decision playbook: {result.decision_playbook_path}")
    print(f"Development handoff: {result.development_handoff_path}")
    print(f"Library JSON: {result.library_json_path}")


def cmd_prd_options(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = PrdOptionsAgent(db).generate(
        project=project,
        run_id=run["id"] if run else None,
    )
    print("Generated PRD options.")
    print(f"Options: {len(result.options)}")
    print(f"Recommended: {result.recommended_option_id}")
    print(f"Options doc: {result.options_md_path}")
    print(f"PM review: {result.review_md_path}")
    print(f"Options JSON: {result.options_json_path}")
    print(f"Next: ./agent-studio prd select {result.recommended_option_id}")


def cmd_prd_select(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    decision_path = PrdOptionsAgent(db).select(
        project=project,
        run_id=run["id"] if run else None,
        option_id=args.option_id,
        notes=args.notes,
    )
    print(f"Selected PRD option: {args.option_id}")
    print(f"Decision: {decision_path}")
    print("Next: ./agent-studio prd draft --import")


def cmd_prd_council(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    agent = PrdCouncilAgent(db)
    if args.prepare and args.import_dir:
        raise ValueError("Use either --prepare or --import-dir, not both.")
    if args.prepare:
        pack = agent.prepare_prompt_pack(project=project, run_id=run["id"] if run else None)
        print("Prepared manual PRD council prompt pack.")
        print(f"Index: {pack.index_path}")
        print(f"Directory: {pack.directory}")
        print("Role prompts:")
        for path in pack.role_prompt_paths:
            print(f"- {path}")
        return
    if args.import_dir:
        result = agent.import_role_outputs(
            project=project,
            run_id=run["id"] if run else None,
            input_dir=Path(args.import_dir),
        )
        print("Imported PRD council outputs.")
        print(f"Roles: {len(result.roles)}")
        for role in result.roles:
            print(f"- {role.name}: {role.path}")
        print(f"Debate: {result.debate_path}")
        print("Next: ./agent-studio prd draft --import")
        return
    result = agent.generate(
        project=project,
        run_id=run["id"] if run else None,
    )
    print("Generated PRD council outputs.")
    print(f"Roles: {len(result.roles)}")
    for role in result.roles:
        print(f"- {role.name}: {role.path}")
    print(f"Debate: {result.debate_path}")
    print("Next: ./agent-studio prd draft --import")


def cmd_prd_import(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    validation = ManualCodexPrdAgent(db).import_result(
        project=project,
        run_id=run["id"] if run else None,
        input_path=Path(args.path),
    )
    _print_prd_validation(validation)
    if not validation.ok:
        raise ValueError("PRD import failed validation.")
    if not args.no_approve and _has_pending_prd_approval(engine, project_id):
        result = engine.approve(project_id, "prd")
        print("PRD gate approved from valid imported artifacts.")
        _print_run_result(result)


def cmd_prd_draft(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    run_id = run["id"] if run else None
    output_path = Path(args.output) if args.output else None
    agent = LocalPrdDraftAgent(db)
    if args.do_import:
        path, validation = agent.draft_and_import(
            project=project,
            run_id=run_id,
            output_path=output_path,
        )
        print(f"Draft: {path}")
        _print_prd_validation(validation)
        if not validation.ok:
            raise ValueError("Generated PRD draft failed validation.")
        if not args.no_approve and _has_pending_prd_approval(engine, project_id):
            result = engine.approve(project_id, "prd")
            print("PRD gate approved from valid generated draft.")
            _print_run_result(result)
    else:
        path = agent.draft(project=project, run_id=run_id, output_path=output_path)
        print(f"Draft: {path}")
        print("Next: ./agent-studio prd import " + str(path))


def cmd_prd_validate(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    validation = validate_prd_files(Path(project["path"]))
    _print_prd_validation(validation)
    if not validation.ok:
        raise ValueError("PRD validation failed.")


def cmd_prd_score(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = PrdScoreAgent(db).run(project=project, run_id=run["id"] if run else None)
    evaluation = result.evaluation
    print(f"PRD score: {evaluation.final_score}/{evaluation.max_score}")
    print(f"Status: {evaluation.status}")
    print(f"Score report: {result.score_md_path}")
    print(f"Score JSON: {result.score_json_path}")
    if evaluation.hard_failures:
        print("Hard failures:")
        for failure in evaluation.hard_failures:
            print(f"- {failure}")
    if evaluation.status != "pass":
        raise ValueError("PRD score failed quality gates.")


def cmd_prd_critique(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = PrdCritiqueAgent(db).run(project=project, run_id=run["id"] if run else None)
    evaluation = result.score_result.evaluation
    print("Generated PRD critique.")
    print(f"Status: {evaluation.status}")
    print(f"PRD score: {evaluation.final_score}/{evaluation.max_score}")
    print(f"Critique: {result.critique_path}")
    print(f"Score report: {result.score_result.score_md_path}")
    if evaluation.hard_failures:
        print("Hard failures:")
        for failure in evaluation.hard_failures:
            print(f"- {failure}")
    if evaluation.status != "pass":
        raise ValueError("PRD critique found hard quality gate failures.")


def cmd_prd_product_fit(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = PrdProductFitAgent(db).run(project=project, run_id=run["id"] if run else None)
    evaluation = result.evaluation
    print(f"Product-fit score: {evaluation.final_score}/{evaluation.max_score}")
    print(f"Status: {evaluation.status}")
    print(f"Verdict: {evaluation.verdict}")
    print(f"Product-fit report: {result.product_fit_md_path}")
    print(f"Product-fit JSON: {result.product_fit_json_path}")
    if evaluation.hard_failures:
        print("Hard failures:")
        for failure in evaluation.hard_failures:
            print(f"- {failure}")
    if evaluation.status != "pass":
        raise ValueError("Product-fit gate failed.")


def cmd_prd_team_review(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = PrdTeamReviewAgent(db).run(project=project, run_id=run["id"] if run else None)
    print("Generated PRD agent team review.")
    print(f"Team review: {result.review_path}")
    print(f"Optimized workflow: {result.optimized_workflow_path}")
    print(f"Contracts JSON: {result.contracts_json_path}")


def cmd_prd_build_review(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = ProductBuildReviewAgent(db).run(project=project, run_id=run["id"] if run else None)
    evaluation = result.evaluation
    print(f"Post-build product review: {evaluation.final_score}/{evaluation.max_score}")
    print(f"Status: {evaluation.status}")
    print(f"Verdict: {evaluation.verdict}")
    print(f"Review: {result.review_md_path}")
    print(f"Review JSON: {result.review_json_path}")
    print(f"Downstream team plan: {result.downstream_team_plan_path}")
    if evaluation.blockers:
        print("Blockers:")
        for blocker in evaluation.blockers:
            print(f"- {blocker}")


def cmd_design_draft(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = UiDesignerAgent(db).generate_design(project=project, run_id=run["id"] if run else None)
    print("Generated design draft.")
    print(f"User flow: {result.user_flow_path}")
    print(f"Design system: {result.design_system_path}")
    print(f"Component spec: {result.component_spec_path}")
    print("Next: ./agent-studio design critique")


def cmd_design_critique(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = DesignCritiqueAgent(db).run(project=project, run_id=run["id"] if run else None)
    evaluation = result.evaluation
    print(f"Design score: {evaluation.final_score}/{evaluation.max_score}")
    print(f"Status: {evaluation.status}")
    print(f"Verdict: {evaluation.verdict}")
    print(f"Design critique: {result.critique_md_path}")
    print(f"Design critique JSON: {result.critique_json_path}")
    if evaluation.hard_failures:
        print("Hard failures:")
        for failure in evaluation.hard_failures:
            print(f"- {failure}")
    if evaluation.status != "pass":
        raise ValueError("Design critique failed quality gates.")


def cmd_design_team(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = UiProductTeamAgent(db).run(project=project, run_id=run["id"] if run else None)
    score = json.loads(result.score_json_path.read_text(encoding="utf-8"))
    print("Generated UI Product Team package.")
    print(f"Status: {score['status']}")
    print(f"Score: {score['final_score']}/{score['max_score']}")
    print(f"UX Flow Lead: {result.ux_flow_lead_path}")
    print(f"Visual Design Lead: {result.visual_design_lead_path}")
    print(f"Asset Strategy Lead: {result.asset_strategy_lead_path}")
    print(f"Visual QA Lead: {result.visual_qa_lead_path}")
    print(f"Design Critic: {result.design_critic_path}")
    print(f"Reference traceability: {result.reference_traceability_path}")
    print(f"Screen spec: {result.screen_spec_path}")
    print(f"Template spec: {result.template_spec_path}")
    print(f"Lead synthesis: {result.lead_synthesis_path}")
    print(f"Dev handoff: {result.dev_handoff_path}")
    print(f"Visual QA checklist: {result.visual_qa_checklist_path}")
    print(f"Design contract JSON: {result.design_contract_json_path}")
    print(f"Contracts JSON: {result.contracts_json_path}")


def cmd_design_review_variants(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    if args.provider != "codex-cli":
        raise ValueError("Only --provider codex-cli is currently supported.")
    result = VisualVariantMultimodalReviewAgent().run(
        project=project,
        model=args.model,
        timeout_seconds=args.timeout,
    )
    print("Generated visual direction multimodal review.")
    print(f"Status: {result.status}")
    print(f"Return code: {result.returncode}")
    print(f"Images: {result.image_count}")
    print(f"Winner: {result.winner_id or 'not parsed'}")
    print(f"Prompt: {result.prompt_path}")
    print(f"Output: {result.output_path}")
    print(f"Review: {result.report_path}")
    print(f"Review JSON: {result.json_path}")
    print(f"Selected direction: {result.selected_path}")


def cmd_architecture_draft(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    result = ArchitectAgent().generate_plan(
        project_path=Path(project["path"]),
        idea=project["idea"],
    )
    print("Generated architecture draft.")
    print(f"Status: {result.status}")
    print(f"Summary: {result.summary}")
    if result.artifacts:
        print("Artifacts:")
        for artifact in result.artifacts:
            print(f"- {Path(project['path']) / artifact}")


def cmd_implementation_draft(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    result = DeveloperAgent(Path(project["path"])).implement_generated_tasks()
    print("Generated implementation draft.")
    print(f"Status: {result.status}")
    print(f"Summary: {result.summary}")
    if result.artifacts:
        print("Artifacts:")
        for artifact in result.artifacts:
            print(f"- {Path(project['path']) / artifact}")
    if result.status != "completed":
        raise ValueError(result.summary)


def cmd_implementation_team(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = DeveloperTeamAgent(db).run(project=project, run_id=run["id"] if run else None)
    score = json.loads(result.score_json_path.read_text(encoding="utf-8"))
    print("Generated Developer Team package.")
    print(f"Status: {score['status']}")
    print(f"Score: {score['final_score']}/{score['max_score']}")
    print(f"Editor Workflow Developer: {result.editor_workflow_path}")
    print(f"Preview/Export Developer: {result.preview_export_path}")
    print(f"Asset Handling Developer: {result.asset_handling_path}")
    print(f"Browser Test Developer: {result.browser_test_path}")
    print(f"Integration Lead: {result.integration_lead_path}")
    print(f"Implementation contract JSON: {result.implementation_contract_path}")
    print(f"Task plan JSON: {result.task_plan_path}")
    print(f"Acceptance matrix: {result.acceptance_matrix_path}")


def cmd_implementation_harden(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    result = ImplementationHardeningAgent(Path(project["path"])).run(target=args.target)
    print("Generated implementation hardening pass.")
    print(f"Status: {result.status}")
    print(f"Summary: {result.summary}")
    if result.artifacts:
        print("Artifacts:")
        for artifact in result.artifacts:
            print(f"- {Path(project['path']) / artifact}")
    if result.status != "completed":
        raise ValueError(result.summary)


def cmd_status(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    status = engine.status(args.project)
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
        return
    _print_status(status)


def cmd_approve(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    result = engine.approve(project_id, args.target)
    print(f"Approved: {args.target}")
    _print_run_result(result)


def cmd_reject(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    result = engine.reject(project_id, args.target, args.reason)
    print(f"Rejected: {args.target}")
    _print_run_result(result)


def cmd_retry(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    result = engine.retry(project_id, args.phase)
    print(f"Retry: {args.phase}")
    _print_run_result(result)


def cmd_logs(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    run_id = args.run_id
    if not run_id:
        project_id = args.project or _latest_project_id(engine)
        run = engine.latest_run(project_id)
        if not run:
            raise ValueError("No run found.")
        run_id = run["id"]
    events = EventBus(Database(paths.db_path)).list_for_run(run_id)
    for event in events:
        phase = f" [{event['phase_id']}]" if event.get("phase_id") else ""
        print(f"{event['id']:04d} {event['created_at']} {event['type']}{phase} {event['message']}")


def cmd_diff(args: argparse.Namespace) -> None:
    print(f"No code diff is recorded for {args.task_id} in the deterministic Phase 1 MVP.")


def cmd_agents(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    agents = AgentRegistry(paths.agents_dir).load_all()
    for agent_id, config in agents.items():
        print(f"{agent_id}: {config.get('name', agent_id)} ({config.get('model', 'unknown')})")


def cmd_run_agent(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    agent_config = AgentRegistry(paths.agents_dir).require(args.agent_id)

    if args.materialize and args.agent_id == "product_manager":
        result = ProductManagerAgent().generate_prd(
            project_path=Path(project["path"]),
            idea=project["idea"],
        )
    elif args.materialize and args.agent_id == "architect":
        result = ArchitectAgent().generate_plan(
            project_path=Path(project["path"]),
            idea=project["idea"],
        )
    elif args.materialize and args.agent_id == "ui_designer":
        result = UiDesignerAgent().generate_design_result(
            project_path=Path(project["path"]),
            idea=project["idea"],
        )
    elif args.materialize and args.agent_id == "developer":
        result = DeveloperAgent(Path(project["path"])).implement_generated_tasks()
    elif args.materialize and args.agent_id == "qa":
        result = QAAgent(Path(project["path"])).run_checks()
    elif args.materialize and args.agent_id == "reviewer":
        result = ReviewerAgent(Path(project["path"])).review_diff()
    else:
        result = AgentRunner(cost_tracker=CostTracker(db)).run_task(
            agent_config,
            AgentContext(
                project_id=project_id,
                run_id=run["id"] if run else None,
                project_path=Path(project["path"]),
                idea=project["idea"],
                instructions=args.instructions,
                output_paths=list(agent_config.get("required_outputs") or []),
            ),
        )

    print(f"Agent: {args.agent_id}")
    print(f"Status: {result.status}")
    print(f"Summary: {result.summary}")
    if result.artifacts:
        print("Artifacts:")
        for artifact in result.artifacts:
            print(f"- {artifact}")


def cmd_costs(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    run_id = args.run_id
    if not run_id:
        project_id = args.project or _latest_project_id(engine)
        run = engine.latest_run(project_id)
        if not run:
            raise ValueError("No run found.")
        run_id = run["id"]
    totals = CostTracker(Database(paths.db_path)).totals_for_run(run_id)
    print(f"Run id: {run_id}")
    print(f"Input tokens: {totals['input_tokens']}")
    print(f"Output tokens: {totals['output_tokens']}")
    print(f"Cost USD: {totals['cost_usd']:.6f}")


def cmd_workflows(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    for path in sorted(paths.workflows_dir.glob("*.yaml")):
        print(path.stem)


def cmd_diagnose(args: argparse.Namespace) -> None:
    """Scan a run for stub fallbacks and validation failures, print fix hints."""
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    db = Database(paths.db_path)
    run_id = args.run_id
    if not run_id:
        project_id = args.project or _latest_project_id(engine)
        run = engine.latest_run(project_id)
        if not run:
            raise ValueError("No run found.")
        run_id = run["id"]

    fallback_rows = db.query_all(
        "SELECT phase_id, message FROM events "
        "WHERE run_id = ? AND type = 'phase.llm_fallback' "
        "ORDER BY id",
        (run_id,),
    )
    validation_rows = db.query_all(
        "SELECT phase_id, message FROM events "
        "WHERE run_id = ? AND type = 'phase.validation_failed' "
        "ORDER BY id",
        (run_id,),
    )
    artifact_rows = db.query_all(
        "SELECT phase_id, path, summary FROM artifacts "
        "WHERE run_id = ? AND COALESCE(is_current, 1) = 1 "
        "ORDER BY phase_id, path",
        (run_id,),
    )

    print(f"Run id: {run_id}")
    print(f"Total artifacts: {len(artifact_rows)}")
    print()

    # Group artifacts by source (llm/stub/mixed) using the stored summary.
    stub_artifacts: list[tuple[str, str]] = []
    for row in artifact_rows:
        summary = row["summary"] or ""
        if "via stub" in summary:
            stub_artifacts.append((row["phase_id"], row["path"]))

    if fallback_rows:
        print(f"=== LLM fallbacks ({len(fallback_rows)} phase(s) failed at least once) ===")
        for row in fallback_rows:
            print(f"  [{row['phase_id']}] {row['message']}")
        print()
    else:
        print("LLM fallbacks: none")
        print()

    if validation_rows:
        print(f"=== Format validation failures ({len(validation_rows)}) ===")
        for row in validation_rows:
            print(f"  [{row['phase_id']}] {row['message']}")
        print()
    else:
        print("Format validation failures: none")
        print()

    if stub_artifacts:
        print(f"=== Stub-content artifacts ({len(stub_artifacts)}) ===")
        for phase_id, path in stub_artifacts:
            print(f"  [{phase_id}] {path}")
        print()
        print("Suggested fix: re-run the affected agent against the same project, e.g.")
        owners = sorted({phase_id for phase_id, _ in stub_artifacts})
        for owner_phase in owners:
            # phase_id and agent_id usually align (e.g. 'design' phase owned by ui_designer);
            # we can't infer owner without re-loading workflow YAML, so suggest run-agent
            # by phase id and let the user map.
            print(f"  python3 -m orchestrator.cli run-agent <agent_id_for_{owner_phase}> --project <project>")
    else:
        print("Stub-content artifacts: none — every required output came from the LLM.")


def cmd_agentic_abandonments_list(args: argparse.Namespace) -> None:
    """List abandonment records for an agentic_project from its JSONL log."""
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    records = _read_abandonment_history(project_path)
    log_path = project_path / AGENTIC_ABANDONMENT_LOG_RELPATH

    if args.json:
        print(json.dumps(records, ensure_ascii=False, indent=2))
        return

    print(f"Project: {project.get('name') or project_id} ({project_id})")
    print(f"Log: {log_path}")
    if not records:
        print("No abandonment records yet for this project.")
        print(f"(File {'exists but is empty' if log_path.exists() else 'does not exist'}.)")
        return

    rows = []
    for record in records:
        final_failure = record.get("final_failure") or {}
        failure_type = ""
        if isinstance(final_failure, dict):
            failure_type = str(final_failure.get("failure_type") or "")
        rows.append({
            "timestamp": str(record.get("timestamp_utc") or "")[:19],
            "event": (str(record.get("event_type") or "run_abandoned").replace("_abandoned", ""))[:9],
            "run_id": str(record.get("run_id") or "")[:18],
            "candidate": str(record.get("candidate") or "")[:12],
            "patch_worker": str(record.get("patch_worker") or "")[:10],
            "failure_type": failure_type[:18],
            "stop_reason": str(record.get("stop_reason") or "")[:24],
            "attempts": f"{record.get('attempt_count') or 0}/{record.get('max_loops') or 0}",
        })

    headers = ["timestamp", "event", "run_id", "candidate", "patch_worker", "failure_type", "stop_reason", "attempts"]
    widths = {h: max(len(h), max((len(row[h]) for row in rows), default=0)) for h in headers}
    print()
    print("  ".join(h.ljust(widths[h]) for h in headers))
    print("  ".join("-" * widths[h] for h in headers))
    for row in rows:
        print("  ".join(row[h].ljust(widths[h]) for h in headers))
    print()
    print(f"Total: {len(records)} abandonment record(s).")

    # Cross-tab summary by (worker, failure_type) for the gate's soft-signal lens.
    bucket: dict[tuple[str, str], int] = {}
    for record in records:
        final_failure = record.get("final_failure") or {}
        failure_type = ""
        if isinstance(final_failure, dict):
            failure_type = str(final_failure.get("failure_type") or "")
        key = (str(record.get("patch_worker") or ""), failure_type)
        bucket[key] = bucket.get(key, 0) + 1
    if bucket:
        print()
        print("By (patch_worker, failure_type):")
        for (worker, ftype), count in sorted(bucket.items(), key=lambda kv: kv[1], reverse=True):
            marker = "  [pattern: gate would warn]" if count >= 2 else ""
            print(f"  {worker or '<none>'} / {ftype or '<unknown>'}: {count}{marker}")


def cmd_agentic_runs_list(args: argparse.Namespace) -> None:
    """List every agentic_project run for a project, most recent first."""
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    walker = ProjectRunPackages(project_path=project_path)
    runs = walker.runs()

    rows: list[dict[str, Any]] = []
    for run in runs:
        promo = run.promotion_report()
        applied = run.applied_candidate()
        try:
            mtime = run.run_dir.stat().st_mtime
            created = datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(timespec="seconds")
        except OSError:
            created = ""
        rows.append({
            "run_id": run.run_id,
            "created_utc": created,
            "decision": str(promo.get("decision") or ""),
            "selected_candidate": promo.get("selected_candidate"),
            "candidate_count": int(promo.get("candidate_count") or len(run.candidate_ids())),
            "applied": bool(applied),
        })

    if args.json:
        print(json.dumps({
            "project_id": project["id"],
            "project_name": project.get("name"),
            "run_count": len(rows),
            "runs": rows,
        }, ensure_ascii=False, indent=2))
        return

    print(f"Project: {project.get('name') or project['id']} ({project['id']})")
    if not rows:
        print("No agentic_project runs yet.")
        return
    table_rows: list[dict[str, str]] = [
        {
            "run_id": r["run_id"][:18],
            "created": r["created_utc"][:19],
            "decision": r["decision"][:16],
            "selected": str(r["selected_candidate"] or "")[:14],
            "applied": "yes" if r["applied"] else "no",
            "candidates": str(r["candidate_count"]),
        }
        for r in rows
    ]
    headers = ["run_id", "created", "decision", "selected", "applied", "candidates"]
    widths = {h: max(len(h), max((len(row[h]) for row in table_rows), default=0)) for h in headers}
    print()
    print("  ".join(h.ljust(widths[h]) for h in headers))
    print("  ".join("-" * widths[h] for h in headers))
    for row in table_rows:
        print("  ".join(row[h].ljust(widths[h]) for h in headers))
    print()
    applied_count = sum(1 for r in rows if r["applied"])
    promote_count = sum(1 for r in rows if r["decision"] == "promote")
    abandoned_count = sum(1 for r in rows if r["decision"] == "abandoned")
    print(f"Total: {len(rows)} run(s); promote={promote_count}, abandoned={abandoned_count}, applied={applied_count}.")


def cmd_agentic_runs_show(args: argparse.Namespace) -> None:
    """Print a high-level summary of a single agentic_project run."""
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    walker = ProjectRunPackages(project_path=project_path)
    run = walker.run(args.run_id)
    if run is None:
        raise ValueError(f"Run not found in project {project_id}: {args.run_id}")

    promo = run.promotion_report()
    intent = run.intent_contract()
    applied = run.applied_candidate()
    candidate_summaries = list(iter_candidate_summaries(promo))

    # Try to read context_pack for context_quality + prior_learnings count.
    context_path = run.run_dir / "context-pack.json"
    context_quality: dict[str, Any] = {}
    prior_run_count = 0
    prior_learnings_count = 0
    if context_path.exists():
        try:
            ctx = json.loads(context_path.read_text(encoding="utf-8"))
            context_quality = ctx.get("context_quality") or {}
            prior_run_count = int(ctx.get("prior_run_count") or 0)
            prior_learnings_count = len(ctx.get("prior_learnings") or [])
        except (OSError, json.JSONDecodeError):
            pass

    # Count abandonment events for THIS run from the project-level log.
    abandonment_log = _read_abandonment_history(project_path)
    candidate_abandoned = sum(
        1 for r in abandonment_log
        if r.get("run_id") == run.run_id and r.get("event_type") == "candidate_abandoned"
    )
    run_abandoned = sum(
        1 for r in abandonment_log
        if r.get("run_id") == run.run_id and r.get("event_type") == "run_abandoned"
    )

    if args.json:
        payload = {
            "project_id": project["id"],
            "run_id": run.run_id,
            "intent_goal": intent.get("goal"),
            "context_quality": context_quality,
            "prior_run_count": prior_run_count,
            "prior_learnings_count": prior_learnings_count,
            "decision": promo.get("decision"),
            "selected_candidate": promo.get("selected_candidate"),
            "candidate_count": promo.get("candidate_count"),
            "candidates": candidate_summaries,
            "candidate_diversity": promo.get("candidate_diversity"),
            "abandonment_events": {
                "candidate_abandoned": candidate_abandoned,
                "run_abandoned": run_abandoned,
            },
            "applied": bool(applied),
            "applied_candidate": applied,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"Project: {project.get('name') or project['id']} ({project['id']})")
    print(f"Run: {run.run_id}")
    print(f"Intent goal: {intent.get('goal') or '<none>'}")
    print()
    print("== Context ==")
    if context_quality:
        for key, value in context_quality.items():
            if isinstance(value, dict):
                continue
            print(f"  {key}: {value}")
    print(f"  prior_run_count: {prior_run_count}")
    print(f"  prior_learnings: {prior_learnings_count}")
    print()
    print("== Candidates ==")
    if not candidate_summaries:
        print("  (none — run may pre-date MVP-3A or be incomplete)")
    else:
        for summary in candidate_summaries:
            marker = " [SELECTED]" if summary.get("selected") else ""
            disqualified = " [disqualified]" if summary.get("disqualified") else ""
            print(
                f"  {summary.get('id')} ({summary.get('strategy')}): "
                f"score={summary.get('score')}, "
                f"eval={'pass' if summary.get('required_eval_passed') else ('fail' if summary.get('required_eval_executed') else 'skipped')}, "
                f"repair_attempts={summary.get('repair_attempts')}"
                f"{disqualified}{marker}"
            )
    diversity = promo.get("candidate_diversity") or {}
    if diversity:
        print(f"  candidate_diversity: average={diversity.get('average')} ({diversity.get('method')})")
    print()
    print("== Promotion ==")
    print(f"  decision: {promo.get('decision') or '<none>'}")
    print(f"  selected_candidate: {promo.get('selected_candidate') or 'none'}")
    print()
    print("== Abandonment events for this run ==")
    print(f"  candidate_abandoned: {candidate_abandoned}")
    print(f"  run_abandoned: {run_abandoned}")
    print()
    print("== Apply state ==")
    if applied:
        print(f"  applied: yes (candidate={applied.get('candidate')}, base={applied.get('base_commit')}, sha256={(applied.get('patch_sha256') or '')[:12]}...)")
        print(f"  timestamp: {applied.get('timestamp_utc')}")
    else:
        print("  applied: no")


def cmd_autonomous_start(args: argparse.Namespace) -> None:
    """MVP-4A entrypoint: start (or resume) an autonomous session and advance
    tasks until the session pauses, completes, or hits --max-steps."""
    from orchestrator.core.autonomous import (
        AutonomousController,
        find_active_session,
        is_git_repo,
        is_worktree_clean,
        read_task_graph,
        create_session_branch,
    )
    from orchestrator.core.run_package import apply_selected_candidate

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])

    if not is_git_repo(project_path):
        raise SystemExit(
            f"error: {project_path} is not a git repository.\n"
            "  Initialize one (cd <project> && git init && git add -A && git commit -m 'init') before starting autonomous mode."
        )
    if not getattr(args, "allow_dirty_worktree", False):
        clean, reason = is_worktree_clean(project_path)
        if not clean:
            raise SystemExit(
                f"error: {reason}.\n"
                "  Commit or stash your changes before starting autonomous mode (or pass --allow-dirty-worktree to override)."
            )

    task_graph = read_task_graph(project_path)
    if not task_graph.get("tasks"):
        raise SystemExit(
            f"error: project has no task-graph.json with tasks. Did you run `agent-studio new --from <requirements.md>`?"
        )

    # MVP-4D: resume gating. If a previous session paused with open
    # blocking reviews, refuse to advance until the user has approved /
    # rejected / resolved each one. This is the whole point of the queue:
    # the controller does not silently keep retrying a state that needs
    # human judgment.
    from orchestrator.core.review_queue import list_blocking_open
    existing_session = find_active_session(project_path)
    if existing_session is not None:
        blocking = list_blocking_open(project_path, existing_session.session_id)
        if blocking:
            print(f"Session: {existing_session.session_id}")
            print(f"Status: {existing_session.status}")
            print(f"Refusing to start/resume — {len(blocking)} blocking review(s) open:")
            for r in blocking:
                task_hint = f" task={r.task_id}" if r.task_id else ""
                print(f"  - {r.review_id} [{r.reason_code}]{task_hint} {r.title}")
            print()
            print("Resolve them with one of:")
            print(f"  agent-studio autonomous reviews show <review_id> --project {project['id']}")
            print(f"  agent-studio autonomous reviews approve <review_id> --yes --project {project['id']}")
            print(f"  agent-studio autonomous reviews reject <review_id> --reason '...' --project {project['id']}")
            print(f"  agent-studio autonomous reviews resolve <review_id> --note '...' --project {project['id']}")
            # Log it on the session log too for audit.
            from orchestrator.core.autonomous import controller_log_file as _clf
            log_path = _clf(project_path, existing_session.session_id)
            if log_path.parent.exists():
                with log_path.open("a", encoding="utf-8") as fh:
                    fh.write(json.dumps({
                        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "event": "resume_blocked_by_open_reviews",
                        "open_review_count": len(blocking),
                    }, ensure_ascii=False) + "\n")
            sys.exit(1)

    runtime = AgenticProjectRuntime(engine.db)

    # RC-2B: read agent-studio.yaml's `agentic:` block so the autonomous
    # controller can drive the inner loop with a real patch worker
    # (default still `none` — must be opted in).
    from orchestrator.core.deploy import load_agentic_config
    agentic_config = load_agentic_config(project_path)

    # RC-2C.1.3: hold a mutable session ref so `_run_inner_loop` can
    # read session.budgets["max_candidates_per_task"] at CALL time
    # (the controller passes it in via start_or_resume below). Closure
    # capture, not snapshot — budgets changes during a long session
    # would also propagate.
    _session_ref: dict[str, Any] = {"session": None}

    def _run_inner_loop(*, project: dict[str, Any], intent_overrides: dict[str, Any]) -> AgenticRunResult:
        # Default candidate_count is 3 (CANDIDATE_STRATEGIES len). When
        # autonomous.budgets.max_candidates_per_task is configured AND
        # has a positive int value, propagate it as the runtime's
        # candidate_count. Pre-fix this knob was silently ignored —
        # operators setting `max_candidates_per_task: 1` to control
        # token spend still saw 3 candidates run (RC-2B.2 Observation A).
        max_cand = None
        sess = _session_ref.get("session")
        if sess is not None:
            raw = sess.budgets.get("max_candidates_per_task")
            if isinstance(raw, int) and raw >= 1:
                max_cand = raw
        kwargs: dict[str, Any] = dict(
            project=project,
            intent_overrides=intent_overrides,
            patch_worker=agentic_config.patch_worker,
            execute_eval=(agentic_config.patch_worker == "codex"),
            timeout_sec=agentic_config.codex.timeout_sec,
            codex_sandbox=agentic_config.codex.sandbox,
            codex_ask_for_approval=agentic_config.codex.ask_for_approval,
            codex_command=agentic_config.codex.command,
        )
        if max_cand is not None:
            kwargs["candidate_count"] = max_cand
        return runtime.run(**kwargs)

    def _apply(*, project_path: Path, run_dir: Path, selected_candidate: str) -> dict[str, Any]:
        record = apply_selected_candidate(
            project_path=project_path, run_dir=run_dir, selected_candidate=selected_candidate,
        )
        record["project_id"] = project["id"]
        return record

    controller = AutonomousController(project=project, run_inner_loop=_run_inner_loop, apply_candidate=_apply)
    session = controller.start_or_resume()
    _session_ref["session"] = session
    create_session_branch(project_path, session.session_id)
    print(f"Session: {session.session_id}")
    print(f"Branch: {session.branch}")

    advanced = 0
    max_steps = getattr(args, "max_steps", None)
    while True:
        if max_steps is not None and advanced >= max_steps:
            print(f"Stopped after --max-steps={max_steps} (session is {session.status}).")
            break
        outcome = controller.advance_one_task(session, task_graph)
        if outcome is None:
            print(f"Session {session.status}.{(' Reason: ' + session.pause_reason) if session.pause_reason else ''}")
            break
        advanced += 1
        marker = "ok" if outcome.new_status == "completed" else ("review" if outcome.new_status == "needs-human-review" else "fail")
        print(f"  [{marker}] {outcome.task_id}: decision={outcome.decision}, new_status={outcome.new_status}, commit={outcome.commit or '-'}")
        if session.status != "running":
            print(f"Session {session.status}.{(' Reason: ' + session.pause_reason) if session.pause_reason else ''}")
            break


def cmd_autonomous_status(args: argparse.Namespace) -> None:
    from orchestrator.core.autonomous import find_active_session, read_task_graph
    from orchestrator.core.deploy import load_agentic_config

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    session = find_active_session(project_path)
    task_graph = read_task_graph(project_path)
    tasks = task_graph.get("tasks") or []
    counts = {"completed": 0, "running": 0, "pending": 0, "needs-human-review": 0, "abandoned": 0}
    for t in tasks:
        counts[str(t.get("status") or "")] = counts.get(str(t.get("status") or ""), 0) + 1
    # RC-2B.12: surface patch_worker so operators don't have to read
    # agent-studio.yaml separately. Tolerant: if config is malformed,
    # fall back to "unknown" rather than failing the whole status call.
    try:
        agentic_cfg = load_agentic_config(project_path)
        patch_worker_label = agentic_cfg.patch_worker
    except Exception:  # noqa: BLE001
        agentic_cfg = None
        patch_worker_label = "unknown (agent-studio.yaml parse failed)"

    if args.json:
        from orchestrator.core.review_queue import list_review_items
        review_items = (
            [r.to_dict() for r in list_review_items(project_path, session.session_id)]
            if session is not None else []
        )
        open_reviews = [r for r in review_items if r.get("status") == "open"]
        blocking_reviews = [r for r in open_reviews if r.get("severity") == "blocking"]
        deployment_block = (session.deployment if session and isinstance(session.deployment, dict) else None)
        print(json.dumps({
            "project_id": project["id"],
            "session": session.to_dict() if session else None,
            "task_counts": counts,
            "task_graph_size": len(tasks),
            "review_status": {
                "total": len(review_items),
                "open_review_count": len(open_reviews),
                "blocking_review_count": len(blocking_reviews),
                "items": review_items,
            },
            "deployment": deployment_block,
            "agentic": (agentic_cfg.to_dict() if agentic_cfg is not None else None),
            # RC-2E.2: top-level patch_worker convenience string for
            # consumers that don't want to traverse `agentic.patch_worker`.
            "patch_worker": patch_worker_label,
        }, ensure_ascii=False, indent=2))
        return

    print(f"Project: {project.get('name') or project['id']} ({project['id']})")
    if session is None:
        print("No autonomous session yet. Run `./agent-studio autonomous start`.")
        return
    print(f"Session: {session.session_id}")
    print(f"Status: {session.status}")
    print(f"Branch: {session.branch}")
    print(f"Patch worker: {patch_worker_label}")
    if session.pause_reason:
        print(f"Pause reason: {session.pause_reason}")
    print()
    print("Tasks:")
    for key in ("completed", "running", "pending", "needs-human-review", "abandoned"):
        print(f"  {key}: {counts.get(key, 0)}")
    if session.current_task_id:
        current = next((t for t in tasks if t["id"] == session.current_task_id), None)
        print()
        print("Current task:")
        if current:
            print(f"  {current['id']}  {current['title']}  (status: {current['status']})")
        else:
            print(f"  {session.current_task_id} (not found in graph)")
    print()
    # RC-2D.2: surface override provenance — diff session.budgets against
    # DEFAULT_BUDGETS so operators can tell at a glance whether they're
    # running at controller defaults or with a user-supplied override.
    from orchestrator.core.autonomous import DEFAULT_BUDGETS, DEFAULT_INTEGRATION_POLICY
    overridden_budgets = {k for k, v in session.budgets.items()
                          if DEFAULT_BUDGETS.get(k) != v}
    budgets_label = " (overridden)" if overridden_budgets else " (defaults)"
    print(f"Budget:{budgets_label}")
    for key, limit in session.budgets.items():
        used_key = {
            "max_tasks_per_session": session.counters["completed_tasks"] + session.counters["abandoned_tasks"],
            "max_abandoned_tasks": session.counters["abandoned_tasks"],
            "max_needs_human_review_tasks": session.counters["needs_review_tasks"],
            "max_total_inner_runs": session.counters["inner_runs"],
        }.get(key, "?")
        marker = " *" if key in overridden_budgets else ""
        print(f"  {key}: {used_key} / {limit}{marker}")
    print()
    overridden_policy = {k for k, v in session.integration_policy.items()
                         if DEFAULT_INTEGRATION_POLICY.get(k) != v}
    policy_label = " (overridden)" if overridden_policy else " (defaults)"
    print(f"Integration:{policy_label}")
    counters = session.counters
    print(f"  runs: {counters.get('integrations_run', 0)}, passed: {counters.get('integrations_passed', 0)}, failed: {counters.get('integrations_failed', 0)}")
    last = session.last_integration_result
    if isinstance(last, dict):
        state = "passed" if last.get("passed") else "FAILED"
        failed_names = ", ".join(last.get("failed_required_command_names") or []) or "none"
        print(f"  last: {state} (trigger={last.get('trigger_reason')}, failed_commands={failed_names}, duration={last.get('duration_sec')}s)")
    else:
        print("  last: no integration runs yet")
    print()
    print("Corrective tasks:")
    print(
        f"  created: {counters.get('corrective_tasks_created', 0)}, "
        f"completed: {counters.get('corrective_tasks_completed', 0)}, "
        f"max budget: {session.budgets.get('max_corrective_tasks', 0)}"
    )
    pending_correctives = [
        t for t in tasks
        if t.get("corrective") and t.get("status") in {"pending", "running"}
    ]
    if pending_correctives:
        print("  pending/running:")
        for t in pending_correctives:
            print(f"    - {t['id']} ({t.get('source_failure_type') or 'unknown'}, source_failure_id={t.get('source_failure_id')})")

    # MVP-4D: review queue summary
    from orchestrator.core.review_queue import list_review_items
    if session is not None:
        all_reviews = list_review_items(project_path, session.session_id)
        open_reviews = [r for r in all_reviews if r.status == "open"]
        blocking_open = [r for r in open_reviews if r.severity == "blocking"]
        print()
        print("Review queue:")
        print(f"  open: {len(open_reviews)}")
        print(f"  blocking: {len(blocking_open)}")
        if open_reviews:
            print("  latest:")
            for r in open_reviews[-3:]:
                task_hint = f" {r.task_id}" if r.task_id else ""
                print(f"    - {r.review_id} [{r.reason_code}]{task_hint} {r.title}")

    # MVP-4E: deployment summary
    if session is not None and isinstance(session.deployment, dict):
        d = session.deployment
        print()
        print("Deployment:")
        print(f"  enabled: {d.get('enabled', False)}")
        print(f"  target: {d.get('target', 'vercel')}")
        print(f"  status: {d.get('status', 'not-configured')}")
        if d.get("latest_deployment_url"):
            print(f"  url: {d['latest_deployment_url']}")
        if d.get("latest_failure_type"):
            print(f"  failure: {d['latest_failure_type']}")

        # MVP-4F: smoke + rollback summary
        print()
        print("Smoke checks:")
        smoke_status = d.get("latest_smoke_status") or "not-run"
        print(f"  status: {smoke_status}")
        if d.get("latest_smoke_check_id"):
            print(f"  latest: {d['latest_smoke_check_id']}")
        if d.get("latest_smoke_failure_type"):
            print(f"  failure: {d['latest_smoke_failure_type']}")
        print()
        print("Rollback:")
        rollback_status = d.get("latest_rollback_status") or "not-run"
        print(f"  status: {rollback_status}")
        if d.get("latest_rollback_id"):
            print(f"  latest: {d['latest_rollback_id']}")
        if d.get("latest_rollback_failure_type"):
            print(f"  failure: {d['latest_rollback_failure_type']}")


def cmd_autonomous_logs(args: argparse.Namespace) -> None:
    from orchestrator.core.autonomous import controller_log_file, find_active_session

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    session = find_active_session(project_path)
    if session is None:
        print("No autonomous session yet.")
        return
    log_path = controller_log_file(project_path, session.session_id)
    if not log_path.exists():
        print(f"No log entries yet at {log_path}.")
        return
    lines = log_path.read_text(encoding="utf-8").splitlines()
    tail_n = max(1, int(args.tail or 20))
    for raw in lines[-tail_n:]:
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            print(raw)
            continue
        ts = event.get("ts", "")
        ev = event.get("event", "")
        rest = {k: v for k, v in event.items() if k not in ("ts", "event")}
        rest_str = " ".join(f"{k}={v}" for k, v in rest.items() if not isinstance(v, (dict, list)))
        print(f"{ts}  {ev:24}  {rest_str}")


def cmd_autonomous_halt(args: argparse.Namespace) -> None:
    from orchestrator.core.autonomous import AutonomousController

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
    session = controller.request_halt()
    if session is None:
        print("No autonomous session to halt.")
    else:
        print(f"Halt requested for session {session.session_id}. Controller will pause after the current task.")


def cmd_autonomous_resume(args: argparse.Namespace) -> None:
    # Resume is identical to start: start_or_resume picks up an existing
    # paused session and clears halt_requested. This handler exists as a
    # discoverable command name; it just delegates.
    cmd_autonomous_start(args)


def _resolve_session_id(args: argparse.Namespace, project_path: Path) -> str:
    """Helper for reviews subcommands: pick session from --session or fall
    back to the most recent active session for the project."""
    from orchestrator.core.autonomous import find_active_session
    explicit = getattr(args, "session_id", None)
    if explicit:
        return explicit
    session = find_active_session(project_path)
    if session is None:
        raise SystemExit("error: no autonomous session found. Run `agent-studio autonomous start` first or pass --session.")
    return session.session_id


def cmd_autonomous_reviews_list(args: argparse.Namespace) -> None:
    from orchestrator.core.review_queue import list_review_items
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    session_id = _resolve_session_id(args, project_path)
    items = list_review_items(project_path, session_id, only_open=not args.all)

    if args.json:
        print(json.dumps({
            "project_id": project["id"],
            "session_id": session_id,
            "review_items": [r.to_dict() for r in items],
        }, ensure_ascii=False, indent=2))
        return

    print(f"Project: {project.get('name') or project['id']} ({project['id']})")
    print(f"Session: {session_id}")
    print(f"{'All' if args.all else 'Open'} review items: {len(items)}")
    if not items:
        print("(none)")
        return
    headers = ["review_id", "status", "reason_code", "severity", "task_id", "run_id", "candidate_id", "title", "created_at"]
    rows = []
    for r in items:
        rows.append({
            "review_id": (r.review_id or "")[:24],
            "status": r.status[:9],
            "reason_code": r.reason_code[:26],
            "severity": r.severity[:8],
            "task_id": (r.task_id or "")[:18],
            "run_id": (r.run_id or "")[:14],
            "candidate_id": (r.candidate_id or "")[:12],
            "title": (r.title or "")[:42],
            "created_at": (r.created_at or "")[:19],
        })
    widths = {h: max(len(h), max((len(row[h]) for row in rows), default=0)) for h in headers}
    print()
    print("  ".join(h.ljust(widths[h]) for h in headers))
    print("  ".join("-" * widths[h] for h in headers))
    for row in rows:
        print("  ".join(row[h].ljust(widths[h]) for h in headers))


def cmd_autonomous_reviews_show(args: argparse.Namespace) -> None:
    from orchestrator.core.review_queue import read_review_item
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    session_id = _resolve_session_id(args, project_path)
    item = read_review_item(project_path, session_id, args.review_id)
    if item is None:
        raise SystemExit(f"error: review item not found: {args.review_id}")

    if args.json:
        print(json.dumps(item.to_dict(), ensure_ascii=False, indent=2))
        return

    print(f"Project: {project.get('name') or project['id']} ({project['id']})")
    print(f"Session: {session_id}")
    print(f"Review id: {item.review_id}")
    print(f"Status: {item.status}")
    print(f"Severity: {item.severity}")
    print(f"Reason: {item.reason_code} (source_type={item.source_type})")
    print(f"Title: {item.title}")
    if item.task_id:
        print(f"Task: {item.task_id}")
    if item.run_id:
        print(f"Run: {item.run_id}")
    if item.candidate_id:
        print(f"Candidate: {item.candidate_id}")
    if item.promotion_decision:
        print(f"Promotion decision: {item.promotion_decision}")
    if item.source_failure_id:
        print(f"Source failure id: {item.source_failure_id}")
    print()
    print("Summary:")
    for line in (item.summary or "").splitlines() or [""]:
        print(f"  {line}")
    if item.evidence_paths:
        print()
        print("Evidence:")
        for p in item.evidence_paths:
            print(f"  - {p}")
    if item.suggested_commands:
        print()
        print("Suggested next commands:")
        for c in item.suggested_commands:
            print(f"  - {c}")
    print()
    print(f"Allowed actions: {', '.join(item.allowed_actions)}")
    print(f"Created: {item.created_at}")
    if item.updated_at and item.updated_at != item.created_at:
        print(f"Updated: {item.updated_at}")
    if item.resolution:
        print()
        print("Resolution:")
        for k, v in item.resolution.items():
            print(f"  {k}: {v}")


def cmd_autonomous_reviews_approve(args: argparse.Namespace) -> None:
    """Human override path. If the review is tied to a candidate patch, run
    the safe Apply Gate (with `human_override=True`) and commit on the
    session branch with full Human-Review-* trailers. Otherwise, just record
    approval (e.g. for needs-more-context where there's no patch to apply)."""
    from orchestrator.core.review_queue import read_review_item, update_review_item
    from orchestrator.core.run_package import apply_selected_candidate, ApplyGateRefused
    from orchestrator.core.autonomous import (
        commit_task as _commit_task,
        read_task_graph,
        write_task_graph,
        find_active_session,
    )
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    session_id = _resolve_session_id(args, project_path)
    item = read_review_item(project_path, session_id, args.review_id)
    if item is None:
        raise SystemExit(f"error: review item not found: {args.review_id}")
    if item.status != "open":
        raise SystemExit(f"error: review {args.review_id} is already {item.status}; refusing to approve again.")

    has_patch_context = bool(item.run_id and item.candidate_id and item.candidate_id != "selected")
    applied_record: dict[str, Any] | None = None
    commit_hash: str | None = None
    task_graph = read_task_graph(project_path)
    task = next((t for t in task_graph.get("tasks") or [] if t.get("id") == item.task_id), None) if item.task_id else None

    if has_patch_context:
        run_dir = project_path / ".agent" / "runs" / str(item.run_id)
        if not run_dir.is_dir():
            raise SystemExit(f"error: run dir missing: {run_dir}. Cannot run safe apply.")
        try:
            applied_record = apply_selected_candidate(
                project_path=project_path,
                run_dir=run_dir,
                selected_candidate=str(item.candidate_id),
                human_override=True,
            )
        except ApplyGateRefused as exc:
            print(f"Apply Gate REFUSED human-override apply for {item.review_id}:")
            print(str(exc))
            sys.exit(1)
        applied_record["project_id"] = project["id"]

        # Commit on the session branch with full human-review trailers.
        if task is not None:
            commit_hash = _commit_task(
                project_path,
                task=task,
                run_id=str(item.run_id),
                selected_candidate=str(item.candidate_id),
                candidate_strategy=str(applied_record.get("strategy") or ""),
                promotion_decision=str(item.promotion_decision or "needs-human-review"),
                promotion_report_relpath=str((Path(".agent") / "runs" / str(item.run_id) / "promotion-report.json").as_posix()),
                corrective=bool(task.get("corrective")),
                source_failure_id=task.get("source_failure_id"),
                human_review_id=item.review_id,
                human_review_decision="approved",
                human_review_override=True,
            )
            task["status"] = "completed"
            task["commit"] = commit_hash
            task["manual_resolution"] = False
            write_task_graph(project_path, task_graph)

    item.status = "approved"
    item.resolution = {
        "decision": "approved",
        "human_override": True,
        "applied": bool(applied_record),
        "commit": commit_hash,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": (
            "Safe Apply Gate executed with human override; patch applied."
            if applied_record else
            "No candidate patch was applied (review had no run/candidate context)."
        ),
    }
    update_review_item(project_path, item)

    # Log on session controller log for audit.
    from orchestrator.core.autonomous import controller_log_file as _clf
    log_path = _clf(project_path, session_id)
    if log_path.parent.exists():
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "review_item_approved",
                "review_id": item.review_id,
                "applied": bool(applied_record),
                "commit": commit_hash,
            }, ensure_ascii=False) + "\n")

    print(f"Approved review {item.review_id}.")
    if applied_record:
        print(f"  patch applied; commit {commit_hash}")
        print(f"  applied-candidate.json updated at .agent/runs/{item.run_id}/")
        print(f"  task {item.task_id} marked completed.")
        print()
        print("Resume with: agent-studio autonomous resume --project " + project["id"])
    else:
        print("  no candidate patch was applied (review had no run/candidate context).")


def cmd_autonomous_reviews_reject(args: argparse.Namespace) -> None:
    from orchestrator.core.review_queue import read_review_item, update_review_item
    from orchestrator.core.autonomous import read_task_graph, write_task_graph
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    session_id = _resolve_session_id(args, project_path)
    item = read_review_item(project_path, session_id, args.review_id)
    if item is None:
        raise SystemExit(f"error: review item not found: {args.review_id}")
    if item.status != "open":
        raise SystemExit(f"error: review {args.review_id} is already {item.status}; cannot reject.")

    item.status = "rejected"
    item.resolution = {
        "decision": "rejected",
        "reason": args.reason,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    update_review_item(project_path, item)

    if item.task_id:
        task_graph = read_task_graph(project_path)
        task = next((t for t in task_graph.get("tasks") or [] if t.get("id") == item.task_id), None)
        if task is not None:
            task["status"] = "blocked"
            task["block_reason"] = "human_rejected"
            write_task_graph(project_path, task_graph)

    from orchestrator.core.autonomous import controller_log_file as _clf
    log_path = _clf(project_path, session_id)
    if log_path.parent.exists():
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "review_item_rejected",
                "review_id": item.review_id,
                "task_id": item.task_id,
                "reason": args.reason,
            }, ensure_ascii=False) + "\n")

    print(f"Rejected review {item.review_id}.")
    print(f"  reason: {args.reason}")
    if item.task_id:
        print(f"  task {item.task_id} marked blocked (block_reason=human_rejected).")
    print()
    print("Resume with: agent-studio autonomous resume --project " + project["id"])


def cmd_autonomous_reviews_resolve(args: argparse.Namespace) -> None:
    from orchestrator.core.review_queue import read_review_item, update_review_item
    from orchestrator.core.autonomous import read_task_graph, write_task_graph
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    session_id = _resolve_session_id(args, project_path)
    item = read_review_item(project_path, session_id, args.review_id)
    if item is None:
        raise SystemExit(f"error: review item not found: {args.review_id}")
    if item.status != "open":
        raise SystemExit(f"error: review {args.review_id} is already {item.status}; cannot resolve.")

    item.status = "resolved"
    mark_task_action = getattr(args, "mark_task", None)
    item.resolution = {
        "decision": "resolved",
        "note": args.note,
        "mark_task": mark_task_action,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    update_review_item(project_path, item)

    if mark_task_action and item.task_id:
        task_graph = read_task_graph(project_path)
        task = next((t for t in task_graph.get("tasks") or [] if t.get("id") == item.task_id), None)
        if task is not None:
            if mark_task_action == "pending":
                task["status"] = "pending"
                task.pop("block_reason", None)
            elif mark_task_action == "completed":
                task["status"] = "completed"
                task["manual_resolution"] = True
            elif mark_task_action == "blocked":
                task["status"] = "blocked"
                task["block_reason"] = "human_resolved"
            write_task_graph(project_path, task_graph)

    from orchestrator.core.autonomous import controller_log_file as _clf
    log_path = _clf(project_path, session_id)
    if log_path.parent.exists():
        with log_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "event": "review_item_resolved",
                "review_id": item.review_id,
                "task_id": item.task_id,
                "note": args.note,
                "mark_task": mark_task_action,
            }, ensure_ascii=False) + "\n")

    print(f"Resolved review {item.review_id}.")
    print(f"  note: {args.note}")
    if mark_task_action:
        print(f"  task {item.task_id} marked: {mark_task_action}")
    print()
    print("Resume with: agent-studio autonomous resume --project " + project["id"])


def cmd_autonomous_smoke(args: argparse.Namespace) -> None:
    """MVP-4F manual smoke check. Resolves a deployment URL (from --url, or
    --deployment, or latest deployment), runs configured smoke checks,
    writes the artifact, and on failure emits a smoke-check review item.

    Does NOT trigger rollback unless --rollback-on-failure --yes are both
    given AND environment is production AND rollback.enabled in config.
    """
    from orchestrator.core.autonomous import (
        AutonomousController, find_active_session,
    )
    from orchestrator.core.deploy import (
        latest_deployment, list_deployments, load_deploy_config,
    )
    from orchestrator.core.smoke import persist_smoke_run, run_smoke_checks

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    session = find_active_session(project_path)
    if session is None:
        raise SystemExit("error: no autonomous session found. Run `agent-studio autonomous start` first.")

    config = load_deploy_config(project_path)
    # Manual smoke can run even when deploy.enabled=false; the user knows
    # what they're doing. Force smoke_checks.enabled=True at runtime so the
    # adapter doesn't short-circuit.
    config.smoke_checks.enabled = True

    # Resolve the URL.
    deployment_id: str | None = None
    deployment_url: str | None = args.url
    if not deployment_url:
        if args.deployment_id:
            for record in list_deployments(project_path, session.session_id):
                if record.get("deployment_id") == args.deployment_id:
                    deployment_id = record["deployment_id"]
                    deployment_url = record.get("deployment_url")
                    break
            if not deployment_url:
                raise SystemExit(f"error: deployment {args.deployment_id} not found in session {session.session_id}")
        else:
            latest = latest_deployment(project_path, session.session_id)
            if latest is not None:
                deployment_id = latest.get("deployment_id")
                deployment_url = latest.get("deployment_url")
    if not deployment_url:
        raise SystemExit("error: no deployment URL available. Pass --url or run `autonomous deploy --yes` first.")

    environment = "production" if config.vercel.prod else config.environment
    smoke_result = run_smoke_checks(config.smoke_checks, deployment_url)
    smoke_check_id, artifact_path = persist_smoke_run(
        project_path,
        session_id=session.session_id, project_id=project["id"],
        deployment_id=deployment_id, deployment_url=deployment_url,
        environment=environment, result=smoke_result,
    )

    review_id: str | None = None
    rollback_id: str | None = None
    rollback_status: str | None = None
    # RC-2C.1.1: state reconciliation. Pre-fix, ONLY the failure branch
    # persisted session.deployment.latest_smoke_*; success branch silently
    # returned, so a manual smoke that healed a previously-failed run
    # left the session showing the OLD failed smoke (real bug surfaced
    # in RC-2C against session_b82c6d6a3c). Post-fix: every manual smoke
    # outcome — passed OR failed — updates session state, then saves.
    controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
    session.deployment["latest_smoke_check_id"] = smoke_check_id
    session.deployment["latest_smoke_status"] = smoke_result.status
    if smoke_result.status == "passed":
        session.deployment["latest_smoke_failure_type"] = None
        # If the latest deployment is itself ready, the deployment-level
        # status advances from `deployed` → `verified` to reflect that
        # smoke confirmed the URL responds.
        if session.deployment.get("status") in {"deployed", "smoke-failed"}:
            session.deployment["status"] = "verified"
        controller._save_session(session)  # noqa: SLF001
    if smoke_result.status != "passed":
        # Manual smoke failure also creates a review item — same audit
        # trail as the controller-driven path.
        failure_type = (smoke_result.failure or {}).get("failure_type") or "unknown"
        session.deployment["latest_smoke_failure_type"] = failure_type
        if session.deployment.get("status") in {"verified", "deployed", "ready"}:
            session.deployment["status"] = "smoke-failed"
        controller._save_session(session)  # noqa: SLF001
        review = controller._emit_review_item(  # noqa: SLF001
            session,
            source_type="smoke_check_failure",
            reason_code="smoke-check-failed",
            title=f"Manual smoke check failed: {failure_type}",
            summary=f"Manual smoke check against {deployment_url} failed with `{failure_type}`.",
            evidence_paths=[str(artifact_path.relative_to(project_path).as_posix())],
            suggested_commands=[
                f"agent-studio autonomous reviews show <review_id> --project {project_id}",
                f"agent-studio autonomous rollback --project {project_id} --dry-run",
            ],
            allowed_actions=["show", "reject", "resolve"],
        )
        review_id = review.review_id

        # Optional rollback. Both flags required for safety; production_only
        # check still applies via config.rollback.production_only.
        if args.rollback_on_failure and args.yes:
            if config.rollback.production_only and environment != "production":
                rollback_status = "skipped"
            elif config.rollback.enabled:
                outcome = controller._maybe_run_rollback_after_smoke_failure(  # noqa: SLF001
                    session,
                    deployment_id=deployment_id or "",
                    smoke_check_id=smoke_check_id,
                    config=config,
                    deployment_url=deployment_url,
                    environment=environment,
                )
                rollback_id = outcome.get("rollback_id")
                rollback_status = outcome.get("status")

    if args.json:
        print(json.dumps({
            "project_id": project_id,
            "session_id": session.session_id,
            "smoke_check_id": smoke_check_id,
            "deployment_url": deployment_url,
            "status": smoke_result.status,
            "failure": smoke_result.failure,
            "artifact_path": str(artifact_path),
            "review_id": review_id,
            "rollback_id": rollback_id,
            "rollback_status": rollback_status,
        }, ensure_ascii=False, indent=2))
        if smoke_result.status != "passed":
            sys.exit(1)
        return

    print(f"Project: {project.get('name') or project_id} ({project_id})")
    print(f"Session: {session.session_id}")
    print(f"Smoke check: {smoke_result.status}")
    print(f"  url: {deployment_url}")
    print(f"  artifact: {artifact_path}")
    if smoke_result.status != "passed":
        failure = smoke_result.failure or {}
        print(f"  failure: {failure.get('failure_type')} on `{failure.get('failed_check')}`")
        print(f"  message: {failure.get('message')}")
        if review_id:
            print(f"  review: {review_id}")
        if rollback_id:
            print(f"  rollback: {rollback_id} ({rollback_status})")
        sys.exit(1)


def cmd_autonomous_rollback(args: argparse.Namespace) -> None:
    """MVP-4F manual rollback. Requires --dry-run or --yes; production-only
    by default. Writes rollback.json on --yes."""
    from orchestrator.core.autonomous import AutonomousController, find_active_session
    from orchestrator.core.deploy import (
        latest_deployment, load_deploy_config, new_rollback_id, write_rollback_artifact,
    )
    from orchestrator.core.deploy_vercel import (
        build_vercel_rollback_command, run_vercel_rollback, serialize_command_results,
    )

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    session = find_active_session(project_path)
    if session is None:
        raise SystemExit("error: no autonomous session found.")

    config = load_deploy_config(project_path)
    config.rollback.enabled = True  # manual rollback always honors --yes
    project_root = (project_path / config.project_path).resolve()
    deployment_url = args.deployment_url
    if not deployment_url:
        latest = latest_deployment(project_path, session.session_id)
        if latest is not None:
            deployment_url = latest.get("deployment_url")

    if args.dry_run:
        _, sanitized = build_vercel_rollback_command(config, project_root, deployment_url=deployment_url, sanitized=True)
        if args.json:
            print(json.dumps({
                "project_id": project_id,
                "dry_run": True,
                "deployment_url": deployment_url,
                "command": sanitized,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"Project: {project.get('name') or project_id} ({project_id})")
            print(f"Deployment URL: {deployment_url or '(latest)'}")
            print()
            print("Sanitized rollback command (would run):")
            print(f"  {' '.join(sanitized)}")
            print()
            print("Re-run with --yes to actually execute.")
        return

    # --yes path
    environment = "production" if config.vercel.prod else config.environment
    rollback_result = run_vercel_rollback(config, project_root, deployment_url=deployment_url)
    rollback_id = new_rollback_id()
    sanitized_commands = serialize_command_results(rollback_result.commands_run)
    artifact_path = write_rollback_artifact(
        project_path,
        session_id=session.session_id, project_id=project_id,
        rollback_id=rollback_id,
        deployment_id=session.deployment.get("latest_deployment_id"),
        smoke_check_id=session.deployment.get("latest_smoke_check_id"),
        target=config.target, environment=environment,
        status=rollback_result.status,
        started_at=rollback_result.started_at, completed_at=rollback_result.completed_at,
        trigger="manual",
        sanitized_commands=sanitized_commands,
        failure=rollback_result.failure,
    )
    session.deployment["latest_rollback_id"] = rollback_id
    session.deployment["latest_rollback_status"] = rollback_result.status
    if rollback_result.failure:
        session.deployment["latest_rollback_failure_type"] = rollback_result.failure.get("failure_type")
    else:
        session.deployment["latest_rollback_failure_type"] = None
    # RC-2C.1.1: persist the rollback outcome back into session state
    # so subsequent `status` / final-run-status / validate-artifacts
    # see the latest rollback. Pre-fix this only mutated the dict
    # in-process and the writes were discarded at exit.
    controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
    controller._save_session(session)  # noqa: SLF001

    if args.json:
        print(json.dumps({
            "project_id": project_id,
            "rollback_id": rollback_id,
            "status": rollback_result.status,
            "failure": rollback_result.failure,
            "artifact_path": str(artifact_path),
        }, ensure_ascii=False, indent=2))
        if rollback_result.status != "completed":
            sys.exit(1)
        return

    print(f"Project: {project.get('name') or project_id} ({project_id})")
    print(f"Rollback: {rollback_result.status}")
    print(f"  artifact: {artifact_path}")
    if rollback_result.failure:
        print(f"  failure: {rollback_result.failure.get('failure_type')}")
        print(f"  message: {rollback_result.failure.get('message')}")
        sys.exit(1)


def cmd_autonomous_deploy(args: argparse.Namespace) -> None:
    """MVP-4E manual deploy entrypoint."""
    from orchestrator.core.autonomous import (
        AutonomousController, find_active_session,
    )
    from orchestrator.core.deploy import (
        DeployConfig, load_deploy_config, list_deployments,
    )
    from orchestrator.core.deploy_vercel import (
        build_vercel_build_command, build_vercel_deploy_command,
    )
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])

    # Load config and apply CLI overrides.
    config = load_deploy_config(project_path)
    if args.prod:
        config.vercel.prod = True
        config.environment = "production"
    elif args.preview:
        config.vercel.prod = False
        config.environment = "preview"
    if args.prebuilt:
        config.vercel.build_before_deploy = True
        config.vercel.prebuilt = True
    # Manual deploy can run even when config.enabled is false — but only via
    # explicit --yes / --dry-run. Mirror config.enabled = True so the
    # adapter doesn't short-circuit out.
    config.enabled = True

    project_root = (project_path / config.project_path).resolve()

    if args.dry_run:
        _, deploy_sanitized = build_vercel_deploy_command(config, project_root, sanitized=True)
        commands_preview: list[dict[str, Any]] = []
        if config.vercel.build_before_deploy or config.vercel.prebuilt:
            _, build_sanitized = build_vercel_build_command(config, project_root, sanitized=True)
            commands_preview.append({"name": "vercel_build", "args": build_sanitized})
        commands_preview.append({"name": "vercel_deploy", "args": deploy_sanitized})
        if args.json:
            print(json.dumps({
                "project_id": project_id,
                "dry_run": True,
                "config": config.to_dict(),
                "commands": commands_preview,
            }, ensure_ascii=False, indent=2))
        else:
            print(f"Project: {project.get('name') or project_id} ({project_id})")
            print(f"Target: {config.target} (environment={config.environment}, prod={config.vercel.prod}, prebuilt={config.vercel.prebuilt})")
            print()
            print("Sanitized commands (would run):")
            for cmd in commands_preview:
                print(f"  {cmd['name']}: {' '.join(cmd['args'])}")
            print()
            print("Re-run with --yes to actually execute.")
        return

    # --yes path: real deploy.
    session = find_active_session(project_path)
    if session is None:
        raise SystemExit(
            "error: no autonomous session found. Run `agent-studio autonomous start` first or pass --session."
        )
    controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
    outcome = controller.run_deploy_now(session, source="manual", config=config)

    if args.json:
        print(json.dumps({
            "project_id": project_id,
            "session_id": session.session_id,
            "outcome": outcome,
        }, ensure_ascii=False, indent=2))
        if outcome["status"] not in {"ready", "skipped"}:
            sys.exit(1)
        return

    print(f"Project: {project.get('name') or project_id} ({project_id})")
    print(f"Session: {session.session_id}")
    print(f"Deployment: {outcome['status']}")
    if outcome.get("deployment_url"):
        print(f"  URL: {outcome['deployment_url']}")
    if outcome.get("deployment_artifact_path"):
        print(f"  Artifact: {outcome['deployment_artifact_path']}")
    if outcome.get("failure"):
        print(f"  Failure: {outcome['failure'].get('failure_type')} — {outcome['failure'].get('message')}")
        if outcome.get("review_id"):
            print(f"  Review: {outcome['review_id']}")
            print(f"  Inspect with: agent-studio autonomous reviews show {outcome['review_id']} --project {project_id}")
        sys.exit(1)


def cmd_autonomous_integrate(args: argparse.Namespace) -> None:
    """MVP-4B: manually trigger an integration check against the project
    working tree. Reads the same eval-harness commands the inner loop uses,
    executes them, records the result, and prints a summary. Does NOT
    advance tasks or change session state (other than counters/last result)."""
    from orchestrator.core.autonomous import (
        AutonomousController,
        build_integration_commands,
        find_active_session,
        run_integration_check,
    )

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    session = find_active_session(project_path)
    if session is None:
        raise SystemExit(
            "error: no autonomous session yet. Run `agent-studio autonomous start` first."
        )

    # Reuse the controller's _run_integration so the same logging /
    # counter-update / corrective-injection semantics apply for ad-hoc checks.
    controller = AutonomousController(project=project, run_inner_loop=lambda **kw: None)
    paused = controller._run_integration(session, reason="manual")  # noqa: SLF001

    last = session.last_integration_result or {}
    integration_passed = bool(last.get("passed"))

    if args.json:
        print(json.dumps({
            "project_id": project["id"],
            "session_id": session.session_id,
            "last_integration_result": last,
            "session_status": session.status,
            "session_pause_reason": session.pause_reason,
            "session_paused_by_this_run": paused,
        }, ensure_ascii=False, indent=2))
        if not integration_passed:
            sys.exit(1)
        return

    print(f"Project: {project.get('name') or project['id']} ({project['id']})")
    print(f"Session: {session.session_id}")
    state = "passed" if integration_passed else "FAILED"
    print(f"Integration: {state}")
    failed_names = ", ".join(last.get("failed_required_command_names") or []) or "none"
    print(f"  failed required commands: {failed_names}")
    print(f"  duration: {last.get('duration_sec')}s")
    if not integration_passed:
        # MVP-4C: integration failure may have triggered corrective injection
        # (so the session keeps running) or may have paused the session
        # (budget exhausted / explicitly disabled). Surface both cases.
        if paused:
            print(f"  session is now paused (reason: {session.pause_reason}).")
            print(f"  see {project_path / '.agent/autonomous/sessions' / session.session_id / 'integration-failure-summary.md'}")
        else:
            print(f"  a corrective task was injected; session status: {session.status}.")
            print(f"  run `agent-studio autonomous resume --project {project['id']}` to advance the corrective task.")
        sys.exit(1)


def cmd_autonomous_validate_artifacts(args: argparse.Namespace) -> None:
    """RC-1: validate every persisted artifact in a session against the
    schema + redaction rules in `orchestrator/core/artifact_validation.py`.

    Walks the session dir + project root and runs the bundle of pure
    validators. Prints either a human-readable per-artifact summary or
    raw JSON. Exits 1 if any error was reported."""
    from orchestrator.core.artifact_validation import (
        has_validation_errors, validate_session_directory,
    )
    from orchestrator.core.autonomous import find_active_session, session_dir as _session_dir

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])

    if args.session_id:
        sess_dir = _session_dir(project_path, args.session_id)
        if not sess_dir.is_dir():
            raise SystemExit(f"error: session {args.session_id} not found at {sess_dir}")
        session_id = args.session_id
    else:
        session = find_active_session(project_path)
        if session is None:
            raise SystemExit("error: no autonomous session found. Run `agent-studio autonomous start` first or pass --session.")
        sess_dir = _session_dir(project_path, session.session_id)
        session_id = session.session_id

    report = validate_session_directory(sess_dir)
    failed = has_validation_errors(report)

    if args.json:
        print(json.dumps({
            "project_id": project["id"],
            "session_id": session_id,
            "session_dir": str(sess_dir),
            "ok": not failed,
            "report": report,
        }, ensure_ascii=False, indent=2))
        if failed:
            sys.exit(1)
        return

    print(f"Project: {project.get('name') or project['id']} ({project['id']})")
    print(f"Session: {session_id}")
    print(f"Session dir: {sess_dir}")
    print(f"Status: {'OK' if not failed else 'ERRORS'}")
    print()
    for artifact_key in sorted(report.keys()):
        errors = report[artifact_key]
        if errors:
            print(f"  {artifact_key}: {len(errors)} error(s)")
            for err in errors:
                print(f"    - {err}")
        else:
            print(f"  {artifact_key}: ok")
    if failed:
        sys.exit(1)


def cmd_autonomous_preflight(args: argparse.Namespace) -> None:
    """RC-2B.11: pre-flight check before `autonomous start`.

    Each check is one of {pass, fail, skip}. Operator runs this once
    after editing agent-studio.yaml or right before kicking off a long
    autonomous run; if everything is green, `start` is unlikely to bail
    on a configuration mistake. Exit 0 only when all checks pass."""
    from orchestrator.core.agentic_runtime import codex_cli_available
    from orchestrator.core.autonomous import (
        is_git_repo, is_worktree_clean, read_task_graph,
    )
    from orchestrator.core.deploy import load_agentic_config, load_deploy_config

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])

    checks: list[dict[str, Any]] = []

    # 1. git repo present
    git_ok = is_git_repo(project_path)
    checks.append({
        "name": "git_repo_present",
        "status": "pass" if git_ok else "fail",
        "detail": str(project_path / ".git") if git_ok else f"no .git/ at {project_path}",
    })

    # 2. worktree clean (controller-owned files tolerated)
    if git_ok:
        clean, reason = is_worktree_clean(project_path)
        checks.append({
            "name": "worktree_clean",
            "status": "pass" if clean else "fail",
            "detail": "clean" if clean else reason,
        })
    else:
        checks.append({
            "name": "worktree_clean",
            "status": "skip",
            "detail": "skipped because git repo is missing",
        })

    # 3. task-graph.json present + has tasks
    try:
        graph = read_task_graph(project_path)
        tasks = list(graph.get("tasks") or [])
        if tasks:
            checks.append({
                "name": "task_graph_has_tasks",
                "status": "pass",
                "detail": f"{len(tasks)} task(s) in graph",
            })
        else:
            checks.append({
                "name": "task_graph_has_tasks",
                "status": "fail",
                "detail": "task-graph.json present but has 0 tasks; run `agent-studio new --from <requirements.md>` first",
            })
    except Exception as exc:  # noqa: BLE001
        checks.append({
            "name": "task_graph_has_tasks",
            "status": "fail",
            "detail": f"task-graph read failed: {exc}",
        })

    # 4. agentic.patch_worker config + (if codex) preflight CLI on PATH
    try:
        agentic = load_agentic_config(project_path)
        checks.append({
            "name": "agentic_config_loaded",
            "status": "pass",
            "detail": f"patch_worker={agentic.patch_worker}",
        })
        if agentic.patch_worker == "codex":
            cli_ok = codex_cli_available(command=agentic.codex.command)
            checks.append({
                "name": "codex_cli_available",
                "status": "pass" if cli_ok else "fail",
                "detail": (
                    f"`{agentic.codex.command}` is on PATH"
                    if cli_ok else
                    f"`{agentic.codex.command}` NOT on PATH; install via `npm i -g @openai/codex`"
                ),
            })
    except ValueError as exc:
        # load_agentic_config raises on unsupported patch_worker values.
        checks.append({
            "name": "agentic_config_loaded",
            "status": "fail",
            "detail": str(exc),
        })
        checks.append({
            "name": "codex_cli_available",
            "status": "skip",
            "detail": "skipped because agentic config did not load",
        })

    # 5. deploy config — loads even when disabled (we just want to prove
    # it's parseable). Actual deploy-enabled vs disabled is informational,
    # not pass/fail at preflight time.
    try:
        deploy = load_deploy_config(project_path)
        checks.append({
            "name": "deploy_config_loaded",
            "status": "pass",
            "detail": f"enabled={deploy.enabled}, target={deploy.target}",
        })
    except Exception as exc:  # noqa: BLE001
        checks.append({
            "name": "deploy_config_loaded",
            "status": "fail",
            "detail": f"deploy config parse failed: {exc}",
        })

    failures = [c for c in checks if c["status"] == "fail"]
    overall = "pass" if not failures else "fail"

    if args.json:
        print(json.dumps({
            "project_id": project["id"],
            "overall": overall,
            "checks": checks,
        }, ensure_ascii=False, indent=2))
        if failures:
            sys.exit(1)
        return

    print(f"Project: {project.get('name') or project_id} ({project_id})")
    print(f"Overall: {overall.upper()}")
    print()
    for c in checks:
        marker = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP"}[c["status"]]
        print(f"  [{marker}] {c['name']}: {c['detail']}")
    if failures:
        print()
        print(f"{len(failures)} check(s) failed; refusing to declare ready.")
        sys.exit(1)


def _resolve_run_package(args: argparse.Namespace) -> tuple[dict[str, Any], RunPackage]:
    """Shared lookup: project + run package (latest by default).

    Returns (project_row, RunPackage). Raises ValueError on missing run.
    """
    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])
    walker = ProjectRunPackages(project_path=project_path)
    run_id = getattr(args, "run_id", None)
    if run_id:
        run = walker.run(run_id)
        if run is None:
            raise ValueError(f"Run not found in project {project_id}: {run_id}")
    else:
        run = walker.latest_run()
        if run is None:
            raise ValueError(f"No agentic_project runs found for project {project_id}.")
    return project, run


def cmd_agentic_candidates_list(args: argparse.Namespace) -> None:
    """List all candidates for one run with score + selection marker."""
    project, run = _resolve_run_package(args)
    promotion = run.promotion_report()
    summaries = list(iter_candidate_summaries(promotion))

    if args.json:
        payload = {
            "project_id": project["id"],
            "run_id": run.run_id,
            "decision": promotion.get("decision"),
            "selected_candidate": promotion.get("selected_candidate"),
            "candidate_count": promotion.get("candidate_count", len(summaries)),
            "candidate_diversity": promotion.get("candidate_diversity"),
            "candidates": summaries,
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"Project: {project.get('name') or project['id']} ({project['id']})")
    print(f"Run: {run.run_id}")
    print(f"Decision: {promotion.get('decision') or 'unknown'}")
    print(f"Selected candidate: {promotion.get('selected_candidate') or 'none'}")
    if not summaries:
        print("No candidate summaries in promotion-report.json (run may pre-date MVP-3A or be incomplete).")
        return
    rows: list[dict[str, str]] = []
    for summary in summaries:
        decision = "selected" if summary.get("selected") else (
            "disqualified" if summary.get("disqualified") else "eligible"
        )
        eval_state = "pass" if summary.get("required_eval_passed") else (
            "fail" if summary.get("required_eval_executed") else "skipped"
        )
        rows.append({
            "candidate": str(summary.get("id") or "")[:12],
            "strategy": str(summary.get("strategy") or "")[:14],
            "decision": decision[:12],
            "score": str(summary.get("score") or 0)[:5],
            "eval": eval_state[:7],
            "repair": str(summary.get("repair_attempts") or 0)[:6],
            "selected": "yes" if summary.get("selected") else "no",
        })
    headers = ["candidate", "strategy", "decision", "score", "eval", "repair", "selected"]
    widths = {h: max(len(h), max((len(row[h]) for row in rows), default=0)) for h in headers}
    print()
    print("  ".join(h.ljust(widths[h]) for h in headers))
    print("  ".join("-" * widths[h] for h in headers))
    for row in rows:
        print("  ".join(row[h].ljust(widths[h]) for h in headers))
    diversity = promotion.get("candidate_diversity") or {}
    if diversity:
        print()
        print(f"Candidate diversity: average {diversity.get('average')} ({diversity.get('method')})")


def cmd_agentic_candidates_show(args: argparse.Namespace) -> None:
    """Print full evidence for one candidate."""
    project, run = _resolve_run_package(args)
    promotion = run.promotion_report()
    candidate_arg = str(args.candidate)
    candidate = run.resolve_candidate(candidate_arg)
    if candidate is None:
        if candidate_arg == "selected":
            raise ValueError(
                f"This run has no selected_candidate (decision={promotion.get('decision')}). "
                "Pass an explicit --candidate <id> instead."
            )
        raise ValueError(f"Candidate not found in run {run.run_id}: {candidate_arg}")

    score = candidate.score()
    repair = candidate.repair_history()
    eval_results = candidate.eval_results()
    changed_files = candidate.changed_files()
    summaries = list(iter_candidate_summaries(promotion))
    summary = next((s for s in summaries if s.get("id") == candidate.candidate_id), {})

    if args.json:
        payload = {
            "project_id": project["id"],
            "run_id": run.run_id,
            "candidate": candidate.candidate_id,
            "strategy": candidate.strategy,
            "selected": (promotion.get("selected_candidate") == candidate.candidate_id),
            "promotion_decision": promotion.get("decision"),
            "summary": summary,
            "score": score,
            "changed_files": changed_files,
            "repair_history": repair,
            "eval_results": eval_results,
            "patch_diff_path": str(candidate.patch_diff_path),
            "critics": candidate.critic_summary(),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return

    print(f"Project: {project.get('name') or project['id']} ({project['id']})")
    print(f"Run: {run.run_id}")
    print(f"Candidate: {candidate.candidate_id} (strategy: {candidate.strategy or 'unknown'})")
    print(f"Selected: {'yes' if promotion.get('selected_candidate') == candidate.candidate_id else 'no'}")
    print(f"Promotion decision: {promotion.get('decision') or 'unknown'}")
    print()
    print("== Score ==")
    print(f"Total: {summary.get('score', 'unknown')}")
    print(f"Disqualified: {summary.get('disqualified')}")
    components = (summary.get("score") and {}) or {}
    # Components/penalties live on the per-candidate score.json under MVP-3A
    # via _score_candidate, but the reduced summary in promotion-report only
    # carries totals. Surface explicitly from score.json when present.
    raw_score = score
    if isinstance(raw_score, dict) and raw_score.get("components"):
        for k, v in raw_score["components"].items():
            print(f"  + {k}: {v}")
        for k, v in (raw_score.get("penalties") or {}).items():
            if v:
                print(f"  - {k}: {v}")
    print()
    print("== Hard gates ==")
    for gate in (
        "source_patch_present", "required_eval_executed", "required_eval_passed",
        "diff_within_scope", "no_critical_security_finding",
    ):
        print(f"  {gate}: {summary.get(gate)}")
    print()
    print(f"== Patch ==")
    print(f"Path: {candidate.patch_diff_path}")
    changed_list = changed_files.get("changed_files") or []
    print(f"Files changed: {len(changed_list)}")
    for entry in changed_list[:20]:
        if isinstance(entry, dict):
            print(f"  {entry.get('change_type', '?'):8} {entry.get('path')}")
    out_of_scope = changed_files.get("out_of_scope_changes") or []
    if out_of_scope:
        print(f"OUT OF SCOPE ({len(out_of_scope)}):")
        for entry in out_of_scope[:5]:
            if isinstance(entry, dict):
                print(f"  ! {entry.get('path')}")
    print()
    print("== Eval ==")
    for command in eval_results.get("commands", []):
        if not command.get("required"):
            continue
        state = "passed" if command.get("passed") else ("failed" if command.get("executed") else "skipped")
        print(f"  {command.get('name')}: {state} (exit_code={command.get('exit_code')})")
    print()
    print("== Repair ==")
    print(f"Attempts: {len(repair.get('attempts') or [])}/{repair.get('max_loops', 0)}")
    print(f"Stop reason: {repair.get('stop_reason')}")
    final = repair.get("final_failure")
    if isinstance(final, dict):
        print(f"Final failure: {final.get('failure_type')} (subtype={final.get('subtype')})")
    else:
        print("Final failure: none")
    print()
    print("== Critics (warnings only) ==")
    critics = candidate.critic_summary()
    for name, body in critics.items():
        warnings = [line for line in body.splitlines() if line.startswith("- ") and ("warning" in line.lower() or "out-of-scope" in line.lower() or "must" in line.lower() or "critical" in line.lower())]
        if warnings:
            print(f"  [{name}]")
            for w in warnings[:5]:
                print(f"    {w}")
    print()


def cmd_agentic_candidates_apply(args: argparse.Namespace) -> None:
    """Apply Gate: 10 hard rules. dry-run reports outcome; --yes applies the patch."""
    project, run = _resolve_run_package(args)
    promotion = run.promotion_report()
    candidate_arg = str(args.candidate)
    candidate = run.resolve_candidate(candidate_arg)
    if candidate is None:
        if candidate_arg == "selected":
            raise ValueError(
                f"This run has no selected_candidate (decision={promotion.get('decision')}). "
                "Refusing apply."
            )
        raise ValueError(f"Candidate not found in run {run.run_id}: {candidate_arg}")

    project_path = Path(project["path"])
    failures: list[str] = []

    # Gate 1: promotion-report schema must be v2.
    if str(promotion.get("schema_version") or "") != "agentic.promotion_report.v2":
        failures.append(f"promotion-report.json schema_version is `{promotion.get('schema_version')}`; require `agentic.promotion_report.v2`.")

    # Gate 2: selected_candidate must exist on the report.
    selected_id = promotion.get("selected_candidate")
    if not selected_id:
        failures.append("promotion-report.selected_candidate is null; no winner to apply.")

    # Gate 3: candidate patch.diff must exist and be non-empty.
    patch_path = candidate.patch_diff_path
    patch_text = candidate.patch_diff()
    if not patch_path.exists():
        failures.append(f"candidate patch.diff missing: {patch_path}")
    elif not patch_text.strip():
        failures.append(f"candidate patch.diff is empty: {patch_path}")

    # Gate 4: changed-files.json must exist.
    changed_files = candidate.changed_files()
    if not changed_files:
        failures.append("candidate changed-files.json missing or unreadable.")

    # Gate 5: source_patch_present must be true (on the candidate's score).
    score = candidate.score()
    if not bool(score.get("source_patch_present")):
        failures.append("candidate.score.source_patch_present is false; nothing to apply.")

    # Gate 6: candidate must not have out_of_scope_changes.
    out_of_scope = changed_files.get("out_of_scope_changes") or []
    if out_of_scope:
        paths = ", ".join(str(item.get("path") if isinstance(item, dict) else item) for item in out_of_scope[:5])
        failures.append(f"candidate has out_of_scope_changes ({len(out_of_scope)}): {paths}")

    # Gate 7: current repo HEAD must equal candidate base_commit.
    base_commit = str(changed_files.get("base_commit") or "")
    if not base_commit or base_commit == "unknown":
        failures.append("candidate changed-files.json has no recorded base_commit; cannot verify repo state.")
    else:
        head_short = _git_short_head(project_path)
        if head_short is None:
            failures.append("project_path is not a git repository (no HEAD); cannot verify base_commit.")
        elif head_short != base_commit:
            failures.append(f"current HEAD `{head_short}` does not match candidate base_commit `{base_commit}`.")

    # Gate 8: working tree must be clean.
    dirty = _git_is_dirty(project_path)
    if dirty is None:
        # Already covered by Gate 7's git check; don't double-report.
        pass
    elif dirty:
        failures.append("working tree is not clean (uncommitted changes present); refuse to apply.")

    # Gate 9: git apply --check must pass.
    if patch_path.exists() and patch_text.strip():
        check_status, check_output = _git_apply_check(project_path, patch_path)
        if check_status != 0:
            failures.append(f"`git apply --check` failed:\n{check_output.strip()}")

    # Gate 10: decision must be promote.
    decision = str(promotion.get("decision") or "")
    if decision != "promote":
        failures.append(f"promotion-report.decision is `{decision}`; refuse to apply (require `promote`).")

    # Gate 11 (MVP-3C re-apply guard): refuse `--yes` when this run has
    # already been applied. Dry-run is still allowed because it is pure
    # inspection. A re-apply attempt is almost always a foot-gun: applied
    # diffs are now in the working tree, so applying them again would
    # either fail (already-applied chunks) or silently double-apply edits
    # that look chunk-disjoint. The user-facing remediation is to create a
    # fresh agentic_project run.
    applied_record = run.applied_candidate()
    if applied_record and not args.dry_run:
        applied_at = applied_record.get("timestamp_utc") or "unknown"
        applied_cid = applied_record.get("candidate") or "unknown"
        failures.append(
            f"this run has already been applied ({applied_cid} at {applied_at}); "
            "create a new agentic_project run before re-applying."
        )

    print(f"Project: {project.get('name') or project['id']} ({project['id']})")
    print(f"Run: {run.run_id}")
    print(f"Candidate: {candidate.candidate_id} (strategy: {candidate.strategy or 'unknown'})")
    print(f"Mode: {'dry-run' if args.dry_run else 'apply'}")

    if failures:
        print()
        print(f"Apply Gate REJECTED ({len(failures)} failure(s)):")
        for index, msg in enumerate(failures, 1):
            print(f"  {index}. {msg}")
        sys.exit(1)

    print()
    print("Apply Gate passed all 10 checks.")
    if args.dry_run:
        print("Dry-run only — no changes written to working tree.")
        return

    # --yes: actually apply.
    apply_status, apply_output = _git_apply(project_path, patch_path)
    if apply_status != 0:
        print(f"git apply FAILED:\n{apply_output.strip()}")
        sys.exit(1)
    applied_to_commit = _git_short_head(project_path) or base_commit
    record = {
        "schema_version": 1,
        "run_id": run.run_id,
        "candidate": candidate.candidate_id,
        "strategy": candidate.strategy,
        "decision_at_apply_time": promotion.get("decision"),
        "project_id": project["id"],
        "base_commit": base_commit,
        "applied_to_commit": applied_to_commit,
        "patch_sha256": hashlib.sha256(patch_text.encode("utf-8")).hexdigest(),
        "dry_run": False,
        "applied": True,
        "changed_files": [
            str(entry.get("path"))
            for entry in (changed_files.get("changed_files") or [])
            if isinstance(entry, dict) and entry.get("path")
        ],
        "timestamp_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    record_path = run.run_dir / "applied-candidate.json"
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Patch applied. Recorded to {record_path}")
    print(f"Worktree now contains {len(record['changed_files'])} change(s). Review with `git diff` / `git status`.")


def _git_short_head(project_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=project_path, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def _git_is_dirty(project_path: Path) -> bool | None:
    """Return True if the working tree has uncommitted changes outside `.agent/`.

    `.agent/` is the runtime's evidence/bookkeeping directory — it changes on
    every agentic run and is NOT user code. Users typically `.gitignore` it,
    but even when they don't, an unclean `.agent/` should not block apply.
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=project_path, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        # Porcelain v1 format: "XY path"; for renames, "XY old -> new".
        # We need the destination path. Strip status flags (first 3 chars).
        if not line:
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        path = path.strip().strip('"')
        if path.startswith(".agent/") or path == ".agent":
            continue
        return True
    return False


def _git_apply_check(project_path: Path, patch_path: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "apply", "--check", str(patch_path)],
            cwd=project_path, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return 127, "git not found"
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _git_apply(project_path: Path, patch_path: Path) -> tuple[int, str]:
    try:
        result = subprocess.run(
            ["git", "apply", str(patch_path)],
            cwd=project_path, capture_output=True, text=True, check=False,
        )
    except FileNotFoundError:
        return 127, "git not found"
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def cmd_teams_plan(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = DownstreamTeamsAgent(db).run(project=project, run_id=run["id"] if run else None)
    print("Generated downstream team plans.")
    print(f"UI team: {result.ui_team_plan_path}")
    print(f"Developer team: {result.developer_team_plan_path}")
    print(f"QA team: {result.qa_team_plan_path}")
    print(f"Review team: {result.review_team_plan_path}")
    print(f"Contracts JSON: {result.contracts_json_path}")
    print(f"Remediation tasks: {result.remediation_tasks_path}")


def cmd_teams_review(args: argparse.Namespace) -> None:
    paths = _initialized_paths(args.root)
    db = Database(paths.db_path)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    run = engine.latest_run(project_id)
    result = TeamSystemReviewAgent(db).run(project=project, run_id=run["id"] if run else None)
    print("Generated team system review.")
    print(f"Overall review: {result.overall_review_path}")
    print(f"Maturity JSON: {result.maturity_json_path}")
    print(f"Optimization tasks: {result.optimization_tasks_path}")
    print(f"Architecture team contract: {result.architecture_team_contract_path}")
    print(f"QA team contract: {result.qa_team_contract_path}")
    print(f"Review team contract: {result.review_team_contract_path}")
    print(f"Lead team contract: {result.lead_team_contract_path}")


# ---------------------------------------------------------------------------
# RC-4A.1: change-mode CLI handlers.
# Foundation only — `change run` is intentionally a stub that points to RC-4A.2
# so operators see the same shape they'll use later.
# ---------------------------------------------------------------------------
def cmd_change_new(args: argparse.Namespace) -> None:
    from orchestrator.core.change_contract import create_change
    from orchestrator.core.change_request_parser import ChangeRequestParseError

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])

    try:
        created = create_change(project_path, Path(args.from_path))
    except ChangeRequestParseError as exc:
        raise SystemExit(f"error: {exc}")
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}")

    if args.json:
        print(json.dumps({
            "change_id": created.change_id,
            "change_dir": str(created.change_dir),
            "artifacts": {
                "change_request_md": str(created.change_request_path),
                "change_contract_json": str(created.change_contract_path),
                "repo_onboarding_md": str(created.repo_onboarding_path),
                "implementation_plan_md": str(created.implementation_plan_path),
                "acceptance_criteria_json": str(created.acceptance_criteria_path),
            },
        }, indent=2))
        return

    print(f"Created change: {created.change_id}")
    print(f"Project: {project['name']} ({project_id})")
    print(f"Change dir: {created.change_dir}")
    print()
    print("Artifacts written:")
    print(f"  - {created.change_request_path}")
    print(f"  - {created.change_contract_path}")
    print(f"  - {created.repo_onboarding_path}")
    print(f"  - {created.implementation_plan_path}")
    print(f"  - {created.acceptance_criteria_path}")
    print()
    print("Next:")
    print(f"  agent-studio change show {created.change_id} --project {project_id}")
    print(f"  agent-studio change validate {created.change_id} --project {project_id}")
    print(f"  agent-studio change run {created.change_id} --project {project_id}   # RC-4A.2+")


def cmd_change_list(args: argparse.Namespace) -> None:
    from orchestrator.core.change_contract import list_changes

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])

    rows = list_changes(project_path)
    if args.json:
        print(json.dumps(rows, indent=2))
        return

    print(f"Project: {project['name']} ({project_id})")
    print(f"Change sessions: {len(rows)}")
    if not rows:
        print("(none yet — run `agent-studio change new --from <change-request.md>`)")
        return
    print()
    print(f"{'change_id':22} {'created_at':25} goal")
    print(f"{'-'*22} {'-'*25} {'-'*40}")
    for row in rows:
        goal = (row.get("goal") or "").splitlines()[0][:60]
        print(f"{row['change_id']:22} {row.get('created_at', ''):25} {goal}")


def cmd_change_show(args: argparse.Namespace) -> None:
    from orchestrator.core.change_contract import (
        change_status_summary,
        read_change_contract,
        resolve_change_id,
    )

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])

    try:
        change_id = resolve_change_id(project_path, args.change_id)
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}")

    contract = read_change_contract(project_path, change_id)
    summary = change_status_summary(project_path, change_id)

    if args.json:
        print(json.dumps({"summary": summary, "contract": contract}, indent=2))
        return

    print(f"Project: {project['name']} ({project_id})")
    print(f"Change: {change_id}")
    print(f"State: {summary['state']}")
    print(f"Created at: {contract.get('created_at')}")
    print(f"Goal: {contract.get('goal')}")
    if contract.get("scope_paths"):
        print("Scope paths:")
        for sp in contract["scope_paths"]:
            print(f"  - {sp}")
    else:
        print("Scope paths: (none declared; scope_missing=True)" if contract.get("scope_missing") else "Scope paths: (none)")
    if contract.get("non_goals"):
        print("Non-goals:")
        for ng in contract["non_goals"]:
            print(f"  - {ng}")
    print(f"Acceptance criteria: {len(contract.get('acceptance') or [])} item(s)")
    for i, criterion in enumerate(contract.get("acceptance") or [], start=1):
        print(f"  AC-{i:03d}: {criterion}")
    print()
    print("Artifacts:")
    for key, value in (summary.get("artifacts") or {}).items():
        present = "exists" if value and Path(value).exists() else "(not yet)"
        print(f"  - {key}: {value or '(not yet)'} [{present}]")


def cmd_change_status(args: argparse.Namespace) -> None:
    from orchestrator.core.change_contract import change_status_summary, resolve_change_id

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])

    try:
        change_id = resolve_change_id(project_path, args.change_id)
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}")

    summary = change_status_summary(project_path, change_id)
    if args.json:
        print(json.dumps(summary, indent=2))
        return
    print(f"Project: {project['name']} ({project_id})")
    print(f"Change: {change_id}")
    print(f"State: {summary['state']}")
    print(f"Goal: {summary['goal']}")
    print(f"Scope paths: {len(summary['scope_paths'])} (missing={summary['scope_missing']})")
    print(f"Non-goals: {len(summary['non_goals'])}")
    print(f"Acceptance criteria: {summary['acceptance_count']}")
    print(f"Created at: {summary['created_at']}")
    print(f"Change dir: {summary['change_dir']}")


def cmd_change_validate(args: argparse.Namespace) -> None:
    from orchestrator.core.artifact_validation import (
        validate_applied_change,
        validate_change_contract,
        validate_delivery_report_text,
    )
    from orchestrator.core.change_contract import change_dir, resolve_change_id

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])

    try:
        change_id = resolve_change_id(project_path, args.change_id)
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}")

    cdir = change_dir(project_path, change_id)
    report: dict[str, list[str]] = {}

    contract_path = cdir / "change-contract.json"
    contract_errors: list[str] = []
    if not contract_path.exists():
        contract_errors.append(f"change-contract.json missing at {contract_path}")
    else:
        try:
            contract_payload = json.loads(contract_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            contract_errors.append(f"invalid JSON: {exc}")
        else:
            contract_errors.extend(validate_change_contract(contract_payload))
    report["change-contract.json"] = contract_errors

    delivery_path = cdir / "delivery-report.md"
    if delivery_path.exists():
        try:
            delivery_text = delivery_path.read_text(encoding="utf-8")
        except OSError as exc:
            report["delivery-report.md"] = [f"cannot read: {exc}"]
        else:
            report["delivery-report.md"] = validate_delivery_report_text(delivery_text)

    # RC-4A.3.1.C: applied-change.json validation. Optional — only present
    # post-`change run` on a `completed` outcome. When present we MUST
    # validate the agentic.applied_change.v1 schema so the operator's "did
    # this change actually deliver something coherent?" check is honest.
    applied_change_path = cdir / "applied-change.json"
    if applied_change_path.exists():
        try:
            applied_payload = json.loads(applied_change_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            report["applied-change.json"] = [f"cannot read / invalid JSON: {exc}"]
        else:
            report["applied-change.json"] = validate_applied_change(applied_payload)

    ok = all(not errs for errs in report.values())

    if args.json:
        print(json.dumps({
            "project_id": project_id,
            "change_id": change_id,
            "change_dir": str(cdir),
            "ok": ok,
            "report": report,
        }, indent=2))
        return

    print(f"Project: {project['name']} ({project_id})")
    print(f"Change: {change_id}")
    print(f"Validation: {'OK' if ok else 'FAILED'}")
    for artifact, errors in report.items():
        marker = "[ok]" if not errors else "[FAIL]"
        print(f"  {marker} {artifact}")
        for err in errors:
            print(f"    - {err}")
    if not ok:
        sys.exit(1)


def cmd_change_run(args: argparse.Namespace) -> None:
    """RC-4A.2: drive a change session through AutonomousController.

    Mirrors the autonomous-start wiring (real AgenticProjectRuntime + the
    Apply Gate) but runs exactly one task derived from the change-contract.
    Writes `applied-change.json` + `delivery-report.md` under the change
    dir on the way out and prints a status summary (or JSON) to stdout.
    """
    from orchestrator.core.change_contract import resolve_change_id
    from orchestrator.core.change_runner import run_change
    from orchestrator.core.run_package import apply_selected_candidate
    from orchestrator.core.deploy import load_agentic_config

    paths = _initialized_paths(args.root)
    engine = create_engine(paths)
    project_id = args.project or _latest_project_id(engine)
    project = engine.require_project(project_id)
    project_path = Path(project["path"])

    try:
        change_id = resolve_change_id(project_path, args.change_id)
    except FileNotFoundError as exc:
        raise SystemExit(f"error: {exc}")

    runtime = AgenticProjectRuntime(engine.db)
    agentic_config = load_agentic_config(project_path)

    def _run_inner_loop(*, project: dict[str, Any], intent_overrides: dict[str, Any]) -> AgenticRunResult:
        return runtime.run(
            project=project,
            intent_overrides=intent_overrides,
            patch_worker=agentic_config.patch_worker,
            execute_eval=(agentic_config.patch_worker == "codex"),
            timeout_sec=agentic_config.codex.timeout_sec,
            codex_sandbox=agentic_config.codex.sandbox,
            codex_ask_for_approval=agentic_config.codex.ask_for_approval,
            codex_command=agentic_config.codex.command,
        )

    def _apply(*, project_path: Path, run_dir: Path, selected_candidate: str) -> dict[str, Any]:
        record = apply_selected_candidate(
            project_path=project_path, run_dir=run_dir, selected_candidate=selected_candidate,
        )
        record["project_id"] = project["id"]
        return record

    try:
        result = run_change(
            project=project,
            change_id=change_id,
            run_inner_loop=_run_inner_loop,
            apply_candidate=_apply,
            allow_dirty_worktree=bool(getattr(args, "allow_dirty_worktree", False)),
        )
    except (RuntimeError, FileNotFoundError) as exc:
        raise SystemExit(f"error: {exc}")

    if args.json:
        print(json.dumps({
            "project_id": project_id,
            "change_id": result.change_id,
            "result": result.result,
            "session_id": result.session_id,
            "task_id": result.task_id,
            "commit_sha": result.commit_sha,
            "applied_change_json": str(result.applied_change_path) if result.applied_change_path else None,
            "delivery_report_md": str(result.delivery_report_path),
            "review_open_count": result.review_open_count,
        }, indent=2))
        if result.result != "completed":
            sys.exit(1)
        return

    print(f"Project: {project['name']} ({project_id})")
    print(f"Change: {result.change_id}")
    print(f"Result: {result.result}")
    if result.session_id:
        print(f"Session: {result.session_id}")
    if result.commit_sha:
        print(f"Commit: {result.commit_sha}")
    if result.applied_change_path:
        print(f"Applied change: {result.applied_change_path}")
    print(f"Delivery report: {result.delivery_report_path}")
    if result.review_open_count:
        print(f"Open review items: {result.review_open_count}")
        print(f"  agent-studio autonomous reviews list --project {project_id}")
    if result.result != "completed":
        sys.exit(1)


def _initialized_paths(root: str | Path | None):
    paths = resolve_paths(root)
    initialize_workspace(paths)
    load_local_env(paths)
    return paths


def _latest_project_id(engine) -> str:
    project = engine.latest_project()
    if not project:
        raise ValueError("No project exists. Run `agent-studio new \"...\"` first.")
    return project["id"]


def _has_pending_prd_approval(engine, project_id: str) -> bool:
    status = engine.status(project_id)
    return any(
        approval["status"] == "pending"
        and approval["phase_id"] == "prd"
        and approval["gate"] == "prd_approval"
        for approval in status["approvals"]
    )


def _print_prd_validation(validation) -> None:
    print(f"PRD validation: {'ok' if validation.ok else 'failed'}")
    if validation.errors:
        print("Errors:")
        for error in validation.errors:
            print(f"- {error}")
    if validation.warnings:
        print("Warnings:")
        for warning in validation.warnings:
            print(f"- {warning}")


def _print_run_result(result: dict[str, Any]) -> None:
    status = result["status"]
    run_id = result["run_id"]
    print(f"Run id: {run_id}")
    print(f"Status: {status}")
    if result.get("phase_id"):
        print(f"Current phase: {result['phase_id']}")
    if status == "needs_approval":
        phase = result.get("phase_id")
        print(f"Next: ./agent-studio approve {phase}")


def _print_agentic_run_result(result: AgenticRunResult) -> None:
    print(f"Run id: {result.run_id}")
    print(f"Status: {result.status}")
    print("Workflow: agentic_project")
    print(f"Decision: {result.decision}")
    print(f"Candidate: {result.candidate}")
    print(f"Run package: {result.run_dir}")
    print(f"Promotion report: {result.promotion_report_path}")
    print(f"Artifacts: {len(result.artifacts)}")


def _print_status(status: dict[str, Any]) -> None:
    project = status["project"]
    if not project:
        print("No projects found.")
        return
    print(f"Project: {project['name']} ({project['id']})")
    print(f"Path: {project['path']}")
    print(f"Project status: {project['status']}")
    run = status["run"]
    if not run:
        print("No runs yet.")
        return
    print(f"Run: {run['id']} [{run['workflow_id']}]")
    print(f"Run status: {run['status']}")
    if run.get("current_phase"):
        print(f"Current phase: {run['current_phase']}")

    phase_counts = Counter(phase["status"] for phase in status["phases"])
    task_counts = Counter(task["status"] for task in status["tasks"])
    print("Phases: " + _format_counts(phase_counts))
    print("Tasks: " + _format_counts(task_counts))

    pending = [approval for approval in status["approvals"] if approval["status"] == "pending"]
    if pending:
        print("Pending approvals:")
        for approval in pending:
            print(f"- {approval['phase_id']} ({approval['gate']}): {approval['reason']}")
    else:
        print("Pending approvals: none")


def _format_counts(counts: Counter[str]) -> str:
    if not counts:
        return "none"
    order = ["pending", "running", "needs_approval", "blocked", "failed", "completed", "skipped"]
    parts = [f"{key}={counts[key]}" for key in order if counts[key]]
    extras = [f"{key}={value}" for key, value in counts.items() if key not in order]
    return ", ".join(parts + extras)


if __name__ == "__main__":  # pragma: no cover
    main()
