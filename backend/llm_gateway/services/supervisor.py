import json
from collections.abc import AsyncIterator
from typing import Any

from common.dto import LLMResponse, LLMStructuredResponse
from common.protocols import LLMGateway


class SupervisorLLMService:
    def __init__(
        self,
        llm_provider: LLMGateway,
        default_model: str = "supervisor_model",
    ) -> None:
        self._llm_provider = llm_provider
        self._default_model = default_model

    async def call_json(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        schema: dict[str, Any] | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> dict[str, Any]:
        response = await self._llm_provider.generate_structured(
            _supervisor_messages(system_prompt, user_prompt),
            schema=schema,
            json_mode=schema is None,
            model=model or self._default_model,
            timeout_seconds=timeout_seconds,
        )
        return structured_response_data(response)

    async def call_text(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> str:
        response = await self._llm_provider.generate(
            _supervisor_messages(system_prompt, user_prompt),
            model=model or self._default_model,
            timeout_seconds=timeout_seconds,
        )
        return response_content(response)

    def call_text_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        model: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[str]:
        return self._llm_provider.generate_stream(
            _supervisor_messages(system_prompt, user_prompt),
            model=model or self._default_model,
            timeout_seconds=timeout_seconds,
        )


def _supervisor_messages(system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def response_content(response: LLMResponse | str) -> str:
    if isinstance(response, str):
        return response
    return response.content


def structured_response_data(response: LLMStructuredResponse | dict[str, Any]) -> dict:
    if isinstance(response, dict):
        return response
    if response.data:
        return response.data
    content = response.raw_response.get("content")
    if not isinstance(content, str) or not content.strip():
        raise ValueError("Empty supervisor JSON response")
    return json.loads(content)
