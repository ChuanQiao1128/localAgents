"""RC-4A.1: deterministic parser for `change-request.md`.

A change request is a short markdown file that describes ONE intended
modification to an existing project. It is the change-mode counterpart
to a project's `requirements.md`: where requirements.md describes what
to build from scratch, change-request.md describes what to change about
something that already exists.

Recognized sections / lines (all case-insensitive):
- `## Goal` block OR (fallback) the first non-heading paragraph
- `Scope:` lines OR `## Scope` bullet list  → list[str] of path globs
- `Non-goals:` lines OR `## Non-goals` bullet list  → list[str]
- `## Acceptance` bullet list OR `Acceptance:` lines → list[str]

Validation:
- `goal` is REQUIRED (non-empty after strip)
- `acceptance` is REQUIRED (at least one bullet/line)
- `scope_paths` is OPTIONAL; if missing, `scope_missing` is set True so
  the operator (or RC-4A.2 wiring) can decide whether to suggest one
  from repo onboarding output

Why deterministic, not LLM-powered:
The contract artifact must be reproducible across runs and must be
reviewable BEFORE any Codex call. A regex-based parser plus explicit
section markers gives the operator full control without an extra round
trip.

Example minimal change-request.md::

    # Add side-by-side diff view

    ## Goal
    Add a side-by-side diff between original and rewritten text on the
    home page so writers can compare before/after at a glance.

    ## Scope
    - app/page.tsx
    - components/**

    ## Non-goals
    - Do not change the rewrite API surface.
    - Do not add new dependencies.

    ## Acceptance
    - Original text appears on the left, rewritten text on the right.
    - Layout stacks vertically below the `md` Tailwind breakpoint.
    - `npm run build` passes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ChangeRequest:
    """Parsed shape of a change-request.md file.

    `scope_missing` is the explicit signal callers should check before
    handing the contract to a patch worker — a contract with no scope
    declaration is allowed to be parsed but should not silently turn
    into "any file is fair game."
    """
    goal: str
    scope_paths: list[str] = field(default_factory=list)
    non_goals: list[str] = field(default_factory=list)
    acceptance: list[str] = field(default_factory=list)
    scope_missing: bool = False
    raw_text: str = ""


class ChangeRequestParseError(ValueError):
    """Raised when the parser cannot produce a valid ChangeRequest."""


_HEADING_RE = re.compile(r"^(#+)\s+(.*?)\s*$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+(.+?)\s*$")
_INLINE_LIST_RE = re.compile(r"^(scope|non[- ]goals?|acceptance)\s*:\s*(.+?)\s*$", re.IGNORECASE)


def parse_change_request_text(text: str) -> ChangeRequest:
    """Parse change-request.md content. See module docstring for the contract.

    Raises ChangeRequestParseError on missing goal or missing acceptance.
    """
    if not text or not text.strip():
        raise ChangeRequestParseError("change-request.md is empty")

    sections = _split_into_sections(text)
    goal = _extract_goal(text, sections)
    if not goal:
        raise ChangeRequestParseError(
            "change-request.md has no goal. Add a `## Goal` section, or put a paragraph as the first non-heading content."
        )

    scope_paths = _extract_list(sections, "scope") + _extract_inline_list(text, "scope")
    non_goals = _extract_list(sections, "non-goals") + _extract_list(sections, "non goals") + _extract_inline_list(text, "non-goals") + _extract_inline_list(text, "non goals")
    acceptance = _extract_list(sections, "acceptance") + _extract_inline_list(text, "acceptance")

    # Dedupe while preserving order — markdown layouts often repeat.
    scope_paths = _ordered_unique(scope_paths)
    non_goals = _ordered_unique(non_goals)
    acceptance = _ordered_unique(acceptance)

    if not acceptance:
        raise ChangeRequestParseError(
            "change-request.md has no acceptance criteria. Add a `## Acceptance` section with bullet items, or one or more `Acceptance:` lines."
        )

    return ChangeRequest(
        goal=goal,
        scope_paths=scope_paths,
        non_goals=non_goals,
        acceptance=acceptance,
        scope_missing=not scope_paths,
        raw_text=text,
    )


def parse_change_request_file(path: Path | str) -> ChangeRequest:
    """Convenience wrapper: read the file then call parse_change_request_text."""
    p = Path(path)
    if not p.exists():
        raise ChangeRequestParseError(f"change-request file does not exist: {p}")
    return parse_change_request_text(p.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _split_into_sections(text: str) -> dict[str, list[str]]:
    """Split markdown into sections keyed by lowercased heading text.

    Only level-2 (`##`) headings are tracked. The body of each section
    is the list of subsequent non-heading lines until the next level-2+
    heading. Returns ALL sections (caller picks the ones it cares about).
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    buffer: list[str] = []

    def flush() -> None:
        if current is not None:
            sections[current] = buffer.copy()

    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match and len(match.group(1)) == 2:
            flush()
            current = match.group(2).strip().lower()
            buffer = []
        else:
            if current is not None:
                buffer.append(line)
    flush()
    return sections


def _extract_goal(text: str, sections: dict[str, list[str]]) -> str:
    """Goal resolution: explicit `## Goal` section, else first non-heading paragraph
    BEFORE any `##`/`###`/etc section heading begins. We must not let the fallback
    leak into `## Acceptance` (or any other named section) because that would
    silently turn a missing-goal document into one with a nonsense goal."""
    if "goal" in sections:
        body = "\n".join(sections["goal"]).strip()
        # Strip wrapping bullets if the section is just a single bullet.
        bullet = _BULLET_RE.match(body)
        if bullet and "\n" not in body:
            return bullet.group(1).strip()
        return body
    # Fallback: scan only the prose ABOVE the first `##` (or deeper) heading.
    # Level-1 (`#`) headings are titles and are skipped, but they don't end
    # the prose region. Anything starting from the first `##`+ is treated as
    # belonging to a named section and is OFF-LIMITS for the fallback.
    paragraph: list[str] = []
    for line in text.splitlines():
        match = _HEADING_RE.match(line)
        if match and len(match.group(1)) >= 2:
            # Hit the first `##` or deeper — stop scanning entirely.
            break
        if match:
            # `# Title` — skip but don't terminate the search yet.
            if paragraph:
                break
            continue
        if not line.strip():
            if paragraph:
                break
            continue
        paragraph.append(line.strip())
    return " ".join(paragraph).strip()


def _extract_list(sections: dict[str, list[str]], key: str) -> list[str]:
    """Pull bullet items from the named section, if present."""
    if key not in sections:
        return []
    items: list[str] = []
    for line in sections[key]:
        match = _BULLET_RE.match(line)
        if match:
            items.append(match.group(1).strip())
        elif line.strip():
            # Tolerate non-bullet lines as inline items if no bullets are present.
            stripped = line.strip()
            if not _HEADING_RE.match(stripped):
                items.append(stripped)
    return items


def _extract_inline_list(text: str, key: str) -> list[str]:
    """Pull `Scope: a, b, c` style inline lists."""
    items: list[str] = []
    pattern = re.compile(rf"^{re.escape(key)}\s*:\s*(.+?)\s*$", re.IGNORECASE)
    for line in text.splitlines():
        match = pattern.match(line.strip())
        if match:
            for piece in match.group(1).split(","):
                stripped = piece.strip()
                if stripped:
                    items.append(stripped)
    return items


def _ordered_unique(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item not in seen:
            seen.add(item)
            result.append(item)
    return result
