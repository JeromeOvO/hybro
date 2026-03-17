#!/usr/bin/env python3
"""
Discovery API CLI.

Query the Hybro Discovery API from the terminal.

Usage:
    discover "data analysis agent"
    discover "finance" --limit 3
    discover "hr automation" --limit 5 --json

Environment variables (loaded from repo-root .env):
    HYBRO_API_KEY     Required Discovery API key.
    HYBRO_API_URL     Optional base URL, default: http://localhost:8000
    HYBRO_API_PREFIX  Optional API prefix, default: /api/v1

Timeout is read from config.settings (hybro_timeout_seconds).

Exit codes:
    0  Success
    2  Invalid CLI arguments
    3  Missing environment config
    4  API error (401/404/429/500)
    5  Network or runtime error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from config.settings import settings

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

EXIT_SUCCESS = 0
EXIT_BAD_ARGS = 2
EXIT_MISSING_CONFIG = 3
EXIT_API_ERROR = 4
EXIT_NETWORK_ERROR = 5


def _load_env_file() -> None:
    if load_dotenv is None:
        return
    repo_root = Path(__file__).resolve().parent.parent
    env_path = repo_root / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=False)


def _get_env() -> tuple[str, str, str, float]:
    """Return (api_key, api_url, api_prefix, timeout)."""
    _load_env_file()
    api_key = os.getenv("HYBRO_API_KEY", "").strip()
    api_url = os.getenv("HYBRO_API_URL", "http://localhost:8000").strip()
    api_prefix = os.getenv("HYBRO_API_PREFIX", "/api/v1").strip()
    timeout = settings.hybro_timeout_seconds
    return api_key, api_url, api_prefix, timeout


def _build_url(api_url: str, api_prefix: str) -> str:
    base = api_url.rstrip("/")
    prefix = api_prefix if api_prefix.startswith("/") else f"/{api_prefix}"
    prefix = prefix.rstrip("/")
    return f"{base}{prefix}/discovery/agents"


def _normalize_error(payload: object) -> dict[str, str]:
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


def _print_json(data: object) -> None:
    print(json.dumps(data, indent=2, default=str))


def _print_table(data: dict) -> None:
    agents = data.get("agents", [])
    count = data.get("count", len(agents))
    query = data.get("query", "")

    print(f'\nQuery: "{query}"')
    print(f"Found: {count} agent(s)\n")

    if not agents:
        return

    print(f"{'#':<4} {'Name':<30} {'Score'}")
    print("-" * 44)

    for i, agent in enumerate(agents, 1):
        card = agent.get("agent_card", {}) or {}
        name = card.get("name", "Unknown")[:28]
        score = float(agent.get("match_score") or 0.0)
        print(f"{i:<4} {name:<30} {score:.2f}")

    print()


async def _run(query: str, limit: int | None, output_json: bool) -> int:
    api_key, api_url, api_prefix, timeout = _get_env()

    if not api_key:
        err = {"error": "missing_config", "message": "HYBRO_API_KEY is not set. Add it to .env or export it."}
        _print_json(err)
        return EXIT_MISSING_CONFIG

    url = _build_url(api_url, api_prefix)
    payload: dict = {"query": query}
    if limit is not None:
        payload["limit"] = limit

    headers = {
        "X-API-Key": api_key,
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.TimeoutException:
        err = {"error": "service_unavailable", "message": "Discovery API request timed out"}
        _print_json(err)
        return EXIT_NETWORK_ERROR
    except httpx.RequestError as exc:
        err = {"error": "service_unavailable", "message": f"Failed to reach Discovery API ({exc})"}
        _print_json(err)
        return EXIT_NETWORK_ERROR

    if response.status_code == 200:
        try:
            data = response.json()
        except ValueError:
            _print_json({"error": "invalid_response", "message": "Backend returned invalid JSON"})
            return EXIT_API_ERROR
        if output_json:
            _print_json(data)
        else:
            _print_table(data)
        return EXIT_SUCCESS

    try:
        body = response.json()
    except ValueError:
        body = {"error": "request_failed", "message": response.text or "Request failed"}

    error_payload = _normalize_error(body)

    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            error_payload["message"] += f" Retry after {retry_after}s."

    _print_json(error_payload)
    return EXIT_API_ERROR


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Query the Hybro Discovery API for matching agents.",
        epilog="Examples:\n"
               '  discover "data analysis agent"\n'
               '  discover "finance" --limit 3\n'
               '  discover "hr automation" --limit 5 --json',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "query",
        type=str,
        help="Natural language search query",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Max number of agents to return (1-100)",
    )
    parser.add_argument(
        "--json",
        dest="output_json",
        action="store_true",
        default=False,
        help="Output raw JSON (default: formatted table)",
    )

    args = parser.parse_args()

    query = args.query.strip()
    if not query:
        parser.error("query cannot be empty")

    if args.limit is not None:
        if args.limit < 1 or args.limit > 100:
            _print_json({"error": "invalid_args", "message": "limit must be between 1 and 100"})
            return EXIT_BAD_ARGS

    return asyncio.run(_run(query=query, limit=args.limit, output_json=args.output_json))


if __name__ == "__main__":
    sys.exit(main())
