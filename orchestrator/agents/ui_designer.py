from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database

from .base import AgentResult


@dataclass(frozen=True)
class UiDesignResult:
    user_flow_path: Path
    design_system_path: Path
    component_spec_path: Path


class UiDesignerAgent:
    id = "ui_designer"

    def __init__(self, db: Database | None = None):
        self.db = db

    def generate_design(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
    ) -> UiDesignResult:
        project_path = Path(project["path"])
        design_dir = project_path / "docs/design"
        design_dir.mkdir(parents=True, exist_ok=True)
        product_context = _load_product_context(project_path)
        domain_type = _domain_type(project["idea"], product_context)
        paths = {
            "user_flow_path": design_dir / "user-flow.md",
            "design_system_path": design_dir / "design-system.md",
            "component_spec_path": design_dir / "component-spec.md",
        }
        paths["user_flow_path"].write_text(
            _render_user_flow(project["idea"], domain_type, product_context), encoding="utf-8"
        )
        paths["design_system_path"].write_text(
            _render_design_system(domain_type, product_context), encoding="utf-8"
        )
        paths["component_spec_path"].write_text(
            _render_component_spec(domain_type, product_context), encoding="utf-8"
        )
        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in paths.values():
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="design",
                    path=str(path.relative_to(project_path)),
                    kind="markdown",
                    summary="UI Designer Agent artifact.",
                )
            EventBus(self.db).emit(
                event_type="design.draft_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="design",
                message=f"Generated UI design draft for {domain_type}.",
                payload={"domain_type": domain_type},
            )
        return UiDesignResult(**paths)

    def generate_design_result(self, *, project_path: Path, idea: str) -> AgentResult:
        project = {"id": "local", "idea": idea, "path": str(project_path)}
        result = self.generate_design(project=project, run_id=None)
        return AgentResult(
            status="completed",
            summary="Generated deterministic UI design artifacts from PRD context.",
            artifacts=[
                str(result.user_flow_path.relative_to(project_path)),
                str(result.design_system_path.relative_to(project_path)),
                str(result.component_spec_path.relative_to(project_path)),
            ],
        )


def _load_product_context(project_path: Path) -> str:
    relative_paths = [
        "docs/product/prd.md",
        "docs/product/product-fit.md",
        "docs/product/ux-patterns.md",
        "docs/product/evidence-chain.md",
        "docs/product/acceptance-criteria.md",
        "docs/product/benchmark-library/development-handoff.md",
        "docs/product/benchmark-library/quality-gates.md",
    ]
    sections: list[str] = []
    for relative_path in relative_paths:
        path = project_path / relative_path
        if path.exists():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                sections.append(f"## {relative_path}\n\n{text}")
    return "\n\n".join(sections)


def _domain_type(idea: str, context: str) -> str:
    lower = f"{idea}\n{context}".lower()
    if any(term in lower for term in ["portfolio", "作品集", "personal site", "personal website"]):
        return "portfolio"
    if any(term in lower for term in ["invoice", "freelance", "billable", "time tracking"]):
        return "freelance"
    if any(term in lower for term in ["expense", "transaction", "income", "支出", "预算"]):
        return "expense"
    return "generic"


