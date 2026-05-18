from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.core.product_review import run_product_review


SAFE_NATURALIZER_SOURCE = """
AI Writing Naturalizer
Third-party reference signal. Detector output is reference only and is not optimized against.
Reference score increased. Add more user context or verify claims before using.
This product does not claim to bypass any detector and does not measure success by detector evasion.
claim_not_in_source introduced claim fabrication source
Audience Purpose Actual work Facts that must be preserved. Constraints or tone notes.
Rewrite provider Codex CLI. Detector provider real provider. Mock fallback.
One rewrite produces one report; the app does not auto retry. Add context and verify claims.
nextSuggestions
"""


class ProductReviewCoreTests(unittest.TestCase):
    def test_naturalizer_rubric_passes_with_reference_signal_wording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "page.tsx").write_text(SAFE_NATURALIZER_SOURCE, encoding="utf-8")

            result = run_product_review(
                root,
                project_id="ai-writing-naturalizer",
                project_name="AI Writing Naturalizer",
            )

            self.assertEqual(result.schema_version, "studio.product_review.v2")
            self.assertEqual(result.verdict, "pass")
            self.assertEqual(result.score, 100)
            self.assertTrue(all(f.status == "resolved" for f in result.findings))
            self.assertTrue((root / ".agent" / "product-reviews" / result.review_id / "product-review.json").exists())

    def test_detector_as_success_wording_is_open_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / "app" / "page.tsx").write_text(
                "AI Writing Naturalizer selected detector score score delta detector mode",
                encoding="utf-8",
            )

            result = run_product_review(
                root,
                project_id="ai-writing-naturalizer",
                project_name="AI Writing Naturalizer",
            )
            by_id = {finding.id: finding for finding in result.findings}

            self.assertEqual(by_id["NAT-001"].status, "open")
            self.assertIn(result.verdict, {"needs_work", "unsafe", "pass_with_recommendations"})

    def test_nat_004_and_nat_005_are_not_false_positives_when_safe_wording_exists(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "lib").mkdir()
            (root / "lib" / "naturalize.ts").write_text(SAFE_NATURALIZER_SOURCE, encoding="utf-8")

            result = run_product_review(
                root,
                project_id="ai-writing-naturalizer",
                project_name="AI Writing Naturalizer",
            )
            by_id = {finding.id: finding for finding in result.findings}

            self.assertEqual(by_id["NAT-004"].status, "resolved")
            self.assertEqual(by_id["NAT-005"].status, "resolved")

    def test_env_local_is_not_read_as_review_input(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            (root / ".env.local").write_text(SAFE_NATURALIZER_SOURCE, encoding="utf-8")
            (root / "app" / "page.tsx").write_text(
                "AI Writing Naturalizer selected detector score score delta",
                encoding="utf-8",
            )

            result = run_product_review(
                root,
                project_id="ai-writing-naturalizer",
                project_name="AI Writing Naturalizer",
            )
            by_id = {finding.id: finding for finding in result.findings}

            self.assertNotIn(".env.local", result.inputs_read)
            self.assertEqual(by_id["NAT-002"].status, "open")

    def test_pass_with_recommendations_for_medium_open_finding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "app").mkdir()
            source = SAFE_NATURALIZER_SOURCE.replace(
                "One rewrite produces one report; the app does not auto retry. Add context and verify claims.\nnextSuggestions",
                "",
            )
            (root / "app" / "page.tsx").write_text(source, encoding="utf-8")

            result = run_product_review(
                root,
                project_id="ai-writing-naturalizer",
                project_name="AI Writing Naturalizer",
            )

            self.assertEqual(result.verdict, "pass_with_recommendations")
            self.assertTrue(any(f.id == "NAT-008" and f.status == "open" for f in result.findings))

    def test_missing_runtime_source_produces_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = run_product_review(
                Path(tmp),
                project_id="ai-writing-naturalizer",
                project_name="AI Writing Naturalizer",
            )

            self.assertEqual(result.verdict, "needs_work")
            self.assertEqual(result.findings[0].id, "GEN-001")
            self.assertIn("runtime source context is missing", result.summary)


if __name__ == "__main__":
    unittest.main()
