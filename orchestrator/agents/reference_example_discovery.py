from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit, urlunsplit
import urllib.error
import urllib.request

from orchestrator.agents.reference_visual_research import (
    _capture_chrome,
    _clean_html,
    _fetch_metadata,
    _safe_slug,
)


@dataclass(frozen=True)
class ReferenceExampleDiscoveryResult:
    report_path: Path
    candidates_path: Path
    examples_json_path: Path
    visual_critic_path: Path
    visual_critic_json_path: Path
    screenshots_dir: Path
    seeds_scanned: int
    candidates_found: int
    selected_examples: int
    captures_attempted: int
    captures_captured: int


@dataclass(frozen=True)
class _RawLink:
    url: str
    text: str
    title: str


class ReferenceExampleDiscoveryAgent:
    def run(
        self,
        *,
        project: dict[str, Any],
        limit: int = 10,
        per_seed: int = 6,
        max_per_source: int = 3,
        capture: bool = True,
        include_mobile: bool = True,
        progress: Callable[[str], None] | None = None,
    ) -> ReferenceExampleDiscoveryResult:
        project_path = Path(project["path"])
        product_dir = project_path / "docs/product"
        reference_path = product_dir / "reference-products/reference-products.json"
        output_dir = product_dir / "example-references"
        cache_dir = product_dir / "reference-cache"
        screenshot_dir = output_dir / "screenshots"
        output_dir.mkdir(parents=True, exist_ok=True)
        cache_dir.mkdir(parents=True, exist_ok=True)
        screenshot_dir.mkdir(parents=True, exist_ok=True)

        products = _load_products(reference_path)
        seeds = _select_seeds(products)
        all_candidates: list[dict[str, Any]] = []
        selected_by_seed: dict[str, list[dict[str, Any]]] = {}

        for seed in seeds:
            seed_id = str(seed.get("source_id") or seed.get("name") or "reference")
            urls = _candidate_urls(seed)
            slug = _safe_slug(seed_id)
            if progress:
                progress(f"[{seed_id}] scanning {len(urls)} entry URL(s)")
            seed_pages = _load_or_fetch_seed_pages(urls, cache_dir / slug)
            if not seed_pages:
                selected_by_seed[seed_id] = []
                continue
            candidates: list[dict[str, Any]] = []
            for seed_page in seed_pages:
                links = _extract_links(seed_page["url"], seed_page["html"])
                candidates.extend(_rank_links(seed, seed_page["url"], links))
            candidates = _dedupe_candidates(candidates)
            selected = candidates[: max(1, per_seed)]
            selected_by_seed[seed_id] = selected
            all_candidates.extend(selected)
            if progress:
                progress(f"[{seed_id}] candidate examples: {len(candidates)}")

        ranked = _dedupe_candidates(all_candidates)
        examples = _select_diverse_examples(ranked, limit=max(1, limit), max_per_source=max(1, max_per_source))
        captures_attempted = 0
        captures_captured = 0
        viewports = [("desktop", "1440,1000")]
        if include_mobile:
            viewports.append(("mobile", "390,844"))

        for index, example in enumerate(examples, start=1):
            slug = _safe_slug(f"{index:02d}-{example['source_id']}-{example['title'] or example['url']}")
            example["screenshots"] = []
            if capture:
                for viewport, window_size in viewports:
                    captures_attempted += 1
                    if progress:
                        progress(f"[example {index}/{len(examples)}] capturing {viewport}: {example['title']}")
                    output = screenshot_dir / f"{slug}-{viewport}.png"
                    captured = _capture_chrome(example["url"], output, window_size=window_size)
                    if captured:
                        captures_captured += 1
                        example["screenshots"].append(
                            {
                                "viewport": viewport,
                                "path": str(output.relative_to(project_path)),
                                "status": "captured",
                                "evidence_level": "screenshot",
                            }
                        )
                    else:
                        example["screenshots"].append(
                            {
                                "viewport": viewport,
                                "path": None,
                                "status": "not_captured",
                                "evidence_level": "link_candidate",
                            }
                        )
            metadata = _fetch_metadata(example["url"])
            if metadata:
                example["metadata"] = metadata
                if _is_generic_anchor(str(example.get("title") or "").lower()) and metadata.get("title"):
                    example["title"] = metadata["title"]
                if not any(item["status"] == "captured" for item in example["screenshots"]):
                    example["evidence_level"] = "page_metadata"
            if any(item["status"] == "captured" for item in example["screenshots"]):
                example["evidence_level"] = "screenshot"

        candidates_path = output_dir / "example-candidates.json"
        examples_json_path = output_dir / "top-examples.json"
        report_path = output_dir / "top-examples.md"
        candidates_path.write_text(json.dumps(ranked, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        examples_json_path.write_text(json.dumps(examples, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        report_path.write_text(_render_report(seeds, selected_by_seed, examples), encoding="utf-8")
        _attach_examples_to_products(reference_path, products, selected_by_seed)
        from orchestrator.agents.example_visual_critic import ExampleVisualCriticAgent

        critic = ExampleVisualCriticAgent().run(project=project)

        return ReferenceExampleDiscoveryResult(
            report_path=report_path,
            candidates_path=candidates_path,
            examples_json_path=examples_json_path,
            visual_critic_path=critic.report_path,
            visual_critic_json_path=critic.json_path,
            screenshots_dir=screenshot_dir,
            seeds_scanned=len(seeds),
            candidates_found=len(ranked),
            selected_examples=len(examples),
            captures_attempted=captures_attempted,
            captures_captured=captures_captured,
        )


def _load_products(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    return [item for item in payload if isinstance(item, dict)]


def _select_seeds(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    preferred = [
        product
        for product in products
        if product.get("critic_verdict") in {"strong_reference", "usable_reference", "seed_profile"}
        and (product.get("url") or product.get("alternate_urls"))
    ]
    fallback = preferred or [product for product in products if product.get("url") or product.get("alternate_urls")]
    return sorted(fallback, key=lambda product: int(product.get("total_score") or 0), reverse=True)


def _candidate_urls(product: dict[str, Any]) -> list[str]:
    urls = [str(product.get("url") or "")]
    urls.extend(str(url) for url in product.get("alternate_urls") or [])
    unique: list[str] = []
    for url in urls:
        normalized = _normalize_url(url)
        if normalized and normalized not in unique:
            unique.append(normalized)
    return unique


def _load_or_fetch_seed_pages(urls: list[str], cache_dir: Path) -> list[dict[str, str]]:
    pages: list[dict[str, str]] = []
    for index, url in enumerate(urls):
        page_cache_dir = cache_dir if index == 0 else cache_dir.parent / f"{cache_dir.name}-alt-{index}"
        page = _load_or_fetch_seed_page([url], page_cache_dir)
        if page:
            pages.append(page)
    return pages


def _load_or_fetch_seed_page(urls: list[str], cache_dir: Path) -> dict[str, str] | None:
    html_path = cache_dir / "page.html"
    metadata_path = cache_dir / "page-source.json"
    if html_path.exists():
        try:
            html = html_path.read_text(encoding="utf-8")
        except OSError:
            html = ""
        if html:
            url = _cached_page_url(metadata_path) or (urls[0] if urls else "")
            return {"url": url, "html": html, "source": "cache"}
    for url in urls:
        html = _fetch_html(url)
        if _link_count(html) < 4:
            dom = _dump_dom_chrome(url)
            if _link_count(dom) > _link_count(html):
                html = dom
        if html and _link_count(html) > 0:
            cache_dir.mkdir(parents=True, exist_ok=True)
            html_path.write_text(html, encoding="utf-8")
            metadata_path.write_text(json.dumps({"url": url}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            return {"url": url, "html": html, "source": "network"}
    return None


def _cached_page_url(path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if isinstance(payload, dict) and isinstance(payload.get("url"), str):
        return payload["url"]
    return None


def _fetch_html(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        return ""
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=16) as response:
            return response.read(1_500_000).decode("utf-8", errors="replace")
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, OSError):
        return ""


def _dump_dom_chrome(url: str) -> str:
    chrome = _find_chrome()
    if not chrome or not url.startswith(("http://", "https://")):
        return ""
    with tempfile.TemporaryDirectory(prefix="agent-studio-example-dom-") as profile_dir:
        command = [
            chrome,
            "--headless",
            "--disable-gpu",
            "--disable-background-networking",
            "--no-first-run",
            "--no-default-browser-check",
            "--hide-scrollbars",
            "--run-all-compositor-stages-before-draw",
            "--virtual-time-budget=3500",
            f"--user-data-dir={profile_dir}",
            "--dump-dom",
            url,
        ]
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        deadline = time.monotonic() + 22
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if process.poll() is None:
            process.terminate()
        try:
            stdout, _stderr = process.communicate(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            stdout, _stderr = process.communicate(timeout=3)
    return stdout or ""


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


def _link_count(html: str) -> int:
    return len(re.findall(r"<a\b", html or "", flags=re.IGNORECASE))


class _AnchorParser(HTMLParser):
    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.links: list[_RawLink] = []
        self._active_href = ""
        self._active_title = ""
        self._active_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        attrs_dict = {key.lower(): value or "" for key, value in attrs}
        href = attrs_dict.get("href", "")
        if not href:
            return
        self._active_href = urljoin(self.base_url, href)
        self._active_title = attrs_dict.get("title", "") or attrs_dict.get("aria-label", "")
        self._active_text = []

    def handle_data(self, data: str) -> None:
        if self._active_href:
            self._active_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._active_href:
            return
        text = _clean_text(" ".join(self._active_text))
        title = _clean_text(self._active_title)
        self.links.append(_RawLink(url=self._active_href, text=text, title=title))
        self._active_href = ""
        self._active_title = ""
        self._active_text = []


def _extract_links(base_url: str, html: str) -> list[_RawLink]:
    parser = _AnchorParser(base_url)
    try:
        parser.feed(html or "")
        parser.close()
    except Exception:
        return []
    seen: set[str] = set()
    links: list[_RawLink] = []
    for link in parser.links:
        normalized = _normalize_url(link.url)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        links.append(_RawLink(url=normalized, text=link.text, title=link.title))
    return links


def _rank_links(seed: dict[str, Any], source_url: str, links: list[_RawLink]) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    seed_id = str(seed.get("source_id") or seed.get("name") or "reference")
    seed_name = str(seed.get("name") or seed_id)
    seed_url = _normalize_url(str(seed.get("url") or ""))
    for link in links:
        score, reasons = _score_example_candidate(seed, link, source_url, seed_url)
        if score < 18:
            continue
        label = _clean_text(link.title or link.text)
        title = _url_label(link.url) if _is_generic_anchor(label.lower()) else label or _url_label(link.url)
        ranked.append(
            {
                "source_id": seed_id,
                "source_name": seed_name,
                "source_url": source_url,
                "url": link.url,
                "title": _clean_html(title)[:180],
                "anchor_text": _clean_html(link.text)[:260],
                "score": score,
                "reasons": reasons,
                "evidence_level": "link_candidate",
            }
        )
    return sorted(ranked, key=lambda item: int(item["score"]), reverse=True)


def _score_example_candidate(seed: dict[str, Any], link: _RawLink, source_url: str, seed_url: str) -> tuple[int, list[str]]:
    url = link.url.lower()
    text = f"{link.text} {link.title} {link.url}".lower()
    source_id = str(seed.get("source_id") or "").lower()
    parsed = urlsplit(link.url)
    source_host = urlsplit(source_url).netloc.lower()
    path = parsed.path.lower()
    label = _clean_text(link.text or link.title).lower()
    external_showcase = "semplice" in source_id and parsed.netloc.lower() not in {"", source_host, "help.semplice.com"}
    score = 0
    reasons: list[str] = []

    if link.url == seed_url or link.url == _normalize_url(source_url):
        score -= 35
    if _has_any(text, ["portfolio", "personal site", "personal website"]):
        score += 18
        reasons.append("portfolio-specific")
    if _has_any(text, ["template", "templates", "cloneable", "remix", "preview"]):
        score += 16
        reasons.append("template or preview candidate")
    if _has_any(text, ["showcase", "examples", "gallery", "case-study", "case study", "case-studies"]):
        score += 14
        reasons.append("showcase or case-study candidate")
    if _has_any(text, ["designer", "developer", "creative", "studio", "freelance", "creator", "ux", "product designer"]):
        score += 10
        reasons.append("relevant creator role")
    if _has_any(text, ["project", "work", "proof", "outcome", "story", "profile"]):
        score += 8
        reasons.append("project proof signal")
    if "framer" in source_id and _is_specific_framer_template(path):
        score += 28
        reasons.append("specific Framer template page")
    elif "framer" in source_id and "/templates/" in url:
        score += 8
        reasons.append("Framer template path")
    if "webflow" in source_id and _is_specific_webflow_example(path):
        score += 28
        reasons.append("specific Webflow template/showcase page")
    elif "webflow" in source_id and ("/templates/" in url or "/made-in-webflow/" in url):
        score += 8
        reasons.append("Webflow template/showcase path")
    if external_showcase:
        score += 34
        reasons.append("Semplice external showcase site")
    elif "semplice" in source_id and "showcase" in url and "archives" not in url:
        score += 14
        reasons.append("Semplice showcase path")
    if "readymag" in source_id and "examples" in url and not path.rstrip("/").endswith("/examples"):
        score += 14
        reasons.append("Readymag examples path")
    if "behance" in source_id and ("/gallery/" in url or "search/projects" in url):
        score += 6
        reasons.append("Behance gallery path")
    if "awwwards" in source_id and "/sites/" in url:
        score += 30
        reasons.append("specific Awwwards site page")
    if _is_category_or_listing(path, parsed.query, label):
        score -= 34
        reasons.append("category/listing page penalty")
    if _is_generic_anchor(label) and not external_showcase:
        score -= 16
        reasons.append("generic anchor penalty")
    if _is_social_or_share_url(parsed.netloc, path):
        score -= 40
        reasons.append("social/share link penalty")
    if "contra" in source_id and ("/community/" in path or "/features/" in path):
        score -= 26
        reasons.append("Contra community/features penalty")
    if _has_any(text, ["how to", "beginner guide", "checklist", "guide", "article", "blog"]):
        score -= 12
        reasons.append("article/guide penalty")
    if _has_any(text, ["pricing", "login", "sign in", "signup", "sign up", "contact", "privacy", "terms", "cookie"]):
        score -= 24
        reasons.append("navigation or legal link penalty")
    if _has_any(text, ["enterprise", "affiliate", "careers", "docs", "support", "status", "download"]):
        score -= 16
        reasons.append("non-example link penalty")
    if _looks_like_asset(url):
        score -= 40
        reasons.append("asset/file link penalty")
    return score, reasons


def _has_any(text: str, terms: list[str]) -> bool:
    return any(term in text for term in terms)


def _looks_like_asset(url: str) -> bool:
    return bool(re.search(r"\.(png|jpg|jpeg|gif|webp|svg|pdf|zip|mp4|mov|css|js)($|\?)", url))


def _is_specific_framer_template(path: str) -> bool:
    return bool(re.match(r"^/(marketplace/)?templates/[^/]+$", path)) and "/category" not in path


def _is_specific_webflow_example(path: str) -> bool:
    return bool(re.match(r"^/templates/html/[^/]+$", path)) or bool(re.match(r"^/made-in-webflow/website/[^/]+$", path))


def _is_category_or_listing(path: str, query: str, label: str) -> bool:
    normalized = path.rstrip("/")
    return (
        "/category" in normalized
        or normalized in {"/templates", "/marketplace/templates", "/showcase", "/examples", "/portfolio", "/websites"}
        or "page=" in query
        or label in {"load more", "categories", "portfolio", "creative", "portfolios", "websites", "store"}
    )


def _is_generic_anchor(label: str) -> bool:
    return label in {
        "",
        "title",
        "de",
        "fr",
        "more",
        "learn more",
        "view",
        "view details",
        "visit",
        "visit site",
        "open",
        "back to webflow templates",
    }


def _is_social_or_share_url(host: str, path: str) -> bool:
    return any(domain in host for domain in ["facebook.com", "linkedin.com", "twitter.com", "x.com"]) or "sharer" in path


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    best_by_url: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        url = _normalize_url(str(candidate.get("url") or ""))
        if not url:
            continue
        candidate["url"] = url
        existing = best_by_url.get(url)
        if not existing or int(candidate.get("score") or 0) > int(existing.get("score") or 0):
            best_by_url[url] = candidate
    return sorted(best_by_url.values(), key=lambda item: int(item.get("score") or 0), reverse=True)


def _select_diverse_examples(candidates: list[dict[str, Any]], *, limit: int, max_per_source: int) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_urls: set[str] = set()
    source_counts: dict[str, int] = {}
    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in candidates:
        source_id = str(candidate.get("source_id") or "")
        if source_id:
            groups.setdefault(source_id, []).append(candidate)
    ordered_sources = sorted(
        groups,
        key=lambda source_id: int(groups[source_id][0].get("score") or 0),
        reverse=True,
    )
    while len(selected) < limit:
        added_in_round = False
        for source_id in ordered_sources:
            if source_counts.get(source_id, 0) >= max_per_source:
                continue
            for candidate in groups[source_id]:
                url = str(candidate.get("url") or "")
                if not url or url in selected_urls:
                    continue
                selected.append(candidate)
                selected_urls.add(url)
                source_counts[source_id] = source_counts.get(source_id, 0) + 1
                added_in_round = True
                break
            if len(selected) >= limit:
                return selected
        if not added_in_round:
            break
    return selected


def _normalize_url(url: str) -> str:
    if not url:
        return ""
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    path = parsed.path or "/"
    query = parsed.query
    if query:
        kept = []
        for part in query.split("&"):
            if not part:
                continue
            key = part.split("=", 1)[0].lower()
            if key.startswith("utm_") or key in {"fbclid", "gclid", "ref", "tracking_source", "l", "mini", "summary", "source"}:
                continue
            kept.append(part)
        query = "&".join(kept)
    normalized = urlunsplit((parsed.scheme, parsed.netloc.lower(), path.rstrip("/") or "/", query, ""))
    return normalized


def _clean_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _url_label(url: str) -> str:
    parsed = urlsplit(url)
    label = parsed.path.strip("/").split("/")[-1] or parsed.netloc
    if len(label) <= 2:
        label = parsed.netloc
    return label.replace("-", " ").replace("_", " ").strip()


def _attach_examples_to_products(
    reference_path: Path,
    products: list[dict[str, Any]],
    selected_by_seed: dict[str, list[dict[str, Any]]],
) -> None:
    if not products or not reference_path.exists():
        return
    for product in products:
        source_id = str(product.get("source_id") or product.get("name") or "")
        if source_id in selected_by_seed:
            product["discovered_examples"] = selected_by_seed[source_id]
    reference_path.write_text(json.dumps(products, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _render_report(
    seeds: list[dict[str, Any]],
    selected_by_seed: dict[str, list[dict[str, Any]]],
    examples: list[dict[str, Any]],
) -> str:
    seed_rows = "\n".join(
        f"- {seed.get('source_id')}: {seed.get('name')} ({len(selected_by_seed.get(str(seed.get('source_id') or seed.get('name') or ''), []))} candidates)"
        for seed in seeds
    ) or "- No reference seeds found."
    example_rows = "\n".join(
        _example_row(index, example)
        for index, example in enumerate(examples, start=1)
    ) or "| - | - | - | - | - | - |"
    patterns = _render_patterns(examples)
    return f"""# Specific Example References

## Purpose

This artifact turns broad reference platforms into concrete portfolio/template/example pages that can be inspected by UI, Visual Direction, and Product Review agents.

## Seeds Scanned

{seed_rows}

## Top Examples

| Rank | Source | Example | Score | Evidence | Screenshots |
| --- | --- | --- | --- | --- | --- |
{example_rows}

## Product Patterns To Borrow

{patterns}

## Use In Later Agents

- UI Team should convert these examples into screen-level decisions, not copy visual styling blindly.
- Visual Direction should compare generated variants against these examples for first viewport hierarchy, project proof treatment, template choice, and mobile behavior.
- PRD Team can treat screenshot examples as stronger evidence than broad seed profiles.
- Developer Team should still implement the selected product's own workflow, not clone any referenced page.
"""


def _example_row(index: int, example: dict[str, Any]) -> str:
    screenshots = [
        item.get("path") or item.get("status")
        for item in example.get("screenshots") or []
    ]
    screenshot_text = "<br>".join(str(item) for item in screenshots) or "-"
    evidence = example.get("evidence_level", "link_candidate")
    return (
        f"| {index} | {example.get('source_name')} | "
        f"[{_escape_table(str(example.get('title') or example.get('url')))}]({example.get('url')}) | "
        f"{example.get('score')} | {evidence} | {screenshot_text} |"
    )


def _render_patterns(examples: list[dict[str, Any]]) -> str:
    reason_counts: dict[str, int] = {}
    for example in examples:
        for reason in example.get("reasons") or []:
            reason_counts[str(reason)] = reason_counts.get(str(reason), 0) + 1
    if not reason_counts:
        return "- No strong patterns yet. Rerun with more seeds or sharper references."
    lines = []
    for reason, count in sorted(reason_counts.items(), key=lambda item: item[1], reverse=True):
        lines.append(f"- {reason}: seen in {count} selected examples.")
    lines.extend(
        [
            "- Require template choice to change layout and information hierarchy, not only color.",
            "- Require project proof fields: problem, role, process, outcome, metric/evidence, links, and screenshot.",
            "- Require mobile screenshot review for selected visual direction and exported HTML.",
        ]
    )
    return "\n".join(lines)


def _escape_table(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", " ")
