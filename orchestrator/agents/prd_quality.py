from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


PRD_QUALITY_INPUTS = [
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
class PrdQualityEvaluation:
    scores: dict[str, int]
    final_score: int
    max_score: int
    status: str
    hard_failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    generic_flags: list[str] = field(default_factory=list)
    domain_type: str = "generic"


@dataclass(frozen=True)
class PrdScoreResult:
    score_md_path: Path
    score_json_path: Path
    evaluation: PrdQualityEvaluation


@dataclass(frozen=True)
class PrdCritiqueResult:
    critique_path: Path
    score_result: PrdScoreResult


class PrdScoreAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> PrdScoreResult:
        project_path = Path(project["path"])
        evaluation = evaluate_prd_quality(project_path)
        score_md_path = project_path / "docs/product/prd-score.md"
        score_json_path = project_path / "docs/product/prd-score.json"
        score_md_path.parent.mkdir(parents=True, exist_ok=True)
        score_md_path.write_text(_render_score_markdown(evaluation), encoding="utf-8")
        score_json_path.write_text(
            json.dumps(_evaluation_payload(evaluation), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in [score_md_path, score_json_path]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="prd",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="Independent PRD quality score.",
                )
            EventBus(self.db).emit(
                event_type="prd.score_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message=f"Scored PRD {evaluation.final_score}/{evaluation.max_score}: {evaluation.status}.",
                payload=_evaluation_payload(evaluation),
            )
        return PrdScoreResult(
            score_md_path=score_md_path,
            score_json_path=score_json_path,
            evaluation=evaluation,
        )


class PrdCritiqueAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> PrdCritiqueResult:
        project_path = Path(project["path"])
        score_result = PrdScoreAgent(self.db).run(project=project, run_id=run_id)
        docs = _load_docs(project_path)
        critique_path = project_path / "docs/product/prd-critique.md"
        critique_path.parent.mkdir(parents=True, exist_ok=True)
        critique_path.write_text(_render_critique_markdown(score_result.evaluation, docs), encoding="utf-8")
        if self.db and run_id:
            ArtifactStore(self.db).register(
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                path=str(critique_path.relative_to(project_path)),
                kind="markdown",
                summary="Multi-role PRD critique report.",
            )
            EventBus(self.db).emit(
                event_type="prd.critique_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="prd",
                message=f"Generated PRD critique: {score_result.evaluation.status}.",
                payload={"status": score_result.evaluation.status},
            )
        return PrdCritiqueResult(critique_path=critique_path, score_result=score_result)


def evaluate_prd_quality(project_path: Path) -> PrdQualityEvaluation:
    docs = _load_docs(project_path)
    combined = "\n\n".join(docs.values())
    lower = combined.lower()
    domain_type = _domain_type(combined)
    scores = {
        "research_depth": _score_research_depth(docs),
        "evidence_chain": _score_evidence_chain(docs),
        "differentiation": _score_differentiation(docs),
        "ux_specificity": _score_ux_specificity(docs, domain_type),
        "mvp_scope_discipline": _score_scope_discipline(docs),
        "testability": _score_testability(docs),
        "handoff_readiness": _score_handoff_readiness(project_path, docs),
        "anti_generic": _score_anti_generic(combined, domain_type),
    }
    generic_flags = _generic_flags(combined, domain_type)
    hard_failures = _hard_failures(project_path, docs, scores, domain_type, generic_flags)
    warnings = _warnings(project_path, docs, scores, domain_type, generic_flags)
    final_score = sum(scores.values())
    max_score = len(scores) * 10
    if final_score < 64:
        hard_failures.append(f"Independent PRD score is below gate: {final_score}/{max_score} < 64/{max_score}.")
    status = "pass" if not hard_failures else "fail"
    return PrdQualityEvaluation(
        scores=scores,
        final_score=final_score,
        max_score=max_score,
        status=status,
        hard_failures=hard_failures,
        warnings=warnings,
        generic_flags=generic_flags,
        domain_type=domain_type,
    )


def _load_docs(project_path: Path) -> dict[str, str]:
    docs: dict[str, str] = {}
    for relative_path in PRD_QUALITY_INPUTS:
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


def _score_research_depth(docs: dict[str, str]) -> int:
    research = docs["docs/product/research.md"]
    competitor = docs["docs/product/competitor-matrix.md"]
    source_count = research.lower().count("source")
    row_count = max(0, competitor.count("\n|") - 2)
    score = 3
    if "sources or assumptions" in research.lower():
        score += 2
    if source_count >= 4:
        score += 2
    elif source_count >= 1:
        score += 1
    if row_count >= 4:
        score += 2
    if "research quality" in research.lower() or "assumption" in research.lower():
        score += 1
    return min(score, 10)


def _score_evidence_chain(docs: dict[str, str]) -> int:
    research = docs["docs/product/research.md"].lower()
    score = 2
    if "evidence chain" in research:
        score += 3
    if "prd decision" in research:
        score += 2
    if "qa gate" in research or "downstream gate" in research:
        score += 2
    if "source:" in research or "source [" in research or "assumption" in research:
        score += 1
    return min(score, 10)


def _score_differentiation(docs: dict[str, str]) -> int:
    prd = docs["docs/product/prd.md"].lower()
    competitor = docs["docs/product/competitor-matrix.md"].lower()
    score = 2
    if "differentiation" in prd or "differentiator" in prd:
        score += 3
    if "positioning" in prd or "narrower" in prd or "better" in prd:
        score += 2
    if "opportunity" in competitor and "caution" in competitor:
        score += 2
    if "non-goals" in prd:
        score += 1
    return min(score, 10)


def _score_ux_specificity(docs: dict[str, str], domain_type: str) -> int:
    combined = f"{docs['docs/product/prd.md']}\n{docs['docs/product/acceptance-criteria.md']}".lower()
    state_terms = ["empty", "loading", "error", "success", "validation", "invalid"]
    score = 3 + min(3, sum(1 for term in state_terms if term in combined))
    if "ux quality bar" in combined:
        score += 2
    if domain_type == "portfolio":
        for term in ["upload", "preview", "export", "theme"]:
            if term in combined:
                score += 1
    else:
        for term in ["summary", "filter", "edit", "delete"]:
            if term in combined:
                score += 1
    return min(score, 10)


def _score_scope_discipline(docs: dict[str, str]) -> int:
    scope = docs["docs/product/scope.md"].lower()
    prd = docs["docs/product/prd.md"].lower()
    score = 1
    for term in ["mvp", "v1", "future", "non-goals"]:
        if term in scope:
            score += 2
    if "scope principle" in prd or "non-goals" in prd:
        score += 1
    return min(score, 10)


def _score_testability(docs: dict[str, str]) -> int:
    acceptance = docs["docs/product/acceptance-criteria.md"].lower()
    criteria_count = acceptance.count("given")
    score = 2
    if criteria_count >= 3:
        score += 3
    if "when" in acceptance and "then" in acceptance:
        score += 3
    if any(term in acceptance for term in ["invalid", "error", "required", "missing", "failure"]):
        score += 1
    if any(term in acceptance for term in ["preview", "summary", "export", "total", "saved"]):
        score += 1
    return min(score, 10)


def _score_handoff_readiness(project_path: Path, docs: dict[str, str]) -> int:
    combined = "\n".join(docs.values()).lower()
    handoff_path = project_path / "docs/product/benchmark-library/development-handoff.md"
    if handoff_path.exists():
        combined += "\n" + handoff_path.read_text(encoding="utf-8").lower()
    score = 2
    for term in ["ui", "architecture", "developer", "qa", "review"]:
        if term in combined:
            score += 1
    if "product management operating model" in combined:
        score += 1
    if "acceptance criteria" in combined:
        score += 1
    if "quality gate" in combined or "gate" in combined:
        score += 1
    return min(score, 10)


def _score_anti_generic(text: str, domain_type: str) -> int:
    score = 10 - len(_generic_flags(text, domain_type))
    domain_terms = {
        "portfolio": ["portfolio", "preview", "export", "upload", "theme", "project"],
        "freelance": ["invoice", "billable", "client", "rate", "time"],
        "expense": ["transaction", "income", "expense", "category", "monthly"],
        "generic": ["workflow", "artifact", "source", "summary"],
    }
    if sum(1 for term in domain_terms[domain_type] if term in text.lower()) >= 4:
        score += 1
    return max(0, min(score, 10))


def _generic_flags(text: str, domain_type: str) -> list[str]:
    lower = text.lower()
    flags: list[str] = []
    domain_terms = {
        "portfolio": ["portfolio", "preview", "export", "upload", "theme", "project"],
        "freelance": ["invoice", "billable", "client", "rate", "time"],
        "expense": ["transaction", "income", "expense", "category", "monthly"],
        "generic": ["workflow", "artifact", "source", "summary"],
    }
    domain_term_count = sum(1 for term in domain_terms[domain_type] if term in lower)
    generic_phrases = [
        "easy to use",
        "user-friendly",
        "simple dashboard",
        "manage data",
        "core crud",
        "basic crud",
        "various features",
        "etc.",
    ]
    for phrase in generic_phrases:
        if phrase in lower:
            flags.append(f"Generic phrase: `{phrase}`.")
    if lower.count("crud") >= 3 and domain_term_count < 4:
        flags.append("CRUD appears too often without enough product-specific language.")
    if domain_type == "portfolio" and "profile form" in lower and "publishable" not in lower:
        flags.append("Portfolio PRD reads like a profile form instead of a publishable artifact workflow.")
    return flags


def _hard_failures(
    project_path: Path,
    docs: dict[str, str],
    scores: dict[str, int],
    domain_type: str,
    generic_flags: list[str],
) -> list[str]:
    research = docs["docs/product/research.md"].lower()
    prd = docs["docs/product/prd.md"].lower()
    acceptance = docs["docs/product/acceptance-criteria.md"].lower()
    scope = docs["docs/product/scope.md"].lower()
    failures: list[str] = []
    if "evidence chain" not in research:
        failures.append("Missing evidence chain in docs/product/research.md.")
    if "prd decision" not in research:
        failures.append("Evidence chain must map evidence to PRD decisions.")
    if "product management operating model" not in prd:
        failures.append("Missing Product Management Operating Model in docs/product/prd.md.")
    if "differentiation" not in prd and "differentiator" not in prd:
        failures.append("Missing product differentiation in docs/product/prd.md.")
    if acceptance.count("given") < 3 or "when" not in acceptance or "then" not in acceptance:
        failures.append("Acceptance criteria must include at least 3 Given/When/Then criteria.")
    if not any(term in research for term in ["source:", "source [", "assumption", "benchmark"]):
        failures.append("MVP traceability is missing: use source, assumption, benchmark, or selected-option evidence.")
    if not all(term in scope for term in ["mvp", "v1", "future", "non-goals"]):
        failures.append("Scope must separate MVP, V1, Future, and Non-goals.")
    if scores["handoff_readiness"] < 7:
        failures.append("Handoff readiness is too weak for UI, architecture, development, QA, and review agents.")
    if scores["anti_generic"] < 6:
        failures.append("PRD is too generic to pass the anti-generic gate.")
    if generic_flags and len(generic_flags) >= 3:
        failures.append("Too many generic product phrases remain in the PRD.")
    if domain_type == "portfolio":
        if "ai and visual asset strategy" not in prd:
            failures.append("Portfolio PRD must include an AI And Visual Asset Strategy section.")
        if not all(term in prd for term in ["placeholder", "fabricate", "user"]):
            failures.append("Portfolio PRD must define AI placeholder rules and forbid fabricated user proof.")
        if not all(term in prd for term in ["upload", "preview", "export", "theme"]):
            failures.append("Portfolio PRD must cover upload, preview, export, and theme behavior.")
    return failures


def _warnings(
    project_path: Path,
    docs: dict[str, str],
    scores: dict[str, int],
    domain_type: str,
    generic_flags: list[str],
) -> list[str]:
    warnings: list[str] = []
    if not (project_path / "docs/product/benchmark-library/development-handoff.md").exists():
        warnings.append("Benchmark development handoff is missing; run `agent-studio prd benchmark` for stronger handoff context.")
    for name, score in scores.items():
        if score < 7:
            warnings.append(f"{name.replace('_', ' ').title()} is weak: {score}/10.")
    warnings.extend(generic_flags)
    if domain_type == "portfolio" and "screenshot" not in docs["docs/product/prd.md"].lower():
        warnings.append("Portfolio PRD does not mention screenshot-specific behavior.")
    return warnings


def _render_score_markdown(evaluation: PrdQualityEvaluation) -> str:
    lines = [
        "# Independent PRD Score",
        "",
        f"Domain: `{evaluation.domain_type}`",
        f"Status: {evaluation.status}",
        f"Final score: {evaluation.final_score}/{evaluation.max_score}",
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
    return "\n".join(lines) + "\n"


def _render_critique_markdown(evaluation: PrdQualityEvaluation, docs: dict[str, str]) -> str:
    research_score = evaluation.scores["research_depth"]
    evidence_score = evaluation.scores["evidence_chain"]
    ux_score = evaluation.scores["ux_specificity"]
    test_score = evaluation.scores["testability"]
    handoff_score = evaluation.scores["handoff_readiness"]
    scope_score = evaluation.scores["mvp_scope_discipline"]
    critique = [
        "# PRD Critique",
        "",
        f"Status: {evaluation.status}",
        f"Independent score: {evaluation.final_score}/{evaluation.max_score}",
        "",
        "## Market PM",
        "",
        _role_sentence(
            research_score,
            "Research has enough source/assumption structure to support product judgment.",
            "Research depth is too weak; strengthen sources, assumptions, and competitor evidence before locking PRD.",
        ),
        "",
        "## UX Researcher",
        "",
        _role_sentence(
            ux_score,
            "UX states and core workflow are specific enough for design exploration.",
            "UX is under-specified; define empty, validation, failure, success, and core workflow states.",
        ),
        "",
        "## Technical PM",
        "",
        _role_sentence(
            scope_score,
            "MVP/V1/future/non-goals are separated well enough for architecture planning.",
            "Scope discipline is weak; separate MVP from platform features before architecture.",
        ),
        "",
        "## QA Lead",
        "",
        _role_sentence(
            test_score,
            "Acceptance criteria are testable enough to drive QA.",
            "Acceptance criteria are not concrete enough; add Given/When/Then checks for core and edge states.",
        ),
        "",
        "## Reviewer",
        "",
        _role_sentence(
            handoff_score,
            "The PRD gives downstream agents a usable handoff contract.",
            "Handoff readiness is weak; UI, architecture, developer, QA, and reviewer responsibilities need clearer contracts.",
        ),
        "",
        "## Critic",
        "",
    ]
    if evaluation.hard_failures:
        critique.extend(f"- {failure}" for failure in evaluation.hard_failures)
    else:
        critique.append("- No hard blockers. Continue to design/architecture only if the selected option remains stable.")
    critique.extend(
        [
            "",
            "## Lead PM Decision",
            "",
            _lead_decision(evaluation),
            "",
            "## Evidence Notes",
            "",
            f"- Evidence chain score: {evidence_score}/10.",
            f"- Genericness score: {evaluation.scores['anti_generic']}/10.",
        ]
    )
    return "\n".join(critique) + "\n"


def _role_sentence(score: int, pass_text: str, fail_text: str) -> str:
    return pass_text if score >= 7 else fail_text


def _lead_decision(evaluation: PrdQualityEvaluation) -> str:
    if evaluation.status == "pass":
        return "Proceed. The PRD passes independent quality gates and can be handed to UI/architecture with normal review."
    return "Do not proceed. Fix hard failures, regenerate the PRD, and rerun `agent-studio prd score` before design or architecture."


def _evaluation_payload(evaluation: PrdQualityEvaluation) -> dict[str, Any]:
    return {
        "scores": evaluation.scores,
        "final_score": evaluation.final_score,
        "max_score": evaluation.max_score,
        "status": evaluation.status,
        "hard_failures": evaluation.hard_failures,
        "warnings": evaluation.warnings,
        "generic_flags": evaluation.generic_flags,
        "domain_type": evaluation.domain_type,
    }
