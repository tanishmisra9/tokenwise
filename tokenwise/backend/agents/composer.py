from __future__ import annotations

from tokenwise.backend.execution.runner import LLMRunner
from tokenwise.backend.models.schemas import Provider, SubTaskResult


class ComposerAgent:
    def __init__(self, runner: LLMRunner, provider: Provider, model_id: str) -> None:
        self.runner = runner
        self.provider = provider
        self.model_id = model_id

    async def compose(
        self,
        task: str,
        subtask_results: list[SubTaskResult],
        revision_feedback: str | None = None,
    ) -> str:
        ordered_outputs = []
        for result in subtask_results:
            ordered_outputs.append(
                f"{result.subtask.id}: {result.subtask.description}\n"
                f"Output:\n{result.final_output or ''}"
            )

        revision_block = (
            f"\n\nPrevious attempt failed quality check: {revision_feedback}. Revise accordingly."
            if revision_feedback
            else ""
        )

        response = await self.runner.generate(
            provider=self.provider,
            model_id=self.model_id,
            system_prompt=(
                "You are the Tokenwise composer. Combine the completed subtask outputs into one final "
                "response that directly answers the original task. Preserve useful structure and headings."
            ),
            user_prompt=(
                f"Original task:\n{task}\n\n"
                f"Subtask outputs in dependency order:\n\n{'\n\n'.join(ordered_outputs)}"
                f"{revision_block}"
            ),
            max_output_tokens=1_200,
            temperature=0.2,
        )
        return response.output_text.strip()
