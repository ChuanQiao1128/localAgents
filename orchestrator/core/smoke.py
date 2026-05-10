"""Smoke-check adapter (MVP-4F).

Runs lightweight HTTP checks against a freshly-deployed URL to confirm the
deployment is actually serving. Intentionally minimal — this is "is the URL
up and returning the expected status?", not full end-to-end validation.

Public surface:
  - `default_http_client(url, method, timeout, headers)` — stdlib `urllib`.
    Tests inject their own callable with the same signature.
  - `build_smoke_check_url(deployment_url, check)` — combine deployment URL
    with the per-check `path` (or override with `url`).
  - `run_smoke_checks(config, deployment_url, *, http_client=None)` —
    execute the configured checks and return a `SmokeRunResult`.
  - `classify_smoke_failure(...)` — turn an HTTP/exception outcome into one
    of `SMOKE_FAILURE_TYPES`.

Security invariants:
  - User-configured request headers may contain secrets (auth tokens, API
    keys). They're sent on the wire BUT the artifact never persists their
    values — `_sanitize_headers_for_artifact` replaces every value with
    `<redacted>` before the dict is written.
  - Response bodies are tail-truncated to 3KB before persistence.
  - No cookies are sent and no cookies are recorded.
"""
from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urljoin

from orchestrator.core.deploy import (
    REDACTED,
    SMOKE_FAILURE_TYPES,
    SmokeCheckConfig,
    new_smoke_check_id,
    write_smoke_check_artifact,
)
from orchestrator.core.ids import now_iso


_RESPONSE_TAIL_BYTES = 3000
_DEFAULT_EXPECTED_STATUS = 200
_DEFAULT_METHOD = "GET"
_VALID_METHODS = {"GET", "HEAD"}


# ---------------------------------------------------------------------------
# HTTP client primitive
# ---------------------------------------------------------------------------
@dataclass
class HttpClientResult:
    """Output of one HTTP attempt. `error` is set when the request itself
    failed (timeout / connection refused / DNS); on protocol-level
    responses (404 / 500) we still return status + body and leave error=None."""
    status: int | None
    body: str
    duration_ms: int
    error: str | None  # short, low-cardinality (used for failure classification)


HttpClient = Callable[[str, str, float, dict[str, str] | None], HttpClientResult]


