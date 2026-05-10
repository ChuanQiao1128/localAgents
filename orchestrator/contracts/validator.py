"""Programmatic artifact validators.

Loads contracts from ``artifact_contracts.yaml``, picks the contract whose key
is a suffix-match for a given file path (longest match wins), and runs each
configured check. Each check contributes a pass/fail to a per-file score
0-100. The result drives:

  * artifact-level ``validation_status`` and ``validation_score`` columns
  * phase-level ``phase_score`` aggregation
  * delivery-grade computation in the autonomous run report

All checks are programmatic and free of LLM calls. Their job is to catch
structural skeletons, not semantic quality — that's a deliberate boundary.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str = ""


@dataclass(frozen=True)
class ValidationResult:
    path: str
    score: int  # 0-100
    status: str  # "passed" | "partial" | "failed" | "not_run"
    checks: list[CheckResult] = field(default_factory=list)
    critical: bool = False

    def failed_checks(self) -> list[CheckResult]:
        return [c for c in self.checks if not c.passed]


@dataclass
class ArtifactContract:
    path_pattern: str
    rules: dict[str, Any]


class Validator:
    """Runs a contract's rules against a file's content."""

    def __init__(self, contracts: list[ArtifactContract], default_rules: dict[str, Any] | None = None):
        # Sort longest-pattern first so more-specific contracts win.
        self.contracts = sorted(contracts, key=lambda c: -len(c.path_pattern))
        self.default_rules = default_rules or {}

    def select_rules(self, relative_path: str) -> dict[str, Any]:
        for contract in self.contracts:
            if relative_path.endswith(contract.path_pattern):
                return contract.rules
        return dict(self.default_rules)

    def validate(self, relative_path: str, content: str) -> ValidationResult:
        rules = self.select_rules(relative_path)
        critical = bool(rules.get("critical"))
        if not rules:
            return ValidationResult(path=relative_path, score=100, status="not_run", critical=critical)
        checks: list[CheckResult] = []
        for name, value in rules.items():
            if name in {"critical", "weight"}:
                # Metadata fields, not checks.
                continue
            check = _run_check(name, value, content, relative_path)
            if check is not None:
                checks.append(check)
        if not checks:
            return ValidationResult(path=relative_path, score=100, status="not_run", critical=critical)
        passed = sum(1 for c in checks if c.passed)
        total = len(checks)
        score = int(round(100 * passed / total))
        if passed == total:
            status = "passed"
        elif passed == 0:
            status = "failed"
        else:
            status = "partial"
        return ValidationResult(
            path=relative_path, score=score, status=status, checks=checks, critical=critical
        )


def load_contracts(yaml_path: Path | None = None) -> Validator:
    """Load contracts from YAML file. If ``yaml_path`` is None, use the
    bundled default ``artifact_contracts.yaml``."""
    if yaml_path is None:
        yaml_path = Path(__file__).resolve().parent / "artifact_contracts.yaml"
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        yaml = None
    if not yaml_path.exists():
        return Validator(contracts=[], default_rules={"min_length_chars": 200})
    if yaml is None:
        from orchestrator.core.yaml_loader import load_yaml

        raw = load_yaml(yaml_path)
    else:
        raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
    raw = _normalize_yaml_values(raw)
    contracts_raw = raw.get("contracts") or {}
    contracts: list[ArtifactContract] = []
    for path_pattern, rules in contracts_raw.items():
        if isinstance(rules, dict):
            contracts.append(ArtifactContract(path_pattern=str(path_pattern), rules=rules))
    default_rules = raw.get("default") or {"min_length_chars": 200}
    return Validator(contracts=contracts, default_rules=default_rules)


def _normalize_yaml_values(value: Any) -> Any:
    """Clean values from the tiny bundled YAML parser used when PyYAML is absent."""
    if isinstance(value, dict):
        return {_clean_yaml_scalar(str(key)): _normalize_yaml_values(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_normalize_yaml_values(item) for item in value]
    if isinstance(value, str):
        return _clean_yaml_scalar(value)
    return value


def _clean_yaml_scalar(value: str) -> str:
    stripped = _strip_inline_comment(value.strip())
    if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in {"'", '"'}:
        if stripped[0] == '"':
            try:
                parsed = json.loads(stripped)
                return str(parsed)
            except json.JSONDecodeError:
                pass
        return stripped[1:-1]
    return stripped


def _strip_inline_comment(value: str) -> str:
    quote: str | None = None
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char in {"'", '"'}:
            quote = None if quote == char else char if quote is None else quote
            continue
        if char == "#" and quote is None and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value


# --- check implementations -------------------------------------------------

def _run_check(name: str, value: Any, content: str, path: str) -> CheckResult | None:
    fn = _CHECK_REGISTRY.get(name)
    if fn is None:
        # Unknown rule names are silently ignored so contracts can carry
        # commentary fields or future rules without crashing the validator.
        return None
    try:
        return fn(value, content, path)
    except Exception as exc:  # noqa: BLE001 — never let a check raise out
        return CheckResult(name=name, passed=False, detail=f"check error: {type(exc).__name__}: {exc}")


def _check_min_length(value: Any, content: str, path: str) -> CheckResult:
    threshold = int(value)
    actual = len(content)
    return CheckResult(
        name="min_length_chars",
        passed=actual >= threshold,
        detail=f"length={actual}, required>={threshold}",
    )


def _check_required_sections(value: Any, content: str, path: str) -> CheckResult:
    sections = list(value or [])
    missing: list[str] = []
    lowered = content.lower()
    for s in sections:
        # Accept both `# Section` and `## Section` style; substring match is
        # sufficient for "this topic is covered somewhere".
        if str(s).lower() not in lowered:
            missing.append(str(s))
    return CheckResult(
        name="required_sections",
        passed=not missing,
        detail=f"missing={missing}" if missing else "all sections present",
    )


def _check_must_not_contain(value: Any, content: str, path: str) -> CheckResult:
    forbidden = list(value or [])
    found: list[str] = []
    for f in forbidden:
        if str(f) in content:
            found.append(str(f))
    return CheckResult(
        name="must_not_contain",
        passed=not found,
        detail=f"found={found}" if found else "none of the forbidden tokens",
    )


def _check_must_match_pattern(value: Any, content: str, path: str) -> CheckResult:
    pattern = re.compile(str(value))
    matched = bool(pattern.search(content))
    return CheckResult(
        name="must_match_pattern",
        passed=matched,
        detail=f"pattern={value} matched={matched}",
    )


def _check_min_pattern_count(value: Any, content: str, path: str) -> CheckResult:
    # Re-applies the previously declared pattern. We need to find a
    # ``must_match_pattern`` rule to know what to count against, but the
    # validator applies rules in declaration order, so by the time this runs
    # the pattern key is whatever the contract intends. To keep things simple
    # we expect ``must_match_pattern`` to be set on the same contract; we
    # re-compile from contract scope via ``content``-only — but that requires
    # access to the contract. Workaround: encode as ``[pattern, min]`` if
    # users want both. For now, accept ``int`` value and just count
    # AC-style codes by scanning for ``AC-\\d+`` as default.
    threshold = int(value)
    matches = re.findall(r"[A-Z]{1,5}-\d+", content)
    return CheckResult(
        name="min_pattern_count",
        passed=len(matches) >= threshold,
        detail=f"count={len(matches)}, required>={threshold}",
    )


def _check_must_parse_as_json(value: Any, content: str, path: str) -> CheckResult:
    if not value:
        return CheckResult(name="must_parse_as_json", passed=True, detail="skipped")
    try:
        json.loads(content)
        return CheckResult(name="must_parse_as_json", passed=True, detail="parsed")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="must_parse_as_json", passed=False, detail=str(exc)[:200])


def _check_must_parse_as_yaml(value: Any, content: str, path: str) -> CheckResult:
    if not value:
        return CheckResult(name="must_parse_as_yaml", passed=True, detail="skipped")
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return CheckResult(name="must_parse_as_yaml", passed=True, detail="PyYAML missing — skipped")
    try:
        yaml.safe_load(content)
        return CheckResult(name="must_parse_as_yaml", passed=True, detail="parsed")
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="must_parse_as_yaml", passed=False, detail=str(exc)[:200])


def _check_required_yaml_keys(value: Any, content: str, path: str) -> CheckResult:
    keys = list(value or [])
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return CheckResult(name="required_yaml_keys", passed=True, detail="PyYAML missing — skipped")
    try:
        loaded = yaml.safe_load(content)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="required_yaml_keys", passed=False, detail=f"parse: {exc}")
    if not isinstance(loaded, dict):
        return CheckResult(name="required_yaml_keys", passed=False, detail="root is not a mapping")
    missing = [k for k in keys if k not in loaded]
    return CheckResult(
        name="required_yaml_keys",
        passed=not missing,
        detail=f"missing={missing}" if missing else "all keys present",
    )


def _check_json_required_path(value: Any, content: str, path: str) -> CheckResult:
    """Very small JSONPath-ish: supports ``$[N].field`` and ``$.field``."""
    expr = str(value)
    try:
        loaded = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="json_required_path", passed=False, detail=f"parse: {exc}")
    cursor: Any = loaded
    # Strip leading $
    rest = expr.lstrip("$")
    # Tokenize: alternating between [\d+] and .field
    pattern = re.compile(r"(\[(?:\d+)\])|\.([A-Za-z_][A-Za-z0-9_]*)")
    pos = 0
    while pos < len(rest):
        m = pattern.match(rest, pos)
        if not m:
            return CheckResult(name="json_required_path", passed=False, detail=f"bad expr at {rest[pos:]}")
        if m.group(1):
            idx = int(m.group(1)[1:-1])
            if not isinstance(cursor, list) or idx >= len(cursor):
                return CheckResult(name="json_required_path", passed=False, detail=f"index {idx} out of range")
            cursor = cursor[idx]
        else:
            key = m.group(2)
            if not isinstance(cursor, dict) or key not in cursor:
                return CheckResult(name="json_required_path", passed=False, detail=f"missing key {key}")
            cursor = cursor[key]
        pos = m.end()
    return CheckResult(name="json_required_path", passed=True, detail=f"resolved {expr}")


def _check_min_json_array_items(value: Any, content: str, path: str) -> CheckResult:
    threshold = int(value)
    try:
        loaded = json.loads(content)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(name="min_json_array_items", passed=False, detail=f"parse: {exc}")
    if not isinstance(loaded, list):
        return CheckResult(name="min_json_array_items", passed=False, detail="root not a list")
    return CheckResult(
        name="min_json_array_items",
        passed=len(loaded) >= threshold,
        detail=f"items={len(loaded)}, required>={threshold}",
    )


_CHECK_REGISTRY: dict[str, Any] = {
    "min_length_chars": _check_min_length,
    "required_sections": _check_required_sections,
    "must_not_contain": _check_must_not_contain,
    "must_match_pattern": _check_must_match_pattern,
    "min_pattern_count": _check_min_pattern_count,
    "must_parse_as_json": _check_must_parse_as_json,
    "must_parse_as_yaml": _check_must_parse_as_yaml,
    "required_yaml_keys": _check_required_yaml_keys,
    "json_required_path": _check_json_required_path,
    "min_json_array_items": _check_min_json_array_items,
}
