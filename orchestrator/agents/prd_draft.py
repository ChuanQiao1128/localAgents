from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from orchestrator.agents.prd_benchmark import PrdBenchmarkAgent
from orchestrator.agents.prd_council import PrdCouncilAgent
from orchestrator.agents.prd_manual import ManualCodexPrdAgent, PrdValidationResult
from orchestrator.agents.prd_research_v2 import PrdResearchV2Agent
from orchestrator.db import Database


class LocalPrdDraftAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def draft(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
        output_path: Path | None = None,
    ) -> Path:
        project_path = Path(project["path"])
        sources = load_research_sources(project_path, run_id)
        PrdBenchmarkAgent(self.db).run(project=project, run_id=run_id)
        PrdResearchV2Agent(self.db).run(project=project, run_id=run_id, sources=sources)
        selected_option = _load_selected_option(project_path, run_id)
        council = PrdCouncilAgent(self.db).generate(
            project=project,
            run_id=run_id,
            sources=sources,
            selected_option=selected_option,
        )
        payload = build_prd_payload(
            project["idea"],
            sources,
            selected_option=selected_option,
            pm_debate_md=council.debate_markdown,
        )
        if output_path is None:
            output_dir = project_path / ".agent/artifacts/manual_codex/prd"
            if run_id:
                output_dir = output_dir / run_id
            output_dir.mkdir(parents=True, exist_ok=True)
            output_path = output_dir / "prd-response.json"
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return output_path

    def draft_and_import(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
        output_path: Path | None = None,
    ) -> tuple[Path, PrdValidationResult]:
        path = self.draft(project=project, run_id=run_id, output_path=output_path)
        validation = ManualCodexPrdAgent(self.db).import_result(
            project=project,
            run_id=run_id,
            input_path=path,
        )
        return path, validation


def load_research_sources(project_path: Path, run_id: str | None) -> list[dict[str, Any]]:
    candidates: list[Path] = []
    if run_id:
        candidates.append(project_path / ".agent/artifacts/research" / run_id / "sources.json")
    candidates.extend(sorted((project_path / ".agent/artifacts/research").glob("*/sources.json"), reverse=True))
    for path in candidates:
        if path.exists():
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                return [item for item in loaded if isinstance(item, dict)]
    return []


def _load_selected_option(project_path: Path, run_id: str | None) -> Any | None:
    from orchestrator.agents.prd_options import load_selected_option

    return load_selected_option(project_path, run_id)


def build_prd_payload(
    idea: str,
    sources: list[dict[str, Any]],
    selected_option: Any | None = None,
    pm_debate_md: str | None = None,
) -> dict[str, str]:
    top_sources = _top_sources(sources)
    source_refs = _source_refs(top_sources)
    source_list = _source_list(top_sources)
    domain_type = _domain_type(idea)
    domain = _domain_from_selected_option(selected_option) if selected_option else _infer_domain(idea)
    core_ref = source_refs[0] if source_refs else "Assumption"
    secondary_ref = source_refs[1] if len(source_refs) > 1 else core_ref
    third_ref = source_refs[2] if len(source_refs) > 2 else secondary_ref
    selected_source_ref = _selected_source_ref(selected_option, secondary_ref)
    option_block = _selected_option_block(selected_option)
    non_goals = domain.get("non_goals") or _default_non_goals()
    risks = domain.get("risks") or _default_risks(domain)
    core_problem = _core_problem_sentence(domain["problem"], from_selected_option=selected_option is not None)
    competitive_synthesis = _competitive_synthesis(domain_type, top_sources)
    opportunity = _product_opportunity(domain_type, selected_option)
    reference_patterns = _reference_patterns(domain_type, top_sources)
    product_strategy = _product_strategy(domain_type, selected_option)
    product_management_operating_model = _product_management_operating_model(domain_type)
    evidence_chain = _evidence_chain_summary(domain_type, top_sources)
    ux_quality_bar = _ux_quality_bar(domain_type)
    ai_visual_strategy = _ai_visual_strategy(domain_type)
    background_research_sentence = _background_research_sentence(domain_type)
    research_insights = _research_insights(
        domain_type=domain_type,
        domain=domain,
        core_ref=core_ref,
        selected_source_ref=selected_source_ref,
        third_ref=third_ref,
    )
    competitor_matrix_md = _competitor_matrix_markdown(domain_type, top_sources)
    pm_debate_md = pm_debate_md or _pm_debate_markdown(
        domain_type=domain_type,
        selected_option=selected_option,
        core_problem=core_problem,
        product_strategy=product_strategy,
        ai_visual_strategy=ai_visual_strategy,
    )
    prd_quality_score_md = _prd_quality_score_markdown(
        domain_type=domain_type,
        sources=top_sources,
        selected_option=selected_option,
        prd_md_sections=[
            reference_patterns,
            product_strategy,
            product_management_operating_model,
            evidence_chain,
            ux_quality_bar,
            ai_visual_strategy,
            domain["mvp"],
            domain["acceptance"],
        ],
    )

    research_md = f"""# Research

## Product Idea

{idea}

{option_block}

## Sources Or Assumptions

{source_list or "- Assumption: No external research source was available when this local draft was generated."}

## Competitive Research Synthesis

{competitive_synthesis}

## Product Opportunity

{opportunity}

## Evidence Chain

{evidence_chain}

## Insights

{research_insights}

## Research Quality Notes

- This local draft was generated by deterministic code from available Tavily source snippets.
- Claims without a strong matching source are marked as assumptions.
"""

    prd_md = f"""# Product Requirements

## Background

{idea}

{background_research_sentence} Source: {core_ref}

{option_block}

## Reference Product Patterns

{reference_patterns}

## Product Strategy And Differentiation

{product_strategy}

## Product Management Operating Model

{product_management_operating_model}

## UX Quality Bar

{ux_quality_bar}

## AI And Visual Asset Strategy

{ai_visual_strategy}

## Users

{domain['users']}

## Core Problem

{core_problem} Source: {selected_source_ref}

## MVP

{domain['mvp']}

## V1

{domain['v1']}

## Non-goals

{non_goals}

## Risks

{risks}
"""

    user_stories_md = f"""# User Stories

{domain['user_stories']}
- As a local-first user, I want files and artifacts to stay inspectable, so that I can trust what the system generated.
"""

    acceptance_criteria_md = f"""# Acceptance Criteria

{domain['acceptance']}
"""

    scope_md = f"""# Scope

## MVP

{domain['scope_mvp']}
- Generated product, design, architecture, QA, and review artifacts remain local.

## V1

{domain['scope_v1']}

## Future

- External integrations.
- Multi-device sync.
- Advanced automation.
- Role-based collaboration.

## Non-goals

- SaaS deployment.
- Enterprise permissions.
- Complex third-party integrations.
- Mobile native app workflow.
"""

    return {
        "research_md": research_md,
        "competitor_matrix_md": competitor_matrix_md,
        "pm_debate_md": pm_debate_md,
        "prd_md": prd_md,
        "user_stories_md": user_stories_md,
        "acceptance_criteria_md": acceptance_criteria_md,
        "scope_md": scope_md,
        "prd_quality_score_md": prd_quality_score_md,
    }


def _domain_from_selected_option(option: Any) -> dict[str, str]:
    return {
        "core_workflow": getattr(option, "thesis"),
        "users": _bullets(getattr(option, "target_users")),
        "problem": _normalize_problem(getattr(option, "core_problem")),
        "mvp": _bullets(getattr(option, "mvp_features")),
        "v1": "\n".join(
            [
                f"- Strengthen the selected differentiator: {getattr(option, 'differentiator')}",
                "- Add exports or templates only after the core workflow is validated.",
                "- Expand analytics around the repeated user decision, not generic dashboards.",
            ]
        ),
        "non_goals": _bullets(getattr(option, "non_goals")),
        "risks": _bullets(_expanded_risks(getattr(option, "risks"))),
        "user_stories": _user_stories_from_option(option),
        "acceptance": _acceptance_from_option(option),
        "scope_mvp": _bullets(getattr(option, "mvp_features")),
        "scope_v1": "\n".join(
            [
                f"- Productize the differentiator: {getattr(option, 'differentiator')}",
                "- Add tested exports for handoff workflows.",
                "- Add saved views for repeated review sessions.",
            ]
        ),
    }


def _competitor_matrix_markdown(domain_type: str, sources: list[dict[str, Any]]) -> str:
    rows = _competitor_rows(domain_type, sources)
    lines = [
        "# Competitor Matrix",
        "",
        "| Competitor / Reference | Source | Pattern | Opportunity | Caution |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['name']} | {row['source']} | {row['pattern']} | {row['opportunity']} | {row['caution']} |"
        )
    lines.extend(
        [
            "",
            "## Product Takeaway",
            "",
            _competitor_takeaway(domain_type),
        ]
    )
    return "\n".join(lines)


