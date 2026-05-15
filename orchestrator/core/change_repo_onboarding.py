"""RC-4A.1: deterministic project scanner for change-mode "repo onboarding."

Before a change request can be planned, the runtime needs a stable
snapshot of the existing project: what stack it is, what build/test
commands it has, what top-level layout, what endpoints, recent commits.
This information is consumed both by the operator (reviewing the
contract before run) and by the patch worker prompt (giving Codex the
context it needs to modify rather than recreate).

Design constraints:
- Pure scan, no LLM, no network.
- Output is markdown so it's diffable + human-reviewable.
- Output is DETERMINISTIC over consecutive scans of the same tree (key
  ordering is fixed; lists are sorted; git commands are bounded).
- Failure tolerant: missing `package.json`, no git history, no README —
  each just emits a "not detected" marker, never raises.

The scanner is intentionally narrow this round (RC-4A.1). Future work
(RC-4A.2/3) may extend the section list once real change runs reveal
what's actually load-bearing for the patch worker.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path
from typing import Any


_README_PREFIX_CHARS = 500
_GIT_LOG_LIMIT = 5
_TOP_LEVEL_DIRS_OF_INTEREST = (
    "app",
    "pages",
    "src",
    "components",
    "backend",
    "prisma",
    "scripts",
    "tests",
    "test",
    "public",
    "lib",
    "api",
)


def scan_repo(project_path: Path | str) -> dict[str, Any]:
    """Return a structured dict describing the project. The render function
    consumes this; tests can also assert against it.

    Output keys (always present, may be None / [] when not detected):
        stack: dict of detected toolchain markers
        package_scripts: dict[str, str] of npm scripts (or {})
        top_level_dirs: sorted list of recognized layout dirs that exist
        backend_indicators: dict of python-backend markers
        endpoints: list[str] of detected FastAPI/Flask/Express endpoints
        git_log: list[str] of last N commit subjects (oldest-first)
        readme_excerpt: str | None — first ~500 chars of README.md (or .rst)
        build_commands: dict[str, str | None] for build/test/typecheck if detected
    """
    project_path = Path(project_path)
    return {
        "project_path": str(project_path),
        "stack": _detect_stack(project_path),
        "package_scripts": _read_package_scripts(project_path),
        "top_level_dirs": _list_known_top_level(project_path),
        "backend_indicators": _detect_backend(project_path),
        "endpoints": _grep_endpoints(project_path),
        "git_log": _git_log_subjects(project_path, limit=_GIT_LOG_LIMIT),
        "readme_excerpt": _read_readme_prefix(project_path, _README_PREFIX_CHARS),
        "build_commands": _summarize_build_commands(project_path),
    }


def render_repo_onboarding(scan: dict[str, Any]) -> str:
    """Render the scan dict to a stable, diff-friendly markdown string."""
    lines: list[str] = []
    lines.append("# Repo Onboarding")
    lines.append("")
    lines.append("Deterministic snapshot of the existing project, produced by `change_repo_onboarding.scan_repo`. Re-run on the same tree → identical output.")
    lines.append("")

    lines.append("## Project path")
    lines.append("")
    lines.append(f"`{scan['project_path']}`")
    lines.append("")

    lines.append("## Detected stack")
    lines.append("")
    stack = scan.get("stack") or {}
    if stack:
        for key in sorted(stack.keys()):
            value = stack[key]
            display = "yes" if value is True else ("no" if value is False else str(value))
            lines.append(f"- **{key}**: {display}")
    else:
        lines.append("- (no markers detected)")
    lines.append("")

    lines.append("## Top-level directories")
    lines.append("")
    dirs = scan.get("top_level_dirs") or []
    if dirs:
        for d in dirs:
            lines.append(f"- `{d}/`")
    else:
        lines.append("- (none of the known layout directories detected)")
    lines.append("")

    lines.append("## package.json scripts")
    lines.append("")
    scripts = scan.get("package_scripts") or {}
    if scripts:
        for name in sorted(scripts.keys()):
            lines.append(f"- `{name}`: `{scripts[name]}`")
    else:
        lines.append("- (no package.json scripts detected)")
    lines.append("")

    lines.append("## Build / test / typecheck commands (detected)")
    lines.append("")
    cmds = scan.get("build_commands") or {}
    if cmds:
        for key in sorted(cmds.keys()):
            value = cmds[key]
            lines.append(f"- **{key}**: {('`' + value + '`') if value else '(not detected)'}")
    else:
        lines.append("- (none detected)")
    lines.append("")

    lines.append("## Backend indicators")
    lines.append("")
    backend = scan.get("backend_indicators") or {}
    if backend:
        for key in sorted(backend.keys()):
            value = backend[key]
            display = "yes" if value is True else ("no" if value is False else str(value))
            lines.append(f"- **{key}**: {display}")
    else:
        lines.append("- (no backend indicators detected)")
    lines.append("")

    lines.append("## Detected endpoints")
    lines.append("")
    endpoints = scan.get("endpoints") or []
    if endpoints:
        for ep in endpoints:
            lines.append(f"- `{ep}`")
    else:
        lines.append("- (none detected by lexical grep)")
    lines.append("")

    lines.append(f"## Recent git history (last {_GIT_LOG_LIMIT})")
    lines.append("")
    git = scan.get("git_log") or []
    if git:
        for subject in git:
            lines.append(f"- {subject}")
    else:
        lines.append("- (no git history available)")
    lines.append("")

    lines.append("## README excerpt")
    lines.append("")
    excerpt = scan.get("readme_excerpt")
    if excerpt:
        lines.append("```")
        lines.append(excerpt.rstrip())
        lines.append("```")
    else:
        lines.append("- (no README detected)")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Detection helpers
# ---------------------------------------------------------------------------
def _detect_stack(project_path: Path) -> dict[str, Any]:
    """Detect toolchain markers via file presence."""
    pkg_json = _read_json_safe(project_path / "package.json") or {}
    deps = {**(pkg_json.get("dependencies") or {}), **(pkg_json.get("devDependencies") or {})}
    return {
        "package_json": (project_path / "package.json").exists(),
        "next_js": "next" in deps,
        "react": "react" in deps,
        "tailwind": "tailwindcss" in deps,
        "typescript": "typescript" in deps or (project_path / "tsconfig.json").exists(),
        "prisma": "prisma" in deps or (project_path / "prisma").exists(),
        "vercel_json": (project_path / "vercel.json").exists(),
        "python_backend_dir": (project_path / "backend").exists(),
        "pyproject_toml": (project_path / "pyproject.toml").exists(),
        "requirements_txt_root": (project_path / "requirements.txt").exists(),
    }


def _read_package_scripts(project_path: Path) -> dict[str, str]:
    pkg = _read_json_safe(project_path / "package.json") or {}
    scripts = pkg.get("scripts") or {}
    return {str(k): str(v) for k, v in scripts.items()}


def _list_known_top_level(project_path: Path) -> list[str]:
    if not project_path.is_dir():
        return []
    found: list[str] = []
    for name in _TOP_LEVEL_DIRS_OF_INTEREST:
        if (project_path / name).is_dir():
            found.append(name)
    return sorted(found)


def _detect_backend(project_path: Path) -> dict[str, Any]:
    backend = project_path / "backend"
    return {
        "backend_dir": backend.exists(),
        "backend_requirements_txt": (backend / "requirements.txt").exists(),
        "backend_app_main": (backend / "app" / "main.py").exists(),
        "backend_tests": (backend / "tests").exists(),
        "backend_pytest_ini": (backend / "pytest.ini").exists(),
        "backend_venv": (backend / ".venv").exists(),
    }


_ENDPOINT_PATTERNS = (
    re.compile(r'@app\.(get|post|put|delete|patch)\("(?P<route>[^"]+)"'),
    re.compile(r"@app\.(get|post|put|delete|patch)\('(?P<route>[^']+)'"),
    re.compile(r'app\.(get|post|put|delete|patch)\("(?P<route>[^"]+)"'),
)


def _grep_endpoints(project_path: Path) -> list[str]:
    """Scan a small set of likely backend files for HTTP routes.

    Bounded to keep scan time predictable; recurses backend/app/ only.
    """
    backend_app = project_path / "backend" / "app"
    if not backend_app.is_dir():
        return []
    routes: list[str] = []
    for py_file in sorted(backend_app.rglob("*.py")):
        try:
            text = py_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in text.splitlines():
            for pat in _ENDPOINT_PATTERNS:
                m = pat.search(line)
                if m:
                    method = m.group(1).upper()
                    route = m.group("route")
                    routes.append(f"{method} {route}")
                    break
    # Sort + dedupe for determinism.
    return sorted(set(routes))


def _git_log_subjects(project_path: Path, *, limit: int) -> list[str]:
    """Return the last N commit subjects, oldest-first. Empty if not a git repo."""
    if not (project_path / ".git").exists():
        return []
    try:
        completed = subprocess.run(
            ["git", "-C", str(project_path), "log", f"-{limit}", "--pretty=%s"],
            capture_output=True, text=True, check=False, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    if completed.returncode != 0:
        return []
    subjects = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    return list(reversed(subjects))


def _read_readme_prefix(project_path: Path, max_chars: int) -> str | None:
    for candidate in ("README.md", "README.rst", "README.txt", "readme.md"):
        path = project_path / candidate
        if path.exists():
            try:
                return path.read_text(encoding="utf-8", errors="replace")[:max_chars]
            except OSError:
                continue
    return None


def _summarize_build_commands(project_path: Path) -> dict[str, str | None]:
    scripts = _read_package_scripts(project_path)
    return {
        "build": scripts.get("build"),
        "test": scripts.get("test"),
        "typecheck": scripts.get("typecheck"),
    }


def _read_json_safe(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
