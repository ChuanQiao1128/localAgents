from __future__ import annotations

from pathlib import Path
from typing import Any

from orchestrator.core.yaml_loader import load_yaml


class AgentRegistry:
    def __init__(self, agents_dir: Path):
        self.agents_dir = agents_dir

    def load_all(self) -> dict[str, dict[str, Any]]:
        agents: dict[str, dict[str, Any]] = {}
        for path in sorted(self.agents_dir.glob("*.yaml")):
            config = load_yaml(path)
            agent_id = str(config.get("id") or path.stem)
            agents[agent_id] = config
        return agents

    def require(self, agent_id: str) -> dict[str, Any]:
        agents = self.load_all()
        if agent_id not in agents:
            raise KeyError(f"Unknown agent: {agent_id}")
        return agents[agent_id]

