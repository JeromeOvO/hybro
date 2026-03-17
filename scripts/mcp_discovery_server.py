#!/usr/bin/env python3
"""
MCP Discovery API Server.

Runs a local MCP stdio server that exposes one tool:
    - discover_agents(query: str, limit: int | None)

Environment variables:
    HYBRO_API_KEY     Required Discovery API key.
    HYBRO_API_URL     Optional base URL, default: http://localhost:8000
    HYBRO_API_PREFIX  Optional API prefix, default: /api/v1

Timeout is read from config.settings (hybro_timeout_seconds).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Allow importing config when run as script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx
from mcp.server.fastmcp import FastMCP

from config.settings import settings

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - optional in some environments
    load_dotenv = None


@dataclass(frozen=True)
class DiscoveryServerConfig:
    api_key: str
    api_url: str
    api_prefix: str
    timeout_seconds: float

    @property
    def discovery_url(self) -> str:
        base = self.api_url.rstrip("/")
        prefix = self.api_prefix if self.api_prefix.startswith("/") else f"/{self.api_prefix}"
        prefix = prefix.rstrip("/")
        return f"{base}{prefix}/discovery/agents"


_CONFIG: DiscoveryServerConfig | None = None
_HTTP_CLIENT: httpx.AsyncClient | None = None
mcp = FastMCP("hybro-discovery")


def _load_env_file() -> None:
    """Load repo-root .env if python-dotenv is available."""
    if load_dotenv is None:
        return

    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


def _get_config() -> DiscoveryServerConfig:
    global _CONFIG
    if _CONFIG is not None:
        return _CONFIG

    _load_env_file()
    api_key = os.getenv("HYBRO_API_KEY", "").strip()
    api_url = os.getenv("HYBRO_API_URL", "http://localhost:8000").strip()
    api_prefix = os.getenv("HYBRO_API_PREFIX", "/api/v1").strip()
    timeout_seconds = settings.hybro_timeout_seconds

    if not api_key:
        raise RuntimeError(
            "Missing required environment variable HYBRO_API_KEY. "
            "Set it in your .env or MCP server env config."
        )

    _CONFIG = DiscoveryServerConfig(
        api_key=api_key,
        api_url=api_url,
        api_prefix=api_prefix,
        timeout_seconds=timeout_seconds,
    )
    return _CONFIG


def _get_http_client(config: DiscoveryServerConfig) -> httpx.AsyncClient:
    """Return a shared AsyncClient instance."""
    global _HTTP_CLIENT
    if _HTTP_CLIENT is None:
        _HTTP_CLIENT = httpx.AsyncClient(timeout=config.timeout_seconds)
    return _HTTP_CLIENT


def _normalize_backend_error(payload: Any) -> dict[str, str]:
    if isinstance(payload, dict):
        if isinstance(payload.get("detail"), dict):
            detail = payload["detail"]
            return {
                "error": str(detail.get("error", "request_failed")),
                "message": str(detail.get("message", "Request failed")),
            }
        if "error" in payload or "message" in payload:
            return {
                "error": str(payload.get("error", "request_failed")),
                "message": str(payload.get("message", "Request failed")),
            }
    return {"error": "request_failed", "message": "Request failed"}


async def _call_discovery_api(query: str, limit: int | None) -> dict[str, Any]:
    config = _get_config()

    payload: dict[str, Any] = {"query": query}
    if limit is not None:
        payload["limit"] = limit

    headers = {
        "X-API-Key": config.api_key,
        "Content-Type": "application/json",
    }

    client = _get_http_client(config)

    try:
        response = await client.post(config.discovery_url, json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise RuntimeError("service_unavailable: Discovery API request timed out") from exc
    except httpx.RequestError as exc:
        raise RuntimeError(f"service_unavailable: Failed to reach Discovery API ({exc})") from exc

    if response.status_code == 200:
        return response.json()

    try:
        body = response.json()
    except ValueError:
        body = {"error": "request_failed", "message": response.text or "Request failed"}

    error_payload = _normalize_backend_error(body)
    retry_after = response.headers.get("Retry-After")

    if response.status_code == 401:
        raise RuntimeError(f"invalid_key: {error_payload['message']}")
    if response.status_code == 404:
        raise RuntimeError(f"no_agent_found: {error_payload['message']}")
    if response.status_code == 429:
        retry_hint = f" Retry after {retry_after}s." if retry_after else ""
        raise RuntimeError(f"rate_limit_exceeded: {error_payload['message']}.{retry_hint}".strip())
    if response.status_code >= 500:
        raise RuntimeError(f"internal_error: {error_payload['message']}")

    raise RuntimeError(f"{error_payload['error']}: {error_payload['message']}")


@mcp.tool()
async def discover_agents(query: str, limit: int | None = None) -> dict[str, Any]:
    """
    Discover matching agents from the Hybro Discovery API.

    Args:
        query: Natural language search query.
        limit: Optional max result count (clamped to 1..100).
    """
    query_value = (query or "").strip()
    if not query_value:
        raise ValueError("query is required and cannot be empty")

    normalized_limit = None
    if limit is not None:
        normalized_limit = max(1, min(100, int(limit)))

    return await _call_discovery_api(query=query_value, limit=normalized_limit)


def main() -> None:
    _get_config()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
