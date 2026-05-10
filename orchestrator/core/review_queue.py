"""Human Review Queue (MVP-4D).

Whenever the autonomous controller pauses for a reason that requires a
human decision, a structured `review-item.json` is written under

    .agent/autonomous/sessions/<session_id>/review-items/<review_id>.json

This module owns:
  - the schema + dataclass for a review item
  - filesystem CRUD (create / read / update / list)
  - lightweight predicates the controller uses for resume gating
    (e.g. `has_blocking_open_reviews`)

The review queue is intentionally NOT a database — every item is its own
JSON file so external tools (a future dashboard, a `git status`-style
inspection tool, even `jq`) can consume them without any orchestrator
runtime. Listing is `iterdir + json.load + sort by created_at`.

Design constraints (from MVP-4D spec):
  - approval is a HUMAN OVERRIDE, NOT a relaxation of the Promotion Gate.
    The promotion-report.json keeps its decision; the review item records
    `human_review_override=true` separately so audit trails distinguish
    "the gate said yes" from "a human said yes despite the gate".
  - reject / resolve must record an explicit `resolution` block (reason +
    timestamp + optional task action), so any later operator can read why
    a review was closed.
  - the queue is per-session. Cross-session aggregation is a future
    concern (analogous to the per-project abandonment log in MVP-3A/B).
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.core.ids import now_iso, short_id


SCHEMA_VERSION_REVIEW_ITEM = 1

# Allowed values — kept narrow so consumers can switch on them.
REVIEW_STATUSES: frozenset[str] = frozenset({"open", "approved", "rejected", "resolved"})
REVIEW_SEVERITIES: frozenset[str] = frozenset({"blocking", "warning", "info"})
REVIEW_SOURCE_TYPES: frozenset[str] = frozenset({
    "task_run",          # inner loop returned needs-human-review / needs-more-context
    "apply_failure",     # apply_selected_candidate raised
    "needs_more_context",
    "corrective_limit",  # too-many-corrective-tasks pause
    "integration_failure",  # reserved for future direct integration-failure reviews
    "deployment_failure",   # MVP-4E: vercel deploy or inspect refused
    "smoke_check_failure",  # MVP-4F: deploy ready but smoke check failed
    "rollback_failure",     # MVP-4F: vercel rollback itself failed after smoke failure
    "manual",            # user-created review (reserved)
})
REVIEW_REASON_CODES: frozenset[str] = frozenset({
    "needs-human-review",
    "needs-more-context",
    "failed-apply",
    "too-many-corrective-tasks",
    "corrective-task-needs-review",
    "deployment-failed",  # MVP-4E
    "smoke-check-failed",  # MVP-4F
    "rollback-failed",     # MVP-4F
})

DEFAULT_ALLOWED_ACTIONS: list[str] = ["show", "approve", "reject", "resolve"]


# ---------------------------------------------------------------------------
# Filesystem layout helpers
# ---------------------------------------------------------------------------
def review_items_dir(project_path: Path, session_id: str) -> Path:
    return project_path / ".agent" / "autonomous" / "sessions" / session_id / "review-items"


def review_item_file(project_path: Path, session_id: str, review_id: str) -> Path:
    return review_items_dir(project_path, session_id) / f"{review_id}.json"


def new_review_id() -> str:
    """Stable unique id like `review_xxxxxxxxxx`."""
    return short_id("review")


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------
@dataclass
class ReviewItem:
    schema_version: int
    review_id: str
    session_id: str
    project_id: str
    status: str       # open | approved | rejected | resolved
    severity: str     # blocking | warning | info
    source_type: str  # one of REVIEW_SOURCE_TYPES
    reason_code: str  # one of REVIEW_REASON_CODES
    title: str
    summary: str
    task_id: str | None = None
    run_id: str | None = None
    candidate_id: str | None = None
    promotion_decision: str | None = None
    source_failure_id: str | None = None
    evidence_paths: list[str] = field(default_factory=list)
    suggested_commands: list[str] = field(default_factory=list)
    allowed_actions: list[str] = field(default_factory=lambda: list(DEFAULT_ALLOWED_ACTIONS))
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    resolution: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "review_id": self.review_id,
            "session_id": self.session_id,
            "project_id": self.project_id,
            "status": self.status,
            "severity": self.severity,
            "source_type": self.source_type,
            "reason_code": self.reason_code,
            "title": self.title,
            "summary": self.summary,
            "task_id": self.task_id,
            "run_id": self.run_id,
            "candidate_id": self.candidate_id,
            "promotion_decision": self.promotion_decision,
            "source_failure_id": self.source_failure_id,
            "evidence_paths": list(self.evidence_paths),
            "suggested_commands": list(self.suggested_commands),
            "allowed_actions": list(self.allowed_actions),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "resolution": self.resolution,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "ReviewItem":
        return cls(
            schema_version=int(payload.get("schema_version") or SCHEMA_VERSION_REVIEW_ITEM),
            review_id=str(payload["review_id"]),
            session_id=str(payload["session_id"]),
            project_id=str(payload["project_id"]),
            status=str(payload.get("status") or "open"),
            severity=str(payload.get("severity") or "blocking"),
            source_type=str(payload.get("source_type") or "task_run"),
            reason_code=str(payload.get("reason_code") or "needs-human-review"),
            title=str(payload.get("title") or ""),
            summary=str(payload.get("summary") or ""),
            task_id=payload.get("task_id"),
            run_id=payload.get("run_id"),
            candidate_id=payload.get("candidate_id"),
            promotion_decision=payload.get("promotion_decision"),
            source_failure_id=payload.get("source_failure_id"),
            evidence_paths=list(payload.get("evidence_paths") or []),
            suggested_commands=list(payload.get("suggested_commands") or []),
            allowed_actions=list(payload.get("allowed_actions") or list(DEFAULT_ALLOWED_ACTIONS)),
            created_at=str(payload.get("created_at") or now_iso()),
            updated_at=str(payload.get("updated_at") or now_iso()),
            resolution=payload.get("resolution"),
        )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------
def create_review_item(project_path: Path, item: ReviewItem) -> Path:
    """Write a fresh review item to disk. Returns the file path."""
    if item.status not in REVIEW_STATUSES:
        raise ValueError(f"invalid review status: {item.status}")
    if item.severity not in REVIEW_SEVERITIES:
        raise ValueError(f"invalid review severity: {item.severity}")
    if item.source_type not in REVIEW_SOURCE_TYPES:
        raise ValueError(f"invalid review source_type: {item.source_type}")
    if item.reason_code not in REVIEW_REASON_CODES:
        raise ValueError(f"invalid review reason_code: {item.reason_code}")
    path = review_item_file(project_path, item.session_id, item.review_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = item.to_dict()
    # RC-2B.13: extend the RC-1.1.6 producer-side validation hook to the
    # review queue. When AGENT_STUDIO_VALIDATE_WRITES=1, run the consumer
    # validator (validate_review_item) BEFORE the file is written and
    # raise on any error. Default off — production write paths unchanged.
    # Lazy-import to avoid an artifact_validation → review_queue cycle.
    if os.environ.get("AGENT_STUDIO_VALIDATE_WRITES") == "1":
        from orchestrator.core import artifact_validation as _av
        from orchestrator.core.deploy import ProducerValidationFailed
        errors = _av.validate_review_item(payload)
        if errors:
            raise ProducerValidationFailed(
                "producer-side validation failed for validate_review_item:\n  - "
                + "\n  - ".join(errors)
            )
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def read_review_item(project_path: Path, session_id: str, review_id: str) -> ReviewItem | None:
    path = review_item_file(project_path, session_id, review_id)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict):
        return None
    return ReviewItem.from_dict(payload)


def list_review_items(
    project_path: Path,
    session_id: str,
    *,
    only_open: bool = False,
) -> list[ReviewItem]:
    """Return every review item for a session, oldest-first by `created_at`.
    Defensive: a corrupt or non-dict file is silently skipped."""
    root = review_items_dir(project_path, session_id)
    if not root.is_dir():
        return []
    items: list[ReviewItem] = []
    for path in root.iterdir():
        if not path.is_file() or path.suffix != ".json":
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        try:
            item = ReviewItem.from_dict(payload)
        except (KeyError, ValueError):
            continue
        if only_open and item.status != "open":
            continue
        items.append(item)
    items.sort(key=lambda r: r.created_at)
    return items


def update_review_item(project_path: Path, item: ReviewItem) -> Path:
    """Write an updated review item back to disk; refreshes `updated_at`."""
    item.updated_at = now_iso()
    return create_review_item(project_path, item)


# ---------------------------------------------------------------------------
# Predicates the controller / CLI consult
# ---------------------------------------------------------------------------
def count_blocking_open(project_path: Path, session_id: str) -> int:
    """Count open review items whose severity is blocking. Used by resume
    gating to decide whether the controller can advance another task."""
    return sum(
        1 for item in list_review_items(project_path, session_id, only_open=True)
        if item.severity == "blocking"
    )


def has_blocking_open_reviews(project_path: Path, session_id: str) -> bool:
    return count_blocking_open(project_path, session_id) > 0


def list_blocking_open(project_path: Path, session_id: str) -> list[ReviewItem]:
    return [
        item for item in list_review_items(project_path, session_id, only_open=True)
        if item.severity == "blocking"
    ]
