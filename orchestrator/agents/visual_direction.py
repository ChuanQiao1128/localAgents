"""DEPRECATED — relies on the removed v0 Platform API.

This agent generated multiple UI variants via v0.dev. Since the v0 paid API was
removed (see orchestrator/tools/v0_tools.py), instantiating ``V0Tools``/
``V0ApiClient`` now raises and this agent will fail at runtime.

The CLI subcommands ``orchestrator design directions`` and
``orchestrator design v0-smoke`` were unwired; nothing in the active workflow
imports this module. It is left in place so a future rewrite can reuse the
variant axes, prompt rendering, and pairwise-critic logic, swapping the
generation backend for Claude CLI.

Module-level imports below resolve because the v0_tools tombstone preserves the
public class names; only ``__init__`` raises.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
from typing import Any

from orchestrator.core.artifact_store import ArtifactStore
from orchestrator.core.event_bus import EventBus
from orchestrator.db import Database
from orchestrator.agents.screenshot_image_analysis import analyze_screenshot
from orchestrator.tools.v0_tools import V0ApiClient, V0GeneratedFile, V0Result, V0Tools


@dataclass(frozen=True)
class VisualDirectionResult:
    overview_path: Path
    pairwise_path: Path
    selected_path: Path
    variants_json_path: Path
    provider: str
    winner_id: str
    variant_count: int


class VisualDirectionAgent:
    def __init__(self, db: Database | None = None):
        self.db = db

    def run(
        self,
        *,
        project: dict[str, Any],
        run_id: str | None,
        provider: str = "auto",
        capture_screenshots: bool = True,
        v0_timeout_seconds: int | None = None,
        v0_request_timeout_seconds: int | None = None,
        v0_model_id: str | None = None,
        v0_retries: int = 1,
        min_successful_variants: int | None = None,
        allow_partial: bool = False,
        prompt_mode: str = "concise",
        variant_ids: list[str] | None = None,
        progress: Any | None = None,
    ) -> VisualDirectionResult:
        if prompt_mode not in {"concise", "full"}:
            raise ValueError("Visual direction prompt mode must be concise or full.")
        project_path = Path(project["path"])
        design_dir = project_path / "docs/design"
        artifact_dir = project_path / ".agent/artifacts/visual_directions"
        design_dir.mkdir(parents=True, exist_ok=True)
        artifact_dir.mkdir(parents=True, exist_ok=True)

        resolved_provider = _resolve_provider(provider)
        context = _load_context(project_path, str(project.get("idea", "")))
        domain_type = _domain_type(context)
        axes = _variant_axes(domain_type)
        if variant_ids:
            known_axis_ids = {axis["id"] for axis in axes}
            axes = axes + [axis for axis in _fallback_variant_axes(domain_type) if axis["id"] not in known_axis_ids]
            requested = set(variant_ids)
            axes = [axis for axis in axes if axis["id"] in requested]
            missing = requested - {axis["id"] for axis in axes}
            if missing:
                raise ValueError(f"Unknown visual direction variant id(s): {', '.join(sorted(missing))}")
        variants: list[dict[str, Any]] = []
        v0_project_id: str | None = None
        required_successes = min_successful_variants
        if required_successes is None and resolved_provider == "v0" and not variant_ids and not allow_partial:
            required_successes = 3
        if allow_partial:
            required_successes = 1

        axes_queue = list(axes)
        processed_axis_ids: set[str] = set()
        while axes_queue:
            axis = axes_queue.pop(0)
            processed_axis_ids.add(axis["id"])
            _emit(progress, f"[{axis['id']}] rendering prompt")
            prompt = _render_v0_prompt(axis, context, prompt_mode=prompt_mode)
            variant_dir = artifact_dir / axis["id"]
            variant_dir.mkdir(parents=True, exist_ok=True)
            (variant_dir / "prompt.md").write_text(prompt, encoding="utf-8")

            result, error_message = _generate_variant_with_retries(
                provider=resolved_provider,
                prompt=prompt,
                axis=axis,
                project_name=str(project.get("name") or project.get("id") or "Agent Studio Project"),
                v0_project_id=v0_project_id,
                v0_timeout_seconds=v0_timeout_seconds,
                v0_request_timeout_seconds=v0_request_timeout_seconds,
                v0_model_id=v0_model_id,
                retries=v0_retries,
                progress=progress,
            )
            saved_files = V0Tools().save_to_artifacts(result, variant_dir / "files") if result.files else []
            if result.demo_url:
                _emit(progress, f"[{axis['id']}] demo URL: {result.demo_url}")
            screenshot_path = _capture_variant_screenshot(result, variant_dir, capture_screenshots)
            if screenshot_path:
                _emit(progress, f"[{axis['id']}] screenshot captured: {screenshot_path}")
            screenshot_quality = _evaluate_screenshot(screenshot_path) if screenshot_path else None
            status = result.status
            screenshot_error = None
            if screenshot_quality and not screenshot_quality["valid"]:
                status = "screenshot_failed" if result.status == "completed" else result.status
                screenshot_error = screenshot_quality["summary"]
                _emit(progress, f"[{axis['id']}] screenshot quality failed: {screenshot_quality['summary']}")
            variant = {
                "id": axis["id"],
                "name": axis["name"],
                "axis": axis["axis"],
                "provider": result.provider,
                "status": status,
                "chat_id": result.chat_id,
                "version_id": result.version_id,
                "demo_url": result.demo_url,
                "screenshot_url": result.screenshot_url,
                "web_url": result.web_url,
                "prompt_path": str((variant_dir / "prompt.md").relative_to(project_path)),
                "screenshot_path": str(screenshot_path.relative_to(project_path)) if screenshot_path else None,
                "screenshot_quality": screenshot_quality,
                "files": [str(path.relative_to(project_path)) for path in saved_files],
                "error": error_message or screenshot_error,
                "scores": _score_variant(axis, context, bool(screenshot_path), screenshot_quality),
            }
            (variant_dir / "metadata.json").write_text(json.dumps(variant, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            variants.append(variant)
            successful_count = len([item for item in variants if _variant_successful(item)])
            if (
                resolved_provider == "v0"
                and not variant_ids
                and required_successes
                and successful_count > 0
                and successful_count < required_successes
                and not axes_queue
            ):
                for fallback in _fallback_variant_axes(domain_type):
                    if fallback["id"] not in processed_axis_ids and all(item["id"] != fallback["id"] for item in axes_queue):
                        _emit(progress, f"[{fallback['id']}] queued fallback visual axis because only {successful_count}/{required_successes} v0 variants succeeded")
                        axes_queue.append(fallback)
                        break

        if not variants:
            raise ValueError("No visual direction variants were selected.")

        comparisons = _pairwise_comparisons(variants)
        winner = _winner(variants, comparisons)

        overview_path = design_dir / "visual-directions.md"
        pairwise_path = design_dir / "visual-direction-pairwise.md"
        selected_path = design_dir / "selected-visual-direction.md"
        variants_json_path = artifact_dir / "variants.json"
        overview_path.write_text(_render_overview(variants, winner), encoding="utf-8")
        pairwise_path.write_text(_render_pairwise(comparisons), encoding="utf-8")
        selected_path.write_text(_render_selected(winner, comparisons), encoding="utf-8")
        variants_json_path.write_text(
            json.dumps({"provider": resolved_provider, "variants": variants, "winner": winner, "comparisons": comparisons}, ensure_ascii=False, indent=2)
            + "\n",
            encoding="utf-8",
        )

        successful_variants = [variant for variant in variants if _variant_successful(variant)]
        if resolved_provider == "v0" and not successful_variants:
            raise RuntimeError(f"v0 did not complete any visual direction variant. Inspect errors in {variants_json_path}")
        if resolved_provider == "v0" and required_successes and len(successful_variants) < required_successes:
            raise RuntimeError(
                f"v0 completed only {len(successful_variants)}/{required_successes} required visual direction variants. "
                f"Inspect errors in {variants_json_path}, retry with a higher --v0-request-timeout, or pass --allow-partial."
            )

        if self.db and run_id:
            artifacts = ArtifactStore(self.db)
            for path in [overview_path, pairwise_path, selected_path, variants_json_path]:
                artifacts.register(
                    project_id=project["id"],
                    run_id=run_id,
                    phase_id="design",
                    path=str(path.relative_to(project_path)),
                    kind="json" if path.suffix == ".json" else "markdown",
                    summary="Visual direction variant artifact.",
                )
            EventBus(self.db).emit(
                event_type="design.visual_directions_completed",
                project_id=project["id"],
                run_id=run_id,
                phase_id="design",
                message=f"Generated {len(variants)} visual direction variants. Winner: {winner['id']}.",
                payload={"provider": resolved_provider, "winner": winner["id"], "variants": len(variants)},
            )

        return VisualDirectionResult(
            overview_path=overview_path,
            pairwise_path=pairwise_path,
            selected_path=selected_path,
            variants_json_path=variants_json_path,
            provider=resolved_provider,
            winner_id=winner["id"],
            variant_count=len(variants),
        )


def _resolve_provider(provider: str) -> str:
    if provider not in {"auto", "mock", "v0"}:
        raise ValueError("Visual direction provider must be auto, mock, or v0.")
    if provider == "auto":
        return "v0" if os.environ.get("V0_API_KEY") else "mock"
    if provider == "v0" and not os.environ.get("V0_API_KEY"):
        raise ValueError("V0_API_KEY is required when --provider v0 is used.")
    return provider


def _load_context(project_path: Path, idea: str) -> dict[str, str]:
    paths = [
        "docs/product/prd.md",
        "docs/product/product-fit.md",
        "docs/product/prd-critique.md",
        "docs/product/reference-products/index.md",
        "docs/product/example-references/top-examples.md",
        "docs/product/example-references/visual-critic.md",
        "docs/product/example-references/multimodal-critic.md",
        "docs/product/ux-patterns.md",
        "docs/design/user-flow.md",
        "docs/design/design-system.md",
        "docs/design/component-spec.md",
        "docs/design/design-critique.md",
        "docs/design/ui-team-dev-handoff.md",
    ]
    loaded = {"idea": idea}
    for relative_path in paths:
        path = project_path / relative_path
        loaded[relative_path] = path.read_text(encoding="utf-8") if path.exists() else ""
    return loaded


def _domain_type(context: dict[str, str]) -> str:
    combined = "\n".join(context.values()).lower()
    if any(term in combined for term in ["portfolio", "作品集", "personal site"]):
        return "portfolio"
    if any(term in combined for term in ["expense", "invoice", "dashboard", "analytics"]):
        return "dashboard"
    return "web_app"


def _variant_axes(domain_type: str) -> list[dict[str, str]]:
    product_note = (
        "portfolio credibility, proof-of-work storytelling, screenshot-first project cards, and export confidence"
        if domain_type == "portfolio"
        else "clear product value, fast comprehension, and production-ready interaction states"
    )
    return [
        {
            "id": "minimalist-editorial",
            "name": "Minimalist Editorial",
            "axis": "Quiet portfolio/editorial system with strong typography, generous whitespace, case-study storytelling, and premium restraint.",
            "product_note": product_note,
        },
        {
            "id": "bold-marketing",
            "name": "Bold Marketing",
            "axis": "High-contrast launch-page energy with confident hero composition, conversion-oriented hierarchy, and expressive visual moments.",
            "product_note": product_note,
        },
        {
            "id": "dense-dashboard",
            "name": "Dense Dashboard",
            "axis": "Work-focused builder interface with compact controls, clear preview states, progress quality checks, and efficient repeated editing.",
            "product_note": product_note,
        },
    ]


def _fallback_variant_axes(domain_type: str) -> list[dict[str, str]]:
    product_note = (
        "portfolio credibility, proof-of-work storytelling, screenshot-first project cards, and export confidence"
        if domain_type == "portfolio"
        else "clear product value, fast comprehension, and production-ready interaction states"
    )
    return [
        {
            "id": "proof-first-case-study",
            "name": "Proof-First Case Study",
            "axis": "Portfolio-grade case-study interface focused on screenshots, role, problem, process, outcomes, metrics, and credibility proof.",
            "product_note": product_note,
        },
        {
            "id": "creator-studio",
            "name": "Creator Studio",
            "axis": "Elegant creator workspace with calm editing, project proof inventory, publish readiness, and polished portfolio preview.",
            "product_note": product_note,
        },
    ]


def _render_v0_prompt(axis: dict[str, str], context: dict[str, str], *, prompt_mode: str = "concise") -> str:
    if prompt_mode == "full":
        prd_limit = 2200
        design_limit = 1800
        references_limit = 2200
        brief = "Create a polished responsive web app visual direction as production-quality React/Next.js UI."
        secondary_state = "at least one meaningful secondary state"
    else:
        prd_limit = 700
        design_limit = 500
        references_limit = 350
        brief = "Create one fast, polished React/Next.js visual direction for the first usable screen."
        secondary_state = "one compact secondary/editing state"

    prd = _excerpt(context.get("docs/product/prd.md", ""), prd_limit)
    design = _excerpt("\n".join([context.get("docs/design/user-flow.md", ""), context.get("docs/design/component-spec.md", "")]), design_limit)
    references = _excerpt(
        "\n".join(
            [
                context.get("docs/product/example-references/multimodal-critic.md", ""),
                context.get("docs/product/example-references/visual-critic.md", ""),
                context.get("docs/product/example-references/top-examples.md", ""),
                context.get("docs/product/reference-products/index.md", ""),
            ]
        ),
        references_limit,
    )
    return f"""Create a polished responsive web app visual direction as production-quality React/Next.js UI.

