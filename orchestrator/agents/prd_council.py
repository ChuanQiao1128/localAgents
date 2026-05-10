from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


@dataclass(frozen=True)
class CouncilRoleResult:
    role_id: str
    name: str
    path: Path
    markdown: str


@dataclass(frozen=True)
class PrdCouncilResult:
    roles: list[CouncilRoleResult]
    debate_path: Path
    debate_markdown: str


@dataclass(frozen=True)
class CouncilPromptPack:
    directory: Path
    index_path: Path
    role_prompt_paths: list[Path]


class PrdCouncilAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def generate(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
        sources: list[dict[str, Any]] | None = None,
        selected_option: Any | None = None,
    ) -> PrdCouncilResult:
        project_path = Path(project["path"])
        if sources is None:
            sources = _load_research_sources(project_path, run_id)
        if selected_option is None:
            selected_option = _load_selected_option(project_path, run_id)
        domain_type = _domain_type(project["idea"])
        roles = _build_role_markdowns(
            domain_type=domain_type,
            idea=project["idea"],
            sources=sources,
            selected_option=selected_option,
        )
        council_dir = project_path / "docs/product/council"
        council_dir.mkdir(parents=True, exist_ok=True)
        written_roles: list[CouncilRoleResult] = []
        for role_id, name, markdown in roles:
            path = council_dir / f"{role_id}.md"
            path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
            written_roles.append(CouncilRoleResult(role_id=role_id, name=name, path=path, markdown=markdown))
        debate_markdown = _render_debate(written_roles, domain_type, selected_option)
        debate_path = project_path / "docs/product/pm-debate.md"
        debate_path.write_text(debate_markdown.rstrip() + "\n", encoding="utf-8")
        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for role in written_roles:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="prd",
                    path=str(role.path.relative_to(project_path)),
                    kind="markdown",
                    summary=f"PRD council role artifact: {role.name}.",
                )
            artifacts.register(
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                path=str(debate_path.relative_to(project_path)),
                kind="markdown",
                summary="PRD council debate synthesis.",
            )
            EventBus(self.db).emit(
                event_type="prd.council_generated",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message=f"Generated PRD council outputs for {len(written_roles)} roles.",
                payload={"roles": [role.role_id for role in written_roles]},
            )
        return PrdCouncilResult(roles=written_roles, debate_path=debate_path, debate_markdown=debate_markdown)

    def prepare_prompt_pack(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
    ) -> CouncilPromptPack:
        project_path = Path(project["path"])
        sources = _load_research_sources(project_path, run_id)
        selected_option = _load_selected_option(project_path, run_id)
        domain_type = _domain_type(project["idea"])
        pack_dir = project_path / ".agent/artifacts/manual_codex/prd_council"
        if run_id:
            pack_dir = pack_dir / run_id
        roles_dir = pack_dir / "roles"
        roles_dir.mkdir(parents=True, exist_ok=True)
        role_prompt_paths: list[Path] = []
        for role_id, role_name in ROLE_ORDER:
            role_dir = roles_dir / role_id
            role_dir.mkdir(parents=True, exist_ok=True)
            prompt_path = role_dir / "prompt.md"
            template_path = role_dir / "response-template.json"
            schema_path = role_dir / "response-schema.json"
            prompt_path.write_text(
                _render_role_prompt(
                    role_id=role_id,
                    role_name=role_name,
                    idea=project["idea"],
                    domain_type=domain_type,
                    sources=sources,
                    selected_option=selected_option,
                ),
                encoding="utf-8",
            )
            template_path.write_text(
                json.dumps(_role_response_template(role_id, role_name), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            schema_path.write_text(
                json.dumps(_role_response_schema(role_id), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            role_prompt_paths.append(prompt_path)
        index_path = pack_dir / "index.md"
        index_path.write_text(_render_prompt_index(role_prompt_paths), encoding="utf-8")
        if self.db and run_id:
            ArtifactStore(self.db).register(
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                path=str(index_path.relative_to(project_path)),
                kind="markdown",
                summary="Manual Codex PRD council prompt pack.",
            )
            EventBus(self.db).emit(
                event_type="prd.council_prompt_prepared",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message="Prepared manual Codex PRD council prompt pack.",
                payload={"roles": [role_id for role_id, _ in ROLE_ORDER]},
            )
        return CouncilPromptPack(directory=pack_dir, index_path=index_path, role_prompt_paths=role_prompt_paths)

    def import_role_outputs(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
        input_dir: Path,
    ) -> PrdCouncilResult:
        project_path = Path(project["path"])
        selected_option = _load_selected_option(project_path, run_id)
        domain_type = _domain_type(project["idea"])
        council_dir = project_path / "docs/product/council"
        council_dir.mkdir(parents=True, exist_ok=True)
        written_roles: list[CouncilRoleResult] = []
        for role_id, role_name in ROLE_ORDER:
            response_path = input_dir / "roles" / role_id / "response.json"
            if not response_path.exists():
                raise FileNotFoundError(f"Missing council role response: {response_path}")
            payload = json.loads(response_path.read_text(encoding="utf-8"))
            markdown = _markdown_from_role_response(
                payload=payload,
                role_id=role_id,
                role_name=role_name,
                idea=project["idea"],
                selected_option=selected_option,
            )
            output_path = council_dir / f"{role_id}.md"
            output_path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
            written_roles.append(
                CouncilRoleResult(role_id=role_id, name=role_name, path=output_path, markdown=markdown)
            )
        debate_markdown = _render_debate(written_roles, domain_type, selected_option)
        debate_path = project_path / "docs/product/pm-debate.md"
        debate_path.write_text(debate_markdown.rstrip() + "\n", encoding="utf-8")
        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for role in written_roles:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="prd",
                    path=str(role.path.relative_to(project_path)),
                    kind="markdown",
                    summary=f"Imported PRD council role artifact: {role.name}.",
                )
            artifacts.register(
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                path=str(debate_path.relative_to(project_path)),
                kind="markdown",
                summary="Imported PRD council debate synthesis.",
            )
            EventBus(self.db).emit(
                event_type="prd.council_imported",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message=f"Imported PRD council outputs for {len(written_roles)} roles.",
                payload={"roles": [role.role_id for role in written_roles]},
            )
        return PrdCouncilResult(roles=written_roles, debate_path=debate_path, debate_markdown=debate_markdown)


ROLE_ORDER = [
    ("market-pm", "Market PM"),
    ("ux-researcher", "UX Researcher"),
    ("product-designer", "Product Designer"),
    ("technical-pm", "Technical PM"),
    ("visual-ai-pm", "Visual/AI PM"),
    ("critic", "Critic"),
]


def _build_role_markdowns(
    *,
    domain_type: str,
    idea: str,
    sources: list[dict[str, Any]],
    selected_option: Any | None,
) -> list[tuple[str, str, str]]:
    option_name = getattr(selected_option, "name", "Unselected MVP direction") if selected_option else "Unselected MVP direction"
    thesis = getattr(selected_option, "thesis", "The product must choose one focused user workflow.") if selected_option else "The product must choose one focused user workflow."
    differentiator = getattr(selected_option, "differentiator", "A focused workflow that produces a useful artifact.") if selected_option else "A focused workflow that produces a useful artifact."
    evidence = _evidence_block(sources)
    if domain_type == "portfolio":
        return [
            (
                "market-pm",
                "Market PM",
                _role_doc(
                    "Market PM",
                    idea,
                    option_name,
                    evidence,
                    [
                        "Portfolio references show demand for fast visual polish, templates, media composition, and a shareable webpage output.",
                        "Theme choice and preview should appear early because users judge portfolio tools by first-impression polish.",
                        "The product should not compete with hosted portfolio platforms on domains, imports, or support in MVP.",
                    ],
                    [
                        "Keep option-b as the default product bet.",
                        "Treat static HTML export as the commercial-quality artifact even though this is local-first.",
                    ],
                ),
            ),
            (
                "ux-researcher",
                "UX Researcher",
                _role_doc(
                    "UX Researcher",
                    idea,
                    option_name,
                    evidence,
                    [
                        "The critical path is profile content -> project gallery -> theme -> preview -> export.",
                        "Every form field should immediately improve the preview or be removed from MVP.",
                        "Upload, replace, remove, invalid type, and oversized image states are part of the product requirement.",
                    ],
                    [
                        "Make preview the center of the workflow, not a final hidden step.",
                        "Require preview/export consistency acceptance tests.",
                    ],
                ),
            ),
            (
                "product-designer",
                "Product Designer",
                _role_doc(
                    "Product Designer",
                    idea,
                    option_name,
                    evidence,
                    [
                        "The UI should feel like a publishing studio, not an admin dashboard.",
                        "Use constrained, polished theme presets with stable spacing, typography, responsive behavior, and accessible contrast.",
                        "Project cards need stable dimensions so screenshots, tags, and links do not shift the layout.",
                    ],
                    [
                        "Hand the UI Agent a visual brief focused on portfolio-grade polish.",
                        "Do not use generic SaaS dashboard styling as the default visual direction.",
                    ],
                ),
            ),
            (
                "technical-pm",
                "Technical PM",
                _role_doc(
                    "Technical PM",
                    idea,
                    option_name,
                    evidence,
                    [
                        "MVP is feasible if image handling is local and themes are limited presets.",
                        "Preview and static export should reuse the same render model to avoid mismatch.",
                        "Advanced hosting, custom domains, and large template libraries should remain non-goals.",
                    ],
                    [
                        "Constrain image upload formats and size limits explicitly.",
                        "Make static export testable as an HTML artifact with image references.",
                    ],
                ),
            ),
            (
                "visual-ai-pm",
                "Visual/AI PM",
                _role_doc(
                    "Visual/AI PM",
                    idea,
                    option_name,
                    evidence,
                    [
                        "AI can raise perceived quality through theme thumbnails, tasteful placeholder covers, background textures, and demo examples.",
                        "AI must not fabricate a user's real screenshots, headshot, work history, client logos, or credentials.",
                        "Generated visuals should be clearly labeled as placeholders until replaced by user-owned assets.",
                    ],
                    [
                        "Generate visual assets from theme, role, project category, and tone.",
                        "Keep consistent aspect ratios for cards, hero images, and theme preview thumbnails.",
                    ],
                ),
            ),
            (
                "critic",
                "Critic",
                _role_doc(
                    "Critic",
                    idea,
                    option_name,
                    evidence,
                    [
                        "The main failure mode is a generic form builder with a portfolio label.",
                        "A PRD that does not make preview fidelity and static export hard requirements should fail the quality gate.",
                        "If AI assets are allowed to imply fake credentials, the product becomes untrustworthy.",
                    ],
                    [
                        "Reject generic CRUD-only PRDs.",
                        "Reject PRDs without upload states, theme constraints, and preview/export tests.",
                    ],
                ),
            ),
        ]
    return [
        (
            "market-pm",
            "Market PM",
            _role_doc(
                "Market PM",
                idea,
                option_name,
                evidence,
                ["Reference products validate the need for a focused repeated workflow.", f"Selected thesis: {thesis}"],
                ["Keep the MVP narrow enough to ship.", "Make the final user artifact explicit."],
            ),
        ),
        (
            "ux-researcher",
            "UX Researcher",
            _role_doc(
                "UX Researcher",
                idea,
                option_name,
                evidence,
                ["The primary path must be obvious and recoverable.", "Empty, validation, success, and error states need explicit behavior."],
                ["Define the workflow before adding automation.", "Make outputs traceable to source inputs."],
            ),
        ),
        (
            "product-designer",
            "Product Designer",
            _role_doc(
                "Product Designer",
                idea,
                option_name,
                evidence,
                ["The interface should prioritize repeated work over marketing layout.", "Use polished, stable controls for core tasks."],
                ["Avoid decorative dashboard bloat.", "Create clear stateful controls for the main workflow."],
            ),
        ),
        (
            "technical-pm",
            "Technical PM",
            _role_doc(
                "Technical PM",
                idea,
                option_name,
                evidence,
                ["MVP is feasible if it stays within local-first CRUD, validation, summaries, and artifacts.", "Integrations should wait until the core workflow is validated."],
                ["Keep acceptance criteria testable.", "Mark risky scope as V1 or future."],
            ),
        ),
        (
            "visual-ai-pm",
            "Visual/AI PM",
            _role_doc(
                "Visual/AI PM",
                idea,
                option_name,
                evidence,
                ["AI visuals should support clarity only where visual assets matter.", "Generated assets should never be treated as user-owned proof."],
                ["Label AI assets as placeholders.", "Use AI only when it improves workflow comprehension."],
            ),
        ),
        (
            "critic",
            "Critic",
            _role_doc(
                "Critic",
                idea,
                option_name,
                evidence,
                ["The main failure mode is generic CRUD without a differentiated artifact.", f"The differentiator must remain testable: {differentiator}"],
                ["Reject vague PRDs.", "Reject PRDs without evidence, tradeoffs, and quality scoring."],
            ),
        ),
    ]


def _render_role_prompt(
    *,
    role_id: str,
    role_name: str,
    idea: str,
    domain_type: str,
    sources: list[dict[str, Any]],
    selected_option: Any | None,
) -> str:
    option_name = getattr(selected_option, "name", "No selected option yet") if selected_option else "No selected option yet"
    thesis = getattr(selected_option, "thesis", "No selected thesis yet") if selected_option else "No selected thesis yet"
    role_brief = _role_brief(role_id, domain_type)
    return f"""# PRD Council Role Prompt: {role_name}

You are one role in a small product research council for Local Agent Dev Studio.

Return JSON only using `response-template.json`. Do not include markdown fences.

## Product Idea

{idea}

## Selected Direction

{option_name}

## Selected Thesis

{thesis}

## Evidence

{_evidence_block(sources, limit=8)}

## Your Role

{role_brief}

## Output Requirements

- Findings must be concrete and evidence-aware.
- Recommendations must be actionable for PRD, UI, architecture, QA, or review.
- Risks must identify how this product could become generic, misleading, unbuildable, or low quality.
- Handoff must state what the Lead PRD Agent should preserve in `pm-debate.md` and `prd.md`.
"""


def _render_prompt_index(role_prompt_paths: list[Path]) -> str:
    lines = [
        "# Manual PRD Council Prompt Pack",
        "",
        "Run each role prompt independently in Codex/ChatGPT. Save each JSON response as `response.json` next to that role's prompt.",
        "",
        "Then import all role responses:",
        "",
        "```bash",
        "./agent-studio prd council --import-dir <this-directory>",
        "```",
        "",
        "## Role Prompts",
        "",
    ]
    for path in role_prompt_paths:
        lines.append(f"- {path}")
    return "\n".join(lines) + "\n"


def _role_response_template(role_id: str, role_name: str) -> dict[str, Any]:
    return {
        "role_id": role_id,
        "role_name": role_name,
        "findings": ["Evidence-backed finding 1", "Evidence-backed finding 2", "Evidence-backed finding 3"],
        "recommendations": ["Actionable recommendation 1", "Actionable recommendation 2"],
        "risks": ["Concrete risk 1", "Concrete risk 2"],
        "handoff": ["Constraint or decision the Lead PRD Agent must preserve"],
    }


def _role_response_schema(role_id: str) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["role_id", "role_name", "findings", "recommendations", "risks", "handoff"],
        "additionalProperties": False,
        "properties": {
            "role_id": {"type": "string", "const": role_id},
            "role_name": {"type": "string"},
            "findings": {"type": "array", "items": {"type": "string"}, "minItems": 2},
            "recommendations": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "risks": {"type": "array", "items": {"type": "string"}, "minItems": 1},
            "handoff": {"type": "array", "items": {"type": "string"}, "minItems": 1},
        },
    }


def _markdown_from_role_response(
    *,
    payload: dict[str, Any],
    role_id: str,
    role_name: str,
    idea: str,
    selected_option: Any | None,
) -> str:
    if payload.get("role_id") != role_id:
        raise ValueError(f"Council role response has wrong role_id: expected {role_id}")
    option_name = getattr(selected_option, "name", "No selected option") if selected_option else "No selected option"
    findings = _string_list(payload.get("findings"), "findings")
    recommendations = _string_list(payload.get("recommendations"), "recommendations")
    risks = _string_list(payload.get("risks"), "risks")
    handoff = _string_list(payload.get("handoff"), "handoff")
    return f"""# {role_name}

## Product Idea

{idea}

## Selected Direction

{option_name}

## Findings

{_bullets(findings)}

## Recommendations

{_bullets(recommendations)}

## Risks

{_bullets(risks)}

## Hand-off

{_bullets(handoff)}
"""


def _string_list(value: Any, field: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"Council role response field `{field}` must be a non-empty list.")
    values = [item.strip() for item in value if isinstance(item, str) and item.strip()]
    if not values:
        raise ValueError(f"Council role response field `{field}` must contain strings.")
    return values


def _role_brief(role_id: str, domain_type: str) -> str:
    portfolio = domain_type == "portfolio"
    briefs = {
        "market-pm": (
            "Study the market/reference products. Identify validated patterns, positioning, pricing/limits, and what the MVP should not copy."
        ),
        "ux-researcher": (
            "Map the user's core workflow. Identify the moments of friction, trust, empty states, validation, and evidence needed to make the product useful."
        ),
        "product-designer": (
            "Define interaction and visual quality standards. Explain what the UI must feel like and which generic design directions should be rejected."
        ),
        "technical-pm": (
            "Constrain the MVP to what can be built and tested. Identify feasibility risks, data/file handling requirements, and what belongs in V1/future."
        ),
        "visual-ai-pm": (
            "Define how AI-generated visuals can help and where they are unsafe. Clarify placeholder policy, asset consistency, and visual prompt inputs."
            if portfolio
            else "Define whether AI-generated visuals are useful. If not, explain why the product should avoid visual generation in MVP."
        ),
        "critic": (
            "Attack the proposal. Identify genericness, missing evidence, unsafe AI claims, weak differentiation, untestable acceptance criteria, and scope creep."
        ),
    }
    return briefs[role_id]


def _role_doc(
    role_name: str,
    idea: str,
    option_name: str,
    evidence: str,
    findings: list[str],
    recommendations: list[str],
) -> str:
    return f"""# {role_name}

## Product Idea

{idea}

## Selected Direction

{option_name}

## Evidence Used

{evidence}

## Findings

{_bullets(findings)}

## Recommendations

{_bullets(recommendations)}

## Hand-off

- Feed these constraints into `docs/product/pm-debate.md`.
- Preserve concrete evidence and tradeoffs in the final PRD.
"""


def _render_debate(roles: list[CouncilRoleResult], domain_type: str, selected_option: Any | None) -> str:
    option_name = getattr(selected_option, "name", "Unselected MVP direction") if selected_option else "Unselected MVP direction"
    differentiator = getattr(selected_option, "differentiator", "A focused workflow that produces a useful artifact.") if selected_option else "A focused workflow that produces a useful artifact."
    lines = ["# PM Debate", "", f"Selected direction: {option_name}", ""]
    for role in roles:
        lines.extend([f"## {role.name}", "", _extract_first_finding(role.markdown), ""])
    if domain_type == "portfolio":
        decision = "Proceed only if preview fidelity, static export, upload states, theme constraints, and AI asset boundaries remain hard requirements."
    else:
        decision = "Proceed only if the PRD remains specific enough to guide UI, architecture, implementation, QA, and review."
    lines.extend(
        [
            "## Debate Resolution",
            "",
            f"- Differentiator: {differentiator}",
            f"- Decision: {decision}",
            "- Council artifacts: `docs/product/council/*.md`.",
        ]
    )
    return "\n".join(lines)


def _extract_first_finding(markdown: str) -> str:
    lines = markdown.splitlines()
    for index, line in enumerate(lines):
        if line.strip() == "## Findings":
            for candidate in lines[index + 1 :]:
                if candidate.startswith("- "):
                    return candidate[2:]
    return "No finding recorded."


def _load_research_sources(project_path: Path, run_id: str | None) -> list[dict[str, Any]]:
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
    try:
        from orchestrator.agents.prd_options import load_selected_option
    except ImportError:
        return None
    return load_selected_option(project_path, run_id)


def _evidence_block(sources: list[dict[str, Any]], limit: int = 4) -> str:
    if not sources:
        return "- Assumption: no external research sources were available."
    lines: list[str] = []
    for index, source in enumerate(sorted(sources, key=lambda item: float(item.get("relevance") or 0.0), reverse=True)[:limit]):
        source_id = source.get("id", f"S{index + 1}")
        title = str(source.get("title") or "Untitled source").replace("|", "/")
        summary = str(source.get("summary") or "No summary available.")
        lines.append(f"- [{source_id}] {title}: {summary}")
    return "\n".join(lines)


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


def _bullets(values: list[str]) -> str:
    return "\n".join(f"- {value}" for value in values)
