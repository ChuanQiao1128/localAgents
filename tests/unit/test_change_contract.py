"""RC-4A.1: tests for orchestrator.core.change_contract + artifact validators."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from orchestrator.core.artifact_validation import validate_change_contract
from orchestrator.core.change_contract import (
    CHANGE_CONTRACT_SCHEMA_VERSION,
    change_dir,
    change_status_summary,
    changes_root,
    create_change,
    latest_change_id,
    list_changes,
    read_change_contract,
    resolve_change_id,
)
from orchestrator.core.change_request_parser import ChangeRequestParseError


def _write_change_request(dir_: Path, *, name: str = "change-request.md", body: str | None = None) -> Path:
    body = body or (
        "## Goal\n"
        "Add a side-by-side diff view between original and rewritten text.\n"
        "\n"
        "## Scope\n"
        "- app/page.tsx\n"
        "- components/**\n"
        "\n"
        "## Non-goals\n"
        "- Do not change the rewrite API.\n"
        "\n"
        "## Acceptance\n"
        "- Diff appears side by side on `md` and up.\n"
        "- npm run build passes.\n"
    )
    path = dir_ / name
    path.write_text(body, encoding="utf-8")
    return path


class CreateChangeTests(unittest.TestCase):
    def test_writes_all_five_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "package.json").write_text(json.dumps({
                "name": "demo",
                "scripts": {"build": "next build", "test": "jest"},
                "dependencies": {"next": "15.5.18", "react": "19.0.0"},
            }), encoding="utf-8")
            change_request = _write_change_request(project)

            created = create_change(project, change_request, now=datetime(2026, 5, 12, tzinfo=timezone.utc))

            self.assertTrue(created.change_id.startswith("change_"))
            self.assertTrue(created.change_dir.exists())
            self.assertTrue(created.change_request_path.exists())
            self.assertTrue(created.change_contract_path.exists())
            self.assertTrue(created.repo_onboarding_path.exists())
            self.assertTrue(created.implementation_plan_path.exists())
            self.assertTrue(created.acceptance_criteria_path.exists())

            contract = json.loads(created.change_contract_path.read_text(encoding="utf-8"))
            self.assertEqual(contract["schema_version"], CHANGE_CONTRACT_SCHEMA_VERSION)
            self.assertEqual(contract["change_id"], created.change_id)
            self.assertIn("side-by-side diff", contract["goal"])
            self.assertEqual(contract["scope_paths"], ["app/page.tsx", "components/**"])
            self.assertFalse(contract["scope_missing"])
            self.assertGreaterEqual(len(contract["acceptance"]), 1)
            self.assertEqual(contract["created_at"], "2026-05-12T00:00:00+00:00")

            # acceptance-criteria.json shape parity with autonomous mode
            ac = json.loads(created.acceptance_criteria_path.read_text(encoding="utf-8"))
            self.assertEqual(ac["schema_version"], 1)
            self.assertEqual(len(ac["criteria"]), len(contract["acceptance"]))
            self.assertTrue(ac["criteria"][0]["id"].startswith("AC-"))

            # implementation-plan placeholder names RC-4A.2 explicitly
            plan = created.implementation_plan_path.read_text(encoding="utf-8")
            self.assertIn("RC-4A.2", plan)
            self.assertIn("ready_for_run", plan)

            # repo-onboarding picked up the package.json
            onboarding = created.repo_onboarding_path.read_text(encoding="utf-8")
            self.assertIn("Repo Onboarding", onboarding)
            self.assertIn("`build`: `next build`", onboarding)

    def test_raises_on_invalid_change_request(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            bad_path = project / "change-request.md"
            bad_path.write_text("## Goal\nNo acceptance.\n", encoding="utf-8")
            with self.assertRaises(ChangeRequestParseError):
                create_change(project, bad_path)
            # No change dir leaked
            self.assertFalse(changes_root(project).exists())

    def test_raises_when_change_request_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(FileNotFoundError):
                create_change(Path(tmp), Path(tmp) / "no-such.md")

    def test_raises_when_project_path_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cr = _write_change_request(Path(tmp))
            with self.assertRaises(FileNotFoundError):
                create_change(Path(tmp) / "nope", cr)


class LatestAndListTests(unittest.TestCase):
    def test_latest_change_id_returns_most_recent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cr = _write_change_request(project)
            first = create_change(project, cr, change_id="change_aaa", now=datetime(2026, 5, 12, 0, 0, 0, tzinfo=timezone.utc))
            second = create_change(project, cr, change_id="change_bbb", now=datetime(2026, 5, 12, 0, 1, 0, tzinfo=timezone.utc))
            self.assertEqual(latest_change_id(project), second.change_id)
            rows = list_changes(project)
            self.assertEqual([row["change_id"] for row in rows], ["change_aaa", "change_bbb"])

    def test_latest_change_id_returns_none_when_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(latest_change_id(Path(tmp)))
            self.assertEqual(list_changes(Path(tmp)), [])

    def test_resolve_change_id_handles_latest_and_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cr = _write_change_request(project)
            created = create_change(project, cr, change_id="change_xyz")
            self.assertEqual(resolve_change_id(project, "latest"), "change_xyz")
            self.assertEqual(resolve_change_id(project, None), "change_xyz")
            self.assertEqual(resolve_change_id(project, "change_xyz"), "change_xyz")
            with self.assertRaises(FileNotFoundError):
                resolve_change_id(project, "change_does_not_exist")


class StatusAndReadTests(unittest.TestCase):
    def test_status_summary_initial_state(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cr = _write_change_request(project)
            created = create_change(project, cr)
            summary = change_status_summary(project, created.change_id)
            self.assertEqual(summary["state"], "ready_for_run")
            self.assertEqual(summary["change_id"], created.change_id)
            self.assertGreaterEqual(summary["acceptance_count"], 1)
            self.assertEqual(summary["scope_missing"], False)
            self.assertIsNone(summary["artifacts"]["delivery_report_md"])
            self.assertIsNone(summary["artifacts"]["applied_change_json"])

    def test_status_state_progresses_when_artifacts_appear(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cr = _write_change_request(project)
            created = create_change(project, cr)
            (created.change_dir / "applied-change.json").write_text("{}", encoding="utf-8")
            self.assertEqual(change_status_summary(project, created.change_id)["state"], "applied")
            (created.change_dir / "delivery-report.md").write_text("# Change Delivery Report\n", encoding="utf-8")
            # RC-4C.1.E fix: `delivered` requires BOTH applied + delivery.
            self.assertEqual(change_status_summary(project, created.change_id)["state"], "delivered")

    def test_status_state_failed_when_delivery_without_applied(self) -> None:
        """RC-4C.1.E regression: pre-fix, a delivery-report.md from a
        FAILED change run (Promotion Gate blocked, no apply) was reported
        as state="delivered". That's actively wrong — nothing was delivered.
        After RC-4C.1.E, delivery-without-apply maps to the report's
        actual `## Result` token."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cr = _write_change_request(project)
            created = create_change(project, cr)
            # Render a minimal failed-result delivery report — no
            # applied-change.json present.
            (created.change_dir / "delivery-report.md").write_text(
                "# Change Delivery Report — change_x\n\n## Result\n\n**failed**\n",
                encoding="utf-8",
            )
            summary = change_status_summary(project, created.change_id)
            self.assertEqual(summary["state"], "failed")
            self.assertIsNone(summary["artifacts"]["applied_change_json"])
            self.assertIsNotNone(summary["artifacts"]["delivery_report_md"])

    def test_status_state_needs_human_review_when_delivery_without_applied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cr = _write_change_request(project)
            created = create_change(project, cr)
            (created.change_dir / "delivery-report.md").write_text(
                "# Change Delivery Report — change_x\n\n## Result\n\n**needs-human-review**\n",
                encoding="utf-8",
            )
            self.assertEqual(
                change_status_summary(project, created.change_id)["state"],
                "needs_human_review",
            )

    def test_status_state_inconsistent_completed_without_apply_treated_as_failed(self) -> None:
        """If a delivery-report says `completed` but applied-change.json is
        missing, that's an inconsistency. Treat as failed — never
        delivered — so a half-rendered report can't masquerade."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cr = _write_change_request(project)
            created = create_change(project, cr)
            (created.change_dir / "delivery-report.md").write_text(
                "# Change Delivery Report — change_x\n\n## Result\n\n**completed**\n",
                encoding="utf-8",
            )
            self.assertEqual(
                change_status_summary(project, created.change_id)["state"],
                "failed",
            )

    def test_status_state_unparseable_delivery_falls_back_to_failed(self) -> None:
        """A delivery-report.md with no recognizable Result token must
        fall back to `failed` — not `delivered`."""
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cr = _write_change_request(project)
            created = create_change(project, cr)
            (created.change_dir / "delivery-report.md").write_text(
                "# Change Delivery Report\n\n(garbled, no Result section)\n",
                encoding="utf-8",
            )
            self.assertEqual(
                change_status_summary(project, created.change_id)["state"],
                "failed",
            )

    def test_read_change_contract_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            cr = _write_change_request(project)
            created = create_change(project, cr)
            payload = read_change_contract(project, created.change_id)
            self.assertEqual(payload["change_id"], created.change_id)
            self.assertEqual(payload["schema_version"], CHANGE_CONTRACT_SCHEMA_VERSION)


class ValidateChangeContractTests(unittest.TestCase):
    def _baseline_payload(self) -> dict:
        return {
            "schema_version": CHANGE_CONTRACT_SCHEMA_VERSION,
            "change_id": "change_abc",
            "source_change_request_path": "/x/change-request.md",
            "goal": "Do something.",
            "scope_paths": ["app/page.tsx"],
            "scope_missing": False,
            "non_goals": [],
            "acceptance": ["Build passes."],
            "created_at": "2026-05-12T00:00:00+00:00",
        }

    def test_baseline_payload_is_valid(self) -> None:
        self.assertEqual(validate_change_contract(self._baseline_payload()), [])

    def test_wrong_schema_version_flagged(self) -> None:
        payload = self._baseline_payload()
        payload["schema_version"] = "wrong"
        errors = validate_change_contract(payload)
        self.assertTrue(any("schema_version" in e for e in errors))

    def test_missing_required_keys_flagged(self) -> None:
        payload = self._baseline_payload()
        del payload["goal"]
        errors = validate_change_contract(payload)
        self.assertTrue(any("goal" in e for e in errors))

    def test_empty_acceptance_flagged(self) -> None:
        payload = self._baseline_payload()
        payload["acceptance"] = []
        errors = validate_change_contract(payload)
        self.assertTrue(any("acceptance" in e for e in errors))

    def test_blank_goal_flagged(self) -> None:
        payload = self._baseline_payload()
        payload["goal"] = "   "
        errors = validate_change_contract(payload)
        self.assertTrue(any("goal" in e for e in errors))

    def test_non_dict_payload_returns_error(self) -> None:
        self.assertEqual(validate_change_contract([]), ["change-contract payload is not a dict"])


if __name__ == "__main__":
    unittest.main()
