from __future__ import annotations

import time

import httpx

from tokenwise.backend.config import Settings
from tokenwise.backend.models.schemas import LLMResponse, Provider, TokenUsage


class LLMRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.timeout = httpx.Timeout(settings.request_timeout_seconds)

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
        if provider == Provider.OPENAI:
            return await self._call_openai(
                model_id=model_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=max_output_tokens,
                temperature=temperature,
                json_mode=json_mode,
            )
        return await self._call_anthropic(
            model_id=model_id,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )

    async def _call_openai(
        self,
        *,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        temperature: float,
        json_mode: bool,
    ) -> LLMResponse:
        if not self.settings.openai_api_key:
            raise RuntimeError("OPENAI_API_KEY is required.")

        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
            "max_completion_tokens": max_output_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.settings.openai_base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.settings.openai_api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        latency_ms = int((time.perf_counter() - start) * 1000)
        response.raise_for_status()
        body = response.json()
        content = body["choices"][0]["message"]["content"]
        usage = body.get("usage", {})
        return LLMResponse(
            output_text=content or "",
            usage=TokenUsage(
                input=int(usage.get("prompt_tokens", 0)),
                output=int(usage.get("completion_tokens", 0)),
            ),
            latency_ms=latency_ms,
        )

    async def _call_anthropic(
        self,
        *,
        model_id: str,
        system_prompt: str,
        user_prompt: str,
        max_output_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        if not self.settings.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY is required.")

        start = time.perf_counter()
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            response = await client.post(
                f"{self.settings.anthropic_base_url}/messages",
                headers={
                    "x-api-key": self.settings.anthropic_api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": model_id,
                    "system": system_prompt,
                    "messages": [{"role": "user", "content": user_prompt}],
                    "max_tokens": max_output_tokens,
                    "temperature": temperature,
                },
            )
        latency_ms = int((time.perf_counter() - start) * 1000)
        response.raise_for_status()
        body = response.json()
        text_chunks = [block.get("text", "") for block in body.get("content", []) if block.get("type") == "text"]
        usage = body.get("usage", {})
        return LLMResponse(
            output_text="\n".join(chunk for chunk in text_chunks if chunk).strip(),
            usage=TokenUsage(
                input=int(usage.get("input_tokens", 0)),
                output=int(usage.get("output_tokens", 0)),
            ),
            latency_ms=latency_ms,
        )

