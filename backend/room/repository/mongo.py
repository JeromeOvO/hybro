from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from pymongo.errors import DuplicateKeyError

from common.a2a_constants import TERMINAL_STATES
from common.dto import (
    RoomTimelineEntry,
    RoomTimelinePage,
    TimelinePosition,
    UserMessageInsertResult,
)
from common.idempotency import (
    MAX_CLIENT_REQUEST_ID_LENGTH,
    normalize_client_request_id,
)
from common.protocols import MongoDAL
from common.utils.logger import get_logger
from room.idempotency import (
    IdempotencyConflictError,
    UnexpectedUserMessageDuplicateError,
    stored_fingerprint_matches,
)
from room.message_graph import normalize_history_rows, status_update_payload
from room.timeline import (
    SOURCE_RANK,
    normalize_timeline_document,
    timeline_key,
    timeline_sort_us_from_value,
)

logger = get_logger(__name__)
_TASK_STATE_PATH = "message_content.message_task.status.state"
_TERMINAL_TASK_STATES = tuple(sorted(state.value for state in TERMINAL_STATES))


def _updated_task_state(updates: dict[str, Any]) -> str | None:
    state = updates.get(_TASK_STATE_PATH)
    if state is None:
        task = updates.get("message_content.message_task")
        if not isinstance(task, dict):
            content = updates.get("message_content")
            task = content.get("message_task") if isinstance(content, dict) else None
        status = task.get("status") if isinstance(task, dict) else None
        state = status.get("state") if isinstance(status, dict) else None
    if state is None:
        return None
    return str(getattr(state, "value", state))


def _canonical_user_message_document(message: dict) -> dict:
    """Validate user-message identity and normalize its optional request key."""

    candidate = dict(message)
    for field in ("message_id", "room_id"):
        value = candidate.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"User-message insert requires non-empty {field}")

    client_request_id = candidate.get("client_request_id")
    if client_request_id is None:
        return candidate
    if not isinstance(client_request_id, str):
        raise ValueError("User-message client_request_id must be a string or null")
    normalized_client_request_id = normalize_client_request_id(client_request_id)
    if (
        not normalized_client_request_id
        or len(normalized_client_request_id) > MAX_CLIENT_REQUEST_ID_LENGTH
    ):
        raise ValueError("User-message insert requires valid client_request_id")
    candidate["client_request_id"] = normalized_client_request_id
    return candidate


class RoomMongoRepository:
    def __init__(self, mongo: MongoDAL, collection_name: str = "rooms") -> None:
        self._rooms = mongo.collection(collection_name)

    async def get_by_id(self, room_id: str) -> dict | None:
        return await self._rooms.find_one({"room_id": room_id})

    async def get_by_owner(self, owner_id: str) -> list[dict]:
        return await self._rooms.find(
            {
                "room_owner_id": owner_id,
                "$or": [
                    {"lifecycle_state": "active"},
                    {"lifecycle_state": {"$exists": False}},
                ],
            }
        )

    async def create(self, room: dict) -> str:
        inserted_id = await self._rooms.insert_one(dict(room))
        return str(room.get("room_id") or inserted_id)

    async def update(self, room_id: str, updates: dict) -> bool:
        return await self._rooms.update_one(
            {
                "room_id": room_id,
                "$or": [
                    {"lifecycle_state": "active"},
                    {"lifecycle_state": {"$exists": False}},
                ],
            },
            {"$set": dict(updates)},
        )

    async def update_fields(self, room_id: str, updates: dict) -> dict | None:
        return await self._rooms.find_one_and_update(
            {
                "room_id": room_id,
                "$or": [
                    {"lifecycle_state": "active"},
                    {"lifecycle_state": {"$exists": False}},
                ],
            },
            {"$set": dict(updates)},
            return_document=True,
        )

    async def set_membership(
        self,
        room_id: str,
        *,
        agent_set: dict[str, str],
        membership_origin: str,
        membership_origin_status: str,
        source_group_id: str | None = None,
        source_group_name: str | None = None,
    ) -> dict | None:
        return await self.update_fields(
            room_id,
            {
                "room_agent_set": dict(agent_set),
                "membership_origin": membership_origin,
                "membership_origin_status": membership_origin_status,
                "source_group_id": source_group_id,
                "source_group_name": source_group_name,
            },
        )

    async def delete(self, room_id: str) -> bool:
        return await self._rooms.delete_one({"room_id": room_id})


