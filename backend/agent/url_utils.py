from __future__ import annotations

from urllib.parse import urlparse, urlunparse

from common.url_utils import LOCAL_HOST_ALIASES

WELL_KNOWN_PATHS = ("/.well-known/agent-card.json", "/.well-known/agent.json")


def is_local_agent_url(url: str | None) -> bool:
    if not url:
        return False
    try:
        hostname = (urlparse(url).hostname or "").lower()
    except ValueError:
        return False
    return hostname in LOCAL_HOST_ALIASES


def normalize_agent_url(url: str | None) -> str | None:
    if not url:
        return url

    try:
        parsed = urlparse(url)
    except ValueError:
        return url

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        return url
    if hostname in LOCAL_HOST_ALIASES:
        hostname = "localhost"

    port = parsed.port
    if (parsed.scheme == "http" and port == 80) or (
        parsed.scheme == "https" and port == 443
    ):
        port = None

    netloc = hostname if port is None else f"{hostname}:{port}"
    path = parsed.path.rstrip("/")
    for well_known_path in WELL_KNOWN_PATHS:
        if path == well_known_path or path.endswith(well_known_path):
            path = path[: -len(well_known_path)].rstrip("/")
            break

    return urlunparse(
        (
            parsed.scheme.lower(),
            netloc,
            path,
            "",
            parsed.query,
            "",
        )
    )
