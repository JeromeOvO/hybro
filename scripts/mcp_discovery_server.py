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

import sys
from pathlib import Path
from typing import Any

# Allow importing config when run as script
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP

from scripts._discovery_client import call_discovery_api

mcp = FastMCP("hybro-discovery")


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

    return await call_discovery_api(query=query_value, limit=normalized_limit)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
