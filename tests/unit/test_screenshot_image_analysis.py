from __future__ import annotations

import struct
import tempfile
import unittest
from pathlib import Path
import zlib

from orchestrator.agents.screenshot_image_analysis import analyze_screenshot


class ScreenshotImageAnalysisTests(unittest.TestCase):
    def test_analyze_screenshot_flags_blank_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "blank.png"
            _write_png(path, 12, 12, lambda _x, _y: (255, 255, 255))

            result = analyze_screenshot(path)

            self.assertEqual(result["status"], "analyzed")
            self.assertIn("blank_or_failed_capture", result["flags"])
            self.assertLess(result["score"], 55)

    def test_analyze_screenshot_scores_content_png(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "content.png"
            _write_png(path, 24, 16, lambda x, y: (20, 20, 20) if (x + y) % 5 == 0 else (230, 240, 250))

            result = analyze_screenshot(path)

            self.assertEqual(result["status"], "analyzed")
            self.assertEqual(result["width"], 24)
            self.assertEqual(result["height"], 16)
            self.assertGreater(result["metrics"]["contrast_range_p95_p05"], 100)


def _write_png(path: Path, width: int, height: int, color_at) -> None:
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            raw.extend(color_at(x, y))
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
