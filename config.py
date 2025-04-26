import os
from pydantic_settings import BaseSettings
from typing import Optional

class Settings(BaseSettings):
    # MongoDB settings
    MONGODB_URL: str = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
    MONGODB_DB_NAME: str = os.getenv("MONGODB_DB_NAME", "multiple-agents-system")
    
    # Pinecone settings
    PINECONE_API_KEY: str = os.getenv("PINECONE_API_KEY", "pcsk_5gQzcD_PauDu3LAUTfD9sqoNH8QtgdFzD9ALcqAHgapU6trEqviyc1uAExApmpTDkEhTp6")
    PINECONE_ENVIRONMENT: str = os.getenv("PINECONE_ENVIRONMENT", "us-east1-aws")
    PINECONE_INDEX_NAME: str = os.getenv("PINECONE_INDEX_NAME", "bromatch-test")
    
    # OpenAI settings
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "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")
    LEAD_AI_MODEL: str = os.getenv("LEAD_AI_MODEL", "gpt-4o")
    CLASSIFIER_AI_MODEL: str = os.getenv("CLASSIFIER_AI_MODEL", "gpt-4o")
    EMBEDDING_MODEL: str = os.getenv("EMBEDDING_MODEL", "text-embedding-3-small")
    
    # Google Gemini settings
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "your-gemini-api-key")
    GEMINI_MODEL_NAME: str = os.getenv("GEMINI_MODEL_NAME", "gemini-1.5-pro")
    GEMINI_EMBEDDING_MODEL_NAME: str = os.getenv("GEMINI_EMBEDDING_MODEL_NAME", "embedding-001")
    
    # Add these new fieldss
    google_api_key: Optional[str] = None
    llama_cloud_api_key: Optional[str] = None
    
    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = Settings() 