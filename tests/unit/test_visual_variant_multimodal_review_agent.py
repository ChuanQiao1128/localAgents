from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.agents.visual_variant_multimodal_review import VisualVariantMultimodalReviewAgent


class VisualVariantMultimodalReviewAgentTests(unittest.TestCase):
    def test_reviews_variant_screenshots_and_updates_selected_direction(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            artifact_dir = project_path / ".agent/artifacts/visual_directions"
            minimal_dir = artifact_dir / "minimalist-editorial"
            dense_dir = artifact_dir / "dense-dashboard"
            minimal_dir.mkdir(parents=True)
            dense_dir.mkdir(parents=True)
            (minimal_dir / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\nminimal")
            (dense_dir / "screenshot.png").write_bytes(b"\x89PNG\r\n\x1a\ndense")
            (artifact_dir / "variants.json").write_text(
                json.dumps(
                    {
                        "provider": "mock",
                        "winner": {"id": "minimalist-editorial", "name": "Minimalist Editorial"},
                        "variants": [
                            {
                                "id": "minimalist-editorial",
                                "name": "Minimalist Editorial",
                                "axis": "quiet editorial",
                                "status": "completed",
                                "scores": {"total": 104},
                            },
                            {
                                "id": "dense-dashboard",
                                "name": "Dense Dashboard",
                                "axis": "work focused",
                                "status": "completed",
                                "scores": {"total": 95},
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )
            (project_path / "docs/product/example-references").mkdir(parents=True)
            (project_path / "docs/product/example-references/multimodal-critic.md").write_text(
                "reference critic",
                encoding="utf-8",
            )
            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="## Winner\n\nWinner: `dense-dashboard`\n\n## Pairwise Review\n\nDense wins.",
                stderr="",
            )
            with patch("orchestrator.agents.visual_variant_multimodal_review._codex_command", return_value=["codex"]):
                with patch("orchestrator.agents.visual_variant_multimodal_review._run_codex", return_value=completed) as run:
                    result = VisualVariantMultimodalReviewAgent().run(
                        project={"path": str(project_path), "idea": "portfolio builder"},
                        model="gpt-5.5",
                    )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.winner_id, "dense-dashboard")
            self.assertEqual(result.image_count, 2)
            self.assertTrue(result.report_path.exists())
            self.assertIn("dense-dashboard", result.selected_path.read_text(encoding="utf-8"))
            payload = json.loads(result.json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["winner_id"], "dense-dashboard")
            variants_payload = json.loads((artifact_dir / "variants.json").read_text(encoding="utf-8"))
            self.assertEqual(variants_payload["multimodal_review"]["winner_id"], "dense-dashboard")
            self.assertIn("portfolio builder", run.call_args.kwargs["prompt"])


if __name__ == "__main__":
    unittest.main()