Project idea:
{context.get('idea', '')}

Visual direction axis:
{axis['name']} - {axis['axis']}

Product objective:
Use this direction to support {axis['product_note']}.

Hard constraints:
- {brief}
- Generate a complete usable first screen plus {secondary_state}.
- Do not produce a generic SaaS dashboard unless the axis explicitly calls for dense dashboard.
- Use real product UI patterns, not marketing filler.
- Include upload/replace/remove screenshot states, image alt text, template/style choice, live preview, validation states, and export readiness if the domain is portfolio.
- No fake headshots, fake client logos, fake testimonials, fake credentials, or fabricated project screenshots.
- Make desktop and mobile layouts screenshot-worthy.
- Keep visual direction distinct from the other axes; optimize for this axis only.
- Keep generation lightweight: avoid image generation, large asset libraries, and unnecessary extra pages.

PRD context:
{prd}

Existing design context:
{design}

Reference context:
{references}
"""


def _generate_variant(
    *,
    provider: str,
    prompt: str,
    axis: dict[str, str],
    project_name: str,
    v0_project_id: str | None,
    v0_timeout_seconds: int | None,
    v0_request_timeout_seconds: int | None,
    v0_model_id: str | None,
    progress: Any | None,
) -> V0Result:
    if provider == "v0":
        system = "You are a senior product designer and frontend engineer. Generate polished, production-ready UI with strong visual direction."
        return V0ApiClient(
            model_id=v0_model_id,
            timeout_seconds=v0_timeout_seconds,
            request_timeout_seconds=v0_request_timeout_seconds,
            progress=progress,
        ).generate(prompt, system=system, project_id=v0_project_id)
    return _mock_variant(prompt, axis, project_name)


def _generate_variant_with_retries(
    *,
    provider: str,
    prompt: str,
    axis: dict[str, str],
    project_name: str,
    v0_project_id: str | None,
    v0_timeout_seconds: int | None,
    v0_request_timeout_seconds: int | None,
    v0_model_id: str | None,
    retries: int,
    progress: Any | None,
) -> tuple[V0Result, str | None]:
    attempts = 1 if provider != "v0" else max(1, retries + 1)
    last_error: str | None = None
    for attempt in range(1, attempts + 1):
        try:
            suffix = f" attempt {attempt}/{attempts}" if attempts > 1 else ""
            _emit(progress, f"[{axis['id']}] generating via {provider}{suffix}")
            result = _generate_variant(
                provider=provider,
                prompt=prompt,
                axis=axis,
                project_name=project_name,
                v0_project_id=v0_project_id,
                v0_timeout_seconds=v0_timeout_seconds,
                v0_request_timeout_seconds=v0_request_timeout_seconds,
                v0_model_id=v0_model_id,
                progress=lambda message, axis_id=axis["id"]: _emit(progress, f"[{axis_id}] {message}"),
            )
            return result, None
        except Exception as exc:
            last_error = str(exc)
            _emit(progress, f"[{axis['id']}] attempt {attempt}/{attempts} failed: {last_error}")
            if provider == "v0" and attempt < attempts:
                time.sleep(min(2.0 * attempt, 5.0))
    result = V0Result(
        prompt=prompt,
        files=[],
        provider=provider,
        status="failed",
        raw={"error": last_error or "unknown error", "axis": axis},
    )
    _emit(progress, f"[{axis['id']}] failed: {last_error or 'unknown error'}")
    return result, last_error or "unknown error"


def _mock_variant(prompt: str, axis: dict[str, str], project_name: str) -> V0Result:
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{axis['name']} Direction</title>
  <style>{_mock_css(axis['id'])}</style>
</head>
<body>
  <main class="direction">
    <section class="hero">
      <p class="eyebrow">{axis['name']}</p>
      <h1>{_mock_headline(axis['id'])}</h1>
      <p class="lede">{axis['axis']}</p>
      <div class="actions"><button>Preview portfolio</button><button class="secondary">Review quality</button></div>
    </section>
    <section class="surface">
      <div class="editor">
        <h2>Project proof builder</h2>
        <label>Problem<input value="Independent builders need credible proof."></label>
        <label>Outcome<input value="Export a persuasive portfolio page."></label>
        <label>Image alt text<input value="Screenshot of portfolio case study."></label>
      </div>
      <article class="preview">
        <div class="shot"></div>
        <h2>{project_name}</h2>
        <p>Case study proof template with screenshot lifecycle, metrics, links, and export readiness.</p>
        <div class="tags"><span>Problem</span><span>Process</span><span>Outcome</span><span>Metrics</span></div>
      </article>
    </section>
  </main>
</body>
</html>
"""
    return V0Result(
        prompt=prompt,
        files=[V0GeneratedFile(path="index.html", content=html)],
        demo_url=None,
        provider="mock",
        status="completed",
        raw={"axis": axis},
    )


