from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from orchestrator.core.cost_tracker import CostTracker
from orchestrator.model import ModelMessage, ModelRequest, ModelRouter


@dataclass(frozen=True)
class AgentContext:
    project_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    project_path: Path | None = None
    idea: str | None = None
    instructions: str = ""
    inputs: dict[str, str] = field(default_factory=dict)
    output_paths: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AgentResult:
    status: str
    summary: str
    artifacts: list[str] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    next_tasks: list[dict[str, Any]] = field(default_factory=list)
    requires_approval: bool = False
    # files maps required_output paths to the full text content the LLM
    # produced for them. The orchestrator writes these to disk because the
    # CLI adapters intentionally disable the LLM's filesystem tools.
    files: dict[str, str] = field(default_factory=dict)


class StructuredOutputParser:
    def parse_agent_result(self, content: str) -> AgentResult:
        payload = self._parse_json(content)
        files_payload = payload.get("files") or {}
        if isinstance(files_payload, dict):
            files = {str(k): str(v) for k, v in files_payload.items() if isinstance(v, str)}
        else:
            files = {}
        return AgentResult(
            status=str(payload.get("status", "completed")),
            summary=str(payload.get("summary", "")),
            artifacts=list(payload.get("artifacts") or []),
            tool_calls=list(payload.get("tool_calls") or []),
            next_tasks=list(payload.get("next_tasks") or []),
            requires_approval=bool(payload.get("requires_approval", False)),
            files=files,
        )

    def _parse_json(self, content: str) -> dict[str, Any]:
        try:
            loaded = json.loads(content)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", content, re.DOTALL)
            if not match:
                raise ValueError("Agent output did not contain a JSON object.")
            loaded = json.loads(match.group(0))
        if not isinstance(loaded, dict):
            raise ValueError("Agent output JSON must be an object.")
        return loaded


class AgentRunner:
    def __init__(
        self,
        router: ModelRouter | None = None,
        cost_tracker: CostTracker | None = None,
        parser: StructuredOutputParser | None = None,
    ):
        self.router = router or ModelRouter()
        self.cost_tracker = cost_tracker
        self.parser = parser or StructuredOutputParser()

    def run_task(self, agent_config: dict[str, Any], context: AgentContext) -> AgentResult:
        request = ModelRequest(
            model=str(agent_config.get("model", "local-stub")),
            temperature=float(agent_config.get("temperature", 0.2)),
            messages=[
                ModelMessage(role="system", content=str(agent_config.get("role", ""))),
                ModelMessage(role="user", content=self._render_prompt(agent_config, context)),
            ],
        )
        response = self.router.complete(request)
        if self.cost_tracker:
            self.cost_tracker.record(
                project_id=context.project_id,
                run_id=context.run_id,
                agent_id=str(agent_config.get("id", "unknown")),
                provider=response.provider,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost_usd=response.cost_usd,
                latency_ms=response.latency_ms,
            )
        return self.parser.parse_agent_result(response.content)

    def _render_prompt(self, agent_config: dict[str, Any], context: AgentContext) -> str:
        lines = [
            f"Agent: {agent_config.get('id', 'unknown')}",
            f"Task: {context.task_id or 'ad-hoc'}",
            f"Instructions: {context.instructions}",
        ]
        if context.idea:
            lines.append(f"Project idea: {context.idea}")
        if context.output_paths:
            lines.append("Required outputs (you MUST produce content for each):")
            lines.extend(f"- {path}" for path in context.output_paths)
        if context.inputs:
            lines.append("Inputs:")
            for name, value in context.inputs.items():
                lines.append(f"## {name}")
                lines.append(value)
        lines.append("")
        lines.append(
            'Return JSON only: {"status","summary","artifacts","files","tool_calls":[],"next_tasks":[],"requires_approval":false}. '
            "`files` maps each required-output path to its FULL text content (no '...', no abbreviation)."
        )
        return "\n".join(lines)

