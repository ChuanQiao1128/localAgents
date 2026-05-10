from __future__ import annotations

import json
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlopen

from orchestrator.tools.shell_tools import ShellTools

from .base import AgentResult
from .screenshot_image_analysis import analyze_screenshot


class QAAgent:
    id = "qa"

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.shell = ShellTools(project_path, timeout_seconds=120)

    def run_checks(self, commands: list[str] | None = None) -> AgentResult:
        if commands is None and (self.project_path / "apps/web/package.json").exists() and (self.project_path / "apps/web/app/page.tsx").exists():
            return self.run_next_web_checks()
        if commands is None and (self.project_path / "apps/web/index.html").exists():
            return self.run_static_web_checks()

        commands = commands or ["python3 -m unittest discover -s tests"]
        qa_dir = self.project_path / "docs/qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        (qa_dir / "test-plan.md").write_text(_test_plan(commands), encoding="utf-8")

        sections: list[str] = ["# Test Results", ""]
        failed = False
        for command in commands:
            result = self.shell.run(command)
            failed = failed or not result.ok
            sections.extend(
                [
                    f"## `{command}`",
                    "",
                    f"Return code: {result.returncode}",
                    "",
                    "### stdout",
                    "",
                    "```text",
                    result.stdout.strip(),
                    "```",
                    "",
                    "### stderr",
                    "",
                    "```text",
                    result.stderr.strip(),
                    "```",
                    "",
                ]
            )
        (qa_dir / "test-results.md").write_text("\n".join(sections), encoding="utf-8")
        return AgentResult(
            status="failed" if failed else "completed",
            summary="QA checks failed." if failed else "QA checks passed.",
            artifacts=["docs/qa/test-plan.md", "docs/qa/test-results.md"],
        )

    def run_static_web_checks(self) -> AgentResult:
        qa_dir = self.project_path / "docs/qa"
        qa_dir.mkdir(parents=True, exist_ok=True)

        task_path = self.project_path / ".agent/tasks/generated-tasks.json"
        task_text = task_path.read_text(encoding="utf-8").lower() if task_path.exists() else ""
        index_path = self.project_path / "apps/web/index.html"
        index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
        is_portfolio = "portfolio" in task_text or "Portfolio Builder MVP" in index_text

        if is_portfolio:
            files = {
                "index": index_path,
                "styles": self.project_path / "apps/web/styles.css",
                "script": self.project_path / "apps/web/app.js",
                "smoke": self.project_path / "tests/portfolio-builder-smoke.md",
            }
        else:
            files = {
                "index": index_path,
                "styles": self.project_path / "apps/web/styles.css",
                "readme": self.project_path / "apps/web/README.md",
            }
        contents = {key: path.read_text(encoding="utf-8") if path.is_file() else "" for key, path in files.items()}

        if is_portfolio:
            checks = [
                ("HTML shell exists", files["index"].exists()),
                ("CSS exists", files["styles"].exists()),
                ("Browser script exists", files["script"].exists()),
                ("Smoke-test checklist exists", files["smoke"].exists()),
                ("Profile editor is present", "portfolioForm" in contents["index"] and "avatarInput" in contents["index"]),
                ("Project editor template is present", "projectTemplate" in contents["index"] and "data-action=\"move-up\"" in contents["index"]),
                ("Live preview is present", "preview" in contents["index"] and "renderPreview" in contents["script"]),
                ("Image validation is present", "readImage" in contents["script"] and "Oversized file" in contents["script"]),
                ("Local persistence is present", "localStorage" in contents["script"]),
                ("Static HTML export handler is present", "exportStaticHtml" in contents["script"] and "EXPORT_STYLES" in contents["script"]),
                ("Generated HTML escapes user content", "escapeHtml" in contents["script"] and "escapeAttr" in contents["script"]),
                ("Responsive layout rules are present", "@media" in contents["styles"]),
            ]
        else:
            checks = [
                ("HTML shell exists", files["index"].exists()),
                ("CSS exists", files["styles"].exists()),
                ("README exists", files["readme"].exists()),
                ("Generated task summary is present", "Generated Tasks" in contents["index"]),
                ("Generated tasks file exists", task_path.exists()),
            ]

        failed_checks = [name for name, passed in checks if not passed]
        screenshot_results, screenshot_artifacts = _capture_browser_screenshots(index_path, self.project_path)
        visual_results, visual_artifacts = _write_visual_regression_report(self.project_path, screenshot_artifacts)
        evidence_results = [*screenshot_results, *visual_results]
        (qa_dir / "test-plan.md").write_text(_static_web_test_plan(), encoding="utf-8")
        evidence_checks = [(name, passed) for name, passed, _detail in evidence_results] if is_portfolio else []
        failed_checks = [name for name, passed in [*checks, *evidence_checks] if not passed]
        (qa_dir / "test-results.md").write_text(_static_web_results(checks, evidence_results if is_portfolio else []), encoding="utf-8")
        (qa_dir / "bugs.md").write_text(_bug_report(failed_checks), encoding="utf-8")

        return AgentResult(
            status="failed" if failed_checks else "completed",
            summary="Static web QA checks failed." if failed_checks else "Static web QA checks passed.",
            artifacts=["docs/qa/test-plan.md", "docs/qa/test-results.md", "docs/qa/bugs.md", *screenshot_artifacts, *visual_artifacts],
        )

    def run_next_web_checks(self) -> AgentResult:
        qa_dir = self.project_path / "docs/qa"
        qa_dir.mkdir(parents=True, exist_ok=True)
        web_dir = self.project_path / "apps/web"
        files = {
            "package": web_dir / "package.json",
            "page": web_dir / "app/page.tsx",
            "layout": web_dir / "app/layout.tsx",
            "globals": web_dir / "app/globals.css",
            "components": web_dir / "components",
            "store": web_dir / "lib/portfolio-store.ts",
            "export": web_dir / "lib/export-html.tsx",
            "trace": web_dir / "visual-direction.json",
            "source": web_dir / "v0-source/README.md",
            "portfolio_smoke": self.project_path / "tests/portfolio-builder-smoke.md",
            "tracker_smoke": self.project_path / "tests/creator-project-tracker-smoke.md",
            "api_client": web_dir / "lib/project-client.ts",
            "api_repository": web_dir / "lib/server/project-repository.ts",
            "api_health": web_dir / "app/api/health/route.ts",
            "api_projects": web_dir / "app/api/projects/route.ts",
            "api_backup": web_dir / "app/api/backup/route.ts",
            "playwright_config": web_dir / "playwright.config.ts",
            "playwright_spec": web_dir / "tests/e2e/creator-project-tracker.spec.ts",
        }
        contents = {key: path.read_text(encoding="utf-8") if path.is_file() else "" for key, path in files.items()}
        app_text = "\n".join(
            path.read_text(encoding="utf-8")
            for path in [*web_dir.glob("app/**/*.tsx"), *web_dir.glob("components/**/*.tsx"), *web_dir.glob("lib/**/*.ts"), *web_dir.glob("lib/**/*.tsx")]
            if path.is_file()
        )
        app_text_lower = app_text.lower()
        trace_text_lower = contents["trace"].lower()
        hardening_expected = (self.project_path / "docs/implementation/hardening-plan.md").exists()
        known_visual_direction_ids = [
            "minimalist-editorial",
            "bold-marketing",
            "dense-dashboard",
            "proof-first-case-study",
            "creator-studio",
        ]
        checks = [
            ("Next package exists", files["package"].exists() and '"next"' in contents["package"]),
            ("Next app route exists", files["page"].exists() and "export default" in contents["page"]),
            ("Layout and global styles exist", files["layout"].exists() and files["globals"].exists()),
            ("v0 source handoff exists", files["source"].exists() and "v0 Source Handoff" in contents["source"]),
            (
                "Visual direction trace exists",
                files["trace"].exists()
                and "selected_direction" in trace_text_lower
                and any(visual_id in trace_text_lower for visual_id in known_visual_direction_ids),
            ),
            ("Core project workflow exists", all(term in app_text for term in ["NewProjectModal", "ProjectDetail", "ProjectCard", "StatsBar"])),
            ("Task editing workflow exists", all(term in app_text_lower for term in ["addtask", "toggletask", "deletetask"])),
            ("Screenshot lifecycle exists", all(term in app_text_lower for term in ["replace screenshot", "remove screenshot", "screenshotalt"])),
            ("Local persistence is present", "localstorage" in app_text_lower),
            ("Static HTML export is present", "downloadHTML" in contents["export"] and "Blob" in contents["export"] and "downloadHTML" in app_text),
            ("Export escapes user content", "escapeHtml" in contents["export"] and "escapeAttr" in contents["export"]),
            ("Smoke-test checklist exists", files["portfolio_smoke"].exists() or files["tracker_smoke"].exists()),
        ]
        hardening_checks = [
            (
                "SQLite API routes exist",
                files["api_repository"].exists()
                and files["api_health"].exists()
                and files["api_projects"].exists()
                and files["api_backup"].exists()
                and "node:sqlite" in contents["api_repository"]
                and "listProjects" in contents["api_projects"],
            ),
            (
                "Client uses SQLite API persistence",
                files["api_client"].exists()
                and "fetchProjectsFromApi" in contents["api_client"]
                and "createProjectInApi" in app_text
                and "updateProjectInApi" in app_text
                and "deleteProjectInApi" in app_text,
            ),
            (
                "Backup import/export exists",
                files["api_backup"].exists()
                and "replaceAllProjects" in contents["api_backup"]
                and "exportBackupFromApi" in app_text
                and "importBackupToApi" in app_text,
            ),
            (
                "Browser interaction tests exist",
                files["playwright_config"].exists()
                and files["playwright_spec"].exists()
                and "creates, edits, persists, and exports" in contents["playwright_spec"],
            ),
        ]
        checks = [*checks, *hardening_checks] if hardening_expected else checks
        screenshot_results, screenshot_artifacts = _capture_next_screenshots(web_dir, self.project_path)
        visual_results, visual_artifacts = _write_visual_regression_report(self.project_path, screenshot_artifacts)
        evidence_results = [*screenshot_results, *visual_results]
        evidence_checks = [(name, passed) for name, passed, _detail in evidence_results]
        failed_checks = [name for name, passed in [*checks, *evidence_checks] if not passed]
        (qa_dir / "test-plan.md").write_text(_next_web_test_plan(), encoding="utf-8")
        (qa_dir / "test-results.md").write_text(_static_web_results(checks, evidence_results), encoding="utf-8")
        (qa_dir / "bugs.md").write_text(_bug_report(failed_checks), encoding="utf-8")

        return AgentResult(
            status="failed" if failed_checks else "completed",
            summary="Next web QA checks failed." if failed_checks else "Next web QA checks passed.",
            artifacts=["docs/qa/test-plan.md", "docs/qa/test-results.md", "docs/qa/bugs.md", *screenshot_artifacts, *visual_artifacts],
        )


