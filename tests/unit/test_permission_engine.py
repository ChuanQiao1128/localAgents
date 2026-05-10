from __future__ import annotations

import unittest

from orchestrator.core.permission_engine import PermissionEngine


class PermissionEngineTests(unittest.TestCase):
    def test_write_must_match_allowed_path_and_not_denied_path(self) -> None:
        engine = PermissionEngine()
        self.assertTrue(engine.can_write("apps/web/page.tsx", ["apps/**"], [".env", "~/**"]))
        self.assertFalse(engine.can_write("docs/product/prd.md", ["apps/**"], [".env", "~/**"]))
        self.assertFalse(engine.can_write(".env", ["**/*"], [".env", "~/**"]))


if __name__ == "__main__":
    unittest.main()

