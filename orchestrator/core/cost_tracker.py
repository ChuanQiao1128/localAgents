from __future__ import annotations

from orchestrator.core.ids import now_iso, short_id
from orchestrator.db import Database


class CostTracker:
    def __init__(self, db: Database):
        self.db = db

    def record(
        self,
        *,
        provider: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        latency_ms: int,
        project_id: str | None = None,
        run_id: str | None = None,
        agent_id: str | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO costs (
                id, project_id, run_id, agent_id, provider, model, input_tokens,
                output_tokens, cost_usd, latency_ms, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                short_id("cost"),
                project_id,
                run_id,
                agent_id,
                provider,
                model,
                input_tokens,
                output_tokens,
                cost_usd,
                latency_ms,
                now_iso(),
            ),
        )

    def totals_for_run(self, run_id: str) -> dict[str, float | int]:
        row = self.db.query_one(
            """
            SELECT
                COALESCE(SUM(input_tokens), 0) AS input_tokens,
                COALESCE(SUM(output_tokens), 0) AS output_tokens,
                COALESCE(SUM(cost_usd), 0.0) AS cost_usd
            FROM costs
            WHERE run_id = ?
            """,
            (run_id,),
        )
        if not row:
            return {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        return {
            "input_tokens": int(row["input_tokens"]),
            "output_tokens": int(row["output_tokens"]),
            "cost_usd": float(row["cost_usd"]),
        }

