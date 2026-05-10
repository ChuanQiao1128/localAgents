from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database


@dataclass(frozen=True)
class ProductReviewRole:
    role: str
    score: int
    max_score: int
    verdict: str
    blockers: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class ProductBuildReviewEvaluation:
    domain_type: str
    final_score: int
    max_score: int
    status: str
    verdict: str
    roles: list[ProductReviewRole]
    blockers: list[str]
    strengths: list[str]
    next_actions: list[str]


@dataclass(frozen=True)
class ProductBuildReviewResult:
    review_md_path: Path
    review_json_path: Path
    downstream_team_plan_path: Path
    evaluation: ProductBuildReviewEvaluation


class ProductBuildReviewAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(self, *, project: dict[str, Any], run_id: str | None) -> ProductBuildReviewResult:
        project_path = Path(project["path"])
        evaluation = evaluate_product_build(project_path)
        product_dir = project_path / "docs/product"
        product_dir.mkdir(parents=True, exist_ok=True)
        review_md_path = product_dir / "post-build-product-review.md"
        review_json_path = product_dir / "post-build-product-review.json"
        downstream_team_plan_path = product_dir / "downstream-agent-team-plan.md"

        review_md_path.write_text(_render_review(evaluation), encoding="utf-8")
        review_json_path.write_text(
            json.dumps(_evaluation_payload(evaluation), ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        downstream_team_plan_path.write_text(_render_downstream_team_plan(evaluation), encoding="utf-8")

        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in [review_md_path, review_json_path, downstream_team_plan_path]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="review",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="Post-build product team review.",
                )
            EventBus(self.db).emit(
                event_type="prd.product_build_review_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="review",
                message=f"Reviewed generated product {evaluation.final_score}/{evaluation.max_score}: {evaluation.status}.",
                payload=_evaluation_payload(evaluation),
            )

        return ProductBuildReviewResult(
            review_md_path=review_md_path,
            review_json_path=review_json_path,
            downstream_team_plan_path=downstream_team_plan_path,
            evaluation=evaluation,
        )


def evaluate_product_build(project_path: Path) -> ProductBuildReviewEvaluation:
    docs = _load_inputs(project_path)
    combined = "\n\n".join(docs.values()).lower()
    domain_type = _domain_type(combined)
    if domain_type == "portfolio":
        roles = _portfolio_roles(docs)
        strengths = _portfolio_strengths(docs)
    elif domain_type == "project_tracker":
        roles = _project_tracker_roles(docs)
        strengths = _project_tracker_strengths(docs)
    else:
        roles = _generic_roles(docs, domain_type)
        strengths = _generic_strengths(docs)

    final_score = sum(role.score for role in roles)
    max_score = sum(role.max_score for role in roles)
    blockers = [blocker for role in roles for blocker in role.blockers]
    if domain_type == "portfolio":
        next_actions = _portfolio_next_actions(blockers)
    elif domain_type == "project_tracker":
        next_actions = _project_tracker_next_actions(blockers)
    else:
        next_actions = _generic_next_actions(domain_type)
    if final_score >= 80 and not blockers:
        status = "pass"
        verdict = "The generated product is strong enough to continue into polish and implementation hardening."
    elif final_score >= 70:
        status = "needs_revision"
        verdict = "The generated product is usable, but product value and design depth need another iteration."
    else:
        status = "fail"
        verdict = "The generated product is runnable, but it is not yet a strong product."

    return ProductBuildReviewEvaluation(
        domain_type=domain_type,
        final_score=final_score,
        max_score=max_score,
        status=status,
        verdict=verdict,
        roles=roles,
        blockers=blockers,
        strengths=strengths,
        next_actions=next_actions,
    )


def _load_inputs(project_path: Path) -> dict[str, str]:
    paths = [
        "docs/product/prd.md",
        "docs/product/product-fit.md",
        "docs/product/prd-critique.md",
        "docs/product/reference-products/index.md",
        "docs/product/example-references/top-examples.md",
        "docs/product/example-references/visual-critic.md",
        "docs/product/example-references/multimodal-critic.md",
        "docs/product/feature-patterns.md",
        "docs/product/ux-patterns.md",
        "docs/design/user-flow.md",
        "docs/design/design-system.md",
        "docs/design/component-spec.md",
        "docs/design/design-critique.md",
        "docs/architecture/architecture.md",
        ".agent/tasks/generated-tasks.json",
        "apps/web/index.html",
        "apps/web/styles.css",
        "apps/web/app.js",
        "apps/web/package.json",
        "apps/web/app/page.tsx",
        "apps/web/app/export/page.tsx",
        "apps/web/app/layout.tsx",
        "apps/web/app/globals.css",
        "apps/web/components/nav.tsx",
        "apps/web/components/stats-bar.tsx",
        "apps/web/components/project-card.tsx",
        "apps/web/components/project-detail.tsx",
        "apps/web/components/new-project-modal.tsx",
        "apps/web/lib/store.ts",
        "apps/web/components/portfolio/PortfolioBuilder.tsx",
        "apps/web/components/portfolio/PreviewPanel.tsx",
        "apps/web/components/portfolio/ScreenshotUpload.tsx",
        "apps/web/components/portfolio/AvatarUpload.tsx",
        "apps/web/components/portfolio/steps/ProfileStep.tsx",
        "apps/web/components/portfolio/steps/ProjectsStep.tsx",
        "apps/web/components/portfolio/steps/ThemeStep.tsx",
        "apps/web/components/portfolio/steps/ExportStep.tsx",
        "apps/web/lib/portfolio-store.ts",
        "apps/web/lib/export-html.tsx",
        "apps/web/visual-direction.json",
        "apps/web/v0-source/README.md",
        "apps/web/README.md",
        "tests/portfolio-builder-smoke.md",
        "tests/creator-project-tracker-smoke.md",
        "docs/qa/test-results.md",
        "docs/qa/bugs.md",
        "docs/review/review-report.md",
        "docs/design/ui-team-plan.md",
        "docs/implementation/developer-team-plan.md",
        "docs/qa/qa-team-plan.md",
        "docs/review/review-team-plan.md",
        "docs/design/ui-team/lead-synthesis.md",
        "docs/design/ui-team/ui-team-contracts.json",
        "docs/implementation/implementation-contract.json",
        "docs/implementation/developer-team-task-plan.json",
        "docs/implementation/acceptance-matrix.md",
        ".agent/teams/downstream-agent-contracts.json",
        ".agent/teams/team-maturity.json",
    ]
    loaded: dict[str, str] = {}
    for relative_path in paths:
        path = project_path / relative_path
        loaded[relative_path] = path.read_text(encoding="utf-8") if path.exists() else ""
    return loaded


def _domain_type(text: str) -> str:
    if any(term in text for term in ["creator project tracker", "project tracker", "retro", "retrospective", "task list"]):
        return "project_tracker"
    if any(term in text for term in ["portfolio", "personal website", "personal site", "作品集"]):
        return "portfolio"
    if any(term in text for term in ["invoice", "freelance", "billable", "time tracking"]):
        return "freelance"
    if any(term in text for term in ["expense", "transaction", "income", "支出", "预算"]):
        return "expense"
    return "generic"


def _portfolio_roles(docs: dict[str, str]) -> list[ProductReviewRole]:
    implementation = "\n".join(
        [
            docs["apps/web/index.html"],
            docs["apps/web/styles.css"],
            docs["apps/web/app.js"],
            docs["apps/web/package.json"],
            docs["apps/web/app/page.tsx"],
            docs["apps/web/app/layout.tsx"],
            docs["apps/web/app/globals.css"],
            docs["apps/web/components/portfolio/PortfolioBuilder.tsx"],
            docs["apps/web/components/portfolio/PreviewPanel.tsx"],
            docs["apps/web/components/portfolio/ScreenshotUpload.tsx"],
            docs["apps/web/components/portfolio/AvatarUpload.tsx"],
            docs["apps/web/components/portfolio/steps/ProfileStep.tsx"],
            docs["apps/web/components/portfolio/steps/ProjectsStep.tsx"],
            docs["apps/web/components/portfolio/steps/ThemeStep.tsx"],
            docs["apps/web/components/portfolio/steps/ExportStep.tsx"],
            docs["apps/web/lib/portfolio-store.ts"],
            docs["apps/web/lib/export-html.tsx"],
            docs["apps/web/visual-direction.json"],
            docs["apps/web/v0-source/README.md"],
            docs["apps/web/README.md"],
        ]
    ).lower()
    product_docs = "\n".join(
        [
            docs["docs/product/prd.md"],
            docs["docs/product/product-fit.md"],
            docs["docs/product/prd-critique.md"],
            docs["docs/product/reference-products/index.md"],
            docs["docs/product/example-references/top-examples.md"],
            docs["docs/product/example-references/visual-critic.md"],
            docs["docs/product/example-references/multimodal-critic.md"],
            docs["docs/product/ux-patterns.md"],
        ]
    ).lower()
    design_docs = "\n".join(
        [
            docs["docs/design/user-flow.md"],
            docs["docs/design/design-system.md"],
            docs["docs/design/component-spec.md"],
            docs["docs/design/design-critique.md"],
            docs["docs/design/ui-team-plan.md"],
            docs["docs/design/ui-team/lead-synthesis.md"],
            docs["docs/design/ui-team/ui-team-contracts.json"],
        ]
    ).lower()
    qa_docs = "\n".join([docs["docs/qa/test-results.md"], docs["docs/qa/bugs.md"], docs["docs/review/review-report.md"]]).lower()
    process_docs = "\n".join(
        [
            docs["docs/design/ui-team-plan.md"],
            docs["docs/implementation/developer-team-plan.md"],
            docs["docs/qa/qa-team-plan.md"],
            docs["docs/review/review-team-plan.md"],
            docs["docs/design/ui-team/lead-synthesis.md"],
            docs["docs/design/ui-team/ui-team-contracts.json"],
            docs["docs/implementation/implementation-contract.json"],
            docs["docs/implementation/developer-team-task-plan.json"],
            docs["docs/implementation/acceptance-matrix.md"],
            docs[".agent/teams/downstream-agent-contracts.json"],
            docs[".agent/teams/team-maturity.json"],
        ]
    ).lower()
    has_browser_evidence = any(
        term in qa_docs
        for term in [
            "browser screenshot evidence",
            "desktop screenshot | pass",
            "mobile screenshot | pass",
            ".agent/artifacts/qa/desktop-screenshot.png",
            ".agent/artifacts/qa/mobile-screenshot.png",
            "playwright",
        ]
    )

    return [
        _role(
            "Lead PM",
            20,
            [
                _points(implementation, ["profile", "project", "theme", "preview", "export"], 7),
                _points(product_docs, ["differentiation", "local-first", "publishable", "proof"], 5),
                _points(implementation, ["case study", "outcome", "impact", "metrics", "template"], 5),
                3 if "generated tasks reviewed" in qa_docs else 0,
            ],
            [
                ("case study" not in implementation and "outcome" not in implementation, "The built product does not guide users to create strong case studies or outcome-driven portfolio proof."),
                ("template" not in implementation, "The implementation has no portfolio template or section strategy, so it feels like a form demo."),
            ],
        ),
        _role(
            "UX Product Manager",
            15,
            [
                _points(implementation, ["portfolioform", "projecttemplate", "renderpreview", "moveproject"], 6),
                _points(implementation, ["oversized file", "invalid type", "upload failed", "avatar removed"], 4),
                _points(design_docs, ["empty", "failure", "validation", "responsive"], 3),
                _points(implementation, ["onboarding", "guided", "example", "quality"], 2),
            ],
            [
                ("onboarding" not in implementation and "guided" not in implementation, "The builder does not coach the user through what makes a portfolio persuasive."),
                ("alt" not in implementation or "accessibility" not in implementation, "Asset accessibility and content quality rules are still thin."),
            ],
        ),
        _role(
            "Market Research PM",
            15,
            [
                _points(product_docs, ["reference", "competitor", "pattern", "benchmark"], 5),
                _points(implementation, ["webflow", "framer", "readymag", "contra", "behance", "dribbble"], 4),
                _points(implementation, ["reference", "inspiration", "style", "template"], 4),
                2 if docs["docs/product/reference-products/index.md"] else 0,
                2 if docs["docs/product/example-references/top-examples.md"] else 0,
            ],
            [
                (not any(term in implementation for term in ["webflow", "framer", "behance", "dribbble", "reference", "inspiration"]), "Research does not visibly shape the generated product experience."),
                ("template" not in implementation, "The product does not translate market references into differentiated portfolio templates."),
            ],
        ),
        _role(
            "Visual Product Lead",
            15,
            [
                _points(implementation, ["theme-editorial", "theme-contrast", "theme-compact"], 4),
                _points(implementation, ["avatar", "screenshot", "image preview"], 3),
                _points(docs["apps/web/styles.css"].lower(), ["@media", "aspect-ratio", "focus-visible"], 3),
                _points(implementation, ["generate image", "ai image", "crop", "layout preset", "cover"], 5),
            ],
            [
                (not any(term in implementation for term in ["generate image", "ai image", "crop", "layout preset"]), "The product has upload fields but no strong visual creation workflow."),
                ("screenshot ready" in implementation and "remove screenshot" not in implementation, "Project screenshot lifecycle is incomplete; replacement exists, removal is missing."),
            ],
        ),
        _role(
            "QA/Product Reviewer",
            20,
            [
                _points(qa_docs, ["status: passed", "status: approve", "no blocking issues"], 6),
                _points(implementation, ["escapehtml", "escapeattr", "exportstatichtml", "downloadhtml", "generatehtml"], 5),
                _points(implementation, ["localstorage", "blob", "download"], 4),
                _points(qa_docs, ["browser", "screenshot", "visual", "manual"], 3),
                2 if docs["tests/portfolio-builder-smoke.md"] else 0,
            ],
            [
                (
                    "browser" not in qa_docs and "visual" not in qa_docs and not has_browser_evidence,
                    "QA is static; it does not verify the real browser experience or exported visual quality.",
                ),
                (not has_browser_evidence, "No browser automation or screenshot evidence exists for the generated UI."),
            ],
        ),
        _role(
            "Agent Process Lead",
            15,
            [
                _points(product_docs, ["council", "critique", "product-fit"], 4),
                _points(design_docs, ["critique", "score", "gate"], 3),
                _points(docs["docs/architecture/architecture.md"].lower(), ["product-fit", "design critique", "prd score"], 3),
                _points(process_docs, ["ui team", "developer team", "qa team", "review team"], 5),
            ],
            [
                ("ui team" not in process_docs, "Downstream UI, development, QA, and review are still mostly single-agent steps."),
                ("developer team" not in process_docs, "Implementation does not yet split frontend, UX polish, browser QA, and product review responsibilities."),
            ],
        ),
    ]


def _generic_roles(docs: dict[str, str], domain_type: str) -> list[ProductReviewRole]:
    implementation = "\n".join([docs["apps/web/index.html"], docs["apps/web/styles.css"], docs["apps/web/README.md"]]).lower()
    qa_docs = "\n".join([docs["docs/qa/test-results.md"], docs["docs/review/review-report.md"]]).lower()
    return [
        _role("Lead PM", 25, [_points(implementation, ["generated tasks", "mvp", domain_type], 10)], [("generated tasks" not in implementation, "Generated page does not summarize architecture tasks.")]),
        _role("UX Product Manager", 20, [_points(implementation, ["html", "section", "summary"], 8)], [("form" not in implementation and "workflow" not in implementation, "Generic MVP has no real workflow yet.")]),
        _role("Market Research PM", 15, [_points("\n".join(docs.values()).lower(), ["reference", "benchmark", "competitor"], 6)], [("reference" not in "\n".join(docs.values()).lower(), "Research is not visible in the generated product.")]),
        _role("QA/Product Reviewer", 20, [_points(qa_docs, ["status: passed", "status: approve"], 10)], [("status: failed" in qa_docs, "QA or review failed.")]),
        _role("Agent Process Lead", 20, [_points("\n".join(docs.values()).lower(), ["team", "handoff", "gate"], 8)], [("team" not in "\n".join(docs.values()).lower(), "Downstream agents are not yet team-based.")]),
    ]


def _project_tracker_roles(docs: dict[str, str]) -> list[ProductReviewRole]:
    implementation = "\n".join(
        [
            docs["apps/web/package.json"],
            docs["apps/web/app/page.tsx"],
            docs["apps/web/app/export/page.tsx"],
            docs["apps/web/app/layout.tsx"],
            docs["apps/web/app/globals.css"],
            docs["apps/web/components/nav.tsx"],
            docs["apps/web/components/stats-bar.tsx"],
            docs["apps/web/components/project-card.tsx"],
            docs["apps/web/components/project-detail.tsx"],
            docs["apps/web/components/new-project-modal.tsx"],
            docs["apps/web/lib/store.ts"],
            docs["apps/web/lib/export-html.tsx"],
            docs["apps/web/visual-direction.json"],
            docs["apps/web/v0-source/README.md"],
            docs["tests/creator-project-tracker-smoke.md"],
        ]
    ).lower()
    product_docs = "\n".join(
        [
            docs["docs/product/prd.md"],
            docs["docs/product/product-fit.md"],
            docs["docs/product/prd-critique.md"],
            docs["docs/product/reference-products/index.md"],
            docs["docs/product/feature-patterns.md"],
            docs["docs/product/ux-patterns.md"],
        ]
    ).lower()
    design_docs = "\n".join(
        [
            docs["docs/design/user-flow.md"],
            docs["docs/design/design-system.md"],
            docs["docs/design/component-spec.md"],
            docs["docs/design/design-critique.md"],
            docs["docs/design/ui-team/lead-synthesis.md"],
            docs["apps/web/visual-direction.json"],
            docs["apps/web/v0-source/README.md"],
        ]
    ).lower()
    qa_docs = "\n".join([docs["docs/qa/test-results.md"], docs["docs/qa/bugs.md"], docs["docs/review/review-report.md"]]).lower()
    process_docs = "\n".join(
        [
            docs["docs/design/ui-team/lead-synthesis.md"],
            docs["docs/implementation/implementation-contract.json"],
            docs["docs/implementation/developer-team-task-plan.json"],
            docs["docs/implementation/acceptance-matrix.md"],
            docs[".agent/teams/team-maturity.json"],
        ]
    ).lower()
    has_browser_evidence = "desktop screenshot | pass" in qa_docs and "mobile screenshot | pass" in qa_docs

    return [
        _role(
            "Lead PM",
            15,
            [
                _points(product_docs, ["goal", "status", "task", "screenshot", "export"], 5),
                _points(implementation, ["your projects", "new project", "portfolio export", "retrospective"], 6),
                _points(implementation, ["localstorage", "downloadhtml"], 4),
            ],
            [
                ("new project" not in implementation or "projectdetail" not in implementation, "The core project creation and detail workflow is missing."),
                ("downloadhtml" not in implementation, "Portfolio export is not wired to a real static HTML download."),
            ],
        ),
        _role(
            "UX Product Manager",
            15,
            [
                _points(implementation, ["search projects", "filter by status", "statsbar", "task list"], 5),
                _points(implementation, ["addtask", "toggletask", "deletetask", "retrospective"], 5),
                _points(implementation, ["replace screenshot", "remove screenshot", "screenshot alt"], 3),
                _points(design_docs, ["responsive", "empty", "validation", "handoff"], 2),
            ],
            [
                ("localstorage" not in implementation, "The tracker does not persist user work locally."),
                ("addtask" not in implementation or "retrospective" not in implementation, "Task and retrospective workflows are too thin."),
            ],
        ),
        _role(
            "Market Research PM",
            15,
            [
                _points(product_docs, ["reference", "competitor", "pattern", "benchmark"], 6),
                _points(design_docs, ["minimalist-editorial", "v0 source handoff", "visual direction"], 5),
                _points(implementation, ["portfolio export", "proof", "status", "goal"], 4),
            ],
            [
                ("reference" not in product_docs, "Research does not visibly inform the product decision trail."),
                ("visual direction" not in design_docs and "minimalist-editorial" not in design_docs, "The v0 visual direction is not traceable into implementation."),
            ],
        ),
        _role(
            "Visual Product Lead",
            15,
            [
                _points(implementation, ["globals.css", "rounded", "aspect-video", "statusbadge"], 4),
                _points(implementation, ["screenshoturl", "screenshotalt", "replace screenshot", "remove screenshot"], 4),
                _points(implementation, ["editorial", "minimal", "dark", "theme"], 3),
                4 if has_browser_evidence else 0,
            ],
            [
                (not has_browser_evidence, "No desktop/mobile screenshot evidence exists for the generated UI."),
                ("remove screenshot" not in implementation, "Screenshot lifecycle is missing removal."),
            ],
        ),
        _role(
            "QA/Product Reviewer",
            20,
            [
                _points(qa_docs, ["status: passed", "no blocking issues", "status: approve"], 8),
                _points(implementation, ["escapehtml", "escapeattr", "blob", "downloadhtml"], 5),
                _points(implementation, ["localstorage", "newprojectmodal", "projectdetail"], 4),
                3 if has_browser_evidence else 0,
            ],
            [
                ("status: failed" in qa_docs, "QA or reviewer failed."),
                ("escapehtml" not in implementation or "escapeattr" not in implementation, "Export does not escape user-controlled content."),
            ],
        ),
        _role(
            "Agent Process Lead",
            20,
            [
                _points(process_docs, ["ui team", "developer team", "qa team", "review team"], 8),
                _points(process_docs, ["contract", "acceptance", "handoff", "gate"], 6),
                _points(product_docs, ["council", "critique", "product-fit"], 4),
                _points(design_docs, ["critique", "score"], 2),
            ],
            [
                ("developer team" not in process_docs, "Developer team handoff is missing."),
                ("qa team" not in process_docs and "browser test" not in process_docs, "QA team responsibilities are not explicit."),
            ],
        ),
    ]


def _role(role: str, max_score: int, score_parts: list[int], blockers: list[tuple[bool, str]]) -> ProductReviewRole:
    score = min(max_score, sum(score_parts))
    active_blockers = [message for active, message in blockers if active]
    verdict = "pass" if score >= int(max_score * 0.8) and not active_blockers else "needs work"
    return ProductReviewRole(role=role, score=score, max_score=max_score, verdict=verdict, blockers=active_blockers)


def _points(text: str, terms: list[str], max_points: int) -> int:
    if not terms:
        return 0
    hits = sum(1 for term in terms if term in text)
    return min(max_points, round(max_points * hits / len(terms)))


def _portfolio_strengths(docs: dict[str, str]) -> list[str]:
    implementation = "\n".join([docs["apps/web/index.html"], docs["apps/web/app.js"]]).lower()
    strengths: list[str] = []
    if all(term in implementation for term in ["profile", "project", "theme", "preview", "export"]):
        strengths.append("The core portfolio builder loop exists: profile, projects, themes, preview, and export.")
    if "localstorage" in implementation:
        strengths.append("The app has local save behavior, which fits a local-first MVP.")
    if "escapehtml" in implementation and "escapeattr" in implementation:
        strengths.append("The renderer includes basic escaping for user-controlled content.")
    if not strengths:
        strengths.append("A static web artifact was generated.")
    return strengths


def _generic_strengths(docs: dict[str, str]) -> list[str]:
    if docs["apps/web/index.html"]:
        return ["A static web artifact was generated from architecture tasks."]
    return ["No meaningful implementation strength found yet."]


def _project_tracker_strengths(docs: dict[str, str]) -> list[str]:
    implementation = "\n".join(
        [
            docs["apps/web/app/page.tsx"],
            docs["apps/web/app/export/page.tsx"],
            docs["apps/web/components/project-detail.tsx"],
            docs["apps/web/components/new-project-modal.tsx"],
            docs["apps/web/lib/export-html.tsx"],
        ]
    ).lower()
    strengths: list[str] = []
    if all(term in implementation for term in ["newprojectmodal", "projectdetail", "statsbar"]):
        strengths.append("The tracker has a real project dashboard, creation modal, detail editor, and stats surface.")
    if all(term in implementation for term in ["addtask", "toggletask", "deletetask"]):
        strengths.append("Task CRUD is represented in the project detail workflow.")
    if "localstorage" in implementation:
        strengths.append("The app persists project data locally, matching the local-first MVP constraint.")
    if all(term in implementation for term in ["downloadhtml", "escapehtml", "escapeattr"]):
        strengths.append("Portfolio export is wired to a static HTML download with basic escaping.")
    if not strengths:
        strengths.append("A Next.js project tracker artifact was generated.")
    return strengths


def _portfolio_next_actions(blockers: list[str]) -> list[str]:
    blocker_text = "\n".join(blockers).lower()
    actions = ["Run a Product Improvement Brief before another implementation pass."]
    if any(term in blocker_text for term in ["coach", "case studies", "outcome", "persuasive"]):
        actions.append("Add guided portfolio coaching: proof prompts, outcome examples, weak-content warnings, and a quality rubric.")
    if any(term in blocker_text for term in ["research", "reference", "market", "template"]):
        actions.append("Turn reference research into 3-5 concrete portfolio templates and visible inspiration choices.")
    if any(term in blocker_text for term in ["visual creation", "asset", "accessibility", "screenshot lifecycle"]):
        actions.append("Harden asset workflow: alt text, remove/replace lifecycle, crop/layout controls, and placeholder integrity rules.")
    if any(term in blocker_text for term in ["single-agent", "split", "downstream ui", "developer team"]):
        actions.append("Run UI, Developer, QA, and Review as teams during remediation, then rerun product review.")
    if any(term in blocker_text for term in ["browser automation", "screenshot evidence", "qa is static"]):
        actions.append("Add browser screenshot verification before Reviewer approval.")
    if len(actions) == 1 and not blockers:
        actions.append("Continue polish and implementation hardening; no product-review blockers are open.")
    return actions


def _project_tracker_next_actions(blockers: list[str]) -> list[str]:
    actions = ["Continue implementation hardening for the creator project tracker."]
    blocker_text = "\n".join(blockers).lower()
    if "persist" in blocker_text or "local" in blocker_text:
        actions.append("Fix local save/load before treating the tracker as usable.")
    if "export" in blocker_text:
        actions.append("Wire Portfolio Export to a real escaped static HTML download.")
    if "screenshot" in blocker_text:
        actions.append("Harden screenshot upload, replace, remove, alt text, and responsive preview QA.")
    if "qa" in blocker_text or "reviewer" in blocker_text:
        actions.append("Rerun QA and Reviewer with browser screenshots before final approval.")
    if len(actions) == 1 and not blockers:
        actions.append("No product-review blockers are open; next work should be browser interaction tests and backend/API implementation.")
    return actions


def _generic_next_actions(domain_type: str) -> list[str]:
    return [
        f"Create a domain-specific product review rubric for {domain_type}.",
        "Translate research into visible product patterns before implementation.",
        "Split downstream agents into UI, implementation, QA, and review teams.",
    ]


def _evaluation_payload(evaluation: ProductBuildReviewEvaluation) -> dict[str, Any]:
    return {
        "domain_type": evaluation.domain_type,
        "final_score": evaluation.final_score,
        "max_score": evaluation.max_score,
        "status": evaluation.status,
        "verdict": evaluation.verdict,
        "roles": [
            {
                "role": role.role,
                "score": role.score,
                "max_score": role.max_score,
                "verdict": role.verdict,
                "blockers": role.blockers,
            }
            for role in evaluation.roles
        ],
        "blockers": evaluation.blockers,
        "strengths": evaluation.strengths,
        "next_actions": evaluation.next_actions,
    }


def _render_review(evaluation: ProductBuildReviewEvaluation) -> str:
    role_rows = "\n".join(
        f"| {role.role} | {role.score}/{role.max_score} | {role.verdict} |"
        for role in evaluation.roles
    )
    blockers = "\n".join(f"- {blocker}" for blocker in evaluation.blockers) or "- None."
    strengths = "\n".join(f"- {strength}" for strength in evaluation.strengths)
    next_actions = "\n".join(f"{index}. {action}" for index, action in enumerate(evaluation.next_actions, start=1))
    return f"""# Post-Build Product Review

Status: {evaluation.status}
Score: {evaluation.final_score}/{evaluation.max_score}
Domain: {evaluation.domain_type}

## Lead Verdict

{evaluation.verdict}

## Gate Meaning

- This review does not block the first implementation pass.
- If status is `fail` or `needs_revision`, the build cannot be treated as final or shippable.
- Development may continue only as a remediation iteration against the blockers below.
- UI, Developer, QA, and Review work should run as teams during remediation.

## Role Scores

| Role | Score | Verdict |
| --- | --- | --- |
{role_rows}

## What Is Working

{strengths}

## Why The Product Is Not Good Enough Yet

{blockers}

## Required Next Actions

{next_actions}
"""


def _render_downstream_team_plan(evaluation: ProductBuildReviewEvaluation) -> str:
    return f"""# Downstream Agent Team Plan

The PRD team found the generated product status is `{evaluation.status}` at {evaluation.final_score}/{evaluation.max_score}.

## Required Team Split

### UI Product Team

- UX Flow Lead: turns PRD jobs-to-be-done into screens and states.
- Visual Design Lead: turns references into a concrete visual direction and templates.
- Asset Strategy Lead: defines upload, generated image, placeholder, crop, alt text, and export asset rules.
- Visual QA Lead: verifies screenshots on desktop and mobile before architecture or implementation.

### Architecture Team

- Product Architect: preserves product intent and rejects generic task splits.
- Frontend Architect: defines editor, preview, export, persistence, and asset boundaries.
- Test Architect: writes acceptance checks before implementation starts.

### Developer Team

- Editor Workflow Developer.
- Preview and Export Developer.
- Asset Handling Developer.
- Browser Test Developer.

### QA and Review Team

- Acceptance QA: maps PRD criteria to browser tests.
- Visual QA: checks screenshots and responsive layout.
- Product Reviewer: decides whether the shipped artifact is useful, differentiated, and professional.

## Gate Rule

Do not treat static QA as enough. A generated app can pass static QA and still fail product review.
"""
