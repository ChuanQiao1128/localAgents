from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
from typing import Any

from orchestrator.agents.codex_multimodal_critic import _codex_command, _extract_summary, _run_codex


@dataclass(frozen=True)
class VisualVariantMultimodalReviewResult:
    prompt_path: Path
    output_path: Path
    json_path: Path
    report_path: Path
    selected_path: Path
    image_count: int
    status: str
    returncode: int
    winner_id: str | None


class VisualVariantMultimodalReviewAgent:
    def run(
        self,
        *,
        project: dict[str, Any],
        model: str = "gpt-5.5",
        timeout_seconds: int = 1200,
    ) -> VisualVariantMultimodalReviewResult:
        project_path = Path(project["path"])
        design_dir = project_path / "docs/design"
        artifact_dir = project_path / ".agent/artifacts/visual_directions"
        variants_path = artifact_dir / "variants.json"
        design_dir.mkdir(parents=True, exist_ok=True)
        variants_payload = _load_variants(variants_path)
        variants = _select_reviewable_variants(project_path, variants_payload)
        if not variants:
            raise ValueError(
                "No visual direction screenshots were found. Run `design directions` without `--no-screenshots` first."
            )

        prompt_path = design_dir / "visual-direction-multimodal-review-prompt.md"
        output_path = design_dir / "visual-direction-multimodal-review-output.md"
        json_path = design_dir / "visual-direction-multimodal-review.json"
        report_path = design_dir / "visual-direction-multimodal-review.md"
        selected_path = design_dir / "selected-visual-direction.md"

        prompt = _render_prompt(project_path=project_path, project=project, variants=variants)
        prompt_path.write_text(prompt, encoding="utf-8")
        images = [variant["image_path"] for variant in variants]
        completed = _run_codex(
            _codex_command(model=model, project_path=project_path, images=images),
            prompt=prompt,
            timeout_seconds=timeout_seconds,
        )
        output = (completed.stdout or "").strip()
        if completed.stderr:
            output = f"{output}\n\n<!-- stderr\n{completed.stderr.strip()}\n-->\n".strip()
        output_path.write_text(output + "\n", encoding="utf-8")
        winner_id = _parse_winner(output, {variant["id"] for variant in variants})
        status = "completed" if completed.returncode == 0 and output else "failed"
        payload = {
            "provider": "codex-cli",
            "model": model,
            "status": status,
            "returncode": completed.returncode,
            "winner_id": winner_id,
            "image_count": len(images),
            "variants": [
                {
                    "id": variant["id"],
                    "name": variant["name"],
                    "axis": variant.get("axis", ""),
                    "image_path": str(variant["image_path"].relative_to(project_path)),
                    "rubric_total": variant.get("rubric_total"),
                }
                for variant in variants
            ],
            "prompt_path": str(prompt_path.relative_to(project_path)),
            "output_path": str(output_path.relative_to(project_path)),
            "summary": _extract_summary(output),
        }
        json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_path.write_text(_render_report(payload, output), encoding="utf-8")
        _write_selected_direction(selected_path, payload, output, variants_payload)
        _attach_review_to_variants(variants_path, variants_payload, payload)

        return VisualVariantMultimodalReviewResult(
            prompt_path=prompt_path,
            output_path=output_path,
            json_path=json_path,
            report_path=report_path,
            selected_path=selected_path,
            image_count=len(images),
            status=status,
            returncode=completed.returncode,
            winner_id=winner_id,
        )


