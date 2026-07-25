from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class LLMGatewayConfig:
    max_attempts: int = 2
    retry_backoff_seconds: float = 0.2
    request_timeout_seconds: float = 60.0
    stream_timeout_seconds: float = 120.0
    supervisor_json_timeout_seconds: float = 30.0
    supervisor_text_timeout_seconds: float = 90.0
    supervisor_stream_timeout_seconds: float = 90.0
    default_generation_model: str = "lead_ai_model"
    default_embedding_model: str = "embedding_model"
    default_supervisor_model: str = "supervisor_model"

    @classmethod
    def from_settings(cls, settings_obj: Any) -> "LLMGatewayConfig":
        defaults = cls()
        return cls(
            max_attempts=_setting(
                settings_obj, "llm_gateway_max_attempts", defaults.max_attempts
            ),
            retry_backoff_seconds=_setting(
                settings_obj,
                "llm_gateway_retry_backoff_seconds",
                defaults.retry_backoff_seconds,
            ),
            request_timeout_seconds=_setting(
                settings_obj,
                "llm_gateway_request_timeout_seconds",
                defaults.request_timeout_seconds,
            ),
            stream_timeout_seconds=_setting(
                settings_obj,
                "llm_gateway_stream_timeout_seconds",
                defaults.stream_timeout_seconds,
            ),
            supervisor_json_timeout_seconds=_setting(
                settings_obj,
                "llm_gateway_supervisor_json_timeout_seconds",
                defaults.supervisor_json_timeout_seconds,
            ),
            supervisor_text_timeout_seconds=_setting(
                settings_obj,
                "llm_gateway_supervisor_text_timeout_seconds",
                defaults.supervisor_text_timeout_seconds,
            ),
            supervisor_stream_timeout_seconds=_setting(
                settings_obj,
                "llm_gateway_supervisor_stream_timeout_seconds",
                defaults.supervisor_stream_timeout_seconds,
            ),
            default_generation_model=_string_setting(
                settings_obj,
                "llm_gateway_default_generation_model",
                defaults.default_generation_model,
            ),
            default_embedding_model=_string_setting(
                settings_obj,
                "llm_gateway_default_embedding_model",
                defaults.default_embedding_model,
            ),
            default_supervisor_model=_string_setting(
                settings_obj,
                "llm_gateway_default_supervisor_model",
                defaults.default_supervisor_model,
            ),
        )


def _setting(settings_obj: Any, name: str, default: Any) -> Any:
    value = getattr(settings_obj, name, default)
    return default if value is None else value


def _string_setting(settings_obj: Any, name: str, default: str) -> str:
    value = _setting(settings_obj, name, default)
    text = str(value).strip()
    return text or default
