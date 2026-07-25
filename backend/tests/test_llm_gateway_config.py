from common.config.settings import Settings
from llm_gateway.config import LLMGatewayConfig


def test_from_settings_wires_gateway_settings_fields():
    settings = Settings(
        _env_file=None,
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