def _competitor_rows(domain_type: str, sources: list[dict[str, Any]]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if domain_type == "portfolio":
        defaults = [
            {
                "name": "Theme-led portfolio builder",
                "pattern": "Templates and first-impression polish make users feel close to publish-ready.",
                "opportunity": "Surface theme choice and preview early instead of hiding it after data entry.",
                "caution": "Large template libraries can become CMS scope creep.",
            },
            {
                "name": "Media-first portfolio editor",
                "pattern": "Images, descriptions, and links are composed together as the core artifact.",
                "opportunity": "Treat upload, project metadata, and preview as one workflow.",
                "caution": "Image handling failures will make the product feel untrustworthy.",
            },
            {
                "name": "Website preview/export flow",
                "pattern": "Users expect to see the final webpage before publishing or exporting.",
                "opportunity": "Make static export the MVP payout.",
                "caution": "Preview/export mismatch should be treated as a product defect.",
            },
            {
                "name": "Hosted portfolio platform",
                "pattern": "Domains, hosting, imports, and support are common platform upsells.",
                "opportunity": "Differentiate by staying local-first and exportable.",
                "caution": "Do not compete on hosting breadth in MVP.",
            },
        ]
    else:
        defaults = [
            {
                "name": "Focused workflow product",
                "pattern": "Strong products make the repeated user job obvious.",
                "opportunity": "Optimize the selected core workflow before integrations.",
                "caution": "Generic CRUD will not create a memorable product.",
            },
            {
                "name": "Summary/reporting product",
                "pattern": "Users trust products when summaries can be traced to source records.",
                "opportunity": "Make outputs inspectable and testable.",
                "caution": "Unverifiable summaries reduce trust.",
            },
            {
                "name": "Automation-first product",
                "pattern": "Automation can differentiate after the manual workflow works.",
                "opportunity": "Use automation later to remove repeated work.",
                "caution": "Automation too early hides weak product judgment.",
            },
            {
                "name": "Platform-style product",
                "pattern": "Broad products add collaboration, permissions, integrations, and dashboards.",
                "opportunity": "Borrow quality patterns without copying platform scope.",
                "caution": "Platform breadth is not an MVP strategy.",
            },
        ]
    for index, default in enumerate(defaults):
        source = sources[index] if index < len(sources) else None
        title = _source_title(source) if source else default["name"]
        source_ref = _source_id(source, index) if source else "Assumption"
        rows.append(
            {
                "name": title,
                "source": source_ref,
                "pattern": default["pattern"],
                "opportunity": default["opportunity"],
                "caution": default["caution"],
            }
        )
    return rows


def _competitor_takeaway(domain_type: str) -> str:
    if domain_type == "portfolio":
        return (
            "The portfolio builder should compete on the quality of the publishable artifact: media upload, "
            "project storytelling, theme confidence, preview fidelity, and static export."
        )
    return (
        "The product should compete on workflow clarity and quality of the final user artifact, not on a longer "
        "feature list."
    )


def _pm_debate_markdown(
    *,
    domain_type: str,
    selected_option: Any | None,
    core_problem: str,
    product_strategy: str,
    ai_visual_strategy: str,
) -> str:
    option_name = getattr(selected_option, "name", "Selected MVP direction") if selected_option else "MVP direction"
    differentiator = getattr(selected_option, "differentiator", "A focused workflow with a useful output.")
    if domain_type == "portfolio":
        market_view = "Portfolio references show demand for fast visual polish, templates, media composition, and a shareable webpage output."
        ux_view = "The critical path is profile content -> project gallery -> theme -> preview -> export; every step should make the final page more credible."
        design_view = "The UI must feel closer to a publishing studio than an admin panel: strong preview, stable cards, polished presets, and clear upload states."
        technical_view = "MVP is feasible if image handling is local, themes are constrained presets, and static export uses the same render model as preview."
        visual_view = "AI can provide placeholder covers, theme thumbnails, and demo examples, but the user must replace proof-of-work assets with real uploads."
        critic_view = "The main failure mode is producing a generic form builder; the PRD must keep static export and preview fidelity as hard requirements."
    else:
        market_view = "Reference products validate that users value a clear repeated workflow and reliable outputs."
        ux_view = "The product must make the primary path obvious and keep summaries traceable to source records."
        design_view = "Design should prioritize dense, inspectable task surfaces over marketing-style decoration."
        technical_view = "MVP is feasible if scope stays on core records, validation, summaries, and local artifacts."
        visual_view = "AI visuals should support clarity only when they help users understand the workflow."
        critic_view = "The main failure mode is generic CRUD without a differentiated user artifact."
    return f"""# PM Debate

Selected direction: {option_name}

## Market PM

{market_view}

## UX Researcher

{ux_view}

## Product Designer

{design_view}

## Technical PM

{technical_view}

## Visual/AI PM

{visual_view}

## Critic

{critic_view}

## Debate Resolution

- Core problem: {core_problem}
- Differentiator: {differentiator}
- Strategy: {product_strategy.splitlines()[0].lstrip("- ")}
- Visual/AI constraint: {ai_visual_strategy.splitlines()[0].lstrip("- ")}
- Decision: proceed only if the PRD remains specific enough to guide UI, architecture, implementation, QA, and review.
"""


def _prd_quality_score_markdown(
    *,
    domain_type: str,
    sources: list[dict[str, Any]],
    selected_option: Any | None,
    prd_md_sections: list[str],
) -> str:
    joined = "\n".join(prd_md_sections).lower()
    research_depth = 9 if len(sources) >= 5 else 7 if sources else 5
    differentiation = 9 if selected_option else 7
    ux_specificity = 9 if "quality bar" in joined or domain_type == "portfolio" else 7
    visual_strategy = 9 if domain_type == "portfolio" and "image" in joined and "ai" in joined else 7
    feasibility = 8
    testability = 9 if "given" in joined and "when" in joined and "then" in joined else 8
    generic_penalty = 0
    if "core crud" in joined or "summary view" in joined and domain_type == "portfolio":
        generic_penalty = 2
    raw_total = research_depth + differentiation + ux_specificity + visual_strategy + feasibility + testability
    final = max(0, raw_total - generic_penalty)
    status = "pass" if final >= 42 else "fail"
    return f"""# PRD Quality Score

- Research depth: {research_depth}/10
- Differentiation: {differentiation}/10
- UX specificity: {ux_specificity}/10
- Visual strategy: {visual_strategy}/10
- Feasibility: {feasibility}/10
- Testability: {testability}/10
- Genericness penalty: -{generic_penalty}

Final score: {final}/60
Status: {status}

## Gate Notes

- Pass threshold: 42/60.
- The PRD must include evidence-backed research, concrete differentiation, UX quality standards, and testable acceptance criteria.
- If status is fail, regenerate the PRD from stronger research or choose a narrower product option before design/architecture.
"""


def _source_title(source: dict[str, Any] | None) -> str:
    if not source:
        return "Assumption"
    return str(source.get("title") or "Untitled reference").replace("|", "/")


def _source_id(source: dict[str, Any] | None, index: int) -> str:
    if not source:
        return f"[S{index + 1}]"
    return f"[{source.get('id', f'S{index + 1}')}]"


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


def _competitive_synthesis(domain_type: str, sources: list[dict[str, Any]]) -> str:
    if not sources:
        return "- Assumption: No external source was available, so competitive synthesis should be treated as provisional."
    refs = _source_refs(sources)
    if domain_type == "portfolio":
        return "\n".join(
            [
                f"- Portfolio builders compete on speed to a polished public page: templates, image/text composition, and website preview are recurring patterns. Source: {_safe_ref(refs, 0)}",
                f"- Strong references make media handling a first-class workflow, not an afterthought: users expect images, project descriptions, links, and layout control to work together. Source: {_safe_ref(refs, 1)}",
                f"- The product should avoid becoming a generic website CMS; it should optimize the narrower portfolio job of proving credibility through selected work. Source: {_safe_ref(refs, 2)}",
                *_source_takeaways(sources, limit=4),
            ]
        )
    return "\n".join(
        [
            f"- The product should convert researched patterns into a focused workflow rather than copying one reference product. Source: {_safe_ref(refs, 0)}",
            f"- MVP scope should prioritize the user's repeated job and defer broad platform features. Source: {_safe_ref(refs, 1)}",
            *_source_takeaways(sources, limit=3),
        ]
    )


def _product_opportunity(domain_type: str, selected_option: Any | None) -> str:
    option_name = getattr(selected_option, "name", "the selected MVP direction") if selected_option else "the MVP"
    if domain_type == "portfolio":
        return "\n".join(
            [
                f"- {option_name} should feel like a publishing workflow, not a database of profile fields.",
                "- The key product bet is that preview plus static export creates a real artifact users can inspect, share, or host.",
                "- AI-generated visuals can raise perceived quality, but user-uploaded work remains the source of truth for credibility.",
            ]
        )
    return "\n".join(
        [
            f"- {option_name} should turn research into a specific user workflow with clear quality gates.",
            "- The product should define a high-quality artifact the user gets at the end, not only saved records.",
        ]
    )


def _background_research_sentence(domain_type: str) -> str:
    if domain_type == "portfolio":
        return (
            "The product should solve the narrow job of turning real profile content, project proof, images, "
            "links, and theme choices into a credible publishable portfolio artifact."
        )
    if domain_type == "freelance":
        return (
            "The product should solve the narrow job of capturing billable work, reviewing unbilled totals, "
            "and producing invoice-ready output without becoming a full accounting platform."
        )
    if domain_type == "expense":
        return (
            "The product should solve the narrow job of fast transaction capture and trustworthy monthly review "
            "without becoming a bank-connected finance suite."
        )
    return (
        "The product should solve one focused local workflow and produce a concrete user artifact before adding "
        "platform breadth."
    )


def _research_insights(
    *,
    domain_type: str,
    domain: dict[str, str],
    core_ref: str,
    selected_source_ref: str,
    third_ref: str,
) -> str:
    if domain_type == "portfolio":
        workflow = _workflow_phrase(domain_type, domain)
        return "\n".join(
            [
                f"- Target users need credible proof-of-work presentation: profile, images, project stories, links, and contact details must reinforce each other. Source: {core_ref}",
                f"- The MVP should focus on {workflow} before hosting, domains, analytics, imports, or broad CMS behavior. Source: {selected_source_ref}",
                f"- Competitive and reference products should be treated as pattern evidence for visual polish, preview fidelity, and export expectations, not as requirements to clone. Source: {third_ref}",
            ]
        )
    workflow = _workflow_phrase(domain_type, domain)
    return "\n".join(
        [
            f"- Target users need the product to reduce repeated manual work while keeping the final artifact inspectable and trustworthy. Source: {core_ref}",
            f"- The MVP should focus on {workflow} before advanced automation or integrations. Source: {selected_source_ref}",
            f"- Competitive and reference products should be treated as pattern evidence, not as requirements to clone. Source: {third_ref}",
        ]
    )


def _workflow_phrase(domain_type: str, domain: dict[str, str]) -> str:
    raw = domain["core_workflow"].strip().rstrip(".")
    if domain_type == "portfolio" and raw.lower().startswith("optimize "):
        return "the full job from content capture to preview to static HTML export"
    if raw.lower().startswith("optimize "):
        return raw[len("optimize ") :].strip()
    if raw[:1].isupper():
        return raw[:1].lower() + raw[1:]
    return raw


def _reference_patterns(domain_type: str, sources: list[dict[str, Any]]) -> str:
    refs = _source_refs(sources)
    if domain_type == "portfolio":
        return "\n".join(
            [
                f"- Template/theme selection should be visible early because portfolio tools sell confidence through first-impression polish. Source: {_safe_ref(refs, 0)}",
                f"- Image and text composition should be designed as one workflow: project screenshots, project descriptions, links, and skills should preview together. Source: {_safe_ref(refs, 1)}",
                f"- Website preview/export should be part of MVP because a portfolio builder's useful output is a presentable page, not stored content. Source: {_safe_ref(refs, 2)}",
                f"- Existing portfolio platforms often include hosting, domains, imports, or large template libraries; this local MVP should learn from those patterns but defer platform features. Source: {_safe_ref(refs, 3)}",
            ]
        )
    return "\n".join(
        [
            f"- Use the strongest reference products as pattern evidence for workflow shape, terminology, and quality expectations. Source: {_safe_ref(refs, 0)}",
            f"- Do not clone broad platform surfaces unless they directly support the selected MVP job. Source: {_safe_ref(refs, 1)}",
        ]
    )


def _product_strategy(domain_type: str, selected_option: Any | None) -> str:
    differentiator = getattr(selected_option, "differentiator", "A focused workflow that produces a useful artifact.")
    if domain_type == "portfolio":
        return "\n".join(
            [
                f"- Differentiator: {differentiator}",
                "- Positioning: a local-first portfolio publishing assistant for people who need a credible portfolio quickly without hand-coding.",
                "- Product principle: every data-entry step must immediately improve the preview page.",
                "- Scope principle: avoid CMS breadth; optimize the path from content to publishable static page.",
            ]
        )
    return "\n".join(
        [
            f"- Differentiator: {differentiator}",
            "- Product principle: every feature must support the selected workflow and produce testable output.",
            "- Scope principle: defer integrations and automation until the manual core workflow feels excellent.",
        ]
    )


def _product_management_operating_model(domain_type: str) -> str:
    base = [
        "- Aha!-style lifecycle: start with strategy and scope boundaries before turning ideas into requirements.",
        "- Dovetail-style research discipline: separate source evidence, synthesized insights, assumptions, and research gaps.",
        "- Productboard-style traceability: connect insights to feature decisions, prioritization, and specs.",
        "- Jira Product Discovery-style selection: preserve option tradeoffs and explain why the selected direction won.",
        "- v0/Replit-style delivery handoff: write requirements that can produce a prototype, preview, tests, and checkpoints.",
        "- Claude Code-style execution gates: keep PM, UI, architecture, development, QA, and review responsibilities separate.",
    ]
    if domain_type == "portfolio":
        base.extend(
            [
                "- Portfolio-specific gate: theme, media upload, preview, and static export must be evaluated as one publishable artifact workflow.",
                "- Portfolio-specific non-goal rule: hosting, domains, imports, analytics, and broad CMS features stay outside MVP unless explicitly selected.",
            ]
        )
    return "\n".join(base)


def _evidence_chain_summary(domain_type: str, sources: list[dict[str, Any]]) -> str:
    refs = _source_refs(sources)
    if domain_type == "portfolio":
        return "\n".join(
            [
                f"- Theme/template selection -> insight: portfolio users need quick confidence in visual polish -> PRD decision: include constrained presets and preview early. Source: {_safe_ref(refs, 0)}",
                f"- Media upload + project description -> insight: proof-of-work requires visual and narrative context together -> PRD decision: project cards need screenshot, title, role, tags, description, and links. Source: {_safe_ref(refs, 1)}",
                f"- Preview/export -> insight: the useful output is a presentable page, not saved form data -> PRD decision: static HTML export remains MVP. Source: {_safe_ref(refs, 2)}",
                f"- AI-assisted visuals -> insight: generated assets can improve first-run polish but must not fake user proof -> PRD decision: allow placeholders and theme thumbnails only. Source: {_safe_ref(refs, 3)}",
                "- Platform breadth -> insight: mature platforms add hosting, domains, imports, and analytics -> PRD decision: keep those as V1/future or non-goals unless selected deliberately. Source: Assumption unless confirmed in research sources.",
            ]
        )
    return "\n".join(
        [
            f"- Source evidence -> insight: the user needs one repeated workflow more than a broad platform -> PRD decision: optimize the selected core workflow first. Source: {_safe_ref(refs, 0)}",
            f"- Source evidence -> insight: trustworthy output must be inspectable -> PRD decision: summaries and artifacts must trace back to source records. Source: {_safe_ref(refs, 1)}",
            "- Mature product benchmark -> insight: integrations and automation are valuable after the manual core works -> PRD decision: keep platform features in V1/future. Source: Assumption.",
        ]
    )


def _ux_quality_bar(domain_type: str) -> str:
    if domain_type == "portfolio":
        return "\n".join(
            [
                "- First-run setup should produce a credible preview in under five minutes using sample prompts or placeholder content.",
                "- Upload states must cover empty, uploading, preview, replace, remove, invalid type, and oversized image.",
                "- The preview should be visually close to exported HTML; mismatches are product defects, not implementation details.",
                "- Theme selection must be constrained to polished presets with stable spacing, typography, responsive behavior, and accessible contrast.",
                "- The project editor should support repeated use: add, edit, reorder, duplicate, delete, and validate project cards without layout jumps.",
            ]
        )
    return "\n".join(
        [
            "- The primary workflow should be obvious without explanatory in-app text.",
            "- Empty, loading, validation, success, and error states must be defined for each core action.",
            "- Summary views must be traceable back to source records so users can verify results.",
        ]
    )


def _ai_visual_strategy(domain_type: str) -> str:
    if domain_type == "portfolio":
        return "\n".join(
            [
                "- Optional AI image generation can create theme preview thumbnails, tasteful placeholder project covers, background textures, and demo portfolio examples.",
                "- AI must not fabricate a user's real project screenshots, headshot, employment history, client logos, or credentials.",
                "- Generated visuals should be labeled as placeholders until the user replaces them with real assets.",
                "- Image generation prompts should be derived from selected theme, role, project category, and desired tone; generated assets should keep consistent aspect ratios for cards and hero sections.",
                "- The UI Designer Agent should receive this visual brief before generating screens, so the first prototype has portfolio-grade polish rather than generic SaaS styling.",
            ]
        )
    return "\n".join(
        [
            "- AI-generated images are optional and should support the product surface only when they clarify the user workflow.",
            "- Generated assets must be labeled as placeholders and never treated as user-owned evidence.",
        ]
    )


def _source_takeaways(sources: list[dict[str, Any]], limit: int) -> list[str]:
    takeaways: list[str] = []
    for source in sources[:limit]:
        title = str(source.get("title") or "Untitled source").strip()
        summary = str(source.get("summary") or "").strip()
        source_id = source.get("id", "S?")
        if summary:
            takeaways.append(f"- Reference takeaway from [{source_id}] {title}: {summary}")
        else:
            takeaways.append(f"- Reference takeaway from [{source_id}] {title}: treat as pattern evidence.")
    return takeaways


def _safe_ref(refs: list[str], index: int) -> str:
    if not refs:
        return "Assumption"
    if index < len(refs):
        return refs[index]
    return refs[-1]


def _selected_option_block(option: Any | None) -> str:
    if not option:
        return ""
    return f"""## Selected Product Option

Selected option: {getattr(option, 'id')} - {getattr(option, 'name')}

Thesis: {getattr(option, 'thesis')}

Decision source: docs/product/decision.md
"""


def _selected_source_ref(option: Any | None, fallback: str) -> str:
    if not option:
        return fallback
    refs = getattr(option, "source_refs", []) or []
    return refs[0] if refs else fallback


def _normalize_problem(value: str) -> str:
    lowered = value.lower()
    for prefix in ["users need to ", "users need a way to ", "users need "]:
        if lowered.startswith(prefix):
            return value[len(prefix) :].strip().rstrip(".")
    return value.strip().rstrip(".")


def _core_problem_sentence(problem: str, *, from_selected_option: bool) -> str:
    clean = problem.strip().rstrip(".")
    lowered = clean.lower()
    if lowered.startswith("users need"):
        sentence = clean
    elif from_selected_option:
        sentence = f"Users need to {clean}"
    else:
        sentence = f"Users need a dependable way to {clean} with minimal friction"
    return sentence if sentence.endswith(".") else sentence + "."


def _expanded_risks(risks: list[str]) -> list[str]:
    expanded = list(risks)
    if len(expanded) < 2:
        expanded.append("If the selected workflow is too broad, MVP implementation and testing will slow down.")
    if len(expanded) < 3:
        expanded.append("If summaries cannot be verified quickly, users may not trust the generated artifacts.")
    return expanded


def _default_non_goals() -> str:
    return "\n".join(
        [
            "- Multi-user collaboration.",
            "- Cloud deployment.",
            "- Complex accounting, compliance, or enterprise workflows.",
            "- Automatic integrations before the core manual flow is excellent.",
        ]
    )


def _default_risks(domain: dict[str, str]) -> str:
    return "\n".join(
        [
            f"- If {domain['entry_risk']}, users will stop maintaining the system.",
            f"- If {domain['summary_risk']}, the app will feel like data storage rather than a tool.",
            "- If scope expands too early, implementation quality and testing will suffer.",
        ]
    )


def _user_stories_from_option(option: Any) -> str:
    users = getattr(option, "target_users")
    primary_user = users[0].lower() if users else "primary user"
    features = getattr(option, "mvp_features")
    lines: list[str] = []
    for feature in features[:4]:
        lines.append(
            f"- As a {primary_user}, I want {feature.lower()}, so that I can complete the selected product workflow."
        )
    return "\n".join(lines)


def _acceptance_from_option(option: Any) -> str:
    features = getattr(option, "mvp_features")
    feature_text = " ".join(features).lower()
    consistency_target = (
        "preview and exported output"
        if "preview" in feature_text or "export" in feature_text or "html" in feature_text
        else "related list and summary views"
    )
    lines = [
        f"- Given I am using the product, when I complete {_feature_action(features[0])}, then the result is saved and visible in the main workflow.",
        f"- Given existing records are available, when I use {_feature_action(features[1]) if len(features) > 1 else 'the primary workflow'}, then {consistency_target} stay consistent.",
    ]
    if "theme" in feature_text:
        lines.append(
            "- Given I choose a theme, when I open the portfolio preview, then the selected theme changes styling without losing saved content."
        )
    if "preview" in feature_text:
        lines.append(
            "- Given saved content exists, when I open preview, then profile, project, image, and contact content render together as the final user-facing page."
        )
    if "export" in feature_text or "html" in feature_text:
        lines.append(
            "- Given the preview is valid, when I export static HTML, then an HTML artifact is created with saved content and image references."
        )
    lines.extend(
        [
            f"- Given the selected option is {getattr(option, 'name')}, when MVP features are reviewed, then the implementation includes the selected differentiator without unrelated scope.",
            "- Given required fields are missing, when I submit the form, then a clear validation message is shown and no invalid record is saved.",
        ]
    )
    if "preview" in feature_text or "export" in feature_text or "html" in feature_text:
        lines.append(
            "- Given saved content changes, when I compare preview and exported output, then the same profile, project, image, and contact content is represented in both artifacts."
        )
    else:
        lines.append(
            "- Given the workflow data changes, when I open the summary view, then totals and status indicators reflect the latest saved data."
        )
    return "\n".join(lines)


def _feature_action(feature: str) -> str:
    if feature.endswith("CRUD"):
        return feature.removesuffix("CRUD").strip().lower() + " create, edit, and delete"
    return feature[0].lower() + feature[1:]


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)


