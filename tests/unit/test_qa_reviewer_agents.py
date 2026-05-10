from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.developer import DeveloperAgent
from orchestrator.agents.qa import QAAgent
from orchestrator.agents.reviewer import ReviewerAgent
from orchestrator.agents.qa import _find_chrome


# Two tests below run a real QA pass that captures Desktop / Mobile browser
# screenshots through Chromium. When no Chrome / Chromium binary is on the
# system, the screenshot evidence is reported as "not captured" and the
# overall QA result flips to "failed" — that is correct production behavior
# but makes these tests environment-dependent. They are RC-1 conditionally
# skipped, with an explicit reason, so a clean CI environment shows them as
# `skipped`, not `failed`.
_HAS_CHROME = _find_chrome() is not None
_SKIP_REASON = (
    "requires a local Chromium / Chrome binary for browser screenshot "
    "evidence (QAAgent flips to status=failed when screenshots cannot be "
    "captured); install Chrome or Chromium to run these checks"
)


class QaReviewerAgentTests(unittest.TestCase):
    def test_qa_agent_records_successful_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = QAAgent(root).run_checks(["python3 -c \"print('qa-ok')\""])

            self.assertEqual(result.status, "completed")
            report = (root / "docs/qa/test-results.md").read_text(encoding="utf-8")
            self.assertIn("qa-ok", report)

    def test_qa_agent_records_failed_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = QAAgent(root).run_checks(["python3 -c \"raise SystemExit(2)\""])

            self.assertEqual(result.status, "failed")
            self.assertTrue((root / "docs/qa/test-results.md").exists())

    @unittest.skipUnless(_HAS_CHROME, _SKIP_REASON)
    def test_qa_agent_checks_generated_static_web_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_dir = root / ".agent/tasks"
            tasks_dir.mkdir(parents=True)
            tasks = [
                {"id": "WEB-001", "title": "Build profile editor with avatar upload states"},
                {"id": "WEB-002", "title": "Build project gallery editor"},
                {"id": "WEB-003", "title": "Build theme selector and live preview"},
                {"id": "EXPORT-001", "title": "Implement static HTML export from preview render model"},
            ]
            (tasks_dir / "generated-tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
            DeveloperAgent(root).implement_generated_tasks()

            result = QAAgent(root).run_checks()
            report = (root / "docs/qa/test-results.md").read_text(encoding="utf-8")
            bugs = (root / "docs/qa/bugs.md").read_text(encoding="utf-8")

            self.assertEqual(result.status, "completed")
            self.assertIn("Static HTML export handler is present", report)
            self.assertIn("No blocking issues", bugs)

    def test_reviewer_agent_writes_report_from_git_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            (root / "README.md").write_text("hello\n", encoding="utf-8")

            result = ReviewerAgent(root).review_diff()

            self.assertEqual(result.status, "completed")
            self.assertTrue((root / "docs/review/review-report.md").exists())

    @unittest.skipUnless(_HAS_CHROME, _SKIP_REASON)
    def test_reviewer_agent_reviews_generated_static_web_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_dir = root / ".agent/tasks"
            tasks_dir.mkdir(parents=True)
            tasks = [
                {"id": "WEB-001", "title": "Build profile editor with avatar upload states"},
                {"id": "WEB-002", "title": "Build project gallery editor"},
                {"id": "WEB-003", "title": "Build theme selector and live preview"},
                {"id": "EXPORT-001", "title": "Implement static HTML export from preview render model"},
            ]
            (tasks_dir / "generated-tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
            DeveloperAgent(root).implement_generated_tasks()
            QAAgent(root).run_checks()

            result = ReviewerAgent(root).review_diff()
            report = (root / "docs/review/review-report.md").read_text(encoding="utf-8")

            self.assertEqual(result.status, "completed")
            self.assertIn("Status: approve", report)
            self.assertIn("Static export task covered", report)

    def test_qa_and_reviewer_accept_generic_static_web_app(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_dir = root / ".agent/tasks"
            tasks_dir.mkdir(parents=True)
            tasks = [
                {"id": "WEB-001", "title": "Build invoice tracker shell"},
                {"id": "API-001", "title": "Plan billable time entry API"},
            ]
            (tasks_dir / "generated-tasks.json").write_text(json.dumps(tasks), encoding="utf-8")
            DeveloperAgent(root).implement_generated_tasks()

            qa = QAAgent(root).run_checks()
            review = ReviewerAgent(root).review_diff()
            review_report = (root / "docs/review/review-report.md").read_text(encoding="utf-8")

            self.assertEqual(qa.status, "completed")
            self.assertEqual(review.status, "completed")
            self.assertIn("Generated task summary covered", review_report)


if __name__ == "__main__":
    unittest.main()