def _mock_css(axis_id: str) -> str:
    if axis_id == "bold-marketing":
        return "body{margin:0;font-family:Inter,system-ui,sans-serif;background:#141414;color:#fff}.direction{padding:48px;min-height:100vh;background:radial-gradient(circle at 20% 20%,#ffdf6e22,transparent 28%),#141414}.eyebrow{color:#ffdf6e;text-transform:uppercase;letter-spacing:.08em}.hero{max-width:980px}.hero h1{font-size:68px;line-height:.94;margin:0 0 18px}.lede{font-size:20px;color:#d7d7d7;max-width:760px}.actions{display:flex;gap:12px;margin-top:26px}button{border:0;border-radius:6px;padding:12px 16px;font-weight:700;background:#ffdf6e;color:#141414}.secondary{background:#282828;color:#fff}.surface{display:grid;grid-template-columns:360px 1fr;gap:18px;margin-top:38px}.editor,.preview{border:1px solid #3b3b3b;background:#1e1e1e;border-radius:8px;padding:22px}.shot{height:220px;background:linear-gradient(135deg,#ffdf6e,#ff6b6b);border-radius:6px}.tags{display:flex;gap:8px;flex-wrap:wrap}.tags span{border:1px solid #555;border-radius:999px;padding:4px 9px}label{display:grid;gap:6px;margin-top:12px}input{border:1px solid #555;border-radius:6px;background:#111;color:#fff;padding:10px}@media(max-width:760px){.direction{padding:24px}.hero h1{font-size:42px}.surface{grid-template-columns:1fr}}"
    if axis_id == "dense-dashboard":
        return "body{margin:0;font-family:Inter,system-ui,sans-serif;background:#eef1f5;color:#20242c}.direction{padding:20px;max-width:1440px;margin:auto}.eyebrow{color:#576071;text-transform:uppercase;font-size:12px}.hero{display:grid;grid-template-columns:1fr auto;gap:16px;align-items:end}.hero h1{font-size:34px;line-height:1;margin:0}.lede{color:#667085;max-width:760px}.actions{display:flex;gap:8px}button{border:1px solid #cfd6df;border-radius:6px;padding:9px 12px;background:#fff}.secondary{background:#2c6f78;color:#fff}.surface{display:grid;grid-template-columns:420px 1fr;gap:14px;margin-top:18px}.editor,.preview{border:1px solid #cfd6df;background:#fff;border-radius:8px;padding:16px}.shot{height:260px;background:repeating-linear-gradient(90deg,#d8dee8,#d8dee8 20px,#edf1f5 20px,#edf1f5 40px);border-radius:6px}.tags{display:flex;gap:6px;flex-wrap:wrap}.tags span{background:#eef1f5;border-radius:999px;padding:4px 9px}label{display:grid;gap:6px;margin-top:10px;font-size:13px}input{border:1px solid #cfd6df;border-radius:6px;background:#f7f8fa;padding:9px}@media(max-width:900px){.hero,.surface{grid-template-columns:1fr}}"
    return "body{margin:0;font-family:Inter,Georgia,system-ui,sans-serif;background:#f4f1eb;color:#1f2933}.direction{padding:44px;max-width:1180px;margin:auto}.eyebrow{color:#7c6f5f;text-transform:uppercase;letter-spacing:.08em}.hero{max-width:880px}.hero h1{font-size:58px;line-height:.98;margin:0 0 18px}.lede{font-size:19px;color:#5f6673;max-width:740px}.actions{display:flex;gap:12px;margin-top:24px}button{border:1px solid #1f2933;border-radius:6px;padding:11px 15px;background:#1f2933;color:#fff}.secondary{background:transparent;color:#1f2933}.surface{display:grid;grid-template-columns:360px 1fr;gap:20px;margin-top:42px}.editor,.preview{border:1px solid #d7d0c3;background:#fbfaf7;border-radius:8px;padding:22px}.shot{height:260px;background:linear-gradient(135deg,#e7dccb,#c8d8dc);border-radius:6px}.tags{display:flex;gap:8px;flex-wrap:wrap}.tags span{border:1px solid #d7d0c3;border-radius:999px;padding:4px 9px}label{display:grid;gap:6px;margin-top:12px}input{border:1px solid #d7d0c3;border-radius:6px;background:#fff;padding:10px}@media(max-width:760px){.direction{padding:24px}.hero h1{font-size:40px}.surface{grid-template-columns:1fr}}"


