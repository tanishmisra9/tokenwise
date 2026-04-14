from __future__ import annotations

import asyncio
import re
from unittest.mock import AsyncMock, Mock

import pytest

from tokenwise.backend.execution.runner import LLMRunner
from tokenwise.backend.models.schemas import (
    Complexity,
    LLMResponse,
    OutputFormat,
    Provider,
    QualityFloor,
    RoutingHint,
    RunRequest,
    TokenUsage,
    ValidationResult,
)


def extract_subtask_id(user_prompt: str) -> str:
    match = re.search(r"Assigned subtask \(([^)]+)\)", user_prompt)
    return match.group(1) if match else "unknown"


def backlog_events(coordinator, run_id: str):
    return coordinator.event_hub.ensure_run(run_id).backlog


def find_events(events, event_name: str):
    return [event for event in events if event.event.value == event_name]


@pytest.mark.asyncio
async def test_full_happy_path_emits_run_completed_with_correct_payload(
    coordinator_factory,
    mock_settings,
    mock_runner,
    llm_response_factory,
    subtask_factory,
    plan_factory,
):
    plan = plan_factory(
        [
            subtask_factory(id="task_1", complexity=Complexity.LOW),
            subtask_factory(id="task_2", complexity=Complexity.MEDIUM, depends_on=["task_1"]),
            subtask_factory(id="task_3", complexity=Complexity.LOW, depends_on=["task_2"]),
        ]
    )
    orchestrator = Mock()
    orchestrator.create_plan = AsyncMock(return_value=plan)
    validator = Mock()
    validator.validate = AsyncMock(return_value=ValidationResult(passed=True, reason="Looks good."))
    composer = Mock()
    composer.compose = AsyncMock(return_value="Final composed answer")
    mock_runner.queue_response(llm_response_factory("task 1 output"))
    mock_runner.queue_response(llm_response_factory("task 2 output"))
    mock_runner.queue_response(llm_response_factory("task 3 output"))
    coordinator = coordinator_factory(
        settings=mock_settings(),
        runner=mock_runner,
        orchestrator=orchestrator,
        validator=validator,
        composer=composer,
    )

    await coordinator.run("run_happy", RunRequest(task="Prepare an answer.", quality_floor=QualityFloor.MEDIUM))

    events = backlog_events(coordinator, "run_happy")
    assert [event.event.value for event in events][-1] == "run_completed"
    assert events[-1].payload["final_output"] == "Final composed answer"
    assert events[-1].payload["run_stats"]["tokens_used"] > 0


@pytest.mark.asyncio
async def test_budget_lock_force_routes_remaining_subtasks_to_tier_one(
    coordinator_factory,
    mock_settings,
    mock_runner,
    llm_response_factory,
    subtask_factory,
    plan_factory,
):
    plan = plan_factory(
        [
            subtask_factory(id="task_1", complexity=Complexity.LOW),
            subtask_factory(id="task_2", complexity=Complexity.MEDIUM, depends_on=["task_1"]),
            subtask_factory(id="task_3", complexity=Complexity.LOW, depends_on=["task_2"]),
        ]
    )
    orchestrator = Mock()
    orchestrator.create_plan = AsyncMock(return_value=plan)
    validator = Mock()
    validator.validate = AsyncMock(return_value=ValidationResult(passed=True, reason="Looks good."))
    composer = Mock()
    composer.compose = AsyncMock(return_value="Final output")

    expensive_response = llm_response_factory(
        "expensive output",
        input_tokens=5_000,
        output_tokens=5_000,
        latency_ms=200,
    )
    mock_runner.queue_response(expensive_response)
    mock_runner.queue_response(llm_response_factory("budget-locked task 2"))
    mock_runner.queue_response(llm_response_factory("budget-locked task 3"))
    coordinator = coordinator_factory(
        settings=mock_settings(),
        runner=mock_runner,
        orchestrator=orchestrator,
        validator=validator,
        composer=composer,
    )

    await coordinator.run(
        "run_budget_lock",
        RunRequest(task="Generate a layered answer.", budget_cap_usd=0.001, quality_floor=QualityFloor.MEDIUM),
    )

    events = backlog_events(coordinator, "run_budget_lock")
    budget_events = [
        event for event in events if event.event.value == "subtask_escalated" and event.payload.get("action") == "budget_lock"
    ]
    degraded_events = [
        event for event in events if event.event.value == "subtask_completed" and event.payload.get("degraded") is True
    ]
    assert budget_events
    assert degraded_events


