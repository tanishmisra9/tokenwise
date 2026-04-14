from __future__ import annotations

from tokenwise.backend.execution.runner import LLMRunner
from tokenwise.backend.models.schemas import OutputFormat, Provider, SubTask, ValidationResult
from tokenwise.backend.utils import extract_json_payload


class ValidatorAgent:
    def __init__(self, runner: LLMRunner, model_id: str) -> None:
        self.runner = runner
        self.model_id = model_id

    async def validate(self, subtask: SubTask, output_text: str) -> ValidationResult:
        output_text = output_text.strip()
        if not output_text:
            return ValidationResult(passed=False, reason="Model returned an empty output.")

        if subtask.output_format == OutputFormat.JSON:
            try:
                extract_json_payload(output_text)
            except Exception:
                return ValidationResult(passed=False, reason="Expected JSON output but the content was not parseable JSON.")

        response = await self.runner.generate(
            provider=Provider.OPENAI,
            model_id=self.model_id,
            system_prompt=(
                "You are the Tokenwise validator. Check whether the output is complete, coherent, and "
                "in the requested format. Return JSON only with {\"passed\": true|false, \"reason\": \"...\"}."
            ),
            user_prompt=(
                f"Subtask description: {subtask.description}\n"
                f"Expected output format: {subtask.output_format.value}\n\n"
                f"Candidate output:\n{output_text}"
            ),
            max_output_tokens=250,
            temperature=0.0,
            json_mode=True,
        )
        payload = extract_json_payload(response.output_text)
        return ValidationResult.model_validate(payload)

