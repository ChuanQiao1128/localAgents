from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from orchestrator.pipeline_continue import (
    build_parser,
    _parse_codex_verdict,
    _parse_next_command,
    _parse_step_status,
    _read_codex_decision,
    _read_product_review,
    _redact,
    _visual_review_ready,
)


class PipelineContinueTests(unittest.TestCase):
    def test_parse_step_status_uses_last_status_line(self) -> None:
        stdout = "Generated implementation draft.\nStatus: completed\nAgent: qa\nStatus: failed\n"

        self.assertEqual(_parse_step_status(stdout, 0), "failed")
        self.assertEqual(_parse_step_status(stdout, 2), "failed")

    def test_redacts_known_secret_shapes(self) -> None:
        text = (
            "https://demo.example?__v0_token=abc.def "
            "export TAVILY_API_KEY=tvly-dev-secret "
            "export V0_API_KEY=v1:abc:def"
        )

        redacted = _redact(text)

        self.assertIn("__v0_token=<redacted>", redacted)
        self.assertIn("tvly-<redacted>", redacted)
        self.assertIn("v1:<redacted>", redacted)
        self.assertNotIn("tvly-dev-secret", redacted)

    def test_visual_review_ready_requires_completed_winner(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design_dir = root / "docs/design"
            design_dir.mkdir(parents=True)
            (design_dir / "selected-visual-direction.md").write_text("Winner: `dense-dashboard`", encoding="utf-8")
            (design_dir / "visual-direction-multimodal-review.json").write_text(
                json.dumps({"status": "completed", "winner_id": "dense-dashboard"}),
                encoding="utf-8",
            )

            self.assertTrue(_visual_review_ready(root))

    def test_read_product_review_score(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            product_dir = root / "docs/product"
            product_dir.mkdir(parents=True)
            (product_dir / "post-build-product-review.json").write_text(
                json.dumps({"status": "pass", "final_score": 93, "max_score": 100}),
                encoding="utf-8",
            )

            self.assertEqual(_read_product_review(root), ("pass", "93/100"))

    def test_parse_codex_verdict_and_next_command(self) -> None:
        text = """## Verdict

`continue`

## What Happened

ok

## Next Command

```bash
./scripts/continue_pipeline.py --project project_123 --skip-visual
```

## Risks
"""

        self.assertEqual(_parse_codex_verdict(text), "continue")
        self.assertEqual(_parse_next_command(text), "./scripts/continue_pipeline.py --project project_123 --skip-visual")

    def test_read_codex_decision_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "summary.md"
            path.write_text("## Verdict\n\nstop_fix_required\n\n## Next Command\n\nfix QA\n", encoding="utf-8")

            self.assertEqual(_read_codex_decision(path), ("stop_fix_required", "fix QA"))

    def test_parse_codex_verdict_unknown_when_missing(self) -> None:
        self.assertEqual(_parse_codex_verdict("## Nothing\n\ncontinue maybe later"), "continue")
        self.assertEqual(_parse_codex_verdict("no decision"), "unknown")

    def test_parser_supports_hardening_stage(self) -> None:
        args = build_parser().parse_args(["--project", "project_123", "--skip-visual", "--run-hardening"])

        self.assertTrue(args.run_hardening)
        self.assertEqual(args.hardening_target, "backend-api")


if __name__ == "__main__":
    unittest.main()