@pytest.mark.asyncio
async def test_validation_failure_at_tier_three_emits_run_failed(
    coordinator_factory,
    mock_settings,
    mock_runner,
    llm_response_factory,
    subtask_factory,
    plan_factory,
):
    plan = plan_factory(
        [
            subtask_factory(id="task_1", complexity=Complexity.LOW),
            subtask_factory(id="task_2", complexity=Complexity.LOW, depends_on=["task_1"]),
            subtask_factory(id="task_3", complexity=Complexity.LOW, depends_on=["task_2"]),
        ]
    )
    orchestrator = Mock()
    orchestrator.create_plan = AsyncMock(return_value=plan)
    validator = Mock()
    validator.validate = AsyncMock(return_value=ValidationResult(passed=False, reason="Still not good enough."))
    composer = Mock()
    composer.compose = AsyncMock(return_value="unused")
    mock_runner.queue_response(llm_response_factory("first failed attempt"))
    mock_runner.queue_response(llm_response_factory("second failed attempt"))
    coordinator = coordinator_factory(
        settings=mock_settings(),
        runner=mock_runner,
        orchestrator=orchestrator,
        validator=validator,
        composer=composer,
    )

    await coordinator.run(
        "run_failure",
        RunRequest(task="Answer carefully.", quality_floor=QualityFloor.HIGH),
    )

    events = backlog_events(coordinator, "run_failure")
    event_names = [event.event.value for event in events]
    assert event_names[-1] == "run_failed"
    assert "run_completed" not in event_names


@pytest.mark.asyncio
async def test_orchestrator_failure_emits_run_failed_immediately(coordinator_factory, mock_settings):
    orchestrator = Mock()
    orchestrator.create_plan = AsyncMock(side_effect=RuntimeError("Planner unavailable"))
    validator = Mock()
    validator.validate = AsyncMock()
    composer = Mock()
    composer.compose = AsyncMock()
    runner = Mock()
    runner.generate = AsyncMock()
    coordinator = coordinator_factory(
        settings=mock_settings(),
        runner=runner,
        orchestrator=orchestrator,
        validator=validator,
        composer=composer,
    )

    await coordinator.run("run_orchestrator_error", RunRequest(task="Break down this task."))

    events = backlog_events(coordinator, "run_orchestrator_error")
    assert [event.event.value for event in events][-1] == "run_failed"
    runner.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_parallel_execution_launches_independent_subtasks_concurrently(
    coordinator_factory,
    mock_settings,
    subtask_factory,
    plan_factory,
):
    plan = plan_factory(
        [
            subtask_factory(id="task_1", complexity=Complexity.LOW),
            subtask_factory(id="task_2", complexity=Complexity.LOW),
            subtask_factory(id="task_3", complexity=Complexity.MEDIUM, depends_on=["task_1", "task_2"]),
        ]
    )
    active_calls = 0
    max_concurrent = 0

    async def generate(**kwargs):
        nonlocal active_calls, max_concurrent
        subtask_id = extract_subtask_id(kwargs["user_prompt"])
        active_calls += 1
        max_concurrent = max(max_concurrent, active_calls)
        try:
            if subtask_id in {"task_1", "task_2"}:
                await asyncio.sleep(0.05)
            return LLMResponse(
                output_text=f"{subtask_id} done",
                usage=TokenUsage(input=100, output=50),
                latency_ms=80,
            )
        finally:
            active_calls -= 1

    runner = Mock()
    runner.generate = AsyncMock(side_effect=generate)
    orchestrator = Mock()
    orchestrator.create_plan = AsyncMock(return_value=plan)
    validator = Mock()
    validator.validate = AsyncMock(return_value=ValidationResult(passed=True, reason="Looks good."))
    composer = Mock()
    composer.compose = AsyncMock(return_value="Composed output")
    coordinator = coordinator_factory(
        settings=mock_settings(),
        runner=runner,
        orchestrator=orchestrator,
        validator=validator,
        composer=composer,
    )

    await coordinator.run("run_parallel", RunRequest(task="Gather two inputs then synthesize.", quality_floor=QualityFloor.LOW))

    assert max_concurrent >= 2


