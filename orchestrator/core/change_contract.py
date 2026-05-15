"""RC-4A.1: change-mode session bootstrap.

Wraps the parser + repo onboarding into a single `create_change`
operation that produces five deterministic artifacts on disk:

    .agent/changes/<change_id>/
      change-request.md          immutable copy of the operator input
      change-contract.json       agentic.change_contract.v1
      repo-onboarding.md         deterministic project snapshot
      implementation-plan.md     placeholder until RC-4A.2 wires execution
      acceptance-criteria.json   shape-compatible with autonomous mode

`change_id` follows the existing `<prefix>_<short_hex>` pattern from
`orchestrator.core.ids` so it sorts and greps the same way as
`session_*`, `run_*`, `deployment_*`, etc.

This module deliberately does NOT execute Codex, run integration, or
mutate project source. RC-4A.2 will add a `run_change` entry that
hands off to `AutonomousController` with a single-task task graph
derived from this contract. RC-4A.1 just ships the data layer + CLI
plumbing so the artifact shape is reviewable end-to-end first.
"""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from orchestrator.core.change_repo_onboarding import render_repo_onboarding, scan_repo
from orchestrator.core.change_request_parser import (
    ChangeRequest,
    ChangeRequestParseError,
    parse_change_request_file,
)
from orchestrator.core.ids import now_iso, short_id


CHANGE_CONTRACT_SCHEMA_VERSION = "agentic.change_contract.v1"


@dataclass
class CreatedChange:
    """Return shape of `create_change` — what got written + where."""
    change_id: str
    change_dir: Path
    change_request_path: Path
    change_contract_path: Path
    repo_onboarding_path: Path
    implementation_plan_path: Path
    acceptance_criteria_path: Path


def changes_root(project_path: Path) -> Path:
    """Where all change session dirs live for a project."""
    return project_path / ".agent" / "changes"


def change_dir(project_path: Path, change_id: str) -> Path:
    return changes_root(project_path) / change_id


def latest_change_id(project_path: Path) -> str | None:
    """Return the most recently created change_id, or None if no changes
    exist for this project. "Most recent" = highest mtime on the change-contract.json
    inside each candidate dir, falling back to lexicographic order on
    change_id."""
    root = changes_root(project_path)
    if not root.exists():
        return None
    candidates: list[tuple[float, str]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        contract = entry / "change-contract.json"
        if contract.exists():
            try:
                mtime = contract.stat().st_mtime
            except OSError:
                mtime = 0.0
            candidates.append((mtime, entry.name))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: (pair[0], pair[1]))
    return candidates[-1][1]


