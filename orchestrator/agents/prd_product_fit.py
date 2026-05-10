from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


PRODUCT_FIT_INPUTS = [
    "docs/product/research.md",
    "docs/product/competitor-matrix.md",
    "docs/product/pm-debate.md",
    "docs/product/prd.md",
    "docs/product/user-stories.md",
    "docs/product/acceptance-criteria.md",
    "docs/product/scope.md",
    "docs/product/evidence-chain.md",
]


@dataclass(frozen=True)
class ProductFitEvaluation:
    scores: dict[str, int]
    final_score: int
    max_score: int
    status: str
    verdict: str
    hard_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    domain_type: str = "generic"


@dataclass(frozen=True)
class ProductFitResult:
    product_fit_md_path: Path
    product_fit_json_path: Path
    evaluation: ProductFitEvaluation


class PrdProductFitAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> ProductFitResult:
        project_path = Path(project["path"])
        evaluation = evaluate_product_fit(project_path)
        product_fit_md_path = project_path / "docs/product/product-fit.md"
        product_fit_json_path = project_path / "docs/product/product-fit.json"
        product_fit_md_path.parent.mkdir(parents=True, exist_ok=True)
        product_fit_md_path.write_text(_render_product_fit_markdown(evaluation), encoding="utf-8")
        product_fit_json_path.write_text(
            json.dumps(_evaluation_payload(evaluation), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in [product_fit_md_path, product_fit_json_path]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="prd",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="PRD product-fit evaluation.",
                )
            EventBus(self.db).emit(
                event_type="prd.product_fit_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message=f"Evaluated product fit {evaluation.final_score}/{evaluation.max_score}: {evaluation.status}.",
                payload=_evaluation_payload(evaluation),
            )
        return ProductFitResult(
            product_fit_md_path=product_fit_md_path,
            product_fit_json_path=product_fit_json_path,
            evaluation=evaluation,
        )


def evaluate_product_fit(project_path: Path) -> ProductFitEvaluation:
    docs = _load_docs(project_path)
    combined = "\n\n".join(docs.values())
    domain_type = _domain_type(combined)
    scores = {
        "user_pain": _score_user_pain(docs),
        "target_user": _score_target_user(docs),
        "alternatives": _score_alternatives(docs),
        "differentiation": _score_differentiation(docs),
        "core_workflow": _score_core_workflow(docs, domain_type),
        "valuable_artifact": _score_valuable_artifact(docs, domain_type),
        "repeat_use": _score_repeat_use(docs, domain_type),
        "mvp_boundary": _score_mvp_boundary(docs),
    }
    final_score = sum(scores.values())
    max_score = len(scores) * 10
    hard_failures = _hard_failures(docs, scores, domain_type)
    warnings = _warnings(docs, scores, domain_type)
    if final_score < 64:
        hard_failures.append(f"Product-fit score is below gate: {final_score}/{max_score} < 64/{max_score}.")
    status = "pass" if not hard_failures else "fail"
    verdict = _verdict(status, final_score, max_score, domain_type)
    return ProductFitEvaluation(
        scores=scores,
        final_score=final_score,
        max_score=max_score,
        status=status,
        verdict=verdict,
        hard_failures=hard_failures,
        warnings=warnings,
        domain_type=domain_type,
    )


def _load_docs(project_path: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    for relative_path in PRODUCT_FIT_INPUTS:
        path = project_path / relative_path
        docs[relative_path] = path.read_text(encoding="utf-8") if path.exists() else ""
    return docs


def _domain_type(text: str) -> str:
    lower = text.lower()
    if any(term in lower for term in ["portfolio", "作品集", "personal site", "personal website"]):
        return "portfolio"
    if any(term in lower for term in ["invoice", "发票", "freelance", "time tracking", "billable"]):
        return "freelance"
    if any(term in lower for term in ["expense", "记账", "transaction", "income", "支出", "预算"]):
        return "expense"
    return "generic"


def _score_user_pain(docs: dict[str, str]) -> int:
    combined = f"{docs['docs/product/research.md']}\n{docs['docs/product/prd.md']}\n{docs['docs/product/pm-debate.md']}".lower()
    score = 2
    for term in ["problem", "pain", "need", "needs", "core problem", "friction", "risk"]:
        if term in combined:
            score += 1
    if "source" in combined or "assumption" in combined:
        score += 2
    return min(score, 10)


def _score_target_user(docs: dict[str, str]) -> int:
    prd = docs["docs/product/prd.md"].lower()
    stories = docs["docs/product/user-stories.md"].lower()
    score = 2
    if "## users" in prd or "users" in prd:
        score += 2
    score += min(3, stories.count("as a "))
    if not any(term in prd for term in ["all users", "everyone", "anyone"]):
        score += 2
    if any(term in prd for term in ["designer", "developer", "freelancer", "job seeker", "budget-conscious", "solo user"]):
        score += 1
    return min(score, 10)


def _score_alternatives(docs: dict[str, str]) -> int:
    competitor = docs["docs/product/competitor-matrix.md"].lower()
    research = docs["docs/product/research.md"].lower()
    score = 2
    if "competitor" in competitor or "reference" in competitor:
        score += 2
    if "opportunity" in competitor:
        score += 2
    if "caution" in competitor:
        score += 2
    if "reference product" in research or "competitive research" in research:
        score += 2
    return min(score, 10)


def _score_differentiation(docs: dict[str, str]) -> int:
    combined = f"{docs['docs/product/prd.md']}\n{docs['docs/product/competitor-matrix.md']}".lower()
    score = 2
    for term in ["differentiation", "differentiator", "positioning", "narrower", "local-first", "better", "opportunity"]:
        if term in combined:
            score += 1
    if "non-goals" in combined:
        score += 1
    if "scope principle" in combined:
        score += 1
    return min(score, 10)


def _score_core_workflow(docs: dict[str, str], domain_type: str) -> int:
    combined = f"{docs['docs/product/research.md']}\n{docs['docs/product/prd.md']}\n{docs['docs/product/acceptance-criteria.md']}".lower()
    score = 2
    if "workflow" in combined or "flow" in combined or "core problem" in combined:
        score += 2
    domain_terms = {
        "portfolio": ["upload", "project", "theme", "preview", "export"],
        "freelance": ["time", "client", "billable", "invoice", "rate"],
        "expense": ["transaction", "income", "expense", "category", "monthly"],
        "generic": ["capture", "validate", "review", "summary", "output"],
    }
    score += min(5, sum(1 for term in domain_terms[domain_type] if term in combined))
    if "given" in combined and "when" in combined and "then" in combined:
        score += 1
    return min(score, 10)


def _score_valuable_artifact(docs: dict[str, str], domain_type: str) -> int:
    combined = (
        f"{docs['docs/product/prd.md']}\n"
        f"{docs['docs/product/scope.md']}\n"
        f"{docs['docs/product/acceptance-criteria.md']}"
    ).lower()
    score = 2
    if any(term in combined for term in ["artifact", "output", "export", "preview", "summary", "draft", "report"]):
        score += 2
    if domain_type == "portfolio":
        checks = [
            ["publishable", "credible"],
            ["portfolio page", "static html"],
            ["preview"],
            ["export"],
            ["project", "proof"],
        ]
    elif domain_type == "freelance":
        checks = [
            ["invoice-ready", "invoice"],
            ["billable"],
            ["draft"],
            ["total"],
            ["client"],
        ]
    elif domain_type == "expense":
        checks = [
            ["monthly"],
            ["summary", "statistics", "cash-flow"],
            ["income"],
            ["expense"],
            ["net total", "artifact", "inspectable"],
        ]
    else:
        checks = [
            ["artifact"],
            ["summary"],
            ["report"],
            ["output"],
            ["inspectable"],
        ]
    score += min(5, sum(1 for terms in checks if any(term in combined for term in terms)))
    if "not just stored" in combined or "not only saved" in combined:
        score += 1
    return min(score, 10)


def _score_repeat_use(docs: dict[str, str], domain_type: str) -> int:
    combined = f"{docs['docs/product/prd.md']}\n{docs['docs/product/scope.md']}\n{docs['docs/product/user-stories.md']}".lower()
    score = 2
    repeat_terms = ["edit", "delete", "reorder", "save", "saved", "local save", "review", "repeated", "current", "monthly"]
    score += min(5, sum(1 for term in repeat_terms if term in combined))
    if domain_type == "portfolio" and any(term in combined for term in ["multiple projects", "project gallery", "reorder"]):
        score += 2
    if domain_type == "expense" and "monthly" in combined:
        score += 2
    if domain_type == "freelance" and ("invoice" in combined or "billable" in combined):
        score += 2
    return min(score, 10)


def _score_mvp_boundary(docs: dict[str, str]) -> int:
    scope = docs["docs/product/scope.md"].lower()
    prd = docs["docs/product/prd.md"].lower()
    score = 1
    for term in ["mvp", "v1", "future", "non-goals"]:
        if term in scope:
            score += 2
    if "scope principle" in prd or "non-goals" in prd:
        score += 1
    return min(score, 10)


def _hard_failures(docs: dict[str, str], scores: dict[str, int], domain_type: str) -> list[str]:
    combined = "\n\n".join(docs.values()).lower()
    failures: list[str] = []
    for key, label in [
        ("target_user", "Target user is not specific enough."),
        ("core_workflow", "Core workflow is not specific enough."),
        ("mvp_boundary", "MVP boundary is not disciplined enough."),
    ]:
        if scores[key] < 7:
            failures.append(label)
    if scores["valuable_artifact"] < 8:
        failures.append("Final user artifact is not valuable or concrete enough.")
    if scores["differentiation"] < 6:
        failures.append("Differentiation is too weak to justify building this product.")
    if "all users" in combined or "everyone" in combined:
        failures.append("Target user is too broad.")
    if domain_type == "portfolio":
        if not all(term in combined for term in ["upload", "preview", "export"]):
            failures.append("Portfolio product must include upload, preview, and export as core product value.")
        if not any(term in combined for term in ["publishable", "credible", "proof-of-work", "proof"]):
            failures.append("Portfolio product must explain why the output is credible proof-of-work.")
    return failures


def _warnings(docs: dict[str, str], scores: dict[str, int], domain_type: str) -> list[str]:
    warnings: list[str] = []
    for key, score in scores.items():
        if score < 8:
            warnings.append(f"{key.replace('_', ' ').title()} could be stronger: {score}/10.")
    combined = "\n\n".join(docs.values()).lower()
    if "existing" not in combined and "competitor" not in combined and "reference" not in combined:
        warnings.append("Alternative products or current workarounds are under-explained.")
    if domain_type == "portfolio" and "hosting" not in combined:
        warnings.append("Portfolio PRD does not explicitly discuss hosting/domain scope.")
    return warnings


def _verdict(status: str, final_score: int, max_score: int, domain_type: str) -> str:
    if status == "fail":
        return "Do not proceed. Product value is not proven enough for design or architecture."
    if domain_type == "portfolio":
        return "Proceed. The product has a credible portfolio artifact, clear workflow, and disciplined MVP boundary."
    return "Proceed. The product has enough product-fit evidence to continue to design and architecture."


def _render_product_fit_markdown(evaluation: ProductFitEvaluation) -> str:
    lines = [
        "# Product Fit Evaluation",
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
            "## Product Judgment Standard",
            "",
            "- A good product has a specific user, a real workflow pain, an existing workaround to beat, a differentiated wedge, a concrete final artifact, repeat-use potential, and a disciplined MVP boundary.",
            "- A weak product reads like generic CRUD, stores data without producing a valuable artifact, or targets everyone.",
        ]
    )
    return "\n".join(lines) + "\n"


def _evaluation_payload(evaluation: ProductFitEvaluation) -> dict[str, Any]:
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
