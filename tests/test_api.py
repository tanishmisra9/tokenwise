from __future__ import annotations

import re

from fastapi.testclient import TestClient

from tokenwise.backend.config import Settings
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


class FakeOrchestrator:
    async def create_plan(self, task: str) -> ExecutionPlan:
        return ExecutionPlan(
            subtasks=[
                SubTask(
                    id="task_1",
                    description="Create an outline of the answer",
                    complexity=Complexity.MEDIUM,
                    depends_on=[],
                    output_format=OutputFormat.LIST,
                    routing_hint=RoutingHint.GENERAL_REASONING,
                ),
                SubTask(
                    id="task_2",
                    description="Turn the outline into a polished section",
                    complexity=Complexity.MEDIUM,
                    depends_on=["task_1"],
                    output_format=OutputFormat.PARAGRAPH,
                    routing_hint=RoutingHint.INSTRUCTION_FOLLOWING,
                ),
                SubTask(
                    id="task_3",
                    description="Summarize the final answer for the user",
                    complexity=Complexity.LOW,
                    depends_on=["task_2"],
                    output_format=OutputFormat.MARKDOWN,
                    routing_hint=RoutingHint.GENERAL_REASONING,
                ),
            ]
        )


class FakeRunner:
    def __init__(self, *, fail_task_two_openai_once: bool = False) -> None:
        self.fail_task_two_openai_once = fail_task_two_openai_once
        self.call_counts: dict[tuple[str, str], int] = {}

    async def generate(
        self,
        *,
        provider: Provider,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int = 900,
        temperature: float = 0.2,
        json_mode: bool = False,
    ) -> LLMResponse:
        match = re.search(r"Assigned subtask \(([^)]+)\)", user_prompt)
        subtask_id = match.group(1) if match else "unknown"
        key = (subtask_id, provider.value)
        self.call_counts[key] = self.call_counts.get(key, 0) + 1

        if self.fail_task_two_openai_once and subtask_id == "task_2" and provider == Provider.OPENAI and self.call_counts[key] == 1:
            raise RuntimeError("OpenAI provider timeout")

        body = f"{subtask_id} handled by {provider.value} using {model_id}"
        usage = TokenUsage(input=2000, output=500)
        return LLMResponse(output_text=body, usage=usage, latency_ms=210)


class FakeValidator:
    def __init__(self, *, fail_first_task_once: bool = False) -> None:
        self.fail_first_task_once = fail_first_task_once
        self.calls: dict[str, int] = {}

    async def validate(self, subtask: SubTask, output_text: str) -> ValidationResult:
        self.calls[subtask.id] = self.calls.get(subtask.id, 0) + 1
        if self.fail_first_task_once and subtask.id == "task_1" and self.calls[subtask.id] == 1:
            return ValidationResult(passed=False, reason="Outline needs one more pass.")
        return ValidationResult(passed=True, reason="Looks good.")


class FakeComposer:
    async def compose(self, task: str, subtask_results) -> str:
        return "\n".join(result.final_output or "" for result in subtask_results)


def make_test_client(
    tmp_path,
    *,
    fail_task_two_openai_once: bool = False,
    fail_first_task_once: bool = False,
) -> TestClient:
    settings = Settings(
        db_path=str(tmp_path / "tokenwise-test.db"),
        openai_api_key=None,
        anthropic_api_key=None,
    )
    coordinator = TokenwiseCoordinator(
        settings=settings,
        runner=FakeRunner(fail_task_two_openai_once=fail_task_two_openai_once),
        orchestrator=FakeOrchestrator(),
        validator=FakeValidator(fail_first_task_once=fail_first_task_once),
        composer=FakeComposer(),
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


def test_healthcheck_starts(tmp_path):
    with make_test_client(tmp_path) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


def test_history_is_empty_initially(tmp_path):
    with make_test_client(tmp_path) as client:
        response = client.get("/history")
        assert response.status_code == 200
        assert response.json() == {
            "total_runs": 0,
            "total_tokens": 0,
            "total_spent_usd": 0.0,
            "total_saved_usd": 0.0,
            "avg_savings_pct": 0.0,
            "runs": [],
        }


def test_successful_run_streams_and_persists_history(tmp_path):
    with make_test_client(tmp_path) as client:
        events = collect_run_events(
            client,
            {
                "task": "Prepare a concise client-ready answer.",
                "budget_cap_usd": 0.50,
                "quality_floor": "medium",
            },
        )

        event_names = [event["event"] for event in events]
        assert event_names[0] == "run_started"
        assert "plan_ready" in event_names
        assert event_names[-1] == "run_completed"

        completed_payload = events[-1]["payload"]
        assert "task_1 handled by" in completed_payload["final_output"]
        assert completed_payload["run_stats"]["tokens_used"] > 0

        history = client.get("/history").json()
        assert history["total_runs"] == 1
        assert history["runs"][0]["status"] == "completed"


def test_retry_and_budget_lock_events_are_streamed(tmp_path):
    with make_test_client(tmp_path, fail_first_task_once=True) as client:
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
