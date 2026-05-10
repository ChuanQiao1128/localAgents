from __future__ import annotations

from dataclasses import dataclass
import html
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any
import urllib.error
import urllib.request


@dataclass(frozen=True)
class ReferenceVisualResearchResult:
    report_path: Path
    manifest_path: Path
    screenshots_dir: Path
    attempted: int
    captured: int


class ReferenceVisualResearchAgent:
    def run(self, *, project: dict[str, Any], limit: int = 4) -> ReferenceVisualResearchResult:
        project_path = Path(project["path"])
        product_dir = project_path / "docs/product"
        reference_path = product_dir / "reference-products/reference-products.json"
        screenshot_dir = product_dir / "reference-screenshots"
        cache_dir = product_dir / "reference-cache"
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        products = _load_products(reference_path)
        targets = _select_targets(products, limit)
        manifest: list[dict[str, Any]] = []

        for product in targets:
            slug = _safe_slug(str(product.get("source_id") or product.get("name") or "reference"))
            candidates = _candidate_urls(product)
            metadata = _load_or_fetch_metadata(candidates, cache_dir / slug)
            for viewport_name, window_size in [("desktop", "1440,1000"), ("mobile", "390,844")]:
                output = screenshot_dir / f"{slug}-{viewport_name}.png"
                captured_url = _capture_first_available(candidates, output, window_size=window_size)
                captured = captured_url is not None
                evidence_level = "screenshot" if captured else "page_metadata" if metadata else str(product.get("evidence_level") or "search_snippet")
                status = "captured" if captured else "metadata_only" if metadata else "not_captured"
                manifest.append(
                    {
                        "source_id": product.get("source_id"),
                        "name": product.get("name"),
                        "url": captured_url or (metadata or {}).get("url") or product.get("url"),
                        "attempted_urls": candidates,
                        "viewport": viewport_name,
                        "path": str(output.relative_to(project_path)) if captured else None,
                        "metadata_path": str((cache_dir / slug / "metadata.json").relative_to(project_path)) if metadata else None,
                        "extract_path": str((cache_dir / slug / "extract.md").relative_to(project_path)) if metadata else None,
                        "status": status,
                        "evidence_level": evidence_level,
                        "critic_verdict": product.get("critic_verdict"),
                        "score": product.get("total_score"),
                    }
                )
            product["visual_evidence"] = [item for item in manifest if item.get("source_id") == product.get("source_id")]

        manifest_path = screenshot_dir / "manifest.json"
        report_path = screenshot_dir / "capture-report.md"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_path.write_text(_render_report(targets, manifest), encoding="utf-8")
        if reference_path.exists():
            reference_path.write_text(json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return ReferenceVisualResearchResult(
            report_path=report_path,
            manifest_path=manifest_path,
            screenshots_dir=screenshot_dir,
            attempted=len(manifest),
            captured=sum(1 for item in manifest if item["status"] == "captured"),
        )


def _load_products(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict) and (item.get("url") or item.get("alternate_urls"))]


