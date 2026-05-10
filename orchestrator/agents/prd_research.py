from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database
from orchestrator.agents.reference_seed_library import seed_queries_for_domain
from orchestrator.agents.prd_research_v2 import PrdResearchV2Agent, ResearchV2Result
from orchestrator.tools.search_tools import SearchProvider, SearchResult, SearchTools, default_search_provider


@dataclass(frozen=True)
class ResearchSource:
    id: str
    query: str
    title: str
    url: str
    summary: str
    relevance: float
    evidence_type: str


@dataclass(frozen=True)
class PrdResearchResult:
    provider: str
    queries: list[str]
    sources: list[ResearchSource]
    research_path: Path
    sources_path: Path
    research_v2: ResearchV2Result | None = None


class PrdResearchAgent:
    def __init__(self, provider: SearchProvider | None = None, db: Database | None = None):
        self.provider = provider or default_search_provider()
        self.search = SearchTools(self.provider)
        self.db = db

    def run(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
        max_queries: int = 6,
        results_per_query: int = 5,
    ) -> PrdResearchResult:
        project_path = Path(project["path"])
        queries = plan_research_queries(project["idea"])[:max_queries]
        sources = self._collect_sources(queries, results_per_query)
        research_dir = project_path / ".agent/artifacts/research"
        if run_id:
            research_dir = research_dir / run_id
        research_dir.mkdir(parents=True, exist_ok=True)
        sources_path = research_dir / "sources.json"
        sources_path.write_text(
            json.dumps([asdict(source) for source in sources], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        product_research_path = project_path / "docs/product/research.md"
        product_research_path.parent.mkdir(parents=True, exist_ok=True)
        product_research_path.write_text(render_research_markdown(project["idea"], queries, sources), encoding="utf-8")
        research_v2 = PrdResearchV2Agent(self.db).run(
            project=project,
            run_id=run_id,
            sources=sources,
        )

        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            artifacts.register(
                project_id=project["id"],
                run_id=run_id,
                phase_id="research",
                path="docs/product/research.md",
                kind="markdown",
                summary="Tavily-backed PRD research document.",
            )
            artifacts.register(
                project_id=project["id"],
                run_id=run_id,
                phase_id="research",
                path=str(sources_path.relative_to(project_path)),
                kind="json",
                summary="Structured research sources.",
            )
            EventBus(self.db).emit(
                event_type="prd.research_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="research",
                message=f"Collected {len(sources)} research source(s) for PRD.",
                payload={"queries": queries, "source_count": len(sources)},
            )
        return PrdResearchResult(
            provider=type(self.provider).__name__,
            queries=queries,
            sources=sources,
            research_path=product_research_path,
            sources_path=sources_path,
            research_v2=research_v2,
        )

    def _collect_sources(self, queries: list[str], results_per_query: int) -> list[ResearchSource]:
        seen_urls: set[str] = set()
        sources: list[ResearchSource] = []
        for query in queries:
            for result in self.search.search(query, limit=results_per_query):
                if not result.url or result.url in seen_urls:
                    continue
                seen_urls.add(result.url)
                sources.append(_source_from_result(len(sources) + 1, query, result))
        return sources


def plan_research_queries(idea: str) -> list[str]:
    compact = " ".join(idea.split())
    base = compact[:160]
    domain = _research_domain(compact)
    if domain == "portfolio":
        return [
            "best portfolio builder for designers templates preview export",
            "Framer portfolio templates product designer case study website examples",
            "Semplice portfolio examples creative portfolio case studies",
            "Readymag portfolio examples personal website visual design",
            "Contra creator profile examples project proof links case studies",
            "best UX designer portfolio case studies problem process outcome",
            "Webflow portfolio templates designer developer personal site",
            "award winning personal portfolio websites project card design mobile",
            *seed_queries_for_domain("portfolio"),
        ]
    if domain == "creator_project_tracker":
        return [
            "best portfolio builder for designers templates preview export",
            "Framer portfolio templates product designer case study website examples",
            "Webflow portfolio templates designer developer personal site",
            "Contra creator profile examples project proof links case studies",
            "best UX designer portfolio case studies problem process outcome",
            "creator project tracker portfolio export project screenshots tasks retrospective",
            "Semplice portfolio examples creative portfolio case studies",
            "Readymag portfolio examples personal website visual design",
            "project tracker template screenshots goals tasks publish links retrospective",
            "creator portfolio workflow project cards proof metrics screenshots",
            "Asana project tracker template status goals tasks project notes",
            *seed_queries_for_domain("creator_project_tracker"),
        ]
    if domain == "expense":
        return [
            f"{base} personal finance app target users pain points",
            "best expense tracker app quick transaction entry categorization reports",
            "personal finance app onboarding UX best practices",
            "expense tracker monthly summary dashboard design patterns",
            "local-first finance app privacy data export risks",
            f"{base} MVP features acceptance criteria examples",
        ]
    if domain == "freelance":
        return [
            f"{base} freelance time tracking invoice workflow pain points",
            "best freelance time tracking invoice app workflow",
            "client project time tracker billable hours reports UX",
            "freelance invoice app local-first privacy export requirements",
            f"{base} MVP features acceptance criteria examples",
            f"{base} risks payments privacy local data",
        ]
    return [
        f"{base} target users pain points",
        f"{base} MVP features product requirements",
        f"{base} competitor apps feature patterns",
        f"{base} onboarding UX best practices",
        f"{base} acceptance criteria examples",
        f"{base} risks privacy local-first data",
    ]


def _research_domain(idea: str) -> str:
    lower = idea.lower()
    has_portfolio = any(
        term in lower
        for term in ["portfolio", "作品集", "personal website", "personal site", "个人网站", "个人主页"]
    )
    has_tracker = any(
        term in lower
        for term in ["project tracker", "项目", "任务", "复盘", "retrospective", "tasks", "status"]
    )
    if has_portfolio and has_tracker:
        return "creator_project_tracker"
    if has_portfolio:
        return "portfolio"
    if any(term in lower for term in ["invoice", "发票", "time tracking", "时间追踪", "freelance", "自由职业"]):
        return "freelance"
    if any(term in lower for term in ["expense", "记账", "income", "收入", "支出", "finance", "预算"]):
        return "expense"
    return "generic"


def render_research_markdown(idea: str, queries: list[str], sources: list[ResearchSource]) -> str:
    lines = [
        "# Research",
        "",
        "## Product Idea",
        "",
        idea,
        "",
        "## Research Queries",
        "",
    ]
    lines.extend(f"- {query}" for query in queries)
    lines.extend(["", "## Sources", ""])
    for source in sources:
        lines.extend(
            [
                f"### [{source.id}] {source.title}",
                "",
                f"- URL: {source.url}",
                f"- Query: {source.query}",
                f"- Relevance: {source.relevance:.3f}",
                f"- Evidence type: {source.evidence_type}",
                "",
                source.summary or "No summary returned.",
                "",
            ]
        )
    lines.extend(
        [
            "## Initial Insights",
            "",
            "- Source-backed PRD claims should cite source IDs like `[S1]`.",
            "- Claims not supported by the sources above must be marked as assumptions.",
            "- Prioritize repeated feature and user-problem patterns across multiple sources.",
            "",
        ]
    )
    return "\n".join(lines)


def _source_from_result(index: int, query: str, result: SearchResult) -> ResearchSource:
    return ResearchSource(
        id=f"S{index}",
        query=query,
        title=result.title,
        url=result.url,
        summary=result.summary,
        relevance=result.relevance,
        evidence_type=result.evidence_type,
    )