def _render_user_flow(idea: str, domain_type: str, context: str) -> str:
    if domain_type == "portfolio":
        return f"""# User Flow

## Product Idea

{idea}

## Primary Flow

1. Start from profile content: avatar, name, title, bio, skills, contact links, and social links.
2. Add project proof: screenshot, project title, role, description, tags, project URL, and repository URL.
3. Choose a constrained theme preset and immediately see the portfolio preview update.
4. Review the full portfolio page across desktop and mobile preview states.
5. Resolve validation issues for missing required content, invalid image type, oversized upload, or broken links.
6. Export static HTML only when preview content and export content match.

## Required States

- Empty profile.
- Empty project gallery.
- Image uploading.
- Image preview.
- Replace image.
- Remove image.
- Invalid image type.
- Oversized image.
- Save success.
- Save failure.
- Theme changed.
- Preview loading.
- Export ready.
- Export blocked by validation.
- Export success.

## Design Principle

This should feel like a lightweight publishing studio, not an admin dashboard. Every edit should make the final portfolio page more credible.

## Source Context

The design must preserve the PRD decision that preview plus static export is the core product artifact.
"""
    if domain_type == "freelance":
        return """# User Flow

## Primary Flow

1. Capture a time entry with client, project, date, duration, billable status, hourly rate, and notes.
2. Review recent time entries by client and billing status.
3. Correct duration, rate, or billable status before invoice generation.
4. Open invoice draft preview for one client and date range.
5. Verify billable totals, excluded non-billable work, and line items.
6. Save or export the invoice-ready draft.

## Required States

- Empty time-entry list.
- Time entry form.
- Missing client.
- Invalid duration.
- Invalid rate.
- Save success.
- Save failure.
- Billable toggle changed.
- Client filter selected.
- Invoice draft loading.
- Invoice draft ready.
- Invoice draft blocked by missing billable entries.
- Invoice draft total mismatch.

## Design Principle

This should feel like a billing workbench for a solo freelancer, not a generic CRUD admin screen. The primary design value is confidence that billable work turns into a trustworthy invoice draft.
"""
    if domain_type == "expense":
        return """# User Flow

## Primary Flow

1. Add income or expense transaction.
2. Choose amount, date, type, category, and optional note.
3. Review recent transactions.
4. Open monthly cash-flow summary.
5. Edit or delete mistakes and verify totals update.

## Required States

- Empty transaction list.
- Form validation errors.
- Save success.
- Edit mode.
- Delete confirmation.
- Empty month.
- Monthly summary with income, expenses, and net total.
"""
    return """# User Flow

## Primary Flow

1. Capture the minimum required input.
2. Validate the input.
3. Review the generated or summarized output.
4. Edit source records when the output is wrong.
5. Export or hand off the useful artifact.

## Required States

- Empty.
- Editing.
- Validation error.
- Loading.
- Success.
- Failure.
"""


def _render_design_system(domain_type: str, context: str) -> str:
    if domain_type == "portfolio":
        return """# Design System

## Experience Direction

The interface should feel like a focused portfolio publishing studio: calm, precise, visual, and credible. Avoid generic SaaS dashboard styling, oversized marketing sections, and decorative gradients that do not help users inspect their work.

## Layout

- Desktop: editor and preview should be visible together when space allows.
- Mobile: use a segmented mode switch for Edit, Theme, Preview, and Export.
- Preview should keep a stable aspect ratio and not jump when images or project cards load.
- Export action belongs near preview readiness, not inside a generic settings area.

## Visual Hierarchy

- Primary visual focus: live portfolio preview.
- Secondary focus: currently edited profile or project form.
- Tertiary focus: theme presets, export status, and validation messages.

## Typography

- Use restrained text sizing inside controls and panels.
- Reserve large display type for the generated portfolio preview, not for the editor chrome.
- Letter spacing should remain neutral.

## Color And Style

- Use a neutral working surface for editor controls.
- Theme presets may vary colors, but the app shell should stay quiet.
- Avoid one-note palettes; theme previews should demonstrate meaningful contrast between typography, surfaces, links, and project cards.

## Accessibility And Responsiveness

- All upload, theme, preview, and export actions need keyboard focus states.
- Image controls need text labels and clear error copy.
- Desktop and mobile previews must preserve content hierarchy and contact-link visibility.

## Asset Integrity

- AI-generated visuals are placeholders only.
- Never generate fake headshots, client logos, project screenshots, credentials, or work history.
- Placeholder imagery must be labelled until replaced by user-owned proof.
"""
    if domain_type == "freelance":
        return """# Design System

## Experience Direction

The interface should be a quiet billing workbench for solo freelancers. It should reduce invoice prep anxiety by making time entries, billable status, rates, and invoice totals easy to inspect.

## Layout

- Desktop: time entry form, recent entries, and invoice draft preview should be reachable without deep navigation.
- Mobile: prioritize quick time entry first, then review and invoice draft as separate modes.
- Invoice draft preview should keep line items and totals stable while filters change.

## Visual Hierarchy

- Primary surface: invoice-ready total and billable line items.
- Secondary surface: time-entry form and correction controls.
- Tertiary surface: filters, non-billable notes, and export status.

## Typography

- Amounts, rates, durations, and totals must be easy to compare.
- Keep labels compact but explicit; avoid decorative headings in the work surface.

## Color And Style

- Use restrained status colors for billable, non-billable, draft, and error states.
- Avoid dashboard decoration that distracts from billing accuracy.

## Accessibility And Responsiveness

- Time entry can be completed by keyboard.
- Invoice totals remain readable on mobile.
- Error states appear next to the field that caused them.
"""
    if domain_type == "expense":
        return """# Design System

## Experience Direction

The interface should be quiet, utilitarian, and optimized for repeated transaction entry and monthly review.

## Layout

- Primary surface: transaction form and recent transaction list.
- Secondary surface: monthly summary with income, expenses, and net total.
- Filters should be compact and stable.

## Visual Hierarchy

- Make the active month and net total easy to scan.
- Keep category and transaction details dense but readable.
- Validation errors should appear next to the field that caused them.

## Accessibility And Responsiveness

- Forms must remain usable on mobile.
- Summary totals must not wrap into unreadable layouts.
- Keyboard users must be able to add and correct records quickly.
"""
    return """# Design System

## Experience Direction

The interface should prioritize the primary workflow and make the useful output easy to inspect.

## Layout

- Keep capture, review, and output surfaces clearly separated.
- Avoid decorative UI that hides the main workflow.

## States

- Empty, loading, validation, success, and failure states are mandatory.
"""


