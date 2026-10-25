"""Read-only host probe for the local MCP adapter. No runtime credentials."""

from typing import Literal

import httpx

MCPStatus = Literal["ready", "unavailable", "unsupported_auth"]
STATUS_LABELS: dict[MCPStatus, str] = {
    "ready": "Ready",
    "unavailable": "Unavailable; start services or check MCP logs.",
    "unsupported_auth": "Unavailable with Clerk authentication.",
}


def mcp_url(port: int) -> str:
    """The instance's MCP address; it is published on loopback only."""
    return f"http://127.0.0.1:{port}/mcp"


def read_status(port: int) -> MCPStatus:
    try:
        with httpx.Client(
            timeout=4.0, trust_env=False, follow_redirects=False
        ) as client:
            response = client.get(f"http://127.0.0.1:{port}/health")
        response.raise_for_status()
        body = response.json()
        if isinstance(body, dict) and body.get("service") == "hybro-mcp":
            status = body.get("status")
            if status in {"ready", "unavailable", "unsupported_auth"}:
                return status
    except (httpx.HTTPError, ValueError, TypeError):
        pass
    return "unavailable"
