"""RC-4A.1: end-to-end CLI test for `agent-studio change` subcommands.

No Codex. No Vercel. No autonomous run. Confirms that the change-mode
foundation (parser → repo onboarding → contract → CLI) hangs together
end-to-end through the real argparse + handler stack.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[2]


def _cli(root: Path, *args: str, expect_success: bool = True) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "LOCALAGENTS_FORCE_STUB": "1", "LOCALAGENTS_QUIET": "1"}
    completed = subprocess.run(
        [sys.executable, "-m", "orchestrator.cli", "--root", str(root), *args],
        cwd=_REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
        env=env,
    )
    if expect_success and completed.returncode != 0:
        raise AssertionError(
            f"CLI exited {completed.returncode} for args {args!r}\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    return completed


_VALID_CHANGE_REQUEST = (
    "## Goal\n"
    "Add a side-by-side diff view between original and rewritten text on the home page.\n"
    "\n"
    "## Scope\n"
    "- app/page.tsx\n"
    "- components/**\n"
    "\n"
    "## Non-goals\n"
    "- Do not change the rewrite API.\n"
    "\n"
    "## Acceptance\n"
    "- Original on the left, rewritten on the right.\n"
    "- npm run build passes.\n"
)


class ChangeCliFlowTests(unittest.TestCase):
    def test_change_new_show_status_validate_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            # 1. Init workspace + create a fresh project.
            _cli(root, "init")
            new = _cli(root, "new", "Demo project for change-mode")
            self.assertIn("Created project", new.stdout)

            # 2. Locate the project dir on disk so we can drop a fake repo state.
            project_dir = _project_path_from_status(_cli(root, "status").stdout, root)
            self.assertTrue(project_dir.exists())
            (project_dir / "package.json").write_text(json.dumps({
                "name": "demo",
                "scripts": {"build": "next build", "test": "jest"},
                "dependencies": {"next": "15.5.18", "react": "19.0.0"},
            }), encoding="utf-8")
            (project_dir / "app").mkdir(exist_ok=True)
            (project_dir / "components").mkdir(exist_ok=True)

            # 3. Write a change-request.md outside the project dir to mirror real usage.
            cr_path = root / "change-request.md"
            cr_path.write_text(_VALID_CHANGE_REQUEST, encoding="utf-8")

            # 4. agent-studio change new --from <path> (JSON for stable parsing)
            change_new = _cli(root, "change", "new", "--from", str(cr_path), "--json")
            new_json = json.loads(change_new.stdout)
            change_id = new_json["change_id"]
            self.assertTrue(change_id.startswith("change_"))
            self.assertTrue(Path(new_json["change_dir"]).exists())
            for key in (
                "change_request_md",
                "change_contract_json",
                "repo_onboarding_md",
                "implementation_plan_md",
                "acceptance_criteria_json",
            ):
                self.assertTrue(Path(new_json["artifacts"][key]).exists(), msg=f"{key} missing")

            # 5. agent-studio change list shows the new change
            list_out = _cli(root, "change", "list", "--json")
            list_rows = json.loads(list_out.stdout)
            self.assertEqual(len(list_rows), 1)
            self.assertEqual(list_rows[0]["change_id"], change_id)

            # 6. agent-studio change show latest
            show_out = _cli(root, "change", "show", "--json")  # default 'latest'
            show_payload = json.loads(show_out.stdout)
            self.assertEqual(show_payload["summary"]["change_id"], change_id)
            self.assertEqual(show_payload["summary"]["state"], "ready_for_run")
            self.assertIn("side-by-side diff", show_payload["contract"]["goal"])

            # 7. agent-studio change status latest
            status_out = _cli(root, "change", "status", "--json")
            status_payload = json.loads(status_out.stdout)
            self.assertEqual(status_payload["state"], "ready_for_run")
            self.assertGreaterEqual(status_payload["acceptance_count"], 1)

            # 8. agent-studio change validate latest -> OK (delivery-report.md
            # and applied-change.json are not present yet — must NOT be required).
            validate_out = _cli(root, "change", "validate", "--json")
            validate_payload = json.loads(validate_out.stdout)
            self.assertTrue(validate_payload["ok"], msg=validate_out.stdout)
            self.assertEqual(validate_payload["report"]["change-contract.json"], [])
            # RC-4A.3.1.C: ready_for_run change without applied-change.json
            # must still validate (the file is optional pre-run; only required
            # on a `delivered` state).
            self.assertNotIn("applied-change.json", validate_payload["report"])
            self.assertNotIn("delivery-report.md", validate_payload["report"])

            # 9. agent-studio change run -> RC-4A.2 wired; the synthetic project
            # here is NOT a git repo (the test creates a bare project dir + a
            # package.json), so `change run` must fail cleanly with the git
            # preflight error. This proves the handler is live AND that it
            # refuses to run on a non-git project. The full happy-path e2e
            # lives in test_change_run_e2e.py with a real git init + fake
            # patch worker.
            run_out = _cli(root, "change", "run", expect_success=False)
            self.assertNotEqual(run_out.returncode, 0)
            combined = run_out.stdout + run_out.stderr
            self.assertIn("not a git repository", combined)

    def test_change_new_with_invalid_change_request_returns_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _cli(root, "init")
            _cli(root, "new", "Demo project")

            cr_path = root / "broken.md"
            cr_path.write_text("## Goal\nNo acceptance section here.\n", encoding="utf-8")

            result = _cli(root, "change", "new", "--from", str(cr_path), expect_success=False)
            self.assertNotEqual(result.returncode, 0)
            combined = (result.stdout + result.stderr).lower()
            self.assertIn("acceptance", combined)


def _project_path_from_status(stdout: str, root: Path) -> Path:
    """Best-effort scrape — projects live under <root>/.agent-studio/projects/<id>/"""
    base = root / ".agent-studio" / "projects"
    candidates = [p for p in base.iterdir() if p.is_dir()]
    assert candidates, f"no project dirs under {base}"
    return candidates[0]


if __name__ == "__main__":
    unittest.main()
