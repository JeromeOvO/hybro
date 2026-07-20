from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

from common.utils.logger import get_logger
from common.utils.time import utcnow

logger = get_logger(__name__)

_PENDING_HITL_DISPLAY_INDEX = "uq_pending_hitl_display_message"
_PENDING_HITL_CONTINUATION_INDEX = "uq_pending_hitl_continuation_message"


def _duplicate_key_mentions_index(error: DuplicateKeyError, index_name: str) -> bool:
    details = getattr(error, "details", None)
    return index_name in str(error) or (
        details is not None and index_name in repr(details)
    )


class HITLRuntimeStorePart:
    def __init__(
        self, *, hitl_requests, room_agent_messages, room_user_messages
    ) -> None:
        self._hitl_requests = hitl_requests
        self._room_agent_messages = room_agent_messages
        self._room_user_messages = room_user_messages

    async def get_pending_hitl_requests_for_message(
        self, user_message_id: str
    ) -> list[dict]:
        try:
            return await self._hitl_requests.find(
                {"user_message_id": user_message_id, "status": "pending"},
                limit=50,
            )
        except Exception:
            logger.error("Failed to get pending HITL requests", exc_info=True)
            return []

    async def create_hitl_request(self, request_data: dict) -> bool:
        try:
            await self._hitl_requests.insert_one(dict(request_data))
            return True
        except Exception:
            logger.error("Failed to create HITL request", exc_info=True)
            return False

    async def get_hitl_request(self, request_id: str) -> dict | None:
        try:
            return await self._hitl_requests.find_one({"request_id": request_id})
        except Exception:
            logger.error("Failed to get HITL request", exc_info=True)
            return None

    async def update_hitl_request(self, request_id: str, **updates) -> bool:
        try:
            return await self._hitl_requests.update_one(
                {"request_id": request_id},
                {"$set": dict(updates)},
            )
        except Exception:
            logger.error("Failed to update HITL request", exc_info=True)
            return False

    async def cas_update_hitl_request(
        self,
        request_id: str,
        expected_status: str,
        **updates,
    ) -> bool:
        try:
            return await self._hitl_requests.update_one(
                {"request_id": request_id, "status": expected_status},
                {"$set": dict(updates)},
            )
        except Exception:
            logger.error("Failed to CAS update HITL request", exc_info=True)
            return False

    async def fenced_update_hitl_request(
        self,
        request_id: str,
        claim_id: str,
        updates: dict | None = None,
        **kw_updates,
    ) -> bool:
        merged = {**(updates or {}), **kw_updates}
        try:
            return await self._hitl_requests.update_one(
                {"request_id": request_id, "claim_id": claim_id},
                {"$set": merged},
            )
        except Exception:
            logger.error("Failed to fenced-update HITL request", exc_info=True)
            return False

    async def claim_hitl_request(self, request_id: str, **updates) -> dict | None:
        try:
            return await self._hitl_requests.find_one_and_update(
                {"request_id": request_id, "status": "pending"},
                {"$set": dict(updates)},
            )
        except Exception:
            logger.error("Failed to claim HITL request", exc_info=True)
            return None

    async def get_pending_hitl_requests(self, room_id: str) -> list[dict]:
        try:
            return await self._hitl_requests.find(
                {"room_id": room_id, "status": "pending"},
                limit=50,
            )
        except Exception:
            logger.error("Failed to get room HITL requests", exc_info=True)
            return []

    async def get_hitl_group_requests(self, group_id: str) -> list[dict]:
        try:
            return await self._hitl_requests.find(
                {"group_id": group_id},
                sort=[("group_index", 1)],
                limit=100,
            )
        except Exception:
            logger.error("Failed to get HITL group requests", exc_info=True)
            return []

    async def count_pending_in_hitl_group(self, group_id: str) -> int:
        try:
            return await self._hitl_requests.count(
                {"group_id": group_id, "status": {"$in": ["pending", "processing"]}},
            )
        except Exception:
            logger.error("Failed to count pending HITL group requests", exc_info=True)
            return -1

    async def claim_hitl_group_routing(
        self,
        group_id: str,
        claim_id: str,
    ) -> bool:
        try:
            return await self._hitl_requests.update_one(
                {
                    "group_id": group_id,
                    "group_index": 0,
                    "group_routing_claim_id": {"$exists": False},
                },
                {
                    "$set": {
                        "group_routing_claim_id": claim_id,
                        "group_routing_claimed_at": utcnow(),
                    }
                },
            )
        except Exception:
            logger.error("Failed to claim HITL group routing", exc_info=True)
            return False

    async def release_hitl_group_routing(
        self,
        group_id: str,
        claim_id: str,
    ) -> bool:
        try:
            return await self._hitl_requests.update_one(
                {"group_id": group_id, "group_routing_claim_id": claim_id},
                {
                    "$unset": {
                        "group_routing_claim_id": "",
                        "group_routing_claimed_at": "",
                    }
                },
            )
        except Exception:
            logger.error("Failed to release HITL group routing", exc_info=True)
            return False

    async def count_hitl_requests_for_message(
        self,
        continuation_message_id: str,
    ) -> int:
        try:
            return await self._hitl_requests.count(
                {
                    "continuation_message_id": continuation_message_id,
                    "status": {"$ne": "canceled"},
                    "$or": [
                        {"group_index": None},
                        {"group_index": {"$exists": False}},
                        {"group_index": 0},
                    ],
                }
            )
        except Exception:
            logger.error("Failed to count HITL requests for message", exc_info=True)
            return 0

    async def update_agent_message_task_state(
        self,
        message_id: str,
        state: str,
    ) -> bool:
        try:
            return await self._room_agent_messages.update_one(
                {"message_id": message_id},
                {"$set": {"message_content.message_task.status.state": state}},
            )
        except Exception:
            logger.error("Failed to update agent message task state", exc_info=True)
            return False

    async def persist_hitl_request_id_on_message(
        self,
        message_id: str,
        request_id: str | None,
    ) -> bool:
        try:
            await self._ensure_message_task_metadata(message_id)
            metadata_path = (
                "message_content.message_task.metadata.hitl_request_id"
            )
            update = (
                {"$set": {metadata_path: request_id}}
                if request_id is not None
                else {"$unset": {metadata_path: ""}}
            )
            projected = await self._room_agent_messages.find_one_and_update(
                {"message_id": message_id},
                update,
                return_document=ReturnDocument.AFTER,
            )
            if not isinstance(projected, dict):
                return False
            metadata = (
                projected.get("message_content", {})
                .get("message_task", {})
                .get("metadata")
                or {}
            )
            return metadata.get("hitl_request_id") == request_id
        except Exception:
            logger.error(
                "Failed to persist HITL request id on agent message",
                exc_info=True,
            )
            return False

    async def find_pending_hitl_request_for_agent_message(
        self,
        *,
        room_id: str,
        display_message_id: str | None,
        continuation_message_id: str | None,
        agent_id: str | None,
        a2a_task_id: str | None,
        a2a_context_id: str | None,
    ) -> dict[str, Any] | None:
        try:
            return await self._find_pending_hitl_request_for_agent_message(
                room_id=room_id,
                display_message_id=display_message_id,
                continuation_message_id=continuation_message_id,
            )
        except Exception:
            logger.error(
                "Failed to find pending agent HITL request",
                extra={
                    "room_id": room_id,
                    "display_message_id": display_message_id,
                    "continuation_message_id": continuation_message_id,
                    "agent_id": agent_id,
                    "a2a_task_id": a2a_task_id,
                    "a2a_context_id": a2a_context_id,
                },
                exc_info=True,
            )
            return None

    async def _find_pending_hitl_request_for_agent_message(
        self,
        *,
        room_id: str,
        display_message_id: str | None,
        continuation_message_id: str | None,
    ) -> dict[str, Any] | None:
        display_existing = None
        continuation_existing = None
        if display_message_id:
            display_existing = await self._hitl_requests.find_one(
                self._pending_agent_hitl_identity_query(
                    room_id=room_id,
                    identity_clause={"display_message_id": display_message_id},
                )
            )
        if continuation_message_id:
            continuation_existing = await self._hitl_requests.find_one(
                self._pending_agent_hitl_identity_query(
                    room_id=room_id,
                    identity_clause={
                        "continuation_message_id": continuation_message_id
                    },
                )
            )
        if display_existing and continuation_existing:
            display_request_id = display_existing.get("request_id")
            continuation_request_id = continuation_existing.get("request_id")
            if display_request_id != continuation_request_id:
                logger.error(
                    "Ambiguous pending agent HITL request lookup",
                    extra={
                        "room_id": room_id,
                        "display_message_id": display_message_id,
                        "continuation_message_id": continuation_message_id,
                        "display_request_id": display_request_id,
                        "continuation_request_id": continuation_request_id,
                    },
                )
                return None
            return display_existing
        return display_existing or continuation_existing

    def _pending_agent_hitl_identity_query(
        self,
        *,
        room_id: str,
        identity_clause: dict[str, str],
    ) -> dict[str, Any]:
        return {
            "room_id": room_id,
            "status": "pending",
            "source": "agent",
            "$or": [identity_clause],
        }

    async def create_or_reuse_pending_hitl_request(
        self,
        request_data: dict[str, Any],
    ) -> tuple[dict[str, Any], bool] | None:
        doc = dict(request_data)
        try:
            await self._hitl_requests.insert_one(doc)
            return doc, True
        except DuplicateKeyError as error:
            existing = await self._read_pending_hitl_after_duplicate(doc, error)
            if existing is not None:
                return existing, False
            logger.error(
                "Failed to read existing pending agent HITL request after duplicate",
                extra={
                    "room_id": doc.get("room_id"),
                    "display_message_id": doc.get("display_message_id"),
                    "continuation_message_id": doc.get("continuation_message_id"),
                },
            )
            return None
        except Exception:
            logger.error("Failed to create pending HITL request", exc_info=True)
            return None

    async def _find_pending_hitl_duplicate_identity(
        self,
        doc: dict[str, Any],
        *,
        display_message_id: str | None,
        continuation_message_id: str | None,
    ) -> dict[str, Any] | None:
        return await self.find_pending_hitl_request_for_agent_message(
            room_id=doc.get("room_id"),
            display_message_id=display_message_id,
            continuation_message_id=continuation_message_id,
            agent_id=doc.get("agent_id"),
            a2a_task_id=doc.get("a2a_task_id"),
            a2a_context_id=doc.get("a2a_context_id"),
        )

    async def _read_pending_hitl_after_duplicate(
        self,
        doc: dict[str, Any],
        error: DuplicateKeyError,
    ) -> dict[str, Any] | None:
        display_message_id = doc.get("display_message_id")
        continuation_message_id = doc.get("continuation_message_id")

        if display_message_id and continuation_message_id:
            first_identity = (
                "continuation"
                if _duplicate_key_mentions_index(
                    error, _PENDING_HITL_CONTINUATION_INDEX
                )
                else "display"
            )
            return await self._read_pending_hitl_duplicate_pair(
                doc,
                display_message_id=display_message_id,
                continuation_message_id=continuation_message_id,
                first_identity=first_identity,
            )

        if _duplicate_key_mentions_index(error, _PENDING_HITL_DISPLAY_INDEX):
            return await self._find_pending_hitl_duplicate_identity(
                doc,
                display_message_id=display_message_id,
                continuation_message_id=None,
            )
        if _duplicate_key_mentions_index(error, _PENDING_HITL_CONTINUATION_INDEX):
            return await self._find_pending_hitl_duplicate_identity(
                doc,
                display_message_id=None,
                continuation_message_id=continuation_message_id,
            )

        return await self._find_pending_hitl_duplicate_identity(
            doc,
            display_message_id=display_message_id,
            continuation_message_id=continuation_message_id,
        )

    async def _read_pending_hitl_duplicate_pair(
        self,
        doc: dict[str, Any],
        *,
        display_message_id: str,
        continuation_message_id: str,
        first_identity: str,
    ) -> dict[str, Any] | None:
        display_existing = None
        continuation_existing = None
        if first_identity == "continuation":
            continuation_existing = await self._find_pending_hitl_duplicate_identity(
                doc,
                display_message_id=None,
                continuation_message_id=continuation_message_id,
            )
            display_existing = await self._find_pending_hitl_duplicate_identity(
                doc,
                display_message_id=display_message_id,
                continuation_message_id=None,
            )
        else:
            display_existing = await self._find_pending_hitl_duplicate_identity(
                doc,
                display_message_id=display_message_id,
                continuation_message_id=None,
            )
            continuation_existing = await self._find_pending_hitl_duplicate_identity(
                doc,
                display_message_id=None,
                continuation_message_id=continuation_message_id,
            )

        if display_existing and continuation_existing:
            display_request_id = display_existing.get("request_id")
            continuation_request_id = continuation_existing.get("request_id")
            if display_request_id != continuation_request_id:
                logger.error(
                    "Ambiguous pending agent HITL duplicate readback",
                    extra={
                        "room_id": doc.get("room_id"),
                        "display_message_id": display_message_id,
                        "continuation_message_id": continuation_message_id,
                        "display_request_id": display_request_id,
                        "continuation_request_id": continuation_request_id,
                    },
                )
                return None
            return display_existing
        return display_existing or continuation_existing

    async def persist_pending_hitl_on_agent_message(
        self,
        message_id: str,
        *,
        request_id: str,
        prompt: str,
        prompt_type: Any,
        choices: list[str] | None,
        a2a_task_id: str | None,
        a2a_context_id: str | None,
        group_id: str | None,
        group_total: int | None,
        group_index: int | None,
    ) -> bool:
        try:
            await self._ensure_message_task_metadata(message_id)
            metadata_prefix = "message_content.message_task.metadata"
            updates: dict[str, Any] = {
                "message_content.message_task.status.state": "input-required",
                f"{metadata_prefix}.hitl_request_id": request_id,
                f"{metadata_prefix}.hitl_prompt": prompt,
                f"{metadata_prefix}.hitl_prompt_type": getattr(
                    prompt_type, "value", prompt_type
                ),
                f"{metadata_prefix}.hitl_choices": choices,
                f"{metadata_prefix}.user_answer": None,
                "task_updated_at": utcnow(),
            }
            optional_metadata = {
                f"{metadata_prefix}.hitl_a2a_task_id": a2a_task_id,
                f"{metadata_prefix}.hitl_a2a_context_id": a2a_context_id,
                f"{metadata_prefix}.hitl_group_id": group_id,
                f"{metadata_prefix}.hitl_group_total": group_total,
                f"{metadata_prefix}.hitl_group_index": group_index,
            }
            unsets: dict[str, str] = {}
            for path, value in optional_metadata.items():
                if value is None:
                    unsets[path] = ""
                else:
                    updates[path] = value

            update_doc: dict[str, Any] = {"$set": updates}
            if unsets:
                update_doc["$unset"] = unsets

            projected = await self._room_agent_messages.find_one_and_update(
                {"message_id": message_id},
                update_doc,
                return_document=ReturnDocument.AFTER,
            )
            if not projected:
                return False

            message_task = (
                projected.get("message_content", {}).get("message_task", {})
                if isinstance(projected, dict)
                else {}
            )
            metadata = message_task.get("metadata") or {}
            state = (message_task.get("status") or {}).get("state")
            return (
                state == "input-required"
                and metadata.get("hitl_request_id") == request_id
            )
        except Exception:
            logger.error(
                "Failed to persist pending HITL on agent message", exc_info=True
            )
            return False

    async def _ensure_message_task_metadata(self, message_id: str) -> None:
        await self._room_agent_messages.update_one(
            {
                "message_id": message_id,
                "message_content.message_task.metadata": None,
            },
            {"$set": {"message_content.message_task.metadata": {}}},
        )

    async def persist_hitl_user_answer(
        self,
        message_id: str,
        user_input: str | None,
    ) -> bool:
        try:
            await self._ensure_message_task_metadata(message_id)
            return await self._room_agent_messages.update_one(
                {"message_id": message_id},
                {
                    "$set": {
                        "message_content.message_task.metadata.user_answer": user_input
                    }
                },
            )
        except Exception:
            logger.error("Failed to persist HITL user answer", exc_info=True)
            return False

    async def persist_hitl_group_metadata(
        self,
        message_id: str,
        *,
        group_id: str | None,
        group_total: int | None,
        group_index: int | None,
    ) -> bool:
        try:
            await self._ensure_message_task_metadata(message_id)
            if group_id is None:
                return await self._room_agent_messages.update_one(
                    {"message_id": message_id},
                    {
                        "$unset": {
                            "message_content.message_task.metadata.hitl_group_id": "",
                            "message_content.message_task.metadata.hitl_group_total": "",
                            "message_content.message_task.metadata.hitl_group_index": "",
                        }
                    },
                )
            updates: dict[str, Any] = {
                "message_content.message_task.metadata.hitl_group_id": group_id,
            }
            if group_total is not None:
                updates["message_content.message_task.metadata.hitl_group_total"] = (
                    group_total
                )
            if group_index is not None:
                updates["message_content.message_task.metadata.hitl_group_index"] = (
                    group_index
                )
            return await self._room_agent_messages.update_one(
                {"message_id": message_id},
                {"$set": updates},
            )
        except Exception:
            logger.error("Failed to persist HITL group metadata", exc_info=True)
            return False

    async def iter_stale_processing_hitl_requests(
        self,
        cutoff: Any,
    ) -> AsyncIterator[dict]:
        try:
            docs = await self._hitl_requests.find(
                {"status": "processing", "responded_at": {"$lt": cutoff}},
            )
        except Exception:
            logger.error(
                "Failed to iterate stale processing HITL requests", exc_info=True
            )
            docs = []
        for doc in docs:
            yield doc

    async def ensure_hitl_indexes(self) -> None:
        noncritical_indexes = [
            ((("request_id", 1),), {"unique": True}),
            ((("room_id", 1), ("status", 1)), {}),
            ((("expires_at", 1), ("status", 1)), {}),
            ((("user_message_id", 1), ("status", 1)), {}),
            ((("continuation_message_id", 1),), {}),
        ]
        for keys, kwargs in noncritical_indexes:
            try:
                await self._hitl_requests.create_index(list(keys), **kwargs)
            except Exception:
                logger.error(
                    "Failed to create non-critical HITL index",
                    extra={"index_keys": list(keys), "index_options": kwargs},
                    exc_info=True,
                )

        critical_indexes = [
            (
                [("room_id", 1), ("display_message_id", 1)],
                {
                    "unique": True,
                    "name": _PENDING_HITL_DISPLAY_INDEX,
                    "partialFilterExpression": {
                        "status": "pending",
                        "source": "agent",
                        "display_message_id": {"$type": "string"},
                    },
                },
            ),
            (
                [("room_id", 1), ("continuation_message_id", 1)],
                {
                    "unique": True,
                    "name": _PENDING_HITL_CONTINUATION_INDEX,
                    "partialFilterExpression": {
                        "status": "pending",
                        "source": "agent",
                        "continuation_message_id": {"$type": "string"},
                    },
                },
            ),
        ]
        for keys, kwargs in critical_indexes:
            try:
                await self._hitl_requests.create_index(keys, **kwargs)
            except Exception:
                logger.error(
                    "Failed to create critical HITL unique index",
                    extra={"index_name": kwargs["name"]},
                    exc_info=True,
                )
                raise