class MessageMongoRepository:
    def __init__(
        self,
        mongo: MongoDAL,
        user_collection_name: str = "room_user_messages",
        agent_collection_name: str = "room_agent_messages",
    ) -> None:
        self._mongo = mongo
        self._user_messages = mongo.collection(user_collection_name)
        self._agent_messages = mongo.collection(agent_collection_name)
        self._cancelled_messages = None

    async def save_user_message(self, message: dict) -> str:
        candidate = normalize_timeline_document(
            _canonical_user_message_document(message)
        )
        await self._user_messages.insert_one(candidate)
        return candidate["message_id"]

    async def get_user_message_by_idempotency_key(
        self,
        room_id: str,
        client_request_id: str,
    ) -> dict | None:
        normalized_client_request_id = client_request_id.strip()
        if (
            not normalized_client_request_id
            or len(normalized_client_request_id) > MAX_CLIENT_REQUEST_ID_LENGTH
        ):
            raise ValueError("Invalid client_request_id for idempotency lookup")
        return await self._user_messages.find_one(
            {
                "room_id": room_id,
                "client_request_id": normalized_client_request_id,
            }
        )

    async def insert_user_message_idempotently(
        self,
        document: dict,
    ) -> UserMessageInsertResult:
        candidate = normalize_timeline_document(
            _canonical_user_message_document(document)
        )
        room_id = candidate.get("room_id")
        client_request_id = candidate.get("client_request_id")
        fingerprint = candidate.get("idempotency_fingerprint")
        fingerprint_version = candidate.get("idempotency_fingerprint_version")
        if not isinstance(room_id, str) or not room_id:
            raise ValueError("Idempotent user-message insert requires room_id")
        if (
            not isinstance(client_request_id, str)
            or not client_request_id
            or len(client_request_id) > MAX_CLIENT_REQUEST_ID_LENGTH
        ):
            raise ValueError(
                "Idempotent user-message insert requires normalized client_request_id"
            )
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError(
                "Idempotent user-message insert requires idempotency_fingerprint"
            )
        if not isinstance(fingerprint_version, int):
            raise ValueError(
                "Idempotent user-message insert requires fingerprint version"
            )

        try:
            await self._user_messages.insert_one(candidate)
        except DuplicateKeyError as exc:
            existing = await self.get_user_message_by_idempotency_key(
                room_id,
                client_request_id,
            )
            if existing is None:
                # The collision came from message_id (or another unique index),
                # not from this request key. It is not a valid replay.
                raise UnexpectedUserMessageDuplicateError(
                    "Unexpected user-message unique-index collision"
                ) from exc
            existing_message_id = existing.get("message_id")
            if not isinstance(existing_message_id, str) or not existing_message_id:
                raise UnexpectedUserMessageDuplicateError(
                    "Conflicting idempotency record has no valid message_id"
                ) from exc
            if existing.get("idempotency_fingerprint") is None:
                logger.warning(
                    "Legacy idempotency replay without fingerprint "
                    "room_id=%s client_request_id=%s message_id=%s",
                    room_id,
                    client_request_id,
                    existing_message_id,
                )
                return UserMessageInsertResult(
                    message_id=existing_message_id,
                    created=False,
                    document=deepcopy(existing),
                )
            if stored_fingerprint_matches(
                existing,
                fingerprint=fingerprint,
                fingerprint_version=fingerprint_version,
            ):
                return UserMessageInsertResult(
                    message_id=existing_message_id,
                    created=False,
                    document=deepcopy(existing),
                )
            raise IdempotencyConflictError(room_id, client_request_id) from None

        return UserMessageInsertResult(
            message_id=candidate["message_id"],
            created=True,
            document=deepcopy(candidate),
        )

    async def save_agent_message(self, message: dict) -> str:
        candidate = normalize_timeline_document(message)
        for field in ("message_id", "room_id"):
            value = candidate.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Agent-message insert requires non-empty {field}")
        inserted_id = await self._agent_messages.insert_one(candidate)
        return str(candidate.get("message_id") or inserted_id)

    async def get_by_id(self, message_id: str) -> dict | None:
        user_message = await self._user_messages.find_one({"message_id": message_id})
        if user_message is not None:
            return user_message
        return await self._agent_messages.find_one({"message_id": message_id})

    async def is_message_cancelled(self, message_id: str) -> bool:
        if self._cancelled_messages is None:
            self._cancelled_messages = self._mongo.collection("cancelled_messages")
        return (
            await self._cancelled_messages.find_one({"message_id": message_id})
        ) is not None

    async def get_by_ids(self, message_ids: list[str]) -> list[dict]:
        if not message_ids:
            return []
        query = {"message_id": {"$in": list(message_ids)}}
        user_messages = await self._user_messages.find(query)
        agent_messages = await self._agent_messages.find(query)
        by_id = {
            str(row.get("message_id")): row
            for row in [*user_messages, *agent_messages]
            if row.get("message_id") is not None
        }
        return [by_id[message_id] for message_id in message_ids if message_id in by_id]

    async def get_for_room(
        self, room_id: str, limit: int, before: datetime | None = None
    ) -> list[dict]:
        if limit <= 0:
            return []
        query: dict[str, Any] = {"room_id": room_id}
        if before is not None:
            query["timeline_sort_us"] = {"$lt": timeline_sort_us_from_value(before)}
        sort = [("timeline_sort_us", -1), ("message_id", -1)]
        user_messages = await self._user_messages.find(query, sort=sort, limit=limit)
        agent_messages = await self._agent_messages.find(query, sort=sort, limit=limit)
        merged = [
            *[("user", row) for row in user_messages],
            *[("agent", row) for row in agent_messages],
        ]
        merged.sort(key=_typed_row_key, reverse=True)
        selected = merged[:limit]
        selected.reverse()
        return [{**row, "message_type": source} for source, row in selected]

    async def get_timeline_page(
        self,
        room_id: str,
        *,
        limit: int,
        before: TimelinePosition | None,
    ) -> RoomTimelinePage:
        fetch_limit = limit + 1
        sort = [("timeline_sort_us", -1), ("message_id", -1)]
        user_rows = await self._user_messages.find(
            _timeline_query(room_id, source="user", before=before),
            sort=sort,
            limit=fetch_limit,
        )
        agent_rows = await self._agent_messages.find(
            _timeline_query(room_id, source="agent", before=before),
            sort=sort,
            limit=fetch_limit,
        )
        merged = [
            *[("user", row) for row in user_rows],
            *[("agent", row) for row in agent_rows],
        ]
        merged.sort(key=_typed_row_key, reverse=True)
        selected = merged[:limit]
        has_more = len(merged) > limit
        next_position = None
        if has_more and selected:
            source, row = selected[-1]
            next_position = TimelinePosition(
                timeline_sort_us=_valid_timeline_sort_us(row),
                source=source,
                message_id=_valid_message_id(row),
            )
        selected.reverse()
        return RoomTimelinePage(
            entries=[
                RoomTimelineEntry(source=source, message=dict(row))
                for source, row in selected
            ],
            has_more=has_more,
            next_position=next_position,
        )

    async def get_user_message_by_id(self, message_id: str) -> dict | None:
        return await self._user_messages.find_one({"message_id": message_id})

    async def get_agent_message_by_id(self, message_id: str) -> dict | None:
        return await self._agent_messages.find_one({"message_id": message_id})

    async def get_user_messages_for_room(
        self, room_id: str, limit: int = 100, before: datetime | None = None
    ) -> list[dict]:
        rows = await self._user_messages.find(
            _room_message_query(room_id, before),
            sort=[("timeline_sort_us", -1), ("message_id", -1)],
            limit=limit,
        )
        rows.reverse()
        return rows

    async def get_agent_messages_for_room(
        self, room_id: str, limit: int = 100, before: datetime | None = None
    ) -> list[dict]:
        rows = await self._agent_messages.find(
            _room_message_query(room_id, before),
            sort=[("timeline_sort_us", -1), ("message_id", -1)],
            limit=limit,
        )
        rows.reverse()
        return rows

    async def get_agent_messages_by_related_message_id(
        self, related_message_id: str
    ) -> list[dict]:
        return await self._agent_messages.find(
            {"related_message_id": related_message_id}
        )

    async def get_agent_task_messages_for_user(
        self, user_id: str, states: list[str]
    ) -> list[dict]:
        return await self.get_pending_task_messages_for_user(user_id, states)

    async def get_task_messages_for_room(self, room_id: str, limit: int) -> list[dict]:
        return await self._agent_messages.find(
            {
                "room_id": room_id,
                "has_task_tracking": True,
            },
            sort=[("task_created_at", -1)],
            limit=limit,
        )

    async def get_pending_task_messages_for_user(
        self, user_id: str, states: list[str]
    ) -> list[dict]:
        return await self._agent_messages.find(
            {
                "user_id": user_id,
                "has_task_tracking": True,
                "message_content.message_task.status.state": {"$in": list(states)},
            },
            sort=[("task_created_at", -1)],
        )

    async def get_thread(self, parent_message_id: str) -> list[dict]:
        thread: list[dict[str, Any]] = []
        frontier = [parent_message_id]
        seen = {parent_message_id}

        while frontier:
            query = {
                "$or": [
                    {"related_message_id": {"$in": frontier}},
                    {"parent_message_id": {"$in": frontier}},
                ]
            }
            rows = [
                *await self._user_messages.find(query),
                *await self._agent_messages.find(query),
            ]
            next_frontier: list[str] = []
            for row in normalize_history_rows([], rows):
                message_id = row.get("message_id")
                if not message_id or message_id in seen:
                    continue
                seen.add(message_id)
                thread.append(row)
                next_frontier.append(str(message_id))
            frontier = next_frontier

        return thread

    async def update_status(
        self, target_message_id: str, status: str, **fields
    ) -> bool:
        payload = status_update_payload(
            status,
            _without_timeline_identity(fields),
        )
        return await self._agent_messages.update_one(
            {
                "message_id": target_message_id,
                _TASK_STATE_PATH: {"$nin": list(_TERMINAL_TASK_STATES)},
            },
            {"$set": payload},
        )

    async def update_user_message(self, message_id: str, updates: dict) -> bool:
        candidate = dict(updates)
        for immutable_field in (
            "message_id",
            "room_id",
            "message_created_at",
            "timeline_sort_us",
            "client_request_id",
            "idempotency_fingerprint",
            "idempotency_fingerprint_version",
        ):
            candidate.pop(immutable_field, None)
        return await self._user_messages.update_one(
            {"message_id": message_id},
            {"$set": candidate},
        )

    async def update_agent_message(self, message_id: str, updates: dict) -> bool:
        candidate = _without_timeline_identity(updates)
        desired_task_state = _updated_task_state(candidate)
        query: dict[str, Any] = {"message_id": message_id}
        if desired_task_state is not None:
            query[_TASK_STATE_PATH] = {"$nin": list(_TERMINAL_TASK_STATES)}
        updated = await self._agent_messages.update_one(
            query,
            {"$set": candidate},
        )
        if updated:
            return True
        current = await self._agent_messages.find_one({"message_id": message_id})
        if current is None:
            return False
        current_state = _updated_task_state(current)
        # A writer may discover that another path committed the same terminal
        # state first, then still need to backfill final response text/artifacts.
        # Never replay the full stale task snapshot: enrich only empty public
        # output fields behind an exact-state/value fence, preserving the task
        # id, history, projection winner, and any artifacts already committed.
        if desired_task_state is not None and current_state in _TERMINAL_TASK_STATES:
            if desired_task_state != current_state:
                return False
            content = candidate.get("message_content")
            current_content = current.get("message_content")
            content = content if isinstance(content, dict) else {}
            current_content = (
                current_content if isinstance(current_content, dict) else {}
            )
            safe_updates: dict[str, Any] = {}
            safe_query: dict[str, Any] = {
                "message_id": message_id,
                _TASK_STATE_PATH: current_state,
            }
            candidate_text = content.get("message_text")
            current_text = current_content.get("message_text")
            if (
                isinstance(candidate_text, str)
                and candidate_text.strip()
                and not (isinstance(current_text, str) and current_text.strip())
            ):
                safe_updates["message_content.message_text"] = candidate_text
                safe_query["message_content.message_text"] = current_text

            candidate_task = content.get("message_task")
            current_task = current_content.get("message_task")
            candidate_task = candidate_task if isinstance(candidate_task, dict) else {}
            current_task = current_task if isinstance(current_task, dict) else {}
            candidate_artifacts = candidate_task.get("artifacts")
            current_artifacts = current_task.get("artifacts")
            if candidate_artifacts and not current_artifacts:
                safe_updates["message_content.message_task.artifacts"] = (
                    candidate_artifacts
                )
                safe_query["message_content.message_task.artifacts"] = current_artifacts

            if not safe_updates:
                return True
            return bool(
                await self._agent_messages.update_one(
                    safe_query,
                    {"$set": safe_updates},
                )
            )
        return True

    async def update_agent_message_if_not_terminal(
        self, message_id: str, updates: dict, terminal_states: list[str]
    ) -> bool:
        return await self._agent_messages.update_one(
            {
                "message_id": message_id,
                "message_content.message_task.status.state": {
                    "$nin": list(terminal_states)
                },
            },
            {"$set": _without_timeline_identity(updates)},
        )

    async def count_agent_messages(self, query: dict) -> int:
        return await self._agent_messages.count(dict(query))

    async def delete_for_room(self, room_id: str) -> dict[str, int]:
        user_count = await self._user_messages.delete_many({"room_id": room_id})
        agent_count = await self._agent_messages.delete_many({"room_id": room_id})
        return {"user_messages": user_count, "agent_messages": agent_count}


