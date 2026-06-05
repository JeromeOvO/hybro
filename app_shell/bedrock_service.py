"""Compatibility adapter for legacy Bedrock supervisor calls.

The Bedrock transport is owned by ``llm_gateway.providers.bedrock_provider``.
This adapter keeps the historical app-shell API surface while delegating all
runtime calls to the focused supervisor service or the central gateway.
"""

from collections.abc import AsyncIterator
from typing import Any

from llm_gateway.config import LLMGatewayConfig
from llm_gateway.errors import LLMServiceNotBoundError
from llm_gateway.services.supervisor import (
    SupervisorLLMService,
    response_content,
    structured_response_data,
)


class BedrockService:
    def __init__(
        self,
        supervisor_service: SupervisorLLMService | None = None,
        llm_gateway_config: LLMGatewayConfig | None = None,
    ) -> None:
        self._supervisor_service = supervisor_service
        self._llm_provider = None
        self._timeout = (
            llm_gateway_config.bedrock_request_timeout_seconds
            if llm_gateway_config
            else 45.0
        )
        self._default_model = "bedrock_supervisor_model"

    @property
    def is_bound(self) -> bool:
        return self._supervisor_service is not None

    def bind_llm_services(
        self,
        *,
        supervisor_service: SupervisorLLMService,
        llm_provider: Any | None = None,
        llm_gateway_config: LLMGatewayConfig,
    ) -> None:
        self._supervisor_service = supervisor_service
        self._llm_provider = llm_provider
        self._timeout = llm_gateway_config.bedrock_request_timeout_seconds

    async def call_claude_json(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> dict:
        model_id = model or self._default_model
        if model and _requires_bedrock_provider_hint(model):
            response = await self._require_llm_provider()._generate_structured_with_provider_hint(
                _messages(system_prompt, user_prompt),
                model=model_id,
                provider_hint="bedrock",
                schema=None,
                json_mode=True,
                timeout_seconds=self._timeout,
            )
            return structured_response_data(response)
        return await self._require_supervisor_service().call_json(
            system_prompt,
            user_prompt,
            model=model_id,
            timeout_seconds=self._timeout,
        )

    async def call_claude_text(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> str:
        model_id = model or self._default_model
        if model and _requires_bedrock_provider_hint(model):
            response = await self._require_llm_provider()._generate_with_provider_hint(
                _messages(system_prompt, user_prompt),
                model=model_id,
                provider_hint="bedrock",
                timeout_seconds=self._timeout,
            )
            return response_content(response)
        return await self._require_supervisor_service().call_text(
            system_prompt,
            user_prompt,
            model=model_id,
            timeout_seconds=self._timeout,
        )

    async def call_claude_text_stream(
        self,
        system_prompt: str,
        user_prompt: str,
        model: str | None = None,
    ) -> AsyncIterator[str]:
        model_id = model or self._default_model
        if model and _requires_bedrock_provider_hint(model):
            async for chunk in self._require_llm_provider()._generate_stream_with_provider_hint(
                _messages(system_prompt, user_prompt),
                model=model_id,
                provider_hint="bedrock",
                timeout_seconds=self._timeout,
            ):
                yield chunk
            return
        async for chunk in self._require_supervisor_service().call_text_stream(
            system_prompt,
            user_prompt,
            model=model_id,
            timeout_seconds=self._timeout,
        ):
            yield chunk

    def _require_supervisor_service(self) -> SupervisorLLMService:
        if self._supervisor_service is None:
            raise LLMServiceNotBoundError("BedrockService LLM services are not bound")
        return self._supervisor_service

    def _require_llm_provider(self) -> Any:
        if self._llm_provider is None:
            raise LLMServiceNotBoundError("BedrockService LLM services are not bound")
        return self._llm_provider


def _requires_bedrock_provider_hint(model: str) -> bool:
    return model != "bedrock_supervisor_model"


def _messages(system_prompt: str, user_prompt: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


bedrock_service = BedrockService()
