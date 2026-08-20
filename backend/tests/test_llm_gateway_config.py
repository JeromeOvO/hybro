from pathlib import Path

import pytest

from common.config.settings import Settings
from llm_gateway.config import LLMGatewayConfig
from llm_gateway.errors import UnsupportedConfiguredProvider


def _settings(**overrides):
    values = {
        "deepseek_api_key": "",
        "openai_api_key": "",
        "google_api_key": "",
        "gemini_api_key": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_from_settings_wires_gateway_settings_fields():
    settings = _settings(
        deepseek_api_key="test-deepseek-key",
        llm_gateway_generation_provider="deepseek",
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


def test_generation_provider_is_explicit_and_stale_gemini_cannot_override_it():
    all_configured = _settings(
        deepseek_api_key="deepseek-key",
        openai_api_key="openai-key",
        google_api_key="google-key",
    )
    without_deepseek = _settings(
        openai_api_key="openai-key",
        google_api_key="google-key",
    )
    gemini_only = _settings(google_api_key="google-key")
    gemini_alias_only = _settings(gemini_api_key="gemini-key")

    assert (
        LLMGatewayConfig.from_settings(all_configured).generation_provider == "openai"
    )
    explicit_deepseek = _settings(
        deepseek_api_key="deepseek-key",
        openai_api_key="openai-key",
        google_api_key="google-key",
        llm_gateway_generation_provider="deepseek",
    )
    assert (
        LLMGatewayConfig.from_settings(explicit_deepseek).generation_provider
        == "deepseek"
    )
    assert (
        LLMGatewayConfig.from_settings(without_deepseek).generation_provider == "openai"
    )
    with pytest.raises(UnsupportedConfiguredProvider):
        LLMGatewayConfig.from_settings(gemini_only)
    with pytest.raises(UnsupportedConfiguredProvider):
        LLMGatewayConfig.from_settings(gemini_alias_only)


def test_generation_provider_keeps_zero_config_openai_degraded_mode():
    assert LLMGatewayConfig.from_settings(_settings()).generation_provider == "openai"


@pytest.mark.parametrize(
    "settings",
    [
        _settings(deepseek_api_key="deepseek-key"),
        _settings(
            openai_api_key="openai-key",
            llm_gateway_generation_provider="deepseek",
        ),
    ],
)
def test_generation_provider_rejects_key_for_a_different_selected_provider(settings):
    with pytest.raises(
        UnsupportedConfiguredProvider,
        match="LLM_GATEWAY_GENERATION_PROVIDER selects",
    ):
        LLMGatewayConfig.from_settings(settings)


def test_env_example_documents_explicit_deepseek_opt_in():
    text = (Path(__file__).parents[2] / ".env.example").read_text()

    assert "LLM_GATEWAY_GENERATION_PROVIDER=openai" in text
    assert "DeepSeek-primary deployments must set this value to deepseek" in text
    assert "first priority" not in text
    assert "provider priority" not in text


def test_generation_provider_rejects_selected_route_without_model():
    settings = _settings(
        deepseek_api_key="deepseek-key",
        deepseek_model_name="",
        openai_api_key="openai-key",
        llm_gateway_generation_provider="deepseek",
    )

    with pytest.raises(UnsupportedConfiguredProvider, match="model route is empty"):
        LLMGatewayConfig.from_settings(settings)
