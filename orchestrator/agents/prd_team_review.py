from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


@dataclass(frozen=True)
class PrdTeamReviewResult:
    review_path: Path
    optimized_workflow_path: Path
    contracts_json_path: Path


class PrdTeamReviewAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> PrdTeamReviewResult:
        project_path = Path(project["path"])
        product_dir = project_path / "docs/product"
        product_dir.mkdir(parents=True, exist_ok=True)
        artifact_status = _artifact_status(project_path)
        contracts = _team_contracts()
        paths = {
            "review_path": product_dir / "prd-agent-team-review.md",
            "optimized_workflow_path": product_dir / "prd-agent-team-optimized-workflow.md",
            "contracts_json_path": product_dir / "prd-agent-team-contracts.json",
        }
        paths["review_path"].write_text(_render_team_review(artifact_status), encoding="utf-8")
        paths["optimized_workflow_path"].write_text(_render_optimized_workflow(artifact_status), encoding="utf-8")
        paths["contracts_json_path"].write_text(
            json.dumps(contracts, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in paths.values():
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="prd",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="PRD agent team review artifact.",
                )
            EventBus(self.db).emit(
                event_type="prd.team_review_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message="Reviewed and optimized PRD agent team design.",
                payload={"missing_artifacts": [item for item, exists in artifact_status.items() if not exists]},
            )
        return PrdTeamReviewResult(**paths)


def _artifact_status(project_path: Path) -> dict[str, bool]:
    required = [
        "docs/product/research.md",
        "docs/product/research-plan.md",
        "docs/product/source-quality-report.md",
        "docs/product/reference-products/index.md",
        "docs/product/example-references/top-examples.md",
        "docs/product/example-references/visual-critic.md",
        "docs/product/example-references/multimodal-critic.md",
        "docs/product/feature-patterns.md",
        "docs/product/ux-patterns.md",
        "docs/product/benchmark-library/index.md",
        "docs/product/options.md",
        "docs/product/decision.md",
        "docs/product/pm-debate.md",
        "docs/product/prd.md",
        "docs/product/product-fit.md",
        "docs/product/prd-score.md",
        "docs/product/prd-critique.md",
    ]
    return {relative_path: (project_path / relative_path).exists() for relative_path in required}


def _team_contracts() -> dict[str, Any]:
    return {
        "version": 1,
        "agents": [
            {
                "id": "prd_research",
                "role": "Collect external or mock sources and write the first research artifact.",
                "owns": ["docs/product/research.md", ".agent/artifacts/research/*/sources.json"],
                "must_not": ["decide final MVP scope", "write implementation plans"],
                "handoff_to": ["prd_research_v2", "prd_benchmark"],
            },
            {
                "id": "prd_research_v2",
                "role": "Turn source snippets into reference products, feature patterns, UX patterns, source quality, and evidence chain.",
                "owns": [
                    "docs/product/reference-products/**",
                    "docs/product/feature-patterns.md",
                    "docs/product/ux-patterns.md",
                    "docs/product/evidence-chain.md",
                ],
                "must_not": ["invent unsupported competitor details"],
                "handoff_to": ["prd_options", "prd_council", "prd_draft"],
            },
            {
                "id": "prd_benchmark",
                "role": "Provide token-free product-management standards and domain templates.",
                "owns": ["docs/product/benchmark-library/**"],
                "must_not": ["replace project-specific research"],
                "handoff_to": ["prd_options", "prd_draft", "design"],
            },
            {
                "id": "prd_options",
                "role": "Generate multiple product strategy options and a recommendation.",
                "owns": ["docs/product/options.md", "docs/product/pm-review.md"],
                "must_not": ["silently choose a direction without decision record"],
                "handoff_to": ["prd_select"],
            },
            {
                "id": "prd_select",
                "role": "Record the selected direction and decision notes.",
                "owns": ["docs/product/decision.md"],
                "must_not": ["change research evidence"],
                "handoff_to": ["prd_council", "prd_draft"],
            },
            {
                "id": "prd_council",
                "role": "Run multi-role PM debate and preserve tradeoffs.",
                "owns": ["docs/product/council/**", "docs/product/pm-debate.md"],
                "must_not": ["bypass product-fit or quality gates"],
                "handoff_to": ["prd_draft"],
            },
            {
                "id": "prd_draft",
                "role": "Generate final PRD package from selected direction, research, benchmark, and council inputs.",
                "owns": [
                    "docs/product/prd.md",
                    "docs/product/user-stories.md",
                    "docs/product/acceptance-criteria.md",
                    "docs/product/scope.md",
                    "docs/product/prd-quality-score.md",
                ],
                "must_not": ["self-approve quality"],
                "handoff_to": ["prd_product_fit", "prd_score", "prd_critique", "design"],
            },
            {
                "id": "prd_product_fit",
                "role": "Judge whether the product is worth building.",
                "owns": ["docs/product/product-fit.md", "docs/product/product-fit.json"],
                "must_not": ["score document formatting instead of product value"],
                "handoff_to": ["prd_validate", "design"],
            },
            {
                "id": "prd_score",
                "role": "Independently score PRD quality and anti-generic strength.",
                "owns": ["docs/product/prd-score.md", "docs/product/prd-score.json"],
                "must_not": ["trust PRD self-score as the final gate"],
                "handoff_to": ["prd_validate"],
            },
            {
                "id": "prd_critique",
                "role": "Write multi-role critique and Lead PM decision.",
                "owns": ["docs/product/prd-critique.md"],
                "must_not": ["approve hard failures"],
                "handoff_to": ["design", "architecture"],
            },
        ],
        "optimized_order": [
            "research",
            "research-v2",
            "benchmark",
            "options",
            "select",
            "council",
            "draft/import",
            "product-fit",
            "score",
            "critique",
            "validate",
            "design draft",
            "design critique",
        ],
        "quality_rule": "A PRD can move to design only when product-fit, independent score, critique, and validate all pass.",
    }


def _render_team_review(artifact_status: dict[str, bool]) -> str:
    present = sum(1 for exists in artifact_status.values() if exists)
    total = len(artifact_status)
    missing = [path for path, exists in artifact_status.items() if not exists]
    lines = [
        "# PRD Agent Team Review",
        "",
        f"Artifact coverage: {present}/{total}",
        "",
        "## Current Team Shape",
        "",
        "- Research Agent collects sources.",
        "- Research v2 synthesizes reference products, feature patterns, UX patterns, and evidence chain.",
        "- Benchmark Agent provides token-free product-management standards.",
        "- Options Agent creates competing product strategies.",
        "- Selection records the chosen strategy.",
        "- Council Agent preserves multi-role PM debate.",
        "- Draft Agent writes the final PRD package.",
        "- Product-fit, score, and critique agents act as independent gates.",
        "",
        "## Findings",
        "",
        "- The team is now strong enough to prevent generic PRDs from moving forward.",
        "- The main remaining risk is handoff drift: downstream design and architecture must consume PRD gates instead of rewriting product intent.",
        "- Research depth is still source-snippet based unless Tavily Extract, Firecrawl, or manual source imports are added.",
        "- Product-fit and design critique now provide a clear before/after boundary: product value before design quality.",
        "",
        "## Missing Artifacts",
        "",
    ]
    lines.extend(f"- {path}" for path in missing)
    if not missing:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Optimizations Applied",
            "",
            "- Product-fit is now a separate product-value gate.",
            "- Design critique is now a separate design-quality gate.",
            "- PRD team contracts are explicit in JSON for future automation.",
            "- The recommended workflow now includes design draft and design critique after PRD gates.",
        ]
    )
    return "\n".join(lines) + "\n"


def _render_optimized_workflow(artifact_status: dict[str, bool]) -> str:
    return """# Optimized PRD Agent Workflow

## Gate Order

1. `prd research`
2. `prd research-v2`
3. `prd benchmark`
4. `prd options`
5. `prd select <option-id>`
6. `prd council`
7. `prd draft --import`
8. `prd product-fit`
9. `prd score`
10. `prd critique`
11. `prd validate`
12. `design draft`
13. `design critique`

## Why This Order

- Research and benchmark establish evidence and standards before options are generated.
- Options and select prevent the PRD from hiding product strategy tradeoffs.
- Council captures disagreement before the draft becomes authoritative.
- Product-fit asks whether the product is worth building.
- Score and critique ask whether the PRD is strong enough to hand off.
- Design draft converts product intent into UI artifacts.
- Design critique asks whether the design expresses product value clearly and professionally.

## Automation Rule

Do not advance to architecture until `prd validate` and `design critique` both pass.

## Next Optimization

Make Architect Agent consume:

- `docs/product/product-fit.md`
- `docs/product/prd-score.md`
- `docs/product/prd-critique.md`
- `docs/design/design-critique.md`
- `docs/design/component-spec.md`
"""
