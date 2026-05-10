from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from orchestrator.agents.screenshot_image_analysis import analyze_screenshot


@dataclass(frozen=True)
class ExampleVisualCriticResult:
    report_path: Path
    json_path: Path
    status: str
    screenshot_backed: int
    image_analyzed: int
    example_count: int


class ExampleVisualCriticAgent:
    def run(self, *, project: dict[str, Any]) -> ExampleVisualCriticResult:
        project_path = Path(project["path"])
        output_dir = project_path / "docs/product/example-references"
        examples_path = output_dir / "top-examples.json"
        report_path = output_dir / "visual-critic.md"
        json_path = output_dir / "visual-critic.json"
        output_dir.mkdir(parents=True, exist_ok=True)

        examples = _load_examples(examples_path)
        enriched = [_enrich_example(item, project_path) for item in examples]
        screenshot_backed = sum(1 for item in enriched if item["evidence_level"] == "screenshot")
        image_analyzed = sum(
            1
            for item in enriched
            for analysis in item.get("screenshot_analysis", [])
            if isinstance(analysis, dict) and analysis.get("status") == "analyzed"
        )
        source_coverage = _source_coverage(enriched)
        standards = _visual_standards(enriched)
        axis_guidance = _axis_guidance(enriched)
        ui_requirements = _ui_team_requirements(enriched)
        image_quality = _image_quality(enriched)
        status = _status(enriched, screenshot_backed, source_coverage, image_quality)

        payload = {
            "status": status,
            "example_count": len(enriched),
            "screenshot_backed": screenshot_backed,
            "image_analyzed": image_analyzed,
            "source_coverage": source_coverage,
            "image_quality": image_quality,
            "examples": enriched,
            "visual_standards": standards,
            "axis_guidance": axis_guidance,
            "ui_team_requirements": ui_requirements,
            "gate_meaning": (
                "This critic upgrades discovered examples into design requirements. "
                "Screenshot-backed examples are stronger evidence than metadata-only examples."
            ),
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_path.write_text(_render_report(payload), encoding="utf-8")
        return ExampleVisualCriticResult(
            report_path=report_path,
            json_path=json_path,
            status=status,
            screenshot_backed=screenshot_backed,
            image_analyzed=image_analyzed,
            example_count=len(enriched),
        )


def _load_examples(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _enrich_example(example: dict[str, Any], project_path: Path) -> dict[str, Any]:
    source_id = str(example.get("source_id") or "")
    source_name = str(example.get("source_name") or source_id or "Reference")
    title = str(example.get("title") or example.get("url") or "Untitled reference")
    screenshots = [item for item in example.get("screenshots") or [] if isinstance(item, dict)]
    screenshot_paths = [str(item.get("path")) for item in screenshots if item.get("status") == "captured" and item.get("path")]
    screenshot_analysis = []
    for screenshot_path in screenshot_paths:
        path = project_path / screenshot_path
        analysis = analyze_screenshot(path)
        analysis["relative_path"] = screenshot_path
        screenshot_analysis.append(analysis)
    evidence_level = "screenshot" if screenshot_paths else str(example.get("evidence_level") or "link_candidate")
    archetype = _archetype(source_id, source_name, str(example.get("url") or ""))
    strengths = _strengths(archetype, evidence_level)
    caution = _caution(archetype)
    return {
        "source_id": source_id,
        "source_name": source_name,
        "title": title,
        "url": example.get("url"),
        "score": int(example.get("score") or 0),
        "evidence_level": evidence_level,
        "screenshot_paths": screenshot_paths,
        "screenshot_analysis": screenshot_analysis,
        "pixel_quality_score": _average_score(screenshot_analysis),
        "pixel_quality_flags": _combined_flags(screenshot_analysis),
        "archetype": archetype,
        "strengths": strengths,
        "caution": caution,
    }


def _archetype(source_id: str, source_name: str, url: str) -> str:
    combined = f"{source_id} {source_name} {url}".lower()
    if "awwwards" in combined:
        return "award_portfolio_site"
    if "behance" in combined:
        return "case_study_gallery"
    if "framer" in combined or "webflow" in combined:
        return "template_or_builder_marketplace"
    if "semplice" in combined:
        return "curated_showcase_site"
    if "readymag" in combined:
        return "editorial_template_platform"
    if "contra" in combined:
        return "creator_profile_marketplace"
    return "reference_article_or_directory"


def _strengths(archetype: str, evidence_level: str) -> list[str]:
    base = {
        "award_portfolio_site": [
            "first viewport identity and taste are the product signal",
            "visual distinctiveness matters before feature density",
            "single-person positioning should feel authored, not templated",
        ],
        "case_study_gallery": [
            "project proof needs visible images and outcome-oriented story structure",
            "role, process, and final work should be inspectable without digging",
            "captions and project titles carry credibility",
        ],
        "template_or_builder_marketplace": [
            "template choice must show materially different layouts",
            "preview before editing reduces blank-canvas risk",
            "responsive preview and publish/export confidence are core payoffs",
        ],
        "curated_showcase_site": [
            "portfolio output should feel like a finished site, not a form preview",
            "image rhythm and spacing carry perceived quality",
            "navigation should stay quiet enough for the work to lead",
        ],
        "editorial_template_platform": [
            "editorial hierarchy and page composition are useful differentiators",
            "portfolio pages need layout presets beyond color themes",
            "section sequencing can make a simple site feel designed",
        ],
        "creator_profile_marketplace": [
            "profile, services, projects, and contact need a clear conversion path",
            "credibility depends on proof and availability, not decorative chrome",
            "marketplace scope should be borrowed as workflow signals only",
        ],
    }.get(
        archetype,
        [
            "reference can inspire source discovery but is weaker design evidence",
            "use only patterns that are backed by screenshot or product metadata",
        ],
    )
    if evidence_level == "screenshot":
        return base + ["screenshot evidence is available for visual QA comparison"]
    return base + ["metadata-only evidence must not override screenshot-backed references"]


def _caution(archetype: str) -> list[str]:
    caution = {
        "award_portfolio_site": ["do not copy award-site gimmicks if they hurt editing/export usability"],
        "case_study_gallery": ["do not add social metrics, likes, or feeds to the MVP"],
        "template_or_builder_marketplace": ["do not copy hosting, CMS, domain, marketplace, or team features into MVP"],
        "curated_showcase_site": ["do not assume every showcase site pattern is editable by a beginner"],
        "editorial_template_platform": ["do not turn the builder into a general publishing platform"],
        "creator_profile_marketplace": ["do not copy marketplace discovery, payments, hiring, or profiles as network features"],
    }.get(archetype, ["do not treat listicles as direct product evidence"])
    return caution


def _source_coverage(examples: list[dict[str, Any]]) -> dict[str, Any]:
    by_archetype: dict[str, int] = {}
    screenshot_by_archetype: dict[str, int] = {}
    for example in examples:
        archetype = str(example["archetype"])
        by_archetype[archetype] = by_archetype.get(archetype, 0) + 1
        if example["evidence_level"] == "screenshot":
            screenshot_by_archetype[archetype] = screenshot_by_archetype.get(archetype, 0) + 1
    return {
        "archetypes": by_archetype,
        "screenshot_backed_archetypes": screenshot_by_archetype,
        "covered_archetype_count": len(by_archetype),
        "screenshot_backed_archetype_count": len(screenshot_by_archetype),
    }


def _image_quality(examples: list[dict[str, Any]]) -> dict[str, Any]:
    analyses = [
        item
        for example in examples
        for item in example.get("screenshot_analysis", [])
        if isinstance(item, dict)
    ]
    analyzed = [item for item in analyses if item.get("status") == "analyzed"]
    flags: dict[str, int] = {}
    for item in analyzed:
        for flag in item.get("flags") or []:
            flags[str(flag)] = flags.get(str(flag), 0) + 1
    scores = [int(item.get("score") or 0) for item in analyzed]
    return {
        "analyzed_screenshots": len(analyzed),
        "unreadable_screenshots": len(analyses) - len(analyzed),
        "average_score": round(sum(scores) / len(scores), 2) if scores else 0,
        "min_score": min(scores) if scores else 0,
        "max_score": max(scores) if scores else 0,
        "flags": flags,
        "strong_pixel_evidence": sum(1 for score in scores if score >= 70),
        "weak_pixel_evidence": sum(1 for score in scores if score < 55),
    }


def _average_score(analyses: list[dict[str, Any]]) -> int:
    scores = [int(item.get("score") or 0) for item in analyses if item.get("status") == "analyzed"]
    return int(sum(scores) / len(scores)) if scores else 0


def _combined_flags(analyses: list[dict[str, Any]]) -> list[str]:
    flags: list[str] = []
    for item in analyses:
        for flag in item.get("flags") or []:
            if str(flag) not in flags:
                flags.append(str(flag))
    return flags


def _visual_standards(examples: list[dict[str, Any]]) -> list[dict[str, str]]:
    archetypes = {str(item["archetype"]) for item in examples}
    standards = [
        {
            "id": "first_viewport_signal",
            "standard": "The first viewport must show identity, role, selected work/proof, and a clear next action.",
            "reject_if": "The generated UI opens with generic dashboard controls before the portfolio output is visible.",
        },
        {
            "id": "template_difference",
            "standard": "Template choices must change layout, story structure, and proof hierarchy, not just colors.",
            "reject_if": "Themes are only color swaps or decorative presets.",
        },
        {
            "id": "proof_card_hierarchy",
            "standard": "Project cards need screenshot, role, problem, outcome, metric/evidence, and links in a scan-friendly hierarchy.",
            "reject_if": "Project cards read like plain CRUD records or link lists.",
        },
        {
            "id": "preview_export_fidelity",
            "standard": "Live preview and exported HTML must preserve template layout, image labels, warnings, and contact path.",
            "reject_if": "Export output differs materially from preview or drops proof/alt text.",
        },
        {
            "id": "mobile_artifact_quality",
            "standard": "Mobile preview/export must keep the portfolio readable and keep contact/action surfaces visible.",
            "reject_if": "Mobile collapses into unusable editor chrome or hides outcomes/contact links.",
        },
    ]
    if "award_portfolio_site" in archetypes or "curated_showcase_site" in archetypes:
        standards.append(
            {
                "id": "authored_visual_identity",
                "standard": "The generated portfolio should feel authored and specific through type scale, spacing, image rhythm, and section order.",
                "reject_if": "The result looks like a neutral admin form with a preview bolted on.",
            }
        )
    if "template_or_builder_marketplace" in archetypes:
        standards.append(
            {
                "id": "selection_before_editing",
                "standard": "Offer a small set of strongly differentiated starting templates before deep editing.",
                "reject_if": "Users must fill a blank form before seeing what kind of site they are making.",
            }
        )
    if "case_study_gallery" in archetypes:
        standards.append(
            {
                "id": "case_study_depth",
                "standard": "At least one template must coach problem, role, process, outcome, and evidence.",
                "reject_if": "Case studies only capture title, description, and link.",
            }
        )
    return standards


def _axis_guidance(examples: list[dict[str, Any]]) -> dict[str, list[str]]:
    screenshot_titles = [
        str(item["title"])
        for item in examples
        if item["evidence_level"] == "screenshot" and int(item.get("pixel_quality_score") or 0) >= 55
    ][:6]
    title_note = ", ".join(screenshot_titles) if screenshot_titles else "no screenshot-backed examples yet"
    return {
        "minimalist-editorial": [
            "Borrow restraint from curated showcase and award-site references.",
            "Prioritize type hierarchy, case-study sequence, white space, and proof image rhythm.",
            f"Compare against screenshot-backed examples with usable pixel evidence: {title_note}.",
        ],
        "bold-marketing": [
            "Use award-site energy only when it makes the user's proof clearer.",
            "Hero must sell the creator's role and strongest project, not the builder product itself.",
            "Avoid fake logos, fake testimonials, and generic SaaS conversion sections.",
        ],
        "dense-dashboard": [
            "Borrow builder-marketplace clarity: template selection, readiness checks, preview/export confidence.",
            "Keep editing controls compact so the portfolio preview remains the visual center.",
            "Use status and checklist density for workflow, not decorative card sprawl.",
        ],
        "proof-first-case-study": [
            "Lean on Behance/case-study references for problem, role, process, outcome, evidence, and captions.",
            "Make one completed project proof state visible in the first generated screen.",
        ],
        "creator-studio": [
            "Balance creator profile, project inventory, visual proof, and contact readiness.",
            "Use marketplace/profile references as workflow signals, not network-scope features.",
        ],
    }


def _ui_team_requirements(examples: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "owner": "visual_design_lead",
            "requirement": "Map at least three screenshot-backed examples to template decisions before Dev Team implementation.",
        },
        {
            "owner": "ux_flow_lead",
            "requirement": "Start with intent/template selection and a preview payoff before long-form editing.",
        },
        {
            "owner": "asset_strategy_lead",
            "requirement": "Treat screenshots as user-owned proof; generated images may only be labelled placeholders/backgrounds.",
        },
        {
            "owner": "visual_qa_lead",
            "requirement": "Use reference screenshots as comparison targets for first viewport hierarchy and mobile/export fidelity.",
        },
        {
            "owner": "design_critic",
            "requirement": "Reject outputs where research is present only as text and not visible in template/layout decisions.",
        },
    ]


def _status(
    examples: list[dict[str, Any]],
    screenshot_backed: int,
    coverage: dict[str, Any],
    image_quality: dict[str, Any],
) -> str:
    if not examples:
        return "missing_examples"
    if screenshot_backed and int(image_quality.get("analyzed_screenshots") or 0) == 0:
        return "needs_readable_screenshots"
    if int(image_quality.get("weak_pixel_evidence") or 0) > int(image_quality.get("strong_pixel_evidence") or 0):
        return "needs_better_screenshot_quality"
    if screenshot_backed >= 5 and int(coverage["screenshot_backed_archetype_count"]) >= 3:
        return "pass"
    if screenshot_backed >= 2:
        return "needs_more_screenshot_diversity"
    return "needs_screenshots"


def _render_report(payload: dict[str, Any]) -> str:
    examples = payload["examples"]
    standards = payload["visual_standards"]
    axis_guidance = payload["axis_guidance"]
    ui_requirements = payload["ui_team_requirements"]
    example_rows = "\n".join(_example_row(index, item) for index, item in enumerate(examples, start=1)) or "| - | - | - | - | - | - |"
    image_quality = payload["image_quality"]
    image_quality_lines = "\n".join(
        [
            f"- Analyzed screenshots: {image_quality['analyzed_screenshots']}",
            f"- Average pixel score: {image_quality['average_score']}",
            f"- Score range: {image_quality['min_score']} - {image_quality['max_score']}",
            f"- Strong pixel evidence: {image_quality['strong_pixel_evidence']}",
            f"- Weak pixel evidence: {image_quality['weak_pixel_evidence']}",
            f"- Flags: {_flag_text(image_quality.get('flags', {}))}",
        ]
    )
    standard_lines = "\n".join(f"- **{item['id']}**: {item['standard']} Reject if: {item['reject_if']}" for item in standards)
    axis_sections = "\n\n".join(
        f"### {axis}\n\n" + "\n".join(f"- {line}" for line in lines)
        for axis, lines in axis_guidance.items()
    )
    requirement_lines = "\n".join(f"- **{item['owner']}**: {item['requirement']}" for item in ui_requirements)
    coverage = payload["source_coverage"]
    return f"""# Example Visual Critic

Status: `{payload['status']}`
Examples: {payload['example_count']}
Screenshot-backed examples: {payload['screenshot_backed']}
Image-analyzed screenshots: {payload['image_analyzed']}
Covered archetypes: {coverage['covered_archetype_count']}
Screenshot-backed archetypes: {coverage['screenshot_backed_archetype_count']}

## Pixel Evidence Summary

{image_quality_lines}

## Evidence Table

| Rank | Source | Archetype | Evidence | Pixel Score | Pixel Flags | Title | Screenshot |
| --- | --- | --- | --- | --- | --- | --- | --- |
{example_rows}

## Visual Standards

{standard_lines}

## Visual Direction Prompt Addendum

{axis_sections}

## UI Team Requirements

{requirement_lines}

## Gate Meaning

{payload['gate_meaning']}
"""


def _example_row(index: int, example: dict[str, Any]) -> str:
    screenshot = "<br>".join(example["screenshot_paths"]) or "-"
    title = str(example["title"]).replace("|", "\\|")
    flags = ", ".join(example.get("pixel_quality_flags") or []) or "-"
    return (
        f"| {index} | {example['source_name']} | {example['archetype']} | {example['evidence_level']} | "
        f"{example.get('pixel_quality_score', 0)} | {flags} | [{title}]({example.get('url') or ''}) | {screenshot} |"
    )


def _flag_text(flags: dict[str, int]) -> str:
    if not flags:
        return "none"
    return ", ".join(f"{key}={value}" for key, value in sorted(flags.items()))
