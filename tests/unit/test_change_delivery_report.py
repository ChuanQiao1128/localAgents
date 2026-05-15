"""RC-4A.1: tests for orchestrator.core.change_delivery_report + delivery validator."""
from __future__ import annotations

import unittest

from orchestrator.core.artifact_validation import validate_delivery_report_text
from orchestrator.core.change_delivery_report import render_delivery_report


class DeliveryReportRendererTests(unittest.TestCase):
    def _result(self, **overrides) -> dict:
        base = {
            "change_id": "change_abc123def456",
            "result": "completed",
            "goal": "Add side-by-side diff view.",
            "files_touched": ["app/page.tsx", "components/Diff.tsx"],
            "validation": {
                "build": {"passed": True, "command": "npm run build", "duration_sec": 12.3},
                "typecheck": {"passed": True, "command": "tsc --noEmit", "duration_sec": 1.4},
            },
            "risks": ["Diff layout untested on tablets."],
            "commit": {
                "branch": "agentic/change/change_abc123def456",
                "sha": "deadbeefcafe",
                "message": "Add side-by-side diff view",
            },
            "review_queue": {"open_count": 0, "items": []},
            "elapsed_sec": 90.5,
            "created_at": "2026-05-12T00:00:00+00:00",
            "completed_at": "2026-05-12T00:01:30+00:00",
        }
        base.update(overrides)
        return base

    def test_renders_completed_result(self) -> None:
        md = render_delivery_report(self._result())
        # All required sections present
        self.assertIn("# Change Delivery Report — change_abc123def456", md)
        self.assertIn("## Goal", md)
        self.assertIn("Add side-by-side diff view.", md)
        self.assertIn("## Result", md)
        self.assertIn("**completed**", md)
        self.assertIn("## What was changed", md)
        self.assertIn("`app/page.tsx`", md)
        self.assertIn("`components/Diff.tsx`", md)
        self.assertIn("## Validation", md)
        self.assertIn("**build**: passed", md)
        self.assertIn("`npm run build`", md)
        self.assertIn("12.3s", md)
        self.assertIn("## Commit", md)
        self.assertIn("deadbeefcafe", md)
        # Validator agrees the markdown shape is OK
        self.assertEqual(validate_delivery_report_text(md), [])

    def test_renders_needs_human_review_result(self) -> None:
        result = self._result(
            result="needs-human-review",
            commit={},
            review_queue={
                "open_count": 1,
                "items": [{"review_id": "review_xyz", "title": "needs-human-review on diff layout"}],
            },
        )
        md = render_delivery_report(result)
        self.assertIn("**needs-human-review**", md)
        self.assertIn("(no commit recorded — change was not applied)", md)
        self.assertIn("## Review queue", md)
        self.assertIn("`review_xyz`", md)
        self.assertEqual(validate_delivery_report_text(md), [])

    def test_renders_failed_result_with_partial_data(self) -> None:
        result = self._result(
            result="failed",
            files_touched=[],
            validation={"build": {"passed": False, "command": "npm run build", "duration_sec": 9.0}},
            risks=["Codex patch did not survive integration."],
            commit={},
        )
        md = render_delivery_report(result)
        self.assertIn("**failed**", md)
        self.assertIn("(no files recorded as changed)", md)
        self.assertIn("**build**: failed", md)
        self.assertEqual(validate_delivery_report_text(md), [])

    def test_renders_with_completely_missing_optional_fields(self) -> None:
        # Minimal — only change_id and result
        md = render_delivery_report({"change_id": "change_min", "result": "completed"})
        self.assertIn("# Change Delivery Report — change_min", md)
        self.assertIn("(no validation results recorded)", md)
        self.assertIn("(no commit recorded — change was not applied)", md)
        self.assertEqual(validate_delivery_report_text(md), [])

    def test_unknown_result_value_is_flagged_in_output_but_does_not_raise(self) -> None:
        md = render_delivery_report(self._result(result="weird-state"))
        self.assertIn("weird-state", md)
        self.assertIn("NOT one of", md)
        # Validator still passes — section markers all present
        self.assertEqual(validate_delivery_report_text(md), [])

    def test_non_dict_input_raises(self) -> None:
        with self.assertRaises(TypeError):
            render_delivery_report("not a dict")  # type: ignore[arg-type]


class DeliveryReportValidatorTests(unittest.TestCase):
    def test_empty_text_flagged(self) -> None:
        self.assertEqual(validate_delivery_report_text(""), ["delivery-report.md is empty"])

    def test_missing_section_marker_flagged(self) -> None:
        # Missing the "## Commit" section
        text = (
            "# Change Delivery Report — x\n"
            "## Goal\n\nfoo\n"
            "## Result\n\nbar\n"
            "## What was changed\n\nbaz\n"
            "## Validation\n\nqux\n"
        )
        errors = validate_delivery_report_text(text)
        self.assertTrue(any("## Commit" in e for e in errors))


if __name__ == "__main__":
    unittest.main()
