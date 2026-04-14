from __future__ import annotations

import re

from tokenwise.backend.execution.runner import LLMRunner
from tokenwise.backend.models.schemas import OutputFormat, Provider, RoutingHint, SubTask, ValidationResult
from tokenwise.backend.utils import extract_json_payload


class ValidatorAgent:
    RUBRICS: dict[RoutingHint, str] = {
        RoutingHint.CODE_GENERATION: (
            "Check for syntactic plausibility, concrete implementation detail, and absence of placeholder text, "
            "TODO markers, or unfinished stubs."
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
    STRUCTURED_OUTPUT_RUBRICS: dict[OutputFormat, str] = {
        OutputFormat.JSON: (
            "Check that the result is valid JSON and that the structure matches the requested schema or shape."
        ),
        OutputFormat.LIST: (
            "Check that the result is list-like with multiple distinct items rendered as markdown bullets, "
            "numbered items, or plain line-separated entries. Do not require JSON for list output."
        ),
    }

    def __init__(self, runner: LLMRunner, provider: Provider, model_id: str) -> None:
        self.runner = runner
        self.provider = provider
        self.model_id = model_id

    @staticmethod
    def _looks_like_list(output_text: str) -> bool:
        bullet_count = 0
        plain_lines: list[str] = []
        for line in output_text.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith(("- ", "* ", "+ ")):
                bullet_count += 1
                continue
            if re.match(r"^\d+[\.\)]\s+", stripped):
                bullet_count += 1
                continue
            plain_lines.append(stripped)

        if bullet_count >= 2:
            return True

        distinct_lines = {line for line in plain_lines if line}
        return len(distinct_lines) >= 2

    def _rubric_for(self, routing_hint: RoutingHint, output_format: OutputFormat) -> str:
        if routing_hint == RoutingHint.STRUCTURED_OUTPUT:
            return self.STRUCTURED_OUTPUT_RUBRICS.get(
                output_format,
                "Check that the result matches the requested structure with clear, well-delimited items.",
            )

        return self.RUBRICS.get(
            routing_hint,
            "Check for completeness, coherence, and alignment to the requested task.",
        )

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
                stripped = re.sub(r"^```(?:json)?\s*", "", output_text.strip(), flags=re.IGNORECASE)
                stripped = re.sub(r"\s*```$", "", stripped.strip())
                extract_json_payload(stripped)
            except Exception:
                return ValidationResult(passed=False, reason="Expected JSON output but the content was not parseable JSON.")
        elif routing_hint == RoutingHint.STRUCTURED_OUTPUT and output_format == OutputFormat.LIST:
            if not self._looks_like_list(output_text):
                return ValidationResult(
                    passed=False,
                    reason=(
                        "Expected list output with multiple distinct items rendered as bullets, numbered entries, "
                        "or plain line-separated items."
                    ),
                )

        rubric = self._rubric_for(routing_hint, output_format)

        response = await self.runner.generate(
            provider=self.provider,
            model_id=self.model_id,
            system_prompt=(
                "You are the Tokenwise validator. Check whether the output is complete, coherent, and "
                "in the requested format. Apply the routing-specific rubric below.\n\n"
                f"Routing hint: {routing_hint.value}\n"
                f"Output format: {output_format.value}\n"
                f"Rubric: {rubric}\n\n"
                "Only return passed: false if the output is genuinely incomplete (cuts off mid-sentence or "
                "mid-section), contains placeholder text, or fundamentally fails the rubric. Do NOT fail for "
                "stylistic issues, missing polish, imperfect transitions, or outputs that are complete but "
                "could be improved. When in doubt, pass.\n\n"
                "Always return both fields: passed and reason. If passed is true, reason can be an empty string "
                "or a brief confirmation.\n\n"
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