def _write_visual_regression_report(project_path: Path, screenshot_artifacts: list[str]) -> tuple[list[tuple[str, bool, str]], list[str]]:
    if not screenshot_artifacts:
        return [], []
    qa_dir = project_path / "docs/qa"
    qa_dir.mkdir(parents=True, exist_ok=True)
    analyses = []
    results: list[tuple[str, bool, str]] = []
    for relative in screenshot_artifacts:
        path = project_path / relative
        analysis = analyze_screenshot(path)
        score = int(analysis.get("score") or 0)
        flags = [str(flag) for flag in analysis.get("flags") or []]
        passed = analysis.get("status") == "analyzed" and score >= 35 and "blank_or_failed_capture" not in flags
        label = "Desktop visual quality" if "desktop" in relative else "Mobile visual quality" if "mobile" in relative else "Visual quality"
        detail = f"score={score}; flags={', '.join(flags) if flags else 'none'}"
        results.append((label, passed, detail))
        analyses.append({"artifact": relative, "passed": passed, **analysis})

    report_path = qa_dir / "visual-regression-report.md"
    json_path = qa_dir / "visual-regression-report.json"
    rows = "\n".join(
        f"| {item['artifact']} | {'pass' if item['passed'] else 'fail'} | {item.get('score', 0)} | {', '.join(item.get('flags') or []) or '-'} |"
        for item in analyses
    )
    report_path.write_text(
        f"""# Visual Regression Report

| Screenshot | Result | Score | Flags |
| --- | --- | ---: | --- |
{rows}
""",
        encoding="utf-8",
    )
    json_path.write_text(json.dumps({"screenshots": analyses}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return results, ["docs/qa/visual-regression-report.md", "docs/qa/visual-regression-report.json"]


def _test_plan(commands: list[str]) -> str:
    command_list = "\n".join(f"- `{command}`" for command in commands)
    return f"""# Test Plan

## Commands

{command_list}
"""


def _static_web_test_plan() -> str:
    return """# Test Plan

## Static Web Artifact Checks

- Verify generated HTML, CSS, browser script, and smoke-test checklist exist.
- Verify the profile editor, avatar upload state handling, project gallery editor, live preview, theme switching, local persistence, and static export hooks are present.
- Verify generated preview content escapes user-controlled text before rendering.
- Verify responsive layout rules exist.
- Capture desktop and mobile browser screenshots when a local headless browser is available.
"""


def _next_web_test_plan() -> str:
    return """# Test Plan

## Next Web Artifact Checks

- Verify generated Next package, app route, layout, global styles, domain components, and UI shims exist.
- Verify the selected v0 visual direction is traceable through `visual-direction.json` and `v0-source`.
- Verify create/edit/delete workflow, task editing, local persistence, screenshot lifecycle, and static HTML export code are present.
- Verify exported HTML escapes user-controlled content.
- Verify SQLite-backed API route handlers and Playwright interaction tests exist after hardening.
- Verify client API persistence and backup import/export controls exist after hardening.
- Capture desktop and mobile screenshots from a local Next dev server when dependencies are installed.
"""


def _static_web_results(checks: list[tuple[str, bool]], screenshot_results: list[tuple[str, bool, str]] | None = None) -> str:
    rows = "\n".join(f"| {name} | {'pass' if passed else 'fail'} |" for name, passed in checks)
    screenshot_results = screenshot_results or []
    status = "failed" if any(not passed for _, passed in checks) or any(not passed for _name, passed, _detail in screenshot_results) else "passed"
    if screenshot_results:
        screenshot_rows = "\n".join(
            f"| {name} | {'pass' if passed else 'not captured'} | {detail} |" for name, passed, detail in screenshot_results
        )
        browser_evidence = f"""## Browser Screenshot Evidence

| Evidence | Result | Detail |
| --- | --- | --- |
{screenshot_rows}
"""
    else:
        browser_evidence = """## Browser Screenshot Evidence

No browser screenshot evidence was attempted for this QA run.
"""
    return f"""# Test Results

Status: {status}

| Check | Result |
| --- | --- |
{rows}

{browser_evidence}
"""


def _bug_report(failed_checks: list[str]) -> str:
    if not failed_checks:
        return """# Bugs

No blocking issues found by static web QA.
"""
    rows = "\n".join(f"- {check}" for check in failed_checks)
    return f"""# Bugs

Blocking QA failures:

{rows}
"""


def _capture_next_screenshots(web_dir: Path, project_path: Path) -> tuple[list[tuple[str, bool, str]], list[str]]:
    if not (web_dir / "node_modules").exists():
        return (
            [
                ("Desktop screenshot", False, "Next dependencies are not installed in apps/web."),
                ("Mobile screenshot", False, "Next dependencies are not installed in apps/web."),
            ],
            [],
        )
    port = _free_port()
    command = ["npm", "run", "dev", "--", "--hostname", "127.0.0.1", "--port", str(port)]
    process = subprocess.Popen(command, cwd=web_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        url = f"http://127.0.0.1:{port}"
        if not _wait_for_http(url, timeout_seconds=30):
            return (
                [
                    ("Desktop screenshot", False, "Next dev server did not become ready."),
                    ("Mobile screenshot", False, "Next dev server did not become ready."),
                ],
                [],
            )
        return _capture_browser_screenshots_for_url(url, project_path)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate(timeout=5)


def _capture_browser_screenshots(index_path: Path, project_path: Path) -> tuple[list[tuple[str, bool, str]], list[str]]:
    return _capture_browser_screenshots_for_url(index_path.resolve().as_uri(), project_path)


def _capture_browser_screenshots_for_url(url: str, project_path: Path) -> tuple[list[tuple[str, bool, str]], list[str]]:
    chrome = _find_chrome()
    qa_artifacts = project_path / ".agent/artifacts/qa"
    qa_artifacts.mkdir(parents=True, exist_ok=True)
    targets = [
        ("Desktop screenshot", "desktop-screenshot.png", "1440,1000"),
        ("Mobile screenshot", "mobile-screenshot.png", "390,844"),
    ]
    if not chrome:
        return (
            [(name, False, "No local Chrome/Chromium executable found.") for name, _, _ in targets],
            [],
        )

    results: list[tuple[str, bool, str]] = []
    artifacts: list[str] = []
    for name, filename, window_size in targets:
        output_path = qa_artifacts / filename
        if output_path.exists():
            output_path.unlink()
        with tempfile.TemporaryDirectory(prefix="agent-studio-chrome-") as profile_dir:
            command = [
                chrome,
                "--headless=new",
                "--disable-gpu",
                "--disable-background-networking",
                "--disable-sync",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-features=AutofillServerCommunication,MediaRouter,OptimizationHints",
                "--no-first-run",
                "--no-default-browser-check",
                "--hide-scrollbars",
                "--allow-file-access-from-files",
                "--run-all-compositor-stages-before-draw",
                "--virtual-time-budget=3000",
                f"--user-data-dir={profile_dir}",
                f"--window-size={window_size}",
                f"--screenshot={output_path}",
                url,
            ]
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            deadline = time.monotonic() + 25
            while time.monotonic() < deadline:
                if output_path.exists() and output_path.stat().st_size > 0:
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.1)
            captured_before_cleanup = output_path.exists() and output_path.stat().st_size > 0
            stdout, stderr, cleanup_warning = _finish_browser_process(process, captured=captured_before_cleanup)
            returncode = process.returncode
            output = cleanup_warning or stderr or stdout
        captured = output_path.exists() and output_path.stat().st_size > 0
        if captured:
            relative = output_path.relative_to(project_path).as_posix()
            results.append((name, True, relative))
            artifacts.append(relative)
        else:
            detail = (output or "Chrome screenshot command failed.").strip().splitlines()
            results.append((name, False, detail[-1] if detail else "Chrome screenshot command failed."))
    return results, artifacts


def _finish_browser_process(process: subprocess.Popen[str], *, captured: bool) -> tuple[str, str, str]:
    if process.poll() is None:
        if captured:
            process.kill()
        else:
            process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=5)
        return stdout, stderr, ""
    except subprocess.TimeoutExpired:
        process.kill()
        try:
            stdout, stderr = process.communicate(timeout=2)
            return stdout, stderr, ""
        except subprocess.TimeoutExpired:
            return "", "", "Chrome screenshot was captured, but Chrome cleanup timed out." if captured else "Chrome screenshot command timed out during cleanup."


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_http(url: str, *, timeout_seconds: int) -> bool:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=2) as response:
                if response.status < 500:
                    return True
        except (URLError, TimeoutError, OSError):
            time.sleep(0.4)
    return False


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
