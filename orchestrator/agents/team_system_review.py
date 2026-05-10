from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


@dataclass(frozen=True)
class TeamSystemReviewResult:
    overall_review_path: Path
    maturity_json_path: Path
    optimization_tasks_path: Path
    architecture_team_contract_path: Path
    qa_team_contract_path: Path
    review_team_contract_path: Path
    lead_team_contract_path: Path


class TeamSystemReviewAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> TeamSystemReviewResult:
        project_path = Path(project["path"])
        docs_dir = project_path / "docs/team-review"
        review_dir = project_path / "docs/review"
        teams_dir = project_path / ".agent/teams"
        tasks_dir = project_path / ".agent/tasks"
        docs_dir.mkdir(parents=True, exist_ok=True)
        review_dir.mkdir(parents=True, exist_ok=True)
        teams_dir.mkdir(parents=True, exist_ok=True)
        tasks_dir.mkdir(parents=True, exist_ok=True)

        paths = TeamSystemReviewResult(
            overall_review_path=docs_dir / "team-system-review.md",
            maturity_json_path=teams_dir / "team-maturity.json",
            optimization_tasks_path=tasks_dir / "team-optimization-tasks.json",
            architecture_team_contract_path=teams_dir / "architecture-team-contract.json",
            qa_team_contract_path=teams_dir / "qa-team-contract.json",
            review_team_contract_path=teams_dir / "review-team-contract.json",
            lead_team_contract_path=teams_dir / "lead-orchestrator-contract.json",
        )

        paths.architecture_team_contract_path.write_text(
            json.dumps(_architecture_team_contract(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths.qa_team_contract_path.write_text(json.dumps(_qa_team_contract(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths.review_team_contract_path.write_text(
            json.dumps(_review_team_contract(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths.lead_team_contract_path.write_text(json.dumps(_lead_team_contract(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths.overall_review_path.write_text("# Team System Review\n\nPreparing assessment.\n", encoding="utf-8")
        (review_dir / "blocker-resolution-matrix.md").write_text(_render_blocker_resolution_matrix(project_path), encoding="utf-8")
        (review_dir / "release-decision.md").write_text(_render_release_decision(project_path), encoding="utf-8")

        assessments = _assess_teams(project_path)
        optimization_tasks = _optimization_tasks(assessments)

        paths.overall_review_path.write_text(_render_overall_review(assessments), encoding="utf-8")
        paths.maturity_json_path.write_text(json.dumps({"teams": assessments}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        paths.optimization_tasks_path.write_text(json.dumps(optimization_tasks, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        for assessment in assessments:
            (docs_dir / f"{assessment['id']}-review.md").write_text(_render_team_review(assessment), encoding="utf-8")
            (teams_dir / f"{assessment['id']}-optimization.json").write_text(
                json.dumps(_team_optimization_contract(assessment), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in [
                paths.overall_review_path,
                paths.maturity_json_path,
                paths.optimization_tasks_path,
                paths.architecture_team_contract_path,
                paths.qa_team_contract_path,
                paths.review_team_contract_path,
                paths.lead_team_contract_path,
            ]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="planning",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="Team system review artifact.",
                )
            EventBus(self.db).emit(
                event_type="teams.system_review_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="planning",
                message="Reviewed and optimized all agent teams.",
                payload={"teams": len(assessments), "optimization_tasks": len(optimization_tasks)},
            )

        return paths


def _assess_teams(project_path: Path) -> list[dict[str, Any]]:
    definitions = [
        {
            "id": "prd_product_team",
            "name": "PRD/Product Team",
            "required": [
                "docs/product/research.md",
                "docs/product/research-plan.md",
                "docs/product/source-quality-report.md",
                "docs/product/reference-products/index.md",
                "docs/product/example-references/top-examples.md",
                "docs/product/example-references/visual-critic.md",
                "docs/product/example-references/multimodal-critic.md",
                "docs/product/options.md",
                "docs/product/decision.md",
                "docs/product/pm-debate.md",
                "docs/product/prd.md",
                "docs/product/product-fit.md",
                "docs/product/prd-score.md",
                "docs/product/prd-critique.md",
                "docs/product/prd-agent-team-contracts.json",
                "docs/product/post-build-product-review.md",
                "docs/product/post-build-product-review.json",
            ],
            "strength": "Research, options, council, scoring, product-fit, critique, and post-build product review are already present.",
            "optimization": "Keep PRD Team as the upstream standard, but make post-build blockers automatically feed downstream team tasks.",
        },
        {
            "id": "ui_product_team",
            "name": "UI Product Team",
            "required": [
                "docs/design/ui-team/ux-flow-lead.md",
                "docs/design/ui-team/visual-design-lead.md",
                "docs/design/ui-team/asset-strategy-lead.md",
                "docs/design/ui-team/visual-qa-lead.md",
                "docs/design/ui-team/design-critic.md",
                "docs/design/reference-to-design-traceability.md",
                "docs/design/screen-level-spec.md",
                "docs/design/template-spec.md",
                "docs/design/design-contract.json",
                "docs/design/ui-team/ui-team-contracts.json",
                "docs/design/ui-team/ui-team-score.json",
            ],
            "strength": "UI Team now has role outputs, critic, traceability, screen spec, template spec, and design contract.",
            "optimization": "Close visual QA evidence with browser screenshots and preview/export visual comparison.",
        },
        {
            "id": "architecture_team",
            "name": "Architecture Team",
            "required": [
                "docs/architecture/architecture.md",
                "docs/architecture/api.openapi.yaml",
                "docs/architecture/database-schema.md",
                "docs/architecture/adr/001-tech-stack.md",
                ".agent/tasks/generated-tasks.json",
                ".agent/teams/architecture-team-contract.json",
            ],
            "strength": "Architecture can generate stack, API, DB, ADR, and task DAG from product/design gates.",
            "optimization": "Split into Product Architect, Frontend Architect, Data/API Architect, and Test Architect with an explicit contract.",
        },
        {
            "id": "developer_team",
            "name": "Developer Team",
            "required": [
                "docs/implementation/developer-team/editor-workflow-developer.md",
                "docs/implementation/developer-team/preview-export-developer.md",
                "docs/implementation/developer-team/asset-handling-developer.md",
                "docs/implementation/developer-team/browser-test-developer.md",
                "docs/implementation/developer-team/integration-lead.md",
                "docs/implementation/implementation-contract.json",
                "docs/implementation/developer-team-task-plan.json",
                "docs/implementation/acceptance-matrix.md",
            ],
            "strength": "Developer Team now consumes UI design contract and produces module owners, task plan, and acceptance matrix.",
            "optimization": "Next step is execution: code remediation must update apps/web and tests from the implementation contract.",
        },
        {
            "id": "qa_team",
            "name": "QA Team",
            "required": [
                "docs/qa/qa-team-plan.md",
                "docs/qa/test-plan.md",
                "docs/qa/test-results.md",
                "docs/qa/bugs.md",
                ".agent/teams/qa-team-contract.json",
                ".agent/artifacts/qa/desktop-screenshot.png",
                ".agent/artifacts/qa/mobile-screenshot.png",
            ],
            "strength": "QA has static artifact checks and bug report output.",
            "optimization": "Promote QA into Acceptance QA, Browser QA, Visual QA, and Export Fidelity QA with screenshot evidence.",
        },
        {
            "id": "review_team",
            "name": "Review Team",
            "required": [
                "docs/review/review-team-plan.md",
                "docs/review/review-report.md",
                ".agent/teams/review-team-contract.json",
                "docs/review/blocker-resolution-matrix.md",
                "docs/review/release-decision.md",
            ],
            "strength": "Review can approve static implementation and read QA output.",
            "optimization": "Split code review, product review, evidence review, and release decision. Reviewer approval must not override failed product review.",
        },
        {
            "id": "lead_orchestrator_team",
            "name": "Lead/Orchestrator Team",
            "required": [
                ".agent/teams/downstream-agent-contracts.json",
                ".agent/tasks/downstream-remediation-tasks.json",
                ".agent/teams/lead-orchestrator-contract.json",
                "docs/team-review/team-system-review.md",
            ],
            "strength": "Lead flow can create projects, run gates, and generate downstream plans.",
            "optimization": "Add explicit gate semantics: first build allowed after PRD/design/architecture; final completion allowed only after QA, review, and post-build product review pass.",
        },
    ]
    assessments: list[dict[str, Any]] = []
    for definition in definitions:
        required = definition["required"]
        present = [path for path in required if (project_path / path).exists()]
        missing = [path for path in required if not (project_path / path).exists()]
        score = round(100 * len(present) / len(required)) if required else 0
        if score >= 85:
            status = "strong"
        elif score >= 60:
            status = "partial"
        else:
            status = "weak"
        assessments.append(
            {
                "id": definition["id"],
                "name": definition["name"],
                "status": status,
                "score": score,
                "present_count": len(present),
                "required_count": len(required),
                "present": present,
                "missing": missing,
                "strength": definition["strength"],
                "optimization": definition["optimization"],
            }
        )
    return assessments


def _optimization_tasks(assessments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for assessment in assessments:
        owner = assessment["id"]
        if assessment["missing"]:
            tasks.append(
                {
                    "id": f"TEAM-OPT-{len(tasks) + 1:03d}",
                    "owner": owner,
                    "phase": "team_optimization",
                    "priority": "high" if assessment["score"] < 75 else "medium",
                    "status": "pending",
                    "title": f"Optimize {assessment['name']}",
                    "description": assessment["optimization"],
                    "missing_artifacts": assessment["missing"],
                    "acceptance_criteria": [
                        "Missing team artifacts are generated",
                        "Team contract states owners, inputs, outputs, gates, and handoffs",
                        "Team score improves without pretending evidence exists",
                    ],
                }
            )
    return tasks


def _render_overall_review(assessments: list[dict[str, Any]]) -> str:
    rows = "\n".join(
        f"| {item['name']} | {item['score']}/100 | {item['status']} | {item['present_count']}/{item['required_count']} |"
        for item in assessments
    )
    incomplete = [item for item in assessments if item["score"] < 100]
    incomplete_lines = "\n".join(f"- {item['name']}: missing {len(item['missing'])} artifact(s)" for item in incomplete) or "- None."
    strong = [item["name"] for item in assessments if item["score"] == 100]
    if incomplete:
        decision = (
            f"{len(strong)} team(s) are complete. "
            f"{len(incomplete)} team(s) still need evidence or artifacts before they can claim final phase approval."
        )
    else:
        decision = "All teams have the required team contracts and evidence artifacts for this review pass."
    return f"""# Team System Review

## Maturity Matrix

| Team | Score | Status | Artifact Coverage |
| --- | ---: | --- | ---: |
{rows}

## Teams Still Not Complete

{incomplete_lines}

## Decision

{decision}

## Rule

A team can be allowed to continue work while still being incomplete, but it cannot claim final approval for its phase until its missing artifacts and gate evidence are closed.
"""


def _render_team_review(assessment: dict[str, Any]) -> str:
    missing = "\n".join(f"- {path}" for path in assessment["missing"]) or "- None."
    present = "\n".join(f"- {path}" for path in assessment["present"]) or "- None."
    return f"""# {assessment['name']} Review

Status: `{assessment['status']}`
Score: {assessment['score']}/100
Artifact coverage: {assessment['present_count']}/{assessment['required_count']}

## Strength

{assessment['strength']}

## Optimization

{assessment['optimization']}

## Present Artifacts

{present}

## Missing Artifacts

{missing}
"""


def _team_optimization_contract(assessment: dict[str, Any]) -> dict[str, Any]:
    return {
        "team_id": assessment["id"],
        "team_name": assessment["name"],
        "score": assessment["score"],
        "status": assessment["status"],
        "optimization": assessment["optimization"],
        "missing": assessment["missing"],
        "gate_rule": "Do not claim final approval while required evidence or team artifacts are missing.",
    }


def _load_post_build_review(project_path: Path) -> dict[str, Any]:
    path = project_path / "docs/product/post-build-product-review.json"
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _render_blocker_resolution_matrix(project_path: Path) -> str:
    review = _load_post_build_review(project_path)
    blockers = [str(item) for item in review.get("blockers", [])] if review else []
    if not blockers:
        rows = "| None | No post-build blockers found | N/A | N/A |\n"
    else:
        rows = "\n".join(
            f"| B{index:02d} | {blocker} | pending remediation | Requires UI/Dev/QA evidence |"
            for index, blocker in enumerate(blockers, start=1)
        )
    return f"""# Blocker Resolution Matrix

## Rule

Review Team cannot approve final completion until each product-review blocker has implementation evidence and QA evidence.

| Id | Blocker | Status | Required Evidence |
| --- | --- | --- | --- |
{rows}
"""


def _render_release_decision(project_path: Path) -> str:
    review = _load_post_build_review(project_path)
    status = str(review.get("status", "unknown"))
    decision = "blocked" if status in {"fail", "needs_revision", "unknown"} else "approved"
    if decision == "approved":
        current_decision = "This build can claim final completion from the product-review perspective."
    else:
        current_decision = f"This build remains blocked from final completion while post-build product review is `{status}`."
    return f"""# Release Decision

Decision: `{decision}`
Post-build product review status: `{status}`

## Rule

Release Lead may approve final completion only when:

- QA Team has static, browser, visual, and export-fidelity evidence.
- Review Team has closed every blocker in `blocker-resolution-matrix.md`.
- Post-build product review status is `pass`.

## Current Decision

{current_decision}
"""


def _architecture_team_contract() -> dict[str, Any]:
    return {
        "version": 1,
        "team_id": "architecture_team",
        "members": ["product_architect", "frontend_architect", "data_api_architect", "test_architect"],
        "inputs": ["docs/product/prd.md", "docs/design/design-contract.json", "docs/implementation/implementation-contract.json"],
        "outputs": ["docs/architecture/architecture.md", "docs/architecture/api.openapi.yaml", "docs/architecture/database-schema.md", ".agent/tasks/generated-tasks.json"],
        "gate": "Architecture may proceed to implementation only when PRD, design critique, and design contract are present.",
    }


def _qa_team_contract() -> dict[str, Any]:
    return {
        "version": 1,
        "team_id": "qa_team",
        "members": ["acceptance_qa", "browser_qa", "visual_qa", "export_fidelity_qa"],
        "inputs": ["docs/product/acceptance-criteria.md", "docs/design/visual-qa-checklist.md", "docs/implementation/implementation-contract.json", "apps/web/index.html"],
        "outputs": ["docs/qa/test-plan.md", "docs/qa/test-results.md", "docs/qa/bugs.md", ".agent/artifacts/qa/desktop-screenshot.png", ".agent/artifacts/qa/mobile-screenshot.png"],
        "gate": "QA approval requires static checks, browser interaction checks, screenshots, and preview/export fidelity notes.",
    }


def _review_team_contract() -> dict[str, Any]:
    return {
        "version": 1,
        "team_id": "review_team",
        "members": ["code_reviewer", "product_reviewer", "evidence_reviewer", "release_lead"],
        "inputs": ["docs/qa/test-results.md", "docs/qa/bugs.md", "docs/product/post-build-product-review.md", "docs/implementation/acceptance-matrix.md"],
        "outputs": ["docs/review/review-report.md", "docs/review/blocker-resolution-matrix.md", "docs/review/release-decision.md"],
        "gate": "Reviewer approval cannot override failed product review; release lead can approve final completion only after post-build product review passes.",
    }


def _lead_team_contract() -> dict[str, Any]:
    return {
        "version": 1,
        "team_id": "lead_orchestrator_team",
        "members": ["workflow_lead", "gatekeeper", "task_board_owner", "evidence_librarian"],
        "inputs": [".agent/tasks/*.json", ".agent/teams/*.json", "docs/**"],
        "outputs": [".agent/tasks/team-optimization-tasks.json", ".agent/teams/team-maturity.json", "docs/team-review/team-system-review.md"],
        "gate": "Lead may start remediation when a team handoff is ready, but final completion requires QA, Review, and post-build product review pass.",
    }
