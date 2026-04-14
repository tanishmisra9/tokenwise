from __future__ import annotations

import pytest

from tokenwise.backend.agents.validator import ValidatorAgent
from tokenwise.backend.config import build_model_registry
from tokenwise.backend.models.schemas import OutputFormat, Provider, RoutingHint


@pytest.mark.asyncio
async def test_passing_output_returns_validation_result_passed(mock_runner, llm_response_factory, subtask_factory):
    mock_runner.queue_response(llm_response_factory('{"passed": true, "reason": "Looks good."}'))
    subtask = subtask_factory()
    agent = ValidatorAgent(mock_runner, Provider.OPENAI, "gpt-4o-mini")

    result = await agent.validate(subtask, "A complete answer.", RoutingHint.GENERAL_REASONING, OutputFormat.PARAGRAPH)

    assert result.passed is True


@pytest.mark.asyncio
async def test_empty_string_output_returns_failed_validation(mock_runner, subtask_factory):
    agent = ValidatorAgent(mock_runner, Provider.OPENAI, "gpt-4o-mini")

    result = await agent.validate(subtask_factory(), "   ", RoutingHint.GENERAL_REASONING, OutputFormat.PARAGRAPH)

    assert result.passed is False
    assert result.reason
    mock_runner.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_truncated_output_returns_failed_validation(mock_runner, llm_response_factory, subtask_factory):
    mock_runner.queue_response(llm_response_factory('{"passed": false, "reason": "The answer appears truncated."}'))
    agent = ValidatorAgent(mock_runner, Provider.OPENAI, "gpt-4o-mini")

    result = await agent.validate(
        subtask_factory(),
        "This answer stops mid-thought...",
        RoutingHint.GENERAL_REASONING,
        OutputFormat.PARAGRAPH,
    )

    assert result.passed is False
    assert "truncated" in result.reason.lower()


@pytest.mark.asyncio
async def test_code_generation_placeholder_text_returns_failed_validation(mock_runner, llm_response_factory, subtask_factory):
    mock_runner.queue_response(llm_response_factory('{"passed": false, "reason": "Contains TODO placeholder text."}'))
    agent = ValidatorAgent(mock_runner, Provider.OPENAI, "gpt-4o-mini")

    result = await agent.validate(
        subtask_factory(routing_hint=RoutingHint.CODE_GENERATION),
        "def handler():\n    # TODO implement\n    pass",
        RoutingHint.CODE_GENERATION,
        OutputFormat.PARAGRAPH,
    )

    assert result.passed is False
    assert "placeholder" in result.reason.lower() or "todo" in result.reason.lower()


@pytest.mark.asyncio
async def test_structured_output_invalid_json_returns_failed_validation_without_runner(mock_runner, subtask_factory):
    agent = ValidatorAgent(mock_runner, Provider.OPENAI, "gpt-4o-mini")

    result = await agent.validate(
        subtask_factory(output_format=OutputFormat.JSON, routing_hint=RoutingHint.STRUCTURED_OUTPUT),
        "{not valid json",
        RoutingHint.STRUCTURED_OUTPUT,
        OutputFormat.JSON,
    )

    assert result.passed is False
    assert "json" in result.reason.lower()
    mock_runner.generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_structured_output_valid_json_returns_passed_validation(mock_runner, llm_response_factory, subtask_factory):
    mock_runner.queue_response(llm_response_factory('{"passed": true, "reason": "Valid JSON and structure."}'))
    agent = ValidatorAgent(mock_runner, Provider.OPENAI, "gpt-4o-mini")

    result = await agent.validate(
        subtask_factory(output_format=OutputFormat.JSON, routing_hint=RoutingHint.STRUCTURED_OUTPUT),
        '{"items": ["a", "b"]}',
        RoutingHint.STRUCTURED_OUTPUT,
        OutputFormat.JSON,
    )

    assert result.passed is True


@pytest.mark.asyncio
async def test_validator_uses_tier_one_model(mock_runner, llm_response_factory, mock_settings, subtask_factory):
    settings = mock_settings()
    tier_one_model_id = build_model_registry(settings)["tier1_openai"].model_id
    mock_runner.queue_response(llm_response_factory('{"passed": true, "reason": "Looks good."}'))
    agent = ValidatorAgent(mock_runner, Provider.OPENAI, tier_one_model_id)

    await agent.validate(subtask_factory(), "A valid answer.", RoutingHint.GENERAL_REASONING, OutputFormat.PARAGRAPH)

    assert mock_runner.generate.await_args.kwargs["model_id"] == tier_one_model_id

