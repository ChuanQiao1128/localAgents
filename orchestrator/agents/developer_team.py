from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


@dataclass(frozen=True)
class DeveloperTeamResult:
    editor_workflow_path: Path
    preview_export_path: Path
    asset_handling_path: Path
    browser_test_path: Path
    integration_lead_path: Path
    implementation_contract_path: Path
    task_plan_path: Path
    acceptance_matrix_path: Path
    score_json_path: Path


class DeveloperTeamAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> DeveloperTeamResult:
        project_path = Path(project["path"])
        implementation_dir = project_path / "docs/implementation"
        team_dir = implementation_dir / "developer-team"
        team_dir.mkdir(parents=True, exist_ok=True)
        contract = _load_design_contract(project_path)
        handoff = _read(project_path / "docs/design/ui-team-dev-handoff.md")
        selected_visual_direction = _read(project_path / "docs/design/selected-visual-direction.md")
        visual_direction_context = _load_visual_direction_context(project_path, selected_visual_direction)
        blockers = _load_post_build_blockers(project_path)
        domain_type = str(contract.get("domain_type") or _domain_type(project.get("idea", "")))
        score = _score_payload(contract, handoff, blockers, domain_type, selected_visual_direction, visual_direction_context)
        implementation_contract = _implementation_contract(domain_type, contract, blockers, selected_visual_direction, visual_direction_context)
        task_plan = _task_plan(domain_type, implementation_contract, blockers)

        result = DeveloperTeamResult(
            editor_workflow_path=team_dir / "editor-workflow-developer.md",
            preview_export_path=team_dir / "preview-export-developer.md",
            asset_handling_path=team_dir / "asset-handling-developer.md",
            browser_test_path=team_dir / "browser-test-developer.md",
            integration_lead_path=team_dir / "integration-lead.md",
            implementation_contract_path=implementation_dir / "implementation-contract.json",
            task_plan_path=implementation_dir / "developer-team-task-plan.json",
            acceptance_matrix_path=implementation_dir / "acceptance-matrix.md",
            score_json_path=team_dir / "developer-team-score.json",
        )

        result.editor_workflow_path.write_text(_editor_workflow_developer(domain_type, blockers), encoding="utf-8")
        result.preview_export_path.write_text(_preview_export_developer(domain_type, blockers), encoding="utf-8")
        result.asset_handling_path.write_text(_asset_handling_developer(domain_type, blockers), encoding="utf-8")
        result.browser_test_path.write_text(_browser_test_developer(domain_type, blockers), encoding="utf-8")
        result.integration_lead_path.write_text(_integration_lead(domain_type, score), encoding="utf-8")
        result.implementation_contract_path.write_text(
            json.dumps(implementation_contract, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result.task_plan_path.write_text(json.dumps(task_plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result.acceptance_matrix_path.write_text(_acceptance_matrix(domain_type, task_plan, blockers), encoding="utf-8")
        result.score_json_path.write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in [
                result.editor_workflow_path,
                result.preview_export_path,
                result.asset_handling_path,
                result.browser_test_path,
                result.integration_lead_path,
                result.implementation_contract_path,
                result.task_plan_path,
                result.acceptance_matrix_path,
                result.score_json_path,
            ]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="implementation",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="Developer Team artifact.",
                )
            EventBus(self.db).emit(
                event_type="implementation.developer_team_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="implementation",
                message=f"Generated Developer Team package for {domain_type}.",
                payload={"domain_type": domain_type, "status": score["status"], "score": score["final_score"]},
            )

        return result


def _load_design_contract(project_path: Path) -> dict[str, Any]:
    path = project_path / "docs/design/design-contract.json"
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _load_post_build_blockers(project_path: Path) -> list[str]:
    path = project_path / "docs/product/post-build-product-review.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    blockers = payload.get("blockers", []) if isinstance(payload, dict) else []
    return [str(item) for item in blockers if str(item).strip()]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _load_visual_direction_context(project_path: Path, selected_text: str) -> dict[str, Any]:
    variants_path = project_path / ".agent/artifacts/visual_directions/variants.json"
    review_json_path = project_path / "docs/design/visual-direction-multimodal-review.json"
    payload = _load_json(variants_path)
    review = _load_json(review_json_path)
    selected_id = _parse_visual_direction_id(selected_text)
    multimodal = payload.get("multimodal_review") if isinstance(payload, dict) else None
    if isinstance(multimodal, dict) and str(multimodal.get("winner_id") or "").strip():
        selected_id = str(multimodal["winner_id"])
    if isinstance(review, dict) and str(review.get("winner_id") or "").strip():
        selected_id = str(review["winner_id"])
    variant = _variant_for_id(payload, selected_id)
    return {
        "id": selected_id,
        "selection_method": "multimodal_review" if isinstance(multimodal, dict) or review else ("selected_markdown" if selected_id else ""),
        "source_artifact": "docs/design/selected-visual-direction.md" if selected_text.strip() else None,
        "review_artifact": "docs/design/visual-direction-multimodal-review.md" if review or isinstance(multimodal, dict) else None,
        "screenshot_artifact": variant.get("screenshot_path") if variant else None,
        "screenshot_quality": variant.get("screenshot_quality") if variant else None,
    }


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _parse_visual_direction_id(text: str) -> str:
    patterns = [
        r"Winner:\s*`([^`]+)`",
        r"Winner:\s*([A-Za-z0-9_-]+)",
        r"获胜(?:方向|者)?[:：]\s*`?([A-Za-z0-9_-]+)`?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    for known_id in ["dense-dashboard", "minimalist-editorial", "bold-marketing", "proof-first-case-study", "creator-studio"]:
        if known_id in text:
            return known_id
    return ""


def _variant_for_id(payload: dict[str, Any], variant_id: str) -> dict[str, Any]:
    variants = payload.get("variants", []) if isinstance(payload, dict) else []
    if isinstance(variants, list):
        for variant in variants:
            if isinstance(variant, dict) and str(variant.get("id") or "") == variant_id:
                return variant
    winner = payload.get("winner", {}) if isinstance(payload, dict) else {}
    if isinstance(winner, dict) and str(winner.get("id") or "") == variant_id:
        return winner
    return {}


def _domain_type(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ["portfolio", "personal website", "作品集"]):
        return "portfolio"
    if any(term in lower for term in ["invoice", "freelance", "billable"]):
        return "freelance"
    if any(term in lower for term in ["expense", "transaction", "记账"]):
        return "expense"
    return "generic"


def _score_payload(
    contract: dict[str, Any],
    handoff: str,
    blockers: list[str],
    domain_type: str,
    selected_visual_direction: str,
    visual_direction_context: dict[str, Any],
) -> dict[str, Any]:
    dimensions = {
        "design_contract_present": 20 if contract else 0,
        "handoff_present": 15 if handoff.strip() else 0,
        "blocker_traceability": 15 if blockers else 8,
        "screen_coverage": min(20, len(contract.get("screens", [])) * 4) if contract else 0,
        "template_coverage": min(20, len(contract.get("templates", [])) * 5) if contract else 0,
        "module_contract": 20 if domain_type == "portfolio" else 12,
        "test_contract": 15,
        "selected_visual_direction": 10 if selected_visual_direction.strip() else 0,
        "visual_evidence_ready": 10 if visual_direction_context.get("review_artifact") and visual_direction_context.get("screenshot_artifact") else 0,
    }
    final_score = sum(dimensions.values())
    max_score = 155
    if not contract:
        status = "blocked_missing_design_contract"
    elif final_score >= 95:
        status = "ready_for_remediation_implementation"
    else:
        status = "needs_revision"
    return {
        "domain_type": domain_type,
        "final_score": final_score,
        "max_score": max_score,
        "status": status,
        "dimensions": dimensions,
        "missing_evidence": [
            "Developer Team can plan browser tests, but QA Team must still capture screenshots.",
            "Post-build product review must rerun after remediation implementation.",
        ],
        "gate_meaning": "Approves Developer Team remediation planning; it does not approve implementation as complete.",
    }


def _implementation_contract(
    domain_type: str,
    design_contract: dict[str, Any],
    blockers: list[str],
    selected_visual_direction: str,
    visual_direction_context: dict[str, Any],
) -> dict[str, Any]:
    if domain_type == "portfolio":
        return {
            "version": 1,
            "domain_type": "portfolio",
            "gate": "developer_team_implementation_contract",
            "gate_meaning": "Developer Team may implement remediation against this contract; final product still requires QA, Review, and post-build product review.",
            "blockers": blockers,
            "source_design_contract": "docs/design/design-contract.json",
            "source_visual_direction": "docs/design/selected-visual-direction.md" if selected_visual_direction.strip() else None,
            "selected_visual_direction": visual_direction_context,
            "write_scope": ["apps/web/**", "tests/**", "docs/implementation/**"],
            "modules": [
                {
                    "id": "state_model",
                    "owner": "integration_lead",
                    "responsibility": "Define portfolio state fields shared by editor, preview, export, save, and tests.",
                    "fields": [
                        "profile.intent",
                        "profile.template",
                        "profile.primaryAudience",
                        "profile.positioningLine",
                        "projects[].problem",
                        "projects[].process",
                        "projects[].outcome",
                        "projects[].metrics",
                        "projects[].proofNotes",
                        "projects[].altText",
                        "projects[].imageIsDecorative",
                        "projects[].placeholderActive",
                    ],
                },
                {
                    "id": "editor_workflow",
                    "owner": "editor_workflow_developer",
                    "responsibility": "Implement intent/template picker, guided case-study fields, and quality coaching.",
                    "functions": ["bindTemplateFields", "renderProjectEditors", "evaluateQualityChecklist"],
                },
                {
                    "id": "preview_export",
                    "owner": "preview_export_developer",
                    "responsibility": "Use one render model for live preview and exported HTML.",
                    "functions": ["renderPreview", "renderProjectCard", "renderQualityChecklist", "exportStaticHtml"],
                },
                {
                    "id": "asset_handling",
                    "owner": "asset_handling_developer",
                    "responsibility": "Handle upload, replace, remove, alt text, decorative intent, and placeholder warnings.",
                    "functions": ["readImage", "removeAvatar", "removeProjectImage", "validateProjectAsset"],
                },
                {
                    "id": "browser_tests",
                    "owner": "browser_test_developer",
                    "responsibility": "Add browser-test hooks and scripted checks for editor, preview, export, and responsive behavior.",
                    "outputs": ["tests/portfolio-builder-browser-checklist.md", "tests/portfolio-builder-contract-smoke.md"],
                },
            ],
            "design_screens": design_contract.get("screens", []),
            "design_templates": design_contract.get("templates", []),
            "done_when": [
                "Every UI Team blocker has a code or test resolution.",
                "Template selection changes preview layout and export output.",
                "Project editor supports problem, process, outcome, metrics, proof notes, image alt text, and screenshot removal.",
                "Quality checklist blocks export when proof or asset rules fail.",
                "Exported HTML preserves template, case-study content, asset warnings, and accessibility labels.",
                "apps/web/visual-direction.json records the same selected visual direction id and review artifact used by this contract.",
            ],
        }
    return {
        "version": 1,
        "domain_type": domain_type,
        "gate": "developer_team_implementation_contract",
        "selected_visual_direction": visual_direction_context,
        "write_scope": ["apps/web/**", "tests/**", "docs/implementation/**"],
        "modules": [
            {"id": "state_model", "owner": "integration_lead"},
            {"id": "primary_workflow", "owner": "editor_workflow_developer"},
            {"id": "output_preview", "owner": "preview_export_developer"},
            {"id": "tests", "owner": "browser_test_developer"},
        ],
    }


def _task_plan(domain_type: str, contract: dict[str, Any], blockers: list[str]) -> list[dict[str, Any]]:
    if domain_type == "portfolio":
        tasks = [
            {
                "id": "DEV-EDITOR-001",
                "owner": "editor_workflow_developer",
                "title": "Implement guided portfolio editor workflow",
                "allowed_paths": ["apps/web/index.html", "apps/web/app.js", "apps/web/styles.css"],
                "acceptance_criteria": [
                    "Intent and template picker exists",
                    "Project editor includes problem, process, outcome, metrics, and proof notes",
                    "Quality checklist shows weak-proof warnings",
                ],
            },
            {
                "id": "DEV-PREVIEW-001",
                "owner": "preview_export_developer",
                "title": "Implement shared preview and export render model",
                "allowed_paths": ["apps/web/app.js", "apps/web/styles.css"],
                "acceptance_criteria": [
                    "Preview renders selected template",
                    "Exported HTML includes the same template and case-study content",
                    "Export is blocked when quality checklist fails",
                ],
            },
            {
                "id": "DEV-ASSET-001",
                "owner": "asset_handling_developer",
                "title": "Complete image lifecycle and asset integrity",
                "allowed_paths": ["apps/web/index.html", "apps/web/app.js", "apps/web/styles.css"],
                "acceptance_criteria": [
                    "Avatar and screenshot remove actions exist",
                    "Project images include alt text or decorative intent",
                    "Generated placeholders are labelled before export",
                ],
            },
            {
                "id": "DEV-TEST-001",
                "owner": "browser_test_developer",
                "title": "Add browser and contract test handoff",
                "allowed_paths": ["tests/**", "docs/implementation/**"],
                "acceptance_criteria": [
                    "Browser checklist covers desktop and mobile",
                    "Preview/export fidelity checks are defined",
                    "All Developer Team modules have smoke checks",
                ],
            },
            {
                "id": "DEV-INTEGRATION-001",
                "owner": "integration_lead",
                "title": "Integrate remediation and prepare QA handoff",
                "allowed_paths": ["apps/web/**", "tests/**", "docs/implementation/**"],
                "acceptance_criteria": [
                    "No unrelated feature scope is added",
                    "Implementation traceability links blockers to code and tests",
                    "QA Team receives browser evidence requirements",
                ],
            },
        ]
    else:
        tasks = [
            {
                "id": "DEV-WORKFLOW-001",
                "owner": "editor_workflow_developer",
                "title": f"Implement guided {domain_type} workflow",
                "allowed_paths": ["apps/web/**", "tests/**"],
                "acceptance_criteria": ["Core workflow is interactive", "Output artifact is inspectable"],
            }
        ]
    for task in tasks:
        task["blocked_by"] = blockers
        task["source_contract"] = contract.get("gate")
        task["status"] = "pending"
        task["priority"] = "high"
    return tasks


def _editor_workflow_developer(domain_type: str, blockers: list[str]) -> str:
    if domain_type == "portfolio":
        return f"""# Editor Workflow Developer

## Mission

Turn the editor from a flat profile/project form into a guided case-study workflow.

## Blockers Addressed

{_blocker_lines(blockers)}

## Required Implementation

- Add intent selector: job search, freelance sales, founder profile, creative showcase.
- Add template selector: Editorial Case Study, Visual Gallery, Builder Resume, Proof-First Landing Page.
- Add project fields: problem, process, outcome, metrics, proof notes, alt text, decorative image toggle.
- Add weak-proof warnings for missing outcome, missing metric/evidence, and placeholder-only project.
- Keep all fields persisted in localStorage.

## Owned Files

- `apps/web/index.html`
- `apps/web/app.js`
- `apps/web/styles.css`
"""
    return f"""# Editor Workflow Developer

## Mission

Implement the guided {domain_type} workflow.

## Blockers Addressed

{_blocker_lines(blockers)}
"""


def _preview_export_developer(domain_type: str, blockers: list[str]) -> str:
    if domain_type == "portfolio":
        return f"""# Preview And Export Developer

## Mission

Make preview and exported HTML share the same render model.

## Blockers Addressed

{_blocker_lines(blockers)}

## Required Implementation

- Render selected template in preview.
- Render case-study fields in project cards and project detail sections.
- Include quality warnings in preview until resolved.
- Block export when required proof fields or asset labels are missing.
- Export the same template, content, asset labels, and contact links as preview.

## Owned Functions

- `renderPreview`
- `renderProjectCard`
- `renderQualityChecklist`
- `exportStaticHtml`
"""
    return f"""# Preview And Export Developer

## Mission

Keep preview and output consistent for {domain_type}.

## Blockers Addressed

{_blocker_lines(blockers)}
"""


def _asset_handling_developer(domain_type: str, blockers: list[str]) -> str:
    if domain_type == "portfolio":
        return f"""# Asset Handling Developer

## Mission

Complete media lifecycle and preserve proof integrity.

## Blockers Addressed

{_blocker_lines(blockers)}

## Required Implementation

- Add remove screenshot action for each project.
- Add image alt text field for each project.
- Add decorative image toggle.
- Add generated placeholder warning state.
- Preserve asset warnings in preview and export.
- Do not generate or imply fake headshots, fake logos, fake screenshots, fake credentials, or fake testimonials.

## Owned Functions

- `readImage`
- `removeAvatar`
- `removeProjectImage`
- `validateProjectAsset`
"""
    return f"""# Asset Handling Developer

## Mission

Implement asset integrity for {domain_type}.

## Blockers Addressed

{_blocker_lines(blockers)}
"""


def _browser_test_developer(domain_type: str, blockers: list[str]) -> str:
    return f"""# Browser Test Developer

## Mission

Prepare real-browser verification so QA Team can close visual evidence gaps.

## Blockers Addressed

{_blocker_lines(blockers)}

## Required Checks

- Open `apps/web/index.html` in a browser.
- Verify desktop, tablet, and mobile layout.
- Fill required editor fields.
- Trigger export-blocked state.
- Resolve quality checklist.
- Export HTML and compare preview/export content.
- Capture screenshots for QA artifacts.

## Output Handoff

- `tests/portfolio-builder-browser-checklist.md`
- `docs/implementation/acceptance-matrix.md`
"""


def _integration_lead(domain_type: str, score: dict[str, Any]) -> str:
    return f"""# Developer Team Integration Lead

Status: `{score['status']}`
Score: {score['final_score']}/{score['max_score']}
Domain: `{domain_type}`

## Decision

Developer Team remediation planning is ready when design contract and UI handoff exist. This does not mean implementation is complete.

## Integration Rules

- Keep implementation scoped to the post-build blockers.
- Do not add hosting, authentication, templates marketplace, or unrelated features.
- Every code change must map to a Developer Team task and a design-contract screen or template.
- QA Team must still provide browser screenshots before final product review can pass.
"""


def _acceptance_matrix(domain_type: str, tasks: list[dict[str, Any]], blockers: list[str]) -> str:
    rows = "\n".join(
        f"| {task['id']} | {task['owner']} | {task['title']} | {', '.join(task['acceptance_criteria'])} |"
        for task in tasks
    )
    return f"""# Developer Team Acceptance Matrix

Domain: `{domain_type}`

## Post-Build Blockers

{_blocker_lines(blockers)}

## Task Matrix

| Task | Owner | Title | Acceptance |
| --- | --- | --- | --- |
{rows}

## Gate Rule

Implementation can proceed as remediation, but final completion requires QA Team evidence, Review Team approval, and post-build product review pass.
"""


def _blocker_lines(blockers: list[str]) -> str:
    if not blockers:
        return "- No post-build blockers were found. Use design contract and PRD gates as source of truth."
    return "\n".join(f"- {blocker}" for blocker in blockers)
