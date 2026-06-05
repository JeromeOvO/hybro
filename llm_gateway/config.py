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
    bedrock_request_timeout_seconds: float = 45.0
    default_generation_model: str = "lead_ai_model"
    default_embedding_model: str = "embedding_model"
    default_supervisor_model: str = "supervisor_model"

    @classmethod
    def from_settings(cls, settings_obj: Any) -> "LLMGatewayConfig":
        return cls(default_supervisor_model="supervisor_model")
