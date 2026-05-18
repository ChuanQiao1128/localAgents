from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


Severity = Literal["critical", "high", "medium", "low"]
FindingStatus = Literal["open", "resolved", "not_applicable"]
Verdict = Literal["pass", "pass_with_recommendations", "needs_work", "unsafe"]


@dataclass(frozen=True)
class ProductReviewFinding:
    id: str
    severity: Severity
    status: FindingStatus
    title: str
    evidence: list[str]
    recommendation: str
    generatedChangeId: str | None = None


@dataclass(frozen=True)
class ProductReviewResult:
    schema_version: str
    project_id: str
    project_name: str
    review_id: str
    created_at: str
    score: int
    max_score: int
    verdict: Verdict
    summary: str
    findings: list[ProductReviewFinding]
    inputs_read: list[str]
    artifacts: dict[str, str]


SOURCE_DIRS = ("app", "components", "lib", "docs")
SOURCE_EXTENSIONS = {".ts", ".tsx", ".js", ".jsx", ".md", ".json"}
MAX_FILES = 90
MAX_FILE_CHARS = 30_000
MAX_TOTAL_CHARS = 240_000


def run_product_review(
    project_path: Path,
    *,
    project_id: str,
    project_name: str,
) -> ProductReviewResult:
    """Deterministic runtime-side product review.

    This CLI path is intentionally file-only and does not read .env files or call
    providers. The Studio Console API adds project-level Change Request drafts.
    """

    project_path = project_path.resolve()
    review_id = "product_review_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    created_at = datetime.now(timezone.utc).isoformat()
    source_text, source_files, inputs_read = _collect_source_text(project_path)
    findings = _evaluate(project_id, source_text, source_files)
    score = _score(findings, bool(source_files))
    verdict = _verdict(findings, bool(source_files))
    summary = _summary(verdict, findings)
    review_dir = project_path / ".agent" / "product-reviews" / review_id
    review_dir.mkdir(parents=True, exist_ok=True)
    artifacts = {
        "review_md": str(review_dir / "product-review.md"),
        "review_json": str(review_dir / "product-review.json"),
        "change_plan_md": str(review_dir / "prioritized-change-plan.md"),
    }
    result = ProductReviewResult(
        schema_version="studio.product_review.v2",
        project_id=project_id,
        project_name=project_name,
        review_id=review_id,
        created_at=created_at,
        score=score,
        max_score=100,
        verdict=verdict,
        summary=summary,
        findings=findings,
        inputs_read=inputs_read,
        artifacts=artifacts,
    )
    (review_dir / "product-review.md").write_text(_render_review(result), encoding="utf-8")
    (review_dir / "prioritized-change-plan.md").write_text(
        _render_plan(result), encoding="utf-8"
    )
    (review_dir / "product-review.json").write_text(
        json.dumps(_to_json(result), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    latest = project_path / ".agent" / "product-reviews" / "latest.json"
    latest.write_text(
        json.dumps({"reviewId": review_id, "reviewJson": artifacts["review_json"], "createdAt": created_at}, indent=2),
        encoding="utf-8",
    )
    return result


def _collect_source_text(project_path: Path) -> tuple[str, list[str], list[str]]:
    chunks: list[str] = []
    files: list[str] = []
    inputs_read: list[str] = []
    total = 0
    for dirname in SOURCE_DIRS:
        root = project_path / dirname
        if not root.exists():
            continue
        for file_path in root.rglob("*"):
            if len(files) >= MAX_FILES or total >= MAX_TOTAL_CHARS:
                break
            if not file_path.is_file():
                continue
            if any(part in {"node_modules", ".next", ".git", "coverage"} for part in file_path.parts):
                continue
            if file_path.name.startswith(".env"):
                continue
            if file_path.suffix not in SOURCE_EXTENSIONS:
                continue
            rel = file_path.relative_to(project_path).as_posix()
            try:
                text = file_path.read_text(encoding="utf-8")[:MAX_FILE_CHARS]
            except UnicodeDecodeError:
                continue
            files.append(rel)
            inputs_read.append(rel)
            chunks.append(f"--- {rel} ---\n{text}")
            total += len(text)
    return "\n\n".join(chunks), files, inputs_read


def _evaluate(
    project_id: str,
    source_text: str,
    source_files: list[str],
) -> list[ProductReviewFinding]:
    text = source_text.lower()
    if not source_files:
        return [
            ProductReviewFinding(
                id="GEN-001",
                severity="critical",
                status="open",
                title="Runtime source context is missing",
                evidence=["No app/components/lib/docs source files were readable."],
                recommendation="Prepare or link the runtime project before product review.",
            )
        ]

    is_naturalizer = "naturalizer" in project_id.lower() or "naturalizer" in text
    if not is_naturalizer:
        return [
            ProductReviewFinding(
                id="GEN-002",
                severity="medium",
                status="open",
                title="Generic product review only",
                evidence=["No specialized product rubric matched this project."],
                recommendation="Add a product-specific rubric before relying on automated change planning.",
            )
        ]

    findings: list[ProductReviewFinding] = []
    detector_score_as_success = (
        "selected detector score" in text
        or "score delta" in text
        or "detector score as success" in text
    )
    reference_signal_language = "third-party reference signal" in text or (
        "reference signal" in text and ("reference only" in text or "not optimized" in text)
    )
    findings.append(
        ProductReviewFinding(
            id="NAT-001",
            severity="high" if detector_score_as_success and not reference_signal_language else "low",
            status="open" if detector_score_as_success and not reference_signal_language else "resolved",
            title="Detector score is not treated as the success metric",
            evidence=[
                "Detector-score-as-success wording was found."
                if detector_score_as_success
                else "No prominent detector-score-as-success labels were found."
            ],
            recommendation="Keep detector output informational and never use score reduction as a product success gate.",
        )
    )
    findings.append(
        ProductReviewFinding(
            id="NAT-002",
            severity="low" if reference_signal_language else "high",
            status="resolved" if reference_signal_language else "open",
            title="Detector output is framed as a reference signal",
            evidence=[
                "Reference-signal or reference-only wording is present."
                if reference_signal_language
                else "Detector output is not clearly framed as a third-party reference signal."
            ],
            recommendation="Use labels such as Third-party reference signal and Reference signal change.",
        )
    )
    score_warning = (
        ("reference score increased" in text or "reference signal increased" in text)
        and ("add more user context" in text or "verify claims" in text)
    )
    findings.append(
        ProductReviewFinding(
            id="NAT-003",
            severity="low" if score_warning else "medium",
            status="resolved" if score_warning else "open",
            title="Score increase has a clear user warning",
            evidence=[
                "Score-increase warning asks users to add context or verify claims."
                if score_warning
                else "No clear warning was found for rewritten score higher than original."
            ],
            recommendation="Warn users to add context, verify claims, or manually edit before use.",
        )
    )
    bypass_terms = "bypass" in text or "evasion" in text or "evade" in text
    bypass_disclaimed = (
        "does not claim to bypass" in text
        or "does not measure success by detector evasion" in text
        or "not optimized against" in text
    )
    findings.append(
        ProductReviewFinding(
            id="NAT-004",
            severity="high" if bypass_terms and not bypass_disclaimed else "low",
            status="open" if bypass_terms and not bypass_disclaimed else "resolved",
            title="Bypass or evasion framing is avoided",
            evidence=[
                "Bypass/evasion wording appears without a clear disclaimer."
                if bypass_terms and not bypass_disclaimed
                else "Bypass/evasion risk is absent or explicitly disclaimed."
            ],
            recommendation="Avoid detector-bypass framing; detector output is reference only.",
        )
    )
    anti_fabrication = (
        "claim_not_in_source" in text
        or ("fabricat" in text and "source" in text)
        or ("introduced" in text and "claim" in text)
    )
    findings.append(
        ProductReviewFinding(
            id="NAT-005",
            severity="low" if anti_fabrication else "high",
            status="resolved" if anti_fabrication else "open",
            title="Anti-fabrication guardrail is present",
            evidence=[
                "Anti-fabrication or unsupported-claim checks are present."
                if anti_fabrication
                else "No post-hoc unsupported-claim check was found."
            ],
            recommendation="Flag newly introduced facts, numbers, entities, dates, locations, and experiences.",
        )
    )
    context_fields = (
        "audience" in text
        and "purpose" in text
        and ("preserve" in text or "preservedfacts" in text or "facts or claims" in text or "must keep" in text)
        and (
            "actual work" in text
            or "actualwork" in text
            or "actually did" in text
            or "what actually happened" in text
            or "what i did" in text
        )
        and ("constraintstonenotes" in text or "constraints or tone" in text or "tone notes" in text)
    )
    findings.append(
        ProductReviewFinding(
            id="NAT-006",
            severity="low" if context_fields else "medium",
            status="resolved" if context_fields else "open",
            title="User context capture supports specificity without invention",
            evidence=[
                "Audience, purpose, actual-work, and preserved-facts fields are present."
                if context_fields
                else "The full structured context shape was not found."
            ],
            recommendation="Capture structured user context so specificity comes from user-provided facts.",
        )
    )
    provider_mode = (
        "rewrite provider" in text
        or "detector provider" in text
        or "real provider" in text
        or "mock" in text
        or "codex cli" in text
    )
    findings.append(
        ProductReviewFinding(
            id="NAT-007",
            severity="low" if provider_mode else "medium",
            status="resolved" if provider_mode else "open",
            title="Provider mode is visible without exposing secrets",
            evidence=[
                "Provider mode/readiness labels are present."
                if provider_mode
                else "Provider mode labels were not found."
            ],
            recommendation="Show real/mock/fallback provider mode, never secret values.",
        )
    )
    next_action = (
        ("one rewrite produces one report" in text or "does not auto" in text or "no automatic retry" in text)
        and ("add context" in text or "add more user context" in text or "verify claims" in text or "nextsuggestions" in text)
    )
    findings.append(
        ProductReviewFinding(
            id="NAT-008",
            severity="low" if next_action else "medium",
            status="resolved" if next_action else "open",
            title="Next action is clear when reference signals remain high",
            evidence=[
                "Terminal-state and next-action guidance are present."
                if next_action
                else "Clear next-action guidance for high/increased reference signals was not found."
            ],
            recommendation="Make one rewrite produce one report, then ask for context, verification, or manual edits.",
        )
    )
    return findings or [
        ProductReviewFinding(
            id="NAT-OK",
            severity="low",
            status="resolved",
            title="No hard Naturalizer product blockers detected",
            evidence=["Core diagnosis/context/guardrail/reference-signal checks were present."],
            recommendation="Continue with polish and manual validation.",
        )
    ]


def _score(findings: list[ProductReviewFinding], has_source: bool) -> int:
    if not has_source:
        return 30
    penalty = 0
    for finding in [finding for finding in findings if finding.status == "open"]:
        penalty += {
            "critical": 18,
            "high": 12,
            "medium": 7,
            "low": 2,
        }[finding.severity]
    return max(0, min(100, 100 - penalty))


def _verdict(findings: list[ProductReviewFinding], has_source: bool) -> Verdict:
    if not has_source:
        return "needs_work"
    open_findings = [finding for finding in findings if finding.status == "open"]
    if any(
        finding.severity == "critical"
        or (finding.id == "NAT-004" and finding.severity == "high")
        for finding in open_findings
    ):
        return "unsafe"
    if any(finding.severity == "high" for finding in open_findings):
        return "needs_work"
    if open_findings:
        return "pass_with_recommendations"
    return "pass"


def _summary(verdict: Verdict, findings: list[ProductReviewFinding]) -> str:
    open_findings = [finding for finding in findings if finding.status == "open"]
    if verdict == "needs_work" and any(finding.id == "GEN-001" for finding in open_findings):
        return "Product review is blocked because runtime source context is missing."
    if verdict == "unsafe":
        return "Product review found a product-positioning or safety-boundary risk."
    if verdict == "pass":
        return "No hard product-review blockers were found."
    if verdict == "pass_with_recommendations":
        return f"Product review passed with {len(open_findings)} recommendation(s)."
    critical = sum(1 for f in open_findings if f.severity == "critical")
    high = sum(1 for f in open_findings if f.severity == "high")
    return f"Product review found {critical} critical and {high} high-priority issue(s)."


def _render_review(result: ProductReviewResult) -> str:
    lines = [
        f"# Product Review — {result.project_name}",
        "",
        f"Schema: `{result.schema_version}`",
        f"Review: `{result.review_id}`",
        f"Score: {result.score}/{result.max_score}",
        f"Verdict: `{result.verdict}`",
        f"Inputs read: {len(result.inputs_read)}",
        "",
        "## Summary",
        "",
        result.summary,
        "",
        "## Findings",
        "",
    ]
    for finding in result.findings:
        lines.extend(
            [
                f"### {finding.id} — {finding.title}",
                "",
                f"Severity: `{finding.severity}`",
                f"Status: `{finding.status}`",
                "",
                "Evidence:",
                *[f"- {item}" for item in finding.evidence],
                "",
                f"Recommendation: {finding.recommendation}",
                "",
            ]
        )
    return "\n".join(lines)


def _render_plan(result: ProductReviewResult) -> str:
    lines = [
        f"# Prioritized Change Plan — {result.project_name}",
        "",
        "Use the Studio Console product-review panel to generate project-level Change Request drafts.",
        "",
    ]
    for finding in result.findings:
        if finding.severity in {"critical", "high", "medium"}:
            lines.append(f"- `{finding.id}` {finding.recommendation}")
    return "\n".join(lines) + "\n"


def _to_json(result: ProductReviewResult) -> dict[str, object]:
    data = asdict(result)
    data["findings"] = [asdict(f) for f in result.findings]
    return data
