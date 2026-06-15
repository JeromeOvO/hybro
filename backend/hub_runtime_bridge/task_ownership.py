from __future__ import annotations

import inspect
from datetime import timedelta
from uuid import uuid4

from common.utils.time import ensure_utc, utcnow


class InMemoryHubTaskOwnershipStore:
    def __init__(self, *, lease_ttl_seconds: int = 120) -> None:
        self._records: dict[str, dict] = {}
        self._alias_index: dict[str, str] = {}
        self._lease_ttl = lease_ttl_seconds

    async def ensure_indexes(self) -> None:
        return None

    async def claim_or_refresh(
        self, aliases: dict, owner_id: str, lease_token: str | None = None
    ) -> dict:
        clean_aliases = {
            key: value for key, value in aliases.items() if isinstance(value, str) and value
        }
        existing_ids = {
            self._alias_index[value]
            for value in clean_aliases.values()
            if value in self._alias_index
        }
        if len(existing_ids) > 1:
            raise ValueError("ownership aliases resolve to different tasks")
        record_id = next(iter(existing_ids), str(uuid4()))
        record = self._records.setdefault(record_id, {"ownership_id": record_id})
        now = utcnow()
        current_owner = record.get("owner_id")
        lease_expires_at = record.get("lease_expires_at")
        if (
            current_owner
            and current_owner != owner_id
            and _lease_is_after(lease_expires_at, now)
        ):
            raise ValueError("ownership lease is held by another worker")
        record.update(owner_id=owner_id, lease_token=lease_token or str(uuid4()))
        record["lease_expires_at"] = now + timedelta(seconds=self._lease_ttl)
        aliases_record = record.setdefault("aliases", {})
        aliases_record.update(clean_aliases)
        for value in clean_aliases.values():
            self._alias_index[value] = record_id
        return dict(record)

    async def resolve_owner(self, alias: str) -> dict | None:
        record_id = self._alias_index.get(alias)
        if record_id is None:
            return None
        record = self._records[record_id]
        lease_expires_at = record.get("lease_expires_at")
        if _lease_is_expired(lease_expires_at):
            return None
        return dict(record)

    async def release(self, alias: str, owner_id: str | None = None) -> None:
        record_id = self._alias_index.get(alias)
        if record_id is None:
            return
        record = self._records.get(record_id)
        if owner_id and record and record.get("owner_id") != owner_id:
            return
        if record:
            for value in record.get("aliases", {}).values():
                self._alias_index.pop(value, None)
        self._records.pop(record_id, None)


