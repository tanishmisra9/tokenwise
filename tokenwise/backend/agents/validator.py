from __future__ import annotations

from tokenwise.backend.execution.runner import LLMRunner
from tokenwise.backend.models.schemas import OutputFormat, Provider, RoutingHint, SubTask, ValidationResult
from tokenwise.backend.utils import extract_json_payload


class ValidatorAgent:
    RUBRICS: dict[RoutingHint, str] = {
        RoutingHint.CODE_GENERATION: (
            "Check for syntactic plausibility, concrete implementation detail, and absence of placeholder text, "
            "TODO markers, or unfinished stubs."
        ),
        RoutingHint.STRUCTURED_OUTPUT: (
            "Check that the result matches the requested structure exactly: valid JSON when JSON is requested, "
            "or a well-formed list with clear item boundaries when list output is requested."
        ),
        RoutingHint.CREATIVE_SYNTHESIS: (
            "Check for coherent narrative flow, polished synthesis, and smooth transitions between ideas."
        ),
        RoutingHint.GENERAL_REASONING: (
            "Check for completeness, coherence, and whether the output fully answers the assigned subtask."
        ),
        RoutingHint.INSTRUCTION_FOLLOWING: (
            "Check for completeness, coherence, and close adherence to the user instruction and requested format."
        ),
    }

    def __init__(self, runner: LLMRunner, provider: Provider, model_id: str) -> None:
        self.runner = runner
        self.provider = provider
        self.model_id = model_id

    async def validate(
        self,
        subtask: SubTask,
        output_text: str,
        routing_hint: RoutingHint,
        output_format: OutputFormat,
    ) -> ValidationResult:
        output_text = output_text.strip()
        if not output_text:
            return ValidationResult(passed=False, reason="Model returned an empty output.")

        if output_format == OutputFormat.JSON:
            try:
                extract_json_payload(output_text)
            except Exception:
                return ValidationResult(passed=False, reason="Expected JSON output but the content was not parseable JSON.")

        rubric = self.RUBRICS.get(
            routing_hint,
            "Check for completeness, coherence, and alignment to the requested task.",
        )

        response = await self.runner.generate(
            provider=self.provider,
            model_id=self.model_id,
            system_prompt=(
                "You are the Tokenwise validator. Check whether the output is complete, coherent, and "
                "in the requested format. Apply the routing-specific rubric below.\n\n"
                f"Routing hint: {routing_hint.value}\n"
                f"Output format: {output_format.value}\n"
                f"Rubric: {rubric}\n\n"
                "Return JSON only with {\"passed\": true|false, \"reason\": \"...\"}."
            ),
            user_prompt=(
                f"Subtask description: {subtask.description}\n"
                f"Routing hint: {routing_hint.value}\n"
                f"Expected output format: {output_format.value}\n\n"
                f"Candidate output:\n{output_text}"
            ),
            max_output_tokens=250,
            temperature=0.0,
            json_mode=True,
        )
        payload = extract_json_payload(response.output_text)
        return ValidationResult.model_validate(payload)
