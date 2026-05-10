from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.reference_example_discovery import (
    ReferenceExampleDiscoveryAgent,
    _extract_links,
)


class ReferenceExampleDiscoveryAgentTests(unittest.TestCase):
    def test_extract_links_resolves_and_deduplicates(self) -> None:
        html = """
        <a href="/templates/folio">Portfolio Template</a>
        <a href="https://example.com/templates/folio#preview">Duplicate</a>
        <a href="/pricing">Pricing</a>
        """

        links = _extract_links("https://example.com/templates/", html)

        self.assertEqual([link.url for link in links], ["https://example.com/templates/folio", "https://example.com/pricing"])
        self.assertEqual(links[0].text, "Portfolio Template")

    def test_discovery_uses_cached_seed_page_and_writes_examples(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            reference_dir = project_path / "docs/product/reference-products"
            cache_dir = project_path / "docs/product/reference-cache/seed-framer-portfolio"
            reference_dir.mkdir(parents=True)
            cache_dir.mkdir(parents=True)
            (reference_dir / "reference-products.json").write_text(
                json.dumps(
                    [
                        {
                            "source_id": "SEED-framer-portfolio",
                            "name": "Framer Portfolio Templates",
                            "url": "https://www.framer.com/templates/categories/portfolio/",
                            "critic_verdict": "strong_reference",
                            "total_score": 82,
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (cache_dir / "page-source.json").write_text(
                json.dumps({"url": "https://www.framer.com/templates/categories/portfolio/"}),
                encoding="utf-8",
            )
            (cache_dir / "page.html").write_text(
                """
                <a href="/templates/portfolio-designer">Portfolio Designer Template</a>
                <a href="/templates/creative-studio">Creative Studio Template</a>
                <a href="/pricing">Pricing</a>
                <a href="/login">Log in</a>
                """,
                encoding="utf-8",
            )

            result = ReferenceExampleDiscoveryAgent().run(
                project={"path": str(project_path)},
                limit=2,
                per_seed=4,
                capture=False,
                include_mobile=False,
            )

            self.assertEqual(result.seeds_scanned, 1)
            self.assertEqual(result.selected_examples, 2)
            self.assertEqual(result.captures_attempted, 0)
            self.assertTrue(result.report_path.exists())
            self.assertTrue(result.examples_json_path.exists())
            report = result.report_path.read_text(encoding="utf-8")
            self.assertIn("Specific Example References", report)
            self.assertIn("Portfolio Designer Template", report)
            examples = json.loads(result.examples_json_path.read_text(encoding="utf-8"))
            self.assertEqual(examples[0]["title"], "Portfolio Designer Template")


if __name__ == "__main__":
    unittest.main()
