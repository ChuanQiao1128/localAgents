from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_yaml(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        loaded = json.loads(text)
    except json.JSONDecodeError:
        loaded, index = _parse_block(_prepare_lines(text), 0, 0)
        if index != len(_prepare_lines(text)):
            raise ValueError(f"Unable to parse all YAML content in {path}")
    if not isinstance(loaded, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return loaded


def _prepare_lines(text: str) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        lines.append(raw.rstrip())
    return lines


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _parse_block(lines: list[str], index: int, indent: int) -> tuple[Any, int]:
    if index >= len(lines):
        return {}, index
    if _indent(lines[index]) < indent:
        return {}, index
    if lines[index].lstrip().startswith("- "):
        return _parse_list(lines, index, indent)
    return _parse_dict(lines, index, indent)


def _parse_dict(lines: list[str], index: int, indent: int) -> tuple[dict[str, Any], int]:
    data: dict[str, Any] = {}
    while index < len(lines):
        line = lines[index]
        current_indent = _indent(line)
        if current_indent < indent:
            break
        if current_indent > indent:
            break
        stripped = line.strip()
        if stripped.startswith("- "):
            break
        if ":" not in stripped:
            raise ValueError(f"Invalid YAML mapping line: {line}")
        key, raw_value = stripped.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value in {">", "|"}:
            value_lines: list[str] = []
            index += 1
            while index < len(lines) and _indent(lines[index]) > current_indent:
                cut = min(len(lines[index]), current_indent + 2)
                value_lines.append(lines[index][cut:])
                index += 1
            data[key] = "\n".join(value_lines).strip()
        elif raw_value:
            data[key] = _parse_scalar(raw_value)
            index += 1
        else:
            if index + 1 < len(lines) and _indent(lines[index + 1]) > current_indent:
                data[key], index = _parse_block(lines, index + 1, _indent(lines[index + 1]))
            else:
                data[key] = {}
                index += 1
    return data, index


def _parse_list(lines: list[str], index: int, indent: int) -> tuple[list[Any], int]:
    data: list[Any] = []
    while index < len(lines):
        line = lines[index]
        current_indent = _indent(line)
        if current_indent < indent:
            break
        if current_indent != indent or not line.lstrip().startswith("- "):
            break
        rest = line.strip()[2:].strip()
        if not rest:
            value, index = _parse_block(lines, index + 1, indent + 2)
            data.append(value)
            continue
        if _looks_like_mapping(rest):
            item: dict[str, Any] = {}
            key, raw_value = rest.split(":", 1)
            key = key.strip()
            raw_value = raw_value.strip()
            if raw_value:
                item[key] = _parse_scalar(raw_value)
                index += 1
            else:
                item[key], index = _parse_block(lines, index + 1, indent + 2)
            while index < len(lines) and _indent(lines[index]) > indent:
                child, index = _parse_block(lines, index, _indent(lines[index]))
                if not isinstance(child, dict):
                    raise ValueError(f"Expected mapping continuation for list item: {line}")
                item.update(child)
            data.append(item)
        else:
            data.append(_parse_scalar(rest))
            index += 1
    return data, index


def _looks_like_mapping(value: str) -> bool:
    if ":" not in value:
        return False
    first = value.split(":", 1)[0].strip()
    return bool(first) and " " not in first


def _parse_scalar(value: str) -> Any:
    if value in {"true", "True"}:
        return True
    if value in {"false", "False"}:
        return False
    if value in {"null", "None", "~"}:
        return None
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(part.strip()) for part in inner.split(",")]
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value

