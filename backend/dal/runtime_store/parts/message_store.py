from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from common.a2a_constants import TERMINAL_STATES, CommonTaskState
from common.utils.a2a_helpers import sanitize_artifact_parts
from common.utils.logger import get_logger
from common.utils.time import utcnow
from dal.runtime_store.parts.parsing import (
    _extract_text_from_artifact_parts,
    _modified_count,
    _mongo_update_succeeded,
    _safe_parse_agent_message,
    _safe_parse_user_message,
    _strip_file_urls,
    _strip_unset_task_tracking_fields,
)
from models.room import MessageContent, RoomAgentMessage, RoomUserMessage

logger = get_logger(__name__)
ARTIFACT_MATERIALIZATION_WAIT_SECONDS = 20 * 60 + 60


def _legacy_claim_threshold_text(stale_threshold: Any) -> str:
    if isinstance(stale_threshold, datetime):
        return (
            stale_threshold.astimezone(UTC)
            .replace(tzinfo=None)
            .isoformat(timespec="microseconds")
        )
    return str(stale_threshold)


class MessageRuntimeStorePart:
    def __init__(
        self, *, room_agent_messages, room_user_messages, message_repository
    ) -> None:
        self._room_agent_messages = room_agent_messages
        self._room_user_messages = room_user_messages
        self._message_repository = message_repository

    async def get_room_user_message_by_message_id(
        self, message_id: str
    ) -> RoomUserMessage | None:
        try:
            return _safe_parse_user_message(
                await self._message_repository.get_user_message_by_id(message_id)
            )
        except Exception:
            logger.error("Failed to get room user message", exc_info=True)
            return None

    async def get_room_user_messages_by_room_id(
        self,
        room_id: str,
    ) -> list[RoomUserMessage]:
        try:
            docs = await self._message_repository.get_user_messages_for_room(room_id)
            messages = [
                _safe_parse_user_message(doc) for doc in docs if doc is not None
            ]
            return [message for message in messages if message is not None]
        except Exception:
            logger.error("Failed to get room user messages", exc_info=True)
            return []

    async def get_room_agent_message_by_message_id(
        self, message_id: str
    ) -> RoomAgentMessage | None:
        try:
            return _safe_parse_agent_message(
                await self._message_repository.get_agent_message_by_id(message_id)
            )
        except Exception:
            logger.error("Failed to get room agent message", exc_info=True)
            return None

    async def get_room_agent_messages_by_room_id(
        self,
        room_id: str,
    ) -> list[RoomAgentMessage]:
        try:
            docs = await self._message_repository.get_agent_messages_for_room(room_id)
            messages = [
                _safe_parse_agent_message(doc) for doc in docs if doc is not None
            ]
            return [message for message in messages if message is not None]
        except Exception:
            logger.error("Failed to get room agent messages", exc_info=True)
            return []

    async def get_room_agent_messages_by_related_message_id(
        self, related_message_id: str
    ) -> list[RoomAgentMessage]:
        try:
            docs = (
                await self._message_repository.get_agent_messages_by_related_message_id(
                    related_message_id
                )
            )
            messages = [
                _safe_parse_agent_message(doc) for doc in docs if doc is not None
            ]
            return [message for message in messages if message is not None]
        except Exception:
            logger.error("Failed to get related agent messages", exc_info=True)
            return []

    async def add_room_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> bool:
        try:
            if room_agent_message.message_id == "":
                room_agent_message.message_id = str(uuid.uuid4())
            await self._message_repository.save_agent_message(
                room_agent_message.model_dump(mode="json")
            )
            return True
        except Exception:
            logger.error("Failed to add room agent message", exc_info=True)
            return False

    async def add_room_user_message(self, room_user_message: RoomUserMessage) -> bool:
        try:
            if room_user_message.message_id == "":
                room_user_message.message_id = str(uuid.uuid4())
            doc = room_user_message.model_dump(mode="json", exclude={"quote"})
            _strip_file_urls(doc)
            return bool(await self._message_repository.save_user_message(doc))
        except Exception:
            logger.error("Failed to add room user message", exc_info=True)
            return False

    async def update_room_user_message_by_message_id(
        self, message_id: str, room_user_message: RoomUserMessage
    ) -> bool:
        try:
            update_data = room_user_message.model_dump(exclude_unset=True, mode="json")
            _strip_file_urls(update_data)
            return await self._message_repository.update_user_message(
                message_id,
                update_data,
            )
        except Exception:
            logger.error("Failed to update room user message", exc_info=True)
            return False

    async def upsert_room_agent_message(
        self, room_agent_message: RoomAgentMessage
    ) -> None:
        try:
            await self._room_agent_messages.replace_one(
                {"message_id": room_agent_message.message_id},
                room_agent_message.model_dump(mode="json"),
                upsert=True,
            )
        except Exception:
            logger.error("Failed to upsert room agent message", exc_info=True)

    async def delete_room_agent_message_by_message_id(self, message_id: str) -> bool:
        try:
            return await self._room_agent_messages.delete_one(
                {"message_id": message_id}
            )
        except Exception:
            logger.error("Failed to delete room agent message", exc_info=True)
            return False

    async def update_room_agent_message_by_message_id(
        self, message_id: str, room_agent_message: RoomAgentMessage
    ) -> bool:
        try:
            update_data = _strip_unset_task_tracking_fields(
                room_agent_message.model_dump(exclude_unset=True, mode="json")
            )
            return await self._message_repository.update_agent_message(
                message_id,
                update_data,
            )
        except Exception:
            logger.error("Failed to update room agent message", exc_info=True)
            return False

    async def claim_user_message_for_processing(self, message_id: str) -> bool:
        try:
            doc = await self._room_user_messages.find_one_and_update(
                {"message_id": message_id, "processing_claimed_at": None},
                {"$set": {"processing_claimed_at": utcnow()}},
            )
            return doc is not None
        except Exception:
            logger.error("Failed to claim user message", exc_info=True)
            return False

    async def unclaim_user_message(self, message_id: str) -> bool:
        try:
            return await self._room_user_messages.update_one(
                {"message_id": message_id},
                {"$set": {"processing_claimed_at": None}},
            )
        except Exception:
            logger.error("Failed to unclaim user message", exc_info=True)
            return False

    async def claim_or_reclaim_user_message(
        self,
        message_id: str,
        stale_threshold: Any,
    ) -> bool:
        try:
            doc = await self._room_user_messages.find_one_and_update(
                {
                    "message_id": message_id,
                    "$or": [
                        {"processing_claimed_at": None},
                        {"processing_claimed_at": {"$lt": stale_threshold}},
                        {
                            "processing_claimed_at": {
                                "$type": "string",
                                "$lt": _legacy_claim_threshold_text(stale_threshold),
                            }
                        },
                    ],
                },
                {"$set": {"processing_claimed_at": utcnow()}},
            )
            return doc is not None
        except Exception:
            logger.error("Failed to claim or reclaim user message", exc_info=True)
            return False

    async def refresh_processing_claim(self, message_id: str) -> bool:
        try:
            return await self._room_user_messages.update_one(
                {"message_id": message_id, "processing_claimed_at": {"$ne": None}},
                {"$set": {"processing_claimed_at": utcnow()}},
            )
        except Exception:
            logger.error("Failed to refresh processing claim", exc_info=True)
            return False

    async def turn_exists(self, room_id: str, turn_id: str) -> bool:
        try:
            user = await self._room_user_messages.find_one(
                {"room_id": room_id, "turn_id": turn_id}
            )
            if user is not None:
                return True
            agent = await self._room_agent_messages.find_one(
                {"room_id": room_id, "turn_id": turn_id}
            )
            return agent is not None
        except Exception:
            logger.error("Failed to check turn existence", exc_info=True)
            return False

    async def cancel_descendants(self, message_id: str) -> int:
        terminal_statuses = sorted(state.value for state in TERMINAL_STATES)
        to_visit = [message_id]
        all_descendant_ids: list[str] = []

        while to_visit:
            children = await self._room_agent_messages.find(
                {
                    "related_message_id": {"$in": to_visit},
                    "message_content.message_task": {"$ne": None},
                    "message_content.message_task.status.state": {
                        "$nin": terminal_statuses
                    },
                },
                projection={"message_id": 1},
            )
            child_ids = [
                str(child["message_id"])
                for child in children
                if child.get("message_id") is not None
            ]
            all_descendant_ids.extend(child_ids)
            to_visit = child_ids

        if not all_descendant_ids:
            return 0

        result = await self._room_agent_messages.update_many(
            {"message_id": {"$in": all_descendant_ids}},
            {
                "$set": {
                    "message_content.message_task.status.state": (
                        CommonTaskState.CANCELED.value
                    ),
                }
            },
        )
        return _modified_count(result)

    async def cancel_agent_messages_by_ids(self, message_ids: list[str]) -> int:
        if not message_ids:
            return 0
        terminal_statuses = sorted(state.value for state in TERMINAL_STATES)
        result = await self._room_agent_messages.update_many(
            {
                "message_id": {"$in": list(message_ids)},
                "message_content.message_task": {"$ne": None},
                "message_content.message_task.status.state": {
                    "$nin": terminal_statuses
                },
            },
            {
                "$set": {
                    "message_content.message_task.status.state": (
                        CommonTaskState.CANCELED.value
                    ),
                }
            },
        )
        return _modified_count(result)

    async def update_room_agent_message_with_new_message_content_by_message_id(
        self, message_id: str, message_content: MessageContent
    ) -> bool:
        try:
            return await self._message_repository.update_agent_message(
                message_id,
                {"message_content": message_content.model_dump(mode="json")},
            )
        except Exception:
            logger.error("Failed to update room agent message content", exc_info=True)
            return False

    async def update_last_notified_state(self, message_id: str, state: str) -> bool:
        try:
            return await self._room_agent_messages.update_one(
                {"message_id": message_id, "last_notified_state": {"$ne": state}},
                {"$set": {"last_notified_state": state}},
            )
        except Exception:
            logger.error("Failed to update last notified state", exc_info=True)
            return False

    async def reset_last_notified_state(self, message_id: str) -> bool:
        try:
            return await self._room_agent_messages.update_one(
                {"message_id": message_id},
                {"$unset": {"last_notified_state": ""}},
            )
        except Exception:
            logger.error("Failed to reset last notified state", exc_info=True)
            return False

    async def update_task_state_on_message(
        self,
        message_id: str,
        state: str,
        *,
        message_text: str | None = None,
        artifacts: list[dict] | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
    ) -> tuple[bool, str | None]:
        resolved_message_text = message_text
        try:
            from common.utils.a2a_helpers import (
                artifacts_to_dicts,
                is_terminal_task_state_value,
                prepare_terminal_agent_content,
            )

            if is_terminal_task_state_value(state):
                if artifacts is None:
                    existing = await self.get_room_agent_message_by_message_id(
                        message_id
                    )
                    task = (
                        existing.message_content.message_task
                        if existing and existing.message_content
                        else None
                    )
                    if task and task.artifacts:
                        artifacts = artifacts_to_dicts(task.artifacts)
                message_text, artifacts, _ = prepare_terminal_agent_content(
                    message_text=message_text,
                    artifacts=artifacts,
                )
                resolved_message_text = message_text

            updates: dict[str, Any] = {
                "message_content.message_task.status.state": state,
                "task_updated_at": utcnow(),
            }
            if message_text is not None:
                updates["message_content.message_text"] = message_text
            if artifacts is not None:
                updates["message_content.message_task.artifacts"] = artifacts
            if task_id is not None:
                updates["message_content.message_task.id"] = task_id
            if context_id is not None:
                updates["message_content.message_task.contextId"] = context_id

            terminal_values = sorted(state.value for state in TERMINAL_STATES)
            updated = (
                await self._message_repository.update_agent_message_if_not_terminal(
                    message_id,
                    updates,
                    terminal_values,
                )
            )
            return updated, resolved_message_text
        except Exception:
            logger.error("Failed to update task state on message", exc_info=True)
            return False, resolved_message_text

    async def claim_terminal_finalization(
        self,
        message_id: str,
        state: str,
        *,
        message_text: str | None,
        artifacts: list[dict] | None,
    ) -> tuple[str | None, str | None]:
        """Claim a recoverable terminal finalizer without making the task terminal."""
        from common.utils.a2a_helpers import prepare_terminal_agent_content

        message_text, artifacts, _ = prepare_terminal_agent_content(
            message_text=message_text,
            artifacts=artifacts,
        )
        token = await self.begin_terminal_finalization(message_id, state)
        if token is None:
            return None, message_text
        if not await self.set_terminal_finalization_content(
            message_id,
            token,
            message_text=message_text,
            artifacts=artifacts,
        ):
            return None, message_text
        return token, message_text

    async def begin_terminal_finalization(
        self,
        message_id: str,
        state: str,
        *,
        recovery_source: str = "message",
        recovery_id: str | None = None,
    ) -> str | None:
        """Fence artifact journal writes before terminal projection is assembled."""
        token = uuid.uuid4().hex
        now = utcnow()
        stale_before = now - timedelta(minutes=5)
        terminal_values = sorted(state.value for state in TERMINAL_STATES)
        updates: dict[str, Any] = {
            "terminal_finalization.state": "finalizing",
            "terminal_finalization.token": token,
            "terminal_finalization.target_state": state,
            "terminal_finalization.recovery_source": recovery_source,
            "terminal_finalization.recovery_id": recovery_id,
            "terminal_finalization.started_at": now,
            "terminal_finalization.heartbeat_at": now,
            "task_updated_at": now,
        }
        query = {
            "message_id": message_id,
            "message_content.message_task.status.state": {"$nin": terminal_values},
            "$and": [
                {
                    "$or": [
                        {"terminal_finalization.state": {"$ne": "finalizing"}},
                        {"terminal_finalization.heartbeat_at": {"$lt": stale_before}},
                        {
                            "$and": [
                                {
                                    "terminal_finalization.heartbeat_at": {
                                        "$exists": False
                                    }
                                },
                                {
                                    "terminal_finalization.started_at": {
                                        "$lt": stale_before
                                    }
                                },
                            ]
                        },
                    ]
                },
                {
                    "$or": [
                        {"artifact_materialization.state": {"$ne": "running"}},
                        {"artifact_materialization.expires_at": {"$lte": now}},
                    ]
                },
            ],
        }
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ARTIFACT_MATERIALIZATION_WAIT_SECONDS
        while loop.time() < deadline:
            claimed_at = utcnow()
            updates.update(
                {
                    "terminal_finalization.started_at": claimed_at,
                    "terminal_finalization.heartbeat_at": claimed_at,
                    "task_updated_at": claimed_at,
                }
            )
            query["$and"][1]["$or"][1]["artifact_materialization.expires_at"] = {
                "$lte": claimed_at
            }
            result = await self._room_agent_messages.update_one(
                query,
                {"$set": updates},
            )
            if _mongo_update_succeeded(result):
                return token
            current = await self._room_agent_messages.find_one(
                {"message_id": message_id},
                {
                    "terminal_finalization.state": 1,
                    "artifact_materialization.state": 1,
                    "artifact_materialization.expires_at": 1,
                },
            )
            if current is None:
                return None
            finalization = current.get("terminal_finalization") or {}
            if finalization.get("state") == "finalizing":
                return None
            artifact_lock = current.get("artifact_materialization") or {}
            if artifact_lock.get("state") != "running":
                return None
            await asyncio.sleep(0.05)
        return None

    async def terminal_finalization_matches(
        self,
        message_id: str,
        state: str,
        *,
        recovery_source: str,
        recovery_id: str | None,
    ) -> bool:
        """Return whether this exact durable recovery already reached terminal."""
        if recovery_id is None:
            return False
        current = await self._room_agent_messages.find_one(
            {
                "message_id": message_id,
                "message_content.message_task.status.state": state,
                "terminal_finalization.state": "terminal",
                "terminal_finalization.target_state": state,
                "terminal_finalization.recovery_source": recovery_source,
                "terminal_finalization.recovery_id": recovery_id,
            }
        )
        return current is not None

    async def set_terminal_finalization_content(
        self,
        message_id: str,
        token: str,
        *,
        message_text: str | None,
        artifacts: list[dict] | None,
    ) -> bool:
        from common.utils.a2a_helpers import prepare_terminal_agent_content

        message_text, artifacts, _ = prepare_terminal_agent_content(
            message_text=message_text,
            artifacts=artifacts,
        )
        updates: dict[str, Any] = {
            "terminal_finalization.heartbeat_at": utcnow(),
            "task_updated_at": utcnow(),
        }
        if message_text is not None:
            updates["message_content.message_text"] = message_text
        if artifacts is not None:
            updates["message_content.message_task.artifacts"] = artifacts
        result = await self._room_agent_messages.update_one(
            {
                "message_id": message_id,
                "terminal_finalization.state": "finalizing",
                "terminal_finalization.token": token,
            },
            {"$set": updates},
        )
        return _mongo_update_succeeded(result)

    async def heartbeat_terminal_finalization(
        self, message_id: str, token: str
    ) -> bool:
        result = await self._room_agent_messages.update_one(
            {
                "message_id": message_id,
                "terminal_finalization.state": "finalizing",
                "terminal_finalization.token": token,
            },
            {
                "$set": {
                    "terminal_finalization.heartbeat_at": utcnow(),
                    "task_updated_at": utcnow(),
                }
            },
        )
        return _mongo_update_succeeded(result)

    async def claim_terminal_finalization_step(
        self, message_id: str, token: str, step: str
    ) -> bool:
        now = utcnow()
        stale_before = now - timedelta(minutes=5)
        prefix = f"terminal_finalization.steps.{step}"
        result = await self._room_agent_messages.update_one(
            {
                "message_id": message_id,
                "terminal_finalization.state": "finalizing",
                "terminal_finalization.token": token,
                f"{prefix}.completed": {"$ne": True},
                "$or": [
                    {f"{prefix}.state": {"$ne": "running"}},
                    {f"{prefix}.started_at": {"$lt": stale_before}},
                ],
            },
            {
                "$set": {
                    f"{prefix}.state": "running",
                    f"{prefix}.started_at": now,
                    "terminal_finalization.heartbeat_at": now,
                }
            },
        )
        return _mongo_update_succeeded(result)

    async def complete_terminal_finalization_step(
        self, message_id: str, token: str, step: str
    ) -> bool:
        prefix = f"terminal_finalization.steps.{step}"
        result = await self._room_agent_messages.update_one(
            {
                "message_id": message_id,
                "terminal_finalization.state": "finalizing",
                "terminal_finalization.token": token,
                f"{prefix}.state": "running",
            },
            {
                "$set": {
                    f"{prefix}.state": "completed",
                    f"{prefix}.completed": True,
                    f"{prefix}.completed_at": utcnow(),
                    "terminal_finalization.heartbeat_at": utcnow(),
                }
            },
        )
        return _mongo_update_succeeded(result)

    async def claim_artifact_materialization(
        self, message_id: str, owner: str
    ) -> str | None:
        token = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        deadline = loop.time() + ARTIFACT_MATERIALIZATION_WAIT_SECONDS
        terminal_values = sorted(state.value for state in TERMINAL_STATES)
        while loop.time() < deadline:
            now = utcnow()
            result = await self._room_agent_messages.update_one(
                {
                    "message_id": message_id,
                    "message_content.message_task.status.state": {
                        "$nin": terminal_values
                    },
                    "terminal_finalization.state": {"$ne": "finalizing"},
                    "$or": [
                        {"artifact_materialization.state": {"$ne": "running"}},
                        {"artifact_materialization.expires_at": {"$lte": now}},
                    ],
                },
                {
                    "$set": {
                        "artifact_materialization": {
                            "state": "running",
                            "token": token,
                            "owner": owner,
                            "started_at": now,
                            "expires_at": now + timedelta(minutes=2),
                        }
                    }
                },
            )
            if _mongo_update_succeeded(result):
                return token
            current = await self._room_agent_messages.find_one(
                {"message_id": message_id},
                {
                    "terminal_finalization.state": 1,
                    "artifact_materialization.state": 1,
                },
            )
            if current is None:
                return None
            if (current.get("terminal_finalization") or {}).get(
                "state"
            ) == "finalizing":
                return None
            await asyncio.sleep(0.05)
        return None

    async def heartbeat_artifact_materialization(
        self, message_id: str, token: str
    ) -> bool:
        now = utcnow()
        result = await self._room_agent_messages.update_one(
            {
                "message_id": message_id,
                "artifact_materialization.state": "running",
                "artifact_materialization.token": token,
            },
            {
                "$set": {
                    "artifact_materialization.expires_at": now + timedelta(minutes=2)
                }
            },
        )
        return _mongo_update_succeeded(result)

    async def release_artifact_materialization(
        self, message_id: str, token: str
    ) -> None:
        await self._room_agent_messages.update_one(
            {
                "message_id": message_id,
                "artifact_materialization.token": token,
            },
            {"$unset": {"artifact_materialization": ""}},
        )

    async def complete_terminal_finalization(
        self, message_id: str, token: str, state: str
    ) -> bool:
        result = await self._room_agent_messages.update_one(
            {
                "message_id": message_id,
                "terminal_finalization.state": "finalizing",
                "terminal_finalization.token": token,
            },
            {
                "$set": {
                    "message_content.message_task.status.state": state,
                    "terminal_finalization.state": "terminal",
                    "terminal_finalization.completed_at": utcnow(),
                    "task_updated_at": utcnow(),
                }
            },
        )
        return _mongo_update_succeeded(result)

    async def accumulate_artifact_on_message(
        self,
        message_id: str,
        artifact: dict,
        append: bool = False,
        update_key: str | None = None,
    ) -> bool:
        """Accumulate A2A artifact chunks with atomic DAL collection updates."""
        try:
            raw_parts = artifact.get("parts", [])
            clean_parts = sanitize_artifact_parts(raw_parts)
            artifact = {**artifact, "parts": clean_parts}

            if append and not clean_parts:
                logger.warning(
                    "All artifact parts dropped by sanitizer; skipping append "
                    "(message_id=%s)",
                    message_id,
                )
                return False

            artifact_id = artifact.get("artifactId") or artifact.get("artifact_id")
            explicit_index = artifact.get("index")
            if not artifact_id and explicit_index is not None:
                artifact_id = f"index:{explicit_index}"
                artifact = {**artifact, "artifactId": artifact_id}
            artifact_text = _extract_text_from_artifact_parts(clean_parts)

            base_filter = {
                "message_id": message_id,
                "terminal_finalization.state": {"$ne": "finalizing"},
                "message_content.message_task.status.state": {
                    "$nin": sorted(state.value for state in TERMINAL_STATES)
                },
            }
            if update_key:
                base_filter["artifact_update_keys"] = {"$ne": update_key}
            if not artifact_id:
                update: dict[str, Any] = {
                    "$push": {"message_content.message_task.artifacts": artifact},
                    "$set": {
                        "message_content.message_task.status.state": "working",
                        "task_updated_at": utcnow(),
                    },
                }
                if artifact_text:
                    update["$set"]["message_content.message_text"] = artifact_text
                if update_key:
                    update["$addToSet"] = {"artifact_update_keys": update_key}
                result = await self._room_agent_messages.update_one(base_filter, update)
                return _mongo_update_succeeded(result)

            if append:
                return await self._append_parts_to_artifact(
                    message_id,
                    artifact_id,
                    artifact,
                    artifact_text,
                    base_filter,
                    update_key,
                )
            return await self._replace_or_insert_artifact(
                artifact_id,
                artifact,
                artifact_text,
                base_filter,
                update_key,
            )
        except Exception:
            logger.error("Failed to accumulate artifact on message", exc_info=True)
            return False

    async def is_artifact_update_recorded(
        self, message_id: str, update_key: str
    ) -> bool:
        return (
            await self._room_agent_messages.find_one(
                {
                    "message_id": message_id,
                    "artifact_update_keys": update_key,
                },
                {"_id": 1},
            )
            is not None
        )

    @staticmethod
    def _artifact_id_match_expr(artifact_id: str) -> dict[str, Any]:
        return {
            "$or": [
                {"$eq": ["$$art.artifactId", artifact_id]},
                {"$eq": ["$$art.artifact_id", artifact_id]},
            ]
        }

    @classmethod
    def _map_replace_artifact_expr(
        cls, artifact_id: str, artifact: dict
    ) -> dict[str, Any]:
        return {
            "$map": {
                "input": {"$ifNull": ["$message_content.message_task.artifacts", []]},
                "as": "art",
                "in": {
                    "$cond": {
                        "if": cls._artifact_id_match_expr(artifact_id),
                        "then": artifact,
                        "else": "$$art",
                    }
                },
            }
        }

    @classmethod
    def _map_append_parts_expr(
        cls,
        artifact_id: str,
        new_parts: list[dict],
        artifact_metadata: dict | None,
    ) -> dict[str, Any]:
        return {
            "$map": {
                "input": {"$ifNull": ["$message_content.message_task.artifacts", []]},
                "as": "art",
                "in": {
                    "$cond": {
                        "if": cls._artifact_id_match_expr(artifact_id),
                        "then": {
                            "$mergeObjects": [
                                "$$art",
                                {
                                    "parts": {
                                        "$concatArrays": [
                                            {"$ifNull": ["$$art.parts", []]},
                                            new_parts,
                                        ]
                                    },
                                    **(
                                        {"metadata": artifact_metadata}
                                        if artifact_metadata is not None
                                        else {}
                                    ),
                                },
                            ]
                        },
                        "else": "$$art",
                    }
                },
            }
        }

    async def _append_parts_to_artifact(
        self,
        message_id: str,
        artifact_id: str,
        artifact: dict,
        artifact_text: str,
        base_filter: dict,
        update_key: str | None,
    ) -> bool:
        new_parts = artifact.get("parts", [])
        if not new_parts:
            return False

        filter_with_artifact = {
            **base_filter,
            "message_content.message_task.artifacts": {
                "$elemMatch": {
                    "$or": [
                        {"artifactId": artifact_id},
                        {"artifact_id": artifact_id},
                    ]
                }
            },
        }
        set_fields: dict[str, Any] = {
            "message_content.message_task.artifacts": self._map_append_parts_expr(
                artifact_id,
                new_parts,
                artifact.get("metadata"),
            ),
            "message_content.message_task.status.state": "working",
            "task_updated_at": utcnow(),
        }
        if artifact_text:
            set_fields["message_content.message_text"] = {
                "$concat": [
                    {"$ifNull": ["$message_content.message_text", ""]},
                    artifact_text,
                ]
            }
        if update_key:
            set_fields["artifact_update_keys"] = {
                "$setUnion": [
                    {"$ifNull": ["$artifact_update_keys", []]},
                    [update_key],
                ]
            }
        result = await self._room_agent_messages.update_one(
            filter_with_artifact,
            [{"$set": set_fields}],
        )
        if _mongo_update_succeeded(result):
            return True

        logger.warning(
            "append=True for nonexistent artifact %s on message %s, inserting new",
            artifact_id,
            message_id,
        )
        insert_update: dict[str, Any] = {
            "$push": {"message_content.message_task.artifacts": artifact},
            "$set": {
                "message_content.message_task.status.state": "working",
                "task_updated_at": utcnow(),
            },
        }
        if artifact_text:
            insert_update["$set"]["message_content.message_text"] = artifact_text
        if update_key:
            insert_update["$addToSet"] = {"artifact_update_keys": update_key}
        result = await self._room_agent_messages.update_one(base_filter, insert_update)
        return _mongo_update_succeeded(result)

    async def _replace_or_insert_artifact(
        self,
        artifact_id: str,
        artifact: dict,
        artifact_text: str,
        base_filter: dict,
        update_key: str | None,
    ) -> bool:
        filter_with_artifact = {
            **base_filter,
            "message_content.message_task.artifacts": {
                "$elemMatch": {
                    "$or": [
                        {"artifactId": artifact_id},
                        {"artifact_id": artifact_id},
                    ]
                }
            },
        }
        set_fields: dict[str, Any] = {
            "message_content.message_task.artifacts": self._map_replace_artifact_expr(
                artifact_id,
                artifact,
            ),
            "message_content.message_task.status.state": "working",
            "task_updated_at": utcnow(),
        }
        if artifact_text:
            set_fields["message_content.message_text"] = artifact_text
        if update_key:
            set_fields["artifact_update_keys"] = {
                "$setUnion": [
                    {"$ifNull": ["$artifact_update_keys", []]},
                    [update_key],
                ]
            }
        result = await self._room_agent_messages.update_one(
            filter_with_artifact,
            [{"$set": set_fields}],
        )
        if _mongo_update_succeeded(result):
            return True

        insert_update: dict[str, Any] = {
            "$push": {"message_content.message_task.artifacts": artifact},
            "$set": {
                "message_content.message_task.status.state": "working",
                "task_updated_at": utcnow(),
            },
        }
        if artifact_text:
            insert_update["$set"]["message_content.message_text"] = artifact_text
        if update_key:
            insert_update["$addToSet"] = {"artifact_update_keys": update_key}
        result = await self._room_agent_messages.update_one(base_filter, insert_update)
        return _mongo_update_succeeded(result)

    async def update_task_state_on_message_if_not_terminal(
        self,
        message_id: str,
        state: str,
    ) -> bool:
        try:
            terminal_values = sorted(state.value for state in TERMINAL_STATES)
            return await self._message_repository.update_agent_message_if_not_terminal(
                message_id,
                {
                    "message_content.message_task.status.state": state,
                    "task_updated_at": utcnow(),
                },
                terminal_values,
            )
        except Exception:
            logger.error("Failed to update task state on message", exc_info=True)
            return False
