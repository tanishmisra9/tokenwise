from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from threading import Lock

from tokenwise.backend.models.schemas import HistoryResponse, HistoryRunSummary, HistoryStats, RunResult
from tokenwise.backend.utils import safe_preview


class HistoryStore:
    def __init__(self, db_path: str, *, recent_runs_limit: int = 8) -> None:
        self.db_path = db_path
        self.recent_runs_limit = recent_runs_limit
        self._lock = Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    run_id TEXT PRIMARY KEY,
                    task TEXT NOT NULL,
                    status TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    budget_cap_usd REAL NOT NULL,
                    budget_locked INTEGER NOT NULL,
                    final_output TEXT,
                    error TEXT,
                    plan_json TEXT,
                    run_stats_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS subtasks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id TEXT NOT NULL,
                    subtask_id TEXT NOT NULL,
                    description TEXT NOT NULL,
                    complexity TEXT NOT NULL,
                    output_format TEXT NOT NULL,
                    routing_hint TEXT NOT NULL,
                    status TEXT NOT NULL,
                    final_output TEXT,
                    route_json TEXT NOT NULL,
                    attempts_json TEXT NOT NULL,
                    escalations INTEGER NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES runs (run_id)
                );
                """
            )
            connection.commit()

    def write_run(self, result: RunResult) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM subtasks WHERE run_id = ?", (result.run_id,))
            connection.execute("DELETE FROM runs WHERE run_id = ?", (result.run_id,))
            connection.execute(
                """
                INSERT INTO runs (
                    run_id, task, status, started_at, completed_at, budget_cap_usd, budget_locked,
                    final_output, error, plan_json, run_stats_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.task,
                    result.status,
                    result.started_at,
                    result.completed_at,
                    result.budget_cap_usd,
                    int(result.budget_locked),
                    result.final_output,
                    result.error,
                    result.plan.model_dump_json() if result.plan else None,
                    result.run_stats.model_dump_json(),
                ),
            )

            for subtask_result in result.subtask_results:
                connection.execute(
                    """
                    INSERT INTO subtasks (
                        run_id, subtask_id, description, complexity, output_format, routing_hint,
                        status, final_output, route_json, attempts_json, escalations
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        result.run_id,
                        subtask_result.subtask.id,
                        subtask_result.subtask.description,
                        subtask_result.subtask.complexity.value,
                        subtask_result.subtask.output_format.value,
                        subtask_result.subtask.routing_hint.value,
                        subtask_result.status,
                        subtask_result.final_output,
                        subtask_result.route.model_dump_json(),
                        json.dumps([attempt.model_dump(mode="json") for attempt in subtask_result.attempts]),
                        subtask_result.escalations,
                    ),
                )
            connection.commit()

    def get_history_response(self) -> HistoryResponse:
        stats = self.get_history_stats()
        runs = self.list_recent_runs()
        return HistoryResponse(
            total_runs=stats.total_runs,
            total_tokens=stats.total_tokens,
            total_spent_usd=stats.total_spent_usd,
            total_saved_usd=stats.total_saved_usd,
            avg_savings_pct=stats.avg_savings_pct,
            runs=runs,
            routing_hint_breakdown=self.get_routing_hint_breakdown(),
        )

    def get_history_stats(self) -> HistoryStats:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT run_stats_json FROM runs WHERE status IN ('completed', 'failed')"
            ).fetchall()

        total_runs = len(row)
        total_tokens = 0
        total_spent = 0.0
        total_saved = 0.0
        total_savings_pct = 0.0

        for item in row:
            stats = json.loads(item["run_stats_json"])
            total_tokens += int(stats.get("tokens_used", 0))
            total_spent += float(stats.get("actual_cost_usd", 0.0))
            total_saved += float(stats.get("saved_usd", 0.0))
            total_savings_pct += float(stats.get("savings_pct", 0.0))

        avg_savings = round(total_savings_pct / total_runs, 2) if total_runs else 0.0
        return HistoryStats(
            total_runs=total_runs,
            total_tokens=total_tokens,
            total_spent_usd=round(total_spent, 6),
            total_saved_usd=round(total_saved, 6),
            avg_savings_pct=avg_savings,
        )

    def list_recent_runs(self) -> list[HistoryRunSummary]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT run_id, task, status, started_at, run_stats_json
                FROM runs
                ORDER BY started_at DESC
                LIMIT ?
                """,
                (self.recent_runs_limit,),
            ).fetchall()

        summaries: list[HistoryRunSummary] = []
        for row in rows:
            stats = json.loads(row["run_stats_json"])
            summaries.append(
                HistoryRunSummary(
                    run_id=row["run_id"],
                    task_preview=safe_preview(row["task"]),
                    status=row["status"],
                    created_at=row["started_at"],
                    actual_cost_usd=float(stats.get("actual_cost_usd", 0.0)),
                    saved_usd=float(stats.get("saved_usd", 0.0)),
                    savings_pct=float(stats.get("savings_pct", 0.0)),
                    tokens_used=int(stats.get("tokens_used", 0)),
                    escalations=int(stats.get("escalations", 0)),
                )
            )
        return summaries

    def get_started_today_spend_utc(self) -> float:
        now = datetime.now(timezone.utc)
        start = datetime(now.year, now.month, now.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)

        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT COALESCE(SUM(CAST(json_extract(run_stats_json, '$.actual_cost_usd') AS REAL)), 0.0) AS total
                FROM runs
                WHERE status IN ('completed', 'failed')
                  AND started_at >= ?
                  AND started_at < ?
                """,
                (start.isoformat(), end.isoformat()),
            ).fetchone()

        return round(float(row["total"] or 0.0), 6)

    def get_routing_hint_breakdown(self) -> dict[str, dict[str, float | int]]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT routing_hint, attempts_json
                FROM subtasks
                """
            ).fetchall()

        breakdown: dict[str, dict[str, float | int]] = {}
        actual_totals: dict[str, float] = {}
        baseline_totals: dict[str, float] = {}

        for row in rows:
            routing_hint = row["routing_hint"]
            attempts = json.loads(row["attempts_json"] or "[]")
            entry = breakdown.setdefault(
                routing_hint,
                {
                    "subtask_count": 0,
                    "avg_savings_pct": 0.0,
                },
            )
            entry["subtask_count"] = int(entry["subtask_count"]) + 1

            for attempt in attempts:
                actual_totals[routing_hint] = actual_totals.get(routing_hint, 0.0) + float(attempt.get("cost_usd", 0.0))
                baseline_totals[routing_hint] = baseline_totals.get(routing_hint, 0.0) + float(
                    attempt.get("baseline_cost_usd", 0.0)
                )

        for routing_hint, entry in breakdown.items():
            baseline_total = baseline_totals.get(routing_hint, 0.0)
            actual_total = actual_totals.get(routing_hint, 0.0)
            if baseline_total > 0:
                entry["avg_savings_pct"] = round(((baseline_total - actual_total) / baseline_total) * 100, 2)
            else:
                entry["avg_savings_pct"] = 0.0

        return breakdown
