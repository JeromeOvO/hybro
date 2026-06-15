from __future__ import annotations

import inspect
from datetime import timedelta
from uuid import uuid4

from common.utils.time import ensure_utc, utcnow


class InMemoryHubResponseJournal:
    def __init__(self, *, claim_ttl_seconds: int = 120) -> None:
        self._records: dict[str, dict] = {}
        self._stable_index: dict[str, str] = {}
        self._claim_ttl = claim_ttl_seconds

    async def ensure_indexes(self) -> None:
        return None

    async def create_or_get(self, event: dict) -> dict:
        record = dict(event)
        stable_key = record.get("stable_idempotency_key")
        if stable_key and stable_key in self._stable_index:
            existing = dict(self._records[self._stable_index[stable_key]])
            existing["newly_created"] = False
            return existing
        journal_id = record.get("journal_id") or str(uuid4())
        record["journal_id"] = journal_id
        record.setdefault("idempotency_key", f"ingest:{journal_id}")
        record.setdefault("dedupe_mode", "stable" if stable_key else "none")
        record.setdefault("processed", False)
        record.setdefault("dead_lettered", False)
        record.setdefault("retry_count", 0)
        record.setdefault("created_at", utcnow())
        self._records[journal_id] = record
        if stable_key:
            self._stable_index[stable_key] = journal_id
        created = dict(record)
        created["newly_created"] = True
        return created

    async def claim_for_processing(self, journal_id: str, owner_id: str) -> dict | None:
        record = self._records.get(journal_id)
        if not record or record.get("processed") or record.get("dead_lettered"):
            return None
        now = utcnow()
        expires_at = record.get("claim_expires_at")
        if _claim_is_active(expires_at, now):
            return None
        token = str(uuid4())
        record.update(
            claim_owner=owner_id,
            claim_token=token,
            claimed_at=now,
            claim_expires_at=now + timedelta(seconds=self._claim_ttl),
        )
        return dict(record)

    async def mark_processed(self, journal_id: str, claim_token: str | None = None) -> None:
        record = self._records.get(journal_id)
        if not record:
            return
        if claim_token and record.get("claim_token") != claim_token:
            return
        record["processed"] = True
        record["processed_at"] = utcnow()

    async def mark_dead_letter(self, journal_id: str, reason: str) -> None:
        if journal_id in self._records:
            self._records[journal_id]["dead_lettered"] = True
            self._records[journal_id]["dead_letter_reason"] = reason

    async def release_claim(self, journal_id: str, claim_token: str | None = None) -> None:
        record = self._records.get(journal_id)
        if not record:
            return
        if claim_token and record.get("claim_token") != claim_token:
            return
        for key in ["claim_owner", "claim_token", "claimed_at", "claim_expires_at"]:
            record.pop(key, None)

    async def find_replayable(self, limit: int = 100) -> list[dict]:
        now = utcnow()
        replayable = [
            dict(record)
            for record in self._records.values()
            if not record.get("processed")
            and not record.get("dead_lettered")
            and _claim_is_replayable(record.get("claim_expires_at"), now)
        ]
        return replayable[:limit]