def _load_variants(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError("Visual direction variants were not found. Run `design directions` first.")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Could not read visual direction variants JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Visual direction variants JSON must be an object.")
    return payload


def _select_reviewable_variants(project_path: Path, payload: dict[str, Any]) -> list[dict[str, Any]]:
    reviewable: list[dict[str, Any]] = []
    for variant in payload.get("variants") or []:
        if not isinstance(variant, dict):
            continue
        image_path = _variant_image_path(project_path, variant)
        if not image_path:
            continue
        reviewable.append(
            {
                "id": str(variant.get("id") or image_path.parent.name),
                "name": str(variant.get("name") or variant.get("id") or image_path.parent.name),
                "axis": str(variant.get("axis") or ""),
                "provider": str(variant.get("provider") or ""),
                "status": str(variant.get("status") or ""),
                "image_path": image_path,
                "rubric_total": (variant.get("scores") or {}).get("total"),
            }
        )
    return reviewable


def _variant_image_path(project_path: Path, variant: dict[str, Any]) -> Path | None:
    candidates: list[Path] = []
    if variant.get("screenshot_path"):
        candidates.append(project_path / str(variant["screenshot_path"]))
    variant_id = str(variant.get("id") or "")
    if variant_id:
        candidates.append(project_path / ".agent/artifacts/visual_directions" / variant_id / "screenshot.png")
    for candidate in candidates:
        if candidate.exists() and candidate.suffix.lower() == ".png":
            return candidate
    return None


def _render_prompt(*, project_path: Path, project: dict[str, Any], variants: list[dict[str, Any]]) -> str:
    variant_lines = "\n".join(
        f"{index}. `{variant['id']}` - {variant['name']} | axis: {variant.get('axis', '')} | screenshot: {variant['image_path'].name}"
        for index, variant in enumerate(variants, start=1)
    )
    reference_context = _read_context(
        project_path,
        [
            "docs/product/example-references/multimodal-critic.md",
            "docs/product/example-references/visual-critic.md",
            "docs/design/ui-team-dev-handoff.md",
            "docs/design/design-contract.json",
            "docs/product/prd.md",
        ],
        limit=8000,
    )
    return f"""你是一名资深产品设计负责人、UI/UX 设计评审和前端实现顾问。你正在评审 Local Agent Dev Studio 为同一个产品生成的 visual direction 截图。

项目目标：
{project.get("idea", "")}

请按图片顺序评审这些候选方向：
{variant_lines}

已有产品/视觉研究上下文：
{reference_context or "暂无。"}

请用中文输出 Markdown，并严格包含下面结构：

## Winner

Winner: `<variant_id>`

必须只从这些 id 中选择一个：{", ".join(f"`{variant['id']}`" for variant in variants)}

## Pairwise Review

逐对比较候选方向。每一对都要说明：
- 哪个更好
- 为什么
- 哪个风险更大

## Rubric Scores

给每个方向按 1-10 分评分：
- first viewport signal
- visual quality
- information hierarchy
- portfolio proof strength
- builder workflow clarity
- reference alignment
- implementation feasibility
- mobile/export risk

## Developer Handoff

把 winner 转成 Developer Team 可以执行的要求：
- 首屏布局
- template/preview/workflow 关系
- project proof card 要求
- dashboard 与 portfolio export preview 的关系
- 不应复制的参考站点元素

## Rejection Notes

说明没有获胜的方向为什么不应该作为默认实现。
"""


def _read_context(project_path: Path, relative_paths: list[str], *, limit: int) -> str:
    sections: list[str] = []
    remaining = limit
    for relative_path in relative_paths:
        path = project_path / relative_path
        if not path.exists() or remaining <= 0:
            continue
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            continue
        chunk = f"\n\n# {relative_path}\n\n{text}"
        sections.append(chunk[:remaining])
        remaining -= len(sections[-1])
    return "\n".join(sections)


def _parse_winner(output: str, valid_ids: set[str]) -> str | None:
    patterns = [
        r"Winner:\s*`([^`]+)`",
        r"Winner:\s*([A-Za-z0-9_-]+)",
        r"获胜(?:方向|者)?[:：]\s*`?([A-Za-z0-9_-]+)`?",
    ]
    for pattern in patterns:
        match = re.search(pattern, output, flags=re.IGNORECASE)
        if match and match.group(1) in valid_ids:
            return match.group(1)
    for variant_id in valid_ids:
        if f"`{variant_id}`" in output:
            return variant_id
    return None


def _render_report(payload: dict[str, Any], output: str) -> str:
    rows = "\n".join(
        f"| {variant['id']} | {variant['name']} | {variant.get('rubric_total') or ''} | {variant['image_path']} |"
        for variant in payload["variants"]
    )
    return f"""# Visual Direction Multimodal Review

Status: `{payload['status']}`
Model: `{payload['model']}`
Winner: `{payload.get('winner_id') or 'not_parsed'}`
Images: {payload['image_count']}
Return code: {payload['returncode']}

## Reviewed Variants

| Id | Name | Prior Rubric | Screenshot |
| --- | --- | ---: | --- |
{rows}

## Codex CLI Review

{output.strip() or "Codex CLI did not return output."}
"""


def _write_selected_direction(
    path: Path,
    payload: dict[str, Any],
    output: str,
    variants_payload: dict[str, Any],
) -> None:
    winner_id = payload.get("winner_id")
    winner = next((item for item in payload["variants"] if item["id"] == winner_id), None)
    if not winner:
        existing = variants_payload.get("winner")
        if isinstance(existing, dict):
            winner = {
                "id": existing.get("id", "not_parsed"),
                "name": existing.get("name", "Previous selected direction"),
                "image_path": existing.get("screenshot_path", ""),
            }
    winner_label = f"`{winner['id']}` - {winner['name']}" if winner else "`not_parsed`"
    path.write_text(
        f"""# Selected Visual Direction

Winner: {winner_label}

## Selection Source

Selected by Codex CLI multimodal review when `winner_id` is parsed. If parsing failed, keep the previous deterministic winner and inspect the review manually.

## Multimodal Review Summary

{_extract_summary(output)}

## Developer Handoff

Use this direction as the visual source of truth before implementation. Preserve the chosen axis, layout density, typography intent, proof workflow, image lifecycle, and responsive behavior.
""",
        encoding="utf-8",
    )


def _attach_review_to_variants(path: Path, variants_payload: dict[str, Any], payload: dict[str, Any]) -> None:
    variants_payload["multimodal_review"] = {
        "provider": payload["provider"],
        "model": payload["model"],
        "status": payload["status"],
        "winner_id": payload.get("winner_id"),
        "image_count": payload["image_count"],
        "report_path": "docs/design/visual-direction-multimodal-review.md",
        "json_path": "docs/design/visual-direction-multimodal-review.json",
    }
    path.write_text(json.dumps(variants_payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
