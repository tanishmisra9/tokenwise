from __future__ import annotations

from pydantic import ValidationError

from tokenwise.backend.execution.runner import LLMRunner
from tokenwise.backend.models.schemas import ExecutionPlan, Provider
from tokenwise.backend.utils import extract_json_payload


class OrchestratorAgent:
    def __init__(self, runner: LLMRunner, model_id: str) -> None:
        self.runner = runner
        self.model_id = model_id

    async def create_plan(self, task: str) -> ExecutionPlan:
        response = await self.runner.generate(
            provider=Provider.OPENAI,
            model_id=self.model_id,
            system_prompt=(
                "You are the Tokenwise orchestration planner. Break the user task into 3 to 7 "
                "dependency-aware subtasks. Return JSON only with this shape: "
                '{"subtasks":[{"id":"task_1","description":"...","complexity":"low|medium|high",'
                '"depends_on":["task_1"],"output_format":"paragraph|list|json|markdown",'
                '"routing_hint":"general_reasoning|structured_output|instruction_following|creative_synthesis|code_generation"}]}'
            ),
            user_prompt=(
                f"User task:\n{task}\n\n"
                "Decompose the task into the smallest set of meaningful subtasks. "
                "Use `json` output_format when a subtask should return structured data."
            ),
            max_output_tokens=1_000,
            temperature=0.1,
            json_mode=True,
        )
        payload = extract_json_payload(response.output_text)
        try:
            return ExecutionPlan.model_validate(payload)
        except ValidationError as exc:
            raise RuntimeError(f"Orchestrator returned invalid execution plan: {exc}") from exc