def _room_message_query(room_id: str, before: datetime | None) -> dict[str, Any]:
    query: dict[str, Any] = {"room_id": room_id}
    if before is not None:
        query["timeline_sort_us"] = {"$lt": timeline_sort_us_from_value(before)}
    return query


def _timeline_query(
    room_id: str,
    *,
    source: str,
    before: TimelinePosition | None,
) -> dict[str, Any]:
    query: dict[str, Any] = {"room_id": room_id}
    if before is None:
        return query

    source_rank = SOURCE_RANK[source]
    cursor_rank = SOURCE_RANK[before.source]
    if source_rank < cursor_rank:
        query["timeline_sort_us"] = {"$lte": before.timeline_sort_us}
    elif source_rank > cursor_rank:
        query["timeline_sort_us"] = {"$lt": before.timeline_sort_us}
    else:
        query["$or"] = [
            {"timeline_sort_us": {"$lt": before.timeline_sort_us}},
            {
                "timeline_sort_us": before.timeline_sort_us,
                "message_id": {"$lt": before.message_id},
            },
        ]
    return query


def _valid_timeline_sort_us(row: dict[str, Any]) -> int:
    value = row.get("timeline_sort_us")
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("invalid timeline_sort_us in stored message")
    return value


def _valid_message_id(row: dict[str, Any]) -> str:
    value = row.get("message_id")
    if not isinstance(value, str) or not value:
        raise ValueError("invalid message_id in stored message")
    return value


def _typed_row_key(item: tuple[str, dict[str, Any]]) -> tuple[int, int, str]:
    source, row = item
    return timeline_key(
        timeline_sort_us=_valid_timeline_sort_us(row),
        source=source,
        message_id=_valid_message_id(row),
    )


def _without_timeline_identity(updates: dict[str, Any]) -> dict[str, Any]:
    candidate = dict(updates)
    for field in (
        "room_id",
        "message_id",
        "message_created_at",
        "timeline_sort_us",
        "terminal_projection_event_id",
    ):
        candidate.pop(field, None)
    return candidate
