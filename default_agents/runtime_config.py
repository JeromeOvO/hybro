"""Read the CLI's scoped Agent projection; never read dotenv or the full auth store."""

from dataclasses import dataclass, field
from functools import lru_cache
import json
import os
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AgentConfig:
    base_url: str
    token: str = field(repr=False)
    image_size: str = "1024x1024"
    text_model: str = "gpt-4o-mini"  # SDK label; backend setup chooses the real model.
    image_model: str = "gpt-image-1"


@lru_cache(maxsize=1)
def get_config() -> AgentConfig:
    try:
        raw = os.environ.get("HYBRO_AGENT_CONFIG", "")
        if len(raw) > 16384:
            raise ValueError
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"base_url", "token", "image_size"}:
            raise ValueError
        if not all(isinstance(item, str) for item in value.values()):
            raise ValueError
        url = urlsplit(value["base_url"])
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password:
            raise ValueError
        if len(value["token"].encode()) < 32 or value["image_size"] not in {"1024x1024", "1536x1024", "1024x1536", "auto"}:
            raise ValueError
        return AgentConfig(**value)
    except (ValueError, TypeError):
        raise RuntimeError("Missing or invalid Agent configuration; start through hybro.") from None
