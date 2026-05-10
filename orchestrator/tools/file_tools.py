from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from orchestrator.core.permission_engine import PermissionEngine


@dataclass(frozen=True)
class FileToolResult:
    path: str
    ok: bool
    message: str


class FileTools:
    def __init__(
        self,
        root: Path,
        allowed_write_patterns: list[str] | None = None,
        denied_patterns: list[str] | None = None,
    ):
        self.root = root.resolve()
        self.allowed_write_patterns = allowed_write_patterns or []
        self.denied_patterns = denied_patterns or [".env", "~/**"]
        self.permissions = PermissionEngine()

    def read_text(self, relative_path: str) -> str:
        path = self._resolve_inside_root(relative_path)
        return path.read_text(encoding="utf-8")

    def write_text(self, relative_path: str, content: str) -> FileToolResult:
        path = self._resolve_inside_root(relative_path)
        normalized = path.relative_to(self.root).as_posix()
        if not self.permissions.can_write(
            normalized,
            self.allowed_write_patterns,
            self.denied_patterns,
        ):
            return FileToolResult(
                path=normalized,
                ok=False,
                message=f"Write denied by allowed_paths policy: {normalized}",
            )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return FileToolResult(path=normalized, ok=True, message="written")

    def _resolve_inside_root(self, relative_path: str) -> Path:
        path = (self.root / relative_path).resolve()
        try:
            path.relative_to(self.root)
        except ValueError as exc:
            raise PermissionError(f"Path escapes project root: {relative_path}") from exc
        return path

