"""RC-4A.1: tests for orchestrator.core.change_repo_onboarding."""
from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from orchestrator.core.change_repo_onboarding import render_repo_onboarding, scan_repo


class RepoOnboardingScannerTests(unittest.TestCase):
    def test_detects_nextjs_prisma_typescript_stack(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "package.json").write_text(json.dumps({
                "name": "demo",
                "scripts": {
                    "build": "next build",
                    "test": "jest",
                    "typecheck": "tsc --noEmit",
                },
                "dependencies": {"next": "15.5.18", "react": "19.0.0", "@prisma/client": "5.22.0"},
                "devDependencies": {"typescript": "5.7.3", "tailwindcss": "3.4.17", "prisma": "5.22.0"},
            }), encoding="utf-8")
            (project / "tsconfig.json").write_text("{}", encoding="utf-8")
            (project / "vercel.json").write_text("{}", encoding="utf-8")
            (project / "prisma").mkdir()
            (project / "app").mkdir()
            (project / "components").mkdir()

            scan = scan_repo(project)

            self.assertTrue(scan["stack"]["next_js"])
            self.assertTrue(scan["stack"]["react"])
            self.assertTrue(scan["stack"]["typescript"])
            self.assertTrue(scan["stack"]["tailwind"])
            self.assertTrue(scan["stack"]["prisma"])
            self.assertTrue(scan["stack"]["vercel_json"])
            self.assertEqual(scan["package_scripts"]["build"], "next build")
            self.assertEqual(scan["build_commands"]["typecheck"], "tsc --noEmit")
            self.assertIn("app", scan["top_level_dirs"])
            self.assertIn("components", scan["top_level_dirs"])
            self.assertIn("prisma", scan["top_level_dirs"])

    def test_handles_missing_package_json_gracefully(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            scan = scan_repo(project)
            self.assertFalse(scan["stack"]["package_json"])
            self.assertEqual(scan["package_scripts"], {})
            self.assertEqual(scan["build_commands"], {"build": None, "test": None, "typecheck": None})
            # Renderer must not crash on empty input
            md = render_repo_onboarding(scan)
            self.assertIn("# Repo Onboarding", md)

    def test_detects_python_backend_with_endpoints(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "backend" / "app").mkdir(parents=True)
            (project / "backend" / "tests").mkdir(parents=True)
            (project / "backend" / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
            (project / "backend" / "app" / "main.py").write_text(
                'from fastapi import FastAPI\n'
                'app = FastAPI()\n'
                '\n'
                '@app.get("/health")\n'
                'def health(): return {"status": "ok"}\n'
                '\n'
                '@app.post("/rewrite")\n'
                'def rewrite(): pass\n',
                encoding="utf-8",
            )

            scan = scan_repo(project)

            self.assertTrue(scan["stack"]["python_backend_dir"])
            self.assertTrue(scan["backend_indicators"]["backend_dir"])
            self.assertTrue(scan["backend_indicators"]["backend_app_main"])
            self.assertTrue(scan["backend_indicators"]["backend_requirements_txt"])
            self.assertIn("GET /health", scan["endpoints"])
            self.assertIn("POST /rewrite", scan["endpoints"])

    def test_git_log_subjects_oldest_first_when_repo_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "README.md").write_text("# demo\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", "-b", "main"], cwd=project, check=True)
            subprocess.run(["git", "config", "user.email", "t@t"], cwd=project, check=True)
            subprocess.run(["git", "config", "user.name", "t"], cwd=project, check=True)
            for subject in ("first", "second", "third"):
                (project / "README.md").write_text(f"# {subject}\n", encoding="utf-8")
                subprocess.run(["git", "add", "-A"], cwd=project, check=True)
                subprocess.run(
                    ["git", "-c", "commit.gpgsign=false", "commit", "-q", "-m", subject],
                    cwd=project, check=True,
                )

            scan = scan_repo(project)

            self.assertEqual(scan["git_log"], ["first", "second", "third"])

    def test_no_git_history_returns_empty_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            scan = scan_repo(project)
            self.assertEqual(scan["git_log"], [])

    def test_readme_excerpt_truncated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            big = "# Demo\n\n" + ("words " * 200)
            (project / "README.md").write_text(big, encoding="utf-8")
            scan = scan_repo(project)
            self.assertIsNotNone(scan["readme_excerpt"])
            self.assertLessEqual(len(scan["readme_excerpt"]), 500)

    def test_render_is_deterministic_for_same_scan(self) -> None:
        scan = {
            "project_path": "/x",
            "stack": {"next_js": True, "react": True},
            "package_scripts": {"build": "next build"},
            "top_level_dirs": ["app"],
            "backend_indicators": {"backend_dir": False},
            "endpoints": [],
            "git_log": [],
            "readme_excerpt": None,
            "build_commands": {"build": "next build", "test": None, "typecheck": None},
        }
        first = render_repo_onboarding(scan)
        second = render_repo_onboarding(scan)
        self.assertEqual(first, second)
        self.assertIn("# Repo Onboarding", first)
        self.assertIn("`/x`", first)
        self.assertIn("`build`: `next build`", first)


if __name__ == "__main__":
    unittest.main()
