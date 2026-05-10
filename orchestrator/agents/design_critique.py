from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


@dataclass(frozen=True)
class DesignCritiqueEvaluation:
    scores: dict[str, int]
    final_score: int
    max_score: int
    status: str
    verdict: str
    hard_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    domain_type: str = "generic"


@dataclass(frozen=True)
class DesignCritiqueResult:
    critique_md_path: Path
    critique_json_path: Path
    evaluation: DesignCritiqueEvaluation


class DesignCritiqueAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> DesignCritiqueResult:
        project_path = Path(project["path"])
        evaluation = evaluate_design(project_path)
        critique_md_path = project_path / "docs/design/design-critique.md"
        critique_json_path = project_path / "docs/design/design-critique.json"
        critique_md_path.parent.mkdir(parents=True, exist_ok=True)
        critique_md_path.write_text(_render_design_critique_markdown(evaluation), encoding="utf-8")
        critique_json_path.write_text(
            json.dumps(_evaluation_payload(evaluation), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in [critique_md_path, critique_json_path]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="design",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="Design critique report.",
                )
            EventBus(self.db).emit(
                event_type="design.critique_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="design",
                message=f"Critiqued design {evaluation.final_score}/{evaluation.max_score}: {evaluation.status}.",
                payload=_evaluation_payload(evaluation),
            )
        return DesignCritiqueResult(
            critique_md_path=critique_md_path,
            critique_json_path=critique_json_path,
            evaluation=evaluation,
        )


def evaluate_design(project_path: Path) -> DesignCritiqueEvaluation:
    docs = _load_design_docs(project_path)
    product_docs = _load_product_docs(project_path)
    combined = "\n\n".join([*docs.values(), *product_docs.values()])
    domain_type = _domain_type(combined)
    scores = {
        "information_architecture": _score_information_architecture(docs, domain_type),
        "first_screen_value": _score_first_screen_value(docs, domain_type),
        "visual_hierarchy": _score_visual_hierarchy(docs),
        "workflow_efficiency": _score_workflow_efficiency(docs, domain_type),
        "state_completeness": _score_state_completeness(docs, domain_type),
        "asset_integrity": _score_asset_integrity(docs, domain_type),
        "responsive_quality": _score_responsive_quality(docs),
        "domain_fit": _score_domain_fit(docs, domain_type),
    }
    final_score = sum(scores.values())
    max_score = len(scores) * 10
    hard_failures = _hard_failures(docs, scores, domain_type)
    warnings = _warnings(docs, scores, domain_type)
    if final_score < 64:
        hard_failures.append(f"Design score is below gate: {final_score}/{max_score} < 64/{max_score}.")
    status = "pass" if not hard_failures else "fail"
    verdict = _verdict(status, domain_type)
    return DesignCritiqueEvaluation(
        scores=scores,
        final_score=final_score,
        max_score=max_score,
        status=status,
        verdict=verdict,
        hard_failures=hard_failures,
        warnings=warnings,
        domain_type=domain_type,
    )


def _load_design_docs(project_path: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    for relative_path in [
        "docs/design/user-flow.md",
        "docs/design/design-system.md",
        "docs/design/component-spec.md",
        "docs/design/ui-team/ux-flow-lead.md",
        "docs/design/ui-team/visual-design-lead.md",
        "docs/design/ui-team/asset-strategy-lead.md",
        "docs/design/ui-team/visual-qa-lead.md",
        "docs/design/ui-team/design-critic.md",
        "docs/design/ui-team/lead-synthesis.md",
        "docs/design/ui-team-dev-handoff.md",
        "docs/design/reference-to-design-traceability.md",
        "docs/design/screen-level-spec.md",
        "docs/design/template-spec.md",
        "docs/design/visual-qa-checklist.md",
    ]:
        path = project_path / relative_path
        docs[relative_path] = path.read_text(encoding="utf-8") if path.exists() else ""
    return docs


def _load_product_docs(project_path: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    for relative_path in [
        "docs/product/prd.md",
        "docs/product/product-fit.md",
        "docs/product/ux-patterns.md",
        "docs/product/acceptance-criteria.md",
    ]:
        path = project_path / relative_path
        docs[relative_path] = path.read_text(encoding="utf-8") if path.exists() else ""
    return docs


def _domain_type(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ["portfolio", "作品集", "personal site", "personal website"]):
        return "portfolio"
    if any(term in lower for term in ["invoice", "freelance", "billable", "time tracking"]):
        return "freelance"
    if any(term in lower for term in ["expense", "transaction", "income", "支出", "预算"]):
        return "expense"
    return "generic"


def _score_information_architecture(docs: dict[str, str], domain_type: str) -> int:
    text = docs["docs/design/user-flow.md"].lower()
    score = 2
    if "primary flow" in text:
        score += 2
    if "required states" in text:
        score += 1
    domain_terms = {
        "portfolio": ["profile", "project", "theme", "preview", "export"],
        "expense": ["transaction", "monthly", "summary", "edit", "delete"],
        "freelance": ["time", "client", "invoice", "billable", "review"],
        "generic": ["capture", "validate", "review", "output"],
    }
    score += min(5, sum(1 for term in domain_terms[domain_type] if term in text))
    return min(score, 10)


def _score_first_screen_value(docs: dict[str, str], domain_type: str) -> int:
    text = f"{docs['docs/design/user-flow.md']}\n{docs['docs/design/design-system.md']}".lower()
    score = 2
    if any(term in text for term in ["primary visual focus", "primary surface", "first", "start from"]):
        score += 3
    if domain_type == "portfolio" and "preview" in text:
        score += 3
    if domain_type != "portfolio" and any(term in text for term in ["summary", "output", "review"]):
        score += 3
    if "not an admin dashboard" in text or "not a generic" in text:
        score += 2
    return min(score, 10)


def _score_visual_hierarchy(docs: dict[str, str]) -> int:
    text = docs["docs/design/design-system.md"].lower()
    score = 2
    for term in ["visual hierarchy", "primary", "secondary", "tertiary", "typography", "color"]:
        if term in text:
            score += 1
    if "stable" in text or "aspect ratio" in text:
        score += 1
    if "avoid" in text:
        score += 1
    return min(score, 10)


def _score_workflow_efficiency(docs: dict[str, str], domain_type: str) -> int:
    text = f"{docs['docs/design/user-flow.md']}\n{docs['docs/design/component-spec.md']}".lower()
    score = 2
    if text.count("\n1.") or "primary flow" in text:
        score += 2
    domain_terms = {
        "portfolio": ["editor", "upload", "theme", "preview", "export"],
        "expense": ["form", "list", "summary", "edit", "delete"],
        "freelance": ["entry", "client", "invoice", "review", "draft"],
        "generic": ["input", "validate", "review", "output"],
    }
    score += min(5, sum(1 for term in domain_terms[domain_type] if term in text))
    if "blocked by validation" in text or "validation" in text:
        score += 1
    return min(score, 10)


def _score_state_completeness(docs: dict[str, str], domain_type: str) -> int:
    text = f"{docs['docs/design/user-flow.md']}\n{docs['docs/design/component-spec.md']}".lower()
    state_terms = ["empty", "loading", "success", "failure", "validation", "invalid", "editing"]
    score = min(7, sum(1 for term in state_terms if term in text))
    if domain_type == "portfolio":
        score += min(3, sum(1 for term in ["uploading", "replace", "remove", "oversized", "export blocked"] if term in text))
    else:
        score += min(3, sum(1 for term in ["delete", "edit", "filtered"] if term in text))
    return min(score, 10)


def _score_asset_integrity(docs: dict[str, str], domain_type: str) -> int:
    text = docs["docs/design/design-system.md"].lower()
    if domain_type != "portfolio":
        return 8 if "accessibility" in text or "validation" in text else 6
    score = 2
    for term in ["placeholder", "never generate", "fake", "user-owned", "proof"]:
        if term in text:
            score += 2
    return min(score, 10)


def _score_responsive_quality(docs: dict[str, str]) -> int:
    text = docs["docs/design/design-system.md"].lower()
    score = 2
    for term in ["desktop", "mobile", "responsive", "keyboard", "focus", "preview"]:
        if term in text:
            score += 1
    if "stable" in text or "aspect ratio" in text:
        score += 1
    return min(score, 10)


def _score_domain_fit(docs: dict[str, str], domain_type: str) -> int:
    text = "\n".join(docs.values()).lower()
    if "generated by the local deterministic mvp stub" in text:
        return 0
    score = 2
    domain_terms = {
        "portfolio": ["publishing studio", "portfolio", "profile", "project", "theme", "preview", "export"],
        "expense": ["transaction", "monthly", "cash-flow", "summary", "category"],
        "freelance": ["invoice", "billable", "client", "time", "draft"],
        "generic": ["workflow", "artifact", "output", "review"],
    }
    score += min(7, sum(1 for term in domain_terms[domain_type] if term in text))
    if "not an admin dashboard" in text or "not a generic" in text:
        score += 1
    return min(score, 10)


def _hard_failures(docs: dict[str, str], scores: dict[str, int], domain_type: str) -> list[str]:
    text = "\n".join(docs.values()).lower()
    failures: list[str] = []
    if "generated by the local deterministic mvp stub" in text:
        failures.append("Design artifacts are still workflow stubs; run `agent-studio design draft`.")
    for key, label in [
        ("information_architecture", "Information architecture is not specific enough."),
        ("workflow_efficiency", "Workflow is not efficient or concrete enough."),
        ("state_completeness", "Design states are incomplete."),
        ("domain_fit", "Design does not match the product domain."),
    ]:
        if scores[key] < 7:
            failures.append(label)
    if domain_type == "portfolio":
        if not all(term in text for term in ["upload", "theme", "preview", "export"]):
            failures.append("Portfolio design must cover upload, theme selection, preview, and export.")
        if scores["asset_integrity"] < 8:
            failures.append("Portfolio design must protect user-owned proof and AI placeholder boundaries.")
    return failures


def _warnings(docs: dict[str, str], scores: dict[str, int], domain_type: str) -> list[str]:
    warnings: list[str] = []
    for key, score in scores.items():
        if score < 8:
            warnings.append(f"{key.replace('_', ' ').title()} could be stronger: {score}/10.")
    return warnings


def _verdict(status: str, domain_type: str) -> str:
    if status != "pass":
        return "Do not proceed. Fix design artifacts before architecture or implementation."
    if domain_type == "portfolio":
        return "Proceed. The design supports a credible portfolio publishing workflow."
    return "Proceed. The design is specific enough for architecture and implementation."


def _render_design_critique_markdown(evaluation: DesignCritiqueEvaluation) -> str:
    lines = [
        "# Design Critique",
        "",
        f"Domain: `{evaluation.domain_type}`",
        f"Status: {evaluation.status}",
        f"Final score: {evaluation.final_score}/{evaluation.max_score}",
        "",
        f"Verdict: {evaluation.verdict}",
        "",
        "| Dimension | Score |",
        "| --- | --- |",
    ]
    for name, score in evaluation.scores.items():
        lines.append(f"| {name.replace('_', ' ').title()} | {score}/10 |")
    lines.extend(["", "## Hard Failures", ""])
    lines.extend(f"- {failure}" for failure in evaluation.hard_failures)
    if not evaluation.hard_failures:
        lines.append("- None.")
    lines.extend(["", "## Warnings", ""])
    lines.extend(f"- {warning}" for warning in evaluation.warnings)
    if not evaluation.warnings:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Design Judgment Standard",
            "",
            "- A good design makes the product value obvious, supports the real workflow, covers important states, protects user trust, and can be implemented and tested.",
            "- A weak design is generic, decorative, state-poor, or disconnected from the product artifact.",
        ]
    )
    return "\n".join(lines) + "\n"


def _evaluation_payload(evaluation: DesignCritiqueEvaluation) -> dict[str, Any]:
    return {
        "scores": evaluation.scores,
        "final_score": evaluation.final_score,
        "max_score": evaluation.max_score,
        "status": evaluation.status,
        "verdict": evaluation.verdict,
        "hard_failures": evaluation.hard_failures,
        "warnings": evaluation.warnings,
        "domain_type": evaluation.domain_type,
    }
