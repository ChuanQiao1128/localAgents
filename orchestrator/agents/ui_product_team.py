from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


@dataclass(frozen=True)
class UiProductTeamResult:
    ux_flow_lead_path: Path
    visual_design_lead_path: Path
    asset_strategy_lead_path: Path
    visual_qa_lead_path: Path
    design_critic_path: Path
    reference_traceability_path: Path
    screen_spec_path: Path
    template_spec_path: Path
    lead_synthesis_path: Path
    dev_handoff_path: Path
    visual_qa_checklist_path: Path
    design_contract_json_path: Path
    contracts_json_path: Path
    score_json_path: Path


class UiProductTeamAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> UiProductTeamResult:
        project_path = Path(project["path"])
        design_dir = project_path / "docs/design"
        team_dir = design_dir / "ui-team"
        team_dir.mkdir(parents=True, exist_ok=True)
        context = _load_context(project_path, project.get("idea", ""))
        domain_type = _domain_type(context)
        blockers = _post_build_blockers(project_path)
        example_evidence = _example_visual_evidence(project_path)
        score = _score_payload(domain_type, blockers, example_evidence)

        result = UiProductTeamResult(
            ux_flow_lead_path=team_dir / "ux-flow-lead.md",
            visual_design_lead_path=team_dir / "visual-design-lead.md",
            asset_strategy_lead_path=team_dir / "asset-strategy-lead.md",
            visual_qa_lead_path=team_dir / "visual-qa-lead.md",
            design_critic_path=team_dir / "design-critic.md",
            reference_traceability_path=design_dir / "reference-to-design-traceability.md",
            screen_spec_path=design_dir / "screen-level-spec.md",
            template_spec_path=design_dir / "template-spec.md",
            lead_synthesis_path=team_dir / "lead-synthesis.md",
            dev_handoff_path=design_dir / "ui-team-dev-handoff.md",
            visual_qa_checklist_path=design_dir / "visual-qa-checklist.md",
            design_contract_json_path=design_dir / "design-contract.json",
            contracts_json_path=team_dir / "ui-team-contracts.json",
            score_json_path=team_dir / "ui-team-score.json",
        )

        result.ux_flow_lead_path.write_text(_ux_flow_lead(domain_type, blockers), encoding="utf-8")
        result.visual_design_lead_path.write_text(_visual_design_lead(domain_type, blockers, example_evidence), encoding="utf-8")
        result.asset_strategy_lead_path.write_text(_asset_strategy_lead(domain_type, blockers), encoding="utf-8")
        result.visual_qa_lead_path.write_text(_visual_qa_lead(domain_type, blockers, example_evidence), encoding="utf-8")
        result.design_critic_path.write_text(_design_critic(domain_type, blockers, example_evidence), encoding="utf-8")
        result.reference_traceability_path.write_text(_reference_traceability(domain_type, blockers, example_evidence), encoding="utf-8")
        result.screen_spec_path.write_text(_screen_spec(domain_type), encoding="utf-8")
        result.template_spec_path.write_text(_template_spec(domain_type), encoding="utf-8")
        result.lead_synthesis_path.write_text(_lead_synthesis(domain_type, blockers, score, example_evidence), encoding="utf-8")
        result.dev_handoff_path.write_text(_dev_handoff(domain_type, blockers, example_evidence), encoding="utf-8")
        result.visual_qa_checklist_path.write_text(_visual_qa_checklist(domain_type, example_evidence), encoding="utf-8")
        result.design_contract_json_path.write_text(
            json.dumps(_design_contract(domain_type, blockers, example_evidence), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result.contracts_json_path.write_text(
            json.dumps(_contracts(domain_type, blockers, example_evidence), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        result.score_json_path.write_text(json.dumps(score, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in [
                result.ux_flow_lead_path,
                result.visual_design_lead_path,
                result.asset_strategy_lead_path,
                result.visual_qa_lead_path,
                result.design_critic_path,
                result.reference_traceability_path,
                result.screen_spec_path,
                result.template_spec_path,
                result.lead_synthesis_path,
                result.dev_handoff_path,
                result.visual_qa_checklist_path,
                result.design_contract_json_path,
                result.contracts_json_path,
                result.score_json_path,
            ]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="design",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="UI Product Team artifact.",
                )
            EventBus(self.db).emit(
                event_type="design.ui_team_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="design",
                message=f"Generated UI Product Team package for {domain_type}.",
                payload={"domain_type": domain_type, "status": score["status"], "score": score["final_score"]},
            )

        return result


def _load_context(project_path: Path, idea: str) -> str:
    paths = [
        "docs/product/prd.md",
        "docs/product/product-fit.md",
        "docs/product/post-build-product-review.md",
        "docs/product/post-build-product-review.json",
        "docs/product/reference-products/index.md",
        "docs/product/example-references/top-examples.md",
        "docs/product/example-references/visual-critic.md",
        "docs/product/example-references/multimodal-critic.md",
        "docs/product/feature-patterns.md",
        "docs/product/ux-patterns.md",
        "docs/design/user-flow.md",
        "docs/design/design-system.md",
        "docs/design/component-spec.md",
        "docs/design/ui-team-plan.md",
        ".agent/tasks/downstream-remediation-tasks.json",
    ]
    sections = [idea]
    for relative_path in paths:
        path = project_path / relative_path
        if path.exists():
            sections.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(sections)


def _domain_type(context: str) -> str:
    lower = context.lower()
    if any(term in lower for term in ["portfolio", "personal website", "personal site", "作品集"]):
        return "portfolio"
    if any(term in lower for term in ["invoice", "freelance", "billable", "time tracking"]):
        return "freelance"
    if any(term in lower for term in ["expense", "transaction", "income", "支出", "预算"]):
        return "expense"
    return "generic"


def _post_build_blockers(project_path: Path) -> list[str]:
    path = project_path / "docs/product/post-build-product-review.json"
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    blockers = payload.get("blockers", []) if isinstance(payload, dict) else []
    return [str(item) for item in blockers if str(item).strip()]


def _example_visual_evidence(project_path: Path) -> dict[str, Any]:
    path = project_path / "docs/product/example-references/visual-critic.json"
    if not path.exists():
        return {
            "status": "missing",
            "screenshot_backed": 0,
            "example_count": 0,
            "covered_archetype_count": 0,
            "screenshot_backed_archetype_count": 0,
            "top_examples": [],
            "standards": [],
        }
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "status": "invalid",
            "screenshot_backed": 0,
            "example_count": 0,
            "covered_archetype_count": 0,
            "screenshot_backed_archetype_count": 0,
            "top_examples": [],
            "standards": [],
        }
    examples = payload.get("examples", []) if isinstance(payload, dict) else []
    coverage = payload.get("source_coverage", {}) if isinstance(payload, dict) else {}
    standards = payload.get("visual_standards", []) if isinstance(payload, dict) else []
    top_examples = []
    for item in examples[:6] if isinstance(examples, list) else []:
        if not isinstance(item, dict):
            continue
        top_examples.append(
            {
                "title": str(item.get("title") or ""),
                "source": str(item.get("source_name") or ""),
                "archetype": str(item.get("archetype") or ""),
                "evidence": str(item.get("evidence_level") or ""),
            }
        )
    return {
        "status": str(payload.get("status") or "unknown") if isinstance(payload, dict) else "unknown",
        "screenshot_backed": int(payload.get("screenshot_backed") or 0) if isinstance(payload, dict) else 0,
        "image_analyzed": int(payload.get("image_analyzed") or 0) if isinstance(payload, dict) else 0,
        "image_quality": payload.get("image_quality", {}) if isinstance(payload, dict) else {},
        "example_count": int(payload.get("example_count") or 0) if isinstance(payload, dict) else 0,
        "covered_archetype_count": int(coverage.get("covered_archetype_count") or 0) if isinstance(coverage, dict) else 0,
        "screenshot_backed_archetype_count": int(coverage.get("screenshot_backed_archetype_count") or 0) if isinstance(coverage, dict) else 0,
        "top_examples": top_examples,
        "standards": standards[:6] if isinstance(standards, list) else [],
    }


def _score_payload(domain_type: str, blockers: list[str], example_evidence: dict[str, Any]) -> dict[str, Any]:
    screenshot_backed = int(example_evidence.get("screenshot_backed") or 0)
    screenshot_archetypes = int(example_evidence.get("screenshot_backed_archetype_count") or 0)
    visual_qa_evidence = 12 if screenshot_backed >= 5 and screenshot_archetypes >= 3 else 6 if screenshot_backed else 0
    dimensions = {
        "role_coverage": 20,
        "blocker_traceability": 20 if blockers else 14,
        "reference_traceability": 16 if domain_type == "portfolio" else 10,
        "screen_specificity": 18 if domain_type == "portfolio" else 12,
        "template_specificity": 18 if domain_type == "portfolio" else 8,
        "design_contract_readiness": 18,
        "asset_strategy": 16 if domain_type == "portfolio" else 10,
        "dev_handoff_readiness": 16,
        "visual_qa_plan": 12,
        "visual_qa_evidence": visual_qa_evidence,
    }
    final_score = sum(dimensions.values())
    max_score = 180
    if final_score >= 135 and dimensions["visual_qa_evidence"] == 0:
        status = "ready_for_dev_team_evidence_pending"
    elif final_score >= 135:
        status = "ready_for_dev_team"
    else:
        status = "needs_revision"
    return {
        "domain_type": domain_type,
        "final_score": final_score,
        "max_score": max_score,
        "status": status,
        "dimensions": dimensions,
        "missing_evidence": _missing_visual_evidence(example_evidence),
        "gate_meaning": "This approves the UI team handoff for remediation work; it does not approve the product as complete.",
    }


def _missing_visual_evidence(example_evidence: dict[str, Any]) -> list[str]:
    missing = []
    if int(example_evidence.get("screenshot_backed") or 0) == 0:
        missing.append("No reference example screenshots have been captured yet.")
    if int(example_evidence.get("screenshot_backed_archetype_count") or 0) < 3:
        missing.append("Reference screenshots do not yet cover at least three visual archetypes.")
    missing.append("No preview/export visual comparison artifact exists yet.")
    return missing


def _ux_flow_lead(domain_type: str, blockers: list[str]) -> str:
    if domain_type == "portfolio":
        return f"""# UX Flow Lead

## Mission

Turn the portfolio builder from a form demo into a guided publishing workflow that helps users create credible proof.

## Blockers Addressed

{_blocker_lines(blockers)}

## Proposed Flow

1. Choose portfolio intent: job search, freelance sales, founder profile, or creative showcase.
2. Pick a template strategy: Case Study, Visual Gallery, Builder Resume, or Proof-First Landing Page.
3. Complete profile essentials: photo or initials, positioning line, bio, skills, contact links.
4. Add project proof with structured prompts:
   - Problem
   - My role
   - Process
   - Outcome
   - Metrics or evidence
   - Links and repository
5. Add or generate safe visual support:
   - User-owned screenshot
   - Neutral generated background
   - Placeholder marked as not real work
6. Review quality checklist before export.
7. Export only when preview, accessibility, and proof quality checks pass.

## Screen Model

- Intent and template picker
- Profile editor
- Guided project case-study editor
- Asset panel
- Live preview
- Quality checklist
- Export panel

## Required States

- Empty portfolio
- Template selected
- Incomplete case study
- Weak outcome warning
- Missing screenshot warning
- Placeholder asset warning
- Preview ready
- Export blocked
- Export ready
"""
    return f"""# UX Flow Lead

## Mission

Turn the product from a generic shell into a guided {domain_type} workflow.

## Blockers Addressed

{_blocker_lines(blockers)}

## Required Flow

1. Select intent.
2. Capture core inputs.
3. Validate quality.
4. Preview useful output.
5. Export or save only when checks pass.
"""


def _visual_design_lead(domain_type: str, blockers: list[str], example_evidence: dict[str, Any]) -> str:
    if domain_type == "portfolio":
        return f"""# Visual Design Lead

## Mission

Translate market references into concrete portfolio templates instead of generic panels.

## Blockers Addressed

{_blocker_lines(blockers)}

## Screenshot-Backed Reference Evidence

{_example_evidence_lines(example_evidence)}

## Template Directions

### Editorial Case Study

- Best for product designers and PMs.
- Large project narrative, restrained typography, proof-first hierarchy.
- Preview focuses on problem, process, outcome, and metrics.

### Visual Gallery

- Best for brand, UI, and creative work.
- Image grid with strong captions and role labels.
- Asset quality warnings appear before export.

### Builder Resume

- Best for developers and technical founders.
- Project cards emphasize stack, repository, live demo, outcome, and maintainership.
- Code links are visible but not louder than project value.

### Proof-First Landing Page

- Best for freelancers.
- Hero positioning, selected results, testimonials placeholder, contact CTA.
- No fake testimonials or fake client logos.

## Visual Rules

- Editor chrome stays quiet; generated portfolio preview gets the visual emphasis.
- Theme choices must use distinct layout and hierarchy, not only color changes.
- All templates need desktop and mobile preview states.
- No generated headshots, fake logos, fake client names, or fabricated work screenshots.
"""
    return f"""# Visual Design Lead

## Mission

Create concrete visual directions for the {domain_type} workflow.

## Blockers Addressed

{_blocker_lines(blockers)}

## Rules

- Avoid generic dashboard layout.
- Make the useful output the visual center.
- Show states and quality warnings before export.
"""


def _asset_strategy_lead(domain_type: str, blockers: list[str]) -> str:
    if domain_type == "portfolio":
        return f"""# Asset Strategy Lead

## Mission

Define media, generated imagery, accessibility, and proof-safety rules.

## Blockers Addressed

{_blocker_lines(blockers)}

## Upload Lifecycle

- Empty
- Selecting
- Preview ready
- Replace
- Remove
- Invalid type
- Oversized
- Failed read
- Missing alt text
- Placeholder still active

## Image Rules

- Avatar can be uploaded, replaced, or removed.
- Project screenshots can be uploaded, replaced, or removed.
- Each project image requires alt text unless marked decorative.
- Crop guidance should prefer 16:10 for project images and square for avatar.

## AI Image Rules

- AI image generation is allowed only for neutral backgrounds, abstract section art, or template placeholders.
- AI must not generate fake headshots, fake product screenshots, fake client logos, fake credentials, or fake testimonials.
- Generated placeholders must be labelled until replaced with user-owned proof.

## Export Rules

- Exported HTML must preserve asset labels, alt text, and placeholder warnings.
- Export must be blocked if required proof fields are missing.
"""
    return f"""# Asset Strategy Lead

## Mission

Define asset and output integrity rules for {domain_type}.

## Blockers Addressed

{_blocker_lines(blockers)}

## Rules

- User-owned evidence must be distinct from generated placeholders.
- Exported output must preserve labels and warnings.
- Accessibility metadata is required for meaningful images.
"""


def _visual_qa_lead(domain_type: str, blockers: list[str], example_evidence: dict[str, Any]) -> str:
    if domain_type == "portfolio":
        return f"""# Visual QA Lead

## Mission

Make visual QA a required gate before Reviewer approval.

## Blockers Addressed

{_blocker_lines(blockers)}

## Reference Screenshot Baseline

{_example_evidence_lines(example_evidence)}

## Required Browser Evidence

- Desktop screenshot: 1440x1000
- Tablet screenshot: 1024x768
- Mobile screenshot: 390x844
- Exported HTML screenshot at desktop and mobile
- Preview/export comparison notes

## Required Interaction Checks

- Add profile
- Upload, replace, and remove avatar
- Add project case-study fields
- Upload, replace, and remove project screenshot
- Switch templates and themes
- Trigger weak-proof warnings
- Save and reload
- Export and open exported HTML

## Rejection Rules

- Reject if preview and export differ materially.
- Reject if visual hierarchy makes project proof hard to inspect.
- Reject if generated placeholders can be mistaken for real work.
- Reject if mobile export hides contact links or project outcomes.
"""
    return f"""# Visual QA Lead

## Mission

Define browser and visual QA for {domain_type}.

## Blockers Addressed

{_blocker_lines(blockers)}

## Required Evidence

- Desktop screenshot
- Mobile screenshot
- Preview/export comparison
- Interaction checklist
"""


def _design_critic(domain_type: str, blockers: list[str], example_evidence: dict[str, Any]) -> str:
    if domain_type == "portfolio":
        return f"""# UI Team Design Critic

## Role

Challenge the UI Team output before Dev Team implements it.

## Blockers Rechecked

{_blocker_lines(blockers)}

## Reference Evidence Rechecked

{_example_evidence_lines(example_evidence)}

## Critical Findings

- Template names are not enough; each template must specify layout, fields, preview behavior, export behavior, and rejection rules.
- Research references must be traceable to concrete screen decisions, otherwise the product can still become a generic form builder.
- The UX flow must include quality coaching and weak-proof warnings, not only required fields.
- Visual QA is still evidence-pending until screenshots exist.
- Dev Team must not treat AI image support as permission to fabricate user proof.

## Pass Conditions

- Reference-to-design traceability exists.
- Screen-level spec exists.
- Template-level spec exists.
- Design contract JSON exists.
- Visual QA evidence gap is explicitly carried to QA Team.
"""
    return f"""# UI Team Design Critic

## Role

Challenge the UI Team output before Dev Team implements it.

## Blockers Rechecked

{_blocker_lines(blockers)}

## Critical Findings

- The {domain_type} workflow still needs screen-level specificity.
- Dev handoff should include field, state, and output contracts.
- Visual QA remains evidence-pending until screenshots exist.
"""


def _reference_traceability(domain_type: str, blockers: list[str], example_evidence: dict[str, Any]) -> str:
    if domain_type == "portfolio":
        return f"""# Reference-To-Design Traceability

## Purpose

Make research visible in the generated product so the next implementation does not collapse into a generic form.

## Example Visual Critic Evidence

{_example_evidence_lines(example_evidence)}

| Reference Pattern | Product Decision | Screen Or Template | Adopt | Reject |
| --- | --- | --- | --- | --- |
| Webflow/Framer-style template choice before editing | Add intent and template picker before profile editing | Intent and Template Picker | Guided starting point and live preview | Complex hosting, domains, CMS, marketplace |
| Behance/Dribbble-style visual proof | Add Visual Gallery template with image-forward cards | Visual Gallery | Strong screenshots and captions | Likes, social feed, fake popularity |
| Case-study portfolio norms | Add structured prompts for problem, role, process, outcome, metrics | Editorial Case Study | Proof-first story structure | Long essay editor without quality guidance |
| Developer portfolio conventions | Add stack, repository, live demo, maintainership and outcome fields | Builder Resume | Technical credibility | Resume-only layout with no project proof |
| Freelancer landing page patterns | Add Proof-First Landing Page template with positioning and contact CTA | Proof-First Landing Page | Conversion-focused preview | Fake testimonials or fake client logos |

## Blockers Covered

{_blocker_lines(blockers)}
"""
    return f"""# Reference-To-Design Traceability

## Purpose

Connect research patterns to concrete {domain_type} screens.

| Reference Pattern | Product Decision | Adopt | Reject |
| --- | --- | --- | --- |
| Domain workflow tools | Guided primary workflow | Clear input-to-output path | Generic dashboard shell |
| Output preview tools | Make final artifact inspectable | Preview-first review | Hidden export-only output |
"""


def _screen_spec(domain_type: str) -> str:
    if domain_type == "portfolio":
        return """# Screen-Level Spec

## 1. Intent And Template Picker

- Purpose: prevent blank-canvas weakness.
- Fields: portfolio intent, template strategy, primary audience.
- States: no intent, selected, recommended template, changed after content exists.
- Desktop: left rail selector with live preview summary.
- Mobile: first step in a segmented flow.

## 2. Profile Editor

- Fields: avatar, initials fallback, name, positioning line, bio, skills, contact links, social links.
- States: empty, incomplete, valid, invalid link, avatar preview, avatar removed.
- Warnings: missing positioning line, no contact path, fake/generated headshot warning.

## 3. Guided Project Case-Study Editor

- Fields: title, role, problem, process, outcome, metrics, proof notes, tags, links, repository, screenshot, alt text.
- States: empty, weak proof, missing outcome, missing alt text, screenshot preview, screenshot removed, ready.
- Quality coaching: prompt user to add outcome and evidence before export.

## 4. Asset Panel

- Fields: avatar asset, project screenshot asset, generated placeholder flag, alt text, decorative toggle.
- States: empty, uploaded, replaced, removed, invalid type, oversized, placeholder active.
- Rejection: generated placeholder cannot be treated as real work proof.

## 5. Live Preview

- Purpose: make the final artifact inspectable while editing.
- States: desktop preview, mobile preview, incomplete, ready, export blocked.
- Behavior: template changes update layout without deleting content.

## 6. Quality Checklist

- Checks: profile complete, at least one credible project, outcome present, asset labels complete, contact path present.
- States: blocking, warning, passed.

## 7. Export Panel

- Fields: export filename, include placeholder warnings, export action.
- States: blocked, ready, exporting, exported, export failed.
- Rule: exported HTML must match preview content and template.
"""
    return f"""# Screen-Level Spec

## Primary Workflow Screen

- Capture core {domain_type} input.
- Validate required fields.
- Preview useful output.
- Block export when quality checks fail.
"""


def _template_spec(domain_type: str) -> str:
    if domain_type == "portfolio":
        return """# Template Spec

## Editorial Case Study

- Best for: product designers, PMs, UX researchers.
- Required fields: problem, role, process, outcome, metrics, proof image, contact.
- Layout: narrow editorial body, large project title, metrics strip, proof image after outcome.
- Export behavior: one-page case-study sequence.
- Reject if: project lacks outcome or evidence.

## Visual Gallery

- Best for: visual designers, brand designers, UI designers.
- Required fields: screenshot, caption, role, tags, project link, alt text.
- Layout: image-forward grid, captions visible without hover, selected project detail.
- Export behavior: gallery with accessible image labels.
- Reject if: images are placeholders without labels.

## Builder Resume

- Best for: developers, technical founders.
- Required fields: stack, repository, live demo, maintainership, outcome, project summary.
- Layout: compact profile header, project cards with code and result details.
- Export behavior: technical credibility page.
- Reject if: repository/demo links overpower outcome.

## Proof-First Landing Page

- Best for: freelancers, consultants.
- Required fields: positioning, selected proof, service area, contact CTA, optional testimonial placeholder.
- Layout: hero positioning, proof cards, contact section.
- Export behavior: conversion-focused one-page site.
- Reject if: testimonial or client logo is fake.
"""
    return f"""# Template Spec

## Primary Output Template

- Best for: focused {domain_type} workflow.
- Required fields: core input, proof, output summary.
- Reject if: output is only a generic record list.
"""


def _lead_synthesis(domain_type: str, blockers: list[str], score: dict[str, Any], example_evidence: dict[str, Any]) -> str:
    return f"""# Lead UI Product Synthesis

Status: `{score['status']}`
Score: {score['final_score']}/{score['max_score']}
Domain: `{domain_type}`

## Decision

The UI Product Team handoff is ready for Dev Team remediation. This is not product approval; it is a better design contract for the next implementation pass.

## Blocker Traceability

{_blocker_lines(blockers)}

## Team Outputs

- UX Flow Lead defines guided workflow and states.
- Visual Design Lead defines templates and visual rules.
- Asset Strategy Lead defines upload, generated-image, proof, and accessibility rules.
- Visual QA Lead defines browser evidence and rejection rules.
- Design Critic challenges weak handoffs before Dev Team starts.
- Reference traceability, screen spec, template spec, and design contract JSON make the handoff implementable.

## Evidence Gap

{_missing_evidence_lines(score.get("missing_evidence", []))}

## Reference Visual Evidence

{_example_evidence_lines(example_evidence)}

## Handoff Rule

Developer Team must not implement unrelated features. Every code change should trace to one blocker, one UI team role output, and one acceptance check.
"""


def _dev_handoff(domain_type: str, blockers: list[str], example_evidence: dict[str, Any]) -> str:
    if domain_type == "portfolio":
        return f"""# UI Team Dev Handoff

## Implementation Priorities

1. Add template selection: Editorial Case Study, Visual Gallery, Builder Resume, Proof-First Landing Page.
2. Add guided project case-study fields: problem, role, process, outcome, metrics, proof notes.
3. Add quality checklist and weak-proof warnings before export.
4. Add screenshot remove and alt-text fields.
5. Add safe AI image placeholder rules without fake proof.
6. Add browser-visible states for export blocked and export ready.

## Acceptance Criteria

- A user can create a portfolio that reads as a case-study artifact, not just a profile form.
- Research-informed template options are visible in the product UI.
- At least three screenshot-backed reference archetypes are reflected in template, preview, and quality-check decisions.
- Every image has remove/replace behavior.
- Project images require alt text or decorative intent.
- Exported HTML includes the same template, case-study content, asset warnings, and contact links as preview.

## Blockers Covered

{_blocker_lines(blockers)}

## Reference Evidence To Use

{_example_evidence_lines(example_evidence)}
"""
    return f"""# UI Team Dev Handoff

## Implementation Priorities

- Replace generic shell with guided {domain_type} workflow.
- Add output preview and quality checks.
- Preserve accessibility and export states.

## Blockers Covered

{_blocker_lines(blockers)}
"""


def _visual_qa_checklist(domain_type: str, example_evidence: dict[str, Any]) -> str:
    return f"""# Visual QA Checklist

Domain: `{domain_type}`

## Reference Baseline

{_example_evidence_lines(example_evidence)}

## Browser Viewports

- Desktop: 1440x1000
- Tablet: 1024x768
- Mobile: 390x844

## Checks

- Primary product value is visible in the first viewport.
- Editor controls do not visually dominate the final artifact.
- Empty, incomplete, warning, ready, and exported states are visible.
- Preview and exported HTML match content, layout intent, and asset labels.
- Text does not overflow buttons, cards, inputs, or preview sections.
- Mobile layout preserves the primary workflow and contact/action surfaces.
- Generated placeholder media cannot be mistaken for real user proof.
"""


def _design_contract(domain_type: str, blockers: list[str], example_evidence: dict[str, Any]) -> dict[str, Any]:
    if domain_type == "portfolio":
        return {
            "version": 1,
            "domain_type": "portfolio",
            "gate": "ui_team_design_contract",
            "gate_meaning": "Dev Team may implement remediation from this contract; final product still needs QA, review, and post-build product review.",
            "blockers": blockers,
            "reference_visual_evidence": example_evidence,
            "screens": [
                {
                    "id": "intent_template_picker",
                    "purpose": "Prevent blank-canvas weakness and make research-informed templates visible.",
                    "fields": ["portfolio_intent", "template_strategy", "primary_audience"],
                    "states": ["no_intent", "selected", "recommended_template", "changed_after_content_exists"],
                },
                {
                    "id": "profile_editor",
                    "purpose": "Capture credible identity and contact path.",
                    "fields": ["avatar", "initials_fallback", "name", "positioning_line", "bio", "skills", "contact_links", "social_links"],
                    "states": ["empty", "incomplete", "valid", "invalid_link", "avatar_preview", "avatar_removed"],
                },
                {
                    "id": "guided_project_case_study_editor",
                    "purpose": "Coach users into outcome-driven project proof.",
                    "fields": ["title", "role", "problem", "process", "outcome", "metrics", "proof_notes", "tags", "links", "repository", "screenshot", "alt_text"],
                    "states": ["empty", "weak_proof", "missing_outcome", "missing_alt_text", "screenshot_preview", "screenshot_removed", "ready"],
                },
                {
                    "id": "live_preview",
                    "purpose": "Show the final artifact while editing.",
                    "fields": ["template", "desktop_preview", "mobile_preview", "quality_status"],
                    "states": ["incomplete", "desktop_preview", "mobile_preview", "ready", "export_blocked"],
                },
                {
                    "id": "export_panel",
                    "purpose": "Export only when quality and asset rules pass.",
                    "fields": ["filename", "include_placeholder_warnings", "export_action"],
                    "states": ["blocked", "ready", "exporting", "exported", "failed"],
                },
            ],
            "templates": [
                {
                    "id": "editorial_case_study",
                    "required_fields": ["problem", "role", "process", "outcome", "metrics", "proof_image"],
                    "reject_if": ["missing_outcome", "missing_evidence"],
                },
                {
                    "id": "visual_gallery",
                    "required_fields": ["screenshot", "caption", "role", "tags", "project_link", "alt_text"],
                    "reject_if": ["placeholder_without_label", "missing_alt_text"],
                },
                {
                    "id": "builder_resume",
                    "required_fields": ["stack", "repository", "live_demo", "maintainership", "outcome"],
                    "reject_if": ["links_without_outcome"],
                },
                {
                    "id": "proof_first_landing_page",
                    "required_fields": ["positioning", "selected_proof", "service_area", "contact_cta"],
                    "reject_if": ["fake_testimonial", "fake_client_logo"],
                },
            ],
            "tokens": {
                "layout": {"editor_min_width": 340, "preview_min_width": 520, "project_image_ratio": "16:10"},
                "states": ["empty", "incomplete", "warning", "ready", "blocked", "exported"],
                "accessibility": ["focus_visible", "alt_text_required", "keyboard_reachable_export"],
            },
            "handoff_checks": [
                "Every implementation change traces to a post-build blocker.",
                "Every template has preview and export behavior.",
                "Every meaningful image has alt text or decorative intent.",
                "Generated placeholders are labelled and preserved in export.",
            ],
        }
    return {
        "version": 1,
        "domain_type": domain_type,
        "gate": "ui_team_design_contract",
        "screens": ["primary_workflow", "output_preview", "export_or_save"],
        "handoff_checks": ["Workflow is domain-specific", "Output is inspectable", "Quality checks block export"],
    }


def _contracts(domain_type: str, blockers: list[str], example_evidence: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": 1,
        "domain_type": domain_type,
        "gate": "ui_team_handoff",
        "gate_meaning": "Approves design remediation handoff to Dev Team; does not approve final product.",
        "blockers": blockers,
        "reference_visual_evidence": example_evidence,
        "roles": [
            {
                "id": "ux_flow_lead",
                "owns": ["docs/design/ui-team/ux-flow-lead.md"],
                "must_answer": ["What is the guided workflow?", "Which states block export?", "How does the flow fix product-review blockers?"],
                "handoff_to": ["visual_design_lead", "developer_team"],
            },
            {
                "id": "visual_design_lead",
                "owns": ["docs/design/ui-team/visual-design-lead.md"],
                "must_answer": ["Which templates exist?", "How are references visible?", "What visual rules prevent generic UI?"],
                "handoff_to": ["asset_strategy_lead", "developer_team", "visual_qa_lead"],
            },
            {
                "id": "asset_strategy_lead",
                "owns": ["docs/design/ui-team/asset-strategy-lead.md"],
                "must_answer": ["What can be generated?", "What must be user-owned proof?", "How are alt text and remove/replace handled?"],
                "handoff_to": ["developer_team", "qa_team"],
            },
            {
                "id": "visual_qa_lead",
                "owns": ["docs/design/ui-team/visual-qa-lead.md", "docs/design/visual-qa-checklist.md"],
                "must_answer": ["Which screenshots are required?", "How is preview/export fidelity checked?", "What rejects the build?"],
                "handoff_to": ["qa_team", "review_team"],
            },
            {
                "id": "design_critic",
                "owns": ["docs/design/ui-team/design-critic.md"],
                "must_answer": ["What is too generic?", "Where is evidence missing?", "What should block Dev Team?"],
                "handoff_to": ["lead_ui_product"],
            },
            {
                "id": "lead_ui_product",
                "owns": [
                    "docs/design/ui-team/lead-synthesis.md",
                    "docs/design/ui-team-dev-handoff.md",
                    "docs/design/reference-to-design-traceability.md",
                    "docs/design/screen-level-spec.md",
                    "docs/design/template-spec.md",
                    "docs/design/design-contract.json",
                ],
                "must_answer": ["Is the handoff coherent?", "Can Dev Team implement without inventing product strategy?"],
                "handoff_to": ["developer_team"],
            },
        ],
    }


def _example_evidence_lines(example_evidence: dict[str, Any]) -> str:
    if not example_evidence or example_evidence.get("status") in {"missing", "invalid"}:
        return "- No example visual critic evidence is available yet."
    lines = [
        f"- Status: `{example_evidence.get('status')}`.",
        f"- Screenshot-backed examples: {example_evidence.get('screenshot_backed', 0)}/{example_evidence.get('example_count', 0)}.",
        f"- Image-analyzed screenshots: {example_evidence.get('image_analyzed', 0)}.",
        f"- Screenshot-backed archetypes: {example_evidence.get('screenshot_backed_archetype_count', 0)}/{example_evidence.get('covered_archetype_count', 0)}.",
    ]
    image_quality = example_evidence.get("image_quality", {})
    if isinstance(image_quality, dict) and image_quality:
        lines.append(
            f"- Pixel quality: average {image_quality.get('average_score', 0)}, "
            f"strong {image_quality.get('strong_pixel_evidence', 0)}, weak {image_quality.get('weak_pixel_evidence', 0)}."
        )
    for item in example_evidence.get("top_examples", [])[:5]:
        if not isinstance(item, dict):
            continue
        lines.append(
            f"- {item.get('source')}: {item.get('title')} ({item.get('archetype')}, {item.get('evidence')})."
        )
    for standard in example_evidence.get("standards", [])[:3]:
        if not isinstance(standard, dict):
            continue
        lines.append(f"- Standard `{standard.get('id')}`: {standard.get('standard')}")
    return "\n".join(lines)


def _missing_evidence_lines(items: Any) -> str:
    if not items:
        return "- No current evidence gaps."
    if not isinstance(items, list):
        return f"- {items}"
    return "\n".join(f"- {item}" for item in items)


def _blocker_lines(blockers: list[str]) -> str:
    if not blockers:
        return "- No post-build blockers were found. Use PRD/design gates as source of truth."
    return "\n".join(f"- {blocker}" for blocker in blockers)