def _mock_headline(axis_id: str) -> str:
    if axis_id == "bold-marketing":
        return "Turn proof into a portfolio people remember."
    if axis_id == "dense-dashboard":
        return "Build, score, and export every portfolio section."
    return "Shape your work into a publishable story."


def _capture_variant_screenshot(result: V0Result, variant_dir: Path, capture: bool) -> Path | None:
    if not capture:
        return None
    url = result.demo_url
    if not url and result.files:
        index = next((item for item in result.files if item.path.endswith("index.html")), None)
        if index:
            preview_path = variant_dir / "preview.html"
            preview_path.write_text(index.content, encoding="utf-8")
            url = preview_path.resolve().as_uri()
    if not url:
        return None
    return _capture_chrome(url, variant_dir / "screenshot.png")


def _capture_chrome(url: str, output_path: Path) -> Path | None:
    chrome = _find_chrome()
    if not chrome:
        return None
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    with tempfile.TemporaryDirectory(prefix="agent-studio-v0-") as profile_dir:
        command = [
            chrome,
            "--headless",
            "--disable-gpu",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--allow-file-access-from-files",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=1200",
            f"--user-data-dir={profile_dir}",
            "--window-size=1440,1000",
            f"--screenshot={output_path}",
            url,
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if output_path.exists() and output_path.stat().st_size > 0:
                break
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=2)
    return output_path if output_path.exists() and output_path.stat().st_size > 0 else None


