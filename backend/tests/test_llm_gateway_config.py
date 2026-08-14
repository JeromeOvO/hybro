import pytest
from pydantic import ValidationError

from common.config.settings import Settings
from llm_gateway.config import LLMGatewayConfig


def test_from_settings_wires_gateway_settings_fields():
    settings = Settings(
        _env_file=None,
        llm_gateway_generation_provider="deepseek",
        deepseek_api_key="test-deepseek-key",
        llm_gateway_max_attempts=4,
        llm_gateway_retry_backoff_seconds=1.25,
        llm_gateway_request_timeout_seconds=31.0,
        llm_gateway_stream_timeout_seconds=62.0,
        llm_gateway_supervisor_json_timeout_seconds=7.5,
        llm_gateway_supervisor_text_timeout_seconds=8.5,
        llm_gateway_supervisor_stream_timeout_seconds=9.5,
        llm_gateway_default_generation_model="custom_generation_route",
        llm_gateway_default_embedding_model="custom_embedding_route",
        llm_gateway_default_supervisor_model="custom_supervisor_route",
    )

    config = LLMGatewayConfig.from_settings(settings)

    assert config == LLMGatewayConfig(
        generation_provider="deepseek",
        max_attempts=4,
        retry_backoff_seconds=1.25,
        request_timeout_seconds=31.0,
        stream_timeout_seconds=62.0,
        supervisor_json_timeout_seconds=7.5,
        supervisor_text_timeout_seconds=8.5,
        supervisor_stream_timeout_seconds=9.5,
        default_generation_model="custom_generation_route",
        default_embedding_model="custom_embedding_route",
        default_supervisor_model="custom_supervisor_route",
    )


def test_settings_rejects_unknown_generation_provider():
    with pytest.raises(
        ValidationError,
        match="LLM_GATEWAY_GENERATION_PROVIDER must be openai or deepseek",
    ):
        Settings(_env_file=None, llm_gateway_generation_provider="other")


def test_settings_requires_deepseek_credentials_when_selected():
    with pytest.raises(
        ValidationError,
        match="DeepSeek generation requires non-empty DEEPSEEK_API_KEY",
    ):
        Settings(
            _env_file=None,
            llm_gateway_generation_provider="deepseek",
            deepseek_api_key="",
        )
