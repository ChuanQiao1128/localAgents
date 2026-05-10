from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.agents.prd_product_fit import PrdProductFitAgent, evaluate_product_fit
from orchestrator.agents.prd_quality import PrdCritiqueAgent, evaluate_prd_quality
from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


PRD_OUTPUT_PATHS = [
    "docs/product/research.md",
    "docs/product/competitor-matrix.md",
    "docs/product/pm-debate.md",
    "docs/product/prd.md",
    "docs/product/user-stories.md",
    "docs/product/acceptance-criteria.md",
    "docs/product/scope.md",
    "docs/product/prd-quality-score.md",
]


@dataclass(frozen=True)
class PrdPromptPack:
    directory: Path
    prompt_path: Path
    template_path: Path
    schema_path: Path


@dataclass(frozen=True)
class PrdValidationResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class ManualCodexPrdAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def prepare_prompt_pack(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
    ) -> PrdPromptPack:
        project_path = Path(project["path"])
        pack_dir = project_path / ".agent/artifacts/manual_codex/prd"
        if run_id:
            pack_dir = pack_dir / run_id
        pack_dir.mkdir(parents=True, exist_ok=True)

        prompt_path = pack_dir / "prompt.md"
        template_path = pack_dir / "response-template.json"
        schema_path = pack_dir / "response-schema.json"

        prompt_path.write_text(
            _render_prompt(
                project["idea"],
                run_id,
                research_context=_read_research_context(project_path),
            ),
            encoding="utf-8",
        )
        template_path.write_text(
            json.dumps(_response_template(project["idea"]), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        schema_path.write_text(
            json.dumps(_response_schema(), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if self.db and run_id:
            ArtifactStore(self.db).register(
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                path=str(prompt_path.relative_to(project_path)),
                kind="markdown",
                summary="Manual Codex PRD prompt pack.",
            )
            EventBus(self.db).emit(
                event_type="prd.prompt_prepared",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message="Prepared manual Codex PRD prompt pack.",
                payload={"path": str(prompt_path.relative_to(project_path))},
            )
        return PrdPromptPack(
            directory=pack_dir,
            prompt_path=prompt_path,
            template_path=template_path,
            schema_path=schema_path,
        )

    def import_result(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
        input_path: Path,
    ) -> PrdValidationResult:
        payload = json.loads(input_path.read_text(encoding="utf-8"))
        artifacts = normalize_prd_payload(payload)
        project_path = Path(project["path"])
        for relative_path, content in artifacts.items():
            target = project_path / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content.rstrip() + "\n", encoding="utf-8")
            if self.db and run_id:
                ArtifactStore(self.db).register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="prd",
                    path=relative_path,
                    kind="markdown",
                    summary=f"Imported manual Codex PRD artifact: {relative_path}.",
                )
        validation = validate_prd_files(project_path)
        if validation.ok and self.db and run_id:
            PrdProductFitAgent(self.db).run(project=project, run_id=run_id)
            PrdCritiqueAgent(self.db).run(project=project, run_id=run_id)
        if self.db and run_id:
            EventBus(self.db).emit(
                event_type="prd.imported",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message="Imported manual Codex PRD result.",
                payload={"ok": validation.ok, "errors": validation.errors},
            )
        return validation


def normalize_prd_payload(payload: dict[str, Any]) -> dict[str, str]:
    if "artifacts" in payload and isinstance(payload["artifacts"], dict):
        raw_artifacts = payload["artifacts"]
    else:
        raw_artifacts = payload

    artifacts: dict[str, str] = {}
    aliases = {
        "research": "docs/product/research.md",
        "research_md": "docs/product/research.md",
        "research.md": "docs/product/research.md",
        "competitor_matrix": "docs/product/competitor-matrix.md",
        "competitor_matrix_md": "docs/product/competitor-matrix.md",
        "competitor-matrix.md": "docs/product/competitor-matrix.md",
        "pm_debate": "docs/product/pm-debate.md",
        "pm_debate_md": "docs/product/pm-debate.md",
        "pm-debate.md": "docs/product/pm-debate.md",
        "prd": "docs/product/prd.md",
        "prd_md": "docs/product/prd.md",
        "prd.md": "docs/product/prd.md",
        "user_stories": "docs/product/user-stories.md",
        "user_stories_md": "docs/product/user-stories.md",
        "user-stories.md": "docs/product/user-stories.md",
        "acceptance_criteria": "docs/product/acceptance-criteria.md",
        "acceptance_criteria_md": "docs/product/acceptance-criteria.md",
        "acceptance-criteria.md": "docs/product/acceptance-criteria.md",
        "scope": "docs/product/scope.md",
        "scope_md": "docs/product/scope.md",
        "scope.md": "docs/product/scope.md",
        "prd_quality_score": "docs/product/prd-quality-score.md",
        "prd_quality_score_md": "docs/product/prd-quality-score.md",
        "prd-quality-score.md": "docs/product/prd-quality-score.md",
    }
    for key, value in raw_artifacts.items():
        if not isinstance(value, str):
            continue
        normalized = aliases.get(key, key)
        if normalized in PRD_OUTPUT_PATHS:
            artifacts[normalized] = value
    missing = [path for path in PRD_OUTPUT_PATHS if path not in artifacts]
    if missing:
        raise ValueError(f"PRD import is missing artifact(s): {', '.join(missing)}")
    return artifacts


def validate_prd_files(project_path: Path) -> PrdValidationResult:
    errors: list[str] = []
    warnings: list[str] = []
    contents: dict[str, str] = {}
    for relative_path in PRD_OUTPUT_PATHS:
        path = project_path / relative_path
        if not path.exists():
            errors.append(f"Missing required file: {relative_path}")
            continue
        text = path.read_text(encoding="utf-8").strip()
        contents[relative_path] = text
        if len(text) < 120:
            errors.append(f"File is too short to be useful: {relative_path}")

    prd = contents.get("docs/product/prd.md", "")
    scope = contents.get("docs/product/scope.md", "")
    acceptance = contents.get("docs/product/acceptance-criteria.md", "")
    research = contents.get("docs/product/research.md", "")
    competitor_matrix = contents.get("docs/product/competitor-matrix.md", "")
    pm_debate = contents.get("docs/product/pm-debate.md", "")
    user_stories = contents.get("docs/product/user-stories.md", "")
    quality_score = contents.get("docs/product/prd-quality-score.md", "")

    _require_terms(
        errors,
        "docs/product/prd.md",
        prd,
        ["background", "users", "mvp", "non-goals", "risks", "product management operating model", "differentiation"],
    )
    _require_terms(
        errors,
        "docs/product/research.md",
        research,
        ["sources or assumptions", "evidence chain"],
    )
    _require_terms(
        errors,
        "docs/product/scope.md",
        scope,
        ["mvp", "v1", "future", "non-goals"],
    )
    _require_terms(
        errors,
        "docs/product/acceptance-criteria.md",
        acceptance,
        ["given", "when", "then"],
    )
    _require_terms(
        errors,
        "docs/product/competitor-matrix.md",
        competitor_matrix,
        ["competitor", "pattern", "opportunity", "source"],
    )
    _require_terms(
        errors,
        "docs/product/pm-debate.md",
        pm_debate,
        ["market pm", "ux researcher", "technical pm", "critic"],
    )
    _require_terms(
        errors,
        "docs/product/prd-quality-score.md",
        quality_score,
        ["research depth", "differentiation", "testability", "status"],
    )
    if acceptance.count("-") + acceptance.count("*") < 3:
        errors.append("docs/product/acceptance-criteria.md must contain at least 3 criteria.")
    if "assumption" not in research.lower() and "source" not in research.lower():
        warnings.append("Research has no explicit sources or assumptions.")
    if "as a " not in user_stories.lower():
        errors.append("docs/product/user-stories.md must include user stories in 'As a ...' format.")
    score = _extract_quality_score(quality_score)
    if score is None:
        errors.append("docs/product/prd-quality-score.md must include a numeric `Final score: N/60` line.")
    elif score < 42:
        errors.append(f"docs/product/prd-quality-score.md final score is below gate: {score}/60 < 42/60.")
    evaluation = evaluate_prd_quality(project_path)
    errors.extend(f"Hard quality gate failed: {failure}" for failure in evaluation.hard_failures)
    warnings.extend(evaluation.warnings)
    product_fit = evaluate_product_fit(project_path)
    errors.extend(f"Product-fit gate failed: {failure}" for failure in product_fit.hard_failures)
    warnings.extend(product_fit.warnings)

    return PrdValidationResult(ok=not errors, errors=errors, warnings=warnings)


def _require_terms(errors: list[str], path: str, text: str, terms: list[str]) -> None:
    lower = text.lower()
    for term in terms:
        if term not in lower:
            errors.append(f"{path} must mention `{term}`.")


def _extract_quality_score(text: str) -> int | None:
    lower = text.lower()
    marker = "final score:"
    if marker not in lower:
        return None
    tail = lower.split(marker, 1)[1].strip()
    number = ""
    for char in tail:
        if char.isdigit():
            number += char
        elif number:
            break
    if not number:
        return None
    return int(number)


def _read_research_context(project_path: Path) -> str | None:
    relative_paths = [
        "docs/product/research.md",
        "docs/product/source-quality-report.md",
        "docs/product/reference-products/index.md",
        "docs/product/example-references/top-examples.md",
        "docs/product/example-references/visual-critic.md",
        "docs/product/example-references/multimodal-critic.md",
        "docs/product/feature-patterns.md",
        "docs/product/ux-patterns.md",
        "docs/product/product-management-benchmarks.md",
        "docs/product/evidence-chain.md",
        "docs/product/benchmark-library/index.md",
        "docs/product/benchmark-library/portfolio-template.md",
        "docs/product/benchmark-library/freelance-template.md",
        "docs/product/benchmark-library/expense-template.md",
        "docs/product/benchmark-library/generic-template.md",
        "docs/product/benchmark-library/quality-gates.md",
        "docs/product/benchmark-library/decision-playbook.md",
        "docs/product/benchmark-library/development-handoff.md",
    ]
    sections: list[str] = []
    for relative_path in relative_paths:
        path = project_path / relative_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8").strip()
        if text:
            sections.append(f"## {relative_path}\n\n{text}")
    return "\n\n".join(sections) or None


def _render_prompt(idea: str, run_id: str | None, research_context: str | None = None) -> str:
    run_line = f"Run id: {run_id}" if run_id else "Run id: not started"
    research_block = ""
    if research_context:
        research_block = f"""
Existing research context:

```markdown
{research_context}
```
"""
    return f"""# Manual Codex PRD Agent Prompt

You are the Product Requirements Agent for Local Agent Dev Studio.

{run_line}

Project idea:

```text
{idea}
```
{research_block}

Generate a complete PRD package for this project. Return **JSON only** using the exact object shape from `response-template.json`.

Required artifacts:

- `research_md`
- `competitor_matrix_md`
- `pm_debate_md`
- `prd_md`
- `user_stories_md`
- `acceptance_criteria_md`
- `scope_md`
- `prd_quality_score_md`

Quality bar:

- Write in clear, implementation-ready product language.
- Produce a market-grade PRD, not a generic CRUD description.
- Extract concrete reference product patterns from research sources: onboarding, content model, visual polish, preview/export flow, pricing or limits when relevant.
- Use mature PM/product-tool benchmarks as operating standards: Aha!-style lifecycle, Dovetail-style evidence synthesis, Productboard-style insight-to-feature traceability, Jira Product Discovery-style option selection, v0/Replit-style prototype handoff, and Claude Code-style gates.
- Include an evidence chain that maps source evidence or assumptions to insight, PRD decision, MVP/non-goal implication, and downstream QA/review gate.
- Define product differentiation: what this product will do better or narrower than the researched references.
- Define UX quality standards for empty states, validation, upload states, preview fidelity, and export behavior when relevant.
- For visual products, define an AI/image asset strategy: where generated images can help, where user-uploaded assets remain the source of truth, and what must never be fabricated.
- Include a competitor matrix with at least 4 researched products or references when sources are available.
- Include a PM debate with Market PM, UX Researcher, Product Designer, Technical PM, Visual/AI PM, and Critic viewpoints.
- Include a PRD quality score with `Final score: N/60` and `Status: pass|fail`; pass requires 42/60 or above.
- Separate MVP, V1, future ideas, and non-goals.
- Acceptance criteria must be testable and use Given/When/Then language.
- Research claims must cite source IDs from the research context when available, such as `[S1]`.
- If a claim cannot be tied to a source, mark it as an assumption.
- Avoid implementation details that belong to the Architect Agent unless needed to define product behavior.
- Do not include markdown fences around the JSON response.
"""


def _response_template(idea: str) -> dict[str, str]:
    return {
        "research_md": f"# Research\n\n## Product Idea\n\n{idea}\n\n## Sources Or Assumptions\n\n- Assumption: ...\n\n## Evidence Chain\n\n- Evidence -> insight -> PRD decision -> downstream gate.\n",
        "competitor_matrix_md": "# Competitor Matrix\n\n| Competitor / Reference | Source | Pattern | Opportunity | Caution |\n| --- | --- | --- | --- | --- |\n| ... | [S1] | ... | ... | ... |\n",
        "pm_debate_md": "# PM Debate\n\n## Market PM\n\n...\n\n## UX Researcher\n\n...\n\n## Product Designer\n\n...\n\n## Technical PM\n\n...\n\n## Visual/AI PM\n\n...\n\n## Critic\n\n...\n\n## Decision\n\n...\n",
        "prd_md": "# Product Requirements\n\n## Background\n\n...\n\n## Product Management Operating Model\n\n...\n\n## Users\n\n...\n\n## MVP\n\n...\n\n## Non-goals\n\n...\n\n## Risks\n\n...\n",
        "user_stories_md": "# User Stories\n\n- As a ..., I want ..., so that ...\n",
        "acceptance_criteria_md": "# Acceptance Criteria\n\n- Given ..., when ..., then ...\n",
        "scope_md": "# Scope\n\n## MVP\n\n...\n\n## V1\n\n...\n\n## Future\n\n...\n\n## Non-goals\n\n...\n",
        "prd_quality_score_md": "# PRD Quality Score\n\n- Research depth: 8/10\n- Differentiation: 8/10\n- UX specificity: 8/10\n- Visual strategy: 8/10\n- Feasibility: 8/10\n- Testability: 8/10\n\nFinal score: 48/60\nStatus: pass\n",
    }


def _response_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "required": [
            "research_md",
            "competitor_matrix_md",
            "pm_debate_md",
            "prd_md",
            "user_stories_md",
            "acceptance_criteria_md",
            "scope_md",
            "prd_quality_score_md",
        ],
        "additionalProperties": False,
        "properties": {
            "research_md": {"type": "string"},
            "competitor_matrix_md": {"type": "string"},
            "pm_debate_md": {"type": "string"},
            "prd_md": {"type": "string"},
            "user_stories_md": {"type": "string"},
            "acceptance_criteria_md": {"type": "string"},
            "scope_md": {"type": "string"},
            "prd_quality_score_md": {"type": "string"},
        },
    }
