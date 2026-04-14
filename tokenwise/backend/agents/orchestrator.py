from __future__ import annotations

import logging

from pydantic import ValidationError

from tokenwise.backend.execution.runner import LLMRunner
from tokenwise.backend.models.schemas import ExecutionPlan, Provider
from tokenwise.backend.utils import extract_json_payload

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    def __init__(self, runner: LLMRunner, provider: Provider, model_id: str) -> None:
        self.runner = runner
        self.provider = provider
        self.model_id = model_id

    async def create_plan(self, task: str) -> ExecutionPlan:
        base_system_prompt = (
            "You are the Tokenwise orchestration planner. Break the user task into 3 to 7 "
            "dependency-aware subtasks. Return JSON only with this shape: "
            '{"subtasks":[{"id":"task_1","description":"...","complexity":"low|medium|high",'
            '"depends_on":["task_1"],"output_format":"paragraph|list|json|markdown",'
            '"routing_hint":"general_reasoning|structured_output|instruction_following|creative_synthesis|code_generation"}]}'
        )
        user_prompt = (
            f"User task:\n{task}\n\n"
            "Decompose the task into the smallest set of meaningful subtasks. "
            "Use `json` output_format when a subtask should return structured data."
        )

        first_error: Exception | None = None

        for attempt_number, system_prompt in enumerate(
            [
                base_system_prompt,
                (
                    f"{base_system_prompt}\n\n"
                    "Your previous response was not valid JSON or did not match the required schema. "
                    "Return only a valid JSON object matching the schema exactly, with no additional text."
                ),
            ],
            start=1,
        ):
            try:
                response = await self.runner.generate(
                    provider=self.provider,
                    model_id=self.model_id,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    max_output_tokens=1_000,
                    temperature=0.1,
                    json_mode=True,
                )
                payload = extract_json_payload(response.output_text)
                return ExecutionPlan.model_validate(payload)
            except Exception as exc:
                if not isinstance(exc, ValidationError | ValueError):
                    raise
                logger.warning("Orchestrator plan attempt %s failed: %s", attempt_number, exc)
                if first_error is None:
                    first_error = exc
                    continue
                logger.error("Orchestrator retry failed: %s", exc)
                raise RuntimeError(f"Orchestrator returned invalid execution plan: {first_error}") from first_error

        raise RuntimeError("Orchestrator failed without producing a plan.")
