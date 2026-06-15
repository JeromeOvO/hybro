import argparse
import asyncio
import os
import sys
from dataclasses import dataclass, field
from typing import Any

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

from common.utils.logger import get_logger

logger = get_logger(__name__)
load_dotenv()


@dataclass(frozen=True)
class IndexSpec:
    collection: str
    name: str
    keys: list[tuple[str, int]]
    options: dict[str, Any]


@dataclass
class MigrationResult:
    dropped: int = 0
    created: int = 0
    skipped: int = 0
    would_drop: list[str] = field(default_factory=list)
    would_create: list[str] = field(default_factory=list)


TASK_TRACKING_INDEXES = (
    IndexSpec(
        collection="room_agent_messages",
        name="has_task_tracking_sparse",
        keys=[("has_task_tracking", 1)],
        options={"sparse": True},
    ),
    IndexSpec(
        collection="room_agent_messages",
        name="task_updated_state_sparse",
        keys=[
            ("task_updated_at", 1),
            ("message_content.message_task.status.state", 1),
        ],
        options={"sparse": True},
    ),
    IndexSpec(
        collection="room_agent_messages",
        name="task_created_state_sparse",
        keys=[
            ("task_created_at", 1),
            ("message_content.message_task.status.state", 1),
        ],
        options={"sparse": True},
    ),
    IndexSpec(
        collection="room_agent_messages",
        name="user_task_state_sparse",
        keys=[
            ("user_id", 1),
            ("message_content.message_task.status.state", 1),
            ("has_task_tracking", 1),
        ],
        options={"sparse": True},
    ),
    IndexSpec(
        collection="room_agent_messages",
        name="room_task_created_sparse",
        keys=[("room_id", 1), ("has_task_tracking", 1), ("task_created_at", -1)],
        options={"sparse": True},
    ),
)


def _get_mongo_client_and_db() -> tuple[AsyncIOMotorClient, object]:
    url = os.getenv("MONGODB_URL")
    db_name = os.getenv("MONGODB_DB_NAME")
    if not url:
        raise ValueError("MONGODB_URL environment variable is not set")
    if not db_name:
        raise ValueError("MONGODB_DB_NAME environment variable is not set")
    client = AsyncIOMotorClient(url)
    return client, client[db_name]


def _index_keys(index: dict[str, Any]) -> list[tuple[str, Any]]:
    return [(field, direction) for field, direction in index.get("key", [])]


def _index_matches_spec(index: dict[str, Any], spec: IndexSpec) -> bool:
    if _index_keys(index) != spec.keys:
        return False
    return all(index.get(option) == value for option, value in spec.options.items())


def _has_same_keys(index: dict[str, Any], spec: IndexSpec) -> bool:
    return _index_keys(index) == spec.keys


def _get_collection(db: object, name: str) -> object:
    return getattr(db, name)


async def _migrate_index(
    db: object,
    spec: IndexSpec,
    *,
    dry_run: bool,
    result: MigrationResult,
) -> None:
    collection = _get_collection(db, spec.collection)
    indexes = await collection.index_information()

    if spec.name in indexes:
        logger.info("%s.%s already exists; skipping", spec.collection, spec.name)
        result.skipped += 1
        return

    equivalent_legacy_name = None
    conflicting_name = None
    for index_name, index in indexes.items():
        if index_name == "_id_":
            continue
        if _index_matches_spec(index, spec):
            equivalent_legacy_name = index_name
            break
        if _has_same_keys(index, spec):
            conflicting_name = index_name

    if conflicting_name:
        raise RuntimeError(
            f"Found non-equivalent index {spec.collection}.{conflicting_name} "
            f"with keys matching target {spec.name}; refusing to drop it"
        )

    if equivalent_legacy_name is not None:
        if dry_run:
            logger.info(
                "Would drop %s.%s before creating %s",
                spec.collection,
                equivalent_legacy_name,
                spec.name,
            )
            result.would_drop.append(equivalent_legacy_name)
        else:
            logger.info("Dropping %s.%s", spec.collection, equivalent_legacy_name)
            await collection.drop_index(equivalent_legacy_name)
            result.dropped += 1

    if dry_run:
        logger.info("Would create %s.%s", spec.collection, spec.name)
        result.would_create.append(spec.name)
        return

    logger.info("Creating %s.%s", spec.collection, spec.name)
    await collection.create_index(spec.keys, name=spec.name, **spec.options)
    result.created += 1


async def run_migration_on_db(
    db: object,
    *,
    dry_run: bool = True,
) -> MigrationResult:
    result = MigrationResult()
    for spec in TASK_TRACKING_INDEXES:
        await _migrate_index(db, spec, dry_run=dry_run, result=result)
    return result


async def run_migration(*, dry_run: bool = True) -> MigrationResult:
    client, db = _get_mongo_client_and_db()
    try:
        return await run_migration_on_db(db, dry_run=dry_run)
    finally:
        client.close()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Recreate room_agent_messages task-tracking indexes with canonical "
            "runtime names."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Drop equivalent legacy indexes and create canonical indexes.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    try:
        migration_result = asyncio.run(run_migration(dry_run=not args.apply))
    except KeyboardInterrupt:
        logger.info("Migration interrupted.")
        sys.exit(1)
    except Exception:
        logger.error("Migration failed", exc_info=True)
        sys.exit(1)

    logger.info(
        "Migration complete. dropped=%d created=%d skipped=%d "
        "would_drop=%s would_create=%s",
        migration_result.dropped,
        migration_result.created,
        migration_result.skipped,
        migration_result.would_drop,
        migration_result.would_create,
    )
