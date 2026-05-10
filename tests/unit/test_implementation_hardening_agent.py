from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.agents.implementation_hardening import ImplementationHardeningAgent


class ImplementationHardeningAgentTests(unittest.TestCase):
    def test_hardening_generates_backend_api_and_browser_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            web = root / "apps/web"
            (web / "app").mkdir(parents=True)
            (web / "package.json").write_text(
                '{"name":"demo","private":true,"scripts":{"dev":"next dev --webpack"},"devDependencies":{}}\n',
                encoding="utf-8",
            )
            (web / "app/page.tsx").write_text("export default function Page(){ return null }\n", encoding="utf-8")

            result = ImplementationHardeningAgent(root).run()

            self.assertEqual(result.status, "completed")
            self.assertTrue((web / "lib/server/project-repository.ts").exists())
            self.assertTrue((web / "lib/project-client.ts").exists())
            self.assertTrue((web / "app/api/projects/route.ts").exists())
            self.assertTrue((web / "app/api/backup/route.ts").exists())
            self.assertTrue((web / "tests/e2e/creator-project-tracker.spec.ts").exists())
            self.assertIn("node:sqlite", (web / "lib/server/project-repository.ts").read_text(encoding="utf-8"))
            self.assertIn("exportBackupFromApi", (web / "lib/project-client.ts").read_text(encoding="utf-8"))
            self.assertIn("test:e2e", (web / "package.json").read_text(encoding="utf-8"))
            self.assertIn("Creator Project Tracker API", (root / "docs/architecture/api.openapi.yaml").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
