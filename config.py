import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    # MongoDB settings
    MONGODB_URL: str = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "multiple-agents-system")

    # Pinecone settings
    PINECONE_API_KEY: str = os.getenv("PINECONE_API_KEY")
    PINECONE_ENVIRONMENT: str = os.getenv("PINECONE_ENVIRONMENT", "us-east1-aws")
    PINECONE_INDEX_NAME: str = os.getenv("PINECONE_INDEX_NAME", "bromatch-test")
    PINECONE_HOST: str = os.getenv("PINECONE_HOST")

    # OpenAI settings
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY")
    LEAD_AI_MODEL: str = os.getenv("LEAD_AI_MODEL", "gpt-4o")
    CLASSIFIER_AI_MODEL: str = os.getenv("CLASSIFIER_AI_MODEL", "gpt-4o")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")

    # Google Gemini settings
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "your-gemini-api-key")
    GEMINI_MODEL_NAME: str = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-pro")
    GEMINI_EMBEDDING_MODEL_NAME: str = os.getenv(
        "GEMINI_EMBEDDING_MODEL_NAME", "embedding-001"
    )

    # Add these new fieldss
    google_api_key: Optional[str] = None
    llama_cloud_api_key: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
