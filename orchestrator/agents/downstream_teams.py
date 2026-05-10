from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


@dataclass(frozen=True)
class DownstreamTeamsResult:
    ui_team_plan_path: Path
    developer_team_plan_path: Path
    qa_team_plan_path: Path
    review_team_plan_path: Path
    contracts_json_path: Path
    remediation_tasks_path: Path


class DownstreamTeamsAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> DownstreamTeamsResult:
        project_path = Path(project["path"])
        review = _load_build_review(project_path)
        domain_type = review.get("domain_type") or _domain_type(project.get("idea", ""))
        blockers = [str(item) for item in review.get("blockers", [])]
        status = str(review.get("status", "unknown"))

        paths = DownstreamTeamsResult(
            ui_team_plan_path=project_path / "docs/design/ui-team-plan.md",
            developer_team_plan_path=project_path / "docs/implementation/developer-team-plan.md",
            qa_team_plan_path=project_path / "docs/qa/qa-team-plan.md",
            review_team_plan_path=project_path / "docs/review/review-team-plan.md",
            contracts_json_path=project_path / ".agent/teams/downstream-agent-contracts.json",
            remediation_tasks_path=project_path / ".agent/tasks/downstream-remediation-tasks.json",
        )
        for path in [
            paths.ui_team_plan_path,
            paths.developer_team_plan_path,
            paths.qa_team_plan_path,
            paths.review_team_plan_path,
            paths.contracts_json_path,
            paths.remediation_tasks_path,
        ]:
            path.parent.mkdir(parents=True, exist_ok=True)

        contracts = _contracts(domain_type)
        tasks = _remediation_tasks(domain_type, blockers, status)
        paths.ui_team_plan_path.write_text(_ui_team_plan(domain_type, blockers, status), encoding="utf-8")
        paths.developer_team_plan_path.write_text(_developer_team_plan(domain_type, blockers, status), encoding="utf-8")
        paths.qa_team_plan_path.write_text(_qa_team_plan(domain_type, blockers, status), encoding="utf-8")
        paths.review_team_plan_path.write_text(_review_team_plan(domain_type, blockers, status), encoding="utf-8")
        paths.contracts_json_path.write_text(json.dumps(contracts, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths.remediation_tasks_path.write_text(json.dumps(tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in [
                paths.ui_team_plan_path,
                paths.developer_team_plan_path,
                paths.qa_team_plan_path,
                paths.review_team_plan_path,
                paths.contracts_json_path,
                paths.remediation_tasks_path,
            ]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="planning",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="Downstream agent team plan.",
                )
            EventBus(self.db).emit(
                event_type="downstream_teams.planned",
                project_id=project["id"],
                run_id=run_id,
                phase_id="planning",
                message=f"Planned downstream teams for {domain_type} remediation.",
                payload={"domain_type": domain_type, "status": status, "task_count": len(tasks)},
            )

        return paths


def _load_build_review(project_path: Path) -> dict[str, Any]:
    path = project_path / "docs/product/post-build-product-review.json"
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _domain_type(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ["portfolio", "personal website", "作品集"]):
        return "portfolio"
    if any(term in lower for term in ["invoice", "freelance", "billable"]):
        return "freelance"
    if any(term in lower for term in ["expense", "transaction", "记账"]):
        return "expense"
    return "generic"


def _contracts(domain_type: str) -> dict[str, Any]:
    return {
        "version": 1,
        "gate_semantics": {
            "pre_build": "PRD/product-fit/design/architecture gates decide whether the first implementation may start.",
            "post_build": "Post-build product review decides whether a generated product can be treated as shippable or must enter remediation.",
            "failed_post_build_review": "Development may continue only as remediation work against explicit blockers; do not call the product complete.",
        },
        "domain_type": domain_type,
        "teams": [
            {
                "id": "ui_product_team",
                "members": ["ux_flow_lead", "visual_design_lead", "asset_strategy_lead", "visual_qa_lead"],
                "owns": ["docs/design/ui-team-plan.md", "docs/design/**"],
                "handoff_to": ["developer_team", "qa_team"],
            },
            {
                "id": "developer_team",
                "members": ["editor_workflow_developer", "preview_export_developer", "asset_handling_developer", "browser_test_developer"],
                "owns": ["apps/web/**", "tests/**", "docs/implementation/**"],
                "handoff_to": ["qa_team"],
            },
            {
                "id": "qa_team",
                "members": ["acceptance_qa", "visual_qa", "browser_qa"],
                "owns": ["docs/qa/**", "tests/**", ".agent/artifacts/qa/**"],
                "handoff_to": ["review_team"],
            },
            {
                "id": "review_team",
                "members": ["code_reviewer", "product_reviewer", "release_lead"],
                "owns": ["docs/review/**"],
                "handoff_to": ["prd_build_review"],
            },
        ],
    }


def _remediation_tasks(domain_type: str, blockers: list[str], status: str) -> list[dict[str, Any]]:
    base_tasks = [
        {
            "id": "TEAM-GATE-001",
            "title": "Apply post-build review gate semantics",
            "owner": "lead",
            "phase": "planning",
            "priority": "high",
            "status": "pending",
            "description": "Treat the current build as remediation work, not completed product work, until product review passes.",
            "acceptance_criteria": [
                "Post-build review status is visible to downstream teams",
                "No merge/final-complete claim is made while status is fail or needs_revision",
            ],
            "allowed_paths": ["docs/product/**", ".agent/tasks/**", ".agent/teams/**"],
        }
    ]
    if domain_type == "portfolio":
        domain_tasks = [
            {
                "id": "UI-TEAM-001",
                "title": "Design portfolio quality coaching and templates",
                "owner": "ui_product_team",
                "phase": "design",
                "priority": "high",
                "status": "pending",
                "description": "Create guided portfolio sections, quality prompts, and 3-5 concrete templates informed by reference products.",
                "acceptance_criteria": [
                    "User is coached to write outcome-driven case studies",
                    "Template choices visibly reflect research patterns",
                    "Design includes empty, incomplete, and ready states",
                ],
                "allowed_paths": ["docs/design/**", "docs/product/**"],
            },
            {
                "id": "DEV-TEAM-001",
                "title": "Implement guided case-study workflow",
                "owner": "developer_team",
                "phase": "implementation",
                "priority": "high",
                "status": "pending",
                "description": "Add fields and preview rendering for problem, role, process, outcome, metrics, and proof quality.",
                "acceptance_criteria": [
                    "Project editor supports case-study fields",
                    "Preview renders case studies as credible portfolio proof",
                    "Export includes the same case-study content as preview",
                ],
                "allowed_paths": ["apps/web/**", "tests/**", "docs/implementation/**"],
            },
            {
                "id": "DEV-TEAM-002",
                "title": "Complete image asset lifecycle",
                "owner": "developer_team",
                "phase": "implementation",
                "priority": "high",
                "status": "pending",
                "description": "Add screenshot remove/replace states, alt text, crop guidance, and generated-image placeholder rules.",
                "acceptance_criteria": [
                    "Avatar and screenshot can be removed after upload",
                    "Each project image has alt text or decorative intent",
                    "Invalid, oversized, failed, replace, and remove states are visible",
                ],
                "allowed_paths": ["apps/web/**", "tests/**"],
            },
            {
                "id": "QA-TEAM-001",
                "title": "Add browser and visual verification",
                "owner": "qa_team",
                "phase": "qa",
                "priority": "high",
                "status": "pending",
                "description": "Verify live preview and exported HTML in a real browser with desktop/mobile screenshots.",
                "acceptance_criteria": [
                    "Browser test opens apps/web/index.html",
                    "Desktop and mobile screenshots are captured",
                    "Preview/export visual mismatch is reported as a product defect",
                ],
                "allowed_paths": ["tests/**", "docs/qa/**", ".agent/artifacts/qa/**"],
            },
            {
                "id": "REVIEW-TEAM-001",
                "title": "Review remediation against product blockers",
                "owner": "review_team",
                "phase": "review",
                "priority": "high",
                "status": "pending",
                "description": "Approve only when post-build blockers are resolved with product evidence, QA evidence, and implementation evidence.",
                "acceptance_criteria": [
                    "Each post-build blocker has an explicit resolution note",
                    "QA evidence includes static and browser checks",
                    "Product review score is pass before final completion",
                ],
                "allowed_paths": ["docs/review/**", "docs/product/**"],
            },
        ]
    else:
        domain_tasks = [
            {
                "id": "UI-TEAM-001",
                "title": f"Design {domain_type} product workflow",
                "owner": "ui_product_team",
                "phase": "design",
                "priority": "high",
                "status": "pending",
                "description": "Turn research and PRD intent into a real workflow instead of a generic generated shell.",
                "acceptance_criteria": ["Workflow states are explicit", "Reference patterns are visible", "QA-ready acceptance states exist"],
                "allowed_paths": ["docs/design/**"],
            },
            {
                "id": "DEV-TEAM-001",
                "title": f"Implement {domain_type} workflow",
                "owner": "developer_team",
                "phase": "implementation",
                "priority": "high",
                "status": "pending",
                "description": "Replace the static shell with the core product workflow.",
                "acceptance_criteria": ["Core workflow is interactive", "Output artifact is useful", "Tests cover the primary path"],
                "allowed_paths": ["apps/web/**", "tests/**"],
            },
        ]
    for task in domain_tasks:
        task["blocked_by"] = blockers
        task["post_build_review_status"] = status
    return base_tasks + domain_tasks


def _ui_team_plan(domain_type: str, blockers: list[str], status: str) -> str:
    blocker_lines = "\n".join(f"- {item}" for item in blockers) or "- None."
    return f"""# UI Product Team Plan

Post-build review status: `{status}`
Domain: `{domain_type}`

## Rule

UI work may continue, but only as remediation against the product review blockers. This is not a fresh design pass.

## Blockers To Resolve

{blocker_lines}

## Team Responsibilities

- UX Flow Lead: convert blockers into corrected user flows and states.
- Visual Design Lead: turn research references into concrete templates and hierarchy.
- Asset Strategy Lead: define upload, generated-image, crop, alt text, replacement, and export asset rules.
- Visual QA Lead: define desktop/mobile screenshot expectations before implementation.
"""


def _developer_team_plan(domain_type: str, blockers: list[str], status: str) -> str:
    blocker_lines = "\n".join(f"- {item}" for item in blockers) or "- None."
    return f"""# Developer Team Plan

Post-build review status: `{status}`
Domain: `{domain_type}`

## Rule

Development can continue only as a remediation iteration. Do not mark implementation complete until QA, review, and post-build product review pass.

## Blockers To Resolve

{blocker_lines}

## Team Responsibilities

- Editor Workflow Developer: owns form structure, validation, guidance, and state transitions.
- Preview and Export Developer: owns shared render model, preview fidelity, and export fidelity.
- Asset Handling Developer: owns upload, remove, replace, alt text, and generated-image placeholders.
- Browser Test Developer: owns browser automation hooks and screenshot verification.
"""


def _qa_team_plan(domain_type: str, blockers: list[str], status: str) -> str:
    blocker_lines = "\n".join(f"- {item}" for item in blockers) or "- None."
    return f"""# QA Team Plan

Post-build review status: `{status}`
Domain: `{domain_type}`

## Rule

Static checks are not enough. QA must provide product evidence and browser evidence.

## Blockers To Verify

{blocker_lines}

## Team Responsibilities

- Acceptance QA: maps PRD criteria and post-build blockers to test cases.
- Browser QA: opens the generated app in a browser and verifies interactions.
- Visual QA: captures desktop/mobile screenshots and flags visual/product quality issues.
"""


def _review_team_plan(domain_type: str, blockers: list[str], status: str) -> str:
    blocker_lines = "\n".join(f"- {item}" for item in blockers) or "- None."
    return f"""# Review Team Plan

Post-build review status: `{status}`
Domain: `{domain_type}`

## Rule

Reviewer approval cannot override a failed or needs_revision product review. It can only confirm code quality and evidence readiness for the next product review.

## Blockers To Close

{blocker_lines}

## Team Responsibilities

- Code Reviewer: checks implementation safety, maintainability, and scope control.
- Product Reviewer: checks that blocker fixes make the product stronger, not just larger.
- Release Lead: allows final completion only after post-build review status is `pass`.
"""
