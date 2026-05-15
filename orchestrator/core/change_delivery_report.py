"""RC-4A.1: deterministic renderer for `delivery-report.md`.

The delivery report is the human-facing output of a change session.
It tells the operator what changed, whether the build/tests passed,
and which commit (if any) carries the change. It is rendered from a
`ChangeResult` dict that future RC-4A.2 wiring will populate from
the autonomous controller's final state.

For RC-4A.1 the renderer is the only piece that exists — it accepts
any well-shaped result dict and emits markdown. Tests construct
fake result dicts to verify the rendering paths for the three
operational outcomes:

  - completed             change applied + committed; build/tests passed
  - needs-human-review    change paused; review queue holds the decision
  - failed                change attempt failed (build error / corrupt patch / etc.)

Result dict shape (loose; missing keys render as "(not recorded)"):

    {
      "change_id": "change_<id>",
      "result": "completed" | "needs-human-review" | "failed",
      "goal": "...",
      "files_touched": ["path1", "path2"],
      "validation": {
          "build": {"passed": True/False, "command": "npm run build", "duration_sec": 12.3},
          ...
      },
      "risks": ["..."],
      "commit": {"branch": "agentic/change/change_<id>", "sha": "abc123", "message": "..."},
      "review_queue": {"open_count": 0, "items": [{"review_id": "...", "title": "..."}]},
      "elapsed_sec": 90.5,
      "created_at": "iso8601",
      "completed_at": "iso8601 or None",
    }
"""
from __future__ import annotations

from typing import Any


VALID_RESULTS = {"completed", "needs-human-review", "failed"}


def render_delivery_report(result: dict[str, Any]) -> str:
    """Render the supplied result dict to a deterministic markdown string.

    The renderer is forgiving: missing fields are surfaced as
    "(not recorded)" rather than raising. This lets RC-4A.2's wiring
    evolve incrementally without breaking the report shape.
    """
    if not isinstance(result, dict):
        raise TypeError(f"render_delivery_report expects a dict, got {type(result).__name__}")

    change_id = str(result.get("change_id") or "change_<unknown>")
    outcome = str(result.get("result") or "unknown")
    if outcome not in VALID_RESULTS:
        # Don't refuse — just flag it. The renderer's job is to emit
        # something diff-friendly even when the inputs are partial.
        outcome_display = f"{outcome} (NOT one of {sorted(VALID_RESULTS)})"
    else:
        outcome_display = outcome

    lines: list[str] = []
    lines.append(f"# Change Delivery Report — {change_id}")
    lines.append("")

    lines.append("## Goal")
    lines.append("")
    lines.append(_or_placeholder(result.get("goal")))
    lines.append("")

    lines.append("## Result")
    lines.append("")
    lines.append(f"**{outcome_display}**")
    lines.append("")

    lines.append("## What was changed")
    lines.append("")
    files = result.get("files_touched") or []
    if files:
        for path in files:
            lines.append(f"- `{path}`")
    else:
        lines.append("- (no files recorded as changed)")
    lines.append("")

    lines.append("## Validation")
    lines.append("")
    validation = result.get("validation") or {}
    if validation:
        for key in sorted(validation.keys()):
            entry = validation[key] or {}
            passed = entry.get("passed")
            cmd = entry.get("command")
            duration = entry.get("duration_sec")
            status = "passed" if passed is True else ("failed" if passed is False else "(not recorded)")
            cmd_str = f"`{cmd}`" if cmd else "(command not recorded)"
            duration_str = f" in {duration}s" if isinstance(duration, (int, float)) else ""
            lines.append(f"- **{key}**: {status} — {cmd_str}{duration_str}")
    else:
        lines.append("- (no validation results recorded)")
    lines.append("")

    lines.append("## Risks / observations")
    lines.append("")
    risks = result.get("risks") or []
    if risks:
        for risk in risks:
            lines.append(f"- {risk}")
    else:
        lines.append("- (none recorded)")
    lines.append("")

    lines.append("## Commit")
    lines.append("")
    commit = result.get("commit") or {}
    if commit:
        branch = commit.get("branch") or "(branch not recorded)"
        sha = commit.get("sha") or "(sha not recorded)"
        msg = commit.get("message") or "(message not recorded)"
        lines.append(f"- Branch: `{branch}`")
        lines.append(f"- SHA: `{sha}`")
        lines.append(f"- Message: {msg}")
    else:
        lines.append("- (no commit recorded — change was not applied)")
    lines.append("")

    review = result.get("review_queue") or {}
    if review:
        lines.append("## Review queue")
        lines.append("")
        open_count = int(review.get("open_count") or 0)
        lines.append(f"- Open items: {open_count}")
        items = review.get("items") or []
        for item in items:
            rid = item.get("review_id") or "(no id)"
            title = item.get("title") or "(no title)"
            lines.append(f"  - `{rid}` — {title}")
        lines.append("")

    lines.append("## Timing")
    lines.append("")
    elapsed = result.get("elapsed_sec")
    created_at = result.get("created_at")
    completed_at = result.get("completed_at")
    lines.append(f"- Elapsed: {elapsed if elapsed is not None else '(not recorded)'} seconds")
    lines.append(f"- Created at: {_or_placeholder(created_at)}")
    lines.append(f"- Completed at: {_or_placeholder(completed_at)}")
    lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _or_placeholder(value: Any) -> str:
    if value is None:
        return "(not recorded)"
    text = str(value).strip()
    return text or "(not recorded)"
