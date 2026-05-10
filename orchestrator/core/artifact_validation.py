"""RC-1: lightweight artifact validation helpers.

These functions are pure and return `list[str]` of human-readable error
strings. Empty list means valid. They are deliberately schema-aware
without requiring the `jsonschema` dependency — each validator encodes
the minimum shape contract we care about as plain Python checks.

Used by:
- the golden-path e2e test (catches drift in evidence shape)
- the optional `agent-studio autonomous validate-artifacts` CLI (future)
- direct invocation from operators debugging a stuck session

Validators are intentionally **minimal** — they confirm the schema_version
and the load-bearing required fields, NOT every leaf. Stricter rules live
in module-local `_validate_*` helpers (e.g. promotion-report v2 in
`agentic_runtime.py`); this module is for cross-artifact sanity checks.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------
def _load_json(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """Read + parse a JSON file. Returns (payload, errors).

    Empty errors list AND payload is not None → ready to validate.
    """
    if not path.exists():
        return None, [f"file not found: {path}"]
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, [f"cannot read {path}: {exc}"]
    if not text.strip():
        return None, [f"file is empty: {path}"]
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, [f"invalid JSON in {path}: {exc}"]
    if not isinstance(payload, dict):
        return None, [f"top-level value is not a dict in {path}"]
    return payload, []


def _check_keys(payload: dict[str, Any], required: list[tuple[str, type | tuple[type, ...]]], *, label: str) -> list[str]:
    """Check that each (key, expected_type) pair is present and well-typed."""
    errors: list[str] = []
    for key, expected in required:
        if key not in payload:
            errors.append(f"{label}.{key} is missing")
            continue
        if not isinstance(payload[key], expected):
            type_name = getattr(expected, "__name__", repr(expected))
            actual = type(payload[key]).__name__
            errors.append(f"{label}.{key} is `{actual}`; expected `{type_name}`")
    return errors


# ---------------------------------------------------------------------------
# autonomous-session.json
# ---------------------------------------------------------------------------
def validate_autonomous_session(payload: dict[str, Any]) -> list[str]:
    """Validate an autonomous-session.json payload.

    Required: schema_version, session_id, project_id, status, branch,
    counters dict, deployment dict.
    """
    errors = _check_keys(payload, [
        ("schema_version", int),
        ("session_id", str),
        ("project_id", str),
        ("status", str),
        ("branch", str),
        ("counters", dict),
        ("deployment", dict),
    ], label="autonomous-session")
    if "status" in payload and isinstance(payload["status"], str):
        if payload["status"] not in {"running", "paused", "completed"}:
            errors.append(
                f"autonomous-session.status `{payload['status']}` "
                "is not one of [running, paused, completed]"
            )
    if "counters" in payload and isinstance(payload["counters"], dict):
        for c in ("completed_tasks", "abandoned_tasks", "needs_review_tasks", "inner_runs"):
            if c not in payload["counters"]:
                errors.append(f"autonomous-session.counters.{c} is missing")
    return errors


# ---------------------------------------------------------------------------
# task-graph.json
# ---------------------------------------------------------------------------
def validate_task_graph(payload: dict[str, Any]) -> list[str]:
    """Validate a task-graph.json payload.

    Required: schema_version, project_title, tasks list. Each task must
    have id, title, intent, scope_paths, acceptance_criteria, dependencies,
    status.
    """
    errors = _check_keys(payload, [
        ("schema_version", int),
        ("project_title", str),
        ("tasks", list),
    ], label="task-graph")
    if isinstance(payload.get("tasks"), list):
        for index, task in enumerate(payload["tasks"]):
            if not isinstance(task, dict):
                errors.append(f"task-graph.tasks[{index}] is not a dict")
                continue
            for key, expected in (
                ("id", str), ("title", str), ("intent", str),
                ("scope_paths", list), ("acceptance_criteria", list),
                ("dependencies", list), ("status", str),
            ):
                if key not in task:
                    errors.append(f"task-graph.tasks[{index}].{key} is missing")
                elif not isinstance(task[key], expected):
                    actual = type(task[key]).__name__
                    type_name = getattr(expected, "__name__", repr(expected))
                    errors.append(
                        f"task-graph.tasks[{index}].{key} is `{actual}`; expected `{type_name}`"
                    )
            if "status" in task and isinstance(task["status"], str):
                if task["status"] not in {
                    "pending", "completed", "needs-human-review",
                    "abandoned", "blocked",
                }:
                    errors.append(
                        f"task-graph.tasks[{index}].status `{task['status']}` "
                        "is not one of [pending, completed, needs-human-review, "
                        "abandoned, blocked]"
                    )
    return errors


# ---------------------------------------------------------------------------
# promotion-report.json (delegates to the v2 validator that lives in
# agentic_runtime.py, so this module stays the single read entry point)
# ---------------------------------------------------------------------------
def validate_promotion_report(payload: dict[str, Any]) -> list[str]:
    """Validate a promotion-report.v2 payload (delegates to the producer's
    own validator so the rules can't drift)."""
    from orchestrator.core.agentic_runtime import _validate_promotion_report_v2
    return _validate_promotion_report_v2(payload)


# ---------------------------------------------------------------------------
# deployment.json (MVP-4E)
# ---------------------------------------------------------------------------
def validate_deployment(payload: dict[str, Any]) -> list[str]:
    """Validate a deployment.json payload.

    Required: schema_version=1, deployment_id, session_id, project_id,
    target, status. failure may be None (success) or a dict with
    failure_type + message.
    """
    errors: list[str] = []
    schema = payload.get("schema_version")
    if schema != 1:
        errors.append(f"deployment.schema_version is `{schema}`; expected `1`")
    errors += _check_keys(payload, [
        ("deployment_id", str),
        ("session_id", str),
        ("project_id", str),
        ("target", str),
        ("status", str),
    ], label="deployment")
    if "status" in payload and isinstance(payload["status"], str):
        if payload["status"] not in {"ready", "failed", "unknown", "skipped"}:
            errors.append(
                f"deployment.status `{payload['status']}` is not one of "
                "[ready, failed, unknown, skipped]"
            )
    if "failure" in payload and payload["failure"] is not None:
        if not isinstance(payload["failure"], dict):
            errors.append(
                f"deployment.failure is `{type(payload['failure']).__name__}`; expected dict or null"
            )
        else:
            if "failure_type" not in payload["failure"]:
                errors.append("deployment.failure.failure_type is missing")
            if "message" not in payload["failure"]:
                errors.append("deployment.failure.message is missing")
    if "commands" in payload and isinstance(payload["commands"], list):
        # Token redaction sanity: no command's serialized args should contain
        # a literal env-var-style token. We can't detect every secret, but we
        # can flag obvious leaks of tokens that look like JWT / hex 32+.
        import re
        token_re = re.compile(r"\b(?:[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{32,}|[a-f0-9]{40,})\b")
        for index, cmd in enumerate(payload["commands"]):
            for arg in (cmd or {}).get("args") or []:
                if isinstance(arg, str) and token_re.search(arg):
                    errors.append(
                        f"deployment.commands[{index}].args contains a value that looks like an unredacted secret"
                    )
                    break
    return errors


# ---------------------------------------------------------------------------
# smoke-check.json (MVP-4F)
# ---------------------------------------------------------------------------
def validate_smoke_check(payload: dict[str, Any]) -> list[str]:
    """Validate a smoke-check.json payload.

    Required: schema_version=1, smoke_check_id, session_id, project_id,
    deployment_url-or-null, status, checks (list).
    """
    errors: list[str] = []
    schema = payload.get("schema_version")
    if schema != 1:
        errors.append(f"smoke-check.schema_version is `{schema}`; expected `1`")
    errors += _check_keys(payload, [
        ("smoke_check_id", str),
        ("session_id", str),
        ("project_id", str),
        ("status", str),
        ("checks", list),
    ], label="smoke-check")
    if "status" in payload and isinstance(payload["status"], str):
        if payload["status"] not in {"passed", "failed", "skipped"}:
            errors.append(
                f"smoke-check.status `{payload['status']}` is not one of [passed, failed, skipped]"
            )
    # Header redaction sanity: every check.headers_redacted value must be
    # the literal `<redacted>` placeholder, never the real header value.
    if isinstance(payload.get("checks"), list):
        for index, check in enumerate(payload["checks"]):
            if not isinstance(check, dict):
                errors.append(f"smoke-check.checks[{index}] is not a dict")
                continue
            for key in ("name", "expected_status"):
                if key not in check:
                    errors.append(f"smoke-check.checks[{index}].{key} is missing")
            headers = check.get("headers_redacted")
            if isinstance(headers, dict):
                for hk, hv in headers.items():
                    if hv != "<redacted>":
                        errors.append(
                            f"smoke-check.checks[{index}].headers_redacted[{hk}] is `{hv}`; expected `<redacted>`"
                        )
    return errors


# ---------------------------------------------------------------------------
# review-items/<review_id>.json (MVP-4D)
# ---------------------------------------------------------------------------
def validate_review_item(payload: dict[str, Any]) -> list[str]:
    """Validate a review-items/<id>.json payload (RC-2B.9).

    Required: schema_version=1, review_id, session_id, project_id, status,
    severity, source_type, reason_code, title, summary. Status / severity
    must be from their canonical enums (we do NOT delegate to
    review_queue.REVIEW_STATUSES because that would create a hard import
    cycle for callers; the values are stable). Optional fields are
    type-checked when present.
    """
    errors: list[str] = []
    schema = payload.get("schema_version")
    if schema != 1:
        errors.append(f"review-item.schema_version is `{schema}`; expected `1`")
    errors += _check_keys(payload, [
        ("review_id", str),
        ("session_id", str),
        ("project_id", str),
        ("status", str),
        ("severity", str),
        ("source_type", str),
        ("reason_code", str),
        ("title", str),
        ("summary", str),
    ], label="review-item")
    if "status" in payload and isinstance(payload["status"], str):
        if payload["status"] not in {"open", "approved", "rejected", "resolved"}:
            errors.append(
                f"review-item.status `{payload['status']}` is not one of "
                "[open, approved, rejected, resolved]"
            )
    if "severity" in payload and isinstance(payload["severity"], str):
        if payload["severity"] not in {"blocking", "warning", "info"}:
            errors.append(
                f"review-item.severity `{payload['severity']}` is not one of "
                "[blocking, warning, info]"
            )
    for list_key in ("evidence_paths", "suggested_commands", "allowed_actions"):
        if list_key in payload and not isinstance(payload[list_key], list):
            errors.append(
                f"review-item.{list_key} is `{type(payload[list_key]).__name__}`; expected list"
            )
    if "resolution" in payload and payload["resolution"] is not None and not isinstance(payload["resolution"], dict):
        errors.append(
            f"review-item.resolution is `{type(payload['resolution']).__name__}`; expected dict or null"
        )
    return errors


# ---------------------------------------------------------------------------
# integration-failures/<id>/integration-failure.json (MVP-4C)
# ---------------------------------------------------------------------------
def validate_integration_failure(payload: dict[str, Any]) -> list[str]:
    """Validate an integration-failure.json payload (RC-2B.10).

    Required: schema_version=1, failure_id, session_id, project_id,
    trigger, after_task_id, failed_command (dict with name + cmd),
    detected_failure_type, created_at.
    """
    errors: list[str] = []
    schema = payload.get("schema_version")
    if schema != 1:
        errors.append(f"integration-failure.schema_version is `{schema}`; expected `1`")
    errors += _check_keys(payload, [
        ("failure_id", str),
        ("session_id", str),
        ("project_id", str),
        ("trigger", str),
        ("detected_failure_type", str),
        ("created_at", str),
    ], label="integration-failure")
    if "failed_command" not in payload:
        errors.append("integration-failure.failed_command is missing")
    elif not isinstance(payload["failed_command"], dict):
        errors.append(
            f"integration-failure.failed_command is `{type(payload['failed_command']).__name__}`; expected dict"
        )
    else:
        if "name" not in payload["failed_command"]:
            errors.append("integration-failure.failed_command.name is missing")
    return errors


# ---------------------------------------------------------------------------
# applied-candidate.json (MVP-3B)
# ---------------------------------------------------------------------------
def validate_applied_candidate(payload: dict[str, Any]) -> list[str]:
    """Validate an applied-candidate.json payload (RC-1.1).

    Required: schema_version=1, run_id, candidate, base_commit,
    applied_to_commit, patch_sha256, applied (bool), dry_run (bool).
    Tolerated-when-present: human_override (bool), changed_files (list of
    str), strategy (str), decision_at_apply_time (str), project_id (str
    or None), timestamp_utc (str).

    The validator is lenient about historical artifacts: only the fields
    that load-bearingly govern the re-apply guard are required. Optional
    fields are type-checked when present, never required absent.

    Also runs a token-leak heuristic over the serialized JSON so that any
    JWT-like or 40+-hex-character secret accidentally written into a free-
    form string (e.g. a strategy label or timestamp) is flagged.
    """
    errors: list[str] = []
    schema = payload.get("schema_version")
    if schema != 1:
        errors.append(f"applied-candidate.schema_version is `{schema}`; expected `1`")
    errors += _check_keys(payload, [
        ("run_id", str),
        ("candidate", str),
        ("base_commit", str),
        ("applied_to_commit", str),
        ("patch_sha256", str),
        ("applied", bool),
        ("dry_run", bool),
    ], label="applied-candidate")
    if "human_override" in payload and not isinstance(payload["human_override"], bool):
        errors.append(
            f"applied-candidate.human_override is `{type(payload['human_override']).__name__}`; expected bool"
        )
    if "changed_files" in payload:
        if not isinstance(payload["changed_files"], list):
            errors.append(
                f"applied-candidate.changed_files is `{type(payload['changed_files']).__name__}`; expected list"
            )
        else:
            for index, item in enumerate(payload["changed_files"]):
                if not isinstance(item, str):
                    errors.append(
                        f"applied-candidate.changed_files[{index}] is `{type(item).__name__}`; expected str"
                    )
    if "project_id" in payload and payload["project_id"] is not None and not isinstance(payload["project_id"], str):
        errors.append(
            f"applied-candidate.project_id is `{type(payload['project_id']).__name__}`; expected str or null"
        )
    for opt in ("strategy", "decision_at_apply_time", "timestamp_utc"):
        if opt in payload and not isinstance(payload[opt], str):
            errors.append(
                f"applied-candidate.{opt} is `{type(payload[opt]).__name__}`; expected str"
            )
    # Defense-in-depth token-leak heuristic — any JWT-like or 40+-hex
    # string anywhere in the serialized payload is flagged. patch_sha256
    # is exactly 64 hex characters, so we whitelist it explicitly.
    import re
    token_re = re.compile(r"\b(?:[A-Za-z0-9_-]{32,}\.[A-Za-z0-9_-]{32,}|[a-f0-9]{40,})\b")
    sha = str(payload.get("patch_sha256") or "")
    serialized = json.dumps(payload, ensure_ascii=False)
    for hit in token_re.finditer(serialized):
        if hit.group(0) == sha:
            continue
        errors.append(
            f"applied-candidate contains a value that looks like an unredacted secret"
        )
        break
    return errors


# ---------------------------------------------------------------------------
# rollback.json (MVP-4F)
# ---------------------------------------------------------------------------
def validate_rollback(payload: dict[str, Any]) -> list[str]:
    """Validate a rollback.json payload."""
    errors: list[str] = []
    schema = payload.get("schema_version")
    if schema != 1:
        errors.append(f"rollback.schema_version is `{schema}`; expected `1`")
    errors += _check_keys(payload, [
        ("rollback_id", str),
        ("session_id", str),
        ("project_id", str),
        ("status", str),
    ], label="rollback")
    if "status" in payload and isinstance(payload["status"], str):
        if payload["status"] not in {"completed", "failed", "skipped"}:
            errors.append(
                f"rollback.status `{payload['status']}` is not one of [completed, failed, skipped]"
            )
    return errors


# ---------------------------------------------------------------------------
# final-run-status.md (markdown, not JSON)
# ---------------------------------------------------------------------------
REQUIRED_FINAL_REPORT_SECTIONS: tuple[str, ...] = (
    "## Summary",
    "## Tasks",
    "## Integration",
    "## Corrective Tasks",
    "## Deployment",
    "## Smoke Checks",
    "## Rollback",
    "## Human Review Queue",
    "## Evidence Trail",
    "## Next Actions",
)


def validate_final_run_status_md(text: str) -> list[str]:
    """Validate the final-run-status.md report has every required section.

    Sections are checked by header substring; order is not enforced.
    """
    errors: list[str] = []
    for section in REQUIRED_FINAL_REPORT_SECTIONS:
        if section not in text:
            errors.append(f"final-run-status.md is missing section `{section}`")
    return errors


# ---------------------------------------------------------------------------
# Convenience: validate an entire session directory
# ---------------------------------------------------------------------------
def validate_session_directory(session_dir: Path) -> dict[str, list[str]]:
    """Walk a session dir and validate every artifact present.

    Returns a dict keyed by artifact relative path → list of validation
    errors. Empty values mean valid; missing keys mean the artifact wasn't
    present (some are optional — e.g. rollback only exists if rollback ran).

    Always reports on the canonical artifacts (autonomous-session,
    final-run-status, task-graph at project root). Reports on deployment,
    smoke-check, rollback if any are present in the canonical dirs.
    """
    out: dict[str, list[str]] = {}

    sess_path = session_dir / "autonomous-session.json"
    payload, load_errs = _load_json(sess_path)
    if load_errs:
        out[str(sess_path.name)] = load_errs
    elif payload is not None:
        out[str(sess_path.name)] = validate_autonomous_session(payload)

    final_path = session_dir / "final-run-status.md"
    if final_path.exists():
        out[str(final_path.name)] = validate_final_run_status_md(
            final_path.read_text(encoding="utf-8")
        )
    else:
        out[str(final_path.name)] = [f"file not found: {final_path}"]

    # task-graph.json lives at project root, four levels up from the session
    # dir: .agent/autonomous/sessions/<sid>/ → parents[0]=sessions,
    # parents[1]=autonomous, parents[2]=.agent, parents[3]=project root.
    project_path = session_dir.parents[3]
    tg_path = project_path / "task-graph.json"
    payload, load_errs = _load_json(tg_path)
    if load_errs:
        out["task-graph.json"] = load_errs
    elif payload is not None:
        out["task-graph.json"] = validate_task_graph(payload)

    deployments_dir = session_dir / "deployments"
    if deployments_dir.is_dir():
        for deployment_json in sorted(deployments_dir.glob("*/deployment.json")):
            payload, load_errs = _load_json(deployment_json)
            key = str(deployment_json.relative_to(session_dir).as_posix())
            if load_errs:
                out[key] = load_errs
            elif payload is not None:
                out[key] = validate_deployment(payload)

    smoke_dir = session_dir / "smoke-checks"
    if smoke_dir.is_dir():
        for smoke_json in sorted(smoke_dir.glob("*/smoke-check.json")):
            payload, load_errs = _load_json(smoke_json)
            key = str(smoke_json.relative_to(session_dir).as_posix())
            if load_errs:
                out[key] = load_errs
            elif payload is not None:
                out[key] = validate_smoke_check(payload)

    rollback_dir = session_dir / "rollbacks"
    if rollback_dir.is_dir():
        for rb_json in sorted(rollback_dir.glob("*/rollback.json")):
            payload, load_errs = _load_json(rb_json)
            key = str(rb_json.relative_to(session_dir).as_posix())
            if load_errs:
                out[key] = load_errs
            elif payload is not None:
                out[key] = validate_rollback(payload)

    # RC-2B.9 + 2B.10: walk review-items/ and integration-failures/ too.
    reviews_dir = session_dir / "review-items"
    if reviews_dir.is_dir():
        for rv_json in sorted(reviews_dir.glob("*.json")):
            payload, load_errs = _load_json(rv_json)
            key = str(rv_json.relative_to(session_dir).as_posix())
            if load_errs:
                out[key] = load_errs
            elif payload is not None:
                out[key] = validate_review_item(payload)
    failures_dir = session_dir / "integration-failures"
    if failures_dir.is_dir():
        for if_json in sorted(failures_dir.glob("*/integration-failure.json")):
            payload, load_errs = _load_json(if_json)
            key = str(if_json.relative_to(session_dir).as_posix())
            if load_errs:
                out[key] = load_errs
            elif payload is not None:
                out[key] = validate_integration_failure(payload)

    # RC-1.1: applied-candidate.json lives at .agent/runs/<run_id>/ rather
    # than under the session dir (the inner agentic runtime owns it). Walk
    # the project-level runs dir so a session validation pass also covers
    # every applied candidate that contributed to this session.
    runs_dir = project_path / ".agent" / "runs"
    if runs_dir.is_dir():
        for ac_json in sorted(runs_dir.glob("*/applied-candidate.json")):
            payload, load_errs = _load_json(ac_json)
            key = str(ac_json.relative_to(project_path).as_posix())
            if load_errs:
                out[key] = load_errs
            elif payload is not None:
                out[key] = validate_applied_candidate(payload)

    return out


def has_validation_errors(report: dict[str, list[str]]) -> bool:
    return any(errs for errs in report.values())
