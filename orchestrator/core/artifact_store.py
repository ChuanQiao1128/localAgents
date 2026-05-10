from __future__ import annotations

import json
from pathlib import Path

from orchestrator.core.ids import now_iso, short_id
from orchestrator.db import Database


class ArtifactStore:
    def __init__(self, db: Database):
        self.db = db

    def write_outputs(
        self,
        *,
        project_id: str,
        run_id: str,
        phase_id: str,
        project_path: Path,
        idea: str,
        outputs: list[str],
    ) -> list[str]:
        written: list[str] = []
        for output in outputs:
            relative = output.format(run_id=run_id)
            path = project_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            content = render_stub_artifact(phase_id, relative, idea, run_id)
            path.write_text(content, encoding="utf-8")
            written.append(relative)
            self.register(
                project_id=project_id,
                run_id=run_id,
                phase_id=phase_id,
                path=relative,
                kind=_artifact_kind(relative),
                summary=f"Generated {relative} for {phase_id}.",
            )
        return written

    def register(
        self,
        *,
        project_id: str,
        run_id: str,
        phase_id: str,
        path: str,
        kind: str,
        summary: str,
        source_type: str = "unknown",
        trust_level: str = "medium",
        validation_status: str = "not_run",
        validation_score: int | None = None,
        repair_attempt: int = 0,
    ) -> None:
        """Record an artifact write with provenance.

        ``source_type``: ``llm`` | ``stub`` | ``fallback`` | ``extra`` | ``repaired`` | ``human``
        ``trust_level``: ``high`` | ``medium`` | ``low`` | ``untrusted``
        ``validation_status``: ``passed`` | ``partial`` | ``failed`` | ``not_run``
        ``validation_score``: 0-100 from the validator suite, or None if no
        validators ran for this artifact's path.
        ``repair_attempt``: 0 for the first write, 1+ for repaired versions.

        C0c — idempotency: any prior ``(run_id, phase_id, path)`` rows are
        marked ``is_current=0``; the new row is the only ``is_current=1``
        entry for that key. final-run-status.md and ``diagnose`` only read
        current rows, so a re-run of the same phase (resume / repair / retry)
        does not double-count or expose stale provenance.
        """
        self.db.execute(
            "UPDATE artifacts SET is_current = 0 "
            "WHERE run_id = ? AND phase_id = ? AND path = ?",
            (run_id, phase_id, path),
        )
        self.db.execute(
            """
            INSERT INTO artifacts (
                id, project_id, run_id, phase_id, path, kind, summary, created_at,
                source_type, trust_level, validation_status, validation_score,
                is_current, repair_attempt
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                short_id("artifact"),
                project_id,
                run_id,
                phase_id,
                path,
                kind,
                summary,
                now_iso(),
                source_type,
                trust_level,
                validation_status,
                validation_score,
                1,  # is_current
                repair_attempt,
            ),
        )

    def list_for_run(self, run_id: str, *, include_history: bool = False) -> list[dict[str, str]]:
        if include_history:
            rows = self.db.query_all(
                "SELECT * FROM artifacts WHERE run_id = ? ORDER BY created_at, path",
                (run_id,),
            )
        else:
            rows = self.db.query_all(
                "SELECT * FROM artifacts WHERE run_id = ? AND COALESCE(is_current, 1) = 1 "
                "ORDER BY created_at, path",
                (run_id,),
            )
        return [dict(row) for row in rows]


def _wrap_json(content: str, marker_reason: str) -> str:
    try:
        loaded = json.loads(content) if content.strip() else {}
    except Exception:  # noqa: BLE001 — fall through to plain wrap
        loaded = None
    meta = {
        "_artifact_meta": {
            "artifact_status": "degraded_fallback",
            "trusted": False,
            "reason": marker_reason,
            "human_review_required": True,
        }
    }
    if isinstance(loaded, dict):
        return json.dumps({**meta, **loaded}, ensure_ascii=False, indent=2) + "\n"
    return json.dumps(
        {**meta, "data": loaded if loaded is not None else content},
        ensure_ascii=False,
        indent=2,
    ) + "\n"


def _wrap_yaml(content: str, marker_reason: str) -> str:
    """Inject artifact_meta as a top-level YAML node, preserving validity.

    We try to parse and re-emit as YAML when PyYAML is available; otherwise
    we fall back to prepending a yaml-comment block (which keeps the file
    parseable but doesn't surface the meta as data).
    """
    try:
        import yaml  # type: ignore[import-not-found]
    except ImportError:
        return _yaml_comment_header(marker_reason) + content
    try:
        loaded = yaml.safe_load(content) if content.strip() else None
    except Exception:  # noqa: BLE001
        return _yaml_comment_header(marker_reason) + content
    meta_node = {
        "artifact_meta": {
            "artifact_status": "degraded_fallback",
            "trusted": False,
            "reason": marker_reason,
            "human_review_required": True,
        }
    }
    if isinstance(loaded, dict):
        merged = {**meta_node, **loaded}
        return yaml.safe_dump(merged, sort_keys=False, default_flow_style=False)
    # Lists or scalars: keep as-is but prepend the comment block.
    return _yaml_comment_header(marker_reason) + content


def _yaml_comment_header(marker_reason: str) -> str:
    return (
        "# ---\n"
        "# artifact_status: degraded_fallback\n"
        "# trusted: false\n"
        f"# reason: {marker_reason}\n"
        "# human_review_required: true\n"
        "# ---\n"
    )


def _wrap_sql(content: str, marker_reason: str) -> str:
    return (
        "-- ---\n"
        "-- artifact_status: degraded_fallback\n"
        "-- trusted: false\n"
        f"-- reason: {marker_reason}\n"
        "-- human_review_required: true\n"
        "-- ---\n"
        + content
    )


def _wrap_hash_comment_code(content: str, marker_reason: str) -> str:
    """Used for languages whose comment marker is `#` (Python, Ruby, shell)."""
    return (
        "# ---\n"
        "# artifact_status: degraded_fallback\n"
        "# trusted: false\n"
        f"# reason: {marker_reason}\n"
        "# human_review_required: true\n"
        "# ---\n"
        + content
    )


def _wrap_slash_comment_code(content: str, marker_reason: str) -> str:
    """For C / JS / TS / Java / Go / Rust / Swift — //-style line comments."""
    return (
        "// ---\n"
        "// artifact_status: degraded_fallback\n"
        "// trusted: false\n"
        f"// reason: {marker_reason}\n"
        "// human_review_required: true\n"
        "// ---\n"
        + content
    )


def _wrap_markdown_or_text(content: str, marker_reason: str) -> str:
    header = (
        "---\n"
        "artifact_status: degraded_fallback\n"
        "trusted: false\n"
        f"reason: {marker_reason}\n"
        "human_review_required: true\n"
        "---\n\n"
        "<!-- AUTO-GENERATED FALLBACK · NOT TRUSTED · DO NOT TREAT CONTENT BELOW AS REAL DELIVERABLE -->\n\n"
    )
    return header + content


# Registered wrappers by extension. Add new formats here without touching the
# public API. The wrapping function MUST keep the file valid (parseable) for
# its format whenever possible; fallback to comment-prefixed wrappers when
# parsing isn't available or the body isn't a top-level mapping.
_WRAPPERS: dict[str, Any] = {
    "json": _wrap_json,
    "yaml": _wrap_yaml,
    "yml": _wrap_yaml,
    "sql": _wrap_sql,
    "py": _wrap_hash_comment_code,
    "rb": _wrap_hash_comment_code,
    "sh": _wrap_hash_comment_code,
    "bash": _wrap_hash_comment_code,
    "zsh": _wrap_hash_comment_code,
    "toml": _wrap_hash_comment_code,
    "js": _wrap_slash_comment_code,
    "jsx": _wrap_slash_comment_code,
    "ts": _wrap_slash_comment_code,
    "tsx": _wrap_slash_comment_code,
    "java": _wrap_slash_comment_code,
    "go": _wrap_slash_comment_code,
    "rs": _wrap_slash_comment_code,
    "c": _wrap_slash_comment_code,
    "cpp": _wrap_slash_comment_code,
    "h": _wrap_slash_comment_code,
    "hpp": _wrap_slash_comment_code,
    "swift": _wrap_slash_comment_code,
    "kt": _wrap_slash_comment_code,
}


def wrap_with_untrusted_frontmatter(content: str, *, path: str, reason: str) -> str:
    """Mark fallback / degraded artifact content as untrusted.

    Format-preserving: each registered wrapper keeps the file valid for its
    format (parseable JSON / YAML / SQL / source code) while attaching an
    ``artifact_meta`` block visible to both humans and downstream parsers.

    Unknown extensions (markdown, text, default) get a markdown-style
    YAML-frontmatter block followed by an HTML-comment banner.
    """
    suffix = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    marker_reason = (reason or "").replace("\n", " ").strip()[:300] or "unspecified"
    wrapper = _WRAPPERS.get(suffix, _wrap_markdown_or_text)
    return wrapper(content, marker_reason)


def _artifact_kind(path: str) -> str:
    if path.endswith(".json"):
        return "json"
    if path.endswith((".yaml", ".yml")):
        return "yaml"
    if path.endswith(".md"):
        return "markdown"
    return "file"


def render_stub_artifact(phase_id: str, relative_path: str, idea: str, run_id: str) -> str:
    if relative_path.endswith("generated-tasks.json"):
        return json.dumps(_generated_tasks(), ensure_ascii=False, indent=2) + "\n"
    if relative_path.endswith("api.openapi.yaml"):
        return _openapi_stub(idea)
    if relative_path.endswith("database-schema.md"):
        return _database_schema_stub(idea)
    if relative_path.endswith("implementation-summary.md"):
        return _implementation_summary()
    if relative_path.endswith("final-report.md"):
        return _final_report(idea, run_id)

    title = {
        "intake": "Project Brief",
        "research": "Product Research",
        "prd": "Product Requirements",
        "design": "Design Notes",
        "architecture": "Architecture",
        "qa": "QA Results",
        "review": "Review Report",
        "merge": "Final Report",
    }.get(phase_id, phase_id.title())
    return f"""# {title}

Project idea: {idea}

Status: generated by the local deterministic MVP stub.

## Notes

- This artifact proves the workflow, artifact registry, and file output path are wired.
- A later agent runtime will replace this stub with model-generated content.
"""


def _generated_tasks() -> list[dict[str, object]]:
    return [
        {
            "id": "SETUP-001",
            "title": "Initialize web and API project skeleton",
            "owner": "developer",
            "phase": "implementation",
            "depends_on": ["ARCH-001"],
            "priority": "high",
            "allowed_paths": ["apps/**", "packages/**", "tests/**"],
            "acceptance_criteria": ["Project skeleton exists", "Basic checks can run"],
            "test_commands": ["python3 -m unittest discover -s tests"],
        },
        {
            "id": "QA-001",
            "title": "Run MVP verification checks",
            "owner": "qa",
            "phase": "qa",
            "depends_on": ["SETUP-001"],
            "priority": "high",
            "allowed_paths": ["docs/qa/**", "tests/**"],
            "acceptance_criteria": ["All configured checks pass"],
            "test_commands": ["python3 -m unittest discover -s tests"],
        },
    ]


def _openapi_stub(idea: str) -> str:
    return f"""openapi: 3.1.0
info:
  title: Local Agent Dev Studio Generated API
  version: 0.1.0
  description: Stub API contract for {idea}
paths:
  /health:
    get:
      summary: Health check
      responses:
        "200":
          description: OK
"""


def _database_schema_stub(idea: str) -> str:
    return f"""# Database Schema

Project idea: {idea}

## Tables

- users
- projects
- domain_records

This is a placeholder schema until the Architect agent is connected.
"""


def _implementation_summary() -> str:
    return """# Implementation Summary

The Phase 1 deterministic MVP does not modify app code yet.

## Result

- Implementation task placeholder completed.
- Developer agent runtime will replace this with real code changes in Phase 2+.
"""


def _final_report(idea: str, run_id: str) -> str:
    return f"""# Final Report

Run: {run_id}

Project idea: {idea}

## Implemented

- Workflow phases executed.
- Artifacts generated.
- PRD approval gate enforced.

## Not Implemented Yet

- Real LLM agent execution.
- Real application code generation.
- Sandbox and worktree isolation.
"""

