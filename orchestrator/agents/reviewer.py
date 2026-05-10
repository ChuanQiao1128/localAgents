from __future__ import annotations

import json
from pathlib import Path

from orchestrator.tools.git_tools import GitTools

from .base import AgentResult


class ReviewerAgent:
    id = "reviewer"

    def __init__(self, project_path: Path):
        self.project_path = project_path
        self.git = GitTools(project_path)

    def review_diff(self) -> AgentResult:
        if (self.project_path / "apps/web/index.html").exists():
            return self.review_static_web_implementation()

        review_dir = self.project_path / "docs/review"
        review_dir.mkdir(parents=True, exist_ok=True)
        diff = self.git.diff()
        status = "completed" if diff.ok else "failed"
        report = _review_report(diff.stdout, diff.stderr, diff.ok)
        (review_dir / "review-report.md").write_text(report, encoding="utf-8")
        return AgentResult(
            status=status,
            summary="Review completed." if diff.ok else "Review could not read git diff.",
            artifacts=["docs/review/review-report.md"],
            requires_approval=not diff.ok,
        )

    def review_static_web_implementation(self) -> AgentResult:
        review_dir = self.project_path / "docs/review"
        review_dir.mkdir(parents=True, exist_ok=True)
        report, approved = _static_web_review_report(self.project_path)
        (review_dir / "review-report.md").write_text(report, encoding="utf-8")
        return AgentResult(
            status="completed" if approved else "failed",
            summary="Static web implementation approved." if approved else "Static web implementation needs changes.",
            artifacts=["docs/review/review-report.md"],
            requires_approval=not approved,
        )


def _review_report(stdout: str, stderr: str, ok: bool) -> str:
    if not ok:
        return f"""# Review Report

Status: request_changes

Git diff could not be read.

```text
{stderr.strip()}
```
"""
    return f"""# Review Report

Status: approve

## Diff Summary

```diff
{stdout.strip() or "No diff."}
```
"""


