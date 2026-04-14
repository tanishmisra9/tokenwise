from __future__ import annotations

from tokenwise.backend.config import build_model_registry
from tokenwise.backend.models.schemas import Provider, TokenPricing, TokenUsage
from tokenwise.backend.tracker.cost import compute_cost, summarise_run_stats


def test_zero_tokens_costs_zero_dollars():
    pricing = TokenPricing(input_per_million=2.5, output_per_million=10.0)

    assert compute_cost(TokenUsage(input=0, output=0), pricing) == 0.0


def test_one_million_input_and_output_tokens_returns_exact_expected_cost():
    pricing = TokenPricing(input_per_million=2.5, output_per_million=10.0)

    assert compute_cost(TokenUsage(input=1_000_000, output=1_000_000), pricing) == 12.5


def test_baseline_cost_uses_tier_three_pricing_contract(
    mock_settings,
    subtask_factory,
    route_factory,
    attempt_factory,
    subtask_result_factory,
):
    settings = mock_settings()
    registry = build_model_registry(settings)
    usage = TokenUsage(input=20_000, output=10_000)
    actual_cost = compute_cost(usage, registry["tier1_openai"].pricing)
    baseline_cost = compute_cost(usage, registry["tier3_openai"].pricing)

    result = subtask_result_factory(
        subtask=subtask_factory(),
        route=route_factory(tier=1, provider=Provider.OPENAI),
        attempts=[
            attempt_factory(
                tier=1,
                provider=Provider.OPENAI,
                model_alias="tier1_openai",
                model_id=settings.openai_tier1_model_id,
                usage=usage,
                cost_usd=actual_cost,
                baseline_cost_usd=baseline_cost,
            )
        ],
    )

    summary = summarise_run_stats([result])

    assert summary.actual_cost_usd == actual_cost
    assert summary.baseline_cost_usd == baseline_cost
    assert summary.baseline_cost_usd > summary.actual_cost_usd


def test_savings_percentage_uses_expected_formula_and_rounding(
    subtask_factory,
    subtask_result_factory,
    attempt_factory,
):
    result = subtask_result_factory(
        subtask=subtask_factory(),
        attempts=[
            attempt_factory(
                cost_usd=0.3,
                baseline_cost_usd=1.0,
                usage=TokenUsage(input=100, output=100),
                model_alias="tier2_openai",
                model_id="gpt-4o",
                tier=2,
            )
        ],
        final_output="done",
        status="completed",
    )

    summary = summarise_run_stats([result])

    assert summary.saved_usd == 0.7
    assert summary.savings_pct == 70.0


def test_summarise_run_stats_aggregates_multiple_subtask_results_correctly(
    subtask_factory,
    subtask_result_factory,
    attempt_factory,
):
    first = subtask_result_factory(
        subtask=subtask_factory(id="task_1"),
        attempts=[
            attempt_factory(
                tier=1,
                model_alias="tier1_openai",
                model_id="gpt-4o-mini",
                usage=TokenUsage(input=100, output=50),
                cost_usd=0.1,
                baseline_cost_usd=0.3,
            )
        ],
        escalations=1,
    )
    second = subtask_result_factory(
        subtask=subtask_factory(id="task_2"),
        attempts=[
            attempt_factory(
                tier=2,
                model_alias="tier2_openai",
                model_id="gpt-4o",
                usage=TokenUsage(input=200, output=100),
                cost_usd=0.2,
                baseline_cost_usd=0.4,
            )
        ],
        escalations=2,
    )

    summary = summarise_run_stats([first, second])

    assert summary.tokens_used == 450
    assert summary.actual_cost_usd == 0.3
    assert summary.baseline_cost_usd == 0.7
    assert summary.saved_usd == 0.4
    assert summary.models_used == {"tier1_openai": 1, "tier2_openai": 1}
    assert summary.escalations == 3


def test_negative_savings_is_clamped_to_zero(
    subtask_factory,
    subtask_result_factory,
    attempt_factory,
):
    result = subtask_result_factory(
        subtask=subtask_factory(),
        attempts=[
            attempt_factory(
                cost_usd=1.2,
                baseline_cost_usd=1.0,
                usage=TokenUsage(input=100, output=100),
            )
        ],
    )

    summary = summarise_run_stats([result])

    assert summary.saved_usd == 0.0
    assert summary.savings_pct == 0.0

