# config.py
from pydantic import field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    app_env: str = "development"  # development, staging, production

    frontend_origins: str | list[str] = ["http://localhost:3000"]
    api_prefix: str = "/api/v1"

    mongodb_url: str = "localhost:27017"
    mongodb_db_name: str = "hybro"
    mongodb_host: str = "127.0.0.1"
    mongodb_port: int = 27017
    mongodb_username: str = ""
    mongodb_password: str = ""

    pinecone_api_key: str = ""
    pinecone_environment: str = ""
    pinecone_index_name: str = "agentmatch"
    pinecone_host: str = ""

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

    debate_rounds: int = 1  # todo: can be as parameter
    parse_confidence_threshold: float = 0.3

    # Clerk Authentication
    clerk_secret_key: str = ""  # Clerk Secret Key for backend API

    # Agent Health Check Settings
    agent_health_check_enabled: bool = True  # enable/disable agent health check

    # Discovery API Settings
    discovery_confidence_threshold: float = 0.3  # Minimum similarity score to return an agent
    discovery_default_limit: int = 5  # Default number of agents to return
    discovery_query_expansion_threshold: int = 5  # Maximum word count for query expansion
    discovery_rate_limit_per_key: int | None = 100  # Requests per API key per hour (None = unlimited)
    discovery_rate_limit_global: int | None = 10000  # Total requests per hour across all keys (None = unlimited)
    
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
    processing_status_expiry_minutes: int = 30  # Clear stuck processing status older than this

    # Change stream reconnection backoff
    cs_backoff_base: float = 1.0  # initial delay in seconds
    cs_backoff_max: float = 30.0  # ceiling delay in seconds
    cs_backoff_factor: float = 2.0  # multiplier per retry
    cs_jitter_fraction: float = 0.25  # ±25% random jitter

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