@pytest.mark.asyncio
async def test_cancellation_mid_run_emits_run_failed(
    coordinator_factory,
    mock_settings,
    subtask_factory,
    plan_factory,
):
    plan = plan_factory(
        [
            subtask_factory(id="task_1", complexity=Complexity.LOW),
            subtask_factory(id="task_2", complexity=Complexity.LOW, depends_on=["task_1"]),
            subtask_factory(id="task_3", complexity=Complexity.LOW, depends_on=["task_2"]),
        ]
    )

    async def generate(**kwargs):
        await asyncio.sleep(0.05)
        subtask_id = extract_subtask_id(kwargs["user_prompt"])
        return LLMResponse(
            output_text=f"{subtask_id} finished",
            usage=TokenUsage(input=100, output=50),
            latency_ms=75,
        )

    runner = Mock()
    runner.generate = AsyncMock(side_effect=generate)
    orchestrator = Mock()
    orchestrator.create_plan = AsyncMock(return_value=plan)
    validator = Mock()
    validator.validate = AsyncMock(return_value=ValidationResult(passed=True, reason="Looks good."))
    composer = Mock()
    composer.compose = AsyncMock(return_value="unused")
    coordinator = coordinator_factory(
        settings=mock_settings(),
        runner=runner,
        orchestrator=orchestrator,
        validator=validator,
        composer=composer,
    )

    run_task = asyncio.create_task(coordinator.run("run_cancel", RunRequest(task="Cancel this run.")))
    await asyncio.sleep(0.01)
    coordinator.event_hub.cancel("run_cancel")
    await run_task

    events = backlog_events(coordinator, "run_cancel")
    assert [event.event.value for event in events][-1] == "run_failed"
    assert events[-1].payload["error"] == "Run cancelled by user"


@pytest.mark.asyncio
async def test_composer_validation_failure_retries_with_reason_and_succeeds(
    coordinator_factory,
    mock_settings,
    mock_runner,
    llm_response_factory,
    subtask_factory,
    plan_factory,
):
    plan = plan_factory()
    orchestrator = Mock()
    orchestrator.create_plan = AsyncMock(return_value=plan)

    async def validate(subtask, output_text, routing_hint, output_format):
        if subtask.id == "composer":
            if output_text == "first draft":
                return ValidationResult(passed=False, reason="Needs tighter synthesis.")
            return ValidationResult(passed=True, reason="Looks good.")
        return ValidationResult(passed=True, reason="Looks good.")

    validator = Mock()
    validator.validate = AsyncMock(side_effect=validate)
    composer = Mock()
    composer.compose = AsyncMock(side_effect=["first draft", "second draft"])
    mock_runner.queue_response(llm_response_factory("task 1"))
    mock_runner.queue_response(llm_response_factory("task 2"))
    mock_runner.queue_response(llm_response_factory("task 3"))
    coordinator = coordinator_factory(
        settings=mock_settings(),
        runner=mock_runner,
        orchestrator=orchestrator,
        validator=validator,
        composer=composer,
    )

    await coordinator.run("run_compose_retry", RunRequest(task="Compose carefully."))

    events = backlog_events(coordinator, "run_compose_retry")
    assert [event.event.value for event in events][-1] == "run_completed"
    assert events[-1].payload["final_output"] == "second draft"
    assert composer.compose.await_count == 2
    assert composer.compose.await_args_list[1].kwargs["revision_feedback"] == "Needs tighter synthesis."


@pytest.mark.asyncio
async def test_timeout_on_tier_one_triggers_provider_fallback(
    coordinator_factory,
    mock_settings,
    subtask_factory,
    plan_factory,
):
    plan = plan_factory(
        [
            subtask_factory(id="task_1", complexity=Complexity.LOW, routing_hint=RoutingHint.GENERAL_REASONING),
            subtask_factory(id="task_2", complexity=Complexity.LOW, depends_on=["task_1"]),
            subtask_factory(id="task_3", complexity=Complexity.LOW, depends_on=["task_2"]),
        ]
    )
    call_counts: dict[tuple[str, str], int] = {}

    async def generate(**kwargs):
        subtask_id = extract_subtask_id(kwargs["user_prompt"])
        key = (subtask_id, kwargs["provider"].value)
        call_counts[key] = call_counts.get(key, 0) + 1
        if subtask_id == "task_1" and kwargs["provider"] == Provider.OPENAI and call_counts[key] == 1:
            await asyncio.sleep(0.05)
        return LLMResponse(
            output_text=f"{subtask_id} by {kwargs['provider'].value}",
            usage=TokenUsage(input=100, output=50),
            latency_ms=75,
        )

    runner = Mock()
    runner.generate = AsyncMock(side_effect=generate)
    orchestrator = Mock()
    orchestrator.create_plan = AsyncMock(return_value=plan)
    validator = Mock()
    validator.validate = AsyncMock(return_value=ValidationResult(passed=True, reason="Looks good."))
    composer = Mock()
    composer.compose = AsyncMock(return_value="Timeout recovered")
    coordinator = coordinator_factory(
        settings=mock_settings(tier1_timeout_seconds=0.01),
        runner=runner,
        orchestrator=orchestrator,
        validator=validator,
        composer=composer,
    )

    await coordinator.run("run_timeout", RunRequest(task="Handle timeout.", quality_floor=QualityFloor.LOW))

    events = backlog_events(coordinator, "run_timeout")
    fallback_events = [
        event for event in events if event.event.value == "subtask_escalated" and event.payload.get("action") == "provider_fallback"
    ]
    assert fallback_events
    assert [event.event.value for event in events][-1] == "run_completed"