def _static_web_review_report(project_path: Path) -> tuple[str, bool]:
    task_path = project_path / ".agent/tasks/generated-tasks.json"
    qa_report_path = project_path / "docs/qa/test-results.md"
    qa_bugs_path = project_path / "docs/qa/bugs.md"
    index_path = project_path / "apps/web/index.html"
    web_dir = project_path / "apps/web"
    index_text = index_path.read_text(encoding="utf-8") if index_path.exists() else ""
    tasks = _load_generated_tasks(task_path)
    task_text = json.dumps(tasks, ensure_ascii=False).lower()
    is_next = (web_dir / "package.json").exists() and (web_dir / "app/page.tsx").exists()
    is_portfolio = "portfolio" in task_text or "Portfolio Builder MVP" in index_text
    if is_next:
        files = {
            "apps/web/package.json": web_dir / "package.json",
            "apps/web/app/page.tsx": web_dir / "app/page.tsx",
            "apps/web/app/export/page.tsx": web_dir / "app/export/page.tsx",
            "apps/web/app/layout.tsx": web_dir / "app/layout.tsx",
            "apps/web/app/globals.css": web_dir / "app/globals.css",
            "apps/web/lib/export-html.tsx": web_dir / "lib/export-html.tsx",
            "apps/web/visual-direction.json": web_dir / "visual-direction.json",
            "apps/web/v0-source/README.md": web_dir / "v0-source/README.md",
            "tests/creator-project-tracker-smoke.md": project_path / "tests/creator-project-tracker-smoke.md",
        }
    elif is_portfolio:
        files = {
            "apps/web/index.html": index_path,
            "apps/web/styles.css": project_path / "apps/web/styles.css",
            "apps/web/app.js": project_path / "apps/web/app.js",
            "tests/portfolio-builder-smoke.md": project_path / "tests/portfolio-builder-smoke.md",
        }
    else:
        files = {
            "apps/web/index.html": index_path,
            "apps/web/styles.css": project_path / "apps/web/styles.css",
            "apps/web/README.md": project_path / "apps/web/README.md",
        }
    contents = {name: path.read_text(encoding="utf-8") if path.exists() else "" for name, path in files.items()}
    next_text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in [*web_dir.glob("app/**/*.tsx"), *web_dir.glob("components/**/*.tsx"), *web_dir.glob("lib/**/*.ts"), *web_dir.glob("lib/**/*.tsx")]
        if path.is_file()
    )
    next_text_lower = next_text.lower()
    script = contents.get("apps/web/app.js", "")
    index = contents.get("apps/web/index.html", "")

    shared_checks = [
        ("Architecture task file exists", task_path.exists()),
        ("Implementation files exist", all(path.exists() for path in files.values())),
        ("QA report exists", qa_report_path.exists()),
        ("QA report passed", "Status: failed" not in qa_report_path.read_text(encoding="utf-8") if qa_report_path.exists() else False),
        ("Bug report has no blocking issues", "No blocking issues" in qa_bugs_path.read_text(encoding="utf-8") if qa_bugs_path.exists() else False),
    ]
    if is_next:
        qa_text = qa_report_path.read_text(encoding="utf-8").lower() if qa_report_path.exists() else ""
        domain_checks = [
            ("Next app route and v0 handoff exist", bool(contents.get("apps/web/app/page.tsx")) and "v0 source handoff" in contents.get("apps/web/v0-source/README.md", "").lower()),
            ("Project CRUD flow exists", all(term in next_text for term in ["NewProjectModal", "ProjectDetail", "onCreate", "onDelete"])),
            ("Task and status workflow exists", all(term in next_text_lower for term in ["status", "addtask", "toggletask", "deletetask"])),
            ("Screenshot lifecycle exists", all(term in next_text_lower for term in ["replace screenshot", "remove screenshot", "screenshotalt"])),
            ("Local persistence exists", "localstorage" in next_text_lower),
            ("Static export is wired and escaped", all(term in next_text for term in ["downloadHTML", "generateHTML", "escapeHtml", "escapeAttr", "Blob"])),
            ("Browser screenshot evidence exists", "desktop screenshot | pass" in qa_text and "mobile screenshot | pass" in qa_text),
        ]
    elif is_portfolio:
        domain_checks = [
            ("Profile task covered", "avatar" in task_text and "avatarInput" in index and "readImage" in script),
            ("Project gallery task covered", "project" in task_text and "projectTemplate" in index and "moveProject" in script),
            ("Theme and preview task covered", "theme" in task_text and "renderPreview" in script),
            ("Static export task covered", "static html export" in task_text and "exportStaticHtml" in script),
            ("User-controlled content is escaped", "escapeHtml" in script and "escapeAttr" in script),
        ]
    else:
        domain_checks = [
            ("Generated task summary covered", "Generated Tasks" in index),
            ("Generic static page has no unnecessary browser script dependency", "apps/web/app.js" not in files),
        ]
    checks = shared_checks + domain_checks
    failed_checks = [name for name, passed in checks if not passed]
    status = "approve" if not failed_checks else "request_changes"
    rows = "\n".join(f"| {name} | {'pass' if passed else 'fail'} |" for name, passed in checks)
    findings = "No blocking findings." if not failed_checks else "\n".join(f"- {check}" for check in failed_checks)
    report = f"""# Review Report

Status: {status}

## Scope Review

- Generated tasks reviewed: {len(tasks)}
- Implementation files reviewed: {len(files)}
- QA report: {'present' if qa_report_path.exists() else 'missing'}

## Checks

| Check | Result |
| --- | --- |
{rows}

## Findings

{findings}
"""
    return report, not failed_checks


def _load_generated_tasks(task_path: Path) -> list[dict[str, object]]:
    if not task_path.exists():
        return []
    try:
        loaded = json.loads(task_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    if not isinstance(loaded, list):
        return []
    return [item for item in loaded if isinstance(item, dict)]
