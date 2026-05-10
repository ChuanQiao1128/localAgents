from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.reference_visual_research import ReferenceVisualResearchAgent


class ReferenceVisualResearchAgentTests(unittest.TestCase):
    def test_visual_research_writes_manifest_and_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            reference_dir = project_path / "docs/product/reference-products"
            reference_dir.mkdir(parents=True)
            (reference_dir / "reference-products.json").write_text(
                json.dumps(
                    [
                        {
                            "source_id": "S1",
                            "name": "Framer Portfolio Templates",
                            "url": "not-a-url",
                            "critic_verdict": "strong_reference",
                            "total_score": 90,
                        }
                    ]
                ),
                encoding="utf-8",
            )

            result = ReferenceVisualResearchAgent().run(
                project={"path": str(project_path)},
                limit=1,
            )

            self.assertEqual(result.attempted, 2)
            self.assertEqual(result.captured, 0)
            self.assertTrue(result.report_path.exists())
            self.assertTrue(result.manifest_path.exists())
            self.assertIn("Reference Screenshot Capture Report", result.report_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
