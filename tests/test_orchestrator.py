from __future__ import annotations

from unittest.mock import AsyncMock, call

import pytest

from tokenwise.backend.agents.orchestrator import OrchestratorAgent
from tokenwise.backend.models.schemas import Provider


VALID_PLAN_JSON = """
{
  "subtasks": [
    {
      "id": "task_1",
      "description": "Gather the relevant facts",
      "complexity": "low",
      "depends_on": [],
      "output_format": "paragraph",
      "routing_hint": "general_reasoning"
    },
    {
      "id": "task_2",
      "description": "Structure the answer",
      "complexity": "medium",
      "depends_on": ["task_1"],
      "output_format": "list",
      "routing_hint": "instruction_following"
    },
    {
      "id": "task_3",
      "description": "Compose the final response",
      "complexity": "medium",
      "depends_on": ["task_2"],
      "output_format": "markdown",
      "routing_hint": "creative_synthesis"
    }
  ]
}
"""


@pytest.mark.asyncio
async def test_valid_task_returns_well_formed_execution_plan(mock_runner, llm_response_factory):
    mock_runner.queue_response(llm_response_factory(VALID_PLAN_JSON))
    agent = OrchestratorAgent(mock_runner, Provider.OPENAI, "gpt-4o")

    plan = await agent.create_plan("Summarize a market brief.")

    assert 3 <= len(plan.subtasks) <= 7
    known_ids = {subtask.id for subtask in plan.subtasks}
    for subtask in plan.subtasks:
        assert subtask.id
        assert subtask.description
        assert subtask.complexity.value
        assert subtask.output_format.value
        assert subtask.routing_hint.value
        assert set(subtask.depends_on).issubset(known_ids)


@pytest.mark.asyncio
async def test_malformed_json_on_first_attempt_triggers_retry_with_stricter_prompt(
    mock_runner,
    llm_response_factory,
):
    mock_runner.queue_response(llm_response_factory("not-json"))
    mock_runner.queue_response(llm_response_factory(VALID_PLAN_JSON))
    agent = OrchestratorAgent(mock_runner, Provider.OPENAI, "gpt-4o")

    plan = await agent.create_plan("Draft a customer update.")

    assert len(plan.subtasks) == 3
    assert mock_runner.generate.await_count == 2
    second_call = mock_runner.generate.await_args_list[1]
    assert "Your previous response was not valid JSON" in second_call.kwargs["system_prompt"]


@pytest.mark.asyncio
async def test_two_consecutive_malformed_responses_raise_runtime_error(mock_runner, llm_response_factory):
    mock_runner.queue_response(llm_response_factory("still not json"))
    mock_runner.queue_response(llm_response_factory("definitely not json"))
    agent = OrchestratorAgent(mock_runner, Provider.OPENAI, "gpt-4o")

    with pytest.raises(RuntimeError, match="Orchestrator returned invalid execution plan"):
        await agent.create_plan("Prepare a client memo.")

    assert mock_runner.generate.await_count == 2


@pytest.mark.asyncio
async def test_valid_json_that_fails_model_validate_triggers_retry(mock_runner, llm_response_factory):
    invalid_schema_json = '{"subtasks":[{"id":"task_1"}]}'
    mock_runner.queue_response(llm_response_factory(invalid_schema_json))
    mock_runner.queue_response(llm_response_factory(VALID_PLAN_JSON))
    agent = OrchestratorAgent(mock_runner, Provider.OPENAI, "gpt-4o")

    plan = await agent.create_plan("Convert notes into an answer.")

    assert len(plan.subtasks) == 3
    assert mock_runner.generate.await_count == 2

