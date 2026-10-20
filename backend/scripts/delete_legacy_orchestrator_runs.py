#!/usr/bin/env python3
"""Delete legacy (v5 or non-canonical) orchestrator runs. Defaults to a dry run.

The orchestrator reads only canonical schema-6 runs, so a legacy document left in
``orchestrator_runs`` fails validation when it is loaded. This one-off removes
every legacy run of any status together with its run events, recovery lease,
Agent calls, and Agent tool bindings.
It does not archive them. Run it before upgrading past C2 step 3.
"""

from __future__ import annotations

import argparse
import asyncio
from dataclasses import dataclass, field
from typing import Any

from scripts.check_legacy_lifecycle_readiness import LEGACY_ORCHESTRATOR_RUN_QUERY

RUNS_COLLECTION = "orchestrator_runs"
# Run-scoped rows go first, so an interrupted apply leaves the runs to find again.
# Agent calls are included so recovery sweeps never revisit a deleted Run's call.
RUN_SCOPED_COLLECTIONS = (
    "orchestrator_run_events",
    "orchestrator_recovery_leases",
    "orchestrator_agent_calls",
    "orchestrator_agent_tool_bindings",
)


@dataclass(frozen=True)
class DeletionResult:
    matched_run_ids: list[str]
    deleted: dict[str, int] = field(default_factory=dict)


async def delete_legacy_orchestrator_runs(
    database: Any, *, apply: bool
) -> DeletionResult:
    rows = await (
        database[RUNS_COLLECTION]
        .find(LEGACY_ORCHESTRATOR_RUN_QUERY, projection={"run_id": 1})
        .to_list(length=None)
    )
    run_ids = [row["run_id"] for row in rows]
    if not apply or not run_ids:
        return DeletionResult(matched_run_ids=run_ids)
    by_run = {"run_id": {"$in": run_ids}}
    deleted: dict[str, int] = {}
    for name in RUN_SCOPED_COLLECTIONS:
        result = await database[name].delete_many(by_run)
        deleted[name] = result.deleted_count
    result = await database[RUNS_COLLECTION].delete_many(
        {"$and": [by_run, LEGACY_ORCHESTRATOR_RUN_QUERY]}
    )
    deleted[RUNS_COLLECTION] = result.deleted_count
    return DeletionResult(matched_run_ids=run_ids, deleted=deleted)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--mongo-url", help="defaults to the configured backend mongodb_url"
    )
    parser.add_argument(
        "--database", help="defaults to the configured backend mongodb_db_name"
    )
    return parser.parse_args()


async def _main() -> None:
    args = _parse_args()
    if args.mongo_url is None or args.database is None:
        from common.config import settings

        args.mongo_url = args.mongo_url or settings.mongodb_url
        args.database = args.database or settings.mongodb_db_name

    from motor.motor_asyncio import AsyncIOMotorClient

    client = AsyncIOMotorClient(args.mongo_url)
    try:
        await client.admin.command("ping")
        result = await delete_legacy_orchestrator_runs(
            client[args.database], apply=args.apply
        )
    finally:
        client.close()
    print(f"matched_runs={len(result.matched_run_ids)} dry_run={int(not args.apply)}")
    for name, count in result.deleted.items():
        print(f"deleted {name}={count}")


if __name__ == "__main__":
    asyncio.run(_main())
