from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
import struct
from typing import Any
import zlib


@dataclass(frozen=True)
class PngImage:
    width: int
    height: int
    pixels: list[tuple[int, int, int, int]]


def analyze_screenshot(path: Path) -> dict[str, Any]:
    try:
        image = _read_png(path)
    except Exception as exc:
        return {
            "path": str(path),
            "status": "unreadable",
            "error": str(exc),
            "score": 0,
            "flags": ["unreadable_png"],
        }
    metrics = _image_metrics(image)
    flags = _quality_flags(metrics)
    score = _quality_score(metrics, flags)
    return {
        "path": str(path),
        "status": "analyzed",
        "width": image.width,
        "height": image.height,
        "score": score,
        "flags": flags,
        "metrics": metrics,
        "summary": _summary(metrics, flags),
    }


def _read_png(path: Path) -> PngImage:
    data = path.read_bytes()
    if not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("not a PNG file")
    offset = 8
    width = 0
    height = 0
    bit_depth = 0
    color_type = 0
    idat = bytearray()
    palette: list[tuple[int, int, int]] = []
    while offset < len(data):
        if offset + 8 > len(data):
            raise ValueError("truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        chunk_data = data[offset + 8 : offset + 8 + length]
        offset += 12 + length
        if chunk_type == b"IHDR":
            width, height, bit_depth, color_type = struct.unpack(">IIBB", chunk_data[:10])
        elif chunk_type == b"PLTE":
            palette = [
                (chunk_data[index], chunk_data[index + 1], chunk_data[index + 2])
                for index in range(0, len(chunk_data) - 2, 3)
            ]
        elif chunk_type == b"IDAT":
            idat.extend(chunk_data)
        elif chunk_type == b"IEND":
            break
    if not width or not height or not idat:
        raise ValueError("missing PNG image data")
    if bit_depth != 8:
        raise ValueError(f"unsupported PNG bit depth {bit_depth}")
    channels = _channels(color_type)
    raw = zlib.decompress(bytes(idat))
    scanline_bytes = width * channels
    rows = _unfilter_rows(raw, width, height, channels, scanline_bytes)
    pixels = _rows_to_pixels(rows, width, color_type, palette)
    return PngImage(width=width, height=height, pixels=pixels)


def _channels(color_type: int) -> int:
    if color_type == 0:
        return 1
    if color_type == 2:
        return 3
    if color_type == 3:
        return 1
    if color_type == 4:
        return 2
    if color_type == 6:
        return 4
    raise ValueError(f"unsupported PNG color type {color_type}")


def _unfilter_rows(raw: bytes, width: int, height: int, channels: int, scanline_bytes: int) -> list[bytes]:
    rows: list[bytes] = []
    offset = 0
    previous = bytearray(scanline_bytes)
    for _row in range(height):
        if offset >= len(raw):
            raise ValueError("truncated PNG scanline")
        filter_type = raw[offset]
        offset += 1
        current = bytearray(raw[offset : offset + scanline_bytes])
        offset += scanline_bytes
        if len(current) != scanline_bytes:
            raise ValueError("truncated PNG row")
        _unfilter_row(current, previous, channels, filter_type)
        rows.append(bytes(current))
        previous = current
    return rows


def _unfilter_row(current: bytearray, previous: bytearray, bpp: int, filter_type: int) -> None:
    if filter_type == 0:
        return
    if filter_type == 1:
        for index in range(len(current)):
            left = current[index - bpp] if index >= bpp else 0
            current[index] = (current[index] + left) & 0xFF
        return
    if filter_type == 2:
        for index in range(len(current)):
            current[index] = (current[index] + previous[index]) & 0xFF
        return
    if filter_type == 3:
        for index in range(len(current)):
            left = current[index - bpp] if index >= bpp else 0
            up = previous[index]
            current[index] = (current[index] + ((left + up) // 2)) & 0xFF
        return
    if filter_type == 4:
        for index in range(len(current)):
            left = current[index - bpp] if index >= bpp else 0
            up = previous[index]
            upper_left = previous[index - bpp] if index >= bpp else 0
            current[index] = (current[index] + _paeth(left, up, upper_left)) & 0xFF
        return
    raise ValueError(f"unsupported PNG filter {filter_type}")


def _paeth(left: int, up: int, upper_left: int) -> int:
    estimate = left + up - upper_left
    left_distance = abs(estimate - left)
    up_distance = abs(estimate - up)
    upper_left_distance = abs(estimate - upper_left)
    if left_distance <= up_distance and left_distance <= upper_left_distance:
        return left
    if up_distance <= upper_left_distance:
        return up
    return upper_left


def _rows_to_pixels(
    rows: list[bytes],
    width: int,
    color_type: int,
    palette: list[tuple[int, int, int]],
) -> list[tuple[int, int, int, int]]:
    pixels: list[tuple[int, int, int, int]] = []
    for row in rows:
        for column in range(width):
            if color_type == 0:
                value = row[column]
                pixels.append((value, value, value, 255))
            elif color_type == 2:
                index = column * 3
                pixels.append((row[index], row[index + 1], row[index + 2], 255))
            elif color_type == 3:
                palette_index = row[column]
                red, green, blue = palette[palette_index] if palette_index < len(palette) else (0, 0, 0)
                pixels.append((red, green, blue, 255))
            elif color_type == 4:
                index = column * 2
                value = row[index]
                pixels.append((value, value, value, row[index + 1]))
            elif color_type == 6:
                index = column * 4
                pixels.append((row[index], row[index + 1], row[index + 2], row[index + 3]))
    return pixels


def _image_metrics(image: PngImage) -> dict[str, Any]:
    sample = _sample_pixels(image, max_samples=12_000)
    luminance = [_luminance(red, green, blue) for red, green, blue, _alpha in sample]
    saturation = [_saturation(red, green, blue) for red, green, blue, _alpha in sample]
    colors = {(red // 8, green // 8, blue // 8) for red, green, blue, _alpha in sample}
    mean_luma = sum(luminance) / len(luminance)
    luma_stdev = _stdev(luminance, mean_luma)
    p05 = _percentile(luminance, 0.05)
    p95 = _percentile(luminance, 0.95)
    edge_density = _edge_density(image)
    content_ratio = _content_ratio(image)
    top_content_ratio = _band_content_ratio(image, start=0.0, end=0.28)
    bottom_content_ratio = _band_content_ratio(image, start=0.72, end=1.0)
    dark_ratio = sum(1 for value in luminance if value < 54) / len(luminance)
    light_ratio = sum(1 for value in luminance if value > 220) / len(luminance)
    return {
        "aspect_ratio": round(image.width / image.height, 3),
        "sampled_pixels": len(sample),
        "unique_color_buckets": len(colors),
        "mean_luminance": round(mean_luma, 2),
        "luminance_stdev": round(luma_stdev, 2),
        "contrast_range_p95_p05": round(p95 - p05, 2),
        "mean_saturation": round(sum(saturation) / len(saturation), 3),
        "edge_density": round(edge_density, 4),
        "content_ratio": round(content_ratio, 4),
        "top_content_ratio": round(top_content_ratio, 4),
        "bottom_content_ratio": round(bottom_content_ratio, 4),
        "dark_pixel_ratio": round(dark_ratio, 4),
        "light_pixel_ratio": round(light_ratio, 4),
    }


def _sample_pixels(image: PngImage, *, max_samples: int) -> list[tuple[int, int, int, int]]:
    total = len(image.pixels)
    step = max(1, total // max_samples)
    return image.pixels[::step]


def _luminance(red: int, green: int, blue: int) -> float:
    return 0.2126 * red + 0.7152 * green + 0.0722 * blue


def _saturation(red: int, green: int, blue: int) -> float:
    high = max(red, green, blue)
    low = min(red, green, blue)
    return 0.0 if high == 0 else (high - low) / high


def _stdev(values: list[float], mean: float) -> float:
    return math.sqrt(sum((value - mean) ** 2 for value in values) / len(values))


def _percentile(values: list[float], ratio: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int((len(ordered) - 1) * ratio)))
    return ordered[index]


def _edge_density(image: PngImage) -> float:
    step_x = max(1, image.width // 160)
    step_y = max(1, image.height // 120)
    comparisons = 0
    edges = 0
    for y in range(0, image.height - step_y, step_y):
        for x in range(0, image.width - step_x, step_x):
            current = _pixel_luminance(image, x, y)
            right = _pixel_luminance(image, x + step_x, y)
            down = _pixel_luminance(image, x, y + step_y)
            comparisons += 2
            if abs(current - right) > 22:
                edges += 1
            if abs(current - down) > 22:
                edges += 1
    return edges / comparisons if comparisons else 0.0


def _content_ratio(image: PngImage) -> float:
    background = _corner_background(image)
    sample = _sample_pixels(image, max_samples=12_000)
    return sum(1 for pixel in sample if _color_distance(pixel, background) > 32) / len(sample)


def _band_content_ratio(image: PngImage, *, start: float, end: float) -> float:
    y0 = int(image.height * start)
    y1 = max(y0 + 1, int(image.height * end))
    background = _corner_background(image)
    total = 0
    content = 0
    step_x = max(1, image.width // 160)
    step_y = max(1, max(1, y1 - y0) // 50)
    for y in range(y0, min(image.height, y1), step_y):
        for x in range(0, image.width, step_x):
            total += 1
            if _color_distance(_pixel(image, x, y), background) > 32:
                content += 1
    return content / total if total else 0.0


def _corner_background(image: PngImage) -> tuple[int, int, int, int]:
    points = [
        _pixel(image, 0, 0),
        _pixel(image, image.width - 1, 0),
        _pixel(image, 0, image.height - 1),
        _pixel(image, image.width - 1, image.height - 1),
    ]
    return tuple(int(sum(pixel[index] for pixel in points) / len(points)) for index in range(4))  # type: ignore[return-value]


def _pixel(image: PngImage, x: int, y: int) -> tuple[int, int, int, int]:
    return image.pixels[y * image.width + x]


def _pixel_luminance(image: PngImage, x: int, y: int) -> float:
    red, green, blue, _alpha = _pixel(image, x, y)
    return _luminance(red, green, blue)


def _color_distance(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2 + (a[2] - b[2]) ** 2)


def _quality_flags(metrics: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if int(metrics["unique_color_buckets"]) < 8 or float(metrics["luminance_stdev"]) < 4:
        flags.append("blank_or_failed_capture")
    if float(metrics["contrast_range_p95_p05"]) < 35:
        flags.append("low_contrast")
    if float(metrics["top_content_ratio"]) < 0.035 and float(metrics["edge_density"]) < 0.018:
        flags.append("weak_first_viewport_signal")
    if float(metrics["edge_density"]) > 0.24:
        flags.append("visually_busy")
    if float(metrics["content_ratio"]) < 0.04:
        flags.append("sparse_or_loading_screen")
    if float(metrics["mean_luminance"]) < 22 or float(metrics["mean_luminance"]) > 245:
        flags.append("extreme_brightness")
    return flags


def _quality_score(metrics: dict[str, Any], flags: list[str]) -> int:
    score = 55
    score += min(18, int(float(metrics["contrast_range_p95_p05"]) / 8))
    score += min(12, int(float(metrics["unique_color_buckets"]) / 18))
    score += min(10, int(float(metrics["edge_density"]) * 85))
    score += min(10, int(float(metrics["content_ratio"]) * 28))
    if 0.04 <= float(metrics["top_content_ratio"]) <= 0.78:
        score += 8
    if "blank_or_failed_capture" in flags:
        score -= 45
    if "low_contrast" in flags:
        score -= 15
    if "weak_first_viewport_signal" in flags:
        score -= 10
    if "sparse_or_loading_screen" in flags:
        score -= 12
    if "visually_busy" in flags:
        score -= 6
    return max(0, min(100, score))


def _summary(metrics: dict[str, Any], flags: list[str]) -> str:
    if "blank_or_failed_capture" in flags:
        return "Image appears blank or failed; do not treat this screenshot as strong visual evidence."
    parts = [
        f"contrast {metrics['contrast_range_p95_p05']}",
        f"edge density {metrics['edge_density']}",
        f"content ratio {metrics['content_ratio']}",
        f"top content {metrics['top_content_ratio']}",
    ]
    if flags:
        parts.append("flags: " + ", ".join(flags))
    else:
        parts.append("no major pixel-quality flags")
    return "; ".join(parts)