def default_http_client(url: str, method: str, timeout: float, headers: dict[str, str] | None) -> HttpClientResult:
    """Stdlib `urllib`-based smoke check executor. Used in production; tests
    inject their own callable.

    Returns body as a UTF-8-decoded string (errors='replace'); empty for HEAD.
    """
    started = time.perf_counter()
    request = urllib.request.Request(url=url, method=method.upper(), headers=headers or {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — known URL
            status = int(response.status)
            raw = response.read() if method.upper() != "HEAD" else b""
            try:
                body = raw.decode("utf-8", errors="replace")
            except UnicodeDecodeError:
                body = raw.decode("latin-1", errors="replace")
    except urllib.error.HTTPError as exc:
        # 4xx / 5xx still arrive here as exceptions in stdlib. Treat them as
        # "we got a response" so the classifier reports `status_mismatch`.
        try:
            raw = exc.read()
        except Exception:  # noqa: BLE001
            raw = b""
        body = raw.decode("utf-8", errors="replace")
        return HttpClientResult(
            status=int(exc.code),
            body=body,
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=None,
        )
    except urllib.error.URLError as exc:
        msg = str(exc.reason) if hasattr(exc, "reason") else str(exc)
        is_timeout = isinstance(getattr(exc, "reason", None), (TimeoutError, socket.timeout))
        return HttpClientResult(
            status=None, body="",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=("timeout" if is_timeout else f"connection_error: {msg}"),
        )
    except (TimeoutError, socket.timeout):
        return HttpClientResult(
            status=None, body="",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error="timeout",
        )
    except OSError as exc:
        return HttpClientResult(
            status=None, body="",
            duration_ms=int((time.perf_counter() - started) * 1000),
            error=f"connection_error: {exc}",
        )
    return HttpClientResult(
        status=status, body=body,
        duration_ms=int((time.perf_counter() - started) * 1000),
        error=None,
    )


# ---------------------------------------------------------------------------
# URL composition
# ---------------------------------------------------------------------------
def build_smoke_check_url(deployment_url: str, check: dict[str, Any]) -> str:
    """Resolve the URL for one check. Per-check `url` overrides everything;
    otherwise we join the deployment URL with `path` (defaulting to '/')."""
    explicit = str(check.get("url") or "").strip()
    if explicit:
        return explicit
    path = str(check.get("path") or "/")
    base = deployment_url if deployment_url.endswith("/") else deployment_url + "/"
    return urljoin(base, path.lstrip("/"))


# ---------------------------------------------------------------------------
# Header redaction (artifacts only — wire still gets real values)
# ---------------------------------------------------------------------------
def _sanitize_headers_for_artifact(headers: dict[str, str] | None) -> dict[str, str] | None:
    if not headers:
        return None
    return {str(k): REDACTED for k in headers}


# ---------------------------------------------------------------------------
# Per-check execution
# ---------------------------------------------------------------------------
@dataclass
class CheckResult:
    name: str
    method: str
    url: str
    expected_status: int
    actual_status: int | None
    passed: bool
    duration_ms: int
    response_body_tail: str
    error: str | None
    attempts: int
    headers_redacted: dict[str, str] | None = None
    expected_text_contains: str | None = None


def classify_smoke_failure(check_result: CheckResult) -> str:
    """Map a failed CheckResult to a SMOKE_FAILURE_TYPES value."""
    if check_result.error == "timeout":
        return "timeout"
    if check_result.error and check_result.error.startswith("connection_error"):
        return "connection_error"
    if check_result.actual_status is not None and check_result.actual_status != check_result.expected_status:
        return "status_mismatch"
    if check_result.expected_text_contains and check_result.expected_text_contains not in check_result.response_body_tail:
        return "expected_text_missing"
    if check_result.error:
        return "unknown"
    return "unknown"


def _execute_single_check(
    check: dict[str, Any],
    deployment_url: str,
    timeout_sec: int,
    retries: int,
    http_client: HttpClient,
) -> CheckResult:
    name = str(check.get("name") or "unnamed")
    method = str(check.get("method") or _DEFAULT_METHOD).upper()
    if method not in _VALID_METHODS:
        # Refuse silently: classify as unknown so the artifact records why.
        return CheckResult(
            name=name, method=method, url=build_smoke_check_url(deployment_url, check),
            expected_status=int(check.get("expected_status") or _DEFAULT_EXPECTED_STATUS),
            actual_status=None, passed=False, duration_ms=0,
            response_body_tail="", error=f"unsupported_method:{method}", attempts=0,
            headers_redacted=_sanitize_headers_for_artifact(check.get("headers")),
        )
    url = build_smoke_check_url(deployment_url, check)
    expected_status = int(check.get("expected_status") or _DEFAULT_EXPECTED_STATUS)
    expected_text = check.get("expected_text_contains")
    headers = check.get("headers") if isinstance(check.get("headers"), dict) else None
    attempts_made = 0
    last_result: HttpClientResult | None = None
    # `retries` is the number of EXTRA attempts after the first one.
    for attempt in range(max(0, retries) + 1):
        attempts_made = attempt + 1
        last_result = http_client(url, method, float(timeout_sec), headers)
        # Success criteria: HTTP status matches expected AND (when provided)
        # response body contains the expected text.
        if last_result.error:
            continue  # retry on transport errors
        if last_result.status != expected_status:
            continue
        if expected_text and expected_text not in (last_result.body or ""):
            continue
        # passed
        body_tail = (last_result.body or "")[-_RESPONSE_TAIL_BYTES:]
        return CheckResult(
            name=name, method=method, url=url,
            expected_status=expected_status, actual_status=last_result.status,
            passed=True, duration_ms=last_result.duration_ms,
            response_body_tail=body_tail, error=None,
            attempts=attempts_made,
            headers_redacted=_sanitize_headers_for_artifact(headers),
            expected_text_contains=str(expected_text) if expected_text else None,
        )
    # Out of retries — record the last failure.
    body_tail = (last_result.body or "")[-_RESPONSE_TAIL_BYTES:] if last_result else ""
    return CheckResult(
        name=name, method=method, url=url,
        expected_status=expected_status,
        actual_status=last_result.status if last_result else None,
        passed=False, duration_ms=last_result.duration_ms if last_result else 0,
        response_body_tail=body_tail,
        error=last_result.error if last_result else "no_response",
        attempts=attempts_made,
        headers_redacted=_sanitize_headers_for_artifact(headers),
        expected_text_contains=str(expected_text) if expected_text else None,
    )


# ---------------------------------------------------------------------------
# Top-level run + serialization
# ---------------------------------------------------------------------------
@dataclass
class SmokeRunResult:
    status: str  # passed | failed | skipped
    checks: list[CheckResult] = field(default_factory=list)
    failure: dict[str, Any] | None = None
    started_at: str = field(default_factory=now_iso)
    completed_at: str = field(default_factory=now_iso)


def run_smoke_checks(
    config: SmokeCheckConfig,
    deployment_url: str | None,
    *,
    http_client: HttpClient | None = None,
) -> SmokeRunResult:
    """Run every configured smoke check sequentially against `deployment_url`.

    Returns a SmokeRunResult. If `deployment_url` is missing or empty,
    returns status="failed" with failure_type="deployment_url_missing"
    (no checks attempted).
    """
    started_at = now_iso()
    if not deployment_url:
        return SmokeRunResult(
            status="failed",
            checks=[],
            failure={
                "failure_type": "deployment_url_missing",
                "message": "deployment URL was not available",
                "failed_check": None,
            },
            started_at=started_at,
            completed_at=now_iso(),
        )
    if not config.checks:
        return SmokeRunResult(
            status="passed",
            checks=[],
            failure=None,
            started_at=started_at,
            completed_at=now_iso(),
        )
    runner = http_client or default_http_client
    results: list[CheckResult] = []
    failed: CheckResult | None = None
    for check in config.checks:
        result = _execute_single_check(check, deployment_url, config.timeout_sec, config.retries, runner)
        results.append(result)
        if not result.passed and failed is None:
            failed = result
    if failed is None:
        return SmokeRunResult(status="passed", checks=results, failure=None,
                              started_at=started_at, completed_at=now_iso())
    failure_type = classify_smoke_failure(failed)
    return SmokeRunResult(
        status="failed",
        checks=results,
        failure={
            "failure_type": failure_type,
            "message": (failed.error or f"{failed.name}: expected {failed.expected_status}, got {failed.actual_status}"),
            "failed_check": failed.name,
        },
        started_at=started_at,
        completed_at=now_iso(),
    )


def serialize_check_results(results: list[CheckResult]) -> list[dict[str, Any]]:
    """Convert CheckResult list to artifact-shaped dicts. Headers are
    already redacted by the runner; bodies are already tail-truncated."""
    return [
        {
            "name": c.name,
            "method": c.method,
            "url": c.url,
            "expected_status": c.expected_status,
            "actual_status": c.actual_status,
            "passed": c.passed,
            "duration_ms": c.duration_ms,
            "response_body_tail": c.response_body_tail,
            "error": c.error,
            "attempts": c.attempts,
            "headers": c.headers_redacted,
            "expected_text_contains": c.expected_text_contains,
        }
        for c in results
    ]


def persist_smoke_run(
    project_path: Path,
    *,
    session_id: str,
    project_id: str,
    deployment_id: str | None,
    deployment_url: str | None,
    environment: str,
    result: SmokeRunResult,
) -> tuple[str, Path]:
    """Convenience wrapper: write_smoke_check_artifact for a SmokeRunResult.
    Returns (smoke_check_id, artifact_path)."""
    smoke_check_id = new_smoke_check_id()
    serialized = serialize_check_results(result.checks)
    artifact_path = write_smoke_check_artifact(
        project_path,
        session_id=session_id,
        project_id=project_id,
        smoke_check_id=smoke_check_id,
        deployment_id=deployment_id,
        deployment_url=deployment_url,
        environment=environment,
        status=result.status,
        started_at=result.started_at,
        completed_at=result.completed_at,
        checks=serialized,
        failure=result.failure,
    )
    return smoke_check_id, artifact_path
