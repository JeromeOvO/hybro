"""
Shared Discovery API client.

Common env loading, config, URL building, error normalization, and HTTP
call logic used by both the CLI and the MCP server.
"""

from __future__ import annotations

import functools
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from common.config.settings import settings

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None


@dataclass(frozen=True)
class DiscoveryConfig:
    api_key: str
    api_url: str
    api_prefix: str
    timeout_seconds: float

    @property
    def discovery_url(self) -> str:
        base = self.api_url.rstrip("/")
        prefix = (
            self.api_prefix
            if self.api_prefix.startswith("/")
            else f"/{self.api_prefix}"
        )
        prefix = prefix.rstrip("/")
        return f"{base}{prefix}/discovery/agents"


def load_env_file() -> None:
    """Load repo-root .env if python-dotenv is available."""
    if load_dotenv is None:
        return
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


@functools.lru_cache(maxsize=1)
def get_config() -> DiscoveryConfig:
    """Build config from env vars (cached after first call).

    Raises RuntimeError when HYBRO_API_KEY is empty.
    """
    load_env_file()
    api_key = os.getenv("HYBRO_API_KEY", "").strip()
    api_url = os.getenv("HYBRO_API_URL", "http://localhost:8000").strip()
    api_prefix = os.getenv("HYBRO_API_PREFIX", "/api/v1").strip()
    timeout_seconds = settings.hybro_timeout_seconds

    if not api_key:
        raise RuntimeError(
            "Missing required environment variable HYBRO_API_KEY. "
            "Set it in your .env or export it."
        )

    return DiscoveryConfig(
        api_key=api_key,
        api_url=api_url,
        api_prefix=api_prefix,
        timeout_seconds=timeout_seconds,
    )


def normalize_error(payload: Any) -> dict[str, str]:
    """Extract a consistent {error, message} dict from various API error shapes."""
    if isinstance(payload, dict):
        detail = payload.get("detail")
        if isinstance(detail, dict):
            return {
                "error": str(detail.get("error", "request_failed")),
                "message": str(detail.get("message", "Request failed")),
            }
        if isinstance(detail, str):
            return {"error": "request_failed", "message": detail}
        if "error" in payload or "message" in payload:
            return {
                "error": str(payload.get("error", "request_failed")),
                "message": str(payload.get("message", "Request failed")),
            }
    return {"error": "request_failed", "message": "Request failed"}


async def call_discovery_api(query: str, limit: int | None) -> dict[str, Any]:
    """POST to the Discovery API and return the JSON response.

    Raises RuntimeError with a prefixed code on any non-200 response or
    network failure.
    """
    config = get_config()

    payload: dict[str, Any] = {"query": query}
    if limit is not None:
        payload["limit"] = limit

    headers = {
        "X-API-Key": config.api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=config.timeout_seconds) as client:
            response = await client.post(
                config.discovery_url, json=payload, headers=headers
            )
    except httpx.TimeoutException as exc:
        raise RuntimeError(
            "service_unavailable: Discovery API request timed out"
        ) from exc
    except httpx.RequestError as exc:
        raise RuntimeError(
            f"service_unavailable: Failed to reach Discovery API ({exc})"
        ) from exc

    if response.status_code == 200:
        try:
            return response.json()
        except ValueError as exc:
            raise RuntimeError(
                "invalid_response: Backend returned invalid JSON"
            ) from exc

    try:
        body = response.json()
    except ValueError:
        body = {"error": "request_failed", "message": response.text or "Request failed"}

    error = normalize_error(body)
    retry_after = response.headers.get("Retry-After")

    if response.status_code == 401:
        raise RuntimeError(f"invalid_key: {error['message']}")
    if response.status_code == 404:
        raise RuntimeError(f"no_agent_found: {error['message']}")
    if response.status_code == 429:
        retry_hint = f" Retry after {retry_after}s." if retry_after else ""
        raise RuntimeError(
            f"rate_limit_exceeded: {error['message']}.{retry_hint}".strip()
        )
    if response.status_code >= 500:
        raise RuntimeError(f"internal_error: {error['message']}")

    raise RuntimeError(f"{error['error']}: {error['message']}")
