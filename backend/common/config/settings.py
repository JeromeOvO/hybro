import math
import os

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"  # development, staging, production

    frontend_origins: str | list[str] = [
        "http://localhost:3000",
        "https://hybro.ai",
    ]
    api_prefix: str = "/api/v1"

    mongodb_url: str = "localhost:27017"
    mongodb_db_name: str = "hybro"
    mongodb_host: str = "127.0.0.1"
    mongodb_port: int = 27017
    mongodb_username: str = ""
    mongodb_password: str = ""

    openai_api_key: str = ""
    lead_ai_model: str = "gpt-5-mini"
    classifier_ai_model: str = "gpt-5-mini"
    embedding_model: str = "text-embedding-3-small"
    supervisor_model: str | None = None

    google_api_key: str = ""
    gemini_api_key: str = ""
    gemini_model_name: str = "gemini-2.0-flash"
    gemini_embedding_model_name: str = "gemini-embedding-exp-03-07"

    # LLM gateway routing and runtime policy
    llm_gateway_max_attempts: int = 2
    llm_gateway_retry_backoff_seconds: float = 0.2
    llm_gateway_request_timeout_seconds: float = 60.0
    llm_gateway_stream_timeout_seconds: float = 120.0
    llm_gateway_supervisor_json_timeout_seconds: float = 30.0
    llm_gateway_supervisor_text_timeout_seconds: float = 90.0
    llm_gateway_supervisor_stream_timeout_seconds: float = 90.0
    llm_gateway_default_generation_model: str = "lead_ai_model"
    llm_gateway_default_embedding_model: str = "embedding_model"
    llm_gateway_default_supervisor_model: str = "supervisor_model"

    log_level: str = "INFO"
    log_format: str = "auto"

    # Feature Flags (runtime-toggleable behavior gates)
    feature_run_event_sse: bool = False
    feature_run_watchdog: bool = True
    orchestration_outcome_guardrails: bool = True

    # Execution Tuning
    supervisor_max_steps: int = 8
    run_watchdog_stale_minutes: int = 90

    # Agent Health
    agent_health_check_interval: int = 3600

    # Local Agent Discovery (Docker backend -> host gateway)
    local_agent_discovery_enabled: bool = False
    local_agent_discovery_host: str = "host.docker.internal"
    local_agent_discovery_port_start: int = Field(default=1024, ge=1, le=65535)
    local_agent_discovery_port_end: int = Field(default=65535, ge=1, le=65535)
    local_agent_discovery_interval_seconds: int = Field(default=120, gt=0)
    local_agent_discovery_connect_timeout_seconds: float = Field(default=0.05, gt=0)
    local_agent_discovery_probe_timeout_seconds: float = Field(default=3.0, gt=0)

    # Compaction
    compaction_concurrency: int = 5

    debate_rounds: int = 2  # todo: can be as parameter
    parse_confidence_threshold: float = 0.3

    # Clerk Authentication
    clerk_secret_key: str = ""  # Clerk Secret Key for backend API
    auth_mode: str = "mock"  # "mock" or "clerk"

    # Default-agent registrar bootstrap (service identity for one-shot registration)
    default_agent_registrar_token: str = ""
    # provider_id assigned to agents registered through the service token.
    default_agent_provider_id: str = "Hybro AI"

    # Agent Health Check Settings
    agent_health_check_enabled: bool = True  # enable/disable agent health check
    cloud_health_check_timeout: float = 5.0  # seconds for on-demand cloud agent probe
    cloud_health_cache_ttl: float = 30.0  # cache healthy/unhealthy result for this long

    # Agent Capability Issue Tracking
    capability_issue_threshold: int = 2  # Exclude agents with >= this many open issues

    # Discovery API Settings
    discovery_default_limit: int = 5  # Default number of agents to return
    discovery_rate_limit_per_key: int | None = (
        100  # Requests per API key per hour (None = unlimited)
    )
    discovery_rate_limit_global: int | None = (
        10000  # Total requests per hour across all keys (None = unlimited)
    )
    hybro_timeout_seconds: float = 45.0

    # Gateway API Settings
    gateway_base_url: str = (
        ""  # e.g. https://api.hybro.ai/api/v1 - if empty, derived at runtime
    )
    gateway_rate_limit_per_key: int | None = (
        200  # Requests per API key per hour (None = unlimited)
    )
    gateway_rate_limit_global: int | None = (
        20000  # Total requests per hour across all keys (None = unlimited)
    )

    # Relay (Hub Phase 2) Settings
    relay_heartbeat_interval: int = 30  # seconds
    relay_offline_queue_max: int = 100  # per hub
    relay_offline_queue_ttl: int = 86400  # 24 hours in seconds
    relay_hub_agent_heartbeat_miss_limit: int = 3
    relay_offline_grace_period: int = (
        120  # seconds before rejecting messages to a disconnected hub
    )
    relay_stream_maxlen: int = 10_000
    relay_hub_heartbeat_ttl: int = 90  # 3x relay_heartbeat_interval

    # A2A Long-Running Tasks Settings
    webhook_base_url: str = (
        ""  # Public URL where agents send webhooks (e.g., https://api.example.com)
    )
    webhook_signing_key: str = ""  # Secret key for HMAC token hashing (min 32 chars)
    max_tasks_per_user: int = 100  # Max concurrent non-terminal tasks per user
    max_tasks_per_room: int = 50  # Max concurrent non-terminal tasks per room
    stale_check_minutes: int = 10  # Poll tasks not updated in this time
    task_expiry_hours: int = 4  # Auto-fail tasks older than this
    pending_task_warning_hours: int = 1  # Warn (log) after this time
    orphan_threshold_minutes: int = 2  # Recover orphaned messages older than this
    processing_status_expiry_minutes: int = (
        30  # Clear stuck processing status older than this
    )

    # Delivery / SSE extraction settings
    heartbeat_interval_seconds: float = 30.0
    cancellation_ttl_seconds: int = 3600
    terminal_dedup_ttl_seconds: int = 300
    cancellation_cache_maxsize: int = 10_000
    cancellation_token_cache_maxsize: int = 10_000
    terminal_dedup_cache_maxsize: int = 10_000
    redis_internal_channel: str = "internal:global"
    redis_dead_letter_channel: str = "delivery:dead_letter"
    dead_letter_memory_maxlen: int = 1000
    handler_shutdown_timeout_seconds: float = 5.0
    redis_subscription_reserved_connections: int = 10
    redis_room_subscription_production_limit: int = 40
    terminal_processing_statuses: frozenset[str] = frozenset(
        {"completed", "failed", "canceled", "rejected", "rate_limited", "error"}
    )

    # Change stream reconnection backoff
    cs_backoff_base: float = 1.0  # initial delay in seconds
    cs_backoff_max: float = 30.0  # ceiling delay in seconds
    cs_backoff_factor: float = 2.0  # multiplier per retry
    cs_jitter_fraction: float = 0.25  # +/-25% random jitter

    # Event Broker (cross-instance SSE fan-out + cancellation)
    redis_url: str = (
        ""  # e.g. "redis://localhost:6379/0" - empty string disables broker
    )
    redis_sse_channel_prefix: str = "sse:room:"  # per-room channel: sse:room:{room_id}
    redis_cancel_channel: str = (
        "cancel:global"  # single channel for all cancellation events
    )
    redis_reconnect_delay: float = 1.0  # initial reconnect delay (seconds)
    redis_reconnect_max_delay: float = 30.0  # max reconnect delay ceiling (seconds)
    redis_cancel_key_prefix: str = "cancelled:"
    redis_terminal_key_prefix: str = "terminal:"

    # ===========================================
    # Context & Memory System Settings
    # See CONTEXT_MEMORY_SYSTEM_DESIGN.md section 14 for specification
    # ===========================================

    # Token Budget Settings
    context_model_window: int = 128000  # Model's max context window
    context_system_prompt_tokens: int = 2000  # Reserved for system prompt
    context_tool_schema_tokens: int = 3000  # Reserved for tool schemas
    context_response_reserve_tokens: int = 4000  # Reserved for response
    context_room_pct: float = 0.15  # % of remaining for room context
    context_history_pct: float = 0.60  # % of remaining for conversation history
    context_task_pct: float = 0.25  # % of remaining for current task

    # Compaction Settings (LOSSLESS - pointer-based, not summarization)
    compaction_enabled: bool = True  # Enable/disable auto-compaction
    compaction_max_full_turns: int = 20  # Max turns to keep in FULL representation
    compaction_max_total_tokens: int = (
        80000  # Trigger compaction when full turns exceed this
    )
    compaction_preserve_recent: int = 10  # Always keep this many recent turns FULL
    compaction_content_ttl_days: int = 0  # TTL for stored content (0 = forever)

    # Memory Search Settings
    memory_search_enabled: bool = True  # Enable/disable memory search
    memory_search_temporal_decay_enabled: bool = True  # Enable recency boost
    memory_search_half_life_days: int = 30  # Half-life for temporal decay
    memory_search_max_results: int = 10  # Max results to return
    memory_search_max_candidates: int = 1000  # Max keyword candidates to rank
    memory_search_max_snippet_chars: int = 300  # Max chars per snippet

    # Local room file storage
    hybro_file_dir: str = Field(
        default="",
        validation_alias=AliasChoices("HYBRO_FILE_DIR", "hybro_file_dir"),
    )
    a2a_inline_file_max_raw_bytes: int = 5 * 1024 * 1024
    a2a_inline_message_max_encoded_bytes: int = 0

    # Graceful Shutdown Settings
    shutdown_drain_seconds: float = (
        5.0  # Drain period for SSE connections during shutdown
    )

    # Connection pool tuning (per-worker; total = workers * value)
    mongodb_max_pool_size: int = 50
    mongodb_min_pool_size: int = 10
    redis_max_connections: int = 50

    class Config:
        extra = "ignore"
        base_dir = os.path.dirname(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        )
        env_file = os.path.join(base_dir, ".env")

    @field_validator("frontend_origins", mode="before")
    @classmethod
    def parse_frontend_origins(cls, v):
        if isinstance(v, str):
            # Split comma-separated string into list
            return [url.strip() for url in v.split(",") if url.strip()]
        return v

    @field_validator("log_level", mode="before")
    @classmethod
    def validate_log_level(cls, value):
        normalized = str(value or "INFO").strip().upper()
        if normalized not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError(
                "LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL"
            )
        return normalized

    @field_validator("log_format", mode="before")
    @classmethod
    def validate_log_format(cls, value):
        normalized = str(value or "auto").strip().lower()
        if normalized not in {"auto", "json", "logfmt"}:
            raise ValueError("LOG_FORMAT must be auto, json, or logfmt")
        return normalized

    @field_validator("terminal_processing_statuses", mode="before")
    @classmethod
    def parse_terminal_processing_statuses(cls, v):
        if isinstance(v, str):
            return frozenset(
                status.strip().lower() for status in v.split(",") if status.strip()
            )
        if v is None:
            return frozenset()
        return frozenset(str(status).strip().lower() for status in v)

    @field_validator("compaction_concurrency", mode="before")
    @classmethod
    def normalize_compaction_concurrency(cls, value):
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 5

    @field_validator("webhook_signing_key", mode="before")
    @classmethod
    def validate_webhook_signing_key(cls, value):
        key = str(value or "").strip()
        if key and len(key.encode()) < 32:
            raise ValueError("WEBHOOK_SIGNING_KEY must be at least 32 bytes")
        return key

    @field_validator("feature_run_event_sse", mode="before")
    @classmethod
    def normalize_feature_run_event_sse(cls, value):
        if value is None or str(value).strip() == "":
            return False
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @field_validator("feature_run_watchdog", mode="before")
    @classmethod
    def normalize_feature_run_watchdog(cls, value):
        if value is None or str(value).strip() == "":
            return True
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() not in {"0", "false", "off"}

    @field_validator("orchestration_outcome_guardrails", mode="before")
    @classmethod
    def normalize_orchestration_outcome_guardrails(cls, value):
        if value is None or str(value).strip() == "":
            return False
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @field_validator("a2a_inline_file_max_raw_bytes", mode="before")
    @classmethod
    def normalize_a2a_inline_file_max_raw_bytes(cls, value):
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return 5 * 1024 * 1024

    @field_validator("a2a_inline_message_max_encoded_bytes", mode="before")
    @classmethod
    def normalize_a2a_inline_message_max_encoded_bytes(cls, value):
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    @model_validator(mode="after")
    def apply_a2a_inline_encoded_default(self):
        if self.a2a_inline_message_max_encoded_bytes <= 0:
            self.a2a_inline_message_max_encoded_bytes = 4 * math.ceil(
                self.a2a_inline_file_max_raw_bytes / 3
            )
        return self

    @model_validator(mode="after")
    def apply_gemini_api_key_fallback(self):
        if not str(self.google_api_key or "").strip():
            self.google_api_key = self.gemini_api_key or ""
        return self

    @property
    def is_gunicorn(self) -> bool:
        """Detect gunicorn from server-injected runtime metadata."""
        return os.environ.get("SERVER_SOFTWARE", "").startswith("gunicorn")


settings = Settings()


__all__ = [
    "Settings",
    "settings",
]
