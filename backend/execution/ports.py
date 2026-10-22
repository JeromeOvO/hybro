from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from enum import Enum
from typing import Any, Protocol

from models.agent_network import AgentMessageRequest, AgentMessageResponse

ProcessingStatusLike = str | Enum


class DirectAgentMessenger(Protocol):
    async def send(self, request: AgentMessageRequest) -> AgentMessageResponse: ...


class TaskFactory(Protocol):
    def __call__(
        self,
        coro: Awaitable[Any],
        *,
        name: str | None = None,
    ) -> asyncio.Task[Any]: ...


class HITLMessageCancellationPort(Protocol):
    async def cancel_requests_for_message(self, message_id: str) -> None: ...


class ClientRequestIdResolver(Protocol):
    async def resolve_client_request_id(
        self,
        message_id: str | None,
        provided_client_request_id: str | None,
    ) -> str | None: ...
