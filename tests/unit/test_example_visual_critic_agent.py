from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path
import zlib

from orchestrator.agents.example_visual_critic import ExampleVisualCriticAgent


class ExampleVisualCriticAgentTests(unittest.TestCase):
    def test_visual_critic_writes_design_requirements_from_screenshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            project_path = Path(tmp)
            examples_dir = project_path / "docs/product/example-references"
            screenshots_dir = examples_dir / "screenshots"
            examples_dir.mkdir(parents=True)
            screenshots_dir.mkdir(parents=True)
            _write_png(screenshots_dir / "example.png", 24, 16)
            (examples_dir / "top-examples.json").write_text(
                json.dumps(
                    [
                        {
                            "source_id": "SEED-awwwards-portfolio",
                            "source_name": "Awwwards Portfolio Website Examples",
                            "title": "Personal Website - Designer",
                            "url": "https://www.awwwards.com/sites/personal-website-designer",
                            "score": 58,
                            "screenshots": [
                                {
                                    "viewport": "desktop",
                                    "path": "docs/product/example-references/screenshots/example.png",
                                    "status": "captured",
                                }
                            ],
                        },
                        {
                            "source_id": "SEED-webflow-portfolio",
                            "source_name": "Webflow Portfolio Templates",
                            "title": "UX Portfolio",
                            "url": "https://webflow.com/made-in-webflow/ux-portfolio",
                            "score": 36,
                            "screenshots": [],
                        },
                    ]
                ),
                encoding="utf-8",
            )

            result = ExampleVisualCriticAgent().run(project={"path": str(project_path)})

            self.assertEqual(result.example_count, 2)
            self.assertEqual(result.screenshot_backed, 1)
            self.assertEqual(result.image_analyzed, 1)
            self.assertTrue(result.report_path.exists())
            self.assertTrue(result.json_path.exists())
            report = result.report_path.read_text(encoding="utf-8")
            self.assertIn("Example Visual Critic", report)
            self.assertIn("first_viewport_signal", report)
            payload = json.loads(result.json_path.read_text(encoding="utf-8"))
            self.assertIn("minimalist-editorial", payload["axis_guidance"])
            self.assertEqual(payload["image_quality"]["analyzed_screenshots"], 1)
            self.assertGreater(payload["examples"][0]["pixel_quality_score"], 0)


def _write_png(path: Path, width: int, height: int) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend((20, 20, 20) if (x + y) % 5 == 0 else (230, 240, 250))
    png = bytearray(b"\x89PNG\r\n\x1a\n")
    png.extend(_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)))
    png.extend(_chunk(b"IDAT", zlib.compress(bytes(raw))))
    png.extend(_chunk(b"IEND", b""))
    path.write_bytes(bytes(png))


def _chunk(kind: bytes, data: bytes) -> bytes:
    import binascii

    crc = binascii.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


if __name__ == "__main__":
    unittest.main()
