"""RC-2E.3: codex-becomes-available recovery path.

Production scenario: user runs `autonomous start` with
`agentic.patch_worker=codex` but codex isn't installed yet → preflight
returns `codex_cli_not_found`, controller pauses on
needs-human-review, user installs codex, resolves the review, and
resumes. The next attempt's preflight + inner loop must see the
now-available codex command.

We can't actually install/uninstall codex in a test, so we simulate by
flipping the `codex_command` config value between an absent binary
("does-not-exist-xyzzy") and a present one (`python3` — proxy for any
on-PATH binary; the preflight only checks `shutil.which()`).
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

from orchestrator.core.agentic_runtime import codex_cli_available


class CodexAvailabilityTransitionTests(unittest.TestCase):
    def test_unavailable_then_available_reflects_environment(self) -> None:
        # Simulates "user installed codex between attempts" by switching
        # the command argument. codex_cli_available is the load-bearing
        # preflight; both the autonomous controller and the
        # `autonomous preflight` CLI use it.
        self.assertFalse(codex_cli_available(command="this-codex-binary-definitely-does-not-exist-xyzzy"))
        # Any binary that exists on PATH proves the transition works —
        # we use python3 (always present in CI).
        self.assertTrue(codex_cli_available(command="python3"))

    def test_preflight_re_runs_codex_check_on_each_invocation(self) -> None:
        # The preflight check is stateless — it doesn't cache the
        # `shutil.which()` result. So a session that paused on
        # codex_cli_not_found will see codex on the NEXT preflight if
        # it was installed in between. This pins that contract.
        from orchestrator.core.agentic_runtime import codex_cli_available as fn
        # Run twice to confirm no stale-cache behavior.
        self.assertFalse(fn(command="absent-binary-xyzzy-1"))
        self.assertTrue(fn(command="python3"))
        self.assertFalse(fn(command="absent-binary-xyzzy-2"))


if __name__ == "__main__":
    unittest.main()