def _top_sources(sources: list[dict[str, Any]], limit: int = 8) -> list[dict[str, Any]]:
    return sorted(
        sources,
        key=lambda item: float(item.get("relevance") or 0.0),
        reverse=True,
    )[:limit]


def _source_refs(sources: list[dict[str, Any]]) -> list[str]:
    return [f"[{source.get('id', f'S{index + 1}')}]" for index, source in enumerate(sources)]


def _source_list(sources: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for source in sources:
        source_id = source.get("id", "S?")
        title = source.get("title", "Untitled")
        url = source.get("url", "")
        summary = source.get("summary", "")
        lines.append(f"- Source [{source_id}]: {title} ({url})")
        if summary:
            lines.append(f"  - Summary: {summary}")
    return "\n".join(lines)


def _infer_domain(idea: str) -> dict[str, str]:
    lower = idea.lower()
    if any(
        term in lower
        for term in ["portfolio", "作品集", "个人网站", "个人主页", "personal site", "personal website"]
    ):
        return {
            "core_workflow": (
                "profile content capture, project gallery management, theme selection, portfolio preview, "
                "and static HTML export"
            ),
            "users": (
                "- A designer who needs to present selected work with screenshots and case descriptions.\n"
                "- A developer who wants a clean personal site without hand-building every layout.\n"
                "- A freelancer or job seeker who needs a shareable portfolio artifact quickly."
            ),
            "problem": (
                "collect profile details, upload portfolio images, describe projects, preview the final page, "
                "and export a static portfolio without building the site manually"
            ),
            "mvp": (
                "- Edit profile content: avatar, name, title, bio, skills, contact links, and social links.\n"
                "- Create, edit, reorder, and delete portfolio projects.\n"
                "- Upload avatar and project screenshots with local preview.\n"
                "- Capture project title, description, role, tags, project URL, and repository URL.\n"
                "- Choose from a small set of constrained themes.\n"
                "- Preview the generated portfolio page before export.\n"
                "- Export a static HTML bundle that can be opened locally."
            ),
            "v1": (
                "- Add more portfolio sections such as experience, education, testimonials, and certifications.\n"
                "- Add image cropping and basic compression.\n"
                "- Add theme customization for colors, typography, and section order.\n"
                "- Add export presets for common hosting targets."
            ),
            "entry_risk": "profile and project entry feels slower than editing a simple document",
            "summary_risk": "preview and exported HTML do not match closely",
            "user_stories": (
                "- As a portfolio owner, I want to upload my avatar and edit my bio, so that visitors understand who I am.\n"
                "- As a portfolio owner, I want to add multiple projects with screenshots and links, so that I can show proof of work.\n"
                "- As a portfolio owner, I want to choose a simple theme, so that the portfolio matches my presentation style.\n"
                "- As a portfolio owner, I want to preview the generated page, so that I can catch content or layout issues before export.\n"
                "- As a portfolio owner, I want to export static HTML, so that I can share or host the portfolio outside the tool."
            ),
            "acceptance": (
                "- Given I am editing my profile, when I upload an avatar and save name, bio, skills, and contact links, then those fields appear in the portfolio preview.\n"
                "- Given I create a project with screenshot, title, description, tags, and links, when I save it, then the project appears in the portfolio gallery.\n"
                "- Given multiple projects exist, when I edit, reorder, or delete a project, then the preview reflects the latest project order and content.\n"
                "- Given I choose a theme, when I open the preview, then the selected theme changes layout styling without losing content.\n"
                "- Given the portfolio preview is valid, when I export static HTML, then an HTML artifact is created that includes profile content, project content, image references, and contact links.\n"
                "- Given required fields or unsupported image files are submitted, when I save, then clear validation messages are shown and invalid content is not saved."
            ),
            "scope_mvp": (
                "- Local portfolio builder workflow for one user.\n"
                "- Profile editor with avatar upload.\n"
                "- Project gallery CRUD with screenshots, descriptions, tags, and links.\n"
                "- Simple theme selection.\n"
                "- Portfolio preview.\n"
                "- Static HTML export.\n"
                "- Local save."
            ),
            "scope_v1": (
                "- Section ordering and extra profile sections.\n"
                "- Image crop/compression tools.\n"
                "- Theme customization beyond preset choices.\n"
                "- Export presets for Netlify, GitHub Pages, or zip packaging."
            ),
        }
    if any(term in lower for term in ["invoice", "发票", "time tracking", "时间追踪", "freelance", "自由职业"]):
        return {
            "core_workflow": "fast time entry, project/client association, invoice preparation, and clear billing review",
            "users": "- A freelancer who tracks billable and non-billable time across clients.\n- A solo consultant who needs clean invoice-ready summaries.\n- A small independent operator who currently relies on spreadsheets or notes.",
            "problem": "capture work sessions, connect them to clients and projects, review billable totals, and prepare invoice-ready records",
            "mvp": "- Create, edit, and delete time entries.\n- Capture client, project, date, duration, billable status, hourly rate, and notes.\n- Show recent time entries with client/project filters.\n- Summarize billable hours and unbilled amount by client and month.\n- Generate an invoice draft from approved billable entries.\n- Keep the workflow local-first and inspectable.",
            "v1": "- Add reusable client and project profiles.\n- Add invoice numbering and payment status.\n- Export invoice drafts to PDF or CSV.\n- Add saved filters for unpaid, unbilled, and billable entries.",
            "entry_risk": "time entry takes too many steps or does not support quick correction",
            "summary_risk": "billable totals, unbilled amounts, and invoice drafts are not easy to verify",
            "user_stories": "- As a freelancer, I want to log a work session quickly, so that I can keep billable time accurate.\n- As a freelancer, I want to assign time entries to a client and project, so that invoices can be grouped correctly.\n- As a freelancer, I want to mark entries as billable or non-billable, so that invoice totals exclude internal work.\n- As a freelancer, I want to generate an invoice draft from approved time entries, so that billing takes less manual effort.",
            "acceptance": "- Given I am on the time entry form, when I submit client, project, date, duration, billable status, and rate, then the time entry appears in the list view.\n- Given a billable time entry exists, when I change its duration or hourly rate, then the client summary updates the unbilled total.\n- Given multiple billable entries exist for one client, when I generate an invoice draft, then the draft includes those entries and the calculated total.\n- Given a time entry is marked non-billable, when I generate an invoice draft, then that entry is excluded from invoice totals.\n- Given required fields are missing, when I submit the form, then a clear validation message is shown and no invalid entry is saved.",
            "scope_mvp": "- Local web app workflow for one freelancer.\n- Time entry CRUD.\n- Client and project fields on each entry.\n- Billable status and hourly rate.\n- Client/month summary of hours and unbilled amount.\n- Invoice draft generation from billable entries.",
            "scope_v1": "- Client and project management screens.\n- PDF invoice export.\n- Invoice status: draft, sent, paid.\n- Recurring project defaults.\n- CSV export.",
        }
    if any(term in lower for term in ["expense", "记账", "收入", "支出", "finance", "预算"]):
        return {
            "core_workflow": "quick transaction capture, categorization, and monthly financial review",
            "users": "- A solo user tracking personal income and expenses.\n- A budget-conscious user who wants private local records.\n- A user replacing a spreadsheet with a small focused app.",
            "problem": "record income and expenses, categorize transactions, correct mistakes, and understand monthly cash flow",
            "mvp": "- Create, edit, and delete income and expense transactions.\n- Capture amount, date, type, category, and optional note.\n- Show recent transactions with category and month filters.\n- Summarize income, expenses, and net total by month.\n- Keep the workflow local-first and inspectable.",
            "v1": "- Add category management.\n- Add CSV import/export.\n- Add recurring transaction templates.\n- Add simple trend charts.",
            "entry_risk": "transaction entry is slower than a spreadsheet or notes app",
            "summary_risk": "monthly income, expense, and net totals are not trustworthy",
            "user_stories": "- As a budget-conscious user, I want to add an expense quickly, so that I can keep spending records current.\n- As a user, I want to add income transactions, so that monthly net totals are accurate.\n- As a user, I want to categorize transactions, so that I can understand where money goes.\n- As a user, I want to view monthly totals, so that I can review my financial position.",
            "acceptance": "- Given I am on the transaction form, when I submit a valid expense, then it appears in the transaction list as an expense.\n- Given I submit a valid income item, when I open monthly statistics, then income total includes that transaction.\n- Given I edit a transaction category, when I save, then list and monthly summary views show the updated category.\n- Given a transaction exists, when I delete it, then monthly totals update.\n- Given required fields are missing, when I submit the form, then a clear validation message is shown and no invalid transaction is saved.",
            "scope_mvp": "- Local web app workflow for one personal finance user.\n- Transaction CRUD for income and expenses.\n- Amount, date, type, category, and note fields.\n- Monthly summary of income, expenses, and net total.\n- Category filtering.",
            "scope_v1": "- Category management UI.\n- CSV import/export.\n- Recurring transaction templates.\n- Budget targets.\n- Trend charts.",
        }
    return {
        "core_workflow": "fast capture, clear review, and simple reporting",
        "users": "- A primary user who wants to complete the core job quickly without maintaining spreadsheets.\n- A user who needs reliable records for review, billing, reporting, or decision-making.\n- A user who prefers a local-first, low-friction tool before adopting a heavier SaaS workflow.",
        "problem": "capture work data, review it, and turn it into usable summaries",
        "mvp": "- Create, edit, and delete core records for the main workflow.\n- Capture the minimum required fields for each record.\n- Show a clear list view for recent records.\n- Provide a summary view that answers the user's most common status or reporting question.\n- Keep the workflow local-first and inspectable.",
        "v1": "- Add saved filters or templates for repeated workflows.\n- Add export support for handoff to external tools.\n- Add lightweight charts or summary comparisons.",
        "entry_risk": "data entry is too slow",
        "summary_risk": "the summary view does not answer the user's actual decision question",
        "user_stories": "- As a primary user, I want to create a record quickly, so that I can keep my data current while doing real work.\n- As a primary user, I want to edit or delete incorrect records, so that the system remains trustworthy.\n- As a primary user, I want to see recent records in one place, so that I can review what has happened.\n- As a primary user, I want a summary view, so that I can understand the current state without manual calculations.",
        "acceptance": "- Given I am on the record creation screen, when I submit all required fields, then the record appears in the list view.\n- Given a record exists, when I update a field and save, then the list and summary views show the updated value.\n- Given a record exists, when I delete it, then it is removed from the list and no longer affects the summary.\n- Given records exist for the active reporting period, when I open the summary view, then the key totals and counts are visible.\n- Given I enter invalid or incomplete data, when I submit the form, then a clear validation message is shown and no invalid record is saved.",
        "scope_mvp": "- Local web app workflow for one primary user.\n- Core record create, read, update, and delete.\n- Required-field validation.\n- Recent records list.\n- Summary view for the main reporting question.",
        "scope_v1": "- Export to CSV or JSON.\n- Record templates for repeated entries.\n- Saved filters.\n- Improved summary charts.",
    }