class MongoHubTaskOwnershipStore:
    def __init__(self, mongo, *, lease_ttl_seconds: int = 120) -> None:
        collection_factory = getattr(mongo, "collection", None)
        if callable(collection_factory):
            self._collection = collection_factory("hub_task_ownership")
        else:
            self._collection = mongo.db.hub_task_ownership
        self._lease_ttl = lease_ttl_seconds

    async def ensure_indexes(self) -> None:
        create_index = getattr(self._collection, "create_index", None)
        if create_index is None:
            return None
        await _maybe_await(create_index([("ownership_id", 1)], unique=True))
        for alias_name in ("agent_message_id", "local_task_id", "hub_task_id"):
            await _maybe_await(
                create_index(
                    [(f"aliases.{alias_name}", 1)],
                    unique=True,
                    partialFilterExpression={
                        f"aliases.{alias_name}": {"$type": "string"}
                    },
                )
            )

    async def claim_or_refresh(
        self, aliases: dict, owner_id: str, lease_token: str | None = None
    ) -> dict:
        clean_aliases = {
            key: value for key, value in aliases.items() if isinstance(value, str) and value
        }
        existing = await self._find_by_aliases(clean_aliases)
        if len({record.get("ownership_id") for record in existing}) > 1:
            raise ValueError("ownership aliases resolve to different tasks")
        record = existing[0] if existing else None
        record_exists = record is not None
        now = utcnow()
        if record:
            current_owner = record.get("owner_id")
            lease_expires_at = record.get("lease_expires_at")
            if (
                current_owner
                and current_owner != owner_id
                and _lease_is_after(lease_expires_at, now)
            ):
                raise ValueError("ownership lease is held by another worker")
            ownership_id = record["ownership_id"]
        else:
            ownership_id = str(uuid4())
            record = {"ownership_id": ownership_id}
        token = lease_token or str(uuid4())
        update_fields = {
            "ownership_id": ownership_id,
            "owner_id": owner_id,
            "lease_token": token,
            "lease_expires_at": now + timedelta(seconds=self._lease_ttl),
        }
        for key, value in clean_aliases.items():
            update_fields[f"aliases.{key}"] = value
        if record_exists:
            update_query = {
                "ownership_id": ownership_id,
                "$or": [
                    {"owner_id": owner_id},
                    {"owner_id": {"$exists": False}},
                    {"lease_expires_at": {"$lte": now}},
                    {"lease_expires_at": {"$exists": False}},
                ],
            }
            upsert = False
        else:
            update_query = {"ownership_id": ownership_id}
            upsert = True
        result = await _maybe_await(
            _claim_with_duplicate_retry(
                self._collection,
                update_query,
                update_fields,
                upsert=upsert,
            )
        )
        if record_exists and getattr(result, "matched_count", 1) == 0:
            raise ValueError("ownership lease is held by another worker")
        aliases_record = dict(record.get("aliases", {}))
        aliases_record.update(clean_aliases)
        return {
            "ownership_id": ownership_id,
            "owner_id": owner_id,
            "lease_token": token,
            "lease_expires_at": update_fields["lease_expires_at"],
            "aliases": aliases_record,
        }

    async def resolve_owner(self, alias: str) -> dict | None:
        query = _alias_query(alias)
        record = await _maybe_await(self._collection.find_one(query))
        if not isinstance(record, dict):
            return None
        lease_expires_at = record.get("lease_expires_at")
        if _lease_is_expired(lease_expires_at):
            return None
        return dict(record)

    async def release(self, alias: str, owner_id: str | None = None) -> None:
        record = await self.resolve_owner(alias)
        if not record:
            return
        if owner_id and record.get("owner_id") != owner_id:
            return
        await _maybe_await(
            self._collection.delete_one({"ownership_id": record["ownership_id"]})
        )

    async def _find_by_aliases(self, aliases: dict) -> list[dict]:
        found: list[dict] = []
        seen: set[str] = set()
        for value in aliases.values():
            record = await _maybe_await(self._collection.find_one(_alias_query(value)))
            if (
                isinstance(record, dict)
                and record.get("ownership_id") not in seen
            ):
                seen.add(record.get("ownership_id"))
                found.append(dict(record))
        return found


def _alias_query(alias: str) -> dict:
    return {
        "$or": [
            {"aliases.agent_message_id": alias},
            {"aliases.local_task_id": alias},
            {"aliases.hub_task_id": alias},
        ]
    }


def _lease_is_after(value, comparison) -> bool:
    return bool(value and ensure_utc(value) > ensure_utc(comparison))


def _lease_is_expired(value) -> bool:
    return bool(value and ensure_utc(value) <= utcnow())


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


async def _claim_with_duplicate_retry(
    collection, query: dict, update_fields: dict, *, upsert: bool
):
    try:
        return await _maybe_await(
            collection.update_one(
                query,
                {"$set": update_fields},
                upsert=upsert,
            )
        )
    except Exception as exc:
        for value in update_fields.values():
            if isinstance(value, str):
                existing = await _maybe_await(collection.find_one(_alias_query(value)))
                if existing:
                    raise ValueError("ownership lease is held by another worker") from exc
        raise


HubTaskOwnershipStore = InMemoryHubTaskOwnershipStore

__all__ = [
    "HubTaskOwnershipStore",
    "InMemoryHubTaskOwnershipStore",
    "MongoHubTaskOwnershipStore",
]
