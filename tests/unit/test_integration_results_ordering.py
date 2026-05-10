"""RC-2E.1: pin the read_integration_results oldest-first contract.

Audit Code Risks #4: the renderer + the controller's "last integration
pass?" probe both rely on `read_integration_results(...)[-1]` returning
the MOST RECENT result. The producer appends to JSONL so on-disk order
is chronological; this test pins that the reader preserves it across
multiple writes.
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from orchestrator.core.autonomous import (
    read_integration_results, record_integration_result,
)


class IntegrationResultsOrderingTests(unittest.TestCase):
    def test_three_writes_are_returned_oldest_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            session_id = "session_x"
            for i in range(3):
                record_integration_result(project_path, session_id, {
                    "schema_version": 1,
                    "passed": (i % 2 == 0),
                    "started_at": f"2026-05-10T05:0{i}:00+00:00",
                    "trigger_reason": f"trigger_{i}",
                    "duration_sec": 0.1 * i,
                    "commands_run": [],
                    "failed_required_command_names": [],
                })
            results = read_integration_results(project_path, session_id)
            self.assertEqual(len(results), 3)
            triggers = [r["trigger_reason"] for r in results]
            self.assertEqual(triggers, ["trigger_0", "trigger_1", "trigger_2"])
            # The renderer's [-1] must give the latest write.
            self.assertEqual(results[-1]["trigger_reason"], "trigger_2")

    def test_corrupt_line_is_skipped_without_breaking_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            session_id = "session_x"
            record_integration_result(project_path, session_id, {"passed": True, "trigger_reason": "first"})
            # Append a corrupt line by hand.
            from orchestrator.core.autonomous import integration_results_file
            log = integration_results_file(project_path, session_id)
            with log.open("a", encoding="utf-8") as fh:
                fh.write("{not valid json\n")
            record_integration_result(project_path, session_id, {"passed": False, "trigger_reason": "third"})
            results = read_integration_results(project_path, session_id)
            self.assertEqual(len(results), 2)
            self.assertEqual([r["trigger_reason"] for r in results], ["first", "third"])

    def test_missing_file_returns_empty_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            results = read_integration_results(Path(tmp), "session_never_existed")
            self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