@pytest.mark.asyncio
async def test_empty_runner_output_retries_once_without_triggering_escalation(
    coordinator_factory,
    mock_settings,
    llm_response_factory,
    subtask_factory,
    plan_factory,
):
    plan = plan_factory(
        [
            subtask_factory(id="task_1", complexity=Complexity.LOW, routing_hint=RoutingHint.GENERAL_REASONING),
            subtask_factory(id="task_2", complexity=Complexity.LOW, depends_on=["task_1"]),
            subtask_factory(id="task_3", complexity=Complexity.LOW, depends_on=["task_2"]),
        ]
    )
    runner = LLMRunner(mock_settings())
    runner._call_openai = AsyncMock(
        side_effect=[
            llm_response_factory("   "),
            llm_response_factory("task_1 recovered"),
            llm_response_factory("task_2 recovered"),
            llm_response_factory("task_3 recovered"),
        ]
    )
    runner._call_anthropic = AsyncMock()
    orchestrator = Mock()
    orchestrator.create_plan = AsyncMock(return_value=plan)
    validator = Mock()
    validator.validate = AsyncMock(return_value=ValidationResult(passed=True, reason="Looks good."))
    composer = Mock()
    composer.compose = AsyncMock(return_value="Recovered output")
    coordinator = coordinator_factory(
        settings=mock_settings(),
        runner=runner,
        orchestrator=orchestrator,
        validator=validator,
        composer=composer,
    )

    await coordinator.run("run_empty_retry", RunRequest(task="Recover from transient empty output."))

    events = backlog_events(coordinator, "run_empty_retry")
    escalation_events = [event for event in events if event.event.value == "subtask_escalated"]

    assert [event.event.value for event in events][-1] == "run_completed"
    assert not escalation_events
    assert runner._call_openai.await_count == 4
    composed_results = composer.compose.await_args.args[1]
    assert composed_results[0].final_output == "task_1 recovered"


@pytest.mark.asyncio
async def test_tier_three_subtasks_use_configured_max_output_tokens(
    coordinator_factory,
    mock_settings,
    subtask_factory,
    plan_factory,
):
    plan = plan_factory(
        [
            subtask_factory(id="task_1", complexity=Complexity.HIGH, routing_hint=RoutingHint.GENERAL_REASONING),
            subtask_factory(id="task_2", complexity=Complexity.LOW, depends_on=["task_1"]),
            subtask_factory(id="task_3", complexity=Complexity.LOW, depends_on=["task_2"]),
        ]
    )
    captured_tokens: list[int] = []

    async def generate(**kwargs):
        captured_tokens.append(kwargs["max_output_tokens"])
        subtask_id = extract_subtask_id(kwargs["user_prompt"])
        return LLMResponse(
            output_text=f"{subtask_id} done",
            usage=TokenUsage(input=100, output=50),
            latency_ms=75,
        )

    runner = Mock()
    runner.generate = AsyncMock(side_effect=generate)
    orchestrator = Mock()
    orchestrator.create_plan = AsyncMock(return_value=plan)
    validator = Mock()
    validator.validate = AsyncMock(return_value=ValidationResult(passed=True, reason="Looks good."))
    composer = Mock()
    composer.compose = AsyncMock(return_value="Tiered output")
    coordinator = coordinator_factory(
        settings=mock_settings(
            tier1_max_output_tokens=1500,
            tier2_max_output_tokens=2000,
            tier3_max_output_tokens=4000,
        ),
        runner=runner,
        orchestrator=orchestrator,
        validator=validator,
        composer=composer,
    )

    await coordinator.run("run_tier_tokens", RunRequest(task="Produce long technical outputs.", quality_floor=QualityFloor.LOW))

    assert captured_tokens[0] == 4000
    assert captured_tokens[1] == 1500
    assert captured_tokens[2] == 1500
