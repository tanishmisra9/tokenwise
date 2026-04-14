from __future__ import annotations

import logging

from pydantic import ValidationError

from tokenwise.backend.execution.runner import LLMRunner
from tokenwise.backend.models.schemas import ExecutionPlan, Provider
from tokenwise.backend.utils import extract_json_payload

logger = logging.getLogger(__name__)


class OrchestratorAgent:
    JSON_TO_MARKDOWN_KEYWORDS = {
        "schema",
        "design",
        "architecture",
        "diagram",
        "plan",
        "strategy",
        "overview",
        "implementation",
    }

    def __init__(self, runner: LLMRunner, provider: Provider, model_id: str) -> None:
        self.runner = runner
        self.provider = provider
        self.model_id = model_id

    @staticmethod
    def _normalize_payload(payload: object) -> object:
        if not isinstance(payload, dict):
            return payload

        subtasks = payload.get("subtasks")
        if not isinstance(subtasks, list):
            return payload

        normalized_subtasks: list[object] = []
        for subtask in subtasks:
            if isinstance(subtask, dict):
                normalized_subtask = dict(subtask)
                output_format = normalized_subtask.get("output_format")
                description = str(normalized_subtask.get("description", "")).lower()

                if output_format == "code":
                    normalized_subtask["output_format"] = "markdown"
                    normalized_subtasks.append(normalized_subtask)
                    continue

                if output_format == "json" and any(
                    keyword in description for keyword in OrchestratorAgent.JSON_TO_MARKDOWN_KEYWORDS
                ):
                    normalized_subtask["output_format"] = "markdown"
                    normalized_subtasks.append(normalized_subtask)
                    continue

            normalized_subtasks.append(subtask)

        normalized_payload = dict(payload)
        normalized_payload["subtasks"] = normalized_subtasks
        return normalized_payload

    async def create_plan(self, task: str) -> ExecutionPlan:
        base_system_prompt = (
            "You are the Tokenwise orchestration planner. Break the user task into 3 to 7 "
            "dependency-aware subtasks. Return JSON only with this shape: "
            '{"subtasks":[{"id":"task_1","description":"...","complexity":"low|medium|high",'
            '"depends_on":["task_1"],"output_format":"paragraph|list|json|markdown",'
            '"routing_hint":"general_reasoning|structured_output|instruction_following|creative_synthesis|code_generation"}]}'
            ' output_format must be exactly one of: paragraph, list, json, markdown — no other values are valid. '
            "IMPORTANT: Use output_format 'json' ONLY when the subtask explicitly asks for a machine-readable data "
            "structure that will be consumed programmatically — for example, an API response schema or a config file. "
            "For data schemas described for human readers, system designs, technical architectures, code "
            "implementations, and any content that mixes explanation with structure, always use 'markdown'. "
            "When in doubt, use 'markdown' not 'json'."
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
                payload = self._normalize_payload(payload)
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
