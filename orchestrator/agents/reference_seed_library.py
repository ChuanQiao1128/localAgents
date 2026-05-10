from __future__ import annotations

from typing import Any


def reference_seeds_for_domain(domain_type: str) -> list[dict[str, Any]]:
    if domain_type not in {"portfolio", "creator_project_tracker"}:
        return []
    return [
        {
            "source_id": "SEED-framer-portfolio",
            "name": "Framer Portfolio Templates",
            "url": "https://www.framer.com/templates/categories/portfolio/",
            "alternate_urls": [
                "https://www.framer.com/templates/",
                "https://www.framer.com/marketplace/?category=portfolio",
            ],
            "fallback_queries": [
                "site:framer.com/templates portfolio designer",
                "site:framer.com/marketplace portfolio template",
            ],
            "known_patterns": [
                "template-first portfolio creation",
                "strong visual preview before editing",
                "responsive template gallery",
                "publish/hosting is platform scope",
            ],
        },
        {
            "source_id": "SEED-webflow-portfolio",
            "name": "Webflow Portfolio Templates",
            "url": "https://webflow.com/templates/category/portfolio",
            "alternate_urls": [
                "https://webflow.com/templates/html/portfolio-website-templates",
                "https://webflow.com/made-in-webflow/portfolio",
            ],
            "fallback_queries": [
                "site:webflow.com/templates portfolio website templates",
                "site:webflow.com/made-in-webflow portfolio designer",
            ],
            "known_patterns": [
                "template marketplace organized by portfolio use case",
                "responsive preview expectations",
                "visual polish before content depth",
                "hosting/domain are non-MVP platform features",
            ],
        },
        {
            "source_id": "SEED-semplice-showcase",
            "name": "Semplice Portfolio Showcase",
            "url": "https://www.semplice.com/showcase",
            "alternate_urls": [
                "https://www.semplice.com/",
                "https://www.semplice.com/features",
            ],
            "fallback_queries": [
                "site:semplice.com showcase portfolio designer",
                "site:semplice.com portfolio case study examples",
            ],
            "known_patterns": [
                "designer-owned portfolio storytelling",
                "high-end typography and project-led visual systems",
                "case-study pages as proof of craft",
                "customization depth should not expand local MVP scope",
            ],
        },
        {
            "source_id": "SEED-readymag-examples",
            "name": "Readymag Portfolio Examples",
            "url": "https://readymag.com/examples/",
            "alternate_urls": [
                "https://readymag.com/",
                "https://readymag.com/design/",
            ],
            "fallback_queries": [
                "site:readymag.com examples portfolio",
                "site:readymag.com personal portfolio design examples",
            ],
            "known_patterns": [
                "editorial visual composition",
                "image-forward storytelling",
                "distinctive typography-led layouts",
                "interactive publishing is non-MVP platform scope",
            ],
        },
        {
            "source_id": "SEED-contra-profile",
            "name": "Contra Creator Profiles And Projects",
            "url": "https://contra.com/",
            "alternate_urls": [
                "https://contra.com/discover",
                "https://contra.com/blog/how-to-build-a-case-study-from-scratch-on-contra",
            ],
            "fallback_queries": [
                "site:contra.com profile designer portfolio project case study",
                "site:contra.com/blog case study portfolio outcome proof",
            ],
            "known_patterns": [
                "creator profile credibility",
                "project proof through role, service, links, and outcomes",
                "case-study coaching",
                "marketplace/social platform scope should stay out of MVP",
            ],
        },
        {
            "source_id": "SEED-behance-case-study",
            "name": "Behance Project Case Studies",
            "url": "https://www.behance.net/",
            "alternate_urls": [
                "https://www.behance.net/search/projects/portfolio",
                "https://www.behance.net/search/projects/ux%20case%20study",
            ],
            "fallback_queries": [
                "site:behance.net UX case study portfolio project layout",
                "site:behance.net product design case study outcome metrics",
            ],
            "known_patterns": [
                "project-first proof presentation",
                "long-form case study hierarchy",
                "screenshots and captions as credibility evidence",
                "social metrics should not be fabricated or copied",
            ],
        },
        {
            "source_id": "SEED-awwwards-portfolio",
            "name": "Awwwards Portfolio Website Examples",
            "url": "https://www.awwwards.com/websites/portfolio/",
            "alternate_urls": [
                "https://www.awwwards.com/websites/personal/",
                "https://www.awwwards.com/websites/studio-agency/",
            ],
            "fallback_queries": [
                "site:awwwards.com/websites/portfolio designer portfolio",
                "award winning personal portfolio websites project cards mobile",
            ],
            "known_patterns": [
                "high visual ambition and first-impression quality",
                "portfolio identity expressed in first viewport",
                "strong motion/interaction patterns",
                "avoid overfitting MVP to award-site animation complexity",
            ],
        },
    ]


def seed_queries_for_domain(domain_type: str) -> list[str]:
    queries: list[str] = []
    for seed in reference_seeds_for_domain(domain_type):
        queries.extend(str(query) for query in seed.get("fallback_queries", []))
    return queries
