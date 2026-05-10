from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.developer import DeveloperAgent
from orchestrator.tools.file_tools import FileTools
from orchestrator.tools.git_tools import GitTools
from orchestrator.tools.shell_tools import ShellTools


class ToolTests(unittest.TestCase):
    def test_file_tools_enforce_allowed_paths_and_root_boundary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tools = FileTools(root, ["apps/**"], [".env", "~/**"])

            allowed = tools.write_text("apps/web/page.tsx", "export default function Page() { return null }")
            denied = tools.write_text("docs/product/prd.md", "# PRD")

            self.assertTrue(allowed.ok)
            self.assertFalse(denied.ok)
            with self.assertRaises(PermissionError):
                tools.write_text("../outside.txt", "no")

    def test_shell_tools_block_dangerous_commands_and_run_safe_commands(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            shell = ShellTools(Path(tmp))

            safe = shell.run(["python3", "-c", "print('ok')"])
            blocked = shell.run("sudo whoami")

            self.assertTrue(safe.ok)
            self.assertEqual(safe.stdout.strip(), "ok")
            self.assertTrue(blocked.blocked)

    def test_git_tools_status_in_repository(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)

            result = GitTools(root).status()

            self.assertTrue(result.ok)

    def test_developer_agent_writes_only_allowed_placeholder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = DeveloperAgent(root).create_placeholder_web_page()

            self.assertEqual(result.status, "completed")
            self.assertTrue((root / "apps/web/README.md").exists())

    def test_developer_agent_implements_portfolio_generated_tasks(self) -> None:
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
            design_dir = root / "docs/design"
            design_dir.mkdir(parents=True)
            (design_dir / "selected-visual-direction.md").write_text("Winner: `minimalist-editorial`", encoding="utf-8")
            v0_file = root / ".agent/artifacts/visual_directions/minimalist-editorial/files/app/page.tsx"
            v0_file.parent.mkdir(parents=True, exist_ok=True)
            v0_file.write_text("export default function Page(){return <main>v0 source</main>}\n", encoding="utf-8")
            variants = {
                "winner": {
                    "id": "minimalist-editorial",
                    "web_url": "https://v0.app/chat/test",
                    "demo_url": "https://demo.example?__v0_token=secret",
                    "files": [str(v0_file.relative_to(root))],
                }
            }
            (root / ".agent/artifacts/visual_directions/variants.json").write_text(json.dumps(variants), encoding="utf-8")

            result = DeveloperAgent(root).implement_generated_tasks()
            store_ts = (root / "apps/web/lib/portfolio-store.ts").read_text(encoding="utf-8")
            export_ts = (root / "apps/web/lib/export-html.tsx").read_text(encoding="utf-8")
            index_html = (root / "apps/web/index.html").read_text(encoding="utf-8")
            readme = (root / "apps/web/README.md").read_text(encoding="utf-8")
            visual_trace = json.loads((root / "apps/web/visual-direction.json").read_text(encoding="utf-8"))

            self.assertEqual(result.status, "completed")
            self.assertTrue((root / "apps/web/package.json").exists())
            self.assertTrue((root / "apps/web/app/page.tsx").exists())
            self.assertTrue((root / "apps/web/components/ui/button.tsx").exists())
            self.assertTrue((root / "tests/portfolio-builder-smoke.md").exists())
            self.assertTrue((root / "apps/web/v0-source/app/page.tsx").exists())
            self.assertTrue((root / "apps/web/visual-direction.json").exists())
            self.assertIn("Portfolio Builder Next App", index_html)
            self.assertEqual(visual_trace["selected_direction"], "minimalist-editorial")
            self.assertIn("apps/web/v0-source/", readme)
            self.assertNotIn("__v0_token", readme)
            self.assertIn("localStorage", store_ts)
            self.assertIn("downloadHTML", export_ts)
            self.assertIn("Blob", export_ts)
            self.assertIn("escapeHtml", export_ts)

    def test_developer_agent_prefers_multimodal_visual_direction_over_pairwise_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            tasks_dir = root / ".agent/tasks"
            tasks_dir.mkdir(parents=True)
            (tasks_dir / "generated-tasks.json").write_text(
                json.dumps([{"id": "WEB-001", "title": "Build portfolio export dashboard"}]),
                encoding="utf-8",
            )
            design_dir = root / "docs/design"
            design_dir.mkdir(parents=True)
            (design_dir / "selected-visual-direction.md").write_text(
                "# Selected Visual Direction\n\nWinner: `dense-dashboard`\n",
                encoding="utf-8",
            )
            artifact_dir = root / ".agent/artifacts/visual_directions"
            dense_file = artifact_dir / "dense-dashboard/files/index.html"
            minimal_file = artifact_dir / "minimalist-editorial/files/index.html"
            dense_file.parent.mkdir(parents=True)
            minimal_file.parent.mkdir(parents=True)
            dense_file.write_text("<main>dense visual source</main>\n", encoding="utf-8")
            minimal_file.write_text("<main>minimal visual source</main>\n", encoding="utf-8")
            (artifact_dir / "variants.json").write_text(
                json.dumps(
                    {
                        "winner": {
                            "id": "minimalist-editorial",
                            "files": [str(minimal_file.relative_to(root))],
                        },
                        "multimodal_review": {
                            "winner_id": "dense-dashboard",
                            "report_path": "docs/design/visual-direction-multimodal-review.md",
                        },
                        "variants": [
                            {
                                "id": "minimalist-editorial",
                                "files": [str(minimal_file.relative_to(root))],
                            },
                            {
                                "id": "dense-dashboard",
                                "screenshot_path": ".agent/artifacts/visual_directions/dense-dashboard/screenshot.png",
                                "files": [str(dense_file.relative_to(root))],
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            result = DeveloperAgent(root).implement_generated_tasks()
            visual_trace = json.loads((root / "apps/web/visual-direction.json").read_text(encoding="utf-8"))
            copied_source = (root / "apps/web/v0-source/index.html").read_text(encoding="utf-8")
            app_js = (root / "apps/web/app.js").read_text(encoding="utf-8")

            self.assertEqual(result.status, "completed")
            self.assertEqual(visual_trace["selected_direction"], "dense-dashboard")
            self.assertEqual(visual_trace["selection_method"], "multimodal_review")
            self.assertEqual(visual_trace["review_artifact"], "docs/design/visual-direction-multimodal-review.md")
            self.assertIn("dense visual source", copied_source)
            self.assertIn("selected dense-dashboard direction", app_js)


if __name__ == "__main__":
    unittest.main()