def _render_component_spec(domain_type: str, context: str) -> str:
    if domain_type == "portfolio":
        components = [
            ("Profile Editor", "Avatar upload, name, title, bio, skills, contact links, social links.", "empty, editing, invalid link, save success, save failure"),
            ("Project Card Editor", "Screenshot, title, role, description, tags, project URL, repository URL.", "empty, editing, reorder, duplicate, delete, validation error"),
            ("Upload Dropzone", "Accepts avatar and project screenshots with local preview.", "empty, uploading, preview, replace, remove, invalid type, oversized, failure"),
            ("Theme Selector", "Small set of constrained presets with real thumbnails or live mini previews.", "selected, hover, keyboard focus, disabled while preview updates"),
            ("Portfolio Preview", "Rendered representation of the final portfolio page.", "loading, desktop preview, mobile preview, content incomplete, ready"),
            ("Export Panel", "Static HTML export readiness and action.", "blocked by validation, ready, exporting, success, failure"),
        ]
    elif domain_type == "freelance":
        components = [
            ("Time Entry Form", "Client, project, date, duration, billable status, hourly rate, and notes.", "empty, missing client, invalid duration, invalid rate, save success, save failure"),
            ("Time Entry List", "Recent entries with edit/delete and billable indicators.", "empty, filtered, editing, deleting, billable toggled"),
            ("Client Filter", "Client and date-range filtering for invoice preparation.", "all, selected client, no billable entries"),
            ("Invoice Draft Preview", "Line items, billable total, excluded non-billable work, and export readiness.", "loading, ready, blocked, total mismatch, exported"),
            ("Billing Summary", "Hours, rate, subtotal, and invoice-ready total.", "empty, recalculated, mismatch warning"),
        ]
    elif domain_type == "expense":
        components = [
            ("Transaction Form", "Amount, date, type, category, optional note.", "empty, invalid amount, missing date, save success, save failure"),
            ("Transaction List", "Recent records with edit and delete actions.", "empty, filtered, editing, deleting"),
            ("Monthly Summary", "Income, expenses, net total, and category grouping.", "empty month, populated, updated after edit/delete"),
            ("Category Filter", "Compact category and month filters.", "all, selected, no results"),
        ]
    else:
        components = [
            ("Input Form", "Minimum required fields for the workflow.", "empty, invalid, saved"),
            ("Record List", "Source records or generated items.", "empty, populated, filtered"),
            ("Output Preview", "Useful user artifact.", "loading, ready, error"),
        ]
    lines = ["# Component Spec", "", "| Component | Responsibility | Required States |", "| --- | --- | --- |"]
    for name, responsibility, states in components:
        lines.append(f"| {name} | {responsibility} | {states} |")
    lines.extend(
        [
            "",
            "## QA Handoff",
            "",
            "- Each component state must map to at least one acceptance criterion or QA checklist item.",
            "- Reviewer should reject components that hide validation failures or make the final artifact hard to inspect.",
        ]
    )
    return "\n".join(lines) + "\n"
