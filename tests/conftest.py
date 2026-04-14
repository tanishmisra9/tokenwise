from __future__ import annotations

import inspect
import itertools
from collections import deque
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest

from tokenwise.backend.config import Settings
from tokenwise.backend.models.schemas import (
    Complexity,
    ExecutionPlan,
    LLMResponse,
    OutputFormat,
    Provider,
    RouteDecision,
    RunResult,
    RunStats,
    SubTask,
    SubTaskAttempt,
    SubTaskResult,
    TokenUsage,
    utcnow_iso,
)
from tokenwise.backend.runtime import TokenwiseCoordinator
from tokenwise.backend.tracker.history import HistoryStore


class MockRunnerHarness:
    def __init__(self, default_response: LLMResponse) -> None:
        self._default_response = default_response
        self._queue: deque[object] = deque()
        self.generate = AsyncMock(side_effect=self._dispatch)

    def queue_response(self, response: LLMResponse) -> None:
        self._queue.append(response)

    def queue_exception(self, error: BaseException) -> None:
        self._queue.append(error)

    def queue_callable(self, callback) -> None:
        self._queue.append(callback)

    def set_default_response(self, response: LLMResponse) -> None:
        self._default_response = response

    async def _dispatch(self, *args, **kwargs) -> LLMResponse:
        if self._queue:
            item = self._queue.popleft()
            if isinstance(item, BaseException):
                raise item
            if callable(item):
                result = item(*args, **kwargs)
                if inspect.isawaitable(result):
                    return await result
                return result
            return item  # type: ignore[return-value]
        return self._default_response


@pytest.fixture
def llm_response_factory():
    def _make(
        output_text: str = "mock output",
        *,
        input_tokens: int = 120,
        output_tokens: int = 80,
        latency_ms: int = 125,
    ) -> LLMResponse:
        return LLMResponse(
            output_text=output_text,
            usage=TokenUsage(input=input_tokens, output=output_tokens),
            latency_ms=latency_ms,
        )

    return _make


@pytest.fixture
def mock_runner(llm_response_factory) -> MockRunnerHarness:
    return MockRunnerHarness(llm_response_factory())


@pytest.fixture
def mock_settings(tmp_path):
    counter = itertools.count(1)

    def _make(**overrides) -> Settings:
        db_path = tmp_path / f"tokenwise-test-{next(counter)}.db"
        defaults = {
            "db_path": str(db_path),
            "openai_api_key": None,
            "anthropic_api_key": None,
            "request_timeout_seconds": 0.1,
        }
        defaults.update(overrides)
        return Settings(**defaults)

    return _make


@pytest.fixture
def subtask_factory():
    counter = itertools.count(1)

    def _make(
        *,
        id: str | None = None,
        description: str | None = None,
        complexity: Complexity = Complexity.LOW,
        depends_on: list[str] | None = None,
        output_format: OutputFormat = OutputFormat.PARAGRAPH,
        routing_hint=None,
    ) -> SubTask:
        next_id = id or f"task_{next(counter)}"
        return SubTask(
            id=next_id,
            description=description or f"Description for {next_id}",
            complexity=complexity,
            depends_on=depends_on or [],
            output_format=output_format,
            routing_hint=routing_hint or "general_reasoning",
        )

    return _make


@pytest.fixture
def plan_factory(subtask_factory):
    def _make(subtasks: list[SubTask] | None = None) -> ExecutionPlan:
        if subtasks is None:
            first = subtask_factory(id="task_1", complexity=Complexity.LOW)
            second = subtask_factory(id="task_2", complexity=Complexity.MEDIUM, depends_on=["task_1"])
            third = subtask_factory(id="task_3", complexity=Complexity.LOW, depends_on=["task_2"])
            subtasks = [first, second, third]
        return ExecutionPlan(subtasks=subtasks)

    return _make


@pytest.fixture
def route_factory():
    def _make(
        *,
        tier: int = 1,
        provider: Provider = Provider.OPENAI,
        model_alias: str | None = None,
        model_id: str | None = None,
        routing_reason: str = "test route",
        forced_by_budget: bool = False,
    ) -> RouteDecision:
        alias = model_alias or f"tier{tier}_{provider.value}"
        resolved_model_id = model_id or {
            (1, Provider.OPENAI): "gpt-4o-mini",
            (2, Provider.OPENAI): "gpt-4o",
            (3, Provider.OPENAI): "o1",
            (1, Provider.ANTHROPIC): "claude-3-5-haiku-20241022",
            (2, Provider.ANTHROPIC): "claude-sonnet-4-20250514",
            (3, Provider.ANTHROPIC): "claude-opus-4-1-20250805",
        }[(tier, provider)]
        return RouteDecision(
            tier=tier,
            provider=provider,
            model_alias=alias,
            model_id=resolved_model_id,
            routing_reason=routing_reason,
            forced_by_budget=forced_by_budget,
        )

    return _make


