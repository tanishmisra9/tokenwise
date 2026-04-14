from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tokenwise.backend.main import create_app
from tokenwise.backend.models.schemas import (
    Complexity,
    ExecutionPlan,
    LLMResponse,
    OutputFormat,
    Provider,
    RoutingHint,
    SubTask,
    TokenUsage,
    ValidationResult,
)
from tokenwise.backend.runtime import TokenwiseCoordinator


class SmokeOrchestrator:
    async def create_plan(self, task: str) -> ExecutionPlan:
        return ExecutionPlan(
            subtasks=[
                SubTask(
                    id="task_1",
                    description="Create an outline",
                    complexity=Complexity.MEDIUM,
                    depends_on=[],
                    output_format=OutputFormat.LIST,
                    routing_hint=RoutingHint.GENERAL_REASONING,
                ),
                SubTask(
                    id="task_2",
                    description="Draft the main section",
                    complexity=Complexity.MEDIUM,
                    depends_on=["task_1"],
                    output_format=OutputFormat.PARAGRAPH,
                    routing_hint=RoutingHint.INSTRUCTION_FOLLOWING,
                ),
                SubTask(
                    id="task_3",
                    description="Polish the final response",
                    complexity=Complexity.LOW,
                    depends_on=["task_2"],
                    output_format=OutputFormat.MARKDOWN,
                    routing_hint=RoutingHint.GENERAL_REASONING,
                ),
            ]
        )


class SmokeRunner:
    def __init__(self) -> None:
        self.call_count = 0

    async def generate(self, **kwargs) -> LLMResponse:
        self.call_count += 1
        return LLMResponse(
            output_text=f"response {self.call_count}",
            usage=TokenUsage(input=2_000, output=500),
            latency_ms=200,
        )


class SmokeValidator:
    def __init__(self, *, fail_first_task_once: bool = False) -> None:
        self.fail_first_task_once = fail_first_task_once
        self.calls: dict[str, int] = {}

    async def validate(self, subtask, output_text, routing_hint, output_format) -> ValidationResult:
        self.calls[subtask.id] = self.calls.get(subtask.id, 0) + 1
        if self.fail_first_task_once and subtask.id == "task_1" and self.calls[subtask.id] == 1:
            return ValidationResult(passed=False, reason="Outline needs one more pass.")
        return ValidationResult(passed=True, reason="Looks good.")


class SmokeComposer:
    async def compose(self, task, subtask_results, revision_feedback=None) -> str:
        return "\n".join(result.final_output or "" for result in subtask_results)


def make_test_client(
    mock_settings,
    *,
    validator=None,
):
    settings = mock_settings()
    coordinator = TokenwiseCoordinator(
        settings=settings,
        runner=SmokeRunner(),
        orchestrator=SmokeOrchestrator(),
        validator=validator or SmokeValidator(),
        composer=SmokeComposer(),
    )
    app = create_app(settings=settings, coordinator=coordinator, validate_provider_keys=False)
    return TestClient(app)


def collect_run_events(client: TestClient, request_body: dict) -> list[dict]:
    response = client.post("/run", json=request_body)
    assert response.status_code == 202
    run_id = response.json()["run_id"]
    events: list[dict] = []

    with client.websocket_connect(f"/runs/{run_id}") as websocket:
        while True:
            event = websocket.receive_json()
            events.append(event)
            if event["event"] in {"run_completed", "run_failed"}:
                break

    return events


def test_healthcheck_starts(mock_settings):
    with make_test_client(mock_settings) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_history_is_empty_initially(mock_settings):
    with make_test_client(mock_settings) as client:
        response = client.get("/history")

    assert response.status_code == 200
    assert response.json() == {
        "total_runs": 0,
        "total_tokens": 0,
        "total_spent_usd": 0.0,
        "total_saved_usd": 0.0,
        "avg_savings_pct": 0.0,
        "runs": [],
        "routing_hint_breakdown": {},
    }


def test_successful_run_streams_and_persists_history(mock_settings):
    with make_test_client(mock_settings) as client:
        events = collect_run_events(
            client,
            {
                "task": "Prepare a concise client-ready answer.",
                "budget_cap_usd": 0.50,
                "quality_floor": "medium",
            },
        )
        history = client.get("/history").json()

    assert events[0]["event"] == "run_started"
    assert "plan_ready" in [event["event"] for event in events]
    assert events[-1]["event"] == "run_completed"
    assert history["total_runs"] == 1
    assert history["runs"][0]["status"] == "completed"


def test_retry_and_budget_lock_events_are_streamed(mock_settings):
    with make_test_client(mock_settings, validator=SmokeValidator(fail_first_task_once=True)) as client:
        events = collect_run_events(
            client,
            {
                "task": "Generate a layered answer with retries.",
                "budget_cap_usd": 0.001,
                "quality_floor": "medium",
            },
        )

    actions = [
        event["payload"].get("action")
        for event in events
        if event["event"] == "subtask_escalated"
    ]
    assert "retry_same_model" in actions
    assert "budget_lock" in actions
    assert events[-1]["event"] == "run_completed"


def test_get_history_includes_routing_hint_breakdown_key(mock_settings):
    with make_test_client(mock_settings) as client:
        response = client.get("/history")

    assert response.status_code == 200
    assert "routing_hint_breakdown" in response.json()


def test_post_run_with_task_exceeding_two_thousand_chars_returns_422(mock_settings):
    with make_test_client(mock_settings) as client:
        response = client.post("/run", json={"task": "x" * 2001, "quality_floor": "medium"})

    assert response.status_code == 422


def test_websocket_for_unknown_run_id_closes_with_expected_code(mock_settings):
    with make_test_client(mock_settings) as client:
        with client.websocket_connect("/runs/run_missing") as websocket:
            with pytest.raises(WebSocketDisconnect) as excinfo:
                websocket.receive_json()

    assert excinfo.value.code == 4404


def test_delete_unknown_run_returns_404(mock_settings):
    with make_test_client(mock_settings) as client:
        response = client.delete("/runs/run_missing")

    assert response.status_code == 404

