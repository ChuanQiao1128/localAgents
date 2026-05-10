from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def state_dir(self) -> Path:
        return self.root / ".agent-studio"

    @property
    def db_path(self) -> Path:
        return self.state_dir / "agent_studio.sqlite3"

    @property
    def projects_dir(self) -> Path:
        return self.state_dir / "projects"

    @property
    def agents_dir(self) -> Path:
        return self.root / "agents"

    @property
    def workflows_dir(self) -> Path:
        return self.root / "workflows"

    @property
    def env_file(self) -> Path:
        return self.root / ".env.local"


def resolve_paths(root: str | Path | None = None) -> AppPaths:
    return AppPaths(root=Path(root or Path.cwd()).resolve())


def load_local_env(paths: AppPaths) -> None:
    if not paths.env_file.exists():
        return
    for raw_line in paths.env_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
