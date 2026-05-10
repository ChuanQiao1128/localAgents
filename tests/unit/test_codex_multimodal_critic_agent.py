from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from orchestrator.agents.codex_multimodal_critic import CodexCliMultimodalCriticAgent


class CodexCliMultimodalCriticAgentTests(unittest.TestCase):
    def test_writes_prompt_output_and_json_without_real_codex_call(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            examples_dir = project_path / "docs/product/example-references"
            screenshots_dir = examples_dir / "screenshots"
            screenshots_dir.mkdir(parents=True)
            image_path = screenshots_dir / "example.png"
            image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")
            (examples_dir / "top-examples.json").write_text(
                json.dumps(
                    [
                        {
                            "title": "Reference",
                            "screenshots": [
                                {
                                    "status": "captured",
                                    "path": "docs/product/example-references/screenshots/example.png",
                                }
                            ],
                        }
                    ]
                ),
                encoding="utf-8",
            )
            (examples_dir / "visual-critic.md").write_text("# Existing critic", encoding="utf-8")

            completed = subprocess.CompletedProcess(
                args=["codex"],
                returncode=0,
                stdout="## 总体判断\n\n这是一张强参考截图。",
                stderr="",
            )
            with patch("orchestrator.agents.codex_multimodal_critic.shutil.which", return_value="/usr/local/bin/codex"):
                with patch("orchestrator.agents.codex_multimodal_critic._run_codex", return_value=completed) as run:
                    result = CodexCliMultimodalCriticAgent().run(
                        project={"path": str(project_path), "idea": "做 portfolio builder"},
                        model="gpt-5.5",
                        limit=1,
                    )

            self.assertEqual(result.status, "completed")
            self.assertEqual(result.image_count, 1)
            self.assertTrue(result.prompt_path.exists())
            self.assertTrue(result.output_path.exists())
            self.assertIn("资深 UI/UX", result.prompt_path.read_text(encoding="utf-8"))
            payload = json.loads(result.json_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["provider"], "codex-cli")
            self.assertEqual(payload["image_count"], 1)
            self.assertIn("强参考", result.report_path.read_text(encoding="utf-8"))
            command = run.call_args.args[0]
            self.assertIn("-i", command)
            self.assertIn(str(image_path), command)


if __name__ == "__main__":
    unittest.main()
