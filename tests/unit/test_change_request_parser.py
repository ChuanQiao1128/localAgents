"""RC-4A.1: tests for orchestrator.core.change_request_parser."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from orchestrator.core.change_request_parser import (
    ChangeRequest,
    ChangeRequestParseError,
    parse_change_request_file,
    parse_change_request_text,
)


class ChangeRequestParserTests(unittest.TestCase):
    def test_parses_explicit_goal_scope_nongoals_acceptance_sections(self) -> None:
        text = (
            "# Add side-by-side diff view\n"
            "\n"
            "## Goal\n"
            "Add a side-by-side diff between original and rewritten text on the home page.\n"
            "\n"
            "## Scope\n"
            "- app/page.tsx\n"
            "- components/**\n"
            "\n"
            "## Non-goals\n"
            "- Do not change the rewrite API surface.\n"
            "- Do not add new dependencies.\n"
            "\n"
            "## Acceptance\n"
            "- Original text appears on the left.\n"
            "- Rewritten text appears on the right.\n"
            "- npm run build passes.\n"
        )

        parsed = parse_change_request_text(text)

        self.assertIn("side-by-side diff", parsed.goal)
        self.assertEqual(parsed.scope_paths, ["app/page.tsx", "components/**"])
        self.assertEqual(parsed.non_goals, [
            "Do not change the rewrite API surface.",
            "Do not add new dependencies.",
        ])
        self.assertEqual(parsed.acceptance, [
            "Original text appears on the left.",
            "Rewritten text appears on the right.",
            "npm run build passes.",
        ])
        self.assertFalse(parsed.scope_missing)

    def test_falls_back_to_first_paragraph_when_no_explicit_goal_section(self) -> None:
        text = (
            "# Some Title\n"
            "\n"
            "First paragraph describes the goal in two\n"
            "wrapped lines.\n"
            "\n"
            "## Acceptance\n"
            "- Build passes.\n"
        )
        parsed = parse_change_request_text(text)
        self.assertEqual(
            parsed.goal,
            "First paragraph describes the goal in two wrapped lines.",
        )
        self.assertEqual(parsed.acceptance, ["Build passes."])

    def test_inline_scope_lines_accumulate(self) -> None:
        text = (
            "## Goal\n"
            "Refactor.\n"
            "\n"
            "Scope: app/page.tsx, components/Editor.tsx\n"
            "\n"
            "## Acceptance\n"
            "- Build passes.\n"
        )
        parsed = parse_change_request_text(text)
        self.assertIn("app/page.tsx", parsed.scope_paths)
        self.assertIn("components/Editor.tsx", parsed.scope_paths)

    def test_scope_missing_flag_when_no_scope_declared(self) -> None:
        text = (
            "## Goal\n"
            "Add a footer.\n"
            "\n"
            "## Acceptance\n"
            "- Footer is present on every page.\n"
        )
        parsed = parse_change_request_text(text)
        self.assertEqual(parsed.scope_paths, [])
        self.assertTrue(parsed.scope_missing)

    def test_raises_on_missing_acceptance(self) -> None:
        text = (
            "## Goal\n"
            "Add a footer.\n"
            "\n"
            "## Scope\n"
            "- app/page.tsx\n"
        )
        with self.assertRaises(ChangeRequestParseError) as ctx:
            parse_change_request_text(text)
        self.assertIn("acceptance", str(ctx.exception).lower())

    def test_raises_on_empty_input(self) -> None:
        with self.assertRaises(ChangeRequestParseError):
            parse_change_request_text("")
        with self.assertRaises(ChangeRequestParseError):
            parse_change_request_text("   \n\n  \n")

    def test_raises_on_no_goal(self) -> None:
        # Only headings + acceptance section, no body content for goal.
        text = (
            "# Title\n"
            "\n"
            "## Acceptance\n"
            "- Something.\n"
        )
        with self.assertRaises(ChangeRequestParseError) as ctx:
            parse_change_request_text(text)
        self.assertIn("goal", str(ctx.exception).lower())

    def test_dedupe_preserves_order(self) -> None:
        text = (
            "## Goal\n"
            "Refactor.\n"
            "\n"
            "## Scope\n"
            "- app/page.tsx\n"
            "- components/**\n"
            "- app/page.tsx\n"
            "\n"
            "## Acceptance\n"
            "- Build passes.\n"
            "- Build passes.\n"
        )
        parsed = parse_change_request_text(text)
        self.assertEqual(parsed.scope_paths, ["app/page.tsx", "components/**"])
        self.assertEqual(parsed.acceptance, ["Build passes."])

    def test_parse_change_request_file_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "change-request.md"
            path.write_text(
                "## Goal\nDo X.\n\n## Acceptance\n- Y.\n",
                encoding="utf-8",
            )
            parsed = parse_change_request_file(path)
            self.assertEqual(parsed.goal, "Do X.")
            self.assertEqual(parsed.acceptance, ["Y."])

    def test_parse_change_request_file_missing_path(self) -> None:
        with self.assertRaises(ChangeRequestParseError):
            parse_change_request_file("/nonexistent/path/change-request.md")

    # ----- RC-5A.13: parser hardening -----------------------------------

    def test_parses_scope_paths_heading(self) -> None:
        """`## Scope paths` (the heading variant the dogfood test used)
        must produce the same `scope_paths` as the historical `## Scope`."""
        text = (
            "## Goal\n"
            "Add the thing.\n"
            "\n"
            "## Scope paths\n"
            "- app/**\n"
            "- components/**\n"
            "- lib/**\n"
            "\n"
            "## Acceptance\n"
            "- Build passes.\n"
        )
        parsed = parse_change_request_text(text)
        self.assertEqual(
            parsed.scope_paths,
            ["app/**", "components/**", "lib/**"],
        )
        self.assertFalse(parsed.scope_missing)

    def test_parses_files_to_change_heading(self) -> None:
        """`## Files to change` (product-doc convention) maps to scope_paths."""
        text = (
            "## Goal\n"
            "Refactor footer.\n"
            "\n"
            "## Files to change\n"
            "- app/footer.tsx\n"
            "- components/Footer.tsx\n"
            "\n"
            "## Acceptance\n"
            "- Build passes.\n"
        )
        parsed = parse_change_request_text(text)
        self.assertEqual(
            parsed.scope_paths,
            ["app/footer.tsx", "components/Footer.tsx"],
        )
        self.assertFalse(parsed.scope_missing)

    def test_strips_wrapping_backticks_from_scope(self) -> None:
        """`` - `app/**` `` becomes `app/**` — RC-4C.1 found that
        autonomous parser captured backticks literally, so we apply the
        same defensive cleanup at the change parser layer."""
        text = (
            "## Goal\n"
            "Tweak the page.\n"
            "\n"
            "## Scope paths\n"
            "- `app/**`\n"
            "- `components/Editor.tsx`\n"
            "\n"
            "## Acceptance\n"
            "- Build passes.\n"
        )
        parsed = parse_change_request_text(text)
        self.assertEqual(
            parsed.scope_paths,
            ["app/**", "components/Editor.tsx"],
        )

    def test_inline_scope_paths_alias(self) -> None:
        """Inline `Scope paths: a, b` works just like `Scope: a, b`."""
        text = (
            "## Goal\n"
            "Refactor.\n"
            "\n"
            "Scope paths: app/page.tsx, components/Editor.tsx\n"
            "\n"
            "## Acceptance\n"
            "- Build passes.\n"
        )
        parsed = parse_change_request_text(text)
        self.assertIn("app/page.tsx", parsed.scope_paths)
        self.assertIn("components/Editor.tsx", parsed.scope_paths)

    def test_inline_files_to_change_alias(self) -> None:
        text = (
            "## Goal\nRefactor.\n\n"
            "Files to change: a, b, c\n\n"
            "## Acceptance\n- Build passes.\n"
        )
        parsed = parse_change_request_text(text)
        self.assertEqual(parsed.scope_paths, ["a", "b", "c"])

    def test_acceptance_criteria_heading_alias(self) -> None:
        """`## Acceptance criteria` should work just like `## Acceptance`."""
        text = (
            "## Goal\nRefactor.\n\n"
            "## Scope paths\n- app/**\n\n"
            "## Acceptance criteria\n- Build passes.\n- Typecheck passes.\n"
        )
        parsed = parse_change_request_text(text)
        self.assertEqual(
            parsed.acceptance,
            ["Build passes.", "Typecheck passes."],
        )


if __name__ == "__main__":
    unittest.main()