def _find_chrome() -> str | None:
    for candidate in [
        shutil.which("chromium"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
    ]:
        if candidate and Path(candidate).exists():
            return str(candidate)
    return None


def _score_variant(
    axis: dict[str, str],
    context: dict[str, str],
    has_screenshot: bool,
    screenshot_quality: dict[str, Any] | None = None,
) -> dict[str, int]:
    domain = _domain_type(context)
    base_by_axis = {
        "minimalist-editorial": {
            "information_hierarchy": 18,
            "contrast": 16,
            "whitespace": 19,
            "brand_consistency": 18,
            "workflow_fit": 17 if domain == "portfolio" else 14,
            "implementation_readiness": 16,
        },
        "bold-marketing": {
            "information_hierarchy": 16,
            "contrast": 19,
            "whitespace": 14,
            "brand_consistency": 16,
            "workflow_fit": 14,
            "implementation_readiness": 15,
        },
        "dense-dashboard": {
            "information_hierarchy": 17,
            "contrast": 15,
            "whitespace": 12,
            "brand_consistency": 14,
            "workflow_fit": 19,
            "implementation_readiness": 18,
        },
        "proof-first-case-study": {
            "information_hierarchy": 18,
            "contrast": 16,
            "whitespace": 16,
            "brand_consistency": 17,
            "workflow_fit": 19 if domain == "portfolio" else 16,
            "implementation_readiness": 16,
        },
        "creator-studio": {
            "information_hierarchy": 17,
            "contrast": 16,
            "whitespace": 17,
            "brand_consistency": 17,
            "workflow_fit": 18,
            "implementation_readiness": 16,
        },
    }
    base = base_by_axis.get(axis["id"], base_by_axis["minimalist-editorial"])
    scores = dict(base)
    valid_screenshot = has_screenshot and (screenshot_quality is None or bool(screenshot_quality.get("valid")))
    scores["screenshot_evidence"] = 5 if valid_screenshot else 0
    scores["total"] = sum(scores.values())
    return scores


def _evaluate_screenshot(path: Path) -> dict[str, Any]:
    analysis = analyze_screenshot(path)
    flags = list(analysis.get("flags") or [])
    score = int(analysis.get("score") or 0)
    valid = analysis.get("status") == "analyzed" and score >= 35 and "blank_or_failed_capture" not in flags
    return {
        "status": analysis.get("status"),
        "score": score,
        "flags": flags,
        "summary": analysis.get("summary") or analysis.get("error") or "",
        "valid": valid,
    }


def _pairwise_comparisons(variants: list[dict[str, Any]]) -> list[dict[str, str]]:
    comparisons: list[dict[str, str]] = []
    for left_index, left in enumerate(variants):
        for right in variants[left_index + 1 :]:
            winner = left if left["scores"]["total"] >= right["scores"]["total"] else right
            loser = right if winner is left else left
            comparisons.append(
                {
                    "left": left["id"],
                    "right": right["id"],
                    "winner": winner["id"],
                    "rationale": _pairwise_rationale(winner, loser),
                }
            )
    return comparisons


def _pairwise_rationale(winner: dict[str, Any], loser: dict[str, Any]) -> str:
    winner_scores = winner["scores"]
    loser_scores = loser["scores"]
    deltas = [
        (key, winner_scores[key] - loser_scores.get(key, 0))
        for key in ["information_hierarchy", "contrast", "whitespace", "brand_consistency", "workflow_fit", "implementation_readiness"]
    ]
    strongest = max(deltas, key=lambda item: item[1])
    return (
        f"{winner['name']} beats {loser['name']} because it has stronger {strongest[0].replace('_', ' ')} "
        f"and a better balance between visual direction and implementation readiness."
    )


def _winner(variants: list[dict[str, Any]], comparisons: list[dict[str, str]]) -> dict[str, Any]:
    successful = [variant for variant in variants if _variant_successful(variant)]
    if successful:
        variants = successful
    wins = {variant["id"]: 0 for variant in variants}
    for comparison in comparisons:
        if comparison["winner"] in wins:
            wins[comparison["winner"]] += 1
    return max(variants, key=lambda item: (wins[item["id"]], item["scores"]["total"]))


def _render_overview(variants: list[dict[str, Any]], winner: dict[str, Any]) -> str:
    rows = "\n".join(
        f"| {variant['name']} | {variant['id']} | {variant['provider']} | {variant['status']} | {variant['scores']['total']}/125 | {_variant_demo_label(variant)} |"
        for variant in variants
    )
    return f"""# Visual Directions

## Variants

| Direction | Id | Provider | Status | Rubric Score | Demo |
| --- | --- | --- | --- | ---: | --- |
{rows}

## Selected Direction

`{winner['id']}` - {winner['name']}

## Rule

Variants must be generated from explicit opposing axes. The critic uses rubric dimensions plus pairwise comparison; the selected direction is the only one handed to Developer Agent by default.
"""


def _variant_successful(variant: dict[str, Any]) -> bool:
    return variant.get("status") == "completed" and bool(variant.get("demo_url") or variant.get("files") or variant.get("screenshot_path"))


def _variant_demo_label(variant: dict[str, Any]) -> str:
    if variant.get("demo_url"):
        return str(variant["demo_url"])
    if variant.get("screenshot_path"):
        return str(variant["screenshot_path"])
    if variant.get("files"):
        return "local/mock"
    if variant.get("error"):
        return "failed"
    return "none"


def _render_pairwise(comparisons: list[dict[str, str]]) -> str:
    rows = "\n".join(f"| {item['left']} vs {item['right']} | {item['winner']} | {item['rationale']} |" for item in comparisons)
    return f"""# Pairwise UI Critic

## Rubric

- Information hierarchy
- Contrast
- Whitespace
- Brand consistency
- Workflow fit
- Implementation readiness
- Screenshot evidence

## Comparisons

| Pair | Winner | Rationale |
| --- | --- | --- |
{rows}
"""


def _render_selected(winner: dict[str, Any], comparisons: list[dict[str, str]]) -> str:
    scores = "\n".join(f"- {key}: {value}" for key, value in winner["scores"].items())
    supporting = "\n".join(f"- {item['rationale']}" for item in comparisons if item["winner"] == winner["id"]) or "- Selected by total rubric score."
    return f"""# Selected Visual Direction

Winner: `{winner['id']}` - {winner['name']}

## Scores

{scores}

## Pairwise Evidence

{supporting}

## Developer Handoff

Use this direction as the visual source of truth before implementation. Preserve the chosen axis, layout density, typography intent, proof workflow, image lifecycle, and responsive behavior.
"""


def _excerpt(text: str, max_chars: int) -> str:
    normalized = "\n".join(line.rstrip() for line in text.splitlines()).strip()
    if len(normalized) <= max_chars:
        return normalized
    return normalized[:max_chars].rstrip() + "\n..."


def _emit(progress: Any | None, message: str) -> None:
    if progress:
        progress(message)
