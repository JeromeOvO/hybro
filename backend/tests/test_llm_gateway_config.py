import pytest
from pydantic import SecretStr

from common.config.loader import Settings
from common.config.runtime_config import (
    ApiKeyCredential,
    RuntimeConfig,
    RuntimeConfigurationError,
    RuntimeModels,
    RuntimeProvider,
)
from common.config.runtime_store import RuntimeConfigStore
from llm_gateway.config import LLMGatewayConfig


def _configure(tmp_path, monkeypatch, provider="openai", model="gpt-5-mini"):
    home = tmp_path / "runtime"
    monkeypatch.setenv("HYBRO_HOME", str(home))
    RuntimeConfigStore(home).save(
        RuntimeConfig(
            provider=RuntimeProvider(id=provider, auth="api_key"),
            models=RuntimeModels(text=model),
        ),
        ApiKeyCredential(provider=provider, api_key=SecretStr("fixture-key")),
        {},
    )


def test_from_settings_wires_runtime_policy_and_setup_provider(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch, "deepseek", "deepseek-v4-flash")
    settings = Settings(
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
    assert LLMGatewayConfig.from_settings(settings) == LLMGatewayConfig(
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


@pytest.mark.parametrize("legacy_route", ["openai", "deepseek", "invalid"])
def test_setup_is_the_only_provider_source(tmp_path, monkeypatch, legacy_route):
    _configure(tmp_path, monkeypatch)
    settings = Settings(
        llm_gateway_generation_provider=legacy_route,
        deepseek_api_key="ignored",
        google_api_key="ignored",
    )
    assert LLMGatewayConfig.from_settings(settings).generation_provider == "openai"


@pytest.mark.parametrize("key", ["", "ignored-key"])
def test_no_setup_fails_even_with_a_legacy_key(tmp_path, monkeypatch, key):
    monkeypatch.setenv("HYBRO_HOME", str(tmp_path / "missing"))
    with pytest.raises(RuntimeConfigurationError, match="hybro setup"):
        LLMGatewayConfig.from_settings(Settings(openai_api_key=key))


def test_orchestrator_defaults_resolve_without_env_example(tmp_path, monkeypatch):
    _configure(tmp_path, monkeypatch)
    from execution.adapters.profiles import OrchestratorProfileResolver
    from llm_gateway.model_registry import ModelRegistryImpl

    settings = Settings()
    registry = ModelRegistryImpl(settings)
    resolver = OrchestratorProfileResolver(
        model_registry=registry, settings_obj=settings
    )
    assert resolver.resolve("fast").thinking_level == "low"
    assert resolver.resolve("ultimate").thinking_level == "high"
