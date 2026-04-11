# config.py
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"  # development, staging, production

    frontend_origins: str | list[str] = [
        "http://localhost:3000",
        "http://dev.localhost:3000",
    ]
    api_prefix: str = "/api/v1"

    mongodb_url: str = "localhost:27017"
    mongodb_db_name: str = "hybro"
    mongodb_host: str = "127.0.0.1"
    mongodb_port: int = 27017
    mongodb_username: str = ""
    mongodb_password: str = ""

    pinecone_api_key: str = ""
    pinecone_index_name: str = "agentmatch"

    openai_api_key: str = ""
    lead_ai_model: str = "gpt-5-mini"
    classifier_ai_model: str = "gpt-5-mini"
    embedding_model: str = "text-embedding-3-small"

    google_api_key: str = ""
    gemini_model_name: str = "gemini-2.0-flash"
    gemini_embedding_model_name: str = "gemini-embedding-exp-03-07"

    log_level: str = "INFO"
    log_format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    log_path: str = "logs/app.log"
    log_backup_count: int = 5
    log_max_bytes: int = 10485760  # 10 MB

    debate_rounds: int = 2  # todo: can be as parameter
    parse_confidence_threshold: float = 0.3

    # Clerk Authentication
    clerk_secret_key: str = ""  # Clerk Secret Key for backend API

    # Agent Health Check Settings
    agent_health_check_enabled: bool = True  # enable/disable agent health check
    cloud_health_check_timeout: float = 5.0  # seconds for on-demand cloud agent probe
    cloud_health_cache_ttl: float = 30.0  # cache healthy/unhealthy result for this long

    # Agent Capability Issue Tracking
    capability_issue_threshold: int = 2  # Exclude agents with >= this many open issues

    # Discovery API Settings
    discovery_confidence_threshold: float = (
        0.3  # Minimum similarity score to return an agent
    )
    discovery_default_limit: int = 5  # Default number of agents to return
    discovery_query_expansion_threshold: int = (
        5  # Maximum word count for query expansion
    )
    discovery_rate_limit_per_key: int | None = (
        100  # Requests per API key per hour (None = unlimited)
    )
    discovery_rate_limit_global: int | None = (
        10000  # Total requests per hour across all keys (None = unlimited)
    )
    hybro_timeout_seconds: float = 45.0

    # Gateway API Settings
    gateway_base_url: str = (
        ""  # e.g. https://api.hybro.ai/api/v1 — if empty, derived at runtime
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
    allowed_agent_hosts: set[str] = (
        set()
    )  # Comma-separated allowlist of trusted agent hosts (optional)
    max_tasks_per_user: int = 100  # Max concurrent non-terminal tasks per user
    max_tasks_per_room: int = 50  # Max concurrent non-terminal tasks per room
    stale_check_minutes: int = 10  # Poll tasks not updated in this time
    task_expiry_hours: int = 4  # Auto-fail tasks older than this
    pending_task_warning_hours: int = 1  # Warn (log) after this time
    orphan_threshold_minutes: int = 2  # Recover orphaned messages older than this
    processing_status_expiry_minutes: int = (
        30  # Clear stuck processing status older than this
    )

    # Change stream reconnection backoff
    cs_backoff_base: float = 1.0  # initial delay in seconds
    cs_backoff_max: float = 30.0  # ceiling delay in seconds
    cs_backoff_factor: float = 2.0  # multiplier per retry
    cs_jitter_fraction: float = 0.25  # ±25% random jitter

    # Event Broker (cross-instance SSE fan-out + cancellation)
    redis_url: str = (
        ""  # e.g. "redis://localhost:6379/0" — empty string disables broker
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
    # See CONTEXT_MEMORY_SYSTEM_DESIGN.md §14 for specification
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
    memory_search_vector_weight: float = 0.7  # Weight for vector similarity
    memory_search_keyword_weight: float = 0.3  # Weight for BM25 keyword matching
    memory_search_temporal_decay_enabled: bool = True  # Enable recency boost
    memory_search_half_life_days: int = 30  # Half-life for temporal decay
    memory_search_mmr_lambda: float = (
        0.7  # MMR diversity parameter (0=diverse, 1=relevant)
    )
    memory_search_max_results: int = 10  # Max results to return
    memory_search_max_snippet_chars: int = 500  # Max chars per snippet
    memory_search_index_name: str = "room-memory"  # Pinecone index for memory

    # AWS S3 (file uploads and binary content storage)
    s3_bucket_name: str = ""
    s3_region: str = "us-east-1"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    s3_presigned_url_ttl: int = 3600  # presigned URL validity in seconds
    max_file_size_mb: int = 50

    # AWS Bedrock Settings (Supervisor LLM)
    bedrock_region: str = "us-east-1"
    bedrock_supervisor_model: str = "us.anthropic.claude-sonnet-4-5-20250929-v1:0"
    use_bedrock_supervisor: bool = False

    # Graceful Shutdown Settings
    shutdown_drain_seconds: float = (
        5.0  # Drain period for SSE connections during shutdown
    )

    # Connection pool tuning (per-worker; total = workers * value)
    mongodb_max_pool_size: int = 50
    mongodb_min_pool_size: int = 10
    redis_max_connections: int = 50

    class Config:
        env_file = ".env"
        extra = "ignore"
        # Ensure .env is read from the project root regardless of CWD
        import os

        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_file = os.path.join(base_dir, ".env")

    @field_validator("frontend_origins", mode="before")
    @classmethod
    def parse_frontend_origins(cls, v):
        if isinstance(v, str):
            # Split comma-separated string into list
            return [url.strip() for url in v.split(",") if url.strip()]
        return v

    @field_validator("allowed_agent_hosts", mode="before")
    @classmethod
    def parse_allowed_agent_hosts(cls, v):
        if isinstance(v, str) and v.strip():
            # Split comma-separated string into set
            return {host.strip() for host in v.split(",") if host.strip()}
        return set() if not v else v


settings = Settings()
