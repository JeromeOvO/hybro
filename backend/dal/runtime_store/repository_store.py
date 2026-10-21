from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from common.protocols import MongoDAL
from common.utils.logger import get_logger
from dal.runtime_store.parts import (
    HITLLifecycleRuntimeStorePart,
    HITLRuntimeStorePart,
)

logger = get_logger(__name__)

__all__ = ["RuntimeRepositoryStore"]


class RuntimeRepositoryStore:
    """Compatibility store backed by DAL repositories during runtime-store migration."""

    def __init__(
        self,
        *,
        mongo: MongoDAL,
    ) -> None:
        self._room_agent_messages = mongo.collection("room_agent_messages")
        self._room_user_messages = mongo.collection("room_user_messages")
        self._hitl_requests = mongo.collection("hitl_requests")
        self._hitl_interactions = mongo.collection("hitl_interactions")
        self._hitl_resume_commands = mongo.collection("hitl_resume_commands")
        self._hitl_part = HITLRuntimeStorePart(
            hitl_requests=self._hitl_requests,
            room_agent_messages=self._room_agent_messages,
            room_user_messages=self._room_user_messages,
        )
        self._hitl_lifecycle_part = HITLLifecycleRuntimeStorePart(
            interactions=self._hitl_interactions,
            resume_commands=self._hitl_resume_commands,
            hitl_requests=self._hitl_requests,
        )

    @property
    def hitl(self) -> HITLRuntimeStorePart:
        return self._hitl_part

    @property
    def hitl_lifecycle(self) -> HITLLifecycleRuntimeStorePart:
        return self._hitl_lifecycle_part

    def _hitl_delegate(self) -> HITLRuntimeStorePart:
        part = getattr(self, "_hitl_part", None)
        if part is not None:
            return part
        return HITLRuntimeStorePart(
            hitl_requests=getattr(self, "_hitl_requests", None),
            room_agent_messages=getattr(self, "_room_agent_messages", None),
            room_user_messages=getattr(self, "_room_user_messages", None),
        )

    def _hitl_lifecycle_delegate(self) -> HITLLifecycleRuntimeStorePart:
        part = getattr(self, "_hitl_lifecycle_part", None)
        if part is not None:
            return part
        return HITLLifecycleRuntimeStorePart(
            interactions=getattr(self, "_hitl_interactions", None),
            resume_commands=getattr(self, "_hitl_resume_commands", None),
            hitl_requests=getattr(self, "_hitl_requests", None),
        )

    async def get_pending_hitl_requests_for_message(
        self, user_message_id: str
    ) -> list[dict]:
        return await self._hitl_delegate().get_pending_hitl_requests_for_message(
            user_message_id
        )

    async def get_pending_hitl_requests_for_message_strict(
        self,
        user_message_id: str,
    ) -> list[dict]:
        return await self._hitl_delegate().get_pending_hitl_requests_for_message_strict(
            user_message_id
        )

    async def create_hitl_request(self, request_data: dict) -> bool:
        return await self._hitl_delegate().create_hitl_request(request_data)

    async def get_hitl_request(self, request_id: str) -> dict | None:
        return await self._hitl_delegate().get_hitl_request(request_id)

    async def update_hitl_request(self, request_id: str, **updates) -> bool:
        return await self._hitl_delegate().update_hitl_request(request_id, **updates)

    async def cas_update_hitl_request(
        self,
        request_id: str,
        expected_status: str,
        **updates,
    ) -> bool:
        return await self._hitl_delegate().cas_update_hitl_request(
            request_id, expected_status, **updates
        )

    async def cas_update_hitl_request_strict(
        self,
        request_id: str,
        expected_status: str,
        **updates,
    ) -> bool:
        return await self._hitl_delegate().cas_update_hitl_request_strict(
            request_id,
            expected_status,
            **updates,
        )

    async def fenced_update_hitl_request(
        self,
        request_id: str,
        claim_id: str,
        updates: dict | None = None,
        **kw_updates,
    ) -> bool:
        return await self._hitl_delegate().fenced_update_hitl_request(
            request_id, claim_id, updates, **kw_updates
        )

    async def claim_hitl_request(self, request_id: str, **updates) -> dict | None:
        return await self._hitl_delegate().claim_hitl_request(request_id, **updates)

    async def get_pending_hitl_requests(self, room_id: str) -> list[dict]:
        return await self._hitl_delegate().get_pending_hitl_requests(room_id)

    async def get_pending_hitl_requests_strict(self, room_id: str) -> list[dict]:
        return await self._hitl_delegate().get_pending_hitl_requests_strict(room_id)

    async def count_hitl_requests_for_message(
        self,
        continuation_message_id: str,
    ) -> int:
        return await self._hitl_delegate().count_hitl_requests_for_message(
            continuation_message_id
        )

    async def update_agent_message_task_state(
        self,
        message_id: str,
        state: str,
    ) -> bool:
        return await self._hitl_delegate().update_agent_message_task_state(
            message_id, state
        )

    async def persist_hitl_request_id_on_message(
        self,
        message_id: str,
        request_id: str | None,
    ) -> bool:
        return await self._hitl_delegate().persist_hitl_request_id_on_message(
            message_id, request_id
        )

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
        return await self._hitl_delegate().find_pending_hitl_request_for_agent_message(
            room_id=room_id,
            display_message_id=display_message_id,
            continuation_message_id=continuation_message_id,
            agent_id=agent_id,
            a2a_task_id=a2a_task_id,
            a2a_context_id=a2a_context_id,
        )

    async def create_or_reuse_pending_hitl_request(
        self,
        request_data: dict[str, Any],
    ) -> tuple[dict[str, Any], bool] | None:
        return await self._hitl_delegate().create_or_reuse_pending_hitl_request(
            request_data
        )

    async def claim_hitl_open_projection(
        self, request_id: str, claim_id: str
    ) -> dict[str, Any] | None:
        return await self._hitl_delegate().claim_hitl_open_projection(
            request_id, claim_id
        )

    async def complete_hitl_open_projection(
        self, request_id: str, claim_id: str
    ) -> bool:
        return await self._hitl_delegate().complete_hitl_open_projection(
            request_id, claim_id
        )

    async def release_hitl_open_projection(
        self, request_id: str, claim_id: str
    ) -> bool:
        return await self._hitl_delegate().release_hitl_open_projection(
            request_id, claim_id
        )

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
        interaction_id: str,
        question_count: int,
        question_index: int,
        expected_request_id: str | None = None,
        projection_at: datetime | None = None,
    ) -> bool:
        return await self._hitl_delegate().persist_pending_hitl_on_agent_message(
            message_id,
            request_id=request_id,
            prompt=prompt,
            prompt_type=prompt_type,
            choices=choices,
            a2a_task_id=a2a_task_id,
            a2a_context_id=a2a_context_id,
            interaction_id=interaction_id,
            question_count=question_count,
            question_index=question_index,
            expected_request_id=expected_request_id,
            projection_at=projection_at,
        )

    async def _ensure_message_task_metadata(self, message_id: str) -> None:
        return await self._hitl_delegate()._ensure_message_task_metadata(message_id)

    async def persist_hitl_user_answer(
        self,
        message_id: str,
        user_input: str | None,
    ) -> bool:
        return await self._hitl_delegate().persist_hitl_user_answer(
            message_id, user_input
        )

    async def persist_hitl_interaction_metadata(
        self,
        message_id: str,
        *,
        interaction_id: str | None,
        question_count: int | None,
        question_index: int | None,
    ) -> bool:
        return await self._hitl_delegate().persist_hitl_interaction_metadata(
            message_id,
            interaction_id=interaction_id,
            question_count=question_count,
            question_index=question_index,
        )

    async def iter_stale_processing_hitl_requests(
        self,
        cutoff: Any,
    ) -> AsyncIterator[dict]:
        async for doc in self._hitl_delegate().iter_stale_processing_hitl_requests(
            cutoff
        ):
            yield doc

    async def ensure_hitl_indexes(self) -> None:
        return await self._hitl_delegate().ensure_hitl_indexes()
