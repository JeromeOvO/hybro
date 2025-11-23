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

    debate_rounds: int = 2 # todo: can be as parameter
    parse_confidence_threshold: float = 0.3

    # Clerk Authentication
    clerk_jwks_url: str = ""  # JWKS URL for JWT token verification

    class Config:
        env_file = ".env"
        extra = "ignore"

    @field_validator('frontend_origins', mode='before')
    @classmethod
    def parse_frontend_origins(cls, v):
        if isinstance(v, str):
            # Split comma-separated string into list
            return [url.strip() for url in v.split(',') if url.strip()]
        return v


settings = Settings()

