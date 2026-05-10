from __future__ import annotations

from fnmatch import fnmatch
from pathlib import PurePosixPath


class PermissionEngine:
    def can_write(self, path: str, allowed_patterns: list[str], denied_patterns: list[str] | None = None) -> bool:
        normalized = PurePosixPath(path).as_posix()
        denied = denied_patterns or []
        if any(fnmatch(normalized, pattern) for pattern in denied):
            return False
        return any(fnmatch(normalized, pattern) for pattern in allowed_patterns)

