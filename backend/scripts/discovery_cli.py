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

Timeout is read from common.config.settings (hybro_timeout_seconds).

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
import sys

from scripts._discovery_client import call_discovery_api, normalize_error

EXIT_SUCCESS = 0
EXIT_BAD_ARGS = 2
EXIT_MISSING_CONFIG = 3
EXIT_API_ERROR = 4
EXIT_NETWORK_ERROR = 5


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
    try:
        data = await call_discovery_api(query=query, limit=limit)
    except RuntimeError as exc:
        msg = str(exc)
        if "HYBRO_API_KEY" in msg:
            _print_json({"error": "missing_api_key", "message": msg})
            return EXIT_MISSING_CONFIG
        if msg.startswith("service_unavailable:"):
            _print_json(
                {
                    "error": "service_unavailable",
                    "message": msg.split(":", 1)[1].strip(),
                }
            )
            return EXIT_NETWORK_ERROR
        error = normalize_error({"message": msg})
        _print_json(error)
        return EXIT_API_ERROR

    if output_json:
        _print_json(data)
    else:
        _print_table(data)
    return EXIT_SUCCESS


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
            _print_json(
                {"error": "invalid_args", "message": "limit must be between 1 and 100"}
            )
            return EXIT_BAD_ARGS

    return asyncio.run(
        _run(query=query, limit=args.limit, output_json=args.output_json)
    )


if __name__ == "__main__":
    sys.exit(main())
