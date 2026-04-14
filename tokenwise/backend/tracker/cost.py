from __future__ import annotations

from tokenwise.backend.models.schemas import RunStats, SubTaskResult, TokenUsage


def compute_cost(usage: TokenUsage, pricing) -> float:
    return round(
        ((usage.input / 1_000_000) * pricing.input_per_million)
        + ((usage.output / 1_000_000) * pricing.output_per_million),
        6,
    )


def summarise_run_stats(subtask_results: list[SubTaskResult]) -> RunStats:
    tokens_used = 0
    actual_cost = 0.0
    baseline_cost = 0.0
    escalations = 0
    models_used: dict[str, int] = {}

    for result in subtask_results:
        escalations += result.escalations
        for attempt in result.attempts:
            tokens_used += attempt.usage.total
            actual_cost += attempt.cost_usd
            baseline_cost += attempt.baseline_cost_usd
            models_used[attempt.model_alias] = models_used.get(attempt.model_alias, 0) + 1

    saved = max(0.0, baseline_cost - actual_cost)
    savings_pct = round((saved / baseline_cost) * 100, 2) if baseline_cost else 0.0

    return RunStats(
        tokens_used=tokens_used,
        actual_cost_usd=round(actual_cost, 6),
        baseline_cost_usd=round(baseline_cost, 6),
        saved_usd=round(saved, 6),
        savings_pct=savings_pct,
        models_used=models_used,
        escalations=escalations,
    )