@pytest.fixture
def attempt_factory():
    def _make(
        *,
        attempt_number: int = 1,
        tier: int = 1,
        provider: Provider = Provider.OPENAI,
        model_alias: str = "tier1_openai",
        model_id: str = "gpt-4o-mini",
        usage: TokenUsage | None = None,
        cost_usd: float = 0.0,
        baseline_cost_usd: float = 0.0,
        latency_ms: int = 0,
        output_text: str = "mock output",
        error: str | None = None,
    ) -> SubTaskAttempt:
        attempt = SubTaskAttempt(
            attempt_number=attempt_number,
            tier=tier,
            provider=provider,
            model_alias=model_alias,
            model_id=model_id,
        )
        attempt.completed_at = utcnow_iso()
        attempt.usage = usage or TokenUsage(input=0, output=0)
        attempt.cost_usd = cost_usd
        attempt.baseline_cost_usd = baseline_cost_usd
        attempt.latency_ms = latency_ms
        attempt.output_text = output_text
        attempt.error = error
        return attempt

    return _make


@pytest.fixture
def subtask_result_factory(subtask_factory, route_factory):
    def _make(
        *,
        subtask: SubTask | None = None,
        route: RouteDecision | None = None,
        attempts: list[SubTaskAttempt] | None = None,
        final_output: str | None = None,
        status: str = "pending",
        escalations: int = 0,
    ) -> SubTaskResult:
        resolved_subtask = subtask or subtask_factory()
        resolved_route = route or route_factory()
        return SubTaskResult(
            subtask=resolved_subtask,
            route=resolved_route,
            attempts=attempts or [],
            final_output=final_output,
            status=status,
            escalations=escalations,
        )

    return _make


@pytest.fixture
def run_result_factory(plan_factory):
    def _make(
        *,
        run_id: str = "run_test",
        task: str = "Test task",
        status: str = "completed",
        budget_cap_usd: float = 999.0,
        plan: ExecutionPlan | None = None,
        subtask_results: list[SubTaskResult] | None = None,
        final_output: str | None = "final output",
        run_stats: RunStats | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
        error: str | None = None,
    ) -> RunResult:
        derived_subtasks = [result.subtask for result in (subtask_results or [])]
        resolved_plan = plan
        if resolved_plan is None:
            if 3 <= len(derived_subtasks) <= 7:
                resolved_plan = plan_factory(derived_subtasks)
            elif not derived_subtasks:
                resolved_plan = plan_factory()
        return RunResult(
            run_id=run_id,
            task=task,
            started_at=started_at or utcnow_iso(),
            completed_at=completed_at or utcnow_iso(),
            status=status,
            budget_cap_usd=budget_cap_usd,
            plan=resolved_plan,
            subtask_results=subtask_results or [],
            final_output=final_output,
            run_stats=run_stats or RunStats(),
            error=error,
        )

    return _make


@pytest.fixture
def history_store_factory():
    def _make(settings: Settings) -> HistoryStore:
        return HistoryStore(str(Path(settings.db_path)))

    return _make


@pytest.fixture
def coordinator_factory(mock_settings, history_store_factory):
    def _make(
        *,
        settings: Settings | None = None,
        runner=None,
        orchestrator=None,
        validator=None,
        composer=None,
        event_hub=None,
        router=None,
        escalation_manager=None,
        history_store=None,
    ) -> TokenwiseCoordinator:
        resolved_settings = settings or mock_settings()
        return TokenwiseCoordinator(
            settings=resolved_settings,
            runner=runner,
            orchestrator=orchestrator,
            validator=validator,
            composer=composer,
            event_hub=event_hub,
            router=router,
            escalation_manager=escalation_manager,
            history_store=history_store or history_store_factory(resolved_settings),
        )

    return _make


@pytest.fixture
def app_factory(mock_settings, coordinator_factory):
    def _make(*, settings: Settings | None = None, coordinator: TokenwiseCoordinator | None = None):
        from tokenwise.backend.main import create_app

        resolved_settings = settings or mock_settings()
        resolved_coordinator = coordinator or coordinator_factory(settings=resolved_settings)
        return create_app(
            settings=resolved_settings,
            coordinator=resolved_coordinator,
            validate_provider_keys=False,
        )

    return _make
