from __future__ import annotations

from datetime import datetime, timedelta, timezone

from tokenwise.backend.models.schemas import RunStats, TokenUsage


def test_empty_db_returns_zero_totals_and_empty_runs(mock_settings, history_store_factory):
    history_store = history_store_factory(mock_settings())

    response = history_store.get_history_response()

    assert response.total_runs == 0
    assert response.total_tokens == 0
    assert response.total_spent_usd == 0.0
    assert response.total_saved_usd == 0.0
    assert response.avg_savings_pct == 0.0
    assert response.runs == []
    assert response.routing_hint_breakdown == {}


def test_writing_run_and_reading_it_back_returns_correct_fields(
    mock_settings,
    history_store_factory,
    run_result_factory,
    subtask_factory,
    route_factory,
    attempt_factory,
    subtask_result_factory,
):
    history_store = history_store_factory(mock_settings())
    subtask = subtask_factory(id="task_1")
    result = run_result_factory(
        run_id="run_1",
        task="Summarize a brief",
        subtask_results=[
            subtask_result_factory(
                subtask=subtask,
                route=route_factory(),
                attempts=[
                    attempt_factory(
                        usage=TokenUsage(input=100, output=50),
                        cost_usd=0.1,
                        baseline_cost_usd=0.2,
                    )
                ],
                final_output="Finished",
                status="completed",
            )
        ],
        run_stats=RunStats(
            tokens_used=150,
            actual_cost_usd=0.1,
            baseline_cost_usd=0.2,
            saved_usd=0.1,
            savings_pct=50.0,
        ),
    )

    history_store.write_run(result)
    response = history_store.get_history_response()

    assert response.total_runs == 1
    assert response.runs[0].run_id == "run_1"
    assert response.runs[0].actual_cost_usd == 0.1
    assert response.runs[0].tokens_used == 150


def test_total_saved_usd_accumulates_correctly_across_multiple_runs(
    mock_settings,
    history_store_factory,
    run_result_factory,
):
    history_store = history_store_factory(mock_settings())
    history_store.write_run(
        run_result_factory(run_id="run_1", run_stats=RunStats(saved_usd=0.3, savings_pct=30.0))
    )
    history_store.write_run(
        run_result_factory(run_id="run_2", run_stats=RunStats(saved_usd=0.7, savings_pct=70.0))
    )

    stats = history_store.get_history_stats()

    assert stats.total_saved_usd == 1.0


def test_avg_savings_pct_is_mean_of_per_run_savings(
    mock_settings,
    history_store_factory,
    run_result_factory,
):
    history_store = history_store_factory(mock_settings())
    history_store.write_run(
        run_result_factory(run_id="run_1", run_stats=RunStats(saved_usd=1.0, savings_pct=10.0))
    )
    history_store.write_run(
        run_result_factory(run_id="run_2", run_stats=RunStats(saved_usd=9.0, savings_pct=90.0))
    )

    stats = history_store.get_history_stats()

    assert stats.avg_savings_pct == 50.0


def test_routing_hint_breakdown_groups_subtasks_with_weighted_average_savings(
    mock_settings,
    history_store_factory,
    run_result_factory,
    subtask_factory,
    route_factory,
    attempt_factory,
    subtask_result_factory,
):
    history_store = history_store_factory(mock_settings())
    first_reasoning = subtask_result_factory(
        subtask=subtask_factory(id="task_1", routing_hint="general_reasoning"),
        route=route_factory(),
        attempts=[attempt_factory(cost_usd=0.1, baseline_cost_usd=0.2)],
        status="completed",
    )
    second_reasoning = subtask_result_factory(
        subtask=subtask_factory(id="task_2", routing_hint="general_reasoning"),
        route=route_factory(),
        attempts=[attempt_factory(cost_usd=0.2, baseline_cost_usd=0.5)],
        status="completed",
    )
    structured = subtask_result_factory(
        subtask=subtask_factory(id="task_3", routing_hint="structured_output"),
        route=route_factory(),
        attempts=[attempt_factory(cost_usd=0.4, baseline_cost_usd=0.4)],
        status="completed",
    )
    history_store.write_run(
        run_result_factory(
            run_id="run_1",
            subtask_results=[first_reasoning, second_reasoning, structured],
            run_stats=RunStats(saved_usd=0.4, savings_pct=36.36),
        )
    )

    breakdown = history_store.get_routing_hint_breakdown()

    assert breakdown["general_reasoning"]["subtask_count"] == 2
    assert breakdown["general_reasoning"]["avg_savings_pct"] == 57.14
    assert breakdown["structured_output"]["subtask_count"] == 1
    assert breakdown["structured_output"]["avg_savings_pct"] == 0.0


def test_runs_from_previous_utc_days_are_excluded_from_daily_spend(
    mock_settings,
    history_store_factory,
    run_result_factory,
):
    history_store = history_store_factory(mock_settings())
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    history_store.write_run(
        run_result_factory(
            run_id="run_old",
            started_at=yesterday,
            completed_at=yesterday,
            run_stats=RunStats(actual_cost_usd=1.25),
        )
    )

    assert history_store.get_started_today_spend_utc() == 0.0


def test_daily_spend_sums_only_todays_runs(mock_settings, history_store_factory, run_result_factory):
    history_store = history_store_factory(mock_settings())
    today = datetime.now(timezone.utc).isoformat()
    yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    history_store.write_run(
        run_result_factory(
            run_id="run_today_1",
            started_at=today,
            completed_at=today,
            run_stats=RunStats(actual_cost_usd=0.75),
        )
    )
    history_store.write_run(
        run_result_factory(
            run_id="run_today_2",
            started_at=today,
            completed_at=today,
            run_stats=RunStats(actual_cost_usd=1.25),
        )
    )
    history_store.write_run(
        run_result_factory(
            run_id="run_yesterday",
            started_at=yesterday,
            completed_at=yesterday,
            run_stats=RunStats(actual_cost_usd=9.0),
        )
    )

    assert history_store.get_started_today_spend_utc() == 2.0

