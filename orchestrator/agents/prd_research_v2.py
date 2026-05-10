from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database
from orchestrator.agents.reference_seed_library import reference_seeds_for_domain


@dataclass(frozen=True)
class ResearchV2Result:
    research_plan_path: Path
    research_planner_json_path: Path
    source_quality_path: Path
    reference_products_path: Path
    reference_critic_path: Path
    feature_patterns_path: Path
    ux_patterns_path: Path
    product_management_benchmarks_path: Path
    evidence_chain_path: Path
    evidence_gate_path: Path
    screenshots_readme_path: Path
    visual_reference_analysis_path: Path


class PrdResearchV2Agent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
        sources: list[Any] | None = None,
    ) -> ResearchV2Result:
        project_path = Path(project["path"])
        normalized_sources = _normalize_sources(sources) if sources is not None else _load_research_sources(project_path, run_id)
        domain_type = _domain_type(project["idea"])
        product_dir = project_path / "docs/product"
        reference_dir = product_dir / "reference-products"
        screenshot_dir = product_dir / "reference-screenshots"
        reference_dir.mkdir(parents=True, exist_ok=True)
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        paths = {
            "research_plan_path": product_dir / "research-plan.md",
            "research_planner_json_path": product_dir / "research-planner.json",
            "source_quality_path": product_dir / "source-quality-report.md",
            "reference_products_path": reference_dir / "index.md",
            "reference_critic_path": reference_dir / "reference-critic.md",
            "feature_patterns_path": product_dir / "feature-patterns.md",
            "ux_patterns_path": product_dir / "ux-patterns.md",
            "product_management_benchmarks_path": product_dir / "product-management-benchmarks.md",
            "evidence_chain_path": product_dir / "evidence-chain.md",
            "evidence_gate_path": product_dir / "evidence-gate.md",
            "screenshots_readme_path": screenshot_dir / "README.md",
            "visual_reference_analysis_path": product_dir / "visual-reference-analysis.md",
        }
        reference_products = _reference_products(domain_type, normalized_sources)
        _apply_visual_evidence(project_path, reference_products)
        query_plan = _research_query_plan(project["idea"], domain_type)
        evidence_rows = _evidence_chain_rows(domain_type, normalized_sources, reference_products)
        paths["research_plan_path"].write_text(
            _render_research_plan(project["idea"], domain_type, query_plan), encoding="utf-8"
        )
        paths["research_planner_json_path"].write_text(
            json.dumps({"domain_type": domain_type, "query_groups": query_plan}, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        paths["source_quality_path"].write_text(
            _render_source_quality_report(domain_type, normalized_sources), encoding="utf-8"
        )
        paths["reference_products_path"].write_text(
            _render_reference_products(reference_products), encoding="utf-8"
        )
        paths["reference_critic_path"].write_text(
            _render_reference_critic(domain_type, reference_products), encoding="utf-8"
        )
        paths["feature_patterns_path"].write_text(
            _render_feature_patterns(domain_type, reference_products), encoding="utf-8"
        )
        paths["ux_patterns_path"].write_text(
            _render_ux_patterns(domain_type, reference_products), encoding="utf-8"
        )
        paths["product_management_benchmarks_path"].write_text(
            _render_product_management_benchmarks(domain_type), encoding="utf-8"
        )
        paths["evidence_chain_path"].write_text(
            _render_evidence_chain(domain_type, normalized_sources, reference_products, evidence_rows), encoding="utf-8"
        )
        paths["evidence_gate_path"].write_text(
            _render_evidence_gate(evidence_rows), encoding="utf-8"
        )
        paths["screenshots_readme_path"].write_text(
            _render_screenshot_plan(domain_type, reference_products), encoding="utf-8"
        )
        paths["visual_reference_analysis_path"].write_text(
            _render_visual_reference_analysis(domain_type, reference_products), encoding="utf-8"
        )

        structured_path = reference_dir / "reference-products.json"
        structured_path.write_text(json.dumps(reference_products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in [*paths.values(), structured_path]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="research",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="PRD Research v2 artifact.",
                )
            EventBus(self.db).emit(
                event_type="prd.research_v2_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="research",
                message=f"Generated Research v2 artifacts from {len(normalized_sources)} source(s).",
                payload={"source_count": len(normalized_sources), "reference_count": len(reference_products)},
            )

        return ResearchV2Result(**paths)


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


def _normalize_sources(sources: list[Any]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for source in sources:
        if isinstance(source, dict):
            normalized.append(source)
        else:
            normalized.append(
                {
                    "id": getattr(source, "id", f"S{len(normalized) + 1}"),
                    "query": getattr(source, "query", ""),
                    "title": getattr(source, "title", "Untitled"),
                    "url": getattr(source, "url", ""),
                    "summary": getattr(source, "summary", ""),
                    "relevance": getattr(source, "relevance", 0.0),
                    "evidence_type": getattr(source, "evidence_type", "research"),
                }
            )
    return normalized


def _domain_type(idea: str) -> str:
    lower = idea.lower()
    has_portfolio = any(
        term in lower
        for term in ["portfolio", "作品集", "个人网站", "个人主页", "personal site", "personal website"]
    )
    has_tracker = any(
        term in lower
        for term in ["project tracker", "项目", "任务", "复盘", "retrospective", "status", "tasks"]
    )
    if has_portfolio and has_tracker:
        return "creator_project_tracker"
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


def _reference_products(domain_type: str, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for source in sorted(sources, key=lambda item: float(item.get("relevance") or 0.0), reverse=True)[:12]:
        product = {
            "source_id": source.get("id", f"S{len(products) + 1}"),
            "name": _reference_name(source),
            "url": source.get("url", ""),
            "source_quality": _quality_label(source),
            "reference_type": _reference_type(source),
            "detected_patterns": _detected_patterns(domain_type, source),
            "useful_signal": _useful_signal(domain_type, source),
            "caution": _caution(domain_type, source),
            "summary": source.get("summary", ""),
        }
        product.update(_reference_critique(domain_type, source, product))
        products.append(product)
    products.extend(_seed_reference_products(domain_type, products))
    products.sort(key=lambda item: (int(item.get("total_score", 0)), float(_source_relevance(item.get("source_id", ""), sources))), reverse=True)
    return products[:14]


def _seed_reference_products(domain_type: str, existing_products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _is_portfolio_like(domain_type):
        return []
    existing_hosts = {urlparse(str(product.get("url") or "")).netloc.replace("www.", "") for product in existing_products}
    seeded: list[dict[str, Any]] = []
    for seed in reference_seeds_for_domain(domain_type):
        host = urlparse(str(seed.get("url") or "")).netloc.replace("www.", "")
        if host and host in existing_hosts:
            continue
        patterns = _seed_patterns(seed)
        product = {
            "source_id": seed["source_id"],
            "name": seed["name"],
            "url": seed["url"],
            "alternate_urls": seed.get("alternate_urls", []),
            "fallback_queries": seed.get("fallback_queries", []),
            "source_quality": "seed",
            "reference_type": "seed-profile",
            "detected_patterns": patterns,
            "useful_signal": "Seeded strong reference to attempt live verification and visual capture.",
            "caution": "Seed profile is not live evidence until screenshot, extract, or metadata capture succeeds.",
            "summary": "Known portfolio reference seed. Use as a target for verification, not as proof by itself.",
            "evidence_level": "seed_profile",
            "needs_live_verification": True,
            "known_patterns": seed.get("known_patterns", []),
            "scores": {
                "relevance": 18,
                "visual_quality": 14,
                "workflow_depth": 12,
                "information_architecture": 10,
                "proof_quality": 12,
                "mobile_quality": 6,
                "borrowability": 14,
                "risk_adjustment": 8,
            },
            "total_score": 66,
            "critic_verdict": "seed_profile",
            "why_excellent": [
                "Known strong portfolio reference; must be live-verified before becoming evidence-backed.",
            ],
            "borrow": _borrow_patterns(domain_type, {"detected_patterns": patterns}),
            "do_not_copy": _do_not_copy(domain_type, {"detected_patterns": patterns, "caution": "Do not treat seed profile as live evidence."}),
            "prd_implications": _prd_implications(domain_type, {"detected_patterns": patterns}),
        }
        seeded.append(product)
    return seeded


def _seed_patterns(seed: dict[str, Any]) -> list[str]:
    text = " ".join([str(seed.get("name", "")), " ".join(seed.get("known_patterns", []))]).lower()
    patterns: list[str] = []
    if any(term in text for term in ["template", "gallery", "visual", "portfolio"]):
        patterns.append("theme/template selection")
    if any(term in text for term in ["screenshot", "image", "caption", "composition"]):
        patterns.append("media upload and composition")
    if any(term in text for term in ["publish", "preview", "hosting", "website"]):
        patterns.append("preview/publish/export")
    if any(term in text for term in ["case study", "proof", "outcome", "metrics", "credibility", "project"]):
        patterns.append("case-study proof")
    if any(term in text for term in ["profile", "creator", "social", "links"]):
        patterns.append("contact/social credibility")
    if any(term in text for term in ["hosting", "domain", "marketplace", "platform"]):
        patterns.append("hosting/domain/platform breadth")
    return patterns or ["general product reference"]


def _source_relevance(source_id: str, sources: list[dict[str, Any]]) -> float:
    for source in sources:
        if str(source.get("id")) == str(source_id):
            return float(source.get("relevance") or 0.0)
    return 0.0


def _apply_visual_evidence(project_path: Path, products: list[dict[str, Any]]) -> None:
    manifest_path = project_path / "docs/product/reference-screenshots/manifest.json"
    if not manifest_path.exists():
        return
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return
    if not isinstance(manifest, list):
        return
    by_source: dict[str, list[dict[str, Any]]] = {}
    for item in manifest:
        if isinstance(item, dict) and item.get("source_id"):
            by_source.setdefault(str(item["source_id"]), []).append(item)
    for product in products:
        evidence = by_source.get(str(product.get("source_id")), [])
        if not evidence:
            continue
        product["visual_evidence"] = evidence
        if any(item.get("status") == "captured" for item in evidence):
            product["evidence_level"] = "screenshot"
            if product.get("critic_verdict") == "seed_profile":
                product["critic_verdict"] = "strong_reference"
                product["source_quality"] = "verified_seed"
                product["total_score"] = max(int(product.get("total_score") or 0), 82)
                product["why_excellent"] = [
                    "Seeded strong reference was live-verified with desktop/mobile screenshot evidence.",
                    *product.get("why_excellent", []),
                ]
        elif any(item.get("status") == "metadata_only" for item in evidence):
            product["evidence_level"] = "page_metadata"
            if product.get("critic_verdict") == "seed_profile":
                product["critic_verdict"] = "usable_reference"
                product["source_quality"] = "metadata_verified_seed"
                product["total_score"] = max(int(product.get("total_score") or 0), 60)


def _reference_critique(domain_type: str, source: dict[str, Any], product: dict[str, Any]) -> dict[str, Any]:
    text = f"{source.get('title', '')} {source.get('url', '')} {source.get('summary', '')}".lower()
    patterns = set(product.get("detected_patterns") or [])
    is_portfolio_like = _is_portfolio_like(domain_type)
    relevance_terms = (
        ["portfolio", "case study", "designer", "creator", "personal website", "framer", "webflow", "readymag", "semplice", "contra", "behance", "作品集"]
        if is_portfolio_like
        else ["workflow", "dashboard", "tracker", "report", "summary", "template"]
    )
    generic_penalty_terms = ["best project management software", "ppm", "enterprise", "resource management", "budget", "timesheet"]
    relevance_score = _term_score(text, relevance_terms, 20)
    if is_portfolio_like and any(term in text for term in generic_penalty_terms):
        relevance_score = max(0, relevance_score - 8)
    visual_quality = _term_score(text, ["template", "examples", "gallery", "visual", "design", "website", "screenshot", "mobile", "award"], 15)
    workflow_depth = _term_score(text, ["upload", "preview", "publish", "export", "edit", "template", "case study", "tasks", "status"], 15)
    information_architecture = _term_score(text, ["profile", "project", "card", "sections", "layout", "dashboard", "template"], 12)
    proof_quality = _term_score(text, ["case study", "outcome", "metrics", "proof", "links", "screenshot", "portfolio", "behance", "contra"], 14)
    mobile_quality = _term_score(text, ["mobile", "responsive", "website", "template"], 8)
    borrowability = min(16, len(patterns) * 4 + _term_score(text, ["mvp", "template", "preview", "export", "workflow"], 8))
    risk = 10
    if any(term in text for term in ["pricing", "enterprise", "all-in-one", "crm", "resource management"]):
        risk -= 4
    if any(term in text for term in ["fake", "testimonial", "client logo"]):
        risk -= 3
    total = relevance_score + visual_quality + workflow_depth + information_architecture + proof_quality + mobile_quality + borrowability + max(0, risk)
    verdict = "strong_reference" if total >= 72 else "usable_reference" if total >= 52 else "weak_or_generic"
    return {
        "scores": {
            "relevance": relevance_score,
            "visual_quality": visual_quality,
            "workflow_depth": workflow_depth,
            "information_architecture": information_architecture,
            "proof_quality": proof_quality,
            "mobile_quality": mobile_quality,
            "borrowability": borrowability,
            "risk_adjustment": max(0, risk),
        },
        "total_score": total,
        "critic_verdict": verdict,
        "why_excellent": _why_excellent(domain_type, product, verdict),
        "borrow": _borrow_patterns(domain_type, product),
        "do_not_copy": _do_not_copy(domain_type, product),
        "prd_implications": _prd_implications(domain_type, product),
    }


def _term_score(text: str, terms: list[str], max_points: int) -> int:
    if not terms:
        return 0
    hits = sum(1 for term in terms if term in text)
    return min(max_points, round(max_points * hits / max(1, min(len(terms), 5))))


def _is_portfolio_like(domain_type: str) -> bool:
    return domain_type in {"portfolio", "creator_project_tracker"}


def _why_excellent(domain_type: str, product: dict[str, Any], verdict: str) -> list[str]:
    patterns = set(product.get("detected_patterns") or [])
    if verdict == "weak_or_generic":
        return ["Potentially useful for market language, but too generic to drive core PRD decisions."]
    reasons: list[str] = []
    if _is_portfolio_like(domain_type):
        if "theme/template selection" in patterns:
            reasons.append("It shows how visual presets reduce blank-page pressure and make polish visible early.")
        if "media upload and composition" in patterns:
            reasons.append("It treats project imagery as part of the proof workflow rather than decoration.")
        if "preview/publish/export" in patterns:
            reasons.append("It makes the user's useful output inspectable before publishing or exporting.")
        if "case-study proof" in patterns:
            reasons.append("It links project narrative, proof, and outcome into a stronger credibility story.")
    if not reasons:
        reasons.append("It contributes a repeated market pattern that can be checked against the MVP boundary.")
    return reasons


def _borrow_patterns(domain_type: str, product: dict[str, Any]) -> list[str]:
    patterns = set(product.get("detected_patterns") or [])
    borrowed: list[str] = []
    if _is_portfolio_like(domain_type):
        if "theme/template selection" in patterns:
            borrowed.append("Offer 2-3 distinct visual directions before implementation.")
        if "media upload and composition" in patterns:
            borrowed.append("Bind screenshot, title, role, outcome, metrics, and links in one project card workflow.")
        if "preview/publish/export" in patterns:
            borrowed.append("Make preview and static export the primary payoff, not a secondary utility.")
        if "case-study proof" in patterns:
            borrowed.append("Coach users to write problem, process, outcome, and proof for each project.")
    return borrowed or ["Use only as background evidence unless another strong reference repeats the pattern."]


def _do_not_copy(domain_type: str, product: dict[str, Any]) -> list[str]:
    warnings = [product.get("caution", "Validate scope before copying.")]
    if _is_portfolio_like(domain_type):
        warnings.extend(
            [
                "Do not copy large hosting/domain/template-marketplace scope into MVP.",
                "Do not fabricate screenshots, client logos, testimonials, work history, or credentials.",
            ]
        )
    return list(dict.fromkeys(warnings))


def _prd_implications(domain_type: str, product: dict[str, Any]) -> list[str]:
    patterns = set(product.get("detected_patterns") or [])
    implications: list[str] = []
    if _is_portfolio_like(domain_type):
        if "theme/template selection" in patterns:
            implications.append("PRD should require visual direction selection and preview evidence.")
        if "media upload and composition" in patterns:
            implications.append("PRD should require image lifecycle, alt text, and proof fields.")
        if "preview/publish/export" in patterns:
            implications.append("PRD should require preview/export fidelity checks.")
        if "case-study proof" in patterns:
            implications.append("PRD should require project story coaching or quality warnings.")
    return implications or ["Keep as context; do not promote to MVP unless repeated by stronger references."]


def _reference_name(source: dict[str, Any]) -> str:
    title = str(source.get("title") or "").strip()
    if title:
        for delimiter in [" | ", " - ", " – ", " — "]:
            if delimiter in title:
                return title.split(delimiter, 1)[0].strip()
        return title[:90]
    parsed = urlparse(str(source.get("url") or ""))
    return parsed.netloc or "Untitled reference"


def _reference_type(source: dict[str, Any]) -> str:
    text = f"{source.get('title', '')} {source.get('url', '')} {source.get('summary', '')}".lower()
    if any(term in text for term in ["price", "pricing", "付费", "免费", "月", "plan"]):
        return "pricing-or-limits"
    if any(term in text for term in ["youtube", "tutorial", "教程", "guide"]):
        return "tutorial"
    if any(term in text for term in ["forum", "reddit", "社区"]):
        return "community"
    if any(term in text for term in ["portfolio", "作品集", "builder", "website", "网页"]):
        return "competitor-or-reference"
    return "reference"


def _quality_label(source: dict[str, Any]) -> str:
    relevance = float(source.get("relevance") or 0.0)
    summary_length = len(str(source.get("summary") or ""))
    if relevance >= 0.75 and summary_length >= 80:
        return "high"
    if relevance >= 0.45 or summary_length >= 60:
        return "medium"
    return "low"


def _detected_patterns(domain_type: str, source: dict[str, Any]) -> list[str]:
    text = f"{source.get('title', '')} {source.get('summary', '')}".lower()
    patterns: list[str] = []
    if _is_portfolio_like(domain_type):
        checks = [
            ("theme/template selection", ["theme", "template", "主题", "模板"]),
            ("media upload and composition", ["upload", "image", "media", "图片", "图像", "截图", "影片"]),
            ("preview/publish/export", ["preview", "publish", "export", "website", "预览", "生成", "网页"]),
            ("case-study proof", ["case study", "proof", "outcome", "metrics", "project card", "作品案例", "成果"]),
            ("project tracking workflow", ["project tracker", "tasks", "status", "goal", "retrospective", "任务", "状态", "复盘"]),
            ("contact/social credibility", ["contact", "social", "email", "联系方式", "links"]),
            ("AI-assisted creation", ["ai", "cursor", "一键生成", "自动生成"]),
            ("hosting/domain/platform breadth", ["domain", "hosting", "behance", "域名", "support"]),
            ("pricing or plan limits", ["free", "paid", "price", "免费", "付费", "美金", "无限"]),
        ]
    else:
        checks = [
            ("fast capture", ["quick", "fast", "capture", "entry", "快速", "记录"]),
            ("summary/reporting", ["summary", "report", "dashboard", "统计", "报告"]),
            ("workflow completeness", ["workflow", "flow", "流程"]),
            ("privacy/local-first", ["privacy", "local", "private", "隐私", "本地"]),
            ("automation", ["automation", "ai", "自动"]),
        ]
    for label, terms in checks:
        if any(term in text for term in terms):
            patterns.append(label)
    return patterns or ["general product reference"]


def _useful_signal(domain_type: str, source: dict[str, Any]) -> str:
    patterns = _detected_patterns(domain_type, source)
    if _is_portfolio_like(domain_type):
        if "case-study proof" in patterns:
            return "Supports making proof, outcomes, screenshots, and links part of project storytelling."
        if "project tracking workflow" in patterns:
            return "Supports tracking status, goals, tasks, and retrospectives before portfolio export."
        if "preview/publish/export" in patterns:
            return "Supports making preview/export a core MVP requirement."
        if "theme/template selection" in patterns:
            return "Supports surfacing theme choice early in the workflow."
        if "media upload and composition" in patterns:
            return "Supports treating media and project description as one editing workflow."
        if "AI-assisted creation" in patterns:
            return "Supports AI as visual/copy assistance, with strict proof-of-work boundaries."
    return "Use as pattern evidence, not as a feature list to copy."


def _caution(domain_type: str, source: dict[str, Any]) -> str:
    patterns = _detected_patterns(domain_type, source)
    if _is_portfolio_like(domain_type):
        if "hosting/domain/platform breadth" in patterns:
            return "Do not let hosting, domains, or imports expand MVP scope."
        if "pricing or plan limits" in patterns:
            return "Pricing/plan limits are market context, not local MVP requirements."
        if "AI-assisted creation" in patterns:
            return "AI must not fabricate user-owned portfolio proof."
    return "Validate the pattern against the selected MVP before adding scope."


def _research_query_plan(idea: str, domain_type: str) -> list[dict[str, Any]]:
    if domain_type == "creator_project_tracker":
        return [
            {
                "group": "portfolio_export_references",
                "purpose": "Find strong portfolio outputs because export quality is the payoff.",
                "queries": [
                    "best portfolio builder for designers templates preview export",
                    "Framer portfolio templates product designer case study website examples",
                    "Contra creator profile examples project proof links case studies",
                ],
            },
            {
                "group": "creator_project_workflow",
                "purpose": "Find how creators track project status, goals, tasks, screenshots, and retrospectives.",
                "queries": [
                    "creator project tracker portfolio export project screenshots tasks retrospective",
                    "project tracker template screenshots goals tasks publish links retrospective",
                    "creator portfolio workflow project cards proof metrics screenshots",
                ],
            },
            {
                "group": "case_study_quality",
                "purpose": "Find how great portfolios turn raw projects into proof-driven case studies.",
                "queries": [
                    "best UX designer portfolio case studies problem process outcome",
                    "Behance project case study layout outcome metrics screenshots",
                    "award winning personal portfolio websites project card design mobile",
                ],
            },
            {
                "group": "seeded_site_search",
                "purpose": "Force attempts against known strong portfolio reference domains.",
                "queries": [
                    query
                    for seed in reference_seeds_for_domain(domain_type)
                    for query in seed.get("fallback_queries", [])
                ],
            },
            {
                "group": "project_tracker_baseline",
                "purpose": "Use project tracker tools only for structure, not visual direction.",
                "queries": [
                    "Asana project tracker template status goals tasks project notes",
                    "simple project tracker dashboard status goals tasks notes",
                ],
            },
        ]
    if domain_type == "portfolio":
        return [
            {
                "group": "direct_competitors",
                "purpose": "Find tools that build polished portfolios from user content.",
                "queries": [
                    "best portfolio builder for designers templates preview export",
                    "portfolio website builder for creatives project screenshots case studies",
                    "personal website builder for developers portfolio examples",
                ],
            },
            {
                "group": "high_quality_templates",
                "purpose": "Find visual systems that can drive v0 direction axes.",
                "queries": [
                    "Framer portfolio templates product designer case study website examples",
                    "Webflow portfolio templates designer developer personal site",
                    "Semplice portfolio examples creative portfolio case studies",
                    "Readymag portfolio examples personal website visual design",
                ],
            },
            {
                "group": "real_case_studies",
                "purpose": "Find proof/storytelling structure for project cards and detail pages.",
                "queries": [
                    "best UX designer portfolio case studies problem process outcome",
                    "award winning personal portfolio websites project card design mobile",
                    "Behance project case study layout outcome metrics screenshots",
                ],
            },
            {
                "group": "adjacent_profiles",
                "purpose": "Borrow credible creator/profile proof patterns without cloning platform scope.",
                "queries": [
                    "Contra creator profile examples project proof links case studies",
                    "Dribbble designer profile portfolio project cards",
                ],
            },
            {
                "group": "seeded_site_search",
                "purpose": "Force attempts against known strong portfolio reference domains.",
                "queries": [
                    query
                    for seed in reference_seeds_for_domain(domain_type)
                    for query in seed.get("fallback_queries", [])
                ],
            },
        ]
    if domain_type == "expense":
        return [
            {
                "group": "direct_competitors",
                "purpose": "Find repeated finance tracking workflows.",
                "queries": [
                    "best expense tracker app quick transaction entry categorization reports",
                    "personal finance app onboarding UX best practices",
                    "expense tracker monthly summary dashboard design patterns",
                ],
            }
        ]
    if domain_type == "freelance":
        return [
            {
                "group": "direct_competitors",
                "purpose": "Find freelance time, billing, and invoice workflows.",
                "queries": [
                    "best freelance time tracking invoice app workflow",
                    "client project time tracker billable hours reports UX",
                    "freelance invoice app local-first privacy export requirements",
                ],
            }
        ]
    return [
        {
            "group": "generic_product_discovery",
            "purpose": "Find target users, workflow, competitors, UX patterns, and risks.",
            "queries": [
                f"{' '.join(idea.split())[:120]} target users pain points",
                f"{' '.join(idea.split())[:120]} competitor apps feature patterns",
                f"{' '.join(idea.split())[:120]} onboarding UX best practices",
            ],
        }
    ]


def _render_research_plan(idea: str, domain_type: str, query_plan: list[dict[str, Any]]) -> str:
    if _is_portfolio_like(domain_type):
        questions = [
            "Which reference products produce a polished portfolio fastest?",
            "How do strong tools handle image upload, project descriptions, theme choice, preview, and publishing?",
            "Which real portfolio examples show strong proof, project hierarchy, and mobile behavior?",
            "Where do products use templates or AI to improve visual quality?",
            "What should remain out of scope: hosting, domains, imports, large template libraries, analytics, or CMS features?",
            "What UX states must be tested for uploads, preview, export, and theme selection?",
        ]
    else:
        questions = [
            "Which reference products validate the target user's repeated workflow?",
            "What core output makes the product useful enough for repeated use?",
            "Which features are MVP-critical versus platform scope?",
            "What UX states and failure modes must be tested?",
            "What evidence is missing before implementation?",
        ]
    lines = ["# Research Plan", "", "## Product Idea", "", idea, "", f"## Product Type", "", domain_type, "", "## Research Questions", ""]
    lines.extend(f"- {question}" for question in questions)
    lines.extend(["", "## Sharp Query Plan", "", "| Group | Purpose | Queries |", "| --- | --- | --- |"])
    for group in query_plan:
        queries = "<br>".join(_escape_table(query) for query in group.get("queries", []))
        lines.append(f"| {_escape_table(str(group.get('group', '')))} | {_escape_table(str(group.get('purpose', '')))} | {queries} |")
    lines.extend(
        [
            "",
            "## Collection Strategy",
            "",
            "- Use the sharp query plan before falling back to the user's raw phrase.",
            "- Reject generic sources when they do not match the product type.",
            "- Use Tavily to discover references and market language.",
            "- Use Firecrawl or Playwright for top references to extract full page content, screenshots, pricing, and UI states.",
            "- Treat snippets as directional evidence until source quality and visual evidence are high.",
        ]
    )
    return "\n".join(lines)


def _render_source_quality_report(domain_type: str, sources: list[dict[str, Any]]) -> str:
    high = sum(1 for source in sources if _quality_label(source) == "high")
    medium = sum(1 for source in sources if _quality_label(source) == "medium")
    low = sum(1 for source in sources if _quality_label(source) == "low")
    lines = [
        "# Source Quality Report",
        "",
        f"- Total sources: {len(sources)}",
        f"- High quality: {high}",
        f"- Medium quality: {medium}",
        f"- Low quality: {low}",
        "",
        "| Source | Quality | Type | Why It Matters |",
        "| --- | --- | --- | --- |",
    ]
    for source in sources[:12]:
        lines.append(
            f"| [{source.get('id', 'S?')}] {_escape_table(_reference_name(source))} | {_quality_label(source)} | {_reference_type(source)} | {_escape_table(_useful_signal(domain_type, source))} |"
        )
    lines.extend(
        [
            "",
            "## Gaps",
            "",
            "- Search snippets are not enough for final product judgment.",
            "- Add Firecrawl markdown extraction for top references.",
            "- Add screenshots for visual and UX products.",
            "- Record pricing/limits and feature depth from primary pages when available.",
        ]
    )
    return "\n".join(lines)


def _render_reference_products(products: list[dict[str, Any]]) -> str:
    lines = [
        "# Reference Products",
        "",
        "| Reference | Source | Critic | Evidence | Quality | Type | Detected Patterns | Useful Signal | Caution |",
        "| --- | --- | ---: | --- | --- | --- | --- | --- | --- |",
    ]
    for product in products:
        patterns = ", ".join(product["detected_patterns"])
        lines.append(
            f"| {_escape_table(product['name'])} | [{product['source_id']}] | {product.get('total_score', 0)}/110 {product.get('critic_verdict', '')} | {product.get('evidence_level', 'search_snippet')} | {product['source_quality']} | {product['reference_type']} | {_escape_table(patterns)} | {_escape_table(product['useful_signal'])} | {_escape_table(product['caution'])} |"
        )
    lines.extend(
        [
            "",
            "## How To Use This",
            "",
            "- Use patterns as evidence for PRD strategy.",
            "- Use only `strong_reference` or repeated `usable_reference` sources for core PRD decisions.",
            "- Treat `seed_profile` rows as mandatory live-verification targets, not as proof.",
            "- Treat `weak_or_generic` sources as market language only.",
            "- Do not copy entire products or platform scope.",
            "- Promote repeated patterns into feature and UX pattern docs.",
        ]
    )
    return "\n".join(lines)


def _render_reference_critic(domain_type: str, products: list[dict[str, Any]]) -> str:
    strong = [product for product in products if product.get("critic_verdict") == "strong_reference"]
    usable = [product for product in products if product.get("critic_verdict") == "usable_reference"]
    seeds = [product for product in products if product.get("critic_verdict") == "seed_profile"]
    weak = [product for product in products if product.get("critic_verdict") == "weak_or_generic"]
    status = "pass" if len(strong) >= 3 or (len(strong) >= 1 and len(usable) >= 3) else "needs_retry"
    lines = [
        "# Reference Critic",
        "",
        f"Status: {status}",
        f"Domain: {domain_type}",
        "",
        "## Reference Quality Summary",
        "",
        f"- Strong references: {len(strong)}",
        f"- Usable references: {len(usable)}",
        f"- Seed profiles needing live verification: {len(seeds)}",
        f"- Weak/generic references: {len(weak)}",
        "",
        "| Reference | Verdict | Evidence | Score | Why Excellent | Borrow | Do Not Copy | PRD Implications |",
        "| --- | --- | --- | ---: | --- | --- | --- | --- |",
    ]
    for product in products:
        lines.append(
            "| {name} | {verdict} | {evidence} | {score}/110 | {why} | {borrow} | {copy} | {implications} |".format(
                name=_escape_table(product["name"]),
                verdict=product.get("critic_verdict", ""),
                evidence=product.get("evidence_level", "search_snippet"),
                score=product.get("total_score", 0),
                why=_escape_table("; ".join(product.get("why_excellent", []))),
                borrow=_escape_table("; ".join(product.get("borrow", []))),
                copy=_escape_table("; ".join(product.get("do_not_copy", []))),
                implications=_escape_table("; ".join(product.get("prd_implications", []))),
            )
        )
    if status != "pass":
        lines.extend(
            [
                "",
                "## Retry Required",
                "",
                "- Current references are not sharp enough for top-tier PRD decisions.",
                "- Rerun research with the sharp query plan in `docs/product/research-plan.md`.",
                "- For portfolio-like products, prioritize Framer, Webflow, Semplice, Readymag, Contra, Behance, UX case studies, and award-winning personal sites.",
            ]
        )
    return "\n".join(lines)


def _render_feature_patterns(domain_type: str, products: list[dict[str, Any]]) -> str:
    pattern_sources: dict[str, list[str]] = {}
    for product in products:
        for pattern in product["detected_patterns"]:
            pattern_sources.setdefault(pattern, []).append(f"[{product['source_id']}]")
    lines = ["# Feature Patterns", ""]
    for pattern, refs in sorted(pattern_sources.items(), key=lambda item: (-len(item[1]), item[0])):
        lines.append(f"- {pattern}: supported by {', '.join(refs[:5])}.")
    if _is_portfolio_like(domain_type):
        lines.extend(
            [
                "",
                "## MVP Implications",
                "",
                "- Theme selection, media upload, portfolio preview, and static export should be evaluated together.",
                "- For creator project trackers, status/tasks/retrospectives are valuable only if they improve the eventual portfolio proof artifact.",
                "- AI visual assistance belongs in placeholder/demo/theme assets, not in user proof-of-work.",
                "- Hosting, domains, imports, and broad CMS behavior are V1/future unless explicitly selected.",
            ]
        )
    return "\n".join(lines)


def _render_ux_patterns(domain_type: str, products: list[dict[str, Any]]) -> str:
    lines = ["# UX Patterns", ""]
    if _is_portfolio_like(domain_type):
        lines.extend(
            [
                "## Critical Flow",
                "",
                "Profile/project capture -> project gallery/tracker -> theme/visual direction -> preview -> static export.",
                "Portfolio baseline: Profile content -> project gallery -> theme -> preview -> static export.",
                "",
                "## UX Requirements",
                "",
                "- Preview must be central and updated after meaningful edits.",
                "- Project status, tasks, goals, and retrospectives must strengthen the portfolio story rather than becoming generic PM overhead.",
                "- Upload states must include empty, uploading, preview, replace, remove, invalid type, oversized file, and failure.",
                "- Theme presets must show visually distinct thumbnails or previews.",
                "- Static export must closely match preview output.",
                "- Project cards must maintain stable layout across screenshots, descriptions, tags, and links.",
            ]
        )
    else:
        lines.extend(
            [
                "## Critical Flow",
                "",
                "Capture -> validate -> review -> produce useful output.",
                "",
                "## UX Requirements",
                "",
                "- Primary workflow must be obvious without explanatory UI copy.",
                "- Empty, loading, validation, success, and error states must be explicit.",
                "- Summary outputs must be traceable to source records.",
            ]
        )
    lines.extend(["", "## Evidence", ""])
    for product in products[:5]:
        lines.append(f"- [{product['source_id']}] {product['name']}: {', '.join(product['detected_patterns'])}")
    return "\n".join(lines)


def _render_product_management_benchmarks(domain_type: str) -> str:
    lines = [
        "# Product Management Benchmarks",
        "",
        "These mature products are used as operating-model references, not as requirements to copy.",
        "Product-specific PRD claims still need project research sources or explicit assumptions.",
        "",
        "| Benchmark | Mature Pattern | How This PRD Agent Should Use It | Quality Gate |",
        "| --- | --- | --- | --- |",
    ]
    rows = [
        {
            "benchmark": "Aha!",
            "pattern": "Product lifecycle from market intelligence to strategy, requirements, roadmap, and delivery.",
            "use": "Force every PRD to explain strategy, users, MVP, non-goals, risks, and handoff readiness.",
            "gate": "The PRD is not ready if it is only a feature list.",
        },
        {
            "benchmark": "Dovetail",
            "pattern": "Raw research is turned into evidence-backed insights and shareable research narratives.",
            "use": "Separate source collection, insight synthesis, assumptions, and open research gaps.",
            "gate": "Important product judgments must cite a source ID or be marked as an assumption.",
        },
        {
            "benchmark": "Productboard",
            "pattern": "Feedback and insights are linked to feature ideas, specs, and prioritization.",
            "use": "Trace each MVP feature back to a pattern, insight, or explicit strategic bet.",
            "gate": "No MVP feature should appear without an evidence or strategy link.",
        },
        {
            "benchmark": "Jira Product Discovery",
            "pattern": "Ideas are captured, compared, prioritized, and then connected to delivery work.",
            "use": "Keep multiple PRD options, record the selected option, and preserve non-selected tradeoffs.",
            "gate": "The selected direction must include why it beat alternatives.",
        },
        {
            "benchmark": "v0 / Replit Agent",
            "pattern": "Natural-language product intent becomes UI/app drafts with preview, iteration, and checkpoints.",
            "use": "Write PRDs that are directly usable by UI, architecture, development, QA, and review agents.",
            "gate": "Acceptance criteria must be testable against a running or generated artifact.",
        },
        {
            "benchmark": "Claude Code style agents",
            "pattern": "Specialized agents use isolated context, explicit tools, permissions, and review gates.",
            "use": "Keep PM, UI, architect, developer, QA, and reviewer responsibilities separate.",
            "gate": "PRD output should not bypass later design, architecture, QA, or review checks.",
        },
    ]
    for row in rows:
        lines.append(
            "| {benchmark} | {pattern} | {use} | {gate} |".format(
                benchmark=_escape_table(row["benchmark"]),
                pattern=_escape_table(row["pattern"]),
                use=_escape_table(row["use"]),
                gate=_escape_table(row["gate"]),
            )
        )
    lines.extend(
        [
            "",
            "## Domain-Specific Benchmark Focus",
            "",
            *_benchmark_focus(domain_type),
        ]
    )
    return "\n".join(lines)


def _benchmark_focus(domain_type: str) -> list[str]:
    if _is_portfolio_like(domain_type):
        return [
            "- Aha! standard: define a narrow product strategy around a publishable portfolio artifact.",
            "- Dovetail standard: distinguish real source evidence from design assumptions before deciding scope.",
            "- Productboard standard: connect theme selection, media upload, preview, and export to specific user value.",
            "- Jira Product Discovery standard: keep hosting, domains, imports, and broad CMS scope out of MVP unless selected deliberately.",
            "- v0/Replit standard: make the PRD detailed enough to generate a polished first web prototype and verify preview/export behavior.",
            "- Claude Code standard: make QA and review check preview fidelity, upload edge cases, and AI asset boundaries.",
        ]
    return [
        "- Aha! standard: define the product strategy before features.",
        "- Dovetail standard: make evidence and assumptions inspectable.",
        "- Productboard standard: trace features to insights and prioritization.",
        "- Jira Product Discovery standard: preserve option tradeoffs and delivery linkage.",
        "- v0/Replit standard: make the PRD actionable enough to produce and test a working artifact.",
        "- Claude Code standard: keep execution gated by QA and review.",
    ]


def _render_evidence_chain(
    domain_type: str,
    sources: list[dict[str, Any]],
    products: list[dict[str, Any]],
    rows: list[dict[str, str]] | None = None,
) -> str:
    rows = rows or _evidence_chain_rows(domain_type, sources, products)
    lines = [
        "# Evidence Chain",
        "",
        "This maps research evidence to PRD decisions so the Product Requirements Agent does not invent scope by default.",
        "",
        "| PRD Decision | Evidence | Evidence Status | Product Insight | MVP / Non-goal Implication | Downstream Gate |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            "| {decision} | {evidence} | {status} | {insight} | {implication} | {gate} |".format(
                decision=_escape_table(row["decision"]),
                evidence=_escape_table(row["evidence"]),
                status=_escape_table(row.get("evidence_status", "unknown")),
                insight=_escape_table(row["insight"]),
                implication=_escape_table(row["implication"]),
                gate=_escape_table(row["gate"]),
            )
        )
    lines.extend(
        [
            "",
            "## Rules",
            "",
            "- Promote repeated evidence into PRD requirements only when it supports the selected product strategy.",
            "- Keep single-source findings as assumptions unless a PM deliberately accepts the risk.",
            "- Core PRD decisions should be `evidence_backed`; `hypothesis` rows need validation gates before implementation is final.",
            "- Turn every MVP implication into acceptance criteria or a later architecture/development task.",
            "- Keep non-goals visible so broad reference products do not expand MVP scope by accident.",
        ]
    )
    return "\n".join(lines)


def _evidence_chain_rows(
    domain_type: str,
    sources: list[dict[str, Any]],
    products: list[dict[str, Any]],
) -> list[dict[str, str]]:
    if domain_type == "portfolio":
        rows = [
            {
                "decision": "Theme/template selection belongs in MVP.",
                "evidence": _refs_for_patterns(products, ["theme/template selection"]),
                "insight": "Portfolio products sell confidence through visible polish before publishing.",
                "implication": "Include constrained theme presets and preview them early; defer large template marketplaces.",
                "gate": "Design must show distinct theme states; QA must verify content persists across theme changes.",
            },
            {
                "decision": "Media upload and project storytelling are one workflow.",
                "evidence": _refs_for_patterns(products, ["media upload and composition"]),
                "insight": "Images without project context do not prove credibility; text without visuals feels incomplete.",
                "implication": "Project cards need screenshot, title, description, tags, role, and links.",
                "gate": "QA must cover empty, upload, replace, remove, invalid type, and oversized image states.",
            },
            {
                "decision": "Preview/export is the MVP payoff.",
                "evidence": _refs_for_patterns(products, ["preview/publish/export"]),
                "insight": "The useful artifact is a presentable portfolio page, not saved form data.",
                "implication": "Preview and static HTML export stay MVP; hosting and domains stay non-goals.",
                "gate": "Reviewer must treat preview/export mismatch as a product defect.",
            },
            {
                "decision": "AI visuals are helper assets, not proof-of-work.",
                "evidence": _refs_for_patterns(products, ["AI-assisted creation"]),
                "insight": "AI can improve first-run polish but can also damage trust if it fabricates credentials.",
                "implication": "Allow generated placeholders/theme thumbnails; forbid fake headshots, client logos, screenshots, or work history.",
                "gate": "Reviewer must verify generated assets are labeled as placeholders.",
            },
            {
                "decision": "Platform breadth is V1/future.",
                "evidence": _refs_for_patterns(
                    products,
                    ["hosting/domain/platform breadth", "pricing or plan limits"],
                ),
                "insight": "Mature portfolio platforms often expand through hosting, domains, imports, analytics, and support.",
                "implication": "Borrow quality patterns while keeping local-first static export as the MVP boundary.",
                "gate": "Architect must reject tasks that add platform scope before core export works.",
            },
        ]
        return [_with_evidence_status(row, products) for row in rows]
    if domain_type == "creator_project_tracker":
        rows = [
            {
                "decision": "Project tracking and portfolio export must be one workflow.",
                "evidence": _refs_for_patterns(products, ["project tracking workflow", "preview/publish/export"]),
                "insight": "Creators need to capture project work while it is happening, then turn selected work into a publishable portfolio artifact.",
                "implication": "MVP includes project CRUD, task/status tracking, screenshots, retrospective notes, and portfolio export.",
                "gate": "QA must verify create/edit/delete, task lifecycle, local persistence, and export.",
            },
            {
                "decision": "Project proof requires screenshot plus narrative fields.",
                "evidence": _refs_for_patterns(products, ["media upload and composition", "case-study proof"]),
                "insight": "A project card is credible when visual proof, role, outcome, metrics, and links reinforce each other.",
                "implication": "Project detail must include screenshot lifecycle, alt text, goal/outcome/retrospective notes, tags, and links.",
                "gate": "Reviewer must reject generated UI if project cards feel like generic task records without proof.",
            },
            {
                "decision": "Visual direction must come from portfolio references, not project-management dashboards.",
                "evidence": _refs_for_patterns(products, ["theme/template selection", "case-study proof"]),
                "insight": "The product can use tracker structure, but its value is judged by the quality of the portfolio output.",
                "implication": "UI Agent must generate portfolio-grade visual directions and use tracker references only for structure.",
                "gate": "Visual Critic must compare variants against reference alignment and screenshot evidence.",
            },
            {
                "decision": "Preview/export is the MVP payoff.",
                "evidence": _refs_for_patterns(products, ["preview/publish/export"]),
                "insight": "The useful artifact is a presentable page, not only saved project data.",
                "implication": "Static HTML export stays MVP; hosting and domains stay non-goals.",
                "gate": "QA must verify exported HTML is wired, escaped, and visually aligned with preview.",
            },
            {
                "decision": "Platform breadth is V1/future.",
                "evidence": _refs_for_patterns(products, ["hosting/domain/platform breadth", "pricing or plan limits"]),
                "insight": "Mature products expand into hosting, domains, collaboration, analytics, and imports after the core publishing loop works.",
                "implication": "Keep cloud accounts, multi-user collaboration, hosting, and analytics out of MVP unless explicitly selected.",
                "gate": "Architect must reject broad platform tasks before core local-first export works.",
            },
        ]
        return [_with_evidence_status(row, products) for row in rows]
    refs = ", ".join(f"[{source.get('id', f'S{index + 1}')}]" for index, source in enumerate(sources[:4]))
    if not refs:
        refs = "Assumption"
    rows = [
        {
            "decision": "Define one primary user workflow.",
            "evidence": refs,
            "insight": "A focused workflow is easier to test and more memorable than generic CRUD.",
            "implication": "MVP should optimize capture, validation, review, and one useful output.",
            "gate": "PRD validation should fail generic feature lists.",
        },
        {
            "decision": "Make outputs traceable.",
            "evidence": refs,
            "insight": "Users trust summaries when they can inspect source records.",
            "implication": "Summary/report artifacts must link back to the records that produced them.",
            "gate": "QA must verify summary consistency after create, edit, and delete.",
        },
        {
            "decision": "Defer platform features.",
            "evidence": refs,
            "insight": "Integrations, collaboration, and automation are valuable only after the core workflow works.",
            "implication": "Keep external integrations, team permissions, and broad automation in V1/future.",
            "gate": "Reviewer must flag scope creep that bypasses MVP evidence.",
        },
    ]
    return [_with_evidence_status(row, products) for row in rows]


def _with_evidence_status(row: dict[str, str], products: list[dict[str, Any]]) -> dict[str, str]:
    evidence = row.get("evidence", "")
    refs = [token.strip("[] ") for token in evidence.split(",") if token.strip().startswith("[")]
    strong_count = 0
    usable_count = 0
    seed_count = 0
    for product in products:
        if str(product.get("source_id")) not in refs:
            continue
        if product.get("critic_verdict") == "strong_reference":
            strong_count += 1
        elif product.get("critic_verdict") == "usable_reference":
            usable_count += 1
        elif product.get("critic_verdict") == "seed_profile":
            seed_count += 1
    if "Assumption" in evidence or not refs:
        status = "hypothesis"
    elif strong_count >= 2 or (strong_count >= 1 and usable_count >= 1) or usable_count >= 3:
        status = "evidence_backed"
    elif strong_count == 1 or usable_count >= 1:
        status = "weak_evidence"
    elif seed_count >= 1:
        status = "seed_profile"
    else:
        status = "hypothesis"
    return {**row, "evidence_status": status}


def _refs_for_patterns(products: list[dict[str, Any]], patterns: list[str]) -> str:
    refs: list[str] = []
    for product in products:
        detected = set(product.get("detected_patterns") or [])
        if any(pattern in detected for pattern in patterns):
            refs.append(f"[{product['source_id']}]")
    if refs:
        return ", ".join(dict.fromkeys(refs))
    return "Assumption"


def _render_evidence_gate(rows: list[dict[str, str]]) -> str:
    blockers = [row for row in rows if row.get("evidence_status") in {"hypothesis", "weak_evidence", "seed_profile"}]
    status = "pass" if not blockers else "needs_evidence"
    lines = [
        "# Evidence Gate",
        "",
        f"Status: {status}",
        "",
        "| Decision | Evidence Status | Action |",
        "| --- | --- | --- |",
    ]
    for row in rows:
        action = (
            "Can enter PRD as a core requirement."
            if row.get("evidence_status") == "evidence_backed"
            else "Live-verify seeds or keep as hypothesis with validation gate."
            if row.get("evidence_status") == "seed_profile"
            else "Keep as hypothesis, add validation gate, or rerun targeted research."
        )
        lines.append(f"| {_escape_table(row['decision'])} | {row.get('evidence_status', 'unknown')} | {_escape_table(action)} |")
    lines.extend(
        [
            "",
            "## Gate Rules",
            "",
            "- Core PRD decisions should have at least two strong references, or one strong plus one usable reference.",
            "- Seed profiles are mandatory targets for live verification, not core evidence by themselves.",
            "- Weak evidence can influence exploration but should not become a hard MVP requirement without validation.",
            "- Hypotheses must be labeled and converted into QA/Product Review checks before final approval.",
        ]
    )
    if blockers:
        lines.extend(["", "## Evidence To Strengthen", ""])
        for row in blockers:
            lines.append(f"- {row['decision']}: rerun targeted search or capture visual evidence.")
    return "\n".join(lines)


def _render_screenshot_plan(domain_type: str, products: list[dict[str, Any]]) -> str:
    targets = [
        f"- [{product['source_id']}] {product['name']} ({product.get('critic_verdict')}): {product['url']}"
        for product in _visual_targets(products)[:8]
        if product.get("url")
    ]
    lines = [
        "# Reference Screenshots",
        "",
        "Reference screenshots are a required visual-research evidence layer. The deterministic pass records capture targets and questions; browser capture can be wired to these targets when Firecrawl/Playwright is enabled.",
        "",
        "## Capture Targets For Firecrawl/Playwright",
        "",
        *(targets or ["- No screenshot targets available."]),
        "",
        "## Screenshot Questions",
        "",
    ]
    if domain_type == "portfolio":
        lines.extend(
            [
                "- Desktop above-the-fold: identity, CTA, typography, visual density.",
                "- Mobile above-the-fold: nav, hero compression, CTA order, text fit.",
                "- What does first-run content entry look like?",
                "- How are themes/templates previewed?",
                "- How are images, project cards, and links composed?",
                "- How does preview/publish/export appear in the UI?",
            ]
        )
    else:
        lines.extend(
            [
                "- What is the primary workflow surface?",
                "- How are empty, validation, success, and error states represented?",
                "- What makes the output trustworthy?",
            ]
        )
    return "\n".join(lines)


def _render_visual_reference_analysis(domain_type: str, products: list[dict[str, Any]]) -> str:
    targets = _visual_targets(products)
    lines = [
        "# Visual Reference Analysis",
        "",
        f"Domain: {domain_type}",
        "",
        "## Required Screenshot Set",
        "",
        "| Reference | Desktop Home | Mobile Home | Key Workflow | Detail/Case Study | Why Capture |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for product in targets[:8]:
        why = _visual_capture_reason(domain_type, product)
        lines.append(
            f"| [{product['source_id']}] {_escape_table(product['name'])} | required | required | required | optional | {_escape_table(why)} |"
        )
    if not targets:
        lines.append("| No strong visual target found | missing | missing | missing | missing | Rerun research with sharper visual queries. |")
    lines.extend(
        [
            "",
            "## Visual Critic Questions",
            "",
            "- First impression: does the first viewport immediately communicate who the product/person is for?",
            "- Information hierarchy: what is largest, what is secondary, and what is deliberately hidden?",
            "- Portfolio proof: how are screenshots, role, outcome, metrics, links, and captions combined?",
            "- Layout density: is it editorial, marketing-led, dashboard-like, or gallery-led?",
            "- Mobile behavior: what collapses, what remains visible, and whether text/control overlap appears.",
            "- Borrowability: which exact pattern should become a v0 prompt constraint?",
            "",
            "## Handoff To UI Direction Planner",
            "",
            "- Use strong visual references to define opposing v0 axes.",
            "- Do not let generic project-management references drive visual style for portfolio-like products.",
            "- A visual direction without screenshot evidence is lower confidence than one with captured desktop/mobile evidence.",
        ]
    )
    return "\n".join(lines)


def _visual_targets(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    visual = [
        product
        for product in products
        if product.get("critic_verdict") in {"strong_reference", "usable_reference", "seed_profile"}
        and any(
            pattern in set(product.get("detected_patterns") or [])
            for pattern in ["theme/template selection", "media upload and composition", "preview/publish/export", "case-study proof"]
        )
    ]
    return sorted(visual, key=lambda product: int(product.get("total_score", 0)), reverse=True)


def _visual_capture_reason(domain_type: str, product: dict[str, Any]) -> str:
    patterns = set(product.get("detected_patterns") or [])
    reasons: list[str] = []
    if "theme/template selection" in patterns:
        reasons.append("template/style selection")
    if "media upload and composition" in patterns:
        reasons.append("image and content composition")
    if "case-study proof" in patterns:
        reasons.append("proof-driven case study structure")
    if "preview/publish/export" in patterns:
        reasons.append("preview/export payoff")
    if domain_type == "creator_project_tracker" and "project tracking workflow" in patterns:
        reasons.append("status/task tracker structure")
    return ", ".join(reasons) or "general visual reference"


def _escape_table(value: str) -> str:
    return value.replace("|", "/").replace("\n", " ")