class MongoHubResponseJournal:
    def __init__(self, mongo, *, claim_ttl_seconds: int = 120) -> None:
        collection_factory = getattr(mongo, "collection", None)
        if callable(collection_factory):
            self._collection = collection_factory("hub_response_journal")
        else:
            self._collection = mongo.db.hub_response_journal
        self._claim_ttl = claim_ttl_seconds

    async def ensure_indexes(self) -> None:
        create_index = getattr(self._collection, "create_index", None)
        if create_index is None:
            return None
        await _maybe_await(create_index([("journal_id", 1)], unique=True))
        await _maybe_await(
            create_index(
            [("stable_idempotency_key", 1)],
            unique=True,
            partialFilterExpression={"stable_idempotency_key": {"$type": "string"}},
            )
        )

    async def create_or_get(self, event: dict) -> dict:
        record = dict(event)
        stable_key = record.get("stable_idempotency_key")
        if stable_key:
            existing = await _maybe_await(
                self._collection.find_one({"stable_idempotency_key": stable_key})
            )
            if existing:
                existing = dict(existing)
                existing["newly_created"] = False
                return existing
        journal_id = record.get("journal_id") or str(uuid4())
        record["journal_id"] = journal_id
        record.setdefault("idempotency_key", f"ingest:{journal_id}")
        record.setdefault("dedupe_mode", "stable" if stable_key else "none")
        record.setdefault("processed", False)
        record.setdefault("dead_lettered", False)
        record.setdefault("retry_count", 0)
        record.setdefault("created_at", utcnow())
        try:
            await _maybe_await(self._collection.insert_one(record))
        except Exception:
            if not stable_key:
                raise
            existing = await _maybe_await(
                self._collection.find_one({"stable_idempotency_key": stable_key})
            )
            if not existing:
                raise
            existing = dict(existing)
            existing["newly_created"] = False
            return existing
        created = dict(record)
        created["newly_created"] = True
        return created

    async def claim_for_processing(self, journal_id: str, owner_id: str) -> dict | None:
        now = utcnow()
        expires_at = now + timedelta(seconds=self._claim_ttl)
        token = str(uuid4())
        query = {
            "journal_id": journal_id,
            "processed": {"$ne": True},
            "dead_lettered": {"$ne": True},
            "$or": [
                {"claim_expires_at": {"$exists": False}},
                {"claim_expires_at": None},
                {"claim_expires_at": {"$lte": now}},
            ],
        }
        update = {
            "$set": {
                "claim_owner": owner_id,
                "claim_token": token,
                "claimed_at": now,
                "claim_expires_at": expires_at,
            }
        }
        finder = getattr(self._collection, "find_one_and_update", None)
        if finder is not None:
            record = await _maybe_await(finder(query, update, return_document=True))
            return dict(record) if record else None
        matched = await _maybe_await(self._collection.update_one(query, update))
        if not matched:
            return None
        record = await _maybe_await(self._collection.find_one({"journal_id": journal_id}))
        return dict(record) if record else None

    async def mark_processed(self, journal_id: str, claim_token: str | None = None) -> None:
        query = {"journal_id": journal_id}
        if claim_token:
            query["claim_token"] = claim_token
        await _maybe_await(
            self._collection.update_one(
            query,
            {
                "$set": {
                    "processed": True,
                    "processed_at": utcnow(),
                }
            },
            )
        )

    async def mark_dead_letter(self, journal_id: str, reason: str) -> None:
        await _maybe_await(
            self._collection.update_one(
            {"journal_id": journal_id},
            {"$set": {"dead_lettered": True, "dead_letter_reason": reason}},
            )
        )

    async def release_claim(self, journal_id: str, claim_token: str | None = None) -> None:
        query = {"journal_id": journal_id}
        if claim_token:
            query["claim_token"] = claim_token
        await _maybe_await(
            self._collection.update_one(
                query,
                {
                    "$unset": {
                        "claim_owner": "",
                        "claim_token": "",
                        "claimed_at": "",
                        "claim_expires_at": "",
                    }
                },
            )
        )

    async def find_replayable(self, limit: int = 100) -> list[dict]:
        now = utcnow()
        result = await _maybe_await(
            self._collection.find(
            {
                "processed": {"$ne": True},
                "dead_lettered": {"$ne": True},
                "$or": [
                    {"claim_expires_at": {"$exists": False}},
                    {"claim_expires_at": None},
                    {"claim_expires_at": {"$lte": now}},
                ],
            },
            limit=limit,
            )
        )
        to_list = getattr(result, "to_list", None)
        if to_list is not None:
            return await _maybe_await(to_list(length=limit))
        return list(result or [])[:limit]


HubResponseJournal = InMemoryHubResponseJournal


async def _maybe_await(value):
    if inspect.isawaitable(value):
        return await value
    return value


def _claim_is_active(value, comparison) -> bool:
    return bool(value and ensure_utc(value) > ensure_utc(comparison))


def _claim_is_replayable(value, comparison) -> bool:
    return value is None or ensure_utc(value) <= ensure_utc(comparison)

__all__ = [
    "HubResponseJournal",
    "InMemoryHubResponseJournal",
    "MongoHubResponseJournal",
]
