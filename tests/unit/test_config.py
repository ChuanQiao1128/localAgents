from __future__ import annotations

import os
import tempfile
import unittest

from orchestrator.config import load_local_env, resolve_paths


class ConfigTests(unittest.TestCase):
    def test_load_local_env_does_not_override_existing_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = resolve_paths(tmp)
            paths.env_file.write_text(
                "TAVILY_API_KEY=from-file\nEXTRA_VALUE='ok'\n",
                encoding="utf-8",
            )
            original = os.environ.get("TAVILY_API_KEY")
            try:
                os.environ["TAVILY_API_KEY"] = "from-env"
                os.environ.pop("EXTRA_VALUE", None)

                load_local_env(paths)

                self.assertEqual(os.environ["TAVILY_API_KEY"], "from-env")
                self.assertEqual(os.environ["EXTRA_VALUE"], "ok")
            finally:
                if original is None:
                    os.environ.pop("TAVILY_API_KEY", None)
                else:
                    os.environ["TAVILY_API_KEY"] = original
                os.environ.pop("EXTRA_VALUE", None)


if __name__ == "__main__":
    unittest.main()