def list_changes(project_path: Path) -> list[dict[str, Any]]:
    """Return a list of {change_id, goal, created_at, change_dir} dicts,
    sorted oldest-first. Used by `agent-studio change list`."""
    root = changes_root(project_path)
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for entry in sorted(root.iterdir(), key=lambda p: p.name):
        if not entry.is_dir():
            continue
        contract = entry / "change-contract.json"
        if not contract.exists():
            continue
        try:
            payload = json.loads(contract.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        rows.append({
            "change_id": entry.name,
            "goal": str(payload.get("goal") or ""),
            "created_at": str(payload.get("created_at") or ""),
            "change_dir": str(entry),
        })
    return rows


def resolve_change_id(project_path: Path, requested: str | None) -> str:
    """Map "latest"/None to the actual most recent change_id; raise on
    missing project changes or unknown id."""
    if requested in (None, "", "latest"):
        latest = latest_change_id(project_path)
        if latest is None:
            raise FileNotFoundError(
                f"no change sessions found under {changes_root(project_path)}. Run `agent-studio change new --from <change-request.md>` first."
            )
        return latest
    candidate_dir = change_dir(project_path, requested)
    if not candidate_dir.exists():
        raise FileNotFoundError(f"change_id not found: {requested} (looked at {candidate_dir})")
    return requested


def create_change(
    project_path: Path,
    change_request_path: Path,
    *,
    change_id: str | None = None,
    now: datetime | None = None,
) -> CreatedChange:
    """Mint a change session from a change-request.md.

    Parses + scans + writes 5 artifacts. Does NOT execute Codex.
    Raises ChangeRequestParseError on invalid input.
    """
    project_path = Path(project_path)
    change_request_path = Path(change_request_path)
    if not project_path.is_dir():
        raise FileNotFoundError(f"project_path is not a directory: {project_path}")
    if not change_request_path.exists():
        raise FileNotFoundError(f"change-request file does not exist: {change_request_path}")

    parsed = parse_change_request_file(change_request_path)
    scan = scan_repo(project_path)

    cid = change_id or short_id("change")
    cdir = change_dir(project_path, cid)
    cdir.mkdir(parents=True, exist_ok=True)

    # 1. immutable copy of input
    saved_input = cdir / "change-request.md"
    shutil.copy2(change_request_path, saved_input)

    # 2. change-contract.json
    contract_path = cdir / "change-contract.json"
    contract_payload = _build_contract(
        change_id=cid,
        source_change_request_path=str(change_request_path),
        parsed=parsed,
        now=now,
    )
    contract_path.write_text(json.dumps(contract_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # 3. repo-onboarding.md
    onboarding_path = cdir / "repo-onboarding.md"
    onboarding_path.write_text(render_repo_onboarding(scan), encoding="utf-8")

    # 4. implementation-plan.md (RC-4A.1 placeholder)
    plan_path = cdir / "implementation-plan.md"
    plan_path.write_text(_render_implementation_plan_placeholder(parsed, scan, cid), encoding="utf-8")

    # 5. acceptance-criteria.json (shape parity with autonomous mode)
    accept_path = cdir / "acceptance-criteria.json"
    accept_path.write_text(
        json.dumps(_build_acceptance_criteria(parsed), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return CreatedChange(
        change_id=cid,
        change_dir=cdir,
        change_request_path=saved_input,
        change_contract_path=contract_path,
        repo_onboarding_path=onboarding_path,
        implementation_plan_path=plan_path,
        acceptance_criteria_path=accept_path,
    )


def read_change_contract(project_path: Path, change_id: str) -> dict[str, Any]:
    path = change_dir(project_path, change_id) / "change-contract.json"
    if not path.exists():
        raise FileNotFoundError(f"change-contract.json not found at {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def change_status_summary(project_path: Path, change_id: str) -> dict[str, Any]:
    """Return a small dict the CLI can print as a status snapshot.

    States (RC-4C.1 — fixed precision):
      - `ready_for_run`         contract exists, no run output yet
      - `applied`               applied-change.json present, delivery-report.md missing
                                (rare — runner crashed between apply and report render)
      - `delivered`             BOTH applied-change.json AND delivery-report.md present
                                (the only "happy" terminal state)
      - `needs_human_review`    delivery-report.md present, applied-change.json missing,
                                report's `## Result` says `needs-human-review`
      - `failed`                delivery-report.md present, applied-change.json missing,
                                report's `## Result` says `failed` (or any other token)

    Pre-RC-4C.1 the helper reported state="delivered" whenever a
    delivery-report.md existed — even when applied-change.json was absent
    because the change had been blocked at the Promotion Gate. That
    confused operators (the failed RC-4C single-demo run on
    ai-writing-quality-editor printed `state="delivered"` despite zero
    apply). The fix below requires both files for `delivered` and reads
    the actual outcome token from the report otherwise.
    """
    cdir = change_dir(project_path, change_id)
    contract_path = cdir / "change-contract.json"
    if not contract_path.exists():
        raise FileNotFoundError(f"change_id {change_id} has no contract at {contract_path}")
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    applied_path = cdir / "applied-change.json"
    delivery_path = cdir / "delivery-report.md"
    has_applied = applied_path.exists()
    has_delivery = delivery_path.exists()
    if has_applied and has_delivery:
        state = "delivered"
    elif has_applied:
        # Apply landed but delivery render didn't fire — rare; surface as
        # "applied" so the operator knows to inspect the change dir.
        state = "applied"
    elif has_delivery:
        # Delivery render fired without apply — change failed or paused
        # for human review. Read the report's `## Result` token for the
        # precise state. If the report shape is unexpected, fall back to
        # "failed" rather than the misleading "delivered".
        state = _state_from_delivery_report(delivery_path)
    else:
        state = "ready_for_run"
    return {
        "change_id": change_id,
        "state": state,
        "goal": contract.get("goal"),
        "scope_paths": contract.get("scope_paths") or [],
        "scope_missing": bool(contract.get("scope_missing")),
        "non_goals": contract.get("non_goals") or [],
        "acceptance_count": len(contract.get("acceptance") or []),
        "created_at": contract.get("created_at"),
        "change_dir": str(cdir),
        "artifacts": {
            "change_request_md": str(cdir / "change-request.md"),
            "change_contract_json": str(contract_path),
            "repo_onboarding_md": str(cdir / "repo-onboarding.md"),
            "implementation_plan_md": str(cdir / "implementation-plan.md"),
            "acceptance_criteria_json": str(cdir / "acceptance-criteria.json"),
            "applied_change_json": str(cdir / "applied-change.json") if has_applied else None,
            "delivery_report_md": str(cdir / "delivery-report.md") if has_delivery else None,
        },
    }


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _state_from_delivery_report(delivery_path: Path) -> str:
    """Read the `## Result` token from a delivery-report.md and map it to
    a precise state. Tolerant: any unexpected shape falls back to
    "failed" — never "delivered" — so a half-rendered report can't
    masquerade as a successful change.
    """
    try:
        text = delivery_path.read_text(encoding="utf-8")
    except OSError:
        return "failed"
    # The renderer writes `## Result\n\n**<token>**` where token is one of
    # `completed`, `needs-human-review`, `failed` (plus an "(NOT one of …)"
    # suffix for unknown values). We grep for the bolded token rather than
    # reparsing the markdown.
    import re
    match = re.search(r"^\*\*(completed|needs-human-review|failed)\b", text, re.MULTILINE)
    if not match:
        return "failed"
    token = match.group(1).lower()
    if token == "needs-human-review":
        return "needs_human_review"
    if token == "failed":
        return "failed"
    # token == "completed" — but applied-change.json is missing, which
    # means the runner reported success without writing the schema-bearing
    # JSON. Treat as inconsistent → not "delivered" (caller already
    # gated `delivered` on has_applied AND has_delivery).
    return "failed"


def _build_contract(
    *,
    change_id: str,
    source_change_request_path: str,
    parsed: ChangeRequest,
    now: datetime | None,
) -> dict[str, Any]:
    iso = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    return {
        "schema_version": CHANGE_CONTRACT_SCHEMA_VERSION,
        "change_id": change_id,
        "source_change_request_path": source_change_request_path,
        "goal": parsed.goal,
        "scope_paths": parsed.scope_paths,
        "scope_missing": parsed.scope_missing,
        "non_goals": parsed.non_goals,
        "acceptance": parsed.acceptance,
        "created_at": iso,
    }


def _build_acceptance_criteria(parsed: ChangeRequest) -> dict[str, Any]:
    """Shape-compatible with the autonomous mode's acceptance-criteria.json
    so future change-mode wiring (RC-4A.2) can hand it to the same
    eval harness path."""
    return {
        "schema_version": 1,
        "criteria": [{"id": f"AC-{i+1:03d}", "text": text} for i, text in enumerate(parsed.acceptance)],
    }


def _render_implementation_plan_placeholder(
    parsed: ChangeRequest, scan: dict[str, Any], change_id: str
) -> str:
    """Deterministic, no-Codex placeholder. RC-4A.2 will replace this with
    a real plan derived from repo onboarding + change contract."""
    lines: list[str] = []
    lines.append(f"# Implementation Plan — {change_id}")
    lines.append("")
    lines.append(
        "RC-4A.1 placeholder. The real plan (file-touch list, suggested "
        "Codex prompt, eval harness invocation) lands in RC-4A.2 when "
        "change-mode wires into AutonomousController."
    )
    lines.append("")
    lines.append("## Goal")
    lines.append("")
    lines.append(parsed.goal)
    lines.append("")
    lines.append("## Declared scope")
    lines.append("")
    if parsed.scope_paths:
        for sp in parsed.scope_paths:
            lines.append(f"- `{sp}`")
    else:
        lines.append("- (no scope declared in change-request.md)")
        lines.append("")
        lines.append(
            "Operator action: edit `change-contract.json` `scope_paths` before running, "
            "OR rely on RC-4A.2's onboarding-derived suggestion when wiring lands."
        )
    lines.append("")
    lines.append("## Non-goals")
    lines.append("")
    if parsed.non_goals:
        for ng in parsed.non_goals:
            lines.append(f"- {ng}")
    else:
        lines.append("- (none declared)")
    lines.append("")
    lines.append("## Acceptance")
    lines.append("")
    for i, criterion in enumerate(parsed.acceptance, start=1):
        lines.append(f"- AC-{i:03d}: {criterion}")
    lines.append("")
    lines.append("## Project context (from repo-onboarding.md)")
    lines.append("")
    stack = scan.get("stack") or {}
    detected = [name for name, value in stack.items() if value is True]
    if detected:
        lines.append("Detected toolchain markers: " + ", ".join(sorted(detected)) + ".")
    else:
        lines.append("No toolchain markers detected — see repo-onboarding.md.")
    lines.append("")
    lines.append("## Status")
    lines.append("")
    lines.append("`ready_for_run` — Autonomous execution is not wired in RC-4A.1. `agent-studio change run` will fail clearly until RC-4A.2 ships.")
    return "\n".join(lines).rstrip() + "\n"