def _select_targets(products: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    preferred = [
        product
        for product in products
        if product.get("critic_verdict") in {"strong_reference", "usable_reference", "seed_profile"}
    ]
    fallback = products if not preferred else preferred
    return sorted(fallback, key=lambda product: int(product.get("total_score") or 0), reverse=True)[: max(1, limit)]


def _candidate_urls(product: dict[str, Any]) -> list[str]:
    urls = [str(product.get("url") or "")]
    urls.extend(str(url) for url in product.get("alternate_urls") or [])
    unique: list[str] = []
    for url in urls:
        if url.startswith(("http://", "https://")) and url not in unique:
            unique.append(url)
    return unique


def _capture_first_available(urls: list[str], output_path: Path, *, window_size: str) -> str | None:
    for url in urls:
        if _capture_chrome(url, output_path, window_size=window_size):
            return url
    return None


def _capture_chrome(url: str, output_path: Path, *, window_size: str) -> bool:
    if not url.startswith(("http://", "https://")):
        return False
    chrome = _find_chrome()
    if not chrome:
        return False
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if output_path.exists():
        output_path.unlink()
    with tempfile.TemporaryDirectory(prefix="agent-studio-ref-") as profile_dir:
        command = [
            chrome,
            "--headless",
            "--disable-gpu",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=1800",
            f"--user-data-dir={profile_dir}",
            f"--window-size={window_size}",
            f"--screenshot={output_path}",
            url,
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 14
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
    return output_path.exists() and output_path.stat().st_size > 0


def _load_or_fetch_metadata(urls: list[str], cache_dir: Path) -> dict[str, str] | None:
    metadata_path = cache_dir / "metadata.json"
    if metadata_path.exists():
        try:
            loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            loaded = None
        if isinstance(loaded, dict) and loaded.get("url"):
            return {str(key): str(value) for key, value in loaded.items()}
    for url in urls:
        metadata = _fetch_metadata(url)
        if metadata:
            cache_dir.mkdir(parents=True, exist_ok=True)
            metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            (cache_dir / "extract.md").write_text(_render_extract(metadata), encoding="utf-8")
            return metadata
    return None


def _fetch_metadata(url: str) -> dict[str, str] | None:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=12) as response:
            body = response.read(400_000).decode("utf-8", errors="replace")
            final_url = response.geturl()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return None
    title = _first_match(body, r"<title[^>]*>(.*?)</title>")
    description = (
        _meta_content(body, "description")
        or _meta_property(body, "og:description")
        or _meta_property(body, "twitter:description")
    )
    if not title and not description:
        return None
    return {
        "url": final_url or url,
        "title": _clean_html(title),
        "description": _clean_html(description),
        "evidence_level": "page_metadata",
    }


def _first_match(text: str, pattern: str) -> str:
    match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


def _meta_content(text: str, name: str) -> str:
    pattern = rf"<meta[^>]+name=[\"']{re.escape(name)}[\"'][^>]+content=[\"']([^\"']*)[\"'][^>]*>"
    return _first_match(text, pattern)


def _meta_property(text: str, name: str) -> str:
    pattern = rf"<meta[^>]+property=[\"']{re.escape(name)}[\"'][^>]+content=[\"']([^\"']*)[\"'][^>]*>"
    return _first_match(text, pattern)


def _clean_html(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", html.unescape(value or "")).strip()
    return cleaned[:1200]


def _render_extract(metadata: dict[str, str]) -> str:
    return f"""# Reference Metadata Extract

- URL: {metadata.get("url", "")}
- Evidence level: page_metadata

## Title

{metadata.get("title", "") or "No title extracted."}

## Description

{metadata.get("description", "") or "No description extracted."}
"""


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


def _safe_slug(value: str) -> str:
    lowered = value.lower()
    return "".join(char if char.isalnum() else "-" for char in lowered).strip("-")[:64] or "reference"


def _render_report(targets: list[dict[str, Any]], manifest: list[dict[str, Any]]) -> str:
    rows = "\n".join(
        f"| {item.get('source_id')} | {item.get('viewport')} | {item.get('status')} | {item.get('evidence_level')} | {item.get('path') or item.get('extract_path') or ''} |"
        for item in manifest
    )
    target_rows = "\n".join(
        f"- [{product.get('source_id')}] {product.get('name')} ({product.get('critic_verdict')}, {product.get('total_score')}/110, {product.get('evidence_level', 'search_snippet')}): {product.get('url')}"
        for product in targets
    ) or "- No reference targets available."
    return f"""# Reference Screenshot Capture Report

## Targets

{target_rows}

## Captures

| Source | Viewport | Status | Evidence | Path |
| --- | --- | --- | --- | --- |
{rows}

## How To Use

- Feed captured desktop/mobile screenshots into Visual Critic.
- Use page metadata as weaker fallback evidence when screenshots are blocked.
- Treat seed profiles as targets until screenshot or metadata evidence upgrades them.
- Compare first viewport, information hierarchy, screenshot/card treatment, CTA order, and mobile text fit.
- If no strong references were captured, rerun `prd research` with sharper queries before trusting visual direction prompts.
"""
