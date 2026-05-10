from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from orchestrator.agents.prd_draft import load_research_sources
from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


@dataclass(frozen=True)
class PrdOption:
    id: str
    name: str
    pm_role: str
    thesis: str
    target_users: list[str]
    core_problem: str
    mvp_features: list[str]
    differentiator: str
    non_goals: list[str]
    risks: list[str]
    complexity: str
    confidence: float
    source_refs: list[str]


@dataclass(frozen=True)
class PrdOptionsResult:
    options: list[PrdOption]
    recommended_option_id: str
    options_json_path: Path
    options_md_path: Path
    review_md_path: Path


class PrdOptionsAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def generate(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
    ) -> PrdOptionsResult:
        project_path = Path(project["path"])
        sources = load_research_sources(project_path, run_id)
        options = build_options(project["idea"], sources)
        recommended = choose_recommended_option(options)
        output_dir = project_path / ".agent/artifacts/prd_options"
        if run_id:
            output_dir = output_dir / run_id
        output_dir.mkdir(parents=True, exist_ok=True)
        options_json_path = output_dir / "options.json"
        options_md_path = project_path / "docs/product/options.md"
        review_md_path = project_path / "docs/product/pm-review.md"
        options_md_path.parent.mkdir(parents=True, exist_ok=True)
        options_json_path.write_text(
            json.dumps(
                {
                    "recommended_option_id": recommended.id,
                    "options": [asdict(option) for option in options],
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        options_md_path.write_text(render_options_markdown(project["idea"], options), encoding="utf-8")
        review_md_path.write_text(render_pm_review_markdown(options, recommended), encoding="utf-8")
        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path, kind, summary in [
                (options_json_path, "json", "Structured PRD options from multiple PM perspectives."),
                (options_md_path, "markdown", "Human-readable PRD options."),
                (review_md_path, "markdown", "Lead PM review and recommendation."),
            ]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="prd",
                    path=str(path.relative_to(project_path)),
                    kind=kind,
                    summary=summary,
                )
            EventBus(self.db).emit(
                event_type="prd.options_generated",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message=f"Generated {len(options)} PRD options.",
                payload={"recommended_option_id": recommended.id},
            )
        return PrdOptionsResult(
            options=options,
            recommended_option_id=recommended.id,
            options_json_path=options_json_path,
            options_md_path=options_md_path,
            review_md_path=review_md_path,
        )

    def select(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
        option_id: str,
        notes: str = "",
    ) -> Path:
        project_path = Path(project["path"])
        option = load_option(project_path, run_id, option_id)
        decision_path = project_path / "docs/product/decision.md"
        decision_path.parent.mkdir(parents=True, exist_ok=True)
        decision_path.write_text(render_decision_markdown(option, notes), encoding="utf-8")
        if self.db and run_id:
            ArtifactStore(self.db).register(
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                path="docs/product/decision.md",
                kind="markdown",
                summary=f"Selected PRD option {option.id}.",
            )
            EventBus(self.db).emit(
                event_type="prd.option_selected",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message=f"Selected PRD option {option.id}: {option.name}.",
                payload={"option_id": option.id, "notes": notes},
            )
        return decision_path


def build_options(idea: str, sources: list[dict[str, Any]]) -> list[PrdOption]:
    source_refs = _top_source_refs(sources)
    domain = _domain_type(idea)
    if domain == "portfolio":
        return _portfolio_options(source_refs)
    if domain == "freelance":
        return _freelance_options(source_refs)
    if domain == "expense":
        return _expense_options(source_refs)
    return _generic_options(source_refs)


def choose_recommended_option(options: list[PrdOption]) -> PrdOption:
    return sorted(options, key=lambda option: (option.confidence, _complexity_score(option.complexity)), reverse=True)[0]


def load_selected_option(project_path: Path, run_id: str | None) -> PrdOption | None:
    decision_path = project_path / "docs/product/decision.md"
    if not decision_path.exists():
        return None
    text = decision_path.read_text(encoding="utf-8")
    for line in text.splitlines():
        if line.startswith("Selected option:"):
            option_id = line.split(":", 1)[1].strip()
            return load_option(project_path, run_id, option_id)
    return None


def load_option(project_path: Path, run_id: str | None, option_id: str) -> PrdOption:
    options_path = _options_json_path(project_path, run_id)
    if not options_path.exists():
        raise FileNotFoundError(f"PRD options not found: {options_path}")
    payload = json.loads(options_path.read_text(encoding="utf-8"))
    for raw in payload.get("options", []):
        if raw.get("id") == option_id:
            return PrdOption(**raw)
    raise ValueError(f"Unknown PRD option: {option_id}")


def render_options_markdown(idea: str, options: list[PrdOption]) -> str:
    lines = ["# Product Options", "", f"Project idea: {idea}", ""]
    for option in options:
        lines.extend(
            [
                f"## {option.id}: {option.name}",
                "",
                f"PM role: {option.pm_role}",
                "",
                f"Thesis: {option.thesis}",
                "",
                "### Target Users",
                "",
                *_bullets(option.target_users),
                "",
                "### MVP Features",
                "",
                *_bullets(option.mvp_features),
                "",
                f"Complexity: {option.complexity}",
                f"Confidence: {option.confidence:.2f}",
                f"Sources: {', '.join(option.source_refs) or 'Assumption'}",
                "",
            ]
        )
    return "\n".join(lines)


def render_pm_review_markdown(options: list[PrdOption], recommended: PrdOption) -> str:
    lines = [
        "# PM Review",
        "",
        f"Recommended option: {recommended.id}",
        "",
        "## Recommendation Rationale",
        "",
        f"- {recommended.name} best balances user clarity, scope control, and confidence.",
        f"- Complexity is {recommended.complexity}; confidence is {recommended.confidence:.2f}.",
        "- You should still explicitly select an option before generating the final PRD.",
        "",
        "## Options",
        "",
    ]
    for option in options:
        lines.append(f"- {option.id}: {option.name} ({option.complexity}, confidence {option.confidence:.2f})")
    lines.extend(["", "## Next Step", "", "`./agent-studio prd select <option-id>`"])
    return "\n".join(lines)


def render_decision_markdown(option: PrdOption, notes: str = "") -> str:
    notes_block = notes.strip() or "No additional user notes."
    return f"""# Product Decision

Selected option: {option.id}

## Option Name

{option.name}

## Thesis

{option.thesis}

## User Notes

{notes_block}
"""


def _options_json_path(project_path: Path, run_id: str | None) -> Path:
    if run_id:
        return project_path / ".agent/artifacts/prd_options" / run_id / "options.json"
    candidates = sorted((project_path / ".agent/artifacts/prd_options").glob("*/options.json"), reverse=True)
    if candidates:
        return candidates[0]
    return project_path / ".agent/artifacts/prd_options/options.json"


def _top_source_refs(sources: list[dict[str, Any]]) -> list[str]:
    top = sorted(sources, key=lambda source: float(source.get("relevance") or 0.0), reverse=True)[:5]
    return [f"[{source.get('id', f'S{index + 1}')}]" for index, source in enumerate(top)]


def _domain_type(idea: str) -> str:
    lower = idea.lower()
    if any(
        term in lower
        for term in ["portfolio", "作品集", "个人网站", "个人主页", "personal site", "personal website"]
    ):
        return "portfolio"
    if any(term in lower for term in ["invoice", "发票", "time tracking", "时间追踪", "freelance", "自由职业"]):
        return "freelance"
    if any(term in lower for term in ["expense", "记账", "收入", "支出", "finance", "预算"]):
        return "expense"
    return "generic"


def _portfolio_options(source_refs: list[str]) -> list[PrdOption]:
    refs = source_refs or ["Assumption"]
    return [
        PrdOption(
            id="option-a",
            name="Profile Page MVP",
            pm_role="Market PM",
            thesis="Ship the fastest path from profile content to one polished personal page.",
            target_users=["Students and early-career creators", "Developers making a simple personal site"],
            core_problem="Users need a faster way to create one credible portfolio page without hand-coding layout.",
            mvp_features=[
                "Profile editor with avatar upload",
                "Project CRUD with screenshots and links",
                "Single portfolio preview",
                "Local save",
            ],
            differentiator="A tiny local portfolio builder that avoids hosting and account setup.",
            non_goals=["Multiple themes", "Static export", "CMS-style publishing", "AI writing assistance"],
            risks=["May be too small because users expect preview plus export from a portfolio builder."],
            complexity="low",
            confidence=0.74,
            source_refs=refs[:3],
        ),
        PrdOption(
            id="option-b",
            name="Publishable Portfolio Workflow",
            pm_role="User Workflow PM",
            thesis="Optimize the full job from content capture to preview to static HTML export.",
            target_users=["Designers", "Developers", "Freelancers", "Job seekers"],
            core_problem=(
                "Users need to turn profile content, project screenshots, and contact details into a presentable "
                "portfolio page they can preview and export."
            ),
            mvp_features=[
                "Profile editor with avatar upload",
                "Project gallery CRUD with screenshots, descriptions, tags, and links",
                "Simple theme selection",
                "Portfolio preview",
                "Static HTML export",
                "Local save",
            ],
            differentiator="The first useful artifact is a publishable static portfolio page, not just stored content.",
            non_goals=["Custom domain hosting", "Multi-page CMS", "Team collaboration", "Advanced animation builder"],
            risks=[
                "Image upload and static export need careful file handling tests.",
                "Theme scope can expand quickly; keep themes simple and constrained.",
            ],
            complexity="medium",
            confidence=0.88,
            source_refs=refs[:4],
        ),
        PrdOption(
            id="option-c",
            name="AI Portfolio Studio",
            pm_role="Differentiation PM",
            thesis="Differentiate with AI-assisted copy, layout recommendations, and stronger visual polish.",
            target_users=["Power users", "Designers refreshing a portfolio", "Creators wanting polished copy"],
            core_problem="Users need help turning raw project notes and images into a compelling personal brand.",
            mvp_features=[
                "Portfolio content import",
                "AI copy suggestions",
                "Theme recommendations",
                "Preview and export",
                "Project scoring checklist",
            ],
            differentiator="More than a builder: it helps users improve the quality of the portfolio itself.",
            non_goals=["Recruiter marketplace", "Hosted analytics", "Custom code editor"],
            risks=["Higher model dependency and UX complexity before the basic builder is proven."],
            complexity="high",
            confidence=0.61,
            source_refs=refs[:5],
        ),
    ]


def _freelance_options(source_refs: list[str]) -> list[PrdOption]:
    refs = source_refs or ["Assumption"]
    return [
        PrdOption(
            id="option-a",
            name="Minimal Time Ledger",
            pm_role="Market PM",
            thesis="Win by making time entry and review faster than spreadsheets.",
            target_users=["Solo freelancers", "Consultants billing hourly"],
            core_problem="Billable time is easy to forget and hard to reconcile at invoice time.",
            mvp_features=["Time entry CRUD", "Client/project fields", "Billable status", "Monthly billable summary"],
            differentiator="Low-friction local tracking without account setup.",
            non_goals=["PDF invoice rendering", "Payments", "Team timesheets"],
            risks=["May feel too small if invoice workflow is expected immediately."],
            complexity="low",
            confidence=0.72,
            source_refs=refs[:3],
        ),
        PrdOption(
            id="option-b",
            name="Invoice-Ready Workflow",
            pm_role="User Workflow PM",
            thesis="Connect time tracking directly to invoice drafts so the product solves the full monthly billing job.",
            target_users=["Freelancers with multiple clients", "Small agencies before team workflows"],
            core_problem="Users need to turn tracked time into billable invoice-ready records with less manual cleanup.",
            mvp_features=[
                "Time entry CRUD",
                "Client/project grouping",
                "Billable and non-billable tracking",
                "Unbilled amount summary",
                "Invoice draft generation",
            ],
            differentiator="The first useful artifact is not just a timesheet, but an invoice draft.",
            non_goals=["Payment collection", "Tax filing", "Multi-user approvals"],
            risks=["Invoice scope can grow quickly; keep draft simple."],
            complexity="medium",
            confidence=0.84,
            source_refs=refs[:4],
        ),
        PrdOption(
            id="option-c",
            name="Freelancer Operating Console",
            pm_role="Differentiation PM",
            thesis="Bundle time, invoices, and lightweight client status into one local command center.",
            target_users=["Power freelancers", "Independent studios"],
            core_problem="Freelancers need a single view of work done, invoices pending, and client revenue.",
            mvp_features=["Time tracking", "Invoice drafts", "Client revenue dashboard", "Status filters"],
            differentiator="More strategic than a timer; less heavy than accounting software.",
            non_goals=["Full CRM", "Bookkeeping", "Payments"],
            risks=["Higher UI and data-model complexity before core tracking is proven."],
            complexity="high",
            confidence=0.63,
            source_refs=refs[:5],
        ),
    ]


def _expense_options(source_refs: list[str]) -> list[PrdOption]:
    refs = source_refs or ["Assumption"]
    return [
        PrdOption(
            id="option-a",
            name="Fast Expense Notebook",
            pm_role="Market PM",
            thesis="Win on speed and simplicity for manual transaction capture.",
            target_users=["Solo personal finance users", "Spreadsheet replacers"],
            core_problem="Users need to record income and spending before details are forgotten.",
            mvp_features=["Transaction CRUD", "Category field", "Monthly totals"],
            differentiator="Fastest local entry flow.",
            non_goals=["Bank sync", "Budgets", "Receipt OCR"],
            risks=["Too simple for users expecting insights."],
            complexity="low",
            confidence=0.76,
            source_refs=refs[:3],
        ),
        PrdOption(
            id="option-b",
            name="Monthly Budget Review",
            pm_role="User Workflow PM",
            thesis="Focus on monthly review and category insight, not just transaction storage.",
            target_users=["Budget-conscious users", "People reviewing monthly cash flow"],
            core_problem="Users need to understand where money went and whether the month is positive or negative.",
            mvp_features=["Transaction CRUD", "Category summaries", "Income/expense/net monthly view", "Basic filters"],
            differentiator="The summary view is the core product, not a secondary report.",
            non_goals=["Investment tracking", "Bank sync", "Shared budgets"],
            risks=["Needs careful summary accuracy tests."],
            complexity="medium",
            confidence=0.86,
            source_refs=refs[:4],
        ),
        PrdOption(
            id="option-c",
            name="Personal Finance Coach",
            pm_role="Differentiation PM",
            thesis="Add suggestions and warnings once enough spending history exists.",
            target_users=["Users wanting guidance", "Habit changers"],
            core_problem="Users do not just need records; they need behavior feedback.",
            mvp_features=["Transactions", "Trends", "Budget alerts", "Simple recommendations"],
            differentiator="Advice-oriented local finance tool.",
            non_goals=["Financial advice compliance", "Automated banking", "Credit products"],
            risks=["Recommendation quality can become untrustworthy without enough data."],
            complexity="high",
            confidence=0.58,
            source_refs=refs[:5],
        ),
    ]


def _generic_options(source_refs: list[str]) -> list[PrdOption]:
    refs = source_refs or ["Assumption"]
    return [
        PrdOption(
            id="option-a",
            name="Smallest Useful MVP",
            pm_role="Market PM",
            thesis="Reduce risk by shipping the narrowest workflow that proves repeated use.",
            target_users=["Early solo users"],
            core_problem="The core job needs a faster local workflow.",
            mvp_features=["Core CRUD", "List view", "Summary view"],
            differentiator="Simple local-first execution.",
            non_goals=["Integrations", "Collaboration", "Automation"],
            risks=["May be too narrow."],
            complexity="low",
            confidence=0.7,
            source_refs=refs[:3],
        ),
        PrdOption(
            id="option-b",
            name="Workflow-Centered MVP",
            pm_role="User Workflow PM",
            thesis="Optimize for the full repeated user job from capture to review.",
            target_users=["Users with recurring workflow pain"],
            core_problem="Users need the whole workflow to connect cleanly.",
            mvp_features=["Core CRUD", "Status workflow", "Filters", "Summary"],
            differentiator="Workflow completeness over raw feature count.",
            non_goals=["Advanced automation", "Enterprise controls"],
            risks=["Medium scope requires strong testing."],
            complexity="medium",
            confidence=0.82,
            source_refs=refs[:4],
        ),
        PrdOption(
            id="option-c",
            name="Differentiated Power Tool",
            pm_role="Differentiation PM",
            thesis="Make the product memorable with a stronger dashboard and automation angle.",
            target_users=["Power users"],
            core_problem="Existing tools feel generic or heavy.",
            mvp_features=["Core workflow", "Dashboard", "Automation hints"],
            differentiator="More opinionated product shape.",
            non_goals=["Broad platform"],
            risks=["Higher complexity before validation."],
            complexity="high",
            confidence=0.6,
            source_refs=refs[:5],
        ),
    ]


def _complexity_score(value: str) -> int:
    return {"low": 3, "medium": 2, "high": 1}.get(value, 0)


def _bullets(values: list[str]) -> list[str]:
    return [f"- {value}" for value in values]
