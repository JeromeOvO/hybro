"""Adapter for fetching task state from remote A2A agents."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from a2a.types import AgentCard, GetTaskRequest, JSONRPCErrorResponse, TaskQueryParams

from common.types import Task

from .card_data import sdk_agent_card_data
from .message_factory import from_sdk_task

logger = logging.getLogger(__name__)


async def fetch_remote_task(
    agent_card_data: Any,
    task_id: str,
    *,
    timeout: float = 30.0,
) -> Task | None:
    """Fetch a task from a remote A2A agent and return an internal task."""
    try:
        from a2a.client import A2AClient

        card = AgentCard(**sdk_agent_card_data(agent_card_data))
        async with httpx.AsyncClient(timeout=timeout) as client:
            a2a_client = A2AClient(client, agent_card=card)
            response = await a2a_client.get_task(
                GetTaskRequest(id=task_id, params=TaskQueryParams(id=task_id))
            )
            if not response or isinstance(response.root, JSONRPCErrorResponse):
                logger.error(
                    "Failed to get task from agent, error: %s",
                    getattr(response.root, "error", "Unknown error")
                    if response
                    else "No response",
                )
                return None
            return from_sdk_task(response.root.result)
    except Exception as exc:
        logger.error("Failed to get task from agent: %s", exc, exc_info=True)
        return None

__all__ = ["fetch_remote_task"]
