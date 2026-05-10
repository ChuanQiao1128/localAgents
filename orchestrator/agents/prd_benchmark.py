from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


@dataclass(frozen=True)
class PrdBenchmarkResult:
    index_path: Path
    domain_template_path: Path
    quality_gates_path: Path
    decision_playbook_path: Path
    development_handoff_path: Path
    library_json_path: Path


class PrdBenchmarkAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> PrdBenchmarkResult:
        project_path = Path(project["path"])
        domain_type = _domain_type(project["idea"])
        benchmark_dir = project_path / "docs/product/benchmark-library"
        benchmark_dir.mkdir(parents=True, exist_ok=True)

        paths = {
            "index_path": benchmark_dir / "index.md",
            "domain_template_path": benchmark_dir / f"{domain_type}-template.md",
            "quality_gates_path": benchmark_dir / "quality-gates.md",
            "decision_playbook_path": benchmark_dir / "decision-playbook.md",
            "development_handoff_path": benchmark_dir / "development-handoff.md",
            "library_json_path": benchmark_dir / "benchmark-library.json",
        }
        library = _benchmark_library(domain_type)
        paths["index_path"].write_text(_render_index(domain_type, library), encoding="utf-8")
        paths["domain_template_path"].write_text(
            _render_domain_template(project["idea"], domain_type), encoding="utf-8"
        )
        paths["quality_gates_path"].write_text(_render_quality_gates(domain_type), encoding="utf-8")
        paths["decision_playbook_path"].write_text(_render_decision_playbook(domain_type), encoding="utf-8")
        paths["development_handoff_path"].write_text(_render_development_handoff(domain_type), encoding="utf-8")
        paths["library_json_path"].write_text(json.dumps(library, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in paths.values():
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="research",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="Local PRD benchmark library artifact.",
                )
            EventBus(self.db).emit(
                event_type="prd.benchmark_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="research",
                message=f"Generated local PRD benchmark library for {domain_type}.",
                payload={"domain_type": domain_type, "benchmark_count": len(library["benchmarks"])},
            )

        return PrdBenchmarkResult(**paths)


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


def _benchmark_library(domain_type: str) -> dict[str, Any]:
    return {
        "domain_type": domain_type,
        "benchmarks": [
            {
                "name": "Aha!",
                "category": "product lifecycle",
                "pattern": "Strategy, requirements, roadmap, prioritization, and delivery remain connected.",
                "prd_agent_rule": "Every PRD needs a clear product strategy, explicit non-goals, and handoff-ready requirements.",
                "failure_signal": "The output is only a feature list or generic PRD sections.",
            },
            {
                "name": "Dovetail",
                "category": "research synthesis",
                "pattern": "Research evidence is organized into highlights, insights, reports, and assumptions.",
                "prd_agent_rule": "Separate raw sources, synthesized insights, assumptions, and open research gaps.",
                "failure_signal": "A product claim appears without a source, assumption label, or debate decision.",
            },
            {
                "name": "Productboard",
                "category": "insight-to-feature traceability",
                "pattern": "Feedback and insights are linked to feature ideas and specs.",
                "prd_agent_rule": "Each MVP feature needs a traceable reason: evidence, insight, strategic bet, or selected option.",
                "failure_signal": "MVP includes features that cannot be traced to user value.",
            },
            {
                "name": "Jira Product Discovery",
                "category": "idea selection and prioritization",
                "pattern": "Ideas are captured, compared, prioritized, and connected to delivery work.",
                "prd_agent_rule": "Generate options, record the selected option, explain tradeoffs, and keep rejected scope visible.",
                "failure_signal": "The PRD hides why one product direction beat the others.",
            },
            {
                "name": "v0",
                "category": "UI prototype handoff",
                "pattern": "A concise product prompt can produce a concrete UI draft and iteration path.",
                "prd_agent_rule": "PRD must give UI enough specificity: flows, states, components, content model, visual constraints.",
                "failure_signal": "The UI Agent would need to invent the primary workflow or visual quality bar.",
            },
            {
                "name": "Replit Agent",
                "category": "app-building loop",
                "pattern": "Natural-language intent turns into a runnable app through previews, tests, checkpoints, and repair loops.",
                "prd_agent_rule": "Acceptance criteria must be runnable, observable, and usable by QA/reviewer agents.",
                "failure_signal": "Criteria cannot be tested in the generated app.",
            },
            {
                "name": "Claude Code agents",
                "category": "multi-agent execution",
                "pattern": "Specialized agents work with explicit context, tool boundaries, and review gates.",
                "prd_agent_rule": "PRD must not collapse PM, UI, architecture, QA, and review into one vague output.",
                "failure_signal": "The PRD bypasses later agents or leaves their handoff ambiguous.",
            },
        ],
        "domain_rules": _domain_rules(domain_type),
    }


def _domain_rules(domain_type: str) -> list[dict[str, str]]:
    if domain_type == "portfolio":
        return [
            {
                "rule": "Publishable artifact first",
                "why": "The user value is a credible portfolio page, not stored profile data.",
                "prd_requirement": "Preview and static export must be MVP gates.",
            },
            {
                "rule": "User proof stays real",
                "why": "Portfolio credibility depends on real work, screenshots, role, links, and contact details.",
                "prd_requirement": "AI-generated assets may be placeholders only; never fake headshots, client logos, screenshots, credentials, or work history.",
            },
            {
                "rule": "Theme/media/story must work together",
                "why": "Visual polish without project storytelling is shallow; text without screenshots is weak.",
                "prd_requirement": "Project cards need screenshot, title, description, role, tags, links, and responsive preview behavior.",
            },
            {
                "rule": "Platform breadth is delayed",
                "why": "Hosting, domains, imports, analytics, and CMS breadth can overwhelm the MVP.",
                "prd_requirement": "Keep local save, preview, and static HTML export as the boundary.",
            },
        ]
    if domain_type == "freelance":
        return [
            {
                "rule": "Invoice-ready artifact first",
                "why": "The user value is reducing billing friction.",
                "prd_requirement": "Time entries must roll into verifiable invoice drafts.",
            },
            {
                "rule": "Trustworthy totals",
                "why": "Billing errors directly damage trust.",
                "prd_requirement": "QA must verify totals after create, edit, delete, billable toggles, and rate changes.",
            },
        ]
    if domain_type == "expense":
        return [
            {
                "rule": "Monthly review first",
                "why": "The user value is understanding cash flow, not storing transactions.",
                "prd_requirement": "Monthly income, expense, and net totals must be visible and traceable.",
            },
            {
                "rule": "Fast entry beats broad reporting",
                "why": "Manual tracking dies when entry is slower than notes or spreadsheets.",
                "prd_requirement": "Transaction creation must stay lightweight and validated.",
            },
        ]
    return [
        {
            "rule": "One primary workflow",
            "why": "Generic app ideas become low-quality CRUD unless the repeated user job is specific.",
            "prd_requirement": "MVP must optimize capture, validation, review, and one useful output.",
        },
        {
            "rule": "Traceable output",
            "why": "Users trust artifacts they can inspect.",
            "prd_requirement": "Summaries and generated outputs must link back to source records or inputs.",
        },
    ]


def _render_index(domain_type: str, library: dict[str, Any]) -> str:
    lines = [
        "# Local PRD Benchmark Library",
        "",
        "This library is local and deterministic. It does not call external APIs.",
        "It gives the PRD Agent reusable product-management standards before any paid research or screenshots are used.",
        "",
        f"Domain type: `{domain_type}`",
        "",
        "| Benchmark | Category | Pattern | PRD Agent Rule | Failure Signal |",
        "| --- | --- | --- | --- | --- |",
    ]
    for item in library["benchmarks"]:
        lines.append(
            f"| {_escape_table(item['name'])} | {_escape_table(item['category'])} | {_escape_table(item['pattern'])} | {_escape_table(item['prd_agent_rule'])} | {_escape_table(item['failure_signal'])} |"
        )
    lines.extend(["", "## Domain Rules", ""])
    for rule in library["domain_rules"]:
        lines.append(f"- {rule['rule']}: {rule['prd_requirement']} Reason: {rule['why']}")
    return "\n".join(lines)


def _render_domain_template(idea: str, domain_type: str) -> str:
    lines = [
        "# Domain PRD Template",
        "",
        "## Product Idea",
        "",
        idea,
        "",
        "## Required PRD Decisions",
        "",
    ]
    if domain_type == "portfolio":
        lines.extend(
            [
                "- Who is the portfolio for: designer, developer, freelancer, job seeker, or mixed?",
                "- What is the first publishable artifact: live preview, exported HTML, zip bundle, or hosted page?",
                "- Which uploaded assets are required: avatar, project screenshots, project links, social links?",
                "- How many theme presets are enough for MVP without creating a template marketplace?",
                "- What AI visuals are allowed as placeholders, and what user proof must never be fabricated?",
                "- What must QA compare between preview and exported HTML?",
            ]
        )
    elif domain_type == "freelance":
        lines.extend(
            [
                "- What is the invoice-ready artifact?",
                "- Which fields are required to make totals trustworthy?",
                "- How should billable and non-billable work differ?",
                "- Which export or handoff is MVP versus V1?",
            ]
        )
    elif domain_type == "expense":
        lines.extend(
            [
                "- What is the fastest valid transaction-entry flow?",
                "- Which monthly summary answers the user's real decision?",
                "- Which category behavior is MVP versus V1?",
                "- What edits must immediately update summary totals?",
            ]
        )
    else:
        lines.extend(
            [
                "- Who is the primary user?",
                "- What repeated job does the product make easier?",
                "- What useful artifact does the user get at the end?",
                "- What is explicitly out of scope?",
            ]
        )
    lines.extend(
        [
            "",
            "## Anti-Generic Checklist",
            "",
            "- The PRD names a specific primary workflow.",
            "- The PRD defines a useful output artifact, not just stored records.",
            "- The PRD explains what is intentionally excluded.",
            "- Acceptance criteria can be tested by QA without asking PM for clarification.",
            "- UI, architecture, developer, QA, and reviewer agents each receive enough handoff detail.",
        ]
    )
    return "\n".join(lines)


def _render_quality_gates(domain_type: str) -> str:
    rows = [
        ("Evidence", "Every major feature cites a source, selected option, benchmark rule, or assumption.", "Fail if MVP features appear with no reason."),
        ("Differentiation", "The PRD states why this product is narrower or better than references.", "Fail if positioning is generic."),
        ("Scope", "MVP, V1, future, and non-goals are separate.", "Fail if platform scope enters MVP without a decision."),
        ("UX", "Core flow, empty/loading/error/success states, and validation are defined.", "Fail if UI Agent must invent basic states."),
        ("Testability", "Acceptance criteria use Given/When/Then and map to QA actions.", "Fail if criteria are subjective only."),
        ("Handoff", "Architecture, development, QA, and review gates are named.", "Fail if downstream agents have no clear contract."),
    ]
    if domain_type == "portfolio":
        rows.extend(
            [
                ("Portfolio visual quality", "Theme, media upload, project story, preview, and export are evaluated together.", "Fail if PRD is only a profile form."),
                ("AI asset integrity", "Generated visuals are placeholders; real proof is user-owned.", "Fail if PRD allows fake credentials or screenshots."),
                ("Preview fidelity", "Preview and exported HTML must represent the same content.", "Fail if mismatch is not treated as a defect."),
            ]
        )
    lines = [
        "# PRD Quality Gates",
        "",
        "| Gate | Pass Standard | Fail Condition |",
        "| --- | --- | --- |",
    ]
    for gate, pass_standard, fail_condition in rows:
        lines.append(f"| {gate} | {pass_standard} | {fail_condition} |")
    lines.extend(
        [
            "",
            "## Score Guidance",
            "",
            "- `0-41/60`: reject; research or product direction is too weak.",
            "- `42-49/60`: pass with caution; good enough for design exploration.",
            "- `50-54/60`: strong; good enough for architecture and implementation planning.",
            "- `55-60/60`: excellent; includes evidence, tradeoffs, edge cases, and clear handoff gates.",
        ]
    )
    return "\n".join(lines)


def _render_decision_playbook(domain_type: str) -> str:
    lines = [
        "# PRD Decision Playbook",
        "",
        "Use this before locking a PRD option.",
        "",
        "| Criterion | Question | Strong Answer |",
        "| --- | --- | --- |",
        "| User value | What painful workflow is improved? | The answer names a concrete user job and final artifact. |",
        "| Evidence | Why believe this matters? | Sources, benchmark patterns, or assumptions are explicit. |",
        "| Differentiation | Why not use an existing tool? | The PRD names a narrower, faster, local, or higher-quality wedge. |",
        "| Feasibility | Can the MVP be built and tested locally? | Scope maps cleanly to UI, architecture, implementation, QA, and review. |",
        "| Risk | What could make the product feel bad? | The PRD names UX, trust, scope, and technical risks. |",
        "| Prototype readiness | Can a UI/developer agent act on this? | States, content model, acceptance criteria, and output artifacts are concrete. |",
    ]
    if domain_type == "portfolio":
        lines.extend(
            [
                "",
                "## Portfolio-Specific Decision Questions",
                "",
                "- Does the selected option make a portfolio page feel publishable, not just editable?",
                "- Does the PRD protect user credibility by separating uploaded proof from AI placeholders?",
                "- Does MVP include preview/export because that is the actual portfolio-builder payoff?",
                "- Does the PRD avoid competing with full hosting/CMS platforms too early?",
            ]
        )
    return "\n".join(lines)


def _render_development_handoff(domain_type: str) -> str:
    lines = [
        "# Development Handoff Contract",
        "",
        "## UI Designer Agent Receives",
        "",
        "- Target user and core workflow.",
        "- Product strategy and selected option.",
        "- UX quality bar, states, content model, and visual constraints.",
        "- Evidence chain and benchmark gates.",
        "",
        "## Architect Agent Receives",
        "",
        "- MVP/V1/future scope boundaries.",
        "- Non-goals and risk list.",
        "- Acceptance criteria and QA gates.",
        "- Data/output artifact requirements.",
        "",
        "## Developer Agent Receives",
        "",
        "- Implementable feature list.",
        "- Allowed scope and expected user-facing states.",
        "- Test commands and acceptance criteria.",
        "- Output artifact expectations.",
        "",
        "## QA Agent Receives",
        "",
        "- Given/When/Then acceptance criteria.",
        "- Edge states and invalid input rules.",
        "- Source-to-output consistency checks.",
        "",
        "## Reviewer Agent Receives",
        "",
        "- Product intent, non-goals, and evidence chain.",
        "- Scope creep rules.",
        "- Quality gates and known risks.",
    ]
    if domain_type == "portfolio":
        lines.extend(
            [
                "",
                "## Portfolio-Specific Handoff",
                "",
                "- UI must show profile editor, project gallery editor, theme selection, preview, and export path.",
                "- Architecture must keep preview and export rendering consistent.",
                "- Developer must implement upload validation states and stable project card layout.",
                "- QA must test theme changes, upload failures, project reorder/edit/delete, preview fidelity, and export fidelity.",
                "- Reviewer must reject fake proof-of-work assets and unlabelled generated visuals.",
            ]
        )
    return "\n".join(lines)


def _escape_table(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ")
